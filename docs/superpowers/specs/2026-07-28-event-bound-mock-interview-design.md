# 事件绑定的文本模拟面试设计

- 任务：`feat: AI add event-bound mock interview`
- 日期：2026-07-28
- 状态：待设计复审；本轮只新增本文档，不实现代码
- 审计基线：当前分支 `feat/20260724-evidence-gated-interview-preparation`，提交 `c5981cd`

## 1. 目标、边界与产品判断

首期提供“围绕一场已安排面试的文本模拟面试”。用户从一个具体的、当前可见的面试事件进入，选择本次使用的简历，确认岗位描述与可选的面试准备建议，然后通过逐题文本对话练习。结束时系统只生成可审阅的结构化反馈建议；用户逐项选择并二次确认后，才创建独立的“模拟练习复盘草稿”。

AI 不判断录用、通过率、岗位匹配度或 Offer，不输出百分制，不替用户决定是否投递或继续流程。首期不做录音、转写、语音、外部平台访问、URL 抓取、自动投递、自动改投递状态，也不自动创建题目、Knowledge、Memory、提醒或日程。

本设计替代旧的自由模拟会话语义，不复活旧 API、旧表或旧 UI。它不是通用聊天入口，也不是脱离 Application 的练习场。

## 2. 当前代码审计

### 2.1 现有实现矩阵

| 能力/入口 | 当前实际事实 | 可发现性与恢复 | 数据写入与外部动作 | 当前判断 | 证据 |
| --- | --- | --- | --- | --- | --- |
| 旧 `MockSession` | `src/offerpilot/models.py` 注册 `mock_sessions`；`src/offerpilot/repositories/mock.py` 提供创建、完成、终止 | `web/src/layout/navigation.ts` 仍保留 `ViewMode='mock'`，但该 view 不在 `VIEW_TO_MODULE`，不能从正式导航解析；旧服务仍可被直接调用 | `POST /api/mock/sessions` 创建 `Conversation(mode="mock_interview")` 与 `MockSession`；结束时调用模型并保存百分制字段；`auto_save_note` 可创建正式 `InterviewNote` | 已废弃候选；不得继续扩展 | `src/offerpilot/api.py` 的 `/api/mock/sessions*` 路由、`tests/test_mock_api.py`、`tests/test_conditional_delete_repositories.py` |
| 旧 MockStudio | `web/src/components/MockStudio/*` 含配置、聊天、结果卡、雷达图；使用 `web/src/services/mock.ts` 和 `web/src/types/mock.ts` | 当前 `AppShell` 没有把 MockStudio 作为正式视图挂载；仍有源码、类型和 service 遗留 | 前端可发起旧创建/结束/删除请求；结果展示自由评分、strengths/weaknesses/drills，并可自动保存复盘 | 未挂载的遗留实现；本任务迁移中删除 | `web/src/components/MockStudio/MockStudioView.tsx`、`MockChat.tsx`、`MockResultCard.tsx`、`web/src/services/mock.ts` |
| Chat 的旧模拟面试痕迹 | Chat 能力列表仍含 `mock-change-direction`、`mock-go-deeper`、`mock-skip-question`、`mock-progress`；会话列表测试仍识别 `mock_interview` mode | 不是本任务入口，但会让用户误以为旧会话仍受支持 | 旧 Chat 会话可能保留共享 `conversations/chat_messages` 记录 | 遗留类型/依赖；迁移时清理，不新增替代工具 | `web/src/components/ChatPanel/capabilities.ts`、`web/src/components/ChatPanel/conversationList.test.ts`、`src/offerpilot/models.py` 的 `Conversation/ChatMessage` |
| 顶层面试索引 | `GET /api/interviews` 返回 Application 下的 `event_type=interview` 事件；软删除 Application 被排除，深链详情为 404 | `AppShell` 的 `interview` 模块加载 `InterviewV01View`；当前索引可查看投递详情、准备面试，不能启动文本模拟面试 | 当前只读索引，不因打开索引写入领域数据 | 正式可用的自然入口，扩展为本功能唯一的顶层入口 | `src/offerpilot/repositories/interview_index.py`、`src/offerpilot/api.py`、`web/src/components/InterviewV01View.tsx`、`tests/test_interview_index_api.py`、`web/src/components/InterviewV01View.test.tsx` |
| 投递详情面试事件 | `ApplicationDetail` 已维护面试事件、复盘、知识沉淀和面试准备入口；准备入口携带精确 `applicationId + eventId` | 可从具体投递进入，事件选择不是全局猜测 | 既有流程按各自 HITL 语义写入；本功能只新增打开文本模拟面试的入口 | 正式可复用入口 | `web/src/components/ApplicationDetail.tsx`、`web/src/layout/AppShell.tsx` 及对应 Interview Review/Preparation/Knowledge Capture 测试 |
| 面试复盘/知识/面试准备 | 已有独立的 `InterviewReviewProposal`、Knowledge Capture 和 `InterviewPreparationProposal`，均有冻结快照、证据校验和历史语义 | 从面试索引与投递详情可恢复历史；这些流程不应被旧 MockSession 复用 | 各流程的写入与确认由其既有 API 管理；新模拟面试只能读取明确允许的冻结准备建议，不调用其写接口 | 正式可用的相邻能力；本设计只定义受控消费者 | `src/offerpilot/models.py`、`src/offerpilot/repositories/interview_*`、`tests/test_interview_*`、`docs/superpowers/specs/2026-07-24-evidence-gated-interview-preparation-design.md` |

### 2.2 文档与事实的差异

`docs/p0-release-checklist.md` 的旧走查仍写“面试空状态/占位页”，而当前代码已经有真实的 `GET /api/interviews` 索引和面试相关入口；这是文档滞后，不是继续保留 MockStudio 的理由。新的实现计划必须同时更新该事实检查，但本轮不修改它。

`docs/architecture/knowledge-system.md` 将模拟面试列为未来可能的练习消费者。本设计遵守其 Source/Evidence/Knowledge 分层：未确认的模拟反馈不是 Knowledge Source，确认的模拟练习复盘草稿也不自动进入 Knowledge。

现有 `tests/test_mock_api.py` 和引用 `MockSession` 的 smoke/删除依赖测试证明旧路径是真实代码，不应被描述为“仅文档遗留”。它们必须在破坏性迁移中删除或改写为“旧接口不可用”测试。

## 3. 首期用户流程

### 3.1 唯一入口与前置条件

入口只有两处：

1. 顶层“面试”索引中某个事件的“开始文本模拟面试”；
2. 已绑定 Application 的 Pilot 会话中，用户明确选择该 Application 的某个面试事件后打开同一原生流程。

两处入口必须携带同一对 `application_id + event_id`，不得由前端取“第一个面试事件”代替用户选择。事件必须同时满足：

- Application 存在且 `deleted_at IS NULL`；
- `ApplicationEvent.application_id` 与路径中的 Application 一致；
- `event_type == "interview"`；
- `scheduled_at IS NOT NULL`，表示已排期；
- 事件仍可见，且没有被物理删除。

不满足条件时不创建 Attempt、不调用 Provider：Application 不可见返回 `404 mock_interview_application_not_found`；事件不存在、跨投递、非 interview 或未排期返回稳定 `422 mock_interview_event_invalid`。没有通用的“开始模拟面试”页面，也不允许只传公司名、职位名或 URL 进入。

### 3.2 配置、练习与结束

配置页要求用户显式选择一个当前可见 Resume；不能把 master Resume 静默代选。JD 必须由用户确认的当前文本提供；后端只使用 `jd_text.strip()` 判断空值，快照、哈希和 Provider 输入保留原始字符串，不接受 `job_url` 补全。

面试准备建议是可选输入。用户可以从当前 Application/Event 下可见的、已验证的面试准备 Proposal 中明确选择若干建议；不选择时发送空列表。服务端只复制被选建议所依赖的冻结原始证据与建议文本，不自动召回其他 Proposal、Knowledge、Memory 或聊天历史。来源已变化的准备 Proposal 不能作为当前 Attempt 的可用输入，需重新准备。

用户确认“开始”后服务端冻结 Attempt 输入。随后前端展示一题一答的文本对话：问题、回答输入、提交、下一题和结束复盘。回答保存为 Turn；每次模型调用只接收本次必要的冻结来源和当前/已完成回答，不接收数据库内部 ID、URL、文件路径、原始聊天历史或未选择内容。

结束时只展示结构化 Feedback Proposal。用户可逐项勾选、编辑展示文本，然后看到二次确认；取消、关闭或未确认都不写入“模拟练习复盘草稿”。确认只创建该独立草稿，不修改正式 `InterviewNote`，不覆盖任何主复盘，也不触发 Knowledge、Question、Memory、Wakeup、MockSession 或投递状态写入。

## 4. 新领域模型

实现阶段新增三个核心表和一个用户确认产物表。以下字段是契约，不由实现者自行缩减或改成复用旧 `MockSession`。

### 4.1 `mock_interview_attempts`

Attempt 是一次事件绑定的完整练习输入和状态容器。

| 字段 | 契约 |
| --- | --- |
| `id` | 正整数主键 |
| `application_id` / `event_id` / `resume_id` | 不可变的快照标识；不使用会级联删除历史的外键。新建和恢复时仍必须通过当前可见性与归属校验 |
| `idempotency_key` | 客户端生成、服务端严格校验的 ASCII key；唯一约束为 `(application_id, event_id, idempotency_key)`，数据库兜底并发首次创建 |
| `input_snapshot_json` | canonical UTF-8 JSON，冻结 Application/Event/Resume/JD/可选 Preparation 输入与所有来源哈希；不保存 Provider 原文 |
| `source_fingerprint` | 完整冻结输入的 SHA-256 |
| `attempt_status` | `generating_question`、`awaiting_answer`、`finishing`、`feedback_ready`、`provider_unknown`、`contract_failed`、`source_changed`、`cancelled` |
| `generation_revision` / `provider_call_token` / `provider_lease_until` | Provider owner 的 CAS/lease；token 只保存不可预测随机值，日志不记录；lease 续期和接管必须短事务完成 |
| `current_turn_no` | 已持久化的最后一轮编号，单调递增 |
| `transcript_fingerprint` | 按已提交 Turn 的稳定顺序计算；回答追加时更新，不参与 Attempt 来源漂移判断 |
| `failure_category` | 仅保存稳定分类，不保存原始模型输出、输入快照、证据摘录或密钥 |
| `created_at` / `completed_at` / `cancelled_at` | 审计时间 |

表中的 `idempotency_key` 明确定义为 `attempt_idempotency_key`；Turn 和 Feedback Proposal 的 key 不复用该字段。事件、投递和 Resume 标识是审计用快照标识，不使用 `ON DELETE CASCADE` 删除 Attempt。Application 软删除后，Application-scoped 读取返回 404；事件物理删除、Resume 删除或其内容变化时，保留快照的历史 Attempt/Proposal 可在 Application 仍可见时只读显示并标记 `source_changed`。未确认且明确取消的 Attempt 可物理清理其子数据；未知结果 Attempt 不能被普通关闭删除。

### 4.2 `mock_interview_turns`

Turn 幂等字段和指纹规则是本节的完整契约：`turn_idempotency_key` 与 `question_idempotency_key` 必须持久化；数据库约束为 `UNIQUE(attempt_id, turn_no, turn_idempotency_key)` 和 `UNIQUE(attempt_id, turn_no, question_idempotency_key)`。首题使用独立的 `initial_question_idempotency_key`；提交回答使用 `turn_idempotency_key`；生成下一题使用 `question_idempotency_key`。同一 key 与同一 payload 重放返回原 Turn/问题（首次 201，重放 200），不同 payload 返回 `409 mock_interview_turn_idempotency_conflict`，不新增 Turn。

Attempt 的 `source_fingerprint`（实现中可命名为 `input_fingerprint`）只覆盖不可变的 Application/Event/Resume/JD/可选准备建议快照；它不包含 Turn。另存按 Turn 顺序 canonical 序列化的 `transcript_fingerprint`，每次成功追加回答后更新。问题和反馈回写的 CAS 同时匹配 `generation_revision + provider_call_token + expected_transcript_fingerprint`；transcript 指纹变化是正常练习进展，不是来源漂移。只有 source/input fingerprint 变化才返回 `409 mock_interview_source_conflict`。

每个 Turn 隶属于一个 Attempt，使用 `attempt_id + turn_no` 唯一约束，`attempt_id` 对已确认历史使用限制删除语义。

字段至少包括：`id`、`attempt_id`、`turn_no`、`question_text`、`answer_text`、`question_source_snapshot_json`、`answer_sha256`、`turn_status`、`created_at`。问题与回答保留逐字文本；`question_source_snapshot_json` 只存本轮生成所需的冻结来源路径/摘录，不存 Provider 原始响应包装。

Turn 的 Provider 路径使用服务端 canonical ID `/turns/001/question` 与 `/turns/001/answer`。序号从 1 连续递增；重复提交同一 `turn_idempotency_key` 只能返回同一 Turn，修改已提交回答必须开始新 Attempt，不原地覆盖历史回答。

### 4.3 `mock_interview_feedback_proposals`

Feedback Proposal 是不可变的结构化建议快照。一个 Attempt 可有多个由用户明确触发的结束尝试，但每个 `(attempt_id, idempotency_key)` 只能有一条。Proposal 一旦写入不可更新，历史读取不调用 AI。

字段至少包括：`id`、`attempt_id`、`idempotency_key`、`input_snapshot_json`、`source_fingerprint`、`transcript_fingerprint`、`proposal_json`、`proposal_hash`、`proposal_status`（`normal`/`safe_empty`）、`failure_category`、`created_at`。写入使用 Attempt 的不可变来源快照和结束时的 Turn transcript fingerprint；晚到 Provider 不能覆盖 ready 结果。

严格输出顶层只能包含 `schema_version`、`proposal_status`、`strengths`、`practice_points`、`follow_up_questions`、`next_practice_steps` 六个字段。四个数组各最多 8 条；每项只能包含 `id`、`text`、`evidence_refs`，`id` 全局唯一，`text` 为 1–1,000 个 Unicode 字符且不得为空白。除版本化固定无前提问题这一明确例外外，`evidence_refs` 为 1–4 项非空数组且每项至少引用一个证据；数组没有足够可验证内容时使用固定安全空结构，不能填入分数、弱点标签、录用判断、confidence、recommendation 或额外字段。

四类数组的证据规则进一步收紧：

- `strengths` 和 `practice_points` 每项至少包含一条 `source=turn`、路径为 `/turns/<n>/answer` 的回答证据；可以同时引用 JD/Resume，但不能只用岗位要求或既有简历事实来断言本次表现。
- `follow_up_questions` 的上下文相关问题必须有至少一条回答或冻结来源引用。无依据问题只能使用服务端版本化常量集合 `mock_interview_questions_v1`，此时 `id` 必须是固定 `question_id`，`text` 必须逐字等于该常量，`evidence_refs` 才允许为空；模型不能自行创造无证据例外。
- `next_practice_steps` 每项至少引用一条回答证据；若步骤还涉及岗位方向或已有经历，必须再引用对应 JD/Resume 证据。不得只凭 JD/Resume 将未观察到的能力写成表现结论。
- `mock_interview_questions_v1` 当前完整 allowlist 只有：`clarify_answer` → “您希望进一步澄清哪一部分？”、`add_example` → “您希望补充一个具体例子吗？”、`choose_next_focus` → “下一次练习时，您想先补充哪一步？”；新增固定问题必须提升常量版本，不能通过自然语言相似度放行。
- `normal` 结果若任一具体条目不满足上述规则即契约失败；`safe_empty` 只能是四个数组全部为空的固定结构，不能带摘要、解释或隐含判断。

Evidence ref 只允许：

```json
{
  "source": "jd|resume|turn",
  "path": "/jd/text | /resume/content_json/<canonical-pointer> | /turns/001/answer",
  "excerpt": "冻结快照中的逐字连续非空片段"
}
```

`resume` Pointer 必须规范解析到冻结 `content_json` 的字符串叶子节点；拒绝对象、数组、非规范索引、空白摘录、拼接摘录、Unicode 改写和未知路径。`jd` 只能是 `/jd/text`；`turn` 必须命中实际存在的稳定轮次路径，摘录逐字等于冻结问题/回答字符串的连续子串。准备建议文本可以帮助提问，但不能作为反馈证据来源；模型不能引用旧 AI 建议、Knowledge、Memory 或内部 ID。

### 4.4 `mock_interview_review_drafts`

用户二次确认后创建独立草稿，至少包括：`id`、`attempt_id`、`proposal_id`、`confirmation_idempotency_key`、`application_id`、`event_id`、`selected_blocks_json`、`content_hash`、`source_fingerprint`、`status=confirmed`、`created_at`。数据库必须有 `UNIQUE(proposal_id)`；首次确认用客户端生成且服务端严格校验的 confirmation key 原子插入，双击/并发请求由唯一约束兜底。相同 key 重放返回原草稿 200，不再次写入；同一 Proposal 使用不同 key 时返回稳定 `409 mock_interview_review_draft_already_confirmed`，不能创建第二份草稿。`selected_blocks_json` 只能包含用户选中的 Proposal 条目及其用户编辑结果和原 Evidence refs；用户编辑不改变原 Proposal。

该表不与 `interview_notes` 建立覆盖关系，也不把自己伪装成主复盘。后续若要形成正式 InterviewNote，必须另有明确的、单独确认的业务流程；本首期不提供该转换按钮。该草稿不进入 Knowledge Source、Question、Memory、提醒或其他领域的自动输入。

## 5. 冻结输入与 Provider 最小化

### 5.1 内部 Attempt 快照

内部 canonical snapshot 使用固定字段顺序和 `ensure_ascii=false`，所有字符串原样保存，不做 NFC/NFD、大小写、空白或换行归一化：

```json
{
  "schema_version": "mock-interview-input-v1",
  "application": {"company_name": "...", "position_name": "..."},
  "event": {
    "event_type": "interview",
    "subtype": "technical",
    "round": 1,
    "scheduled_at": "...",
    "duration_minutes": 60,
    "status": "todo"
  },
  "resume": {"title": "...", "content_json": {}},
  "jd": {"text": "..."},
  "selected_preparation": [],
  "turns": []
}
```

Application/Event/Resume 的数据库 ID、`job_url`、事件地点、会议链接、联系人、文件路径、聊天会话 ID 和日志不进入 Provider。`selected_preparation` 只包含用户明确选中的、来源仍有效的准备建议及其必要原始引用；不把完整旧 Proposal 或旧复盘发送给模型。当前回答只在本轮调用中发送；已结束的回答只能按最小的 Turn 文本与路径发送。

### 5.2 Provider 契约

若当前 Provider 配置是 JSON 布尔 `supports_json_schema=true`，可以传原生 JSON Schema；其他值按不支持处理，不传未知参数。无论是否使用原生 Schema，服务端都必须严格解析 JSON、拒绝 fenced Markdown、重复 key、非有限数值、额外字段、空白值、数量超限和伪造 Evidence ref。

契约失败最多执行一次格式修复。修复请求只携带机器可读类别，例如 `invalid_json`、`unexpected_field`、`missing_evidence_ref`、`unknown_evidence_ref`、`excerpt_mismatch` 或 `limit_exceeded`，仍使用同一冻结快照和同一 Provider lease；不带模型原文，不扩大输入，不追加第三次调用。Provider、网络、超时、网关或响应丢失不重试修复，而按结果未知处理。

内部诊断只记录 `failure_category`、是否修复、修复次数、耗时、attempt/proposal 的非敏感内部关联和 Provider request id 的脱敏形式。严禁日志、API 错误、前端 toast 出现模型原文、完整 JD、Resume、回答、证据摘录、API Key 或异常 message。

## 6. 状态、幂等与失败语义

### 6.1 首次创建与慢调用

1. 短事务使用 `BEGIN IMMEDIATE` 校验 Application、事件归属、事件类型/排期、Resume 可见性、JD 非空和可选准备建议状态，生成 canonical snapshot/fingerprint。
2. 若 `(application_id,event_id,idempotency_key)` 不存在，原子插入 `generating_question` Attempt，写入 `generation_revision=1`、随机 `provider_call_token` 和未过期 lease，提交并关闭 SQLite session。
3. 仅 owner 在关闭 session 后调用 Provider。模型调用期间不得持有 SQLite 连接。
4. Provider 返回后，新短事务按 `attempt_status + generation_revision + provider_call_token` CAS 回写 Turn/状态。CAS 失败时丢弃晚到结果，不写模型原文，不覆盖较新的 owner。
5. 两个独立 SQLite connection 的首次并发请求只能得到一个 Attempt 和一次 Provider 调用；第二方读取现有行，按状态返回 202/200/409，而不是自行调用 Provider。

### 6.2 状态表

| 状态/场景 | 服务端行为 | 客户端语义 |
| --- | --- | --- |
| `generating_question` / `finishing` 且 lease 未到期 | 同 key 返回 `202`，只含状态、revision、retry_after；不返回半成品 | 保留输入、key 和草稿，控件冻结，按原 key 有界重试 |
| `provider_unknown` | 返回 `502 mock_interview_provider_error`，保留 key、快照、token 和 lease；lease 未到期禁止接管 | 显示“结果待确认，请使用原尝试重试”，关闭/重挂载后仍复用原 key |
| lease 到期 | `BEGIN IMMEDIATE` 原子 CAS 为 `generating_*`、递增 revision、换 token、设置新 lease；两个接管者只有一个成功 | 成功接管者仍使用原 key；另一方 202 |
| Provider 返回合法 Proposal/问题 | CAS 成功后写入 Turn 或不可变 Proposal，状态变为 `awaiting_answer`/`feedback_ready`，201；同 key 重放 200 | 用户可查看；历史只读 |
| 稳定契约失败 | 最多一次修复仍失败，返回 `502 mock_interview_unverifiable`，不写 Feedback Proposal；Attempt 进入 `contract_failed`，失败分类脱敏保存 | 这是确定失败，可新建 key；不展示模型原文，不把失败伪装为评分 |
| 没有可验证反馈证据 | 不调用 Provider 或由服务端生成并校验固定 `safe_empty` Proposal，201；四类数组为空 | 展示“暂无可验证的练习建议”，不是系统错误 |
| 来源变化 | 回写前只重算不可变 Application/Event/Resume/JD/Preparation 的 `input_fingerprint`；该指纹变化时 CAS 标记 `source_changed`，返回 `409 mock_interview_source_conflict`，不写结果 | 冻结原历史只读；用户必须重新配置并生成新 key |
| transcript 并发推进 | 回写前发现 `transcript_fingerprint` 已推进时，不标记 `source_changed`；返回当前 Attempt/Turn 的安全状态或稳定并发冲突，丢弃陈旧 owner 结果 | 继续使用当前 Turn；同 key 重放返回已有结果 |
| 普通取消/关闭 | 只有确定未进行中的 Attempt 才能短事务删除未确认 Attempt、Turns 和未确认 Proposal；接口幂等 | 清除本地草稿 |
| 取消时结果未知 | 不删除服务端 Attempt，不清除原 key，保留 `provider_unknown`/进行中状态 | 关闭后可恢复；不得生成新 key |
| 事件删除/改为非 interview/Resume 删除 | 新建和继续调用返回稳定 404/409；既有冻结历史在 Application 可见时只读并标 `source_changed` | 禁止继续生成和提交草稿 |
| Application 软删除 | 所有 application-scoped 读写统一 404 并清除 Drawer/Pilot 当前上下文；不把隐藏数据泄露到全局索引 | 显示固定中文 404，不可 handoff |

同 key 的不可变 input fingerprint 不同，无论 Attempt 状态都返回 `409 mock_interview_idempotency_conflict`；ready 结果保持不可变，不被冲突请求改写。Turn 变化只更新 `transcript_fingerprint`，用于 CAS 防晚到回写并绑定问题/反馈输入，不触发 `source_changed`。同一 Turn 幂等键但回答载荷不同返回 `409 mock_interview_turn_idempotency_conflict`；用户修改已提交回答必须创建新 Attempt。用户修改 Resume/JD/准备选择后也必须新建 key；历史 key 不可复用到新快照。

## 7. API 与中文错误契约

全部 API 均为 Application/Event scoped，路径中的两级归属必须在同一短事务校验。建议接口如下，最终实现不得恢复 `/api/mock/*`：

```text
POST   /api/applications/{application_id}/events/{event_id}/mock-interview/attempts
GET    /api/applications/{application_id}/events/{event_id}/mock-interview/attempts
GET    /api/applications/{application_id}/events/{event_id}/mock-interview/attempts/{attempt_id}
POST   /api/applications/{application_id}/events/{event_id}/mock-interview/attempts/{attempt_id}/turns
POST   /api/applications/{application_id}/events/{event_id}/mock-interview/attempts/{attempt_id}/finish
GET    /api/applications/{application_id}/events/{event_id}/mock-interview/attempts/{attempt_id}/feedback
POST   /api/applications/{application_id}/events/{event_id}/mock-interview/attempts/{attempt_id}/review-drafts
DELETE /api/applications/{application_id}/events/{event_id}/mock-interview/attempts/{attempt_id}
```

POST 创建只接受 `resume_id`、原始 `jd_text`、可选 `preparation_proposal_id`、客户端 `attempt_idempotency_key` 和 `initial_question_idempotency_key`；不接受 `job_url`、snapshot、fingerprint、proposal、内部 ID 列表或 Provider 输出。提交回答使用 `turn_idempotency_key`，生成下一题使用 `question_idempotency_key`，结束反馈使用 `feedback_idempotency_key`；每个 key 都在同一 Attempt 内校验并按第 4.2/4.3 节重放。

前端只按稳定 `error_code`/HTTP 状态映射中文：404“当前投递或面试事件不可用”、422“请先选择简历并补充有效的 JD/面试事件”、409“当前来源已变化，请重新开始”、410“本次练习已过期”、502 Provider 未知“AI 服务结果待确认，请使用原尝试重试”、契约失败“AI 输出未通过验证，未创建反馈建议，请重新开始”。未知错误使用统一中文兜底；不得透传 `response.data.error`、Axios message、Python exception 或模型内容。

202 响应只允许 `attempt_id`、`attempt_status`、`generation_revision`、`retry_after_ms` 和归属的非敏感数值，不带问题半成品、回答、Proposal、snapshot 或 Provider 文本。201/200 才返回完整的结构化问题/Proposal；历史接口只读，不触发模型调用。

## 8. 破坏性迁移与遗留清理

本功能选择破坏性迁移，不保留旧 MockSession 兼容层。当前最新迁移版本是 `0015_interview_review_history_retention`；实现使用新的唯一版本 `0016_event_bound_mock_interview`，不得复用旧版本号。

迁移顺序：

1. 短事务读取并删除所有 `mock_sessions` 及其 `conversation_id` 对应的 `chat_messages`；随后删除仅由这些会话创建的 `conversations(mode='mock_interview')`。不能删除普通 Chat conversation 或普通 ChatMessage。
2. 删除旧表的命名索引后删除 `mock_sessions`，避免旧索引与新对象同名冲突；确认表不存在后再创建新表、索引和唯一约束。
3. 创建 `mock_interview_attempts`、`mock_interview_turns`、`mock_interview_feedback_proposals`、`mock_interview_review_drafts`。Attempt 的 Application/Event/Resume 标识不设级联外键；子表按确认状态使用限制删除，保证冻结历史不因事件或 Resume 删除而丢失。
4. 使用 `INSERT OR IGNORE` 记录 `0016_event_bound_mock_interview`；重复启动不能重复删表、重复建索引或丢失新表数据。

旧自动保存的正式 `InterviewNote` 是既有复盘领域数据，不是 `mock_sessions` 或 Chat 消息本体；迁移不得用模糊内容猜测并删除它们，也不得将它们伪装成新的 Attempt/Turn。它们继续按既有 InterviewNote API 管理，旧 MockSession 数据本身不保留。

实现清理清单：

- 删除 `MockSession` 模型、`MockSessionsRepository`、API import/实例、`MockSessionOut`、`_mock_session_json`、`_mock_scoring_prompt`、`_mock_transcript`、`_save_mock_feedback_note` 和全部 `/api/mock/sessions*` 路由；旧路径应返回普通 404，不提供兼容错误处理。
- 从 `APPLICATION_FOREIGN_KEY_MODELS`、数据库 reset/cleanup 分支、smoke 清理和 conditional-delete 测试移除 `MockSession` 依赖，改为新 Attempt 的明确依赖测试。
- 删除 `web/src/components/MockStudio/*`、`web/src/services/mock.ts`、`web/src/types/mock.ts` 及旧测试；从 `navigation.ts` 删除 `ViewMode='mock'` 和旧测试断言。
- 删除 Chat 的 `mock_interview` mode 过滤与 `mock-*` 能力类型；保留通用 Chat 能力，不把新模拟面试伪装成 Chat conversation 或工具调用。

迁移测试必须使用包含旧 `mock_sessions`、命名索引、关联 `chat_messages/conversations` 的真实上一版本 DDL，而不是先用当前 `create_all()` 掩盖缺失；断言旧表/路由不可用、旧会话消息已删除、普通 Chat 不受影响、新表与 `0016` 可重复启动且 Attempt/Proposal 哈希不变。

## 9. 前端状态与 HITL

`AppShell` 或其持久 reducer 按 `(applicationId,eventId,attemptKey)` 持有配置输入、Attempt 状态、Turn、未知结果 key 和历史恢复信息；Drawer 只是受控视图。关闭或切换事件时：

- 普通取消且结果确定未写入：调用服务端 DELETE 成功后清理 draft；
- Provider/网络结果未知：不调用 DELETE，不清理 key，重进显示“结果待确认”，只允许同 key 查询/重试；
- 404/来源冲突/契约失败：服务端已明确不可继续时清理当前上下文和 key，但保留历史只读记录；
- 任何状态下都不能自动创建 Review Draft、InterviewNote 或其他领域写入。

UI 固定文案全部中文，至少包含“开始文本模拟面试”“本次只围绕已安排的面试事件练习”“请选择本次使用的简历”“JD 将按当前输入冻结”“AI 只提供练习建议，不判断录用结果”“结束并查看反馈”“确认保存模拟练习复盘草稿”“暂无可验证的练习建议”和上述安全错误。事件、公司、职位、JD、简历标题、回答、证据摘录和 AI 建议正文是动态内容，保持原文，不做文案扫描误伤。

历史查看显示 Attempt 的冻结事件/Resume/JD 摘要、Turn 问答、不可变 Proposal、已确认草稿和各项来源状态。来源变化只影响生成/确认能力，不改写历史；Application 软删除后不在全局索引泄露，深链显示固定 404 并清空当前上下文。

## 10. 测试先行与验收

### 10.1 后端专项

- 入口：仅可见、未软删除、已排期、`event_type=interview`；跨 Application、未排期、非 interview、缺 Resume、空 JD、URL fallback 均为稳定 404/422，且 Provider 未被调用。
- 迁移：真实旧 DDL、旧命名索引、旧会话/消息清理、普通 Chat 保留、新表建立和重复迁移幂等。
- Attempt/Turn：冻结输入 `source_fingerprint` 与可变 `transcript_fingerprint` 分离；覆盖首题、回答提交、下一题、结束反馈各自 key 的唯一约束、同 key 重放、不同 payload 冲突、回答正常追加不触发来源漂移、修改输入创建新 key、普通取消删除、未知取消保留。
- 并发：两个独立 SQLite connection 的首次请求只插入一个 Attempt/只调用一次 Provider；lease 到期两个 owner 仅一个接管；晚到 owner 不能覆盖 ready；陈旧 revision/token CAS 失败。
- 失败：Provider/网络/超时保留 key；稳定契约失败为 502 且不写 Proposal；安全空结构严格校验；来源变化 409；事件删除、Resume 删除、Application 软删除按本设计返回；日志不含敏感字段。
- 证据：JD、Resume canonical Pointer、Turn 路径和逐字摘录逐项校验；伪造 source/path、空白/拼接/Unicode 改写摘录和额外字段拒绝。
- HITL：未确认不生成 Review Draft；两个独立连接并发确认只原子创建一份，`UNIQUE(proposal_id)` 和同 key 重放均覆盖；原 `InterviewNote` 不变；Knowledge/Question/Memory/Wakeup/MockSession/Application/Event/Resume/投递状态均无跨领域写入。
- 旧接口：`/api/mock/sessions` 及其子路径不可用；旧模型/仓储/类型不再注册或被 import。

### 10.2 前端专项

- 面试索引只对单个合格事件显示入口；多事件不会自动选错；Pilot 必须携带精确 Application/Event。
- 配置、确认弹窗、逐题文本对话、结束反馈、逐项选择、二次确认、取消和历史只读均有交互测试。
- `generating`/`provider_unknown` 重挂载后控件仍冻结且复用原 key；确定失败才清 key；成功后重新开始使用新 key。
- 404/409/422/410/502 和未知错误只显示安全中文，不显示 Axios、服务端原文、模型原文或密钥。
- 反馈校验覆盖：strengths/practice_points 缺少回答证据被拒绝；只引用 JD/Resume 伪装为表现被拒绝；follow-up 固定 `question_id` 与逐字常量通过，任意自造无证据问题被拒绝；next_practice_steps 缺少回答证据被拒绝。
- 旧 MockStudio、旧 service/type、旧 `/mock` 导航入口不再出现；不把 Feedback Proposal 文案渲染成分数、录用判断或“弱点画像”。

### 10.3 隔离 real-AI API 与浏览器验收

real-AI 验收创建临时数据目录，只复制现有 `config.json`，使用动态空闲端口并验证监听进程属于本次 harness；不读写用户正式数据目录，不访问招聘平台。

API smoke 至少创建三组不同的合成已排期面试事件，逐组选择 Resume、提交非空 JD、完成至少两轮回答并结束；每组终态必须是合法结构化 Proposal 或经服务端严格校验的安全空 Proposal。Provider/网络未知必须在有界等待后用原 key/原请求重试，超时即失败，不以未完成的 202 作为通过。至少一组要求非空、有逐字 Evidence ref 的反馈，所有组检查公开字段白名单和哈希归属。

浏览器必须完成：

1. 顶层“面试”→明确选择一条已排期面试事件→选择简历→确认 JD/可选准备建议；
2. 逐题文本回答→结束复盘→查看结构化反馈；
3. 用户选择反馈条目→二次确认→查看独立模拟练习复盘草稿；
4. 关闭后重开历史，只读显示冻结事件、Turn、Proposal 和来源状态；未知结果路径用同 key 恢复；
5. 数据库断言 Application/Event/Resume/JD、正式 InterviewNote、Material Kit、Knowledge、Question、Memory、Wakeup、Reminder、投递状态和旧 MockSession 均未被意外改变。浏览器网络 allowlist 只允许本地静态资源和本地 `/api`；浏览器不得直接访问 AI Provider 或任何外部域名。AI Provider 外联只能由服务端发起，服务端 harness 单独校验实际目标为配置的 AI endpoint，且不属于招聘平台或公司网站。

浏览器 harness 必须记录浏览器网络请求清单并对 provider 域名直接请求失败；服务端 harness 单独记录 provider 目标主机、请求次数与是否为配置 endpoint。两份证据不能合并成“浏览器访问 Provider”。

清理按依赖顺序删除子表、Attempt、合成 Event/Application/Resume 及临时配置；清理前先比较跨领域基线，清理后再断言隔离目录无残留。源数据目录需做全文件快照前后比较并完全一致。

## 11. 实施顺序、风险与回归范围

1. **迁移与遗留删除**：先写真实旧库迁移测试，再移除旧模型/API/UI/type/test；风险是共享 Conversation/Chat 清理误删，回归必须证明普通 Chat 保留。
2. **Attempt/Turn 仓储和状态机**：先实现短事务、lease、CAS、fingerprint 和不可见性校验；风险是慢模型占用 SQLite 或晚到回写，重点跑双连接 barrier 测试。
3. **严格 AI 与 Feedback Proposal**：实现 Provider 能力分支、一次格式修复、证据校验和安全空；风险是把反馈变成评分/决定或泄露模型原文，覆盖结构负向测试和脱敏日志。
4. **HITL Review Draft**：确认事务只写独立草稿；风险是绕过人工确认写入 InterviewNote/Knowledge，覆盖跨领域零写入断言。
5. **面试索引、投递详情、Pilot 和历史 UI**：接入唯一事件入口与受控 reducer；风险是错误事件选择、未知 key 丢失、中文错误透传，覆盖真实卸载/重挂载测试。
6. **隔离 API/浏览器和发布门禁**：最后再执行真实 Provider；Provider 稳定性不足时如实标为验收阻塞，不放宽契约或把 502 降级成通过。

本任务明确不做：Offer 决策助手、模拟面试以外的通用练习平台、录音/转写/语音、公司或招聘平台访问、自动投递、知识库自动扩张、Memory/能力画像、自动提醒、Question 生成、正式 InterviewNote 自动创建，以及旧 MockSession 的兼容层。

## 12. 设计自审结论

- 本文档只定义新设计，没有修改后端、前端、数据库或测试实现。
- 旧自由评分和自动保存语义已明确归为删除对象；新流程只有事件级入口、严格证据和人工确认。
- Provider 慢调用释放 SQLite，未知结果保留原 key，稳定契约失败不写 Proposal，ready/历史不可被晚到 owner 覆盖。
- 所有具体建议都需要冻结 JD、Resume 或 Turn 的逐字 Evidence；没有可靠证据时只允许安全空结果。
- 已检查全文，无占位符或未决的实现选择；下一步等待设计复审，不进入实施计划或代码修改。
