# 证据门控的面试复盘建议设计

日期：2026-07-22
状态：待复审；本阶段只提交设计，不进入代码实现
范围：将用户保存的 InterviewNote 绑定到具体面试事件，并基于该复盘及事件元数据生成可审阅、可追溯的 AI 建议。

## 1. 目标与非目标

### 1.1 目标

1. 允许用户将一条 `InterviewNote` 绑定到同一 Application 下的一条 `event_type=interview` 的 `ApplicationEvent`。
2. 为已保存复盘生成不可变的 `InterviewReviewProposal` 快照。每个建议都能追溯到本次复盘的冻结字段，不把模型推测伪装成事实。
3. 复盘编辑后保留旧建议作为历史快照并标记来源已变化；用户明确重新生成时才产生新的建议。
4. 保留人工确认：生成建议和查看建议不自动写入事件、题库、知识库、Memory、Application 状态或其他外部平台；首期不提供跨领域预填动作。

### 1.2 非目标

- 不重做 InterviewNote 基础 CRUD、行动队列或提醒派生逻辑。
- 不读取 JD、其他 Resume、聊天历史、长期 Memory 或招聘网站，不增加 AI 工具调用。
- 不做自动外联、自动投递、自动状态迁移、模拟面试、录音或转写。
- 不将“表现”“弱点”“能力不足”等判断写入长期 Memory；建议只能作为本次复盘的可审阅草稿。

## 2. 领域边界与数据来源

本功能的唯一 AI 输入是用户本次复盘的冻结字段，以及绑定面试事件的有限元数据：

```text
InterviewNote snapshot
  company
  position
  round
  date
  questions
  self_reflection
  difficulty_points
  mood

InterviewEvent snapshot
  id
  application_id
  event_type=interview
  subtype
  round
  scheduled_at
  duration_minutes
  status
```

Application 只用于可见性和同投递关系校验；公司名、职位名和事件元数据不作为“候选人表现”的证据。AI prompt、日志和错误响应不得包含 JD、Resume、聊天、Memory 或完整数据库对象。

## 3. 面试事件绑定

### 3.1 数据模型

为 `interview_notes` 增加可空字段：

```text
application_event_id INTEGER NULL
  REFERENCES application_events(id) ON DELETE SET NULL
```

增加普通索引 `idx_notes_event`，并增加 SQLite 部分唯一索引：

```sql
CREATE UNIQUE INDEX uq_interview_notes_event_main
ON interview_notes(application_event_id)
WHERE application_event_id IS NOT NULL;
```

这表示一个面试事件最多有一条主复盘；复盘本身仍可不绑定事件。

### 3.2 绑定校验

创建或更新复盘时，如果 `application_event_id` 非空，服务端在同一短事务中校验：

1. 事件存在且可见；
2. 事件的 `event_type` 精确为 `interview`；
3. 事件所属 Application 可见且与复盘的 `application_id` 相同；
4. 复盘已属于一个 Application。独立复盘不能直接绑定投递事件；
5. 该事件没有另一条主复盘。

关系不合法返回稳定的 `422`；事件已经被另一条复盘占用返回稳定的 `409`。唯一索引是最终并发防线，冲突转换为同一 `409`，不得静默改绑或覆盖其他复盘。

`application_id` 或 `application_event_id` 的改变也必须重新执行全部关系校验。更新失败不改变原绑定。

### 3.2.1 复盘更新字段所有权

`application_id` 是服务端拥有的投递归属字段，不允许通过普通 `PUT /api/notes/{note_id}` 静默改变：

- 已绑定投递的复盘，更新请求省略 `application_id` 时保留原值；显式传入 `null` 或不同的 `application_id` 均返回 `422`，不提供跨投递移动或静默解绑的隐式兼容。
- 独立复盘省略或显式传入 `null` 的 `application_id` 时继续保持 `NULL`；普通更新显式传入非空 `application_id` 返回 `422`。若要创建投递归属，必须走 application-scoped 创建/绑定校验，不能由普通更新绕过关系检查。
- `application_event_id` 采用“省略即保留、显式 `null` 才解绑”的 PATCH 语义。显式解绑会让现有建议进入 `source_changed` 历史状态，并使当前生成 key 失效；重新绑定必须重新执行第 3.2 节全部校验。
- 普通更新响应始终返回原始 `application_id` 与 `application_event_id`，不得再从响应中剔除投递归属。

如果未来需要跨投递移动复盘，必须新增专用操作并在同一事务中校验、记录审计语义；本设计不开放该操作。

### 3.3 删除语义

- 删除面试事件时，数据库外键将 `InterviewNote.application_event_id` 置空；不删除用户复盘，也不删除已有建议快照。
- Application 软删除不删除复盘、事件或建议快照；涉及该投递的读写接口按不可见资源返回 `404`，防止继续生成或交接。
- 复盘物理删除时，属于该复盘的建议快照一并删除；复盘编辑不删除、不覆盖任何历史建议。

### 3.4 既有复盘 API 的软删除边界

软删除过滤必须覆盖既有复盘接口，而不只覆盖建议接口：

- `GET /api/notes` 返回未绑定复盘，以及绑定到当前可见 Application 的复盘；绑定到软删除 Application 的复盘不返回。
- `GET /api/applications/{application_id}/notes` 在 Application 不可见时返回 `404`；可见时只返回该投递的复盘。
- 创建或更新复盘时，如果目标 Application 不可见，返回 `404`；`PUT /api/notes/{note_id}` 和 `DELETE /api/notes/{note_id}` 对绑定到不可见 Application 的复盘也返回 `404`，不得修改或删除。
- 独立复盘（`application_id IS NULL`）仍可通过全局列表读取、编辑和删除；它们不能绑定面试事件，也不能生成本功能建议。

`NotesRepository.list/get/update/delete` 及其 API 调用方必须共同遵守这条可见性规则；不能依赖前端隐藏记录。

## 4. 不可变建议快照

### 4.1 新表

新增 `interview_review_proposals`：

| 字段 | 约束与用途 |
| --- | --- |
| `id` | 主键 |
| `note_id` | 非空外键到 `interview_notes`；复盘删除时级联删除建议 |
| `application_event_id` | 生成时绑定事件 ID 的快照，可空，仅用于审计展示 |
| `idempotency_key` | 非空；与 `note_id` 组成唯一键 |
| `input_snapshot_json` | 严格 JSON，保存本次 note/event 输入快照 |
| `source_fingerprint` | 输入快照的 canonical JSON SHA-256 |
| `proposal_json` | 严格校验后的建议 JSON，不保存原始模型回复 |
| `proposal_hash` | `proposal_json` canonical JSON SHA-256 |
| `created_at` | 生成并落库时间 |

增加 `idx_interview_review_proposals_note`，并增加唯一约束 `(note_id, idempotency_key)`。建议快照只增不改；模型名称、token、原始错误和原始输出不进入本表。

### 4.2 指纹与来源状态

服务端对固定字段按稳定键顺序、UTF-8、无额外空白进行 canonical JSON 序列化后计算 `source_fingerprint`。指纹输入包含第 2 节列出的 note/event 字段，不包含数据库时间戳、模型字段或当前 UI 状态。

历史读取与新生成使用不同的可见性边界：

- `GET` 建议历史/详情只要求 note 及其所属 Application 当前可见。它使用保存的 `input_snapshot_json` 和 `proposal_json` 返回历史快照，不要求当前面试事件仍存在。
- `POST` 新生成才要求 note 仍绑定一个当前可见、同一 Application 下的 `event_type=interview` 事件，并以该事件重新构建输入快照。

读取历史建议时使用以下确定算法，不把当前事件缺失误判为历史不可读：

1. 重新读取 `note`；若 note 不存在，或其所属 Application 不可见，返回 `404`。
2. 读取 note 当前的 `application_event_id`，并与保存快照中的 `application_event_id` 比较。
3. 若当前绑定 ID 为空，或与快照中的 ID 不同，直接返回 `source_status=source_changed`；此时不读取当前事件，也不计算当前指纹。
4. 若绑定 ID 相同但事件不存在、不可见、`event_type` 不是 `interview`，或事件的 `application_id` 与 note 不同，直接返回 `source_status=source_changed`。
5. 只有当前事件通过上述检查时，才用当前 note 与当前事件的最小字段重算指纹；指纹相等返回 `source_status=current`，否则返回 `source_status=source_changed`。

事件删除不会阻断历史读取；只要 note 及其 Application 可见，就从保存快照返回历史建议。

历史 `input_snapshot_json` 和 `proposal_json` 仍可只读展示；`source_changed` 时禁止把该建议当作当前复盘的可执行依据。前端应明确显示“来源已变化，请重新生成”，不得静默重算或覆盖旧快照。

### 4.3 幂等与并发生命周期

用户首次确认生成前由前端生成 `proposalAttemptKey`。该 key 只用于当前未决生成尝试；成功或契约明确保证不写入的失败结束尝试，复盘编辑、显式解绑或重新绑定事件则使旧 key 失效并要求下一次确认生成新 key。超时、断网、响应丢失属于结果未知，必须保留原 key，优先使用同一 key 重试。

`POST` 生成接口的幂等规则：

1. 先以 `(note_id, idempotency_key)` 查询已有建议；存在时只返回该建议，不调用 AI、不创建第二条记录。返回中的 `source_status` 按当前快照重新计算。
2. 不存在时先在短 session 冻结 note/event snapshot 并关闭 session，再调用 AI。AI 调用期间不持有 SQLite 连接。
3. AI 返回并通过严格校验后，使用新的短 session 执行 `BEGIN IMMEDIATE`，重新读取可见 note/event，重新计算指纹；指纹或绑定关系变化则返回 `409`，不插入建议。
4. 指纹仍一致时，在同一事务内检查唯一幂等键、插入建议并提交。并发请求若先插入，后到请求读取并返回已存在结果。
5. Provider 错误、超时或不可验证输出不写入建议；客户端不得因未知结果自动生成新 key。

### 4.4 幂等键的明确失效规则

- **可清除 key 的确定不写入失败**：输入或关系校验在模型调用前返回的 `422`、调用前确认资源不可见的 `404`，以及带稳定错误码 `interview_review_provider_error` 或 `interview_review_unverifiable` 的 `502`。这些错误的 API 契约必须明确保证本次没有插入建议，客户端收到后才清除 key。
- **必须保留原 key 的结果未知**：超时、断网、响应丢失、网关错误、普通 `5xx`、没有稳定错误码的 `502`、无效或无法解析的 HTTP 响应，以及客户端取消请求或切换上下文时服务端结果未知的情况。客户端不得将这些情况当作未写入，也不得新建 key；重新进入后仍使用原 key 重试。
- **复盘来源改变**：复盘正文编辑后、显式将 `application_event_id` 解绑后，或重新绑定其他事件成功后，旧 key 必须失效。下一次生成必须在用户再次确认时创建新 key；旧建议保留为历史快照并按第 4.2 节标记来源状态。
- **历史读取不改变未决尝试**：历史列表、历史详情、`source_changed` 展示不会清除、替换或推断当前未决 key；事件删除后的历史读取也不能据此生成新 key。
- **旧 key 的迟到重放**：复盘编辑或重新绑定后，服务端不得用旧 key 创建或覆盖新建议。若旧客户端重放该 key，只能返回既有建议或来源冲突；当前客户端必须使用新 key。

## 5. AI 输入与严格输出契约

### 5.1 Prompt 边界

系统 prompt 必须明确：

- 只能使用本次冻结 InterviewNote 和 InterviewEvent 元数据；
- 不得声称“面试官评价”、外部事实、候选人能力结论或未记录的题目答案；
- 没有复盘依据时，只能放入待澄清问题；
- 只返回 raw JSON，不得返回 Markdown、代码围栏、解释文字或额外字段；
- 所有用户可见主张必须通过本次复盘字段证据引用验证。

### 5.2 JSON 结构

顶层只允许以下字段：

```json
{
  "summary": {
    "text": "string",
    "evidence_refs": [
      {"source": "interview_note", "path": "/self_reflection", "excerpt": "string"}
    ]
  },
  "observations": [
    {
      "id": "string",
      "text": "string",
      "evidence_refs": [
        {"source": "interview_note", "path": "/questions", "excerpt": "string"}
      ]
    }
  ],
  "clarifications": [
    {
      "id": "string",
      "question": "string",
      "evidence_refs": []
    }
  ],
  "practice_focuses": [
    {
      "id": "string",
      "text": "string",
      "evidence_refs": [
        {"source": "interview_note", "path": "/difficulty_points", "excerpt": "string"}
      ]
    }
  ],
  "next_questions": [
    {
      "id": "string",
      "question": "string",
      "evidence_refs": []
    }
  ]
}
```

校验规则：

- 顶层和每个对象拒绝额外字段；所有 ID、文本、路径和摘录均为字符串；严格数组上限为：`observations` 最多 10 项、`clarifications` 最多 10 项、`practice_focuses` 最多 10 项、`next_questions` 最多 10 项；每个 `evidence_refs`（包括 `summary`、观察、练习重点和问题）最多 5 项；任一数组超限均拒绝；
- `summary`、每个 `observation`、每个 `practice_focus` 的 `evidence_refs` 必须非空，唯一例外是 `summary.text` 与版本化常量 `INTERVIEW_REVIEW_UNGROUNDED_SUMMARY_V1` 逐字相等；
- `source` 只能是 `interview_note`；`path` 只能是 `/questions`、`/self_reflection`、`/difficulty_points`、`/mood`；
- `excerpt` 必须非空，并且是冻结快照对应字段的逐字连续片段，不能由模型改写、拼接或引用事件/JD/Resume；
- `clarifications` 和 `next_questions` 必须包含 `evidence_refs` 字段。服务端使用版本化常量 `INTERVIEW_REVIEW_UNGROUNDED_QUESTIONS_V1`，其完整值固定为：

  ```json
  [
    "请补充本次面试中未记录的具体问题与回答。",
    "请补充你希望进一步澄清的内容。",
    "请补充你希望下次练习的具体场景。"
  ]
  ```

  仅当 `evidence_refs` 为空且 `question` 与上述常量中的一项逐字相等时才允许无引用；任何其他问题都必须带一个或多个经过逐字校验的引用。模型不得自由改写无引用问题；未来增删该列表必须提升常量版本并同步更新契约与测试。

服务端使用版本化常量 `INTERVIEW_REVIEW_UNGROUNDED_SUMMARY_V1`，其唯一逐字值为：

```text
本次复盘记录不足以形成有依据的表现判断，请先补充待澄清问题。
```

仅当 `summary.text` 与该常量逐字相等时，才允许 `summary.evidence_refs` 为空；该摘要常量不得由模型自由改写。若本次复盘的 `questions`、`self_reflection`、`difficulty_points`、`mood` 均为空，只能返回该固定摘要、空的观察和练习重点，并将需要用户补充的内容放入问题数组。未来修改该文本必须提升常量版本并同步更新契约与测试。
- `json.loads` 使用拒绝 `NaN`、`Infinity`、`-Infinity` 的解析器；拒绝 fenced JSON、重复键、非法 UTF-8、非 JSON 顶层值和任何额外字段；
- 校验失败统一归类为 `interview_review_unverifiable`，返回 `502`，不保存建议，不向前端或日志写入原始模型回复。

Provider 鉴权、网络、超时和服务不可用统一归类为 `interview_review_provider_error`，返回 `502`，同样不保存建议。错误日志只记录安全失败类别和 note/application 的内部关联标识，不记录复盘正文、事件完整内容、API Key 或模型原文。

### 5.3 一次格式修复重试

生成器先调用模型一次，再执行严格 JSON 解析和完整契约校验。只有首次失败属于 JSON 无法解析、顶层/对象结构错误、字段类型错误、数组超限或证据引用格式/逐字校验失败时，才允许再调用模型一次；第二次请求固定携带安全失败类别 `invalid_change_shape`，要求只返回同一输入快照和同一 JSON 契约下的 raw JSON。

重试次数上限固定为一次。重试不得扩大输入快照、加入 JD/Resume/聊天/Memory/事件 location 等新来源，也不得跳过任何严格校验；不能把首次原始回复回传给模型、前端或日志。Provider 鉴权、网络、超时和服务不可用属于基础设施失败，只调用一次并返回 `interview_review_provider_error`。第二次仍失败统一返回 `interview_review_unverifiable`，不保存建议。

## 6. API 契约

### 6.1 复盘绑定

沿用现有 InterviewNote 路由，仅增量扩展字段，不改变旧请求的合法语义：

```text
GET  /api/notes
GET  /api/applications/{application_id}/notes
POST /api/applications/{application_id}/notes
POST /api/notes
PUT  /api/notes/{note_id}
DELETE /api/notes/{note_id}
```

当前没有 `GET /api/notes/{note_id}` 单条读取路由；本功能不把它描述为既有能力。前端从全局或 application-scoped 列表取得复盘，建议历史接口按 `note_id` 读取并在服务端执行第 4.2 节的历史可见性规则。若后续确需单条复盘读取，应作为单独新增 API 评审，不在本设计中隐式引入。

响应的 `InterviewNote` 增加 nullable `application_event_id`，并始终保留 `application_id`。创建时可通过 application-scoped 路由绑定事件；普通 `PUT /api/notes/{note_id}` 对投递归属使用服务端拥有的字段语义：已绑定复盘省略 `application_id` 保留原绑定，显式 `null` 或不同值返回 `422`；独立复盘省略或显式 `null` 仍保持无投递归属，不允许普通更新静默跨投递移动或解绑。`application_event_id` 采用字段存在性区分的更新语义：省略保留原绑定，显式 `null` 才解绑；显式绑定新事件必须重新执行第 3.2 节全部校验。跨投递、非 interview 事件、不可见事件和重复主复盘按第 3.2 节返回 `404`、`422` 或 `409`，错误文本不作为前端契约。

### 6.2 建议生成与历史

新增只读/生成接口：

```text
GET  /api/notes/{note_id}/interview-review-proposals
GET  /api/notes/{note_id}/interview-review-proposals/{proposal_id}
POST /api/notes/{note_id}/interview-review-proposals
```

生成请求体仅包含：

```json
{"idempotency_key": "client-generated-key"}
```

服务端从 note/event 当前记录构建输入快照，不接受客户端上传的复盘正文、事件元数据、指纹或 proposal JSON。成功返回 `201`（新建）或 `200`（同 key 幂等返回），响应包含 `id`、`note_id`、`application_event_id`、`source_fingerprint`、`source_status`、`proposal`、`proposal_hash`、`created_at`。

稳定错误语义：

| 情况 | HTTP | 错误码 |
| --- | --- | --- |
| 历史读取时 note 或其 Application 不存在/不可见 | 404 | `interview_review_not_found` |
| 新生成时 note 或其 Application 不存在/不可见 | 404 | `interview_review_not_found` |
| 新生成时 `application_event_id` 为空（包括面试事件删除后外键自动解绑） | 422 | `interview_review_event_required` |
| 新生成时保留了非空 `application_event_id`，但事件不存在、不可见或关系不一致 | 404 | `interview_review_not_found` |
| 生成前后指纹或绑定关系变化 | 409 | `interview_review_source_conflict` |
| Provider 不可用 | 502 | `interview_review_provider_error` |
| 输出严格校验失败 | 502 | `interview_review_unverifiable` |

前端仅按错误码/HTTP 状态显示固定中文文案，禁止透传 `response.data.error`、Axios message、Python exception 或模型文本。

## 7. 前端交互

### 7.1 入口与状态

- 面试事件卡片显示“记录复盘”；已有绑定复盘显示“查看复盘”。事件列表只展示当前可见 Application 的 `event_type=interview`。
- 复盘表单保留现有字段和 CRUD；绑定事件是显式选择，保存前展示绑定的公司、职位、轮次和时间。
- 已保存且有绑定事件的复盘显示“生成复盘建议”。首次生成前必须弹出确认，明确提示“本次复盘内容与面试事件信息将发送给当前配置的 AI 服务”。
- 生成中禁用重复提交；结果未知时保留同一个 `proposalAttemptKey`，显示“结果待确认/使用原尝试重试”，不自动重试。
- 建议历史按生成时间列出；编辑复盘后旧卡片显示“来源已变化”，仍可查看快照但不能被当作当前建议直接执行。点击“重新生成”产生新的 key。

### 7.2 建议卡片

结构化展示并固定区分：

- “用户记录”：复盘字段及其原文；
- “AI 建议”：摘要、已观察到的表现、练习重点；每项显示证据来源标签和原文摘录；
- “待澄清问题”：模型无法从本次记录确认的内容；
- “下次可追问”：只作为问题清单，不表示已发生事实；
- “来源状态”：当前来源或来源已变化。

证据标签固定为“复盘问题”“自我反思”“困难点”“情绪记录”；路径和摘录保留原文。不得显示“面试官评价”“平台已验证”或自动判断类措辞。

### 7.3 跨领域动作边界

首期不提供“创建跟进”“开始练习”“保存为知识草稿”三个跨领域入口。Proposal JSON 不包含行动类型、行动依据或任何跨领域预填字段，因此前端不能从 AI 建议推导并触发这些动作。

如未来需要知识草稿，必须由用户明确选择本次复盘的原始字段或原始片段，经过 Knowledge 的 Captured Source/Note Preview 流程后再由用户确认；AI 建议正文不能直接成为 Knowledge Source、Memory 或 Note。跟进和练习也必须另行定义各自的带证据 action contract，不能以本设计的普通建议文本代替。

### 7.4 Pilot 入口

Pilot 只增加 Application 上下文的“打开面试复盘”入口，导航到原生复盘页面/抽屉并传递 `applicationId`，不复制复盘表单、不新增开放式工具、不伪造聊天消息或工具进度。复盘生成、历史查看和所有写入仍由原生复盘流程承载。

## 8. 增量迁移与兼容性

使用现有 `schema_migrations` 机制新增一个版本：

1. `ALTER TABLE interview_notes ADD COLUMN application_event_id INTEGER NULL`；
2. 创建 `idx_notes_event`；
3. 创建 `interview_review_proposals` 表、索引和 `(note_id, idempotency_key)` 唯一约束；
4. 创建 `uq_interview_notes_event_main` 部分唯一索引；
5. 既有 InterviewNote 的新字段全部为 `NULL`，既有 CRUD、行动队列和提醒数据无需回填或重算。

启动时 migration 必须幂等；旧数据库升级失败时不得删除既有表或数据。现有未绑定复盘继续可读、可编辑；只有绑定事件后才可生成本功能建议。

## 9. 测试与验收

### 9.1 后端

- migration：空库、已有库、重复启动和既有未绑定复盘兼容；
- 绑定：同投递 interview 事件成功，非 interview、跨投递、不可见事件、重复主复盘分别返回预期错误；普通更新省略 `application_id` 和 `application_event_id` 时保留原值并在响应中返回，显式传入不同 `application_id` 返回 `422`，显式 `null` 才解绑；解绑不删除复盘；删除事件后绑定字段变空；事件删除后历史建议仍可读并标记 `source_changed`，新生成返回 `422 interview_review_event_required`；保留非空但不可读的事件 ID 才返回 `404 interview_review_not_found`；Application 软删除后相关读取/生成返回 404；
- 指纹：首次生成保存快照；复盘编辑后旧建议仍存在且标记 `source_changed`；事件元数据变化同样标记来源变化；
- 历史来源算法：事件 ID 为空或与快照不同、事件删除/不可见/类型错误/跨投递时直接标记 `source_changed`；只有当前事件有效时才重算指纹；note 或 Application 不可见才返回 `404`；
- 幂等：同 note/key 只返回同一记录，模型只调用一次；并发插入不产生重复建议；输入前置 `422`、调用前 `404` 和带稳定错误码的 `502` 清除 key；超时、断网、响应丢失、普通 `5xx`、无稳定码 `502` 和无效响应保留原 key；复盘编辑、显式解绑和重新绑定使旧 key 失效；
- 问题 allowlist：仅 `INTERVIEW_REVIEW_UNGROUNDED_QUESTIONS_V1` 的完整三项固定中文问题可无引用，其余问题必须逐字引用复盘字段；
- 漂移：模型调用期间改变复盘或绑定事件，第二短事务返回 `409` 且数据库没有错误版本；
- AI 校验：合法输出成功；fenced JSON、额外字段、重复键、NaN/Infinity、伪造路径、伪造摘录、空 evidence_refs、未逐字匹配 `INTERVIEW_REVIEW_UNGROUNDED_SUMMARY_V1` 的无证据摘要、将事件/JD/Resume 当证据、各数组超过明确上限均安全失败；仅逐字匹配的固定摘要和 `INTERVIEW_REVIEW_UNGROUNDED_QUESTIONS_V1` 问题可通过无引用分支；
- 格式修复重试：首次 JSON/结构/字段类型/证据格式失败时恰好再调用一次并可接受第二次合法结果；两次均失败返回 `interview_review_unverifiable` 且不落库；Provider 异常、鉴权、网络和超时只调用一次并返回 `interview_review_provider_error`；重试不扩大快照、不绕过证据校验；
- Provider：网络、鉴权、超时和无配置均返回带稳定错误码的 `502`，不落库、不泄露原始输出；
- 写入边界：生成建议不改变 Application 状态、不创建事件/提醒/题库/知识/Memory；首期没有跨领域动作写入路径。

### 9.2 前端

- 事件与复盘入口、绑定确认、重复绑定错误和删除事件后的解绑展示；
- 生成确认弹窗、加载、同 key 重试、历史快照和来源已变化状态；
- 结构化建议、证据标签/路径/摘录、用户记录与 AI 建议区分；
- 首期没有跨领域动作入口；后续若增加动作必须单独定义并测试领域写入契约；
- `404/409/422/502` 和未知错误只显示固定中文，不显示 Axios/服务端原文；
- 软删除后清理当前卡片，不能继续生成或触发后续动作；
- 固定英文短语扫描仅检查已知固定 UI 文案，不禁止英文用户数据、事件原文或 AI 摘录。

### 9.3 隔离真实 AI 浏览器闭环

在临时数据目录中复制现有 AI 配置，不使用用户真实数据库：

1. 创建合成 Application、一个 `event_type=interview` 的面试事件和绑定 Resume 不相关的最小复盘；
2. 浏览器打开复盘入口，保存复盘并确认发送给当前 AI；
3. 真实生成建议，检查摘要/观察/练习逐项显示复盘证据，空证据时只出现澄清问题；
4. 确认首期界面没有跨领域动作入口；建议页面不新增事件、提醒、题库、知识、Memory 或 Application 状态写入路径；
5. 编辑复盘，确认旧建议仍显示来源已变化；重新生成才创建新建议；
6. 删除事件后确认历史建议仍显示并标记“来源已变化”，新生成被拒绝；软删除 Application 后确认页面显示安全中文 404、清理当前复盘并禁止继续生成；
7. 断言网络请求仅到本地 `/api` 与已配置 AI Provider，无招聘平台访问；
8. 停止隔离服务、删除合成 Application/事件/复盘/建议并断言临时目录无残留；不输出 API Key、完整复盘、完整事件或模型原文。

## 10. 破坏性变化与风险

破坏性变化：无。字段增量可空、旧数据无需迁移内容，既有 InterviewNote CRUD 和行动/提醒派生逻辑保持兼容。

主要风险及控制：

- 模型把复盘内容扩写为能力判断：严格 evidence_refs 和字段逐字校验拒绝；
- 复盘或事件在慢模型调用期间改变：第二短事务 `BEGIN IMMEDIATE` 重查指纹并返回 `409`；
- 旧建议被误当作当前建议：响应提供 `source_status`，前端显式标记并阻止后续建议动作；
- AI 或浏览器验收污染本地数据：real-AI 使用隔离目录、受控进程和 scoped cleanup；
- 后续跨领域动作若进入范围，必须先经过独立设计评审；本期不提供入口，避免把 AI 建议误解为已保存的领域数据。

本设计通过复审后，下一步另行编写测试先行实施计划；在计划获批前不修改模型、迁移、API、前端或 smoke 代码。
