# 面试准备慢 Provider 租约心跳设计

## 1. 文档状态与范围

- 日期：2026-08-04
- 状态：待设计复审；本轮只定义设计，不进入实现计划
- 目标分支：feat/20260801-offer-negotiation
- 目标领域：面试准备 Proposal 的 Provider 调用租约与 CAS 生命周期

本设计只修复面试准备在慢 Provider 调用期间租约过期、导致已验证结果无法写入 ready 的问题。不新增 API、数据库迁移、前端交互、业务重试、AI 能力或跨领域写入，也不改变 Offer 谈薪及其他 Proposal 流程。

实现必须保留现有的输入快照、严格 JSON Schema、证据校验、一次格式修复、安全空结果、Provider 未知结果、来源冲突和人工确认语义。租约心跳只改变生成过程的所有权保持方式，不改变 Provider 输入和输出契约。

## 2. 当前实现与已确认根因

当前实现位于 src/offerpilot/repositories/interview_preparation_proposals.py，面试准备生成记录使用 InterviewPreparationProposal。现有字段已经提供本设计所需的持久化基础：

- attempt_status
- generation_revision
- provider_call_token
- provider_lease_until
- input_snapshot_json
- source_fingerprint
- proposal_json 与 proposal_hash
- idempotency_key

当前 LEASE_SECONDS 为 30 秒。创建或接管 Attempt 后，仓储在提交事务后调用 Provider；Provider 返回后，最终写入事务除了检查状态、revision、token 和来源指纹，还要求 provider_lease_until 仍未过期。租约一过期，合法结果就被当作当前 owner 已失效，API 返回 202/generating，而不是写入 ready。

隔离诊断使用同一冻结输入和现有 DeepSeek 配置观察到：

- 模型：deepseek-v4-flash
- Provider endpoint 三元组：https://api.deepseek.com:443
- 单次 Provider 调用约 43 秒
- 请求体大小和 Provider request id 只以脱敏诊断形式记录
- 直接调用获得了可验证的面试准备结果，但约 46 秒后 API 仍返回 202 generating

因此根因是正常 Provider 调用时长超过 30 秒租约，而不是输入证据不合格、客户端超时或需要增加业务重试。

## 3. 方案选择

### 3.1 采用：租约心跳 + fencing token

每个 Provider 调用由其 owner 启动一个独立的租约心跳任务：默认租约仍为 30 秒，约每 10 秒续签一次。心跳只在数据库中延长同一 Attempt 的 provider_lease_until，不调用 Provider，不发送网络请求，也不修改快照或结果。

最终写入使用现有 generation_revision + provider_call_token 作为 fencing token。最终 CAS 不再要求租约时间晚于当前时间；只要状态、revision、token 和来源指纹仍匹配，当前 owner 可以提交一次结果。若另一个请求已经接管，revision 或 token 必然已经改变，旧 Provider 结果不能覆盖新 owner。

### 3.2 不采用的方案

- 单纯把租约改成 120 或 300 秒：不能覆盖更慢的 Provider，并会延长崩溃后的接管等待时间。
- 只删除最终租约过期检查：现有 generation_revision + provider_call_token 已能隔离被接管后的迟到结果，但没有心跳时租约仍会过期，其他请求仍可能接管并产生第二次 Provider 调用。因此必须同时解决“结果能否安全写入”和“慢调用期间是否会被重复接管”两个问题。
- 通过业务层再次调用 Provider：会增加调用费用，并掩盖所有权生命周期问题。

## 4. 不变量与术语

### 4.1 Attempt owner

一次成功领取生成任务的事务得到以下不可公开的 owner 凭证：

    owner_revision = generation_revision
    owner_token = provider_call_token

二者必须成对使用。任何续签、Provider 异常状态回写或最终结果回写都必须同时匹配 Attempt ID、attempt_status == generating、generation_revision == owner_revision 和 provider_call_token == owner_token。

provider_lease_until 只表示其他请求何时可以尝试接管；它不是最终结果的唯一所有权凭证。只有数据库 CAS 成功修改 revision/token 的新 owner 才能取代旧 owner。

### 4.2 冻结输入

Provider 使用创建或接管时生成的 snapshot。它的规范 JSON 和 source_fingerprint 继续作为本次 Attempt 的冻结输入。心跳不得重建、修改或重新指纹化该快照。

最终写入前仍用独立数据库 session 重建当前来源快照，并要求其指纹等于 owner 持有的 source_fingerprint。Application、Interview Event、Resume、JD、Knowledge Evidence 或用户断言发生来源变化时，继续执行现有来源冲突语义，不把变化误判为租约问题。

### 4.3 所有权丢失与续签结果未知

必须区分两个进程内状态，不能把所有心跳异常都解释成丢权：

- confirmed_ownership_lost：心跳条件更新返回 0 行，或通过读取明确发现 attempt_status、generation_revision 或 provider_call_token 已不再匹配。此时数据库已经证明当前 owner 被取代或状态已改变；停止心跳，Provider 返回后禁止旧 owner 写入，也不得把新 owner 的状态改回 provider_unknown 或 invalidated。
- heartbeat_uncertain：SQLite 锁冲突、session/连接异常、心跳线程异常退出，或在有限重试后无法确认续签是否成功，但没有证据证明 revision/token/status 已变化。此时只停止心跳并记录结果未知；Provider 返回后仍必须执行最终 fencing CAS，由数据库决定当前 owner 是否仍可写入。

正常的 Provider 返回路径也会先停止心跳并等待其退出，但“正常停止”不等于丢权。无论是正常停止还是 heartbeat_uncertain，最终 CAS 都继续依赖 fencing token；只有 confirmed_ownership_lost 可以在最终 CAS 前阻止旧 owner 写入。

## 5. 生成生命周期

### 5.1 创建或接管

创建和接管继续在短事务中完成，且事务提交后才调用 Provider：

1. 校验 idempotency key，并按现有规则构建当前输入快照及 source_fingerprint。
2. 新 Attempt 原子写入 generating、revision 1、随机 provider_call_token、当前时间加 30 秒的 provider_lease_until、冻结快照和指纹。
3. 同 key 且快照相同的未过期 generating/provider_unknown Attempt 仍返回现有状态，不创建第二次 Provider 调用。
4. 已过期的同 key Attempt 只能由一个数据库事务通过状态、旧 revision、旧 token 和过期条件的 CAS 接管；成功者 revision 加一、生成新 token、写入新的 30 秒租约，然后提交事务。
5. 任何数据库 session 在提交后关闭；模型调用期间不得持有创建/接管事务的 session 或连接。

现有 preflight 仍是只读的快速路径：它可以在 Provider 配置解析前返回 ready 或未过期的 pending，也可以把过期 Attempt 标记为需要真正生成。preflight 不启动心跳；只有 create_generated 成功创建或接管 owner、并且即将调用 Provider 时才启动心跳。这样不会因同 key 的轮询或 Provider 未配置而产生后台任务。

同 key 的不同快照、已失效 Attempt、来源变化等继续使用现有稳定错误语义；本设计不把冲突转换成新 API 错误。

### 5.2 启动与运行心跳

成功创建或接管后，owner 在调用 generate_interview_preparation_proposal() 前启动只属于自己的心跳任务。

心跳契约固定如下：

- 默认 LEASE_SECONDS = 30。
- 默认 heartbeat interval = 10 秒。
- 每次续签使用新的 session_factory() session，并在一个短事务内执行单条条件更新。
- 更新目标只包括 provider_lease_until = heartbeat_now + LEASE_SECONDS。
- 条件必须包括 Attempt ID、attempt_status == generating、owner revision 和 owner token。
- 不更新 input_snapshot_json、source_fingerprint、proposal_json、proposal_hash、attempt_status、revision 或 token。
- SQLite 短暂锁冲突只允许在该次心跳的有限短重试中处理；重试仍无法确认续签结果时，设置 heartbeat_uncertain 并停止后续续签，不把它当作已被接管。
- rowcount 为 0 或刷新后明确发现 owner 条件不匹配时，设置 confirmed_ownership_lost 并停止后续续签。

心跳任务使用可停止的 waiter/event，而不是阻塞数据库连接。它必须保存可观察的 heartbeat 次数、confirmed_ownership_lost 和 heartbeat_uncertain 状态，但不保留快照、JD、简历、模型输出或完整请求。

心跳线程的意外退出必须设置 heartbeat_uncertain，除非退出前已经确认数据库条件不匹配。只有由 owner 在 Provider 返回后显式设置 stop event 并成功等待退出，才算正常停止。若心跳线程无法在清理边界内退出，必须记录 heartbeat_uncertain、保证其 session 最终关闭，并仍执行最终 fencing CAS；测试必须暴露线程或 session 泄漏，不能用“异常退出即禁止写入”掩盖未确认的数据库状态。

### 5.3 Provider 调用与已有校验

心跳覆盖整个现有 Provider 处理区间，包括：

- 首次 Provider 调用；
- 现有的一次格式修复调用（如果严格契约允许修复）；
- 严格 JSON Schema、证据路径、证据摘录和安全空结果校验。

本设计不改变 Provider 输入、模型选择、修复次数、证据门控、Provider 异常分类或用户断言隔离。额外的心跳不是 Provider 重试，也不产生第二个 AI 请求。

### 5.4 最终 CAS 写入

Provider 返回后按以下顺序执行：

1. 设置 heartbeat stop event，等待心跳任务结束，并确保其 session 已关闭。
2. 若本地 confirmed_ownership_lost 已设置，不进行旧 owner 的结果写入；通过现有状态读取语义返回当前 Attempt 或冲突结果。heartbeat_uncertain 不走该提前返回路径，必须继续执行最终 fencing CAS。
3. 使用新的短数据库 session 开启最终事务，读取同一 application/event/key 的 Attempt。
4. 重建当前来源快照，重新计算 fingerprint；来源找不到或指纹不同，按现有 source_conflict 语义失效当前 owner，不能写入 Proposal。
5. 仅当以下条件同时满足时写入 proposal_json、proposal_hash、proposal_status 并转为 ready：
   - attempt_status == generating；
   - generation_revision == owner_revision；
   - provider_call_token == owner_token；
   - 当前来源 fingerprint 等于冻结的 source_fingerprint。
6. 最终条件不再检查 provider_lease_until > now。租约过期本身不能否定仍持有匹配 fencing token 的正常返回；若已被接管，revision/token 条件会使 CAS 失败。
7. 写入成功后清空 token 和 lease，提交事务；历史 Proposal 的 hash、快照和来源状态保持现有语义。

若最终 CAS 的 rowcount 为 0，旧结果不得覆盖数据库中已有的 ready、provider_unknown、invalidated 或新 owner 的 generating 状态。API 不得伪造 201 ready；按当前行返回可重放的安全状态或现有冲突错误。

### 5.5 正常结束与异常清理

Provider 成功、严格校验失败、Provider 异常、来源冲突、数据库异常和 ownership lost 都必须经过同一 finally 清理路径：

- stop event 必须被设置；
- 心跳任务必须退出并被 join/await；
- 心跳使用的每个数据库 session 必须结束；
- 不得遗留后台线程继续访问数据库；
- 只有在 owner 条件仍匹配时，才允许写入 provider_unknown 或 invalidated；
- 旧 owner 不得清理或覆盖新 owner 的 lease、token、revision 或结果。

## 6. 状态与并发语义

| 场景 | 数据库状态变化 | Provider 调用 | API/重放语义 |
| --- | --- | ---: | --- |
| 新 key 成功领取 | 无记录 → generating，revision=1，新 token，新 lease | 1 | 当前调用继续；同 key 并发请求在 lease 有效时返回 202 generating |
| 心跳成功 | 保持 generating，只延长 lease | 0 | 同 key 仍由原 owner 处理，不可接管 |
| 续签结果未知但未确认接管 | 数据库状态未知，不主动改写 Attempt | 0 | Provider 返回后仍执行最终 fencing CAS；CAS 成功则保存结果，失败则返回当前安全状态 |
| 慢 Provider 合法返回 | generating → ready，写 Proposal/hash，清空 token/lease | 1 | 返回现有成功响应，不再返回 202 generating |
| Provider/网络未知 | generating → provider_unknown，保留原 key/token/冻结输入 | 1 | 现有 502 provider_error；同 key 在 lease 有效时返回 202，不重复调用 |
| 心跳停止且 lease 过期 | generating/provider_unknown 可被一个新事务接管，revision+1，新 token | 新 owner 最多 1 | 新 owner 使用同 key、原冻结输入继续；旧结果不能覆盖 |
| 旧 Provider 迟到 | 旧 revision/token CAS 失败 | 已发生 | 返回当前安全状态，不能写 ready 或替换新结果 |
| 来源漂移或删除 | 当前 Attempt 按现有规则 invalidated/冲突 | 不增加 | 返回现有来源冲突；不通过心跳修复 |
| 严格契约失败/安全空结果 | 维持现有面试准备语义 | 首次加既有一次修复（若适用） | 不改变现有 201/502 与 Proposal 状态 |

“同 key 只调用一次”适用于同一 owner 在其租约心跳有效期间的并发重放。只有 owner 已停止且租约自然过期后，安全接管才允许该同 key 产生新的 Provider 调用；这属于既有恢复机制，不是新增业务重试。

## 7. API、前端和数据边界

### 7.1 API 兼容

不修改 HTTP 路径、请求字段、响应字段或状态码。现有生成接口继续区分：

- 慢但成功：最终返回现有 ready 成功结果；
- 同 key 正在生成：202 与现有 attempt_status/retry_after_ms 语义；
- Provider 未知：现有 Provider 错误码，保留原 key 和冻结输入；
- 来源冲突、非法输入和严格契约失败：现有错误码与失效语义。

不得按 HTTP 状态粗略加入重试；不得为了租约心跳改变前端提示或原尝试恢复规则。

### 7.2 数据库兼容

不新增表、不新增字段、不修改迁移。InterviewPreparationProposal 现有 revision/token/lease/fingerprint 字段足以承载心跳与 fencing。实现只改变这些现有字段在生成生命周期中的更新时序和 CAS 条件。

### 7.3 领域隔离

心跳不写入 Application、ApplicationEvent、Resume、Knowledge、InterviewNote、材料、Offer、Chat、提醒或其他领域。它只更新当前面试准备 Proposal 的 provider_lease_until，最终只写入该 Proposal 原有结果字段。

## 8. 诊断与隐私

生产日志只允许记录以下脱敏字段：

- Attempt/Proposal ID；
- generation revision；
- heartbeat count；
- confirmed_ownership_lost / heartbeat_uncertain 是否发生及安全失败类别；
- Provider 既有诊断中的 model、endpoint 三元组、HTTP status、timeout、耗时和哈希后的 request id（沿用现有脱敏机制）。

禁止记录 JD、简历、Knowledge Evidence、用户断言、模型原文、完整 Provider 请求、响应体、API key 或配置文件内容。测试和报告也只能记录上述类别、计数和退出结果，不保存隐私快照。

## 9. 测试先行契约

设计通过后，实施计划必须先为以下测试建立可重复的失败回归，再写最小实现。测试不得真实等待 30 秒；应通过注入租约时钟、heartbeat interval、可控 waiter 和 Provider barrier 构造确定性时序。

### 9.1 Repository 与双连接并发

目标测试文件：tests/test_interview_preparation_repository.py。

1. Provider barrier 持续超过注入的 lease，心跳连续续签，最终写入 ready。
2. 无论 Provider 耗时，模型调用次数严格为 1；心跳调用次数只影响 lease 更新，不影响 Provider。
3. 心跳有效期间，同 key 的第二个 repository/SQLite connection 返回 pending/generating，不能接管、不能新增 Provider 调用。
4. 停止心跳并让 lease 过期后，两个独立 SQLite connection 并发接管，只有一个 revision/token CAS 成功。
5. 旧 owner 的 Provider barrier 在新 owner ready 后释放，旧结果不能覆盖新 Proposal、hash、revision 或状态。
6. 心跳停止与最终提交之间触发接管，最终 CAS 只能产生一个可见结果；旧 owner 不得返回伪造的 ready。
7. 续签只更新 provider_lease_until；快照、fingerprint、Proposal、revision 和 token 不发生非预期变化。
8. 每次 heartbeat 使用独立短 session；模型调用期间不存在长期持有的数据库 session/事务。
9. SQLite 锁冲突的有限重试后若无法确认续签结果，owner 标记为 heartbeat_uncertain；在没有实际接管的控制测试中，合法 Provider 结果仍能通过最终 fencing CAS 写入 ready。

### 9.2 既有失败语义与幂等

仍在 tests/test_interview_preparation_repository.py 和 tests/test_interview_preparation_api.py 覆盖：

1. Provider 异常后保持 provider_unknown、原 idempotency key、冻结输入和可重放状态。
2. 来源漂移、事件删除、Resume 变化和非法输入继续产生既有来源/验证错误，不被心跳转成成功。
3. 严格 JSON 失败、一次格式修复和安全空 Proposal 的调用次数与状态保持不变。
4. 同 key 的相同冻结输入在 lease 有效时不增加 Provider 调用；lease 过期后的接管只允许一个新 owner。
5. 成功、Provider 异常、格式修复、来源冲突、confirmed_ownership_lost、heartbeat_uncertain 和接管路径均释放 heartbeat task、session 和连接。

### 9.3 API 与隔离验收

API 回归继续使用 tests/test_interview_preparation_api.py，并在 tests/test_smoke.py 的既有面试准备 smoke 中验证：

- 慢 Provider 最终得到 200/201 的 ready 结果，不停留在 202 generating；
- 同一 Attempt 的 Provider 调用数为 1；
- 诊断只包含失败类别、耗时、HTTP 状态/timeout 和哈希后的 request id；
- 无跨领域写入，无配置或隐私输出；
- local isolate、真实配置复制和清理语义不变。

真实 AI 验收只在专项实现完成后执行：先使用同一冻结输入运行面试准备专项，再执行完整 real-ai verify。真实请求应记录脱敏的 provider/model/endpoint 三元组、请求体大小、耗时和 request id 哈希，但不将 JD、简历、模型原文或密钥写入报告。

## 10. 验收与发布门禁

实现完成后，且只有在设计与测试先行计划分别复审通过后，按以下顺序验收：

1. 面试准备 Repository/API 专项与并发/session 生命周期回归。
2. Ruff、Mypy；确认前端门禁未发生非预期变化。
3. oc verify --profile local，使用隔离临时数据目录。
4. 同一现有配置下的面试准备真实 AI 专项：约 43 秒的 Provider 调用最终返回 ready；同一 Attempt 仅一次 Provider 调用。
5. 完整 real-ai verify，真实 Provider 不稳定时如实报告，不把超时或 502 说成通过。
6. 清理所有临时数据库、服务、线程、session、代理和浏览器资源；不修改用户数据目录，不输出密钥。

本任务的发布通过条件是：慢 Provider 成功结果可在 lease 心跳保护下完成一次 CAS 写入；旧 owner 不能覆盖接管者；所有已有证据门控和错误语义保持通过。real-AI 若仍失败，只能作为 Provider/环境稳定性风险记录，不能通过放宽契约或增加业务重试掩盖。

## 11. 明确不做

- 不把租约扩大到其他 Proposal 流程。
- 不新增 API、数据库迁移、字段、状态或前端控件。
- 不改变面试准备 Provider 输入、JSON Schema、证据路径、一次格式修复和安全空结果。
- 不改变 Provider/网络未知、来源漂移、非法输入和人工确认语义。
- 不增加业务级重试或额外 AI 调用。
- 不修改 Offer 谈薪、Pilot、模拟面试、材料、Knowledge 或 Chat 行为。
- 不推送、不合并。
