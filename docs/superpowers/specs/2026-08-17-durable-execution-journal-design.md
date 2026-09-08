# Durable Execution Journal 设计

日期：2026-08-17

状态：待实施

分支：`feat/20260817-durable-execution-journal`

固定基线：`b0a5697`（叠加于 Harness Reliability Contract）

## 1. 背景与问题

OfferPilot 已具备 Conversation、ChatMessage、Pending Action、Proposal Attempt、幂等键、lease、CAS、fencing、SSE 与脱敏 Harness，但这些结构分别服务于聊天展示、业务一致性、恢复和验收，尚不能回答一条 Agent 请求完整经历了什么。

当前因果信息分散在以下位置：

- `src/offerpilot/api.py` 的普通聊天、流式聊天与确认路由；
- `src/offerpilot/ai/agent.py` 的模型循环、工具调用和人工确认；
- Conversation 与 ChatMessage 中混合保存的对话、工具调用和工具结果；
- 进程内的 SSE 状态；
- 各业务 Attempt、恢复策略和脱敏 Harness 诊断。

这导致故障排查必须跨日志、消息、Attempt 和浏览器审计人工拼接，而且进程退出后部分运行时信息不可恢复。后续要建设统一 Tool Pipeline、Write Operation Ledger 和 Context Projector，也缺少稳定的运行身份与因果关联基础。

本项目新增一个小型、持久化、失败开放的 **Durable Execution Journal**。它记录已经发生的运行事实，供内部诊断和 Harness 重建因果链使用。

它不是事件溯源系统，不替代现有业务事实源，也不承担恢复、授权、重试或 SSE 重放。

## 2. 目标与非目标

### 2.1 目标

1. 为一次逻辑 Agent 运行提供稳定 `AgentRun` 身份。
2. 区分逻辑 Run、物理请求 Segment 和模型调用 Step。
3. 持久记录路由、上下文摘要、模型调用、工具调用、人工确认和终态之间的因果关系。
4. 普通、流式、确定性路由和确认恢复使用同一事件语义。
5. Journal 故障时业务 fail-open，且故障本身可观察。
6. 不保存 Prompt、用户正文、模型正文、附件内容或密钥。
7. 提供内部确定性读取模型，重建因果 trace 并识别不完整运行。
8. 为后续 Tool Pipeline、Write Operation Ledger 和 Context Projector 提供稳定关联点，但不提前实现这些能力。

### 2.2 非目标

本期明确不实现：

- 事件溯源、状态回放或从 Journal 恢复业务状态；
- SSE 断点续传或持久化 Token 流；
- 后台任务队列、多 Agent 编排或分布式调度；
- Tool 输入输出协议重构；
- Write Operation Ledger；
- 能力裁剪、审批策略重构或 Context Projector；
- 完整 Prompt、消息正文、JD、简历、附件或模型输出归档；
- 新增公开 API、CLI 页面、设置页或前端 UI；
- 声称 Journal 与各业务写入具有跨 Repository 原子事务；
- 拆分整个 `api.py` 或重写现有 Agent Runtime。

## 3. 方案选择

### 3.1 采用：增量式 Durable Execution Journal

在现有执行路径旁增加窄 Recorder，通过稳定 Run ID 和版本化事件记录事实。业务事实源和恢复契约保持不变。

优点：

- 改动可控，可在不改变产品行为的前提下单独验收；
- 为后续重构建立可观察基线；
- Recorder 可 fail-open，也可通过 kill switch 完全关闭；
- 不把当前耦合一次性迁移到新框架。

### 3.2 不采用：完整事件溯源

以 Event Store 作为 Conversation、工具写入和确认状态的唯一事实源，理论上能回放全部状态，但会同时改变持久化、恢复、迁移和删除语义，范围过大。OfferPilot 当前不需要该复杂度。

### 3.3 不采用：仅扩展日志与 Trace JSONL

继续向诊断日志写入字段，实施成本较低，但无法提供数据库级顺序、并发去重、确认跨请求关联和进程退出后的稳定读取，不足以支撑后续 Runtime 重构。

## 4. 身份模型

```text
Conversation
└── AgentRun：一次逻辑运行
    ├── ExecutionSegment：一次物理 HTTP/SSE/确认恢复请求
    │   ├── ModelStep 1
    │   ├── Tool Call A
    │   └── ModelStep 2
    └── ExecutionSegment：后续确认、编辑确认或拒绝请求
```

### 4.1 AgentRun

一次用户输入、显式 Pilot Action 或内部系统动作触发一个逻辑 Run。若该 Run 进入人工确认，后续确认、编辑确认、拒绝或既有待确认动作的重新展示继续使用原 `agent_run_id`，不得创建第二个逻辑 Run。

Run 只保存初始身份：

```text
origin_kind = user_message | pilot_action | system
initial_route_kind = model | deterministic | unknown
initial_transport_mode = sync | stream
```

`confirmation` 是后续 Segment 的 request kind，`deterministic` 是执行路径，不是 Run 的 origin。

Run 创建时 `initial_route_kind` 可以为 `unknown`；首个 `route.selected` 通过 CAS 只写入一次初始 route。后续 Segment 的执行路径仅记录在各自事件中，不覆盖初始值。

### 4.2 ExecutionSegment

每个物理请求生成新的 UUID `execution_segment_id`，包括：

- 首次普通聊天请求；
- 首次流式聊天请求；
- `/api/chat/confirm`；
- `/api/chat/confirm/stream`；
- 编辑后确认或拒绝。

第一期不建立 Segment 表。Segment ID 作为 AgentEvent 和 AgentContextSnapshot 的必填关联字段。

`segment.started` payload 固定包含：

```text
request_kind = initial | confirmation | pending_replay
transport_mode = sync | stream
execution_path = model_turn | deterministic_action | agent_resume | deterministic_confirmation
transport_run_id = 当前 SseRun.run_id；非流式请求为空
```

三个身份不可互换：

```text
agent_run_id           逻辑运行，可跨越多个请求
execution_segment_id   一次物理执行段
transport_run_id       当前流式请求的 SseRun 身份
```

硬约束：不得把 `agent_run_id` 写入现有 SSE 协议的 `run_id`，不得改变现有 `SseRun` 的创建、seq 或响应语义。

### 4.3 ModelStep

同一 Segment 内每次真正发起模型请求时递增整数 `model_step`，并为该次调用生成稳定 UUID `model_call_id`。每个 ModelStep 必须一一对应一个 `model_input` Context Snapshot。确定性路由可以没有 ModelStep，并明确记录 Provider 调用为零。

### 4.4 序号边界

`AgentEvent.seq` 是同一 Run 内的持久事件序号，与 SSE seq 完全独立。禁止将两者映射或复用。

## 5. 数据模型

当前最新迁移为 `0023_immersive_interview_studio`。本项目新增迁移：

```text
0024_durable_execution_journal
```

迁移只新增三张表，不修改现有 Conversation、ChatMessage、Pending Action 或业务表字段。

### 5.1 AgentRun

建议字段：

| 字段 | 语义 |
|---|---|
| `id` | UUID 字符串，逻辑 Run 身份 |
| `conversation_id` | Conversation 外键，删除 Conversation 时级联清理 |
| `input_message_id` | 初始用户消息 nullable 外键，`ON DELETE SET NULL`，不保存正文，也不改变现有消息持久化顺序 |
| `origin_kind` | `user_message / pilot_action / system` |
| `initial_context_type` | Recorder allowlist 归一化后的上下文类型 |
| `initial_context_entity_id` | 经类型校验和可见性校验的内部整数或 UUID，可空 |
| `initial_context_ref_fingerprint` | 非结构化上下文引用的 HMAC fingerprint，可空，绝不保存原文 |
| `fingerprint_key_id` | 当前安装的非秘密 Key Domain UUID，非空；没有可持久化 Key Domain 时不创建 Journal Run |
| `initial_transport_mode` | `sync / stream` |
| `initial_route_kind` | `model / deterministic / unknown` |
| `status` | Journal 观察到的运行状态投影，不是业务事实源 |
| `waiting_tool_call_id` | 等待确认时的稳定关联 ID，可空 |
| `last_seq` | 已分配的最后事件序号，初始为 0，由数据库原子更新 |
| `recording_status` | `healthy / degraded` |
| `recording_error_count` | Journal 内部错误计数 |
| `failure_code` | 脱敏的最终失败类别，可空 |
| `started_at / updated_at / finished_at` | UTC 时间 |

上下文归一化规则固定为：

```text
initial_context_type = workspace | global | application | mode | unknown
```

- 只有 `application` 且 `context_ref` 是经严格解析、当前可见的内部 Application ID 时，才保存 `initial_context_entity_id`；
- `workspace` 与 `global` 不保存 entity ID 或 ref；
- `mode` 和未知类型不保存原始 type/ref，需要诊断时只保存两者 canonical tuple 的 HMAC fingerprint；
- 任意未进入 allowlist 的 `Conversation.context_type/context_ref` 都不能原样复制到 Journal。

所有引用字段采用 type-specific normalizer：数据库整数 ID、36 位 UUID 和受控 Provider Tool Call ID 可以在字符集与长度校验后保存；自由字符串、模型 ID 和不满足语法的引用只能保存 HMAC fingerprint 或留空。`transport_run_id` 必须是当前 `SseRun` 的合法 UUID；`operation_id` 必须通过既有格式校验；`model_id` 使用 HMAC fingerprint，不使用普通 SHA-256 伪装匿名化。

每次成功追加 Event、Context Snapshot 或状态转换都更新 `AgentRun.updated_at`。Trace 的 stale 判断使用 `max(AgentRun.updated_at, latest AgentEvent.created_at)`，不得只使用 `started_at`。

Run 状态固定为：

```text
running
waiting_confirmation
completed
failed
cancelled
timed_out
```

允许转换：

```text
running -> waiting_confirmation | completed | failed | cancelled | timed_out
waiting_confirmation -> running | failed | cancelled | timed_out
```

正常记录模式严格遵循上述转换，所有终态不可再次转换。已知降级模式允许任意非终态直接收敛到已经观察到的业务终态，并记录 `missing_intermediate_transition` anomaly：

```text
running              -> completed | failed | cancelled | timed_out
waiting_confirmation -> completed | failed | cancelled | timed_out
```

`status` 与 `recording_status` 相互独立；业务可以成功而记录降级。`healthy` 只表示“尚未观察到记录错误”，不能证明现实中的所有 callback 都已被记录。

`waiting_tool_call_id` 的更新规则固定为：

- 进入 `waiting_confirmation` 时，在同一 Journal 事务内写入当前 Tool Call ID 并追加状态事件；
- 恢复 `running` 时清除旧 ID 并追加 `run.resumed`；
- 同一 Run 后续产生新待确认动作时写入新 Tool Call ID；
- 进入任何终态时清除该字段。

确认请求只允许通过以下唯一查询定位原 Run：

```text
conversation_id + waiting_tool_call_id -> AgentRun
```

数据库建立非空部分唯一索引：

```sql
UNIQUE (conversation_id, waiting_tool_call_id)
WHERE waiting_tool_call_id IS NOT NULL
```

不建立“同一 Conversation 只能存在一个 waiting Run”的约束。业务 Pending Action 才是确认真相，过期或降级的 Journal 状态不能阻止新的业务操作。

### 5.2 AgentEvent

建议字段：

| 字段 | 语义 |
|---|---|
| `id` | UUID 字符串 |
| `run_id` | AgentRun 外键，Run 删除时级联 |
| `seq` | Run 内持久顺序 |
| `dedupe_key` | 同一事实的稳定去重键 |
| `event_type` | 固定事件类型 |
| `schema_version` | 事件 payload 版本，首期为 1 |
| `execution_segment_id` | 物理请求身份 |
| `model_step` | 模型调用序号，可空 |
| `model_call_id` | 模型调用 UUID，可空 |
| `source_ref_type / source_ref_id` | Message、Tool Call、Operation 等稳定关联，可空 |
| `fingerprint_key_id` | payload 中存在 HMAC fingerprint 时使用的非秘密 Key Domain UUID，可空 |
| `payload_json` | 白名单、脱敏、限长 canonical JSON |
| `payload_digest` | canonical payload SHA-256 |
| `fact_digest` | 仅由该事件稳定事实字段生成的 SHA-256，用于幂等冲突判断 |
| `created_at` | UTC 时间 |

数据库约束：

```text
UNIQUE(run_id, seq)
UNIQUE(run_id, dedupe_key)
```

幂等写入必须先按 dedupe key 查询已有事件，再执行当前状态转换校验。相同 dedupe key 和相同 `fact_digest` 返回原事件；相同 key 但稳定事实不同属于 Journal 冲突，SafeRunRecorder 将记录降级，不得静默接受。`duration_ms` 等不稳定遥测可以保存在 payload，但不属于 `fact_digest`。

### 5.3 AgentContextSnapshot

Context Snapshot 仅保存“使用了哪些上下文”的 manifest，不保存内容：

| 字段 | 语义 |
|---|---|
| `id` | UUID 字符串 |
| `run_id` | AgentRun 外键 |
| `execution_segment_id` | 物理请求身份 |
| `snapshot_key` | Run 内稳定去重身份 |
| `manifest_schema_version` | Manifest 结构版本，首期固定为 1 |
| `snapshot_kind` | `initial / confirmation_resume / model_input` |
| `model_step` | 关联模型调用，可空 |
| `model_call_id` | 关联模型调用 UUID，可空 |
| `manifest_json` | 来源 kind、稳定 ID、revision、路径类别与计数 |
| `manifest_digest` | canonical manifest SHA-256 |
| `canonicalizer_version` | 逻辑输入规范化算法版本 |
| `logical_input_fingerprint` | 逻辑 Provider 请求对象的 HMAC-SHA-256 |
| `fingerprint_key_id` | 生成 fingerprint 的非秘密 Key Domain UUID |
| `estimated_token_count` | 可空的确定性估算 |
| `token_estimator_name / token_estimator_version` | Token 估算器身份，可空但必须成对出现 |
| `created_at` | UTC 时间 |

Context manifest canonical JSON 上限为 16 KiB；单个 Event payload canonical UTF-8 上限为 4 KiB。

`snapshot_key` 在同一 Run 内唯一：

```text
initial:{segment_id}
confirmation-resume:{segment_id}
model-input:{segment_id}:{model_call_id}
```

每次调用 Agent 层 `model.complete` 或 `stream_complete` 前，都必须先生成一个 `model_input` Snapshot，再由 `model.requested` 引用其 ID。这里的“逻辑输入”严格指 OfferPilot Agent 层传给 Provider client 的规范化 messages + tools 对象，不承诺等于 Provider 最终网络字节。

`manifest_schema_version=1` 使用有界集合摘要，不枚举完整长对话或完整资源集：

```json
{
  "conversation": {
    "message_count": 184,
    "first_message_id": 1,
    "last_message_id": 184,
    "ordered_ids_digest": "sha256:...",
    "included_recent_message_ids": [169, 170, 171, 172]
  },
  "tools": {
    "count": 24,
    "ordered_names_digest": "sha256:...",
    "included_names": ["get_application", "save_application_jd_version"]
  },
  "attachments": {
    "count": 4,
    "ordered_refs_digest": "sha256:...",
    "included_refs": [{"id": "validated-internal-id", "revision": 2}]
  },
  "domain_sources": {
    "count": 3,
    "ordered_refs_digest": "sha256:...",
    "included_refs": [{"kind": "application", "id": 37, "revision": 5}]
  }
}
```

完整集合仅用 count、边界 ID 和有序集合摘要表达。`included_recent_message_ids` 固定最多 16 个；`included_names` 固定最多 32 个并保持实际 Provider tool 顺序；attachments 固定最多 16 个；domain sources 固定最多 32 个。所有代表性引用先经过 type-specific normalizer，每个字符串字段再应用独立字符集和长度上限。16 KiB 是异常防线，不是正常长 Conversation 的淘汰线。

输入指纹使用独立的每安装 `journal_hmac_secret`，计算公式固定为：

```text
HMAC-SHA256(
    journal_hmac_secret,
    "offerpilot-agent-input-v1\0" || canonical_input_utf8
)
```

Secret 不写入 `config.json`，而是与非秘密随机 UUID `journal_hmac_key_id` 一起原子写入数据目录下独立的 `agent-journal-key.json`。文件结构版本首期固定为 1，只包含 `schema_version`、`key_id` 与 base64url Secret。这样设置更新不接触该 Secret。POSIX 上 key file 设置为 `0600`；Windows 上继承当前用户数据目录 ACL，不放宽继承权限，也不宣称等价的 POSIX 权限语义。

Key file 生成或原子持久化失败时：

- Config 加载、应用启动和可正常落盘的设置更新继续；
- 当前进程禁用 Durable Journal 并使用 `NullRunRecorder`；
- 输出只含固定类别的 `journal_secret_unavailable` 安全诊断；
- 禁止使用仅存在内存的临时 Secret 继续写 Journal；
- 普通配置文件整体不可写时仍沿用现有设置 API 的失败语义，Journal 不伪造设置保存成功。

`GET /api/settings` 和 `GET /api/settings/backup` 不得返回 Journal key file、Secret 或当前 key ID。`GET /api/backups/export` 必须按精确相对路径排除 `agent-journal-key.json`，不能只依赖对 `config.json` 脱敏；备份中的 `data.db` 保留历史 Journal 行及其非秘密 `fingerprint_key_id`，但绝不包含 Secret。由于备份恢复不会携带当前 Key Domain，恢复后生成新的 key/ID；不同 `fingerprint_key_id` 下的 fingerprint 明确不可比较。若恢复后的业务仍引用旧 Journal Run，Recorder 不得用新 key 重算后与旧 fingerprint 判等，而是将该 Journal 路径标为 `fingerprint_key_domain_changed` 并 fail-open；业务确认、恢复或写入仍由既有事实源决定。

输入 fingerprint 属于敏感派生数据，不得进入普通日志或发布报告。`payload_digest` 和 `manifest_digest` 的输入已经过白名单且不含正文，可以使用普通 SHA-256。`pending_identity_fingerprint` 和模型 ID fingerprint 都使用 Journal HMAC；不得将低熵参数或模型 ID 的裸 SHA-256 描述成匿名化。

### 5.4 数据约束与索引

所有 UUID 使用 36 位带连字符的小写形式。数据库与应用层共同保证：

```text
seq > 0
recording_error_count >= 0
estimated_token_count >= 0（非空时）
manifest_schema_version = 1
AgentRun.fingerprint_key_id 为合法 UUID
AgentContextSnapshot.fingerprint_key_id 为合法 UUID
AgentEvent.fingerprint_key_id 为空或合法 UUID
length(CAST(payload_json AS BLOB)) <= 4096
length(CAST(manifest_json AS BLOB)) <= 16384
UNIQUE(run_id, snapshot_key)
```

至少建立以下索引：

```text
AgentRun(conversation_id, waiting_tool_call_id)
AgentEvent(run_id, event_type, seq)
AgentEvent(run_id, execution_segment_id, seq)
AgentContextSnapshot(run_id, execution_segment_id, model_step)
```

## 6. Recorder 架构

### 6.1 分层

新增三个职责明确的组件：

1. `AgentRunRepository`
   - 创建 Run；
   - 原子分配 seq；
   - 追加事件和 Context Snapshot；
   - 在同一 Journal 事务中更新状态并追加状态事件。
2. `RunRecorder`
   - 面向 API 和 Agent 的窄接口；
   - 校验事件 schema；
   - canonicalize、脱敏、限长；
   - 构造稳定 dedupe key。
3. `SafeRunRecorder`
   - 捕获所有 Journal 异常；
   - 输出不含用户原文的结构化诊断；
   - 在内存维护不可逆的 degraded latch，并 best-effort 将 Run 标为 `degraded`；
   - 绝不改变原 API、SSE、Provider、工具和业务写入结果。

创建 Run 本身失败时使用 `NullRunRecorder` 完成请求，并记录 `journal_run_create_failed`。`mark_degraded()` 不得递归调用自身；落库失败只输出经过分类的安全诊断，不得记录 `str(exc)`、SQL 参数或 payload。后续成功写入时可再次尝试同步 degraded latch。调用方不得直接操作 Journal 表或自行生成 seq。

SafeRunRecorder 只捕获正常 Journal 异常（`Exception`），不得捕获 `BaseException`。请求取消、进程退出和键盘中断继续遵循现有运行语义。

### 6.2 窄接口

```python
start_run(...)
start_segment(...)
capture_context(...)
append_event(...)
transition(...)
mark_degraded(...)
```

生产默认启用内部开关：

```text
OFFERPILOT_AGENT_JOURNAL_ENABLED=true
```

关闭时使用 `NullRunRecorder`。不增加设置页或公开 API。开启与关闭的 HTTP、SSE、Provider、工具、ChatMessage 和业务写入必须一致，唯一区别是三张 Journal 表是否产生记录。

### 6.3 并发与原子序号

seq 必须通过数据库原子 `UPDATE ... RETURNING` 或等价 CAS 循环取得，并与事件插入处于同一短事务。状态转换和对应状态事件也必须位于同一 Journal 事务。

以下复合写入必须原子完成：

- 创建 AgentRun、`run.started` 与初始 `segment.started`；
- 按 `snapshot_key` 幂等创建 AgentContextSnapshot、分配 seq 并写入 `context.captured`；
- 更新 Run 状态、更新/清理 `waiting_tool_call_id`、追加对应 Run 状态事件和 `segment.finished`。

`capture_context()` 在单个事务中先查询 snapshot key；相同事实返回已有 Snapshot 与 Event，不同事实进入 degraded；不存在时同时插入 Snapshot 和 `context.captured`。禁止产生孤立 Snapshot 或指向不存在 Snapshot 的事件。

优先使用：

```sql
UPDATE agent_runs
SET last_seq = last_seq + 1
WHERE id = :run_id
RETURNING last_seq
```

实现必须检测运行时 SQLite 是否支持 `RETURNING`；不支持时使用带 expected `last_seq` 的有限 CAS fallback。CAS 最多尝试 2 次。

明确禁止：

- `MAX(seq) + 1`；
- 以进程内锁代替数据库并发控制；
- 先提交状态、再 best-effort 追加对应状态事件；
- 将 SSE 序号作为持久序号。

### 6.4 有界延迟的 fail-open

仅捕获异常不足以保证 fail-open，因为当前文件 SQLite 使用单连接池并可能发生连接池或文件锁等待。Journal 使用独立的低等待预算 Session/Engine，不复用持有主业务事务的 Session。

固定边界：

- 单次连接池 checkout 或 SQLite lock 等待预算最多 50 ms；
- seq CAS 最多 2 次且不得无界退避；
- 单个 Segment 的同步 Journal 总耗时预算最多 150 ms，覆盖 schema 校验、canonical JSON、Manifest 归一化、HMAC、连接池等待、SQLite lock、事务和提交，而不只是数据库等待；
- 超出预算立即设置内存 degraded latch，并跳过该 Segment 后续非终态记录；
- Segment 结束时允许一次独立、最多 50 ms 的 disposition convergence，覆盖 `waiting_confirmation` 和所有终态；
- Journal 预算不得延长 Provider、工具、HTTP 或 SSE 的既有 timeout。

Disposition convergence 使用一个有界事务补齐最低一致集合：

```text
suspended:
    tool.proposed（此前缺失时）
    approval.requested
    waiting_tool_call_id
    run.waiting_confirmation
    segment.finished(suspended)

terminal:
    run.completed | run.failed | run.cancelled | run.timed_out
    segment.finished(completed | failed | cancelled | timed_out)
```

若该尝试仍失败，业务结果不回滚，后续确认允许进入 `journal_run_missing` 路径。

实现计划必须用受控 SQLite 写锁和连接池占用测试证明等待有界；不能只测试最终抛出异常。

## 7. 事件契约

第一期只记录重建因果链必需的事件：

```text
run.started
segment.started
segment.finished
route.selected
context.captured

model.requested
model.completed
model.failed

tool.proposed
tool.started
tool.completed
tool.failed

approval.requested
approval.decided

assistant.persisted

run.waiting_confirmation
run.resumed
run.completed
run.failed
run.cancelled
run.timed_out
```

每个事件类型都有固定 `schema_version`、字段白名单与 payload 上限。未知字段、异常对象、非普通 JSON、超限内容和敏感字段拒绝写入并使记录降级，不做“尽量序列化”。

`payload_json` 固定分为：

```json
{
  "facts": {},
  "telemetry": {}
}
```

`facts` 的字段由下表逐事件冻结，并用于 `fact_digest`。`telemetry` 只允许 `duration_ms`、`item_count`、`byte_count`、`retry_count` 和固定枚举的诊断计数；它参与 `payload_digest`，但不参与幂等事实冲突判断。

两个 digest 的计算公式固定为：

```text
payload_digest = SHA-256(canonical_json(payload_json))

fact_digest = SHA-256(
    canonical_json({
        "event_type": event_type,
        "schema_version": schema_version,
        "execution_segment_id": execution_segment_id,
        "model_step": model_step,
        "model_call_id": model_call_id,
        "source_ref_type": source_ref_type,
        "source_ref_id": normalized_source_ref_id,
        "facts": payload_json["facts"]
    })
)
```

所有 envelope 引用在进入公式前必须完成类型归一化。`run.started` 归属于初始 Segment：先生成 `agent_run_id` 和初始 `execution_segment_id`，再在一个 Journal 事务中创建 AgentRun、`run.started` 与 `segment.started`。因此所有 AgentEvent 的 `execution_segment_id` 始终非空。

示例：

```json
{
  "event_type": "tool.proposed",
  "schema_version": 1,
  "payload": {
    "facts": {
      "tool_name": "save_application_jd_version",
      "tool_kind": "write",
      "proposal_outcome": "confirmation_required",
      "args_shape_digest": "sha256:..."
    },
    "telemetry": {}
  }
}
```

需要确认时工具尚未开始，因此只能记录 `tool.proposed -> approval.requested`，不得记录 `tool.started` 或 `tool.completed`。确认通过后才记录实际执行。现有工具仍返回字符串；实际执行结果标记为 `legacy_string_v1`，不借机重构 `ai/tools.py`。

### 7.1 禁止持久化的内容

- Prompt 和消息正文；
- 模型完整响应或 Token delta；
- 工具完整参数与返回值；
- JD、简历、证据摘录、附件内容；
- API key、确认 token、幂等 key 原文；
- 未经白名单批准的异常字符串。

稳定关联使用内部 ID、分类字段、计数、长度、revision 和 hash。

### 7.2 记录时机

- `model.requested`：真正发起 Provider 请求前；
- `model.completed`：完整响应收到且基础解析完成后；
- `tool.started`：确认已通过或只读工具确定执行，并即将进入 handler 时；
- `tool.completed`：工具结果已经确定后；
- `approval.requested`：待确认动作成功持久化后；
- `approval.decided`：既有 confirmation token、Pending Action 身份与 confirmation claim 校验通过后、工具开始前；
- `assistant.persisted`：Assistant Message 保存成功后；
- `run.completed`：所有必要业务写入完成后。

进程异常退出时不补造失败事件。

`model.failed` 和 `tool.failed` 只表示一次调用失败，不决定 Run 终态。现有 Agent 可以把工具错误作为 Tool Result 交回模型并继续生成正常回复。只有现有 Orchestrator 最终无法生成并持久化有效结果，或现有业务契约明确将请求判定为失败时，才记录 `run.failed`。

`segment.finished` payload 固定为：

```text
outcome = completed | suspended | failed | cancelled | timed_out | noop
terminal_run_status = completed | failed | cancelled | timed_out | null
```

Segment 结束事件和对应的 Run 状态事件、Run 状态投影必须由一个 Repository 方法在同一 Journal 事务中追加和更新。

### 7.3 事件去重与事实字段矩阵

`confirmation_attempt_id` 关联一次批准、编辑确认或拒绝请求。若现有路径没有该 ID，则在合法确认 Segment 开始时生成 Journal-local UUID；只有身份与 confirmation claim 校验通过后才能用它记录 `approval.decided`，同一 Segment 内的 callback 必须复用它。

`route.selected.route_reason_code` 仅允许：

```text
model_default
deterministic_action_match
pending_action_replay
```

模型事件不得保存 Provider URL 或模型输出。`model.requested` 的 capability 摘要只允许 `provider_kind`、`model_id_fingerprint`、`supports_tools`、`supports_json_schema`、`stream`、`tools_count` 和 `response_format_kind`；`model_id_fingerprint` 必须使用当前 Journal HMAC key，并由 Event 的 `fingerprint_key_id` 标明 Key Domain。`model.completed` 只允许 `assistant_kind`、`tool_call_count` 和固定 `finish_category`；`model.failed` 只允许已有脱敏 failure category 和 provider outcome。

| 事件 | dedupe key | 参与 `fact_digest` 的字段 |
|---|---|---|
| `run.started` | `run.started:{agent_run_id}` | origin、conversation、context、初始 transport |
| `segment.started` | `segment.started:{segment_id}` | request kind、transport、execution path、transport run ID |
| `segment.finished` | `segment.finished:{segment_id}` | outcome、terminal Run status |
| `route.selected` | `route.selected:{segment_id}` | route kind、route reason code |
| `context.captured` | `context.captured:{snapshot_id}` | snapshot key、manifest digest、logical input fingerprint |
| `model.requested` | `model.requested:{model_call_id}` | model step、snapshot ID、上述 Provider/Model capability 摘要 |
| `model.completed` | `model.completed:{model_call_id}` | assistant kind、tool call count、finish category |
| `model.failed` | `model.failed:{model_call_id}` | failure category、Provider outcome |
| `tool.proposed` | `tool.proposed:{tool_call_id}` | tool name/kind、args shape digest、proposal outcome |
| `tool.started` | `tool.started:{segment_id}:{tool_call_id}` | tool name、result contract |
| `tool.completed` | `tool.completed:{segment_id}:{tool_call_id}` | tool name、outcome、result shape digest |
| `tool.failed` | `tool.failed:{segment_id}:{tool_call_id}` | tool name、failure category |
| `approval.requested` | `approval.requested:{tool_call_id}` | tool call、confirmation mode、`pending_identity_fingerprint`（Journal HMAC） |
| `approval.decided` | `approval.decided:{confirmation_attempt_id}` | decision、tool call、新旧输入 HMAC fingerprint |
| `run.waiting_confirmation` | `run.waiting_confirmation:{tool_call_id}` | tool call ID |
| `run.resumed` | `run.resumed:{confirmation_attempt_id}` | confirmation attempt、tool call ID |
| `assistant.persisted` | `assistant.persisted:{message_id}` | message ID、message kind；只在 ID 可直接取得时记录 |
| Run 终态 | `{event_type}:{agent_run_id}` | status、failure code |

事件 payload 可以包含 `duration_ms`、计数和时间等遥测，但这些字段不参与 `fact_digest`。重放先按 dedupe key 查询已有事件；稳定事实一致时返回原事件，再决定是否需要状态操作，不能先因当前 Run 已终态而拒绝幂等重放。

事件组合还必须满足：

- 同一 `model_call_id` 恰好一个 `model.requested`，并且最多出现一个 `model.completed` 或 `model.failed`；
- 同一 Segment 内同一 Tool Call 最多一个 `tool.started`，并且最多出现一个 `tool.completed` 或 `tool.failed`；
- 每个 Segment 恰好一个 `segment.started`，最多一个 `segment.finished`；
- 同一 Run 最多一个终态事件；
- `approval.decided` 必须能关联已持久化的 `approval.requested`，但 Pending Replay 不重复追加 requested；
- 违反互斥或前置关系时不修改既有事件，记录 `semantic_anomaly` 并使 Recorder 降级。

## 8. 四条运行时序

### 8.1 普通回答

```text
run.started
segment.started(initial)
route.selected(model)
context.captured(step=1, call=M1, snapshot=S1)
model.requested(call=M1, snapshot=S1)
model.completed(call=M1)
assistant.persisted
run.completed
segment.finished(completed)
```

最后两个事件及 Run 状态投影在同一 Journal 事务完成。

### 8.2 工具循环

```text
context.captured(step=1, call=M1, snapshot=S1)
model.requested(call=M1, snapshot=S1)
model.completed(call=M1)
tool.proposed(call=A)
tool.started(call=A)
tool.completed(call=A)
# 或 tool.failed(call=A)，Run 仍可继续
context.captured(step=2, call=M2, snapshot=S2)
model.requested(call=M2, snapshot=S2)
model.completed(call=M2)
assistant.persisted
run.completed
segment.finished(completed)
```

一次响应包含多个工具时，每个工具使用独立 `tool_call_id`。事件仅保存工具名、读写分类、耗时、结果类别、错误类别和脱敏 Operation 关联。

### 8.3 确定性路由

```text
run.started
segment.started(initial)
route.selected(deterministic)
context.captured
tool.proposed
approval.requested
run.waiting_confirmation
segment.finished(suspended)
```

该序列明确证明 Provider 调用为零，不能与 Provider 失败混淆。

### 8.4 HITL 确认与恢复

首次请求持久化 Pending Action 后记录 `approval.requested`、`run.waiting_confirmation` 和 `segment.finished(suspended)`。确认、编辑确认或拒绝只通过 `conversation_id + waiting_tool_call_id` 找回原 `agent_run_id`。现有 token、Pending Action 身份和原 Run 绑定校验通过后创建确认 Segment；confirmation claim 真正通过时才记录 `approval.decided`，随后继续以下时序：

```text
segment.started(confirmation)
approval.decided(decision=approved | edited | rejected)
run.resumed
tool.started（仅 approved / edited）
tool.completed 或 tool.failed（仅实际执行时）
context.captured（后续再次调用模型时）
model.requested / model.completed 或 model.failed（如有）
assistant.persisted（如有）
run.completed 或 run.failed
segment.finished(completed | failed)
```

确认 token、幂等键、Conversation、Pending Action、Attempt、lease、CAS 和 fencing 继续是业务事实源。Journal 只记录相关 ID 或 hash。

找不到原 Run 时，确认业务继续执行，并记录 `journal_run_missing` 诊断。Journal 写入失败时不得回滚已经成功的确认结果。

编辑确认只保存决定类型和新旧输入摘要指纹，不保存编辑原文。

拒绝不会记录 `tool.started`。若确认后模型再次提出新的写操作，则在同一 AgentRun 中更新为新的 `waiting_tool_call_id`，记录新的 `approval.requested`、`run.waiting_confirmation` 和 `segment.finished(suspended)`。

### 8.5 Pending Replay 与 Journal ingress

已有 Pending Action 被再次展示时：

- 找到对应 Journal Run：在原 Run 下创建 `pending_replay` Segment，返回既有动作，并以 `segment.finished(noop)` 结束；
- 找不到对应 Journal Run：业务照常返回既有动作，只输出安全 `journal_run_missing` 诊断，不创建 orphan Run 或 Segment。

Journal ingress 边界固定为：

- HTTP Schema 校验失败：不创建 Run；
- Conversation 不存在或不可见：不创建 Run；
- 确认 token、Pending Action 身份或原 Run 尚未绑定：不创建确认 Segment；
- 既有 Pending Action 重放按上述规则处理；
- 只有合法 Conversation 与可记录的 Agent 执行入口才创建 Run 或 Segment。

## 9. 失败与不完整运行

- 已知模型或工具调用失败：记录调用级失败事件，Run 是否继续完全沿用现有 Orchestrator 行为；
- 现有 Orchestrator 最终无法生成并持久化有效结果，或业务契约明确失败：进入 `failed`；
- 客户端取消且服务端确认停止：进入 `cancelled`；
- 服务端明确判定超时：进入 `timed_out`；
- 进程崩溃、机器退出或强制终止：保留最后事件和 `running` 状态；
- 本期不实现后台清扫器，也不自动把旧 `running` 改成失败；
- 读取端在没有 stale threshold 时只把未结束 Run 标为 `open`，不能推断进程是否已经退出；
- 业务成功但 Journal 部分失败时，Run 可以为 `completed + degraded`。

## 10. 内部读取模型

提供 Python 内部接口：

```python
reconstruct_agent_run(
    run_id: UUID,
    *,
    as_of: datetime,
    stale_after: timedelta | None,
) -> AgentRunTrace
```

返回：

- Run 身份、触发方式、业务状态与记录健康状态；
- 按 Segment 分组的物理请求；
- 每段中的 ModelStep、Tool Call 和 Approval；
- Context manifest 与输入指纹；
- 能够从现有调用链直接取得的 Message ID，以及 Pending Action、Tool Call、confirmation attempt、Operation 的稳定 ID 关联；
- lifecycle、completion 与 integrity 三个独立判定；
- 可证明的事件缺口、语义异常和已知记录降级。

返回字段固定拆分为：

```text
lifecycle_status = running | waiting_confirmation | completed | failed | cancelled | timed_out
completion_status = terminal | suspended | open | stale_open
integrity_status = healthy | known_degraded | sequence_gap | semantic_anomaly
anomalies[]
```

`waiting_confirmation` 对应 `suspended`；终态对应 `terminal`；`running` 在未提供 `stale_after` 时只能是 `open`，超过明确阈值后才能标为 `stale_open`。若同时存在多类 integrity 问题，`integrity_status` 按 `sequence_gap > semantic_anomaly > known_degraded > healthy` 取最高优先级，完整明细保留在 `anomalies[]`。

Trace 可以检测：

- 数据库中真实存在的 `1, 2, 4` seq gap；
- `model.requested` 没有 completed/failed；
- `tool.started` 没有 completed/failed；
- Segment 缺少 finished；
- 终态 Run 缺少对应终态事件；
- 已知 `recording_status=degraded`；
- 非法状态或事件组合。

seq 连续只证明已持久化事件没有数字缺口，不能证明所有现实 callback 都成功写入。Trace 不承诺绝对完整性。

`source_ref_id`、`input_message_id` 和 Message 关联是真正可空的。只有 `append_message()` 等现有调用直接返回 Message ID 时才记录；`persist_pending_action()`、`resolve_pending_confirmation()` 等没有返回 Message ID 的路径不得通过时间、正文或再次查询猜测。不得为了 Journal 修改现有 ChatRepository 返回值或消息持久化顺序。

该接口只用于测试、诊断和后续内部 Harness。它不得驱动业务恢复、授权、重试或写入，也不新增公开路由。

## 11. 与现有可靠性契约的关系

- `operation_id`：继续关联一次业务操作；
- `idempotency_key`：继续控制业务去重，Journal 不保存原文；
- Attempt、lease、CAS、fencing：继续决定谁能写入业务终态；
- SSE：继续负责实时传输，格式和 seq 不变；
- Harness Recovery Contract：继续决定错误恢复动作；
- Durable Journal：只解释这条链路已经发生了什么。

本分支叠加在 `b0a5697`，保留 Harness Reliability 的现有提交，不重写历史。

## 12. 文件边界

预计允许修改或新增：

```text
src/offerpilot/models.py
src/offerpilot/db.py
src/offerpilot/repositories/agent_runs.py

src/offerpilot/agent_runtime/__init__.py
src/offerpilot/agent_runtime/events.py
src/offerpilot/agent_runtime/journal.py
src/offerpilot/agent_runtime/keyring.py
src/offerpilot/agent_runtime/trace.py

src/offerpilot/api.py
src/offerpilot/ai/agent.py

tests/test_agent_run_migrations.py
tests/test_agent_runs_repository.py
tests/test_agent_run_journal.py
tests/test_agent_run_keyring.py
tests/test_agent_run_trace.py
tests/test_ai_agent.py
tests/test_chat_api.py
tests/test_settings_api.py
tests/test_smoke.py

docs/superpowers/specs/2026-08-17-durable-execution-journal-design.md
docs/superpowers/plans/2026-08-17-durable-execution-journal.md
docs/reports/2026-08-17-durable-execution-journal-release-verification.md
```

禁止修改：

```text
web/**
src/offerpilot/ai/tools.py
现有业务 Repository
现有 Proposal / Attempt / lease / CAS 实现
SSE 公开事件格式
公开 API Schema
README.md
```

若实施中确认必须越过边界，应先修订设计与计划，不得以“顺手重构”为由扩大范围。

## 13. 测试与验收

### 13.1 Migration 与 Repository

- 空库创建；
- 当前 `0023` 数据库升级到 `0024`；
- 重复迁移；
- Conversation 删除时 Run/Event/Snapshot 级联删除，单条 input Message 删除时 `input_message_id` 置空且 Run 保留；
- 双 SQLite 连接并发追加，seq 唯一且连续；
- `UPDATE ... RETURNING` 与运行时不支持时的有限 CAS fallback；
- 相同 dedupe key、相同事实幂等返回；
- 相同 dedupe key、遥测不同但稳定事实相同时幂等返回；
- 相同 dedupe key 下 Segment、Step、Model Call 或规范化 source ref 不同，即使 payload facts 相同也因 Event Envelope 不同进入 degraded；
- `payload_digest` 覆盖 facts + telemetry，`fact_digest` 只覆盖已冻结的 stable envelope + facts；
- 幂等终态重放先返回原事件，不被终态转换校验误拒绝；
- 严格状态转换、降级终态收敛和终态不可变；
- `conversation_id + waiting_tool_call_id` 部分唯一索引；
- Run + `run.started` + 初始 `segment.started` 同事务成功或回滚；
- Snapshot + `context.captured` 同事务成功或回滚，Snapshot key 唯一、每个 Model Call 对应一个 Snapshot；
- `manifest_schema_version=1`、36 位小写 UUID、Key Domain UUID、数值 Check 与 UTF-8 BLOB 长度约束；
- 长 Conversation、长工具表、附件与领域来源按固定上限采样，同时保留完整 count、边界与 ordered digest；
- 4 KiB payload 与 16 KiB manifest 的边界；
- 非法 JSON、异常对象和敏感字段拒绝。

### 13.2 Runtime 集成

- 普通非流式回答；
- 普通 SSE 回答；
- 确定性路由且 Provider 调用为零；
- 只读工具循环；
- 写工具进入等待确认；
- 已有 Pending Action 重新展示时复用原 Run 的 `pending_replay` Segment，找不到原 Run 时不创建 orphan；
- 无效 HTTP Schema、Conversation、token 与 Pending Action 不创建 Run/Segment；
- 确认、编辑确认、拒绝复用原 Run 并新增 Segment；
- `approval.decided` 发生在合法 confirmation claim 之后、工具执行之前；
- 等待确认和终态均产生正确的 `segment.finished`；
- 同步预算耗尽后，最多 50 ms disposition convergence 可原子补齐 suspended 的 Tool/Approval/Run/Segment 最低集合；
- 工具失败后模型继续并正常完成 Run；
- 多轮工具与多次确认；
- Provider、工具和消息持久化失败；
- 客户端取消与服务端超时；
- Event、Snapshot 和状态追加会推进 `updated_at`；模拟进程中断后，stale 使用 `max(updated_at, latest event created_at)`，在无阈值时读取为 `open`，超过显式阈值后读取为 `stale_open`；
- 读取模型分别检测 lifecycle、completion、integrity 和 anomalies；
- Message ID 不可直接取得时保持空，不做内容或时间反查；
- Journal 创建、追加和状态更新分别故障时业务结果不变；
- 受控 SQLite 写锁与连接池占用下，单次与累计等待均在设计预算内；
- 150 ms Segment 总预算覆盖 manifest 构造、schema、canonical JSON、HMAC、连接等待和事务提交，不允许预处理游离于预算之外；
- Recorder 只捕获 `Exception`，取消信号、`KeyboardInterrupt` 和其他 `BaseException` 不被吞掉；
- `run.resumed` 写入失败后仍能以 degraded 模式收敛到业务终态；
- degraded 落库失败不递归，内存 latch 保留且诊断不包含异常正文。

### 13.3 隐私与行为等价

- Journal 不包含 Prompt、用户原文、附件、JD/简历全文、模型正文、确认 token 或幂等 key 原文；
- 任意自由 `Conversation.context_type/context_ref`、模型 ID 和非法 source ref 不原样持久化，只能进入 allowlist 内部 ID、HMAC fingerprint 或空值；
- Context 只保存版本化、有界 manifest、稳定 ID、revision、计数、版本化估算器和 HMAC fingerprint；
- `agent-journal-key.json` 原子生成；持久化失败时应用与设置仍可工作、Journal 禁用，且不使用临时内存 key；
- POSIX key file 权限为 `0600`；Windows 测试验证位于当前用户数据目录、未放宽继承 ACL，并把平台差异记录为明确契约；
- `/api/settings` 与 `/api/settings/backup` 不包含 key file、Secret 或当前 key ID；`/api/backups/export` 排除 key file 与 Secret，同时保留数据库中解释历史 fingerprint 所需的非秘密 `fingerprint_key_id`；设置更新不接触 key file；
- 备份恢复生成新 `journal_hmac_key_id`；同一 Key Domain 内输入相同可比，不同 `fingerprint_key_id` 的 fingerprint 明确不可比较，旧 Run 进入 `fingerprint_key_domain_changed` fail-open 路径而不阻断业务；
- 输入 fingerprint 被视为敏感派生数据，日志、普通诊断与发布报告不得包含；
- 开启与关闭 Journal 时，HTTP 状态、响应体、SSE 序列、消息、业务写入以及 Provider/工具调用次数逐项一致；
- 读取模型可以确定性还原因果顺序；
- SSE seq 与 Journal seq 保持独立；
- 普通流式与确认流式请求继续使用各自的 `SseRun.run_id`，绝不替换成 `agent_run_id`。

### 13.4 发布门禁

1. 后端五组完整门禁，校验 manifest、重复 node ID、允许 skip 和 aggregate；
2. 前端完整门禁，证明 SSE 与既有 UI 无回归；
3. Ruff、Mypy、生产构建、Smoke、Local Verify；
4. 本地受控 Provider 完成一条普通、工具、确认的完整因果链；
5. 以上全部通过后，最多执行一次既有 real-AI verify，不进行无界重试；
6. 非平凡代码独立复审；
7. 发布报告记录真实结果和剩余风险。

本期没有 UI 变化，因此不要求产品截图。若浏览器 Harness 用于验证 SSE，只保存脱敏网络审计，不把它包装成新功能截图。

## 14. 分提交策略

实施阶段拆成四个可独立复审的提交：

1. `0024` Schema、Repository、并发和迁移测试；
2. Recorder、版本化事件契约、读取模型和单元测试；
3. API/Agent 接入、确认关联、fail-open 和行为等价测试；
4. 完整门禁、独立复审与发布报告。

所有提交遵循仓库格式，例如：

```text
feat: AI add durable agent run journal
```

## 15. 后续演进

该项目完成后，再分别设计并实施：

1. Tool Execution Pipeline：统一结构化 ToolRequest/ToolOutcome、capability 与 approval；
2. Write Operation Ledger：把写入意图、审批、执行与结果形成独立账本；
3. Context Projector：只向模型暴露任务所需的上下文和工具表面；
4. Proposal/Attempt 标准化；
5. 最后才评估后台队列或多 Agent 编排。

Durable Journal 只提供这些项目需要的稳定运行身份与因果观察，不提前承担它们的职责。
