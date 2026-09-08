# Write Operation Ledger 设计

状态：已复审通过

日期：2026-08-19

分支：`feat/20260819-write-operation-ledger`

固定基线：`5e560580e86da7d1eb272e0df9d3d13304717499`（Tool Execution Pipeline 第二期最终提交）

## 1. 背景

前两期已经建立了两条互补但边界不同的基础设施：

- Durable Execution Journal 记录 Run / Segment / Event，故障时 fail-open，只用于诊断；
- Tool Execution Pipeline 统一了 25 个模型可见工具的契约、校验、确认、类型化结果和执行顺序，但只保证单次 `execute_prepared()` 内最多调用一次 executor。

当前 12 个模型可见写工具和 3 个确定性 Legacy 写动作最终都写入同一个业务 SQLite，但 Repository 各自创建 Session 并自行提交。确认 claim、领域写入、工具结果、Pending Action 清理分属多个事务。因此进程可能在以下位置退出：

```text
确认 claim 已提交
→ 领域写入已提交
→ ToolMessage / Pending Action 尚未持久化
→ HTTP 或 SSE 响应丢失
```

此时客户端无法安全判断第一次写入是否成功。重试可能再次调用 executor，现有 15 分钟 confirmation claim lease 只能减少并发，不能给出跨请求的业务 exactly-once。

第三期引入 Write Operation Ledger。Ledger 是业务写操作的幂等与结果真值，但不是领域状态真值，也不是事件溯源：

```text
Domain tables
  → 当前业务状态的真值

Write Operation Ledger
  → 某个 operation_id 是否提交、首次结果是什么的真值

Durable Journal
  → 执行过程发生过什么的诊断记录
```

## 2. 目标

本期必须实现：

1. 每个 Agent 发起的真实业务写入获得服务端生成、持久化的 `operation_id`。
2. 相同 `operation_id` 的领域事务最多提交一次。
3. 响应、进程或 SSE 连接在领域提交后丢失时，重试回读首次结果，不再次调用 executor 或 Provider。
4. Ledger 与领域写入共享一个 SQLite 事务；不存在“领域成功、Ledger 未提交”或相反状态。
5. 生命周期可审计：

   ```text
   proposed
     → rejected
     → approved → claimed → committed | failed
   ```

6. 明确区分领域写入结果和后续说明交付结果，能够表示“领域已 committed，但 Provider 说明或传输失败”。
7. 保留现有 HITL、Pending Action、确认编辑、确认拒绝、CAS、业务归属校验、Provider Schema、用户可见工具结果和调用顺序。
8. 12 个 Typed 写工具、3 个确定性 Legacy 写动作以及现有 4 类 AI 写入补偿操作全部纳入机械清单；不能静默漏接。
9. Ledger 故障时写操作 fail-closed，executor 为零次；读工具和非 Agent 业务界面不因 Ledger 不可用而停止。
10. 不把原始参数、异常对象、Prompt 或模型回答写入 Ledger、Journal 或普通日志。

## 3. 非目标

本期不实现：

- Context Projector；
- SSE reconnect/replay 事件流；
- 多 Agent、后台 Wakeup 或分布式数据库；
- 任意外部 HTTP、Provider、文件系统或浏览器副作用的 exactly-once；
- 对不同 `operation_id` 做语义去重；
- 从 Ledger 重建领域数据库；
- 把所有手工 UI / REST CRUD 写入改造成 Agent Operation；
- 把 3 个确定性 Legacy 动作迁移为模型可见 Typed Tool；
- 新的用户可见 Operation 调试界面；
- Ledger key rotation、归档或清理策略；
- 取代领域 Repository 已有的 revision、归属、唯一约束和 idempotency key。

## 4. 精确范围

### 4.1 12 个 Typed 主写操作

```text
create_application
update_application_status
create_application_event
update_application_event
delete_application_event
add_note
update_note
delete_note
update_offer
save_offer_assessment
resume_update_career_intent
resume_rewrite_highlight
```

### 4.2 3 个确定性 Legacy 写操作

```text
save_application_jd_version
create_application_submission_snapshot
record_application_outcome
```

它们继续由服务端确定性流程创建、由可信 Pending Action 恢复，仍不进入 Provider Tool Schema。本期只让其领域提交经过 Ledger，不借机迁移 Legacy Tool 协议。

### 4.3 4 类现有补偿写操作

```text
undo:update_application_status
undo:create_application
undo:create_application_event
undo:add_note
```

补偿操作作为 `operation_role=compensation` 的子 Operation，引用原始 `parent_operation_id`。同一原始 Operation 只允许一个成功补偿 Operation。重试已经成功的补偿时回读首次补偿结果，不把第二次 CAS 冲突误报给用户。

### 4.4 明确排除

Conversation / ChatMessage / Pending Action 的控制状态持久化不是“领域业务写入”，不单独建立 Operation。它们仍参与 proposal、claim 和 delivery 的 CAS。手工表单、普通 REST CRUD、Knowledge、Interview、Offer Negotiation 等非 Agent Tool 写入维持各自既有幂等契约。

“本期全部写入”特指上述 Agent Runtime 15 个主写入口和 4 个既有补偿入口，不表示 OfferPilot 所有 API 写端点。

## 5. 方案比较

### 方案 A：Ledger 与领域写入分别提交

先把 Ledger 标记 claimed，再调用现有 Repository；结束后把 Ledger 标记 committed。

优点是改动小。缺点是任意两个提交之间退出都会产生无法自动判定的分叉：claimed 可能没有领域写入，领域写入也可能已经成功但 Ledger 仍是 claimed。增加 lease 只能把未知状态延后，不能证明结果。

不采用。

### 方案 B：给每个领域表增加 `operation_id`

在 Application、Event、Note、Offer、Resume 等表分别增加唯一列，以领域行本身承担幂等。

它能保护部分 create，但 update/delete、一个操作更新多行、失败结果回读和统一审计仍需要另一套协议。它还把 Harness 身份污染到所有领域模型中。

不采用。

### 方案 C：同库 Unit of Work + 统一 Ledger（采用）

Coordinator 打开一个业务 Session 和 `BEGIN IMMEDIATE` 事务，原子完成 Pending claim、Ledger claim、领域 executor、结果投影与 Ledger terminal。Repository 在此路径使用显式绑定的外部 Session，只 `flush`，绝不自行 commit/rollback。

```text
prepare / read-only preflight
  → BEGIN IMMEDIATE
  → reload Ledger identity / terminal replay check
  → Session-bound mutable recheck
  → Pending claim CAS
  → approved transition
  → claimed transition
  → tool.started
  → SAVEPOINT
  → transactional executor
  → result + undo + transport projection
  → committed | failed transition
  → COMMIT
```

优势：只有一个原子提交点；进程退出会同时回滚领域变更和本次 Ledger transitions；提交后任何重试都能读到稳定 terminal。代价是需要逐领域拆出无自提交的 Session-bound 核心方法。本期 15 个 executor 均为本地 SQLite 操作，不包含网络或 Provider 调用，适合该方案。

## 6. 核心保证与限制

### 6.1 保证

对服务端生成且身份匹配的同一 `operation_id`：

- 最多存在一个 committed 领域事务；
- committed/failed/rejected 终态不可变；
- committed 重试返回首次 `result_json`、可见工具结果、transport projection 和 undo；
- failed 重试返回首次固定失败分类和可见错误；
- rejected 首次 owning request 只启动一次基线 continuation，executor 为零；continuation 内 Provider/read-tool 循环次数不变；rejected 重试保持 terminal，executor/Provider 均为零；
- Pending/Ledger 是确认恢复唯一持久真值；旧、坏或缺失的 LangGraph checkpoint 不参与身份或 stale 判定；
- terminal continuation 由持久 generation/token/lease fence 保护；active owner 不被 fallback 抢占，expired owner 的晚到 Bundle 不能提交；
- 单次请求不会因为 Journal、renderer、transport、undo 或后续 Provider 失败而第二次调用 executor；pre-commit internal failure 会回滚整个事务，后续请求只有看到仍为 proposed 才可重试；
- 不会在 commit 状态未知时自动进行第二次 executor 调用。

### 6.2 不保证

- 不同 `operation_id` 即使参数完全相同，也表示两个独立、经用户分别确认的操作；Ledger 不自动合并。
- executor 在一个已确定整体回滚的本地事务中可能被后续请求再次调用；保证的是领域提交最多一次，不是进程内 Python 函数调用在所有崩溃模型下恰好一次。
- 本协议只允许事务内本地 SQLite 副作用。未来带外部网络、文件或 Provider 副作用的写工具必须另行设计 outbox/remote idempotency，不得直接声明为本期 transactional executor。

## 7. 数据模型

迁移版本固定为 `0026_write_operation_ledger`，新增两张表，并为 Conversation 与 ChatMessage 增加私有控制列。

### 7.1 `write_operations`

| 字段 | 约束与语义 |
| --- | --- |
| `id` | 36 位小写 UUID，主键，即 `operation_id` |
| `operation_role` | `primary` / `compensation` |
| `parent_operation_id` | 补偿操作引用原始 Operation；primary 必须为空 |
| `conversation_id` | nullable FK，`ON DELETE SET NULL`；Conversation 删除后 Ledger 保留 |
| `agent_run_id` | nullable 36 位 UUID；Journal 不可用时允许为空；刻意不对 fail-open Journal 建 FK |
| `tool_call_id` | primary 必填的受控 Provider/server call id；补偿可为空 |
| `tool_name` | 精确 Operation manifest 中的名称 |
| `adapter_kind` | `typed` / `legacy_deterministic` / `compensation` |
| `status` | `proposed` / `rejected` / `committed` / `failed` |
| `fingerprint_key_id` | Ledger HMAC key id |
| `proposal_fingerprint` | 原始 proposal 参数的 HMAC-SHA256 |
| `input_fingerprint` | 批准后 effective arguments 的 HMAC-SHA256；未批准时为空 |
| `confirmation_token_fingerprint` | confirmation token 的 HMAC-SHA256；不保存 token 原文 |
| `operation_request_fingerprint` | 当前 Operation role 的首次请求身份 HMAC-SHA256；按封闭 envelope 计算并不可变 |
| `result_contract` | terminal 结果 codec：`typed_json_v1` / `legacy_string_v1` / `compensation_json_v1` / `rejection_json_v1` |
| `result_json` | terminal 的 canonical typed/legacy/compensation/rejection result envelope |
| `visible_result` | 首次 terminal 兼容 ToolMessage/拒绝文本，用于精确回读 |
| `transport_json` | 首次 terminal transport projection，用于 HTTP/SSE 回读 |
| `undo_json` | 可选、已验证的现有 undo payload |
| `terminal_payload_sha256` | 对完整 terminal payload canonical envelope 的 SHA-256 完整性摘要 |
| `failure_category` | 仅固定 ToolFailure 分类 |
| `failure_code` | 受控稳定 code；不保存异常文本 |
| `delivery_status` | `pending` / `completed` / `failed` / `not_applicable` |
| `delivery_failure_code` | 固定安全 code，不保存 Provider/异常原文 |
| `delivery_outcome` | `final_response` / `chained_pending` / `fallback` / `none` |
| `delivery_message_count` | operation-bound 消息总数；pending 时为空 |
| `delivery_manifest_sha256` | 对完整 delivery manifest 的 SHA-256；不复制消息正文 |
| `delivery_next_operation_id` | chained Pending 对应的新 Operation；其余为空 |
| `delivery_generation` | fencing generation；proposal 初始为 0，每次 terminal owner/takeover 只加 1 |
| `delivery_owner_token_fingerprint` | 当前 owner 随机 token 的 Ledger HMAC；只在 terminal + delivery pending 时存在 |
| `delivery_lease_expires_at` | SQLite UTC epoch seconds；只在 terminal + delivery pending 时存在 |
| `created_at` | proposal 创建时间 |
| `approved_at` | effective input 被批准时间 |
| `claimed_at` | 本次原子提交中的 claim 时间 |
| `rejected_at` / `committed_at` / `failed_at` | 对应终态时间 |
| `delivered_at` | operation-bound message transaction 成功持久化时间，包括 deterministic fallback |
| `updated_at` | 每次合法状态变化更新时间 |

数据库约束：

- primary 必须有 `tool_call_id`，compensation 必须有 `parent_operation_id`；
- terminal 字段必须与 `status` 一致；
- fingerprint 格式固定为 `hmac-sha256:<64 lowercase hex>`；
- `failure_category` 只能来自 ToolFailure 闭集；
- `result_json` 默认 UTF-8 上限 512 KiB；
- `visible_result` 默认 UTF-8 上限 256 KiB；
- `transport_json` 默认 UTF-8 上限 128 KiB；
- `undo_json` 默认 UTF-8 上限 64 KiB；
- 每个 WriteContract 可以声明更小的字段预算，但不能超过上述默认值；
- 单个 Operation 的完整 canonical terminal payload 聚合上限为 1 MiB；
- 数据库另以各 terminal 字段 `length(CAST(... AS BLOB))` 之和不超过 1 MiB 作为防御性 backstop；
- `failure_code` 上限 128 ASCII 字符；
- primary 对 `(conversation_id, tool_call_id)` 建立部分唯一索引，防止同一已持久 ToolCall 被分配两个 Operation；
- `(parent_operation_id, operation_role)` 对 compensation 建立部分唯一索引；
- terminal 结果更新使用 status CAS，不能覆盖首次结果。

`parent_operation_id` 与 `delivery_next_operation_id` 对同表使用 `ON DELETE RESTRICT` FK；primary 的 `parent_operation_id` 必须为空。SQLite 普通 CHECK 不能跨行证明 parent 状态，因此 compensation 创建时由 Coordinator 在 `BEGIN IMMEDIATE` 锁内权威查询 parent，并由 `BEFORE INSERT` 及相关字段 `BEFORE UPDATE` trigger 再验证 parent 存在、`operation_role=primary` 且 `status=committed`。chained delivery 的 trigger 验证 next Operation 与旧 Operation 同 conversation、`operation_role=primary`、`status=proposed`；Coordinator 和 delivery manifest 再验证其 tool identity 与 persisted chained Pending 相同。`agent_run_id` 只作为有界关联引用，不建立跨 fail-open Journal 的外键，否则 Journal 缺失或清理会反向阻塞业务写入。

字段组合使用 role-aware 数据库 CHECK 固定：primary proposed 在 decision 前允许 request fingerprint 为空，必须有 proposal/token fingerprint；compensation proposed 创建时必须已有 request fingerprint，proposal/token fingerprint 为空。rejected 只允许 primary，且不得有 input/claim/commit/failure 字段，但必须有 operation request fingerprint、rejection envelope、摘要和 rejected 时间；committed/failed 都必须有 operation request fingerprint、input fingerprint、result envelope、摘要和对应时间，failed 还必须有固定 failure category/code。`approved_at/claimed_at` 只允许 committed/failed；首次 request fingerprint 与 terminal envelope 一经写入不可变。所有文本 byte 约束使用 `length(CAST(value AS BLOB))`，不使用字符数近似 UTF-8 大小。

delivery CHECK 固定：Operation proposed 时 `delivery_status=pending`、generation=0，owner/lease/outcome/count/manifest/next id/delivered time/failure code 均为空；primary terminal + delivery pending 必须 generation>=1、owner/lease 非空，其余 delivery result 字段为空；completed 必须 generation>=1、owner/lease 清空、outcome 为 `final_response` 或 `chained_pending`、message count 至少为 2、manifest 和 delivered time 非空、failure code 为空；failed 必须 generation>=1、owner/lease 清空、outcome=`fallback`、message count 恰为 2、manifest/delivered time/failure code 非空；只有 `chained_pending` 允许且必须有 `delivery_next_operation_id`；compensation 的 not_applicable 固定 generation=0、`outcome=none/message_count=0`，owner/lease/manifest/next id/failure code 为空且 delivered time 与 terminal time 相同。delivery terminal 字段只能由带 generation/owner fencing 的 pending CAS 一次写入；trigger 禁止 generation 回退、跳号以及 active owner 被无条件覆盖。

全部字段预算和 1 MiB 聚合边界必须在任何 terminal CAS 前验证；执行型 Operation 还必须在领域 SAVEPOINT 释放前完成验证。任一超界都回滚整个 Operation 事务、保持 `proposed`，返回固定 `operation_result_too_large`；不会出现领域已写入但结果无法回读。该错误不自动重试，修复数据或 Contract 后可由后续请求使用原 operation id 重试。

### 7.2 `write_operation_transitions`

| 字段 | 语义 |
| --- | --- |
| `id` | UUID 主键 |
| `operation_id` | FK → `write_operations.id` |
| `seq` | 从 1 递增，`(operation_id, seq)` 唯一 |
| `state` | `proposed` / `approved` / `rejected` / `claimed` / `committed` / `failed` |
| `created_at` | transition 时间 |

Transition 不保存参数、结果、异常、用户反馈或任意 facts。正常批准写入最终持久序列固定为：

```text
1 proposed
2 approved
3 claimed
4 committed | failed
```

`approved`、`claimed` 与 terminal 在同一 SQLite commit 中持久化。它们是完整审计序列，而不是可被其他进程观察和抢占的中间租约。崩溃时三者一起回滚，Operation 保持 `proposed`。

### 7.3 Conversation 与 ChatMessage 私有列

```text
pending_operation_id TEXT NOT NULL DEFAULT ''
last_write_operation_id TEXT NOT NULL DEFAULT ''
```

`pending_operation_id` 与 pending tool identity 一起 CAS；所有 clear/replace/claim 路径必须同时匹配和维护它。`last_write_operation_id` 只关联现有 undo 控制，不对外替代 `last_write_undo_json`。

旧库中迁移前已经存在的 Pending Action 保持可确认。第一次读取/确认时，Repository 在同一事务中为它创建 proposal Operation 并填入 `pending_operation_id`。不在迁移脚本中复制或长期扫描 `pending_args`。

ChatMessage 增加：

```text
operation_id TEXT NULL
delivery_kind TEXT NULL     -- origin_tool_result | continuation_message
delivery_ordinal INTEGER NULL
```

三列必须同时为空或同时非空；已有普通消息保持为空。数据库 CHECK 固定本地 shape：

```text
delivery_kind=origin_tool_result
  → delivery_ordinal=0
  → role=tool AND tool_call_id 非空

delivery_kind=continuation_message
  → delivery_ordinal>=1
  → role IN (assistant, tool)
  → role=tool 时 tool_call_id 非空
  → role=assistant 时 tool_call_id 为空
```

对 `(operation_id, delivery_ordinal)` 建立非空部分唯一索引，并以 FK 关联 Ledger。Coordinator 在锁内验证 message conversation 与 Operation conversation 相同；`origin_tool_result.tool_call_id` 必须等于 Operation 的 `tool_call_id`。`BEFORE INSERT` 及相关字段 `BEFORE UPDATE` trigger 对这两个跨行条件提供数据库 backstop。读取/对账时再次验证 ordinal 连续性、role、tool call 和 conversation；错误消息即使占用了唯一槽位也只能返回 `operation_delivery_unknown`，不得覆盖或伪装成成功恢复。

一次 Operation 恰有一个 ordinal 0 的原始兼容 ToolMessage，之后可以有基线 continuation 产生的多个 assistant/read-tool 消息。deterministic fallback 是唯一的 ordinal 1 assistant continuation；不能与成功 continuation 并存。全部消息、匹配 Pending 清理或新 Pending 替换、新 Operation proposal、last undo 更新以及旧 Operation delivery CAS 必须在同一个短事务中完成。

`delivery_manifest_sha256` 的 canonical 输入固定为：旧 Operation id/status/terminal digest、最终 delivery generation、按 ordinal 排序的全部 ChatMessage 持久字段、delivery outcome/failure code、旧 Pending disposition、validated undo digest，以及 chained Pending 的新 operation id/tool call/tool name/proposal fingerprint（若有）。Ledger 只保存摘要、消息数、outcome 和 next operation id，不复制模型回答。delivery completed/failed 后这些字段不可变；回放先从 ChatMessage 与新 Operation 重建 manifest 并校验，再返回 final text 或 chained Pending。

### 7.4 Delivery owner fencing

primary terminal commit 前生成 32-byte CSPRNG owner token，只把 `hmac-sha256:<64 lowercase hex>` fingerprint 写入 Ledger；算法固定为 `HMAC-SHA256(ledger_key, b"write-operation-delivery-owner-v1\0" + raw_32_bytes)`。原 token 只存在于 owning request 的瞬态对象，不进入 State、checkpoint、日志、Journal、HTTP 或 SSE。复用 Ledger HMAC key domain；随机 token 与 fingerprint 必须在进入领域事务前准备，事务内不做 keyring 或文件 I/O。初始 lease 由事务内纯 SQLite 时间表达式设置，不需要外部 I/O。

瞬态 `DeliveryOwnership` handle 保存 operation id、generation、raw token 和 fingerprint；字段 `repr=False`，禁止通用 serialization/copy，delivery terminal 或 fencing 后立即释放引用。Repository 只接收该 handle 并用 fingerprint 参与 CAS，不提供按 operation id 无条件完成 delivery 的 API。

常量固定为：

```text
DELIVERY_OWNER_LEASE_SECONDS = 120
DELIVERY_OWNER_HEARTBEAT_SECONDS = 30
```

lease 的 now 与续租值均由 SQLite `unixepoch('now')` 计算，避免不同进程的应用时钟漂移。首次 primary terminal commit 原子把 generation 从 0 置为 1，并写 owner fingerprint 与 `now + 120`。`FirstTerminalCommit` 返回后启动 scoped heartbeat task，覆盖整个 continuation、Bundle 投影与 delivery transaction 重试窗口；只有 delivery completed/failed、明确被 fence 或请求放弃后才停止。Heartbeat 每 30 秒使用独立短 Session 执行 CAS：

```text
delivery_status = pending
AND delivery_generation = expected_generation
AND delivery_owner_token_fingerprint = expected_fingerprint
AND delivery_lease_expires_at > unixepoch('now')
```

命中才续租到 `now + 120`。在每个 Provider/read-tool step 前后还执行同一 fence check。pre-step 在现有短 SQLite budget 内仍无法确认有效 fence 时不得启动该 step；post-step 无法确认时不得继续下一次 Provider/read tool，也不得形成 authoritative Bundle。Heartbeat/post-step 可以在剩余 lease 内重试短 CAS，但不得重跑已经调用的 Provider/read tool；若在到期前仍无法续租或确认，停止 heartbeat、丢弃未交付 Bundle，等待 fenced recovery。

owner 的正常 Bundle、明确 continuation failure fallback、以及 commit-unknown 后的 deterministic fallback 都必须在一个 `BEGIN IMMEDIATE` delivery transaction 中重新验证 status + generation + fingerprint + 未过期 lease。成功插入全部消息并完成 delivery CAS 后清空 owner/lease，保留最终 generation 进入 manifest。任一 fence 不匹配时 transaction 不得写消息、Pending、undo 或 next Operation。

## 8. Ledger HMAC Key Domain

Ledger fingerprint 不能使用普通 SHA-256 暴露低熵状态、ID、薪资或文本候选，也不能依赖 fail-open Journal key。新增独立且强制的 Ledger HMAC key domain：

```text
write-operation-ledger.key
```

要求：

- 原子创建、并发创建保护、权限收紧和备份排除沿用第一期 keyring 的成熟实现；
- 数据库尚无 Operation 时允许首次创建；
- 已有 Operation 但 key 缺失、损坏或 key id 不匹配时，Agent 写入 fail-closed，不得静默生成新 key；
- 应用仍可启动并提供读工具、手工 UI 与普通非 Agent API；
- key、原始参数和 HMAC 输入不得进入日志、Journal、错误响应或测试资产；
- 本期不实现 key rotation。

## 9. Operation 身份

### 9.1 创建

Agent 选中写 ToolCall 并形成 Pending Action 时，由服务端生成 UUIDv4 `operation_id`。它进入 `PendingAction` 的安全控制字段，但不进入 Provider Tool Schema 或模型消息正文。

proposal 持久化必须原子完成：

```text
assistant ToolCall message
+ Conversation pending identity / pending_operation_id
+ write_operations(proposed)
+ transition(proposed)
```

任一步失败则整个事务回滚，不向客户端返回一个没有 Ledger 的确认卡。

确定性 Legacy Pending Action 同样在服务端创建时获得 operation id。澄清态尚不是可执行 proposal，不提前创建 Operation；澄清完成并真正写入 Pending 时才创建。

### 9.2 Primary 绑定字段

Primary Operation 同时绑定：

```text
conversation_id
tool_call_id
tool_name
proposal_fingerprint
confirmation_token_fingerprint
agent_run_id（若可用）
```

首次确认 decision 再绑定 primary 的 `operation_request_fingerprint`；首次批准/编辑后还绑定 `input_fingerprint`。任一不匹配返回稳定 conflict/stale，executor 为零次。`operation_id` 永远由服务端生成；客户端只能回显，不能选择新的 id 来进入 executor。

### 9.3 外部传输

Pending Action JSON 增加 `operation_id`。新版前端在同步和 SSE confirmation 请求中原样回显：

```json
{
  "conversation_id": 42,
  "operation_id": "00000000-0000-4000-8000-000000000000",
  "confirmation_token": "<existing token>",
  "approved": true,
  "edited_args": {}
}
```

后端为迁移中的旧客户端保留窄兼容：当 live Pending 仍存在时，缺少 `operation_id` 可使用服务端 `pending_operation_id`；Pending 已清除后的 terminal replay 必须携带 operation id。该兼容不允许客户端工具名决定 Legacy 路由。

请求继续使用现有 `edited_args` 语义，不新增 Provider 参数。首次 decision 在内存中 canonical 编码以下对象并计算 Ledger HMAC：

```text
request_kind               -- confirmation_v1
operation_id
tool_call_id
decision                   -- approved | rejected
edited_args_present        -- 区分字段缺失与字段存在
edited_args                -- 字段存在时必须为 object，包括空 object；仅作为 HMAC 输入
rejection_feedback_present -- 区分字段缺失与字段存在
rejection_feedback         -- 经过现有 API strip/500 字符校验后的字符串；仅作为 HMAC 输入
confirmation_token_fingerprint
proposal_fingerprint
```

该 primary envelope 使用 `request_kind=confirmation_v1` 域分离。所得 `operation_request_fingerprint` 与 decision 在首次确认事务中持久化，绝不单独保存 `edited_args` 或 feedback 原文字段。反馈规范化必须复用现有 API 语义：字段只允许 rejected 请求使用、必须为字符串、执行 `strip()`、上限 500 字符，不做额外 Unicode 规范化；字段缺失与字段存在但规范化为空仍是两个不同请求身份。

`edited_args` 严格保持当前 API：字段缺失表示未编辑；字段存在时必须是 JSON object，允许空 object；显式 `null` 或其他 JSON 类型在安全控制字段解析阶段直接返回 422，不查询 Ledger，也不形成 request fingerprint 身份状态。

规则固定为：

- live Pending 存在时，服务端从可信 proposal args 加 `edited_args` 重建 effective arguments，`input_fingerprint` 只绑定这次执行授权；
- terminal replay 不再尝试重建 effective arguments，也不使用 `input_fingerprint` 验证请求；
- terminal replay 只按当前请求重算 operation request fingerprint，并与首次值恒定时间比较；
- decision、编辑字段存在性/内容、feedback 字段存在性/规范化内容、token 或 proposal identity 任一不同均返回 `operation_input_conflict`，executor/Provider 为零次。

因此 Pending 清除后无需原始 proposal args 即可验证批准或拒绝重放。新版客户端必须保留并重发 `operation_id + confirmation_token + decision + 原始 edited_args/rejection_feedback 字段形态`。旧客户端只在 live Pending 仍存在时兼容；终态响应丢失恢复不承诺支持缺少 operation id 的旧客户端。客户端不得提交完整 Tool args 来替代服务端 proposal，也不能借 replay 改变 effective input。

## 10. Repository 与 Unit of Work 边界

### 10.1 `WriteOperationCoordinator`

Coordinator 是唯一能够把 Operation 从 proposed 推向 terminal 的组件。它持有业务 `SessionFactory`、Ledger Repository、context binder 和 Ledger key domain。

公共结果固定为瞬态对象：

```python
OperationExecution = OperationCommitted | OperationFailed | OperationReplay | OperationUnknown
```

这些对象不得进入 LangGraph checkpoint。`ToolExecutionRecord` 增加 `operation_id` 与 `replayed`，仍保持瞬态和禁止通用序列化。

首次 terminal commit 的 owning request 还可以产生瞬态 `ContinuationBundle`：

```text
origin ToolMessage
ordered continuation messages
delivery outcome = final_response | chained_pending | fallback
optional chained Pending + prebuilt Operation proposal
```

Bundle 由现有 `run_turn()` continuation 启动一次并完整运行基线循环；它可以包含 Provider → read tool → Provider，也可以以新的 write Pending 结束。Runner 以瞬态 control overlay 把旧 Pending 视为已解决，使循环可以继续，但数据库中的旧 Pending 在 delivery transaction 成功前仍保留用于恢复；新的写调用只更新 overlay 并形成 chained Pending intent。Runner 同时注入瞬态 collecting sink：sync 路径只累积消息；SSE 路径可把基线已有的非权威 progress/token delta tee 给当前 socket，同时累积最终消息，但新的 Pending 卡、operation id 和 authoritative terminal event 必须缓冲到 delivery transaction commit 之后再发送。两条路径都不得提前调用 ChatRepository。Bundle 不进入 Graph State 或 checkpoint；连接中断、进程退出或 owner fencing 失败也不能让后续请求重跑 continuation。Continuation 中的 read tool 保持现有调用次数；write tool 绝不在 continuation 中越过确认执行。新 proposal 的 UUID、fingerprint 和有界 payload 在 delivery transaction 前准备，真正的消息/Pending/Operation 写入由一次 delivery transaction 完成。

### 10.2 确认恢复真值与 LangGraph checkpoint 退役

第三期对 Chat/LangGraph 主路径做内部破坏性切换：写工具 proposal、确认恢复、chained Pending 和后续普通 turn 的持久真值只来自业务数据库中的 Conversation/ChatMessage/Pending Action 与 Write Operation Ledger。`agent_checkpoints.sqlite` 完全退出该路径，不再作为 interrupt identity、resume cursor 或 stale 判定依据。

固定协议：

- 每次 Chat `run_turn()` 固定使用 request-scoped `InMemorySaver` 和新的瞬态 graph namespace，只服务于本次 invoke；不同 HTTP/SSE 请求绝不复用 saver 或 namespace；
- 模型产生写 ToolCall 时，Graph 返回 Pending intent，由 Session-bound ChatRepository 原子持久化 assistant ToolCall、Pending 与 Ledger proposal；不要求把 interrupt 持久化到 LangGraph；
- approve/edit/reject dispatcher 先按 operation id 查询 Ledger，proposed 才读取可信 Pending；它从二者重建 prepared/claim/authorization，不调用 `Command(resume=...)`，也不验证旧 checkpoint interrupt id；
- terminal commit 后从 canonical Conversation messages + persisted/typed origin ToolMessage 启动一次新的 continuation，而不是恢复旧 graph frame；
- continuation 产生 chained Pending 时，新的 assistant ToolCall、Pending 和 Operation proposal 在 delivery transaction 中成为下一次确认的唯一身份；下一次确认重复相同 Ledger-first 协议；
- 普通下一 turn 同样从业务 Conversation messages 建立新瞬态 graph，不合并或恢复历史 checkpoint state。

升级不读取、不迁移、不修复也不依赖旧 `agent_checkpoints.sqlite`。本期保留旧文件在磁盘上但永不打开；清理属于后续独立 maintenance，不进入请求或 migration。文件缺失、损坏、只读、包含旧 interrupt 或来自上一版本都不得改变 Chat/HITL 行为，也不能触发 legacy resume fallback。Chat Agent/API 直接删除 `checkpoint_path` 参数及所有传参、`SqliteSaver`、`_assert_pending_checkpoint_identity`、persistent `_has_pending_checkpoint` 分支和 write-confirmation `Command(resume)` 路径；`resume_after_confirm` 由 Ledger-first confirmation Coordinator + fresh `run_turn()` 取代，不保留 facade、feature flag 或双轨选择。

### 10.3 Session-bound Repository

以下领域边界增加显式 `bind(session)` 或等价的 `*_in_session` 核心方法：

```text
ApplicationsRepository
ApplicationEventsRepository
NotesRepository
OffersRepository
ResumesRepository
ApplicationJDService
ApplicationOutcomesRepository
ChatRepository operation proposal / confirmation / delivery helpers
```

ChatRepository 的 Session-bound 范围必须完整覆盖：

```text
append assistant ToolCall message
create / replace Pending Action + pending_operation_id
confirmation claim / rejection CAS
append operation-bound ToolMessage
append ordered operation-bound continuation/fallback messages
clear old Pending or atomically replace it with chained Pending
create chained write Operation proposal + transition(proposed)
update last_write_undo_json + last_write_operation_id
delivery manifest + status CAS
```

约束：

- 原有 UI/API 公共方法继续自己创建 Session 和 commit，外部行为不变；
- bound 模式复用 Coordinator 的 Session，只允许 query/add/execute/flush；
- bound 模式不得调用 commit、rollback、close 或另一个 `BEGIN IMMEDIATE`；
- ToolExecutionContext 在执行时创建一个绑定 Session 的副本，Catalog 和原始 context 不变；
- executor 不接触 Session 参数，继续通过 Repository 边界工作；
- 源码/AST 门禁禁止 15 个 operation executor 调用网络、Provider、文件写入或未绑定写 Repository；
- 源码/AST 门禁同时覆盖上述全部 ChatRepository 路径，禁止 proposal、rejection、message delivery、旧 Pending clear/新 Pending replace、新 Operation proposal 或 undo 状态绕过外部 Session。

不为全仓库强行引入统一 UoW。只改造本期 15 个 executor 实际需要的领域方法，并保留各 Repository 当前公共接口。

### 10.4 SAVEPOINT

Coordinator 的外层事务持有 Pending claim、Ledger transitions 和最终 terminal。领域 executor 位于 SAVEPOINT 内。每个 WriteContract 必须为 executor 可能产生的失败声明封闭 disposition：

事务外 `prepare_call()` 只执行 Schema/decode/capability/binding audit 和只读 preflight，不形成可被执行阶段信任的 Repository 状态。`BEGIN IMMEDIATE` 成功、Operation 锁内 reload 之后，必须用绑定同一 Session 的 `ToolExecutionContext` 重新运行 mutable validator；归属、revision、stale-state、记录存在性及 WriteContract 声明的全部可变前置条件都以这次锁内读取为准。锁内 mutable recheck 通过后才能 claim Pending、创建 authorization 或写 `tool.started`。

```text
terminal_domain_failure
retryable_infrastructure_failure
```

只有明确映射为 `terminal_domain_failure` 的领域结果/异常（例如稳定 not-found、stale 或 conflict）可以形成不可变 `failed`。SQLite busy/I/O、连接故障、未映射普通 `Exception`、`internal_error`、codec/projector 缺陷均属于 retryable infrastructure/internal failure，不得冻结 Operation。

固定处理为：

- success：flush 领域变更；生成并验证 typed result、兼容可见结果、transport 以及 required undo；全部通过后才释放 SAVEPOINT 并写 committed；
- terminal domain failure：回滚领域 SAVEPOINT；生成并验证稳定 failure envelope 后写 failed；
- retryable infrastructure failure 或未映射普通 `Exception`：回滚整个外层事务，Operation 保持 `proposed`，返回安全 retryable code；
- mandatory codec、canonical JSON、projector 或任一 byte budget 失败：视为 internal contract failure，回滚整个外层事务并保持 `proposed`；
- `BaseException`：回滚整个外层事务并继续传播，不形成虚假 failed；
- executor 在单次请求中仍最多调用一次；只有后续请求看到 Operation 仍为 proposed 时才能重新尝试；
- 任何异常都不得在同一请求内触发旧路径、fallback projector 或第二次 executor 调用。

Delivery failure 只表示 terminal commit 之后的 Provider、消息持久化或 HTTP/SSE 交付失败。它不能用于掩盖 result、renderer、transport projector 或 required undo 缺失。

## 11. Pipeline 时序

### 11.1 等待确认

```text
prepare_call()
→ validation / capability / binding / read-only preflight
→ create server operation_id
→ atomic persist proposal + Pending
→ tool.proposed / approval.requested（Journal fail-open）
→ executor 0 次
```

该暂停点以业务 Pending + Ledger proposal 完成持久化，不写也不要求 LangGraph interrupt checkpoint。request-scoped Graph 在返回 Pending intent 后即可销毁。

### 11.2 Confirmation terminal-first 入口

批准、编辑和拒绝共用同一个入口顺序：

```text
parse safe control fields
→ resolve operation_id
→ read Ledger by operation_id
→ terminal: verify identity + operation_request_fingerprint + terminal payload digest
            → replay/delivery convergence
→ proposed: load trusted Pending
            → prepare/reject/claim/execute
```

`parse safe control fields` 只处理 conversation id、operation id、token、decision 和 edited/feedback 的字段存在性及有界值，不初始化模型、不解析原始 Tool args、不查询领域 Repository，也不打开或检查 LangGraph checkpoint。缺少 operation id 的旧客户端仅在 live Pending 存在时，允许读取 Conversation 的安全 Pending identity 来取得服务端 `pending_operation_id`，随后立刻回到 Ledger-first 路由；Pending 已清除时不提供该兼容。

terminal 分支必须在任何 Pending stale 判断之前执行，且不得读取 `pending_args`、调用 `prepare_call()`、运行 binding/mutable preflight、初始化 Provider/模型或取得领域 Repository。它只验证当前请求 envelope、Operation identity、完整 terminal payload 和 delivery identity。只有 Ledger 返回 proposed 后，才允许读取可信 Pending 和原始参数。

### 11.3 拒绝

拒绝仍不重新 `prepare_call()`，但保持当前基线的完整 continuation 语义：

```text
terminal-first router returns proposed
→ load trusted Pending + verify token / Pending identity
→ normalize rejection_feedback with existing API rules
→ compute operation_request_fingerprint(decision=rejected)
→ prepare delivery owner token/fingerprint outside transaction
→ BEGIN IMMEDIATE
→ reload Operation; terminal caused by a race → rollback and replay with Provider 0
→ operation proposed CAS + rejection claim CAS
→ bind operation request fingerprint
→ transition rejected + persist rejection terminal envelope
→ delivery_status=pending + generation=1 + owner fingerprint + lease
→ COMMIT
→ owning request builds original ToolMessage from persisted rejection result
→ owning request starts run_turn continuation exactly once
→ atomic delivery transaction: ContinuationBundle + Pending clear/replace + optional new proposal + undo + manifest CAS
→ return rejection delivery
→ executor 0 次
```

rejection terminal 的 `result_json/visible_result/transport_json` 必须保存现有 `_rejection_result()` 兼容结果；当规范化 feedback 非空时，该结果允许包含最多 500 字符的用户反馈，以便向 continuation 传递并精确回放现有 ToolMessage。feedback 不建立独立明文字段，不进入 Journal、普通日志或 transition。只有明确返回 `FirstTerminalCommit` 且持有有效 delivery fence 的 rejection owner 启动一次 continuation；continuation 内 Provider → read tool → Provider 或生成 chained Pending 的行为与调用次数保持基线；terminal replay、commit-unknown 对账或 delivery recovery 的 continuation/Provider/read-tool 调用均为零。

### 11.4 批准或编辑后批准

```text
terminal-first router returns proposed
→ load trusted Pending + operation_id
→ prepare_call(effective args)
→ compute operation_request_fingerprint(decision=approved)
→ prepare tool.started EventDraft, Journal key, delivery owner token/fingerprint outside transaction
→ BEGIN IMMEDIATE
→ reload Operation; terminal caused by a race → rollback and replay with Provider 0
→ verify proposed identity
→ mutable recheck(bound Session ToolExecutionContext)
→ Pending claim CAS in the same Session
→ bind operation request fingerprint
→ create ExecutionAuthorization bound to operation_id + effective args digest
→ authorization match
→ append approved / claimed transitions
→ SAVEPOINT insert prebuilt tool.started
→ SAVEPOINT
→ executor(bound ToolExecutionContext) exactly once in this attempt
→ generate and validate result / visible result / transport / required undo
→ append committed | failed + delivery generation=1/owner/lease
→ COMMIT domain + Ledger + tool.started
→ project tool.completed | tool.failed through normal fail-open Journal path
→ owning request starts run_turn continuation exactly once
→ atomic delivery transaction: ContinuationBundle + Pending clear/replace + optional new proposal + undo + manifest CAS
→ return HTTP/SSE response
```

锁内 mutable recheck、claim、authorization、operation request 或 Operation identity 失败时，不写 `tool.started`，executor 为零次。若 outer transaction 因 infrastructure/internal failure 回滚，`tool.started` 也一并消失，Operation 保持 proposed。

只有 `commit()` 明确成功并返回 `FirstTerminalCommit`、且仍持有 generation=1 有效 delivery owner fence 的 owning request 可以启动一次 `run_turn()` continuation。Continuation 内 Provider 调用、只读工具循环、多 ToolCall 语义以及“产生下一张写确认卡即暂停”全部保持第二期基线，不设置“一次 Provider”上限。进程在 terminal commit 后退出、commit 对账得到 terminal、continuation 返回后进程退出，或 delivery commit 结果未知时，后续请求均不得再次启动 continuation、Provider 或 read tool；它们按 owner lease/fencing 协议等待或用 deterministic assistant fallback 收敛交付。已经生成但尚未提交的 ContinuationBundle 可以在同一请求内重试 delivery transaction，但每次都必须重新验证 generation/token/lease，且不能再次运行 `run_turn()`。

### 11.5 Read Tool

Read Tool 不创建 Operation，不经过 Ledger，不获取业务写锁，Pipeline 行为保持第二期原样。

## 12. Journal 集成

第一期事件 Schema 不升级，不增加 `operation.*` 事件，也不把 operation id 塞入未允许的 facts。Ledger 通过 `agent_run_id + tool_call_id + tool_name` 与 Journal 关联。

普通 Journal 仍使用独立短超时 Pool。写 Operation 已持有 SQLite write transaction 时，另一个 Journal 连接无法可靠插入 `tool.started`。因此 SafeRunRecorder 增加一个窄的、可选的同 Session 投影入口：

- 仅用于本期 `tool.started`；terminal 事件在业务 commit 后走普通 Journal Pool；
- recorder、HMAC key、完整 `tool.started` EventDraft、payload size 和 dedupe identity 必须在进入业务写事务前准备；
- 事务内不允许 keyring、文件、网络、事件 canonical 化或 HMAC 计算，只允许把预构造 EventDraft 置于独立 SAVEPOINT insert；
- SAVEPOINT insert 失败只回滚 Journal 投影，不污染外层业务事务；
- Null/degraded/budget-exhausted recorder 直接跳过；
- Journal 失败仍不能改变 Ledger、领域结果或 executor 次数；
- outer transaction 回滚时同 Session 的 `tool.started` 一并消失，这是明确接受的 fail-open 诊断边界；
- terminal Journal append 失败可能留下只有 `tool.started` 的不完整诊断序列，同样不得影响业务；
- replay 不伪造 `tool.started/completed/failed`。

这保持第一期 fail-open 性质，同时避免每个业务写事务因为 SQLite 自锁而必然降级。

## 13. Result、Undo 与隐私

### 13.1 为什么需要持久结果

update/delete 后领域行可能继续变化或消失，仅保存 ID 无法回读第一次 ToolMessage。Ledger 因此必须保存第一次结果投影。它与领域表位于同一个业务数据库和备份域，是业务幂等数据，不是诊断 Journal。

### 13.2 持久内容边界

允许：

- 15 个受控 adapter 产生的 JSON result；
- 已冻结的兼容 ToolMessage 文本；
- transport projector 的既有 payload；
- 4 类现有、结构化且有界的 undo payload；
- rejection compatibility result 中按现有协议出现的、规范化后最多 500 字符的用户反馈；
- 固定 failure category/code、时间和引用 ID。

禁止：

- 原始 Tool args；
- Prompt、完整 Conversation、模型回答；
- 原始异常文本、stack trace、exception repr；
- credential、Provider request body、API key；
- 任意 adapter 未声明的对象或 pickle；
- 把 result/undo 复制到 Journal 或普通日志。

每个 WriteContract 使用封闭 codec。只接受 JSONValue，canonical 编码禁止非有限数字。terminal commit 前构造并 canonical 编码以下完整 envelope：

```text
status
result_contract
result_json
visible_result
transport_json
undo_json
failure_category
failure_code
```

其中 `result_json/transport_json/undo_json` 先解析为 JSONValue，`visible_result` 保持字符串；nullable 字段必须显式编码为 JSON null。canonical 算法沿用第二期：UTF-8、对象键排序、紧凑分隔符、禁止非有限数字、不做 Unicode 规范化。`terminal_payload_sha256` 覆盖整个 envelope，而不是只覆盖 `result_json`。任何 terminal replay、message delivery 或 compensation 前都重新计算并验证；不匹配返回 `operation_integrity_error`，绝不执行 executor、Provider 或 undo。

各字段先执行 Contract 自身预算，再对上述完整 canonical envelope 执行 1 MiB 聚合硬上限。数据库中的 byte CHECK 使用 `length(CAST(value AS BLOB))`；Python 在 SAVEPOINT 释放前用同一 UTF-8 编码再次验证。Resume 写工具的首次结果可能包含领域数据库中已经存在的简历内容，本期为了精确回读允许其出现在 Ledger result，但仍受字段与聚合双重预算约束，不进入日志、Journal 或测试 golden。任一 codec、projector、Schema 或预算失败均回滚整个 Operation 事务并保持 proposed。

### 13.3 Undo

WriteContract 必须声明：

```python
UndoPolicy.NONE | UndoPolicy.REQUIRED
```

以下四个主 Operation 固定为 REQUIRED：

```text
create_application
update_application_status
create_application_event
add_note
```

其余主 Operation 和 compensation 固定为 NONE；NONE 的 `undo_json` 必须为 null，不允许生成未受支持的伪 undo。现有 undo seed 和 result projector 移入 WriteContract，在领域事务内读取 before、在 executor 后生成 expected-after。REQUIRED undo 必须通过封闭 Schema、canonical JSON、字段预算和 terminal 聚合预算，才能释放领域 SAVEPOINT；缺失、空 fallback、projector 异常或校验失败均回滚整个 Operation 事务，不能提交领域写入。

有效 `undo_json` 与 committed 一起提交。Conversation 的 `last_write_undo_json` 和 `last_write_operation_id` 在后续原子 delivery transaction 中更新；若 delivery 暂时失败，terminal replay 会从已校验的 Ledger undo 收敛。

## 14. Terminal Replay 与交付收敛

### 14.1 回读规则

primary confirmation 和 compensation 入口都必须先按 operation id 查询 Ledger，再考虑 Pending/last-write stale：

- committed + operation identity / operation request fingerprint 一致：返回首次结果，executor 0，Provider 0；
- failed + operation identity / operation request fingerprint 一致：返回首次固定失败，executor 0，Provider 0；
- rejected + operation identity / operation request fingerprint 一致：返回首次拒绝结果，executor 0，Provider 0；
- terminal 但 identity、role-specific request envelope 或 fingerprint 不一致：409 conflict；
- proposed：继续正常 claim/execute；
- integrity 校验失败：500 `operation_integrity_error`，executor 0；
- Ledger 暂时不可读：503 `operation_result_unknown` 且 `retryable=true`，executor 0，客户端保留原请求重试。

### 14.2 Pending 已清除

response 丢失后 Pending 可能已经清除。新版客户端仍持有 `operation_id + confirmation_token + decision + 原始 edited_args/rejection_feedback 字段形态`，后端用 operation request fingerprint 直接从 Ledger 回读，不要求重新存在 Pending，也不重新进入 LangGraph/Provider。缺少 operation id 的旧客户端不支持该终态恢复。

### 14.3 Pending 尚未清除

若 primary 已进入 committed/failed/rejected terminal，但进程在 confirmation result sink 前退出，Pending 仍携带同一 operation id。重试先验证完整 terminal payload 和 delivery owner 状态；不能仅凭 Pending confirmation claim 的年龄抢占交付。active owner 存在时按 §14.4 返回 pending，owner lease 过期后才允许 fenced takeover 在一个短事务中：

```text
BEGIN IMMEDIATE
reload terminal Operation + delivery fence
INSERT origin ToolMessage(ordinal=0)
INSERT deterministic assistant fallback(ordinal=1)
clear matching old Pending / claim
apply validated last undo state when committed + REQUIRED
CAS pending + expired generation G
  → delivery_status=failed + delivery_outcome=fallback + generation G+1 manifest
COMMIT
```

`BEGIN IMMEDIATE` 的单 writer lock 保护 fallback 构造，最后一条 expired-generation terminal CAS 决定 winner；两个 INSERT、Pending/undo 收敛和 CAS 必须处于同一事务，CAS rowcount 不是 1 时全部回滚，不留下“已抢占但未交付”的窗口。若 delivery 已由一次结果未知的 transaction 提交，重放只能读取并验证已有 manifest，不能重复插入。整个收敛过程不调用 executor、continuation、Provider 或 read tool。

### 14.4 Delivery owner lease 与 takeover

terminal replay 读取 `delivery_status=pending` 时固定分支：

```text
owner lease 未过期
  → 409 operation_delivery_pending, retryable=true
  → Retry-After = clamp(ceil(lease remaining seconds), 1, 120)
  → messages/Pending/delivery 均不写，executor/continuation/Provider/read tool=0

owner lease 已过期
  → BEGIN IMMEDIATE
  → reload status/generation/lease
  → 已完成：验证 manifest 后 replay
  → 已被其他请求续租：返回 operation_delivery_pending
  → 仍过期：在锁内构造 deterministic fallback，最终以 expired generation G→terminal generation G+1 CAS 完成交付
```

只有锁内 expired-generation terminal CAS winner 能提交 fallback；loser 的整个 transaction（包括暂存消息）回滚，再读取 completed/failed 或 active lease。原 owner 续租、正常 Bundle delivery 或明确 continuation failure fallback 都必须持有相同 generation + token fingerprint + 未过期 lease。takeover 后，晚到 owner 即使已经得到 Provider answer 或 chained Pending Bundle，也因 generation/token 不匹配被 fence：丢弃整个 Bundle，不写消息/Pending/new Operation/undo，不发送 authoritative SSE event，并回读 winner 的 fallback。它不得重跑 continuation，也不得用旧 token 恢复 owner 身份。

continuation 以现有普通 `Exception` failure 明确结束时，可以在 lease 未过期期间用自己的 fence 直接写 deterministic fallback；不需要等 120 秒。取消与其他 `BaseException` 继续传播并停止 heartbeat，不在异常路径抢写 fallback；进程崩溃、任务消失或无法续租同样保持 pending，等待 lease 到期后的 takeover。

### 14.5 Delivery 状态

领域 terminal 后：

- 完整 ContinuationBundle、旧 Pending clear 或新 Pending/Operation replace、undo state 和 delivery manifest CAS 原子持久化：`delivery_status=completed`；
- continuation 明确失败时由有效 owner，或 owner lease 过期时由 takeover CAS winner 使用 deterministic assistant fallback；同一 fenced delivery transaction 持久 fallback bundle 并写 `delivery_status=failed` + 固定 code；
- 进程退出或结果未知：保持 `pending`；
- rejection 与批准路径共用 ContinuationBundle delivery；不产生 Chat message 的 compensation 使用 `not_applicable`。

成功 Bundle 的 outcome 固定区分 `final_response` 与 `chained_pending`。后者必须在同一 transaction 中持久化 continuation 的 assistant ToolCall message、新 Pending、Conversation `pending_operation_id`、新 `write_operations(proposed)` 和 transition(proposed)，并把新 operation id 写入旧 Operation 的 `delivery_next_operation_id`；绝不能先提交其中任一部分。Response-loss 对账对 final_response 验证最终消息，对 chained_pending 额外验证新 proposal/transition 和记录的 next operation identity。

Delivery 更新是 terminal 之后的独立 CAS，不能改变 committed/failed/rejected，也不能触发 executor。`delivery_status=completed/failed` 后不可回到 pending，operation-bound message、manifest 和 next operation id 不可覆盖。`agent_run_id` 允许将 committed Operation 与随后失败的 Run 对照，从而回答“领域写入成功，但说明生成失败”。同步响应以及 SSE 的 authoritative final/chained-Pending event 发生在 delivery commit 之后；为保持现有流式体验而提前 tee 的非权威 delta 不携带未提交的 Pending/operation identity。网络断开不修改已提交 delivery 状态，也不授权重跑 continuation。

## 15. 确认与 Operation 并发

SQLite 使用 `BEGIN IMMEDIATE` 获取单 writer。并发相同 operation id：

1. 两个请求都可以在事务外完成只读 prepare；
2. 第一个获得 writer lock，验证 proposed，执行并提交 terminal；
3. 第二个获得 lock 后看到 terminal，只回读；
4. 如果尚未获得 writer lock 就超时，第二个返回 `operation_busy` 且 `retryable=true`，executor 为零次；只有 fresh read 也不可用时才返回 `operation_result_unknown`。

不同 operation id 继续遵守 SQLite 现有单 writer 行为。本期不提高写吞吐，不在写锁内执行 Provider、网络或文件 I/O。

## 16. Commit 结果未知

不能把 `session.commit()` 抛错简单当成“肯定没提交”。执行 transaction 的 Coordinator 在 commit 异常后：

1. 丢弃当前 Session；
2. 用新 Session 只读查询 operation id；
3. 若 terminal 和完整性校验成立，绝不启动 continuation；若持久 owner fence 与本请求预生成的 generation/token 一致且 lease 有效，可在 fenced delivery transaction 中直接写 deterministic fallback，否则按 active/expired owner 规则返回 pending 或 takeover；
4. 若仍是 proposed，返回 503 `operation_not_committed` 且 `retryable=true`，不在同一请求再次调用 executor；
5. primary execution 对账若 Operation absent，违反“确认前 proposal 已独立提交”的不变量，返回 `operation_result_unknown` 并禁止自动重建；
6. compensation 的 absent/proposed/terminal/unreadable 分支按 §18 的两阶段状态机处理；
7. 若数据库不可读，返回 `operation_result_unknown`，不进行任何自动 retry/fallback executor。

因此一次 HTTP 请求内 executor 仍最多调用一次；只有后续请求在能够证明前一事务整体未提交时才允许重新尝试本地事务。

Delivery transaction 的 commit 异常使用相同对账原则，但查询对象是 `delivery_status`、delivery manifest、ordered messages 和 optional chained Operation：

- terminal + completed/failed + message count/ordinal/manifest 完整：按 outcome 返回已记录的 final response 或 chained Pending，不再插入；
- chained_pending 还必须存在匹配的 next Operation proposal 与 proposed transition；缺任一项均为完整性错误；
- terminal + pending + 无 operation-bound message、无 next Operation：已知前次 delivery 未提交；只有仍匹配 generation/token/未过期 lease 的原 owner可以重试内存 ContinuationBundle 或其 deterministic fallback，非 owner 在 lease 活跃时只返回 pending，lease 过期时只能通过 generation takeover 写 fallback；任何分支都不得再次运行 continuation/Provider/read tool；
- delivery commit fresh read 发现 generation/token 已变化或 lease 已过期：丢弃内存 Bundle，按 §14.4 回读/takeover，不能覆盖 winner；
- 只存在部分消息、孤立 next Operation、payload/manifest 不一致或数据库不可读：返回 `operation_delivery_unknown`，不覆盖、不补写、不运行 continuation，由完整性修复流程处理。

## 17. Legacy 集成

3 个 Legacy Adapter 保持：

- 不进入 Typed Catalog；
- 不进入 Provider Schema；
- 只能由服务端 deterministic action 创建；
- 确认入口先按 operation id 查询 Ledger；terminal 使用 Ledger 中可信的 `adapter_kind/tool_name` 回放，proposed 才读取 server Pending 并按封闭名称路由；
- 保留已有参数校验、业务 CAS、idempotency key、错误文案和返回字符串。

需要重构 `ApplicationJDService` 和 `ApplicationOutcomesRepository` 的三个写方法，使其核心逻辑能在外部 Session 中执行。原有领域 idempotency key/unique constraint 继续存在，作为 Ledger 之外的领域级第二道保护。Ledger replay 时不再次调用这些领域方法。

## 18. 补偿 Operation

`undo-last-write` 从 Conversation 读取 `last_write_operation_id` 和 Ledger 中的 immutable `undo_json`，不信任客户端 undo payload。新版前端请求携带 `parent_operation_id`；live last-write 仍存在时后端可为旧客户端使用服务端字段，last-write 已清除后的 replay 必须携带 parent id。

补偿 operation id 使用 RFC 4122 UUIDv5，算法完全固定：

```text
COMPENSATION_OPERATION_NAMESPACE = 4079900d-84a6-5cff-aa63-65089c4ccccd
parent = canonical lowercase hyphenated parent_operation_id
kind = manifest 中精确的 compensation_kind ASCII 字符串
name = parent + ":" + kind
operation_id = uuid5(COMPENSATION_OPERATION_NAMESPACE, UTF-8(name))
```

namespace 是提交在代码中的常量，不得在运行时重新派生；name 不添加空白、不做 Unicode 规范化或大小写折叠。实现使用标准 UUIDv5 的 namespace bytes + UTF-8 name + SHA-1 语义。以 parent `00000000-0000-4000-8000-000000000001` 为固定 golden：

```text
undo:update_application_status → f5cb2151-0014-5ac3-b392-01cb650c67af
undo:create_application        → 007fcd71-31a0-5489-a474-9fe0ab59bb90
undo:create_application_event  → 920dc6a7-e9b8-5484-8749-9a1cf37b1b06
undo:add_note                  → 9d8c2d9c-8a14-5e26-bad1-9fd5fb5ef73c
```

同一原始 Operation 与 compensation kind 在所有进程得到相同 id；不同 parent 或 kind 得到不同 id。

Compensation 不使用 Pending confirmation token。它使用 `request_kind=compensation_v1` 的独立封闭 envelope 计算同一字段 `operation_request_fingerprint`：

```text
request_kind               -- compensation_v1
operation_id
parent_operation_id
compensation_kind
conversation_id
```

所有值都来自服务端确定的 operation identity 或已验证的请求控制字段。Compensation 的 `proposal_fingerprint` 和 `confirmation_token_fingerprint` 必须为 null。执行授权的 `input_fingerprint` 另以 Ledger HMAC 绑定 `operation_request_fingerprint + parent terminal_payload_sha256 + validated undo_json`，不会复用 primary confirmation 算法。

补偿明确使用两阶段持久化，而不是在首次出现 operation id 的事务中直接执行领域 undo：

```text
derive deterministic operation_id
→ transaction-outside preliminary Ledger read
→ preliminary terminal: verify request fingerprint + integrity, replay
→ preliminary absent/proposed:
     BEGIN IMMEDIATE
     reload deterministic operation_id under writer lock
     if absent:
       validate committed primary parent + conversation + required undo
       insert compensation proposed + transition(proposed) + request fingerprint
       COMMIT proposal transaction
     if proposed:
       verify request fingerprint, COMMIT/close no-op proposal transaction
     if terminal:
       ROLLBACK and replay
→ BEGIN IMMEDIATE execution transaction
→ reload operation again
→ terminal: ROLLBACK and replay
→ proposed:
     revalidate request fingerprint + parent terminal digest + undo + mutable CAS preconditions
     bind input fingerprint
     append approved / claimed
     SAVEPOINT execute conditional compensation once in this attempt
     append committed | deterministic failed
     COMMIT
```

事务外 absent 只是优化提示，绝不授权 INSERT。只有取得 writer lock 后再次读取仍为 absent 才能创建 proposal。两个连接同时在事务外读到 absent 时，第一个创建 proposal；第二个获得锁后必须收敛到该 proposed/terminal 并验证 request fingerprint，不能把唯一约束异常暴露为业务结果。Execution transaction 同样锁内 reload，因此只有一个 executor winner。

旧客户端缺少 `parent_operation_id` 时，只允许从仍存在的 `last_write_operation_id` 安全控制字段取得 parent，再立即进入 Ledger-first 路由；last-write 已清除后不支持旧客户端重放。`approved` 表示用户发起 undo API 的显式意图，不表示存在 confirmation token。

Compensation commit 异常后的 fresh Session 对账固定区分四种状态：

```text
absent
  → proposal-create commit 对账：证明 executor 从未开始，返回 operation_not_committed；
    同一请求不继续，后续请求可用同一确定性 id 重新创建
  → execution commit 对账：违反“执行前 proposed 已独立提交”的不变量，
    返回 operation_result_unknown；禁止自动创建或执行，等待完整性修复

proposed
  → proposal 已存在但领域补偿未提交
  → 同一请求不重跑；后续请求可继续 execution transaction

terminal
  → 校验 request fingerprint 与 terminal payload 后精确回放

unreadable
  → operation_result_unknown；不执行、不创建、不覆盖
```

它调用现有 conditional delete/restore CAS 的 Session-bound 版本。Compensation 不调用 Provider、不创建 operation-bound ChatMessage，terminal 时原子写 `delivery_status=not_applicable`。第一次补偿成功后：

- Ledger 记录 committed result；
- Conversation 仅在匹配原 operation id 时清除 last undo；
- 响应丢失后的重试回读“已撤销”结果；
- 不再次执行 delete/restore。

如果领域记录已被其他操作修改，补偿 Operation 稳定 failed/conflict；不会覆盖新状态。

## 19. API、SSE 与前端

### 19.1 Provider

Provider 看到的 25 个工具完整 envelope、名称、顺序、description 和 Schema 必须与第二期 golden 完全相同。`operation_id` 不进入模型工具参数。

### 19.2 HTTP

以下 payload 增加 additive `operation_id`：

- pending confirmation；
- confirm success/failure/rejected；
- write-related error details；
- undo success/failure。

用户可见兼容 ToolMessage 和现有 `write_status`/undo 语义保持不变。terminal replay 明确返回 `replayed: true`；第一次执行为 false 或省略。

### 19.3 SSE

sync/stream 共用同一个 Operation coordinator 和 replay service。SSE 的 tool/result/final response 增加同一 operation id；断线重试不会尝试从 Ledger 重放旧 SSE seq，只返回一个新的请求流和已持久 terminal result。本期仍不实现 SSE reconnect/replay。

### 19.4 Frontend

前端只做隐藏控制字段传递：

- PendingAction 类型保存 operation id；
- approve/edit/reject 的同步和流式请求原样回显；
- 网络失败时保留同一 operation id、token、decision，以及 edited_args/rejection_feedback 的原始“字段缺失/字段存在 + 值”形态；
- terminal replay 按普通成功/失败渲染；
- undo 请求只回显 `parent_operation_id`，使用服务端关联的 immutable undo，不接受前端生成的领域 payload。

不新增 Operation UI、历史页或调试面板。

## 20. 错误语义

新增固定 Ledger code：

```text
operation_unavailable
operation_busy
operation_identity_conflict
operation_input_conflict
operation_result_unknown
operation_not_committed
operation_integrity_error
operation_result_too_large
operation_not_transactional
operation_delivery_failed
operation_delivery_unknown
operation_projection_failed
```

映射原则：

- proposal/claim 前 Ledger 失败：executor 0，写操作 fail-closed；
- executor 明确映射的确定性领域失败：executor 1，领域 SAVEPOINT 回滚，稳定 failed；
- SQLite busy/I/O、连接故障、未映射普通异常或 internal_error：executor 为 0 或 1，整个 Operation transaction 回滚并保持 proposed；
- BaseException：传播，整个事务回滚；
- Journal 失败：不改变 Operation outcome；
- mandatory result codec、renderer、transport projector、required undo 或 byte budget 失败：整个 Operation transaction 回滚并保持 proposed，不持久 fallback/空 undo；
- commit 结果未知：不自动重跑；
- terminal commit 后的 Provider/delivery 普通失败：保留 terminal；有效 owner 用 fence 原子持久化 deterministic fallback，否则保持 pending；expired/late owner 不得写入；
- replay 投影失败：使用已持久 `visible_result/transport_json`，不调用当前 renderer 或 executor。

日志只允许：operation id、tool name、状态、固定 code、耗时和安全计数。禁止 result、undo、参数、token、异常正文。

## 21. 迁移与兼容

`0026` 必须覆盖：

- fresh database；
- 从第二期 `0025` 升级；
- 重复 `init_database()` 幂等；
- 已有空 Pending；
- 已有非空 Pending 的 lazy proposal adoption；
- conversation 删除后 Operation 保留；
- ChatMessage operation/delivery 私有列、kind/role/tool-call CHECK、跨表 trigger、部分唯一索引和已有消息 null backfill；
- delivery generation/owner-token fingerprint/lease 字段、组合 CHECK、单调 generation trigger 和已有 Operation 的安全 backfill；
- compensation parent trigger、role-aware Operation CHECK 与所有 UTF-8 BLOB byte CHECK；
- 现有 0024 Journal 和 0025 confirmation claim 数据不变。

本分支尚未发布，因此可以直接调整 0025 之后的模型/迁移顺序，但 migration version 仍固定记录为独立 `0026_write_operation_ledger`，不重写 0024/0025 的语义。`agent_checkpoints.sqlite` 不属于业务 migration：0026 不打开、复制或删除它；旧文件存在与否都不影响迁移结果。

Ledger table 是业务真值，迁移失败不能 fail-open。数据库初始化失败时必须明确阻止 Agent 写入；不得用内存 Ledger 或旧 executor fallback 继续写。

## 22. 机械契约

新增只读 manifest，测试固定：

```text
12 typed primary
3 legacy deterministic primary
4 compensation
```

机械门禁必须证明：

- 每个 `kind=write` 的 ToolSpec 恰有一个 WriteContract；
- read ToolSpec 不得声明 WriteContract；
- 3 个 Legacy 名称恰好各有一个 Ledger adapter，仍不在 Provider manifest；
- 4 个 undo kind 恰好各有一个 compensation adapter；
- 四个可撤销主写恰好声明 `UndoPolicy.REQUIRED`，其余 operation 恰好为 NONE；
- 没有 write executor 绕过 Coordinator；
- Agent/API 不直接调用 15 个领域写核心；
- bound Repository 路径不存在 commit/rollback/新 Session；
- proposal、rejection、operation-bound message、旧 Pending clear/新 Pending replace、chained Operation proposal、undo state 和 delivery CAS 不得绕过 Session-bound ChatRepository；
- operation-bound message 只能使用 ordinal 0 的 `origin_tool_result` 和 ordinal 1..N 的 `continuation_message`；role/tool_call_id/conversation、ordinal 连续性及 manifest 必须匹配且不能绕过 CHECK、trigger 或唯一索引；
- primary confirmation dispatcher 必须 Ledger-first；terminal 分支不得引用 Pending args、prepare/preflight、模型或领域 Repository；
- primary 与 compensation 必须使用不同 request-kind envelope，compensation 不得依赖 confirmation token；
- executor 事务内不存在 Provider、HTTP、浏览器、文件写入；
- result/renderer/transport/required undo 不存在 fallback、空值降级或 terminal commit 后二次生成路径；
- Chat/HITL runtime 不接受 persistent `checkpoint_path`，不打开 `agent_checkpoints.sqlite`，不使用 `SqliteSaver`、`Command(resume)` 或 checkpoint interrupt identity；Pending/Ledger 是唯一恢复真值；
- delivery continuation 只能由匹配 generation/token/lease 的 owner 启动和提交；heartbeat、normal delivery、explicit fallback、expired takeover 均使用封闭 CAS，禁止无 fence 的 `delivery_status` 更新；
- raw delivery owner token 不得进入数据库、State/checkpoint、日志、Journal、HTTP/SSE 或异常文本；
- 不存在 `ledger_enabled` feature flag、shadow write、dual write、memory fallback 或旧路径 fallback；
- Provider 第二期 golden byte/canonical 等价；
- Journal 第一期开集不变；
- 瞬态 Graph State 中不存在 Ledger Session、Coordinator、OperationExecution、ContinuationBundle、delivery owner token、result、undo 或 key；persistent checkpoint 在 Chat/HITL 路径不存在。

## 23. 测试策略

### 23.1 模型与迁移

- 表、列、索引、CHECK、FK、UTF-8 byte 上限；
- fresh / 0025 upgrade / repeated init；
- old live Pending lazy adoption；
- terminal immutability；
- transition seq 与固定生命周期；
- primary/compensation operation request fingerprint、terminal payload digest 格式和不可变性；
- 分别篡改 status、contract、result、visible、transport、undo、failure category/code 时完整性校验都失败；
- ASCII/多字节 UTF-8 字段预算与 1 MiB canonical envelope 聚合边界；
- compensation parent 不存在、非 primary 或非 committed 时 trigger 拒绝；
- chained next Operation 缺失、跨 conversation、非 primary/proposed 或与 Pending tool identity 不一致时 FK/trigger/Coordinator 拒绝；
- operation-bound message 三列成组、kind/role/tool_call/ordinal shape CHECK、跨表 tool_call/conversation trigger 与 `(operation_id, delivery_ordinal)` 唯一；
- delivery outcome/count/manifest/next-operation 组合 CHECK 与 terminal 后不可变；
- delivery generation/owner/lease 的 proposed、terminal-pending、completed/failed/not-applicable 组合 CHECK，generation 只能按协议单调 +1；
- HMAC key create/race/permission/missing-existing-key failure。

### 23.2 Repository / UoW

对每个领域 adapter 验证：

- public Repository 仍自己 commit；
- bound Repository 不 commit；
- domain + Ledger 同一事务成功；
- executor 后、terminal 前注入崩溃时两者都回滚；
- terminal 写入后、commit 前崩溃时两者都回滚；
- 明确映射的 deterministic domain failure 回滚领域 SAVEPOINT 后可以提交 failed；
- SQLite busy/I/O、未映射 Exception 和 internal_error 回滚整个 transaction、保持 proposed；
- result codec、renderer、transport、required undo 或任一预算失败时整个 transaction 回滚、保持 proposed；
- 四个 REQUIRED undo 缺失时不能提交领域写，NONE operation 必须持久 null undo；
- proposal、rejection 和 delivery 的全部 ChatRepository bound 方法不自行 commit；
- BaseException 不形成 terminal。

### 23.3 并发与 crash matrix

- 两线程/两连接同 operation，只产生一个领域 commit 和一个 executor winner；
- 两连接 barrier 固定 primary 时序：连接 A 完成事务外 preflight 后，连接 B 修改目标并提交；连接 A 随后取得 `BEGIN IMMEDIATE`，锁内 Session-bound mutable recheck 必须观察新状态并返回 stale/conflict，executor 为零；
- committed loser 回读；
- failed/rejected loser 回读；
- 不同 input/tool/conversation/decision 冲突且 executor 0；
- 尚未取得 writer lock 的 SQLite busy 返回 `operation_busy`，executor 为零；只有 fresh read 失败才返回 `operation_result_unknown`；
- primary execution commit exception 覆盖 terminal/proposed/absent/unreadable，对 absent 使用 result unknown；
- compensation proposal/execution commit exception 分别覆盖 absent/proposed/terminal/unreadable 四种结果；
- 两连接同时在事务外读到相同 compensation id 为 absent，锁内二次读取只创建一条 proposal，第二个请求收敛到已有 proposed/terminal，最终只有一个 executor winner；
- 进程在领域 commit 后、Pending clear 前退出，下一请求只收敛 delivery；
- 在 origin ToolMessage、每条 continuation message、旧 Pending clear、新 Pending/Operation replace、undo 和 delivery CAS 的每个边界注入 commit/response loss，整个 transaction 要么全有要么全无，ordinal 不重复；
- delivery commit unknown 的 fresh Session 对账分别覆盖 final response、chained Pending、未提交和部分损坏；chained 分支必须验证新 Pending、Conversation 指针、新 Operation proposal/transition 与旧 Operation `delivery_next_operation_id`；
- 双连接 delivery barrier：owner 正在 Provider/read-tool continuation 且 heartbeat lease 有效时，replay 只得到 `operation_delivery_pending`，消息/Pending/delivery 不变，Provider/read-tool 为零；
- owner crash 后不续租：到期前 replay 不抢占，到期后两个连接同时 takeover 只有一个 generation CAS winner 原子写 fallback；
- late Bundle：owner lease 到期并被 takeover 后才返回的 final/chained Bundle 因 generation/token fencing 被完整丢弃，不覆盖 fallback、不创建 chained Operation、不发送 authoritative SSE；
- heartbeat CAS busy/失配/到期、正常 owner delivery、ordinary-Exception fallback、BaseException/cancellation 停止续租以及 commit-unknown fallback 的 generation、调用次数及最终状态；
- pre-step fence 不可确认时该 Provider/read tool 调用为零；post-step fence 丢失时不再调用下一 step、不提交 Bundle，已发生的调用恰为一次；
- 任一 terminal replay/delivery recovery 的 Provider 调用为零。

### 23.4 15 个主写 golden

每个操作至少覆盖：

- first success；
- same-operation sync replay；
- same-operation SSE replay；
- result/transport/undo 等价；
- domain row/count 只变化一次；
- executor 和 Provider 调用次数；
- declared failure stable replay；
- operation/transition 精确行。

已有领域 idempotency 的 3 个 Legacy 还要覆盖 Ledger operation id 与领域 idempotency key 的交叉冲突。

### 23.5 HITL

- approve / edit / reject；
- edit 后 input HMAC 只用于首次授权，terminal replay 只验证 operation request fingerprint；
- edited_args 缺失、空 object 和非空 object 的存在性/canonical 值分别绑定；显式 null/数组/标量返回 422，且 Ledger/Pending/Provider/executor spy 调用均为零；
- rejection_feedback 缺失、空、纯空白和非空值按现有 strip/500 字符规则绑定；
- Pending 清除后的 edited approve/reject 精确重放不读取原始 args；
- terminal-first spy 证明 terminal approve/reject 不读 Pending、不 prepare/preflight、不初始化模型/Provider、不查询领域 Repository；
- 首次拒绝不 prepare、不 executor，只启动一次完整 continuation；兼容 ToolMessage 保留规范化 feedback，continuation 内 Provider/read-tool 调用次数遵循基线，replay Provider/read-tool 0 次；
- approve/reject → read tool → final response 的 sync/SSE golden 固定消息顺序、最终 payload、Provider/read-tool 次数和副作用；
- approve → 新 write Pending 的 sync/SSE golden 固定 chained Pending、assistant ToolCall、Conversation 指针、新 Operation proposal/transition 与旧 delivery CAS 的原子结果；
- SSE spy 证明 commit 前只允许基线非权威 delta，新的 Pending/operation id 与 authoritative final event 必须在 delivery commit 后发送；delivery 回滚不得暴露不可恢复的 Pending identity；
- restart 后 approve/edit/reject 只从 Pending/Ledger 重建，不读取 checkpoint；删除、损坏、只读或预置不匹配的旧 `agent_checkpoints.sqlite` 结果完全相同；
- approve A 产生 chained Pending B 后重启，即使旧 checkpoint 仍是 A 的 interrupt，B 的确认仍按新 operation id 成功；最终完成后的普通下一 turn 也不合并旧 graph state；
- missing/foreign/malformed operation id；
- old client live-Pending compatibility；
- Pending 已清除的 terminal replay；
- active/stale claim 的 delivery 收敛；
- sync/stream 完全一致。

### 23.6 Journal 与隐私

- first execution 保持 proposed → started → completed/failed；
- replay 不伪造 started/terminal；
- tool.started draft/key 在事务外准备，事务内 spy 证明 keyring/file/network/canonical/HMAC 调用为零；
- 同 Session Journal SAVEPOINT 失败不影响 domain/Ledger；outer rollback 时 started 同步消失；
- post-commit terminal Journal 失败只留下允许的不完整诊断，不影响 Operation；
- Ledger/日志/Journal 不含 args、exception、token、Prompt、回答；
- raw delivery owner token 的数据库/State/checkpoint/log/Journal/HTTP/SSE 负向扫描；只允许 Ledger HMAC fingerprint；
- synthetic fixture 不保存真实用户内容或 key。

### 23.7 补偿

- 四种 undo first commit + replay；
- UUIDv5 固定 namespace、name 的 UTF-8 编码和四个 compensation kind golden id；
- compensation request fingerprint 精确绑定 operation/parent/kind/conversation，token/proposal fingerprint 为空；
- proposed 独立 commit 后才允许领域 compensation executor；
- 两连接 concurrent absent→locked reread 只创建一条 proposal，并只有一个 compensation executor winner；
- absent/proposed/terminal/unreadable 对账及各分支 executor 次数；
- 原领域状态变化后的 stable conflict；
- parent/child 唯一；
- undo response loss 不产生第二次领域写；
- Conversation last undo 只由匹配 parent 清除。

## 24. 发布门禁

实现完成前必须通过：

- 固定 baseline + immutable allowlist；
- 15/4 operation manifest、并集和重复检查；
- Provider 第二期 golden、Tool outcome golden、Journal sequence golden；
- 后端全量分组 manifest/duplicate/skip/aggregate；
- 前端全量分组 aggregate；
- Ruff、Mypy、frontend build、static smoke、local verify；
- 一次受控 real-AI verify；
- 本地浏览器 Chat 闭环：approve、edit、reject、approve/reject 后 read-tool continuation、生成 chained write Pending、重启后再次确认、sync、SSE、edited response-loss retry、active-owner retry、owner-crash takeover、undo retry；
- DB 证明领域行一次、Ledger terminal 一次、transition 序列正确、operation-bound message ordinal 唯一连续、final/chained Pending 原子收敛；
- Provider/Tool 调用次数证明首次 continuation 保持基线循环、replay/delivery recovery 为零；
- 独立 CR 无 P0/P1/P2；
- allowlist、未跟踪文件、`git diff --check` 和干净工作区；
- 发布报告明确内部破坏性重构、15/4 边界、业务提交 exactly-once 的精确定义及外部副作用非目标。

## 25. 破坏性变化

允许并计划进行以下内部破坏性重构：

- PendingAction、ExecutionAuthorization 和 ToolExecutionRecord 增加 operation identity；
- write ToolSpec 必须声明 WriteContract 与 UndoPolicy；
- Pipeline write 分支必须经过 Coordinator；
- 相关 Repository 拆出 Session-bound 核心；
- ChatMessage 增加 operation delivery identity 和部分唯一索引；
- Chat/LangGraph 移除 persistent checkpoint confirmation resume；`agent_checkpoints.sqlite` 退出 Chat/HITL 路径，旧文件只是不被读取的可丢弃 cache artifact；
- Ledger delivery 增加 owner token fingerprint、generation、lease 与 heartbeat/takeover fencing；
- confirmation claim 改为外层业务事务内 CAS；
- undo 从 API helper 移入 compensation adapter；
- 旧的“executor commit 后再推断 undo/result”分支删除；
- 任何绕过 Ledger 的 Agent write 路径删除，不保留 feature flag 或 fallback。

外部 Provider Tool 契约保持不变。HTTP/SSE 只增加 operation id/replayed 等 additive 控制字段，现有用户可见文本和领域语义保持兼容。

## 26. 验收结论标准

第三期只有同时满足以下条件才能宣称完成：

1. 12 个 Typed 写工具全部通过 Ledger；
2. 3 个 deterministic Legacy 写动作全部通过 Ledger，且仍模型不可见；
3. 4 类现有补偿写入全部通过子 Operation；
4. 相同 operation id 的领域事务最多 committed 一次；
5. 编辑确认与拒绝反馈在 Pending 清除后通过 operation request fingerprint 精确重放，不需要原始 args；
6. commit 后 HTTP/SSE/进程丢失能回读首次结果，executor 和 Provider 均不重跑，消息不重复；active delivery owner 不被并发 fallback 抢占，expired owner 的晚到 Bundle 被 fence；
7. domain/Ledger 原子性、terminal payload 完整性和 crash matrix 通过；
8. 四类 REQUIRED undo 与所有 replay 投影在领域提交前完整生成并通过聚合预算；
9. infrastructure/internal failure 保持 proposed，确定性领域失败才形成 terminal failed；
10. Journal 仍 fail-open，Ledger 写入 fail-closed；
11. Provider、HITL、Pending、CAS、业务结果与调用次数满足兼容 golden，包括完整 read-tool continuation 和 chained write Pending；
12. Pending/Ledger 是写确认唯一恢复真值，重启、旧/坏 checkpoint 与 chained Pending 不改变结果；
13. 没有未列明的 Agent write、persistent checkpoint resume 或 Chat delivery fencing bypass；
14. 独立复审和完整发布门禁通过。

完成第三期后，第四期 Context Projector 才能使用 Ledger 的 committed/failed/replayed 数据区分“模型提出动作”和“业务真正提交”，但第四期不属于本设计。
