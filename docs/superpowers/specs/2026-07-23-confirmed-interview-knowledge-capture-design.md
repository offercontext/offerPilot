# 证据门控的已确认面试知识沉淀设计

- 任务：`feat: AI add confirmed interview knowledge capture`
- 状态：待复审设计
- 日期：2026-07-23
- 实施边界：本阶段只提交设计，不修改代码、数据库或前端行为

## 1. 目标与原则

复盘建议已经能够对面试复盘提供证据化建议，但 AI 摘要、观察、练习重点和“弱点”不能直接成为知识事实。本任务提供一条由用户控制的知识沉淀路径：用户从一条可见的 `InterviewNote` 中选择原始面试片段，审阅可选的 AI 笔记预览，明确确认后再原子创建 Captured Source、Evidence 和 Knowledge Note Version。

必须满足以下原则：

1. 知识来源只能是用户选中的 `InterviewNote` 原始字段或原始片段。AI 复盘建议的任何字段都不是 Source，也不能单独成为 Evidence。
2. AI 仅在用户主动点击“生成笔记预览”后调用；默认路径不调用 AI。
3. 预览可编辑，但最终 Knowledge Note Version 的每个内容块必须引用本次确认创建或复用的 Evidence。预览未确认时不进入 Knowledge 检索、练习输入、Memory 或能力画像。
4. 确认前对原面试记录的任何变化都返回来源冲突，不静默覆盖；确认后的 Source、Evidence 和 Note Version 是冻结快照，不随原复盘解绑、编辑或删除而变化。
5. 所有写入都由用户明确确认触发。不自动创建题目、练习计划、日程、Memory、能力判断、投递状态变更或外部平台请求。
6. 禁止 URL 抓取、招聘平台访问和自动投递。AI 输入只包含冻结的已选原始片段，不包含 JD、简历、聊天历史、长期 Memory、会议地点或完整复盘。

本设计沿用 `docs/architecture/knowledge-system.md` 的 Source → Evidence → Knowledge Note Version 分层，以及“Captured Source 只保存确认相关原始片段”的约束。

## 2. 用户流程

```text
面试事件 / 对应复盘
        │
        ▼
选择原始片段 ──► 审阅选中内容 ──► 直接确认保存
        │
        └────────► 用户主动生成 AI 笔记预览
                             │
                      预览 / 编辑 / 引用校验
                             │
                             ▼
                        明确确认保存
```

### 2.1 选择原始片段

在面试事件与对应复盘之间提供“从复盘沉淀知识”入口。入口只对当前用户可见的 `InterviewNote` 展示；投递软删除、复盘不可见或读取返回 404 时清理当前沉淀上下文，不展示旧冻结内容，也不允许交接。

选择器只展示本次复盘的以下原始字段：

- `questions`：用户记录的面试问题；
- `self_reflection`：用户记录的自我复盘；
- `difficulty_points`：用户记录的困难点；
- `mood`：用户记录的状态感受。

每个选择项都保存为一个片段。定位协议固定使用浏览器与 JavaScript 一致的 UTF-16 code-unit 坐标：`start` 为 0-based、`end` 为 exclusive，按原始 JSON 字符串的 UTF-16 code units 计数。后端必须用等价的 UTF-16LE code-unit 视图切片再还原文本，不能直接把 Python Unicode code-point 索引当作协议索引；`start`/`end` 不得落在 surrogate pair 中，组合字符不做 grapheme 合并。服务端与客户端均禁止 Unicode NFC/NFD、空白、换行或大小写规范化，逐字比较使用原始字符串。

片段限制固定为最多 20 个、每个片段最多 4,096 个 UTF-8 bytes、所有片段 `text` 的 UTF-8 bytes 总和最多 32,768；超限返回 422。CJK 字符按 1 个 UTF-16 code unit 计数，emoji 按其实际 surrogate pair 计数，组合字符的每个 code point 按原始 code units 计数。实现测试必须覆盖 CJK、emoji、组合字符，以及边界落在 emoji surrogate pair 中的拒绝路径。

服务端收到片段后按固定字段顺序 `/questions`、`/self_reflection`、`/difficulty_points`、`/mood`，再按 `start`、`end` 排序，重新分配稳定的 `fragment_001`、`fragment_002` 等 canonical ID；客户端 ID 只用于请求关联，不参与 hash。Snapshot 的 `canonical_text` 使用 `interview-note-capture-v1` 的确定性 UTF-8 byte serialization：

```text
interview-note-capture-v1\n
fragment=<ordinal>\npath=<ASCII path>\nstart=<decimal>\nend=<decimal>\nbytes=<UTF-8 byte length>\ntext-bytes=<exact UTF-8 bytes>\nseparator=<single LF>\n
```

其中 metadata 行使用 ASCII、小写字段名和十进制数字，`text-bytes` 原样写入，不进行任何规范化；每段只追加一个 LF 分隔符，最后一段也追加。Snapshot digest、Source hash 和 Evidence 定位均基于这套字节序列，不依赖平台换行或 JSON 序列化顺序。

每个选择项都保存为一个片段：

```json
{
  "fragment_id": "f-1",
  "path": "/questions",
  "start": 0,
  "end": 42,
  "text": "用户原始记录中的逐字片段"
}
```

`path` 只能是上述四个固定路径；`start`/`end` 按上述 UTF-16 code-unit 协议计算，`text` 必须与冻结前端请求中的当前字段切片逐字相等。空片段、重叠片段、越界位置、落在 surrogate pair 中的位置、重复 `fragment_id` 和未允许路径均拒绝。未选择任何非空片段时，生成预览和确认按钮均禁用。

选择阶段可在前端保留草稿，但向服务端提交后必须建立服务端 capture attempt，以便重试、来源漂移检查和幂等恢复。关闭或取消只删除该 attempt，不删除或创建任何 Knowledge 资产。

### 2.2 默认直接保存

默认不调用 AI。用户审阅选中的原始片段后可点击“直接保存选中内容”，确认页展示：

- 将要创建的 Knowledge Note Version 内容；
- 每个内容块对应的 Evidence 引用；
- “只保存你选中的面试原文，不会自动创建练习、Memory 或能力结论”的说明。

直接保存的默认内容是每个选中片段一个内容块，内容与原文逐字相等，引用该片段对应的 Evidence。用户可以编辑标题和内容块，但任何最终内容块仍必须有至少一个本次选中的 Evidence 引用；不能通过编辑绕过证据要求。

### 2.3 可选 AI 笔记预览

只有用户主动点击“生成笔记预览”并确认发送后才调用当前配置的 AI 服务。发送前明确提示：所选原始面试片段将发送给当前配置的 Provider；不发送完整 `InterviewNote`，不发送复盘建议，不发送 JD、简历、聊天历史、Memory、能力画像或事件 `location`。

AI 预览可以被用户编辑。预览内容只作为待确认的派生成果，不是 Source；确认时 Captured Source 和 Evidence 仍只保存所选原始片段。AI 失败或返回安全空预览都不阻塞“直接保存选中内容”。

## 3. 数据模型与迁移

当前数据库没有独立的 `knowledge_notes`、`knowledge_note_versions` 和 `knowledge_note_evidence` 模型；本任务按增量迁移补齐它们，并复用已有 `knowledge_sources`、`knowledge_extraction_snapshots` 和 `knowledge_evidence` 表，不重做 Knowledge Source/Evidence 基础设施。

### 3.1 未确认的 capture attempt

新增 `interview_knowledge_capture_attempts`，它是可过期的流程状态，不属于 Knowledge 资产，也不参与 Knowledge 检索：

| 字段 | 约束与用途 |
| --- | --- |
| `id` | 主键 |
| `note_id` | 非空外键到 `interview_notes`，`ON DELETE CASCADE`；仅用于未确认流程恢复，不作为确认后 Source 的唯一身份 |
| `attempt_key` | 非空；与 `note_id` 联合唯一；客户端首次提交选择时生成 |
| `note_fingerprint` | 选择提交时对完整可见复盘来源计算的 SHA-256 |
| `selected_fragments_json` | 规范化片段数组；只包含所选原始字段、位置和逐字文本 |
| `last_preview_mode` | `direct` 或 `ai`；仅记录最近一次请求模式，attempt key 不绑定模式 |
| `preview_status` | `not_requested`、`direct_ready`、`ai_generating`、`ai_ready`、`safe_empty`、`provider_unknown`、`confirmed`、`expired` |
| `preview_revision` | 非负整数；每次 AI 生成尝试递增，用于结果回写 CAS |
| `provider_call_token` | 仅在 `ai_generating` 时存在的随机 token；并发请求不得复用 |
| `preview_json` | 严格预览 JSON；失败时为空或固定安全空结构 |
| `preview_error_code` | 仅存安全错误类别，不存模型原文 |
| `confirmed_note_version_id` | 确认成功后指向生成的 Note Version；未确认为空 |
| `created_at` / `expires_at` | 过期清理时间；建议默认 24 小时 |

数据库唯一约束为 `(note_id, attempt_key)`。`attempt_key` 只绑定 `note_fingerprint` 与规范化片段集合，不绑定 `direct/ai` 模式，因此同一 key 可以先生成直接预览，再切换到 AI 预览；切换不改变 key，也不扩大冻结输入。该表允许保存必要的冻结原始片段以支持重试，但不得被 Knowledge API、检索、练习或 Memory 查询；过期和取消只清理未确认 attempt。

AI 预览采用短 session 状态机：请求先用短事务校验 key、指纹和规范化片段；若状态为 `ai_generating`，直接返回当前 attempt 状态，不发起第二次 Provider 调用。若允许发起新一轮，则在事务内递增 `preview_revision`、写入 `provider_call_token`、置为 `ai_generating`，提交并关闭 SQLite session 后才调用 Provider。Provider 返回后用新短 session 执行带条件的 CAS 更新：`WHERE id = ? AND preview_revision = ? AND provider_call_token = ? AND preview_status = 'ai_generating'`。CAS 失败时丢弃结果且不写入模型原文；成功后才写入严格预览或安全状态。Provider/网络未知可将状态置为 `provider_unknown`，同一 key 后续允许重新抢占下一 revision，但任何时刻最多一个 `ai_generating` 调用。

### 3.2 Captured Source 与 Evidence

Captured Source 不新建平行的 Source 体系，而是在现有 `knowledge_sources` 中使用 `source_kind = "captured_interview_note"`。首次确认时在同一数据库事务中创建：

- `knowledge_sources`：`source_hash` 由 `note_id`、确认时的 `note_fingerprint`、规范化片段路径/范围/文本和 capture schema 版本组成；不能只按文本 hash 去重不同复盘。`manifest_json` 保存最小来源元数据和片段定位，不保存完整复盘。
- `knowledge_extraction_snapshots`：保存所选片段拼接后的规范化只读文本及 digest；不运行 Imported Source 的完整 Ingest/Brief pipeline。
- `knowledge_evidence`：每个选中片段一个 Evidence，`kind = "interview_note_fragment"`，`block_kind = "captured_fragment"`，`canonical_excerpt` 必须等于原始片段；Evidence 的路径元数据指向 `/questions`、`/self_reflection`、`/difficulty_points` 或 `/mood` 及其位置。

为保留可审计来源，可新增 `knowledge_captured_source_metadata`：`source_id` 主键、`origin_note_id`（普通不可变整数，不设外键、不设 `ON DELETE` 级联）、`note_fingerprint`、`selected_fragments_json`、`capture_schema_version`、`captured_at`。它只保存确认的片段清单和原始 note 标识，不复制整条 `InterviewNote`；确认后原 note 被删除、解绑、编辑或其投递软删除时，该 metadata、Source、Snapshot、Evidence 和 Note Version 均保留。只有 `interview_knowledge_capture_attempts.note_id` 允许随未确认流程级联清理。

Captured Source 的内容通过快照和 Evidence 读取；不访问原始 URL，不创建外部文件抓取任务。若现有 Source 模型要求 `main_relative_path` 等字段，使用受控的 `captured://interview-note/<source_id>` 逻辑标识，禁止把它解释为可抓取 URL。

### 3.3 Knowledge Note 与不可变版本

新增表：

#### `knowledge_notes`

- `id` 主键；
- `title`；
- `current_version_id` 可空外键；
- `origin_kind = "confirmed_interview_capture"`；
- `created_at`、`updated_at`、可选 `archived_at`。

#### `knowledge_note_versions`

- `id` 主键；
- `note_id` 非空外键；
- `version_number`，同一 Note 单调递增且唯一；
- `content_json`：用户确认的结构化内容，不含 Source 原文副本以外的隐藏模型字段；
- `content_hash`：规范化内容 hash；
- `content_origin`：`direct_selected_text`、`ai_preview` 或 `user_edited_preview`；
- `capture_attempt_key`：用于确认幂等审计；
- `created_at`。

#### `knowledge_note_evidence`

- `note_version_id`、`block_id`、`evidence_id` 均非空；
- 唯一约束为 `(note_version_id, block_id, evidence_id)`；同一 Evidence 可以被同一版本的多个 block 复用，一个 block 也可以引用多个 Evidence；
- `block_id` 使用内容 JSON 中的稳定 block ID，不允许空字符串；
- 外键均为受保护的只读引用。

每个 Note Version 至少引用一条当前确认产生或复用的 Evidence。确认事务必须解析最终 `content_json`，要求内容块 ID 集合与关联表中的 block ID 集合完全一致，并逐 block 验证至少存在一条 Evidence 关联；孤立 block、孤立关联或缺少引用的 block 均回滚。用户编辑文本或 AI 预览文本只能存入 `content_json`，不能写入 `knowledge_sources`、`knowledge_evidence` 或任何长期 Memory；所有内容块都必须通过 `knowledge_note_evidence` 指向所选片段 Evidence。

### 3.4 迁移顺序与兼容性

当前迁移注册中已有 `0009_knowledge_provenance_kbr02` 和 `0010_interview_review_proposals`，本任务使用新的唯一版本，例如 `0011_confirmed_interview_knowledge_capture`，不得复用旧版本号。

启动迁移顺序：

1. 先执行 `Base.metadata.create_all()`，保证全新数据库能创建现有表和新增表；
2. 对已存在的旧表执行兼容性补列（如实现需要），不得在表不存在时直接 `ALTER TABLE`；
3. 创建唯一键、普通索引和外键约束；
4. 记录 `0011_confirmed_interview_knowledge_capture`；
5. 迁移必须幂等，重复启动不重复建表、不改写已有 Knowledge 数据。

迁移不删除现有复盘、Proposal 或 Knowledge Source；旧数据库没有新表时仍可启动，新增能力只在迁移完成后可用。

## 4. 冻结快照、来源漂移与幂等

### 4.1 复盘指纹

`note_fingerprint` 对以下字段按固定顺序、固定 JSON 编码计算：`note_id`、`application_id`、`application_event_id`、`company`、`position`、`round`、`date`、`questions`、`self_reflection`、`difficulty_points`、`mood`。因此任何复盘编辑、显式解绑、重新绑定、投递归属变化都会使待确认 attempt 失效；事件删除导致外键置空也会被识别为来源变化。

确认时必须在写事务内重新读取当前可见 `InterviewNote`，重新计算指纹，并用统一的 UTF-16 code-unit slicing helper 逐片段检查 `text == slice_utf16(current_value[path], start, end)`；不得使用 Python 原生 code-point slice 代替。任一不匹配返回 `409 interview_knowledge_source_changed`，不创建或修改任何 Source、Evidence、Note 或 Version。

确认后，历史 Knowledge 读取只依赖 `knowledge_captured_source_metadata`、Snapshot、Evidence 和 Note Version；即使原复盘被编辑、解绑、删除或关联投递软删除，历史资产仍可审计。原始复盘不可见只影响新的选择、预览和确认，不影响已确认的冻结资产读取。

### 4.2 attempt key 生命周期

- 首次提交选择时生成 `captureAttemptKey`，后续直接预览、AI 预览重试和确认均复用该 key。
- key 绑定的是 `note_fingerprint + canonical selected_fragments`，不是 `preview_mode`：同一 key 可先请求 `direct_ready`，再主动切换 `mode=ai`；切换只更新 attempt 预览状态，不产生新 Source 或新 key。
- `ai_generating` 是唯一的 Provider 调用占用状态；抢占由 `preview_revision + provider_call_token` 组成。并发请求若观察到该状态只能返回进行中结果，不能调用 Provider；Provider 调用前必须提交并关闭 SQLite session，结果只能通过 revision/token CAS 回写。
- 同一 `(note_id, attempt_key)` 且输入快照相同，返回同一 attempt 或已确认的同一 Note Version，不重复写入。
- Provider/网络/超时或响应丢失属于结果未知：保留 key 和 attempt，允许使用同 key 重试；不得用新 key 猜测历史结果。
- 明确的 404（复盘/投递不可见）、409 来源变化、422 选择或契约错误表示本次 attempt 不能继续，客户端清除 key；服务端不写 Knowledge 资产。
- AI 预览成功或安全空预览后，确认成功会结束 attempt；关闭但未确认仍不创建 Knowledge。若用户要重新选择片段、修改复盘后重新预览或主动重新生成，必须生成新 key。
- 已确认的 key 再次确认只返回同一版本。相同片段的新内容版本必须由用户明确重新选择/编辑并生成新 key，不能通过复用旧 key 覆盖旧版本。

### 4.3 确认写入的原子事务

确认接口在 SQLite 短事务内执行：

1. 取得 `BEGIN IMMEDIATE` 写锁；
2. 先按 `(note_id, attempt_key)` 查幂等结果；命中已确认版本则直接返回，不解析 Provider 配置、不重新生成；
3. 校验 note 可见性、attempt 未过期、当前指纹、所有片段位置/逐字文本和确认内容的 Evidence 引用；
4. 查找相同 `source_hash` 的 Captured Source；不存在则创建 Source metadata、Snapshot、Evidence，存在则验证 manifest 完全一致后复用；
5. 创建 `knowledge_notes`、`knowledge_note_versions` 和 `knowledge_note_evidence`，更新当前版本指针；
6. 将 attempt 标记为 `confirmed` 并记录 `confirmed_note_version_id`；
7. 同一事务提交。任一步失败全部回滚，不留下半个 Source、孤立 Evidence 或无引用 Note Version。

事务中不调用 AI、不访问网络、不写行动队列，不修改投递或原始复盘。

## 5. AI 预览契约与安全空预览

### 5.1 输入边界

AI 请求只发送：

- capture schema / prompt 版本；
- 所选片段的 `fragment_id`、固定字段标签、逐字 `text`；
- 明确指令：只可改写或组织这些片段，不得补造事实。

不发送 `InterviewReviewProposal` 的摘要、观察、练习重点、缺口或证据；不发送完整 `InterviewNote`、Application、JD、Resume、事件地点、聊天历史、Memory、Provider 密钥或本地文件路径。

### 5.2 严格 JSON Schema

如 Provider 明确声明支持原生 JSON Schema，则请求使用与下述结构等价的 `response_format`；否则使用严格 JSON 文本并由服务端解析。无论 Provider 能力如何，服务端校验都是最终门禁：拒绝 fenced Markdown、额外字段、非有限数值、错误类型、超限数组、空或未知引用以及与冻结片段不逐字相等的 excerpt。

预览 schema v1：

```json
{
  "title": "string",
  "blocks": [
    {
      "block_id": "string",
      "text": "string",
      "evidence_refs": [
        {
          "fragment_id": "string",
          "excerpt": "string"
        }
      ]
    }
  ]
}
```

固定限制：`title` 最多 120 个字符；`blocks` 最多 20 个；每个 `text` 最多 2,000 个字符；每个 `evidence_refs` 最多 5 个且至少 1 个；`fragment_id` 必须来自当前 attempt；`excerpt` 必须与该片段逐字相等。除 schema 字段外不接受 `summary`、`weaknesses`、`skills`、`actions`、`memory`、`exercise` 等字段。

每个非空 block 必须带 Evidence 引用。验证器只允许引用本次冻结的所选片段，不能引用未选择字段、AI 输出本身或其他 Knowledge Source。

### 5.3 修复与安全空结果

格式或结构校验失败时最多执行一次受控格式修复。修复请求只携带机器可读失败类别，例如 `invalid_json`、`unexpected_field`、`missing_evidence_ref`、`unknown_evidence_ref`、`excerpt_mismatch` 或 `limit_exceeded`，仍使用同一冻结 attempt，不扩大输入，不允许补造证据。

Provider/网络/超时异常不重试，返回 502，保留 attempt key；前端可继续直接保存。首次和一次修复后仍不通过时，服务端不保存模型原文，严格校验并返回固定安全空预览：

```json
{
  "title": "",
  "blocks": []
}
```

安全空预览只表示“目前没有可验证、可给出的笔记预览”，不表示 AI 已完成分析，不包含推断、弱点、能力结论或模型原文。它可以作为正常预览状态返回，且不阻塞直接保存原始片段。安全空预览本身不会创建 Source、Evidence 或 Note Version；只有用户确认保存时才执行知识写入。

### 5.4 安全诊断

服务端最多记录：安全失败类别、是否执行修复、修复次数、耗时、Provider 请求标识（如有）和最终状态。禁止记录模型原文、完整 Prompt、复盘内容、候选人内容、证据摘录、API Key、完整响应体或完整快照。日志和错误响应只使用稳定的错误码，不将 `Error.message`、Axios 原文或 Provider 原文透传给用户。

## 6. API 契约

新增 API 不改变现有复盘、Interview Review Proposal 或 Knowledge Source API 的语义。

### 6.1 创建/恢复待确认预览

`POST /api/notes/{note_id}/knowledge-capture/preview`

请求严格包含：

```json
{
  "attempt_key": "uuid",
  "mode": "direct | ai",
  "selected_fragments": [
    {
      "fragment_id": "f-1",
      "path": "/questions",
      "start": 0,
      "end": 42,
      "text": "原始片段"
    }
  ]
}
```

`mode=direct` 不调用 AI，直接返回可确认的确定性预览；`mode=ai` 才解析 Provider 并调用模型。响应包含 `attempt_key`、`note_fingerprint`、冻结片段、预览状态、预览 JSON 和安全错误码（如有），不返回完整复盘。

### 6.2 确认保存

`POST /api/notes/{note_id}/knowledge-capture/confirm`

请求包含 `attempt_key`、确认时的 `note_fingerprint`、用户确认的 `title` 和 `blocks`。服务端重新读取复盘并完成第 4.3 节的原子事务。成功首次返回 `201`，幂等命中返回 `200`；响应包含 Note Version、Captured Source、Evidence 的 ID 与只读展示数据。

确认接口不接受 `source_text`、任意外部 URL、AI 生成的 Source 字段或未列出的知识类型。

### 6.3 Knowledge 入口

现有 Knowledge 工作台新增 `origin_kind=confirmed_interview_capture` 的只读来源标签和 Note Version 详情：

- 展示用户确认的 Note Version 内容；
- 展示每个内容块引用的 Evidence 原文、复盘路径和冻结时间；
- 展示来源已与原复盘解绑/修改时仍保持可审计的快照；
- 不展示未确认 attempt，不把预览状态列入检索；
- 不提供自动创建题目、练习计划、日程、Memory 或能力画像的按钮。

后续用户主动进入练习时，检索只能读取已确认的 Knowledge Note Version 与其 Evidence；未确认预览没有数据库资产 ID，因此不可能成为练习输入。

## 7. 错误语义与前端映射

后端只返回稳定 `error_code`，前端按错误码/HTTP 状态映射固定中文，不透传后端 `error`、Axios message、Provider 原文或 JavaScript `Error.message`。

| HTTP | `error_code` | 语义 | 前端行为 |
| --- | --- | --- | --- |
| 404 | `interview_note_not_found` / `application_not_found` | 复盘或所属投递不可见 | 显示“该复盘已不可用”，清理当前 capture 上下文和 handoff，不允许确认 |
| 409 | `interview_knowledge_source_changed` | 确认前原复盘已变化 | 显示“复盘内容已变化，请重新选择原始片段”，保留历史 Knowledge，要求新 key |
| 409 | `interview_knowledge_attempt_conflict` | 同 key 对应不同输入 | 显示“当前沉淀草稿已变化，请重新开始”，禁止覆盖旧 attempt |
| 410 | `interview_knowledge_attempt_expired` | 未确认 attempt 已过期 | 显示“沉淀草稿已过期，请重新选择片段” |
| 422 | `interview_knowledge_selection_invalid` | 路径、范围、逐字文本、上限或确认结构非法 | 显示“所选片段无法验证，请重新选择” |
| 502 | `interview_knowledge_preview_provider_error` | Provider/网络/超时，结果未知 | 显示“AI 预览暂不可用，可直接保存选中原文”，保留 key，允许重试或直接确认 |
| 200 | `preview_status=safe_empty` | 两次契约校验失败后的安全空预览 | 显示“暂无可验证的笔记预览”，不视为系统错误，仍可直接确认保存 |

未知 5xx、网关错误、无效响应体和响应丢失均不伪装成确定未写入；对于 AI 预览，只保留 attempt key 并允许同 key 重试。确认写入的网络未知也保留 key，下一次同 key 先查幂等结果。

## 8. 前端确认与安全边界

前端新增“面试复盘知识沉淀”抽屉或原生卡片，不把 AI 预览伪装成聊天消息或工具进度：

1. 原始片段选择列表显示字段名、原文和选中状态；动态内容保持原文。
2. 默认展示“直接保存选中内容”；AI 预览按钮旁展示发送给当前 AI 服务的说明。
3. 生成 AI 预览前弹出二次确认；取消不发请求、不消耗 attempt key 之外的服务端资产。
4. 预览中分离展示“用户选中的原始片段”“AI 笔记预览”和“证据引用”；引用 chip 显示固定中文来源标签，路径和摘录保持原文。
5. 编辑预览时，每个内容块的 Evidence 引用保持显式；缺少引用、删除所有引用或超限时确认按钮禁用。
6. “直接保存”或“确认保存预览”再次弹出确认，明确将创建不可变 Knowledge Note Version；确认后只写 Knowledge 资产，不执行任何后续动作。
7. 关闭、取消或未确认预览不写 Source、Evidence、Note Version；网络未知时不删除服务端 attempt key，重进后可恢复同 key。
8. 成功后提供“在知识库查看”入口；不提供自动练习、自动 Memory、自动能力结论、自动日程或投递操作。

## 9. 测试先行范围

实施阶段先补失败测试，再实现代码。至少覆盖：

### 后端与迁移

- UTF-16 code-unit 坐标在 CJK、emoji、组合字符和 surrogate pair 边界上与浏览器一致；不做 Unicode 规范化；超过 20 个片段、单片段 4,096 UTF-8 bytes 或总计 32,768 UTF-8 bytes 拒绝；
- canonical fragment 排序、ID 重分配和 `interview-note-capture-v1` Snapshot 拼接在重复请求中产生完全相同的 bytes/digest；
- 全新数据库先 `create_all` 再补列、建表和索引，能创建所有新增表；
- 缺列旧库迁移成功，记录唯一 `0011_confirmed_interview_knowledge_capture`，重复启动幂等；
- 允许路径、范围和逐字片段校验；空片段、越界、重复和跨字段引用拒绝；
- 软删除或不可见复盘返回 404 并不写 attempt 以外的 Knowledge 资产；
- 复盘任一字段编辑、解绑或重绑后确认返回 409，Source/Evidence/Note Version 数量不增加；
- 事件或投递后续删除/软删除不影响已确认冻结资产的读取和证据审计；
- 同 key 重复预览/确认返回同一结果，不产生重复 Source、Evidence 或 Version；不同输入复用旧 key 返回 409；
- 直接保存成功时原子创建 Captured Source、Evidence、Note Version；中途失败时三者均不残留；
- 用户编辑或 AI 预览内容没有 Evidence 引用、引用未选片段、excerpt 不逐字相等或超过上限时确认拒绝；
- AI 请求只收到 selected fragments，不含完整 note、JD、Resume、location、Memory 或复盘建议。

#### 本轮复审补充回归

- 物理删除原 `InterviewNote` 后，`knowledge_captured_source_metadata.origin_note_id`、Source、Snapshot、Evidence 和 Note Version 仍可读，只有未确认 attempt 被级联清理；
- 同 key 先 direct 后 ai 可切换；两个并发 AI 请求最多一次 Provider 调用，调用前 session 已关闭，过期响应无法通过 revision/token CAS 写回；
- 同一 Evidence 被多个 block 引用时允许，任一 block 缺少 Evidence 时确认事务回滚。

### AI 契约

- Provider 支持 JSON Schema 时收到明确 `response_format`；不支持或未声明时不发送未知参数；
- 合法预览通过严格 JSON、字段和引用校验；fenced JSON、额外字段、非有限值、未知引用和上限超限拒绝；
- 首次格式失败只修复一次，修复请求只携带机器可读失败类别；Provider/网络异常只调用一次并返回 502；
- 两次契约失败返回固定安全空预览，不保存模型原文，不创建 Knowledge 资产；
- 安全空预览不阻塞直接保存，同 key 后续仍可返回同一 attempt/确认结果。

### 前端

- 只显示用户可选的四类原始字段，不能选择 AI 摘要、观察、练习重点或弱点；
- 默认直接保存不调用 AI；主动生成预览才弹确认并调用 AI；
- AI 502、安全空预览、404、409、422 均显示安全中文，页面不出现 Axios/Provider 原始英文；
- 关闭/取消未确认预览不产生 Knowledge 写入；未知结果重挂载后复用原 attempt key；
- 确认前编辑复盘后显示来源冲突，不能自动覆盖；确认后历史详情仍显示冻结 Evidence；
- Knowledge 入口可查看已确认内容和 Evidence，不展示未确认预览，不提供自动练习或 Memory 写入动作。

## 10. 隔离 real-AI 浏览器验收

真实验收使用连续的隔离 harness，而不是正式数据目录：

1. 创建临时 `$tempData`，只复制现有 AI 配置中的非数据配置；不复制正式数据库、简历、复盘或知识目录。
2. 选择并确认空闲端口，以 `OFFERPILOT_DATA=$tempData` 启动服务；启动后验证监听进程属于本次 harness 进程树，再打开浏览器，禁止仅凭健康检查误连旧服务。
3. 通过本地 API 创建合成 Application、Interview Event 和至少三条非空 InterviewNote，内容使用固定无敏感 marker；不调用 URL、招聘平台或外部数据源。
4. 浏览器打开服务根地址，从投递详情进入面试复盘知识沉淀入口：
   - 第一组走默认直接保存，确认前断言数据库没有 `knowledge_sources`、`knowledge_evidence`、`knowledge_note_versions` 新行，确认后断言三类资产存在且引用链完整；
   - 第二组主动生成 AI 预览，记录其为有效带引用预览、安全空预览或安全 502；无论结果如何，确认前不得有 Knowledge 写入，随后用直接保存完成确认；
   - 第三组生成预览后编辑原复盘再确认，断言返回 409 且无新增 Knowledge 资产；重新选择新 key 后可重新走流程。
5. 确认后进入 Knowledge 工作台，验证冻结原始片段、Evidence 路径和 Note Version 可读；解绑/编辑或软删除原复盘后再次查看，仍可审计且不把旧内容写回原复盘。
6. 从浏览器网络记录断言请求只到本地 `/api`、静态资源和当前配置的 AI Provider；没有招聘平台、URL 抓取或自动投递请求。
7. 停止并只清理本次 harness 启动的进程树和 `$tempData`；清理后断言合成 Application、InterviewNote、Knowledge Source、Evidence、Note Version、attempt 均无残留，并确认 `sourceData` 未发生变化。所有失败必须显式转为 harness 异常，不能依赖 PowerShell 的 `$LASTEXITCODE` 隐式传播。

## 11. 非目标与破坏性变化

本任务不做：

- 自动创建题目、练习计划、日程、Memory 或能力画像；
- 将 AI 摘要、观察、练习重点、弱点或模型原文写入 Source/Evidence；
- 自动修改投递状态、自动联系、自动投递、招聘平台访问或 URL 抓取；
- 录音、转写、模拟面试或重做普通复盘 CRUD；
- 全局国际化、Knowledge 架构重写或替换现有 Imported Source Ingest。

预期无破坏性 API 变化；新增路由和数据表为增量兼容。旧复盘、旧 Interview Review Proposal、现有 Knowledge Source/Evidence 不被迁移重写。若实现阶段发现现有 Knowledge Note 表已由其他任务创建，必须以当前 schema 检查结果为准，采用唯一迁移版本和向后兼容补列，禁止复用或静默改写已有版本。

## 12. 复审门槛

设计复审通过后再进入测试先行实施计划。实施前不得修改代码。进入下一阶段的必要条件：

- 明确 AI 预览可选，默认直接保存；
- 明确确认前没有任何 Knowledge 资产，确认后 Source/Evidence/Note Version 同事务落库；
- 明确复盘漂移 409、网络未知保留幂等键、确定失败可重新开始；
- 明确原始片段是唯一 Source，AI/用户编辑文本只能是带 Evidence 引用的派生成果；
- 明确全新库、旧库迁移、前端 HITL 和隔离 real-AI 浏览器验收的测试边界。
