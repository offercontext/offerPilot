# 结构化面试故事库设计

**状态**：设计修订，待复审
**日期**：2026-08-10
**范围**：第一期只建立和维护可审计、可版本化的候选人面试故事资产；不实现故事使用记录或练习消费。

## 1. 目标、背景与非目标

OfferPilot 已经保存了简历版本、面试事件、面试复盘、模拟面试 Turn、证据门控 Proposal 和人工确认流程。当前缺少的是一项独立资产：用户无法把已确认的真实经历整理为可长期维护、可跨投递复用的 STAR 故事。

本功能将候选人故事定义为“有证据、可版本化、由用户确认的个人经历资产”，用于后续面试准备和练习；它不判断故事优劣，也不替用户选择或使用故事。

第一期目标：

- 在顶层“面试”模块提供故事库列表、筛选、搜索、详情和只读版本历史；
- 支持手动创建、基于现有故事创建新版本、归档和恢复；
- 支持从用户显式选择的已保存面试复盘原始片段整理 Story Proposal；
- 支持在 Pilot 中由用户主动发起、选择来源后复用同一 Proposal 和确认保存流程；
- 对每个已确认版本保留最小冻结来源、逐字摘录和可读的来源风险；
- 保持现有 Evidence、Proposal、HITL、幂等恢复和不确定结果处理边界。

第一期明确不做：

- 自适应练习、自动选故事答题、自动从历史批量生成故事；
- Story Usage 表、使用次数、使用过多提示，或从面试/模拟面试/复盘自动推断使用；
- 故事评分、排名、录用概率、“最佳故事”或任何录用判断；
- 自动写入 Knowledge、Memory、Application 状态、面试记录或外部平台；
- 语音、录音、转写、招聘平台访问，以及投递结果反馈闭环。

## 2. 领域边界与方案比较

### 2.1 结论：独立 Interview Story 聚合

采用独立的 `InterviewStory` 领域。它描述候选人本人主张并由候选人确认的经历，默认属于当前工作区，不绑定单一 `Application`。当前产品没有多用户所有权模型，因此第一期不虚构 `user_id`；工作区即所有权边界。

该聚合不属于以下任一现有领域：

| 领域 | 既有职责 | 与 Story 的关系 |
| --- | --- | --- |
| Knowledge | 外部世界的可引用知识，以及经确认沉淀的 Knowledge Note | 第一阶段不作为 Story 事实来源，也不得接收 Story 或 AI 草稿 |
| Interview Business Record | 某次面试、复盘和模拟面试实际发生的记录 | 可以成为 Story 的显式来源；不因生成 Story 被修改 |
| Memory | 用户偏好、目标、薄弱点和掌握程度 | 不能作为故事事实来源，也不接收 Story 内容 |
| Application | 某次投递的事实与状态 | 可以在未来 Usage 中关联；第一期不绑定 Story |

### 2.2 被否决的方案

1. **复用 Knowledge Note**：实现表面较少，但会把候选人经历误归入外部/通用知识，违反 `docs/architecture/knowledge-system.md` 的职责边界，并会让未确认草稿污染 Knowledge。
2. **从复盘实时派生**：无需持久化新模型，但不能稳定编辑、版本化、审计来源或跨投递复用；也无法表达归档与来源变化。

两种方案均不采用。除非实施前发现当前主干缺少使独立聚合可运行的基础设施，否则不得退回这两种模型。

## 3. 第一期开端与用户流程

### 3.1 手动创建

```text
面试 > 故事库 > 新建故事
  -> 填写标题与 STAR 区块
  -> 为事实性区块显式添加可用来源，或输入并确认用户原始陈述
  -> 明确点击“保存首个版本”
  -> 原子创建 Story、Story Version、Evidence Links
```

手动保存不调用 AI。用户可以只保存带有事实缺口的故事草稿结构，但任何非空的 `situation`、`task`、`action`、`result` 事实性文本都必须通过本设计的来源校验；`reflection` 必须被标注为“用户观点”，不能伪装为外部或客观事实。

### 3.2 从面试复盘整理

```text
已保存面试复盘 > 整理为故事
  -> 用户选择允许使用的原始复盘片段
  -> 确认发送本次冻结来源给 AI
  -> Story Proposal（STAR、能力标签、适用问题、事实缺口、证据）
  -> 用户编辑、选择区块并确认保存
  -> 原子创建 Story、首个 Version、Evidence Links
```

这里的来源是已保存的复盘原文/Transcript 片段，不是 AI 生成的复盘建议。来源不足时，服务端返回可验证的安全空 Proposal 或确定性事实缺口；不得补造数字、团队规模、职责或业务结果。

当前主干的 `InterviewNote` 没有另一层“confirmed review”状态。因此本设计中“已确认的面试复盘”严格指用户已经保存的复盘业务记录，而不是未保存表单、AI 复盘建议或旧 Proposal。实施不得为这句话虚构新的确认字段；如未来产品需要复盘审批，必须另行设计。

### 3.3 Pilot

Pilot 默认不扫描简历、复盘或模拟面试历史。用户必须主动点击或明确输入“帮我整理一个面试故事”，随后：

1. Pilot 要求用户选择现有来源或粘贴明确的原始陈述；
2. 用户确认生成请求后，调用与 UI 相同的 Story Proposal API；
3. 用户在同一审阅/确认界面选择和编辑内容；
4. 保存时调用与 UI 相同的确认 API。

Pilot 不能猜测“最好的故事”，不能自动发送模型请求，也不能维护第二套状态机或写入语义。

## 4. 数据模型与删除语义

### 4.1 `interview_stories`

`InterviewStory` 是稳定身份和生命周期容器：

| 字段 | 语义 |
| --- | --- |
| `id` | 主键 |
| `title` | 当前版本标题的便捷投影；每次 Version 确认时同事务更新 |
| `status` | `active` 或 `archived` |
| `current_version_id` | 当前不可变 Version 的 ID |
| `story_revision` | 每次确认新版本、归档或恢复递增的 fencing/CAS revision |
| `created_at` / `updated_at` / `archived_at` | 生命周期审计时间 |

第一期不提供物理删除接口。归档仅改变 `status`，不会删除 Version、来源链接或历史 Proposal。

已归档 Story 仍然可搜索（在 archived 筛选中）和查看，但不得创建新 Version 或发起新 Proposal；用户必须先携带当前 `story_revision` 明确恢复。恢复不会改变 `current_version_id`。

### 4.2 `interview_story_versions`

每次用户确认内容都创建一条不可变 Version：

| 字段 | 语义 |
| --- | --- |
| `id`、`story_id`、`version_number` | 稳定身份；`(story_id, version_number)` 唯一 |
| `content_json` | 规范化的标题、STAR 区块、能力标签、适用问题和事实缺口；每个可引用条目都有稳定 ID |
| `content_hash` | `content_json` 的 canonical JSON SHA-256 |
| `source_fingerprint` | 本版本最终选中来源及显式用户陈述的 canonical JSON SHA-256 |
| `origin_kind` | `manual` 或 `proposal`，仅作审计，不决定可信度或入口 |
| `confirmed_at` | 用户确认保存的时间 |

`content_json` 中所有可以被证据引用的条目都有一个仅在本 Version 内稳定的 ASCII ID；文本重复不影响链接身份。固定结构为：

```json
{
  "title": {"id": "title", "text": "用户确认的故事标题"},
  "blocks": [
    {
      "id": "action_1",
      "kind": "situation|task|action|result|reflection",
      "text": "用户确认的文本",
      "fact_mode": "evidence_backed|user_view"
    }
  ],
  "capability_labels": [{"id": "capability_1", "text": "能力标签"}],
  "applicable_questions": [{"id": "question_1", "text": "适用问题"}],
  "fact_gap_codes": ["missing_result"]
}
```

`title`、`situation`、`task`、`action`、`result`、每个能力标签和每个适用问题均必须有至少一条合规 Evidence Link。`situation`、`task`、`action`、`result` 只能使用 `evidence_backed`。`reflection` 可以使用 `user_view`，但必须明显标注为用户观点，并具有对应的用户陈述或其他允许来源；它不能以客观事实标签展示。手工创建、手工修订和 Proposal 确认均使用完全相同的 ID、Evidence Link 与验证规则。

### 4.3 `interview_story_version_evidence_links`

每个 Story Version 的每个区块与辅助条目通过独立链接保存证据。该表不保存可变的来源状态。

| 字段 | 语义 |
| --- | --- |
| `story_version_id`、`target_kind`、`target_id` | Version 与精确引用目标的归属；`target_kind` 只能是 `title`、`block`、`capability_label` 或 `applicable_question`，`target_id` 对应 `content_json` 中同类条目的稳定 ID |
| `source_kind`、`source_stable_id` | 来源类型与稳定身份 |
| `source_version_or_snapshot` | 原来源版本或最小冻结来源快照 |
| `source_path` / `text_location` | 规范 JSON Pointer 或受控文本区间 |
| `excerpt` | 保存时的逐字连续摘录；不做 trim 或 Unicode 规范化 |
| `source_fingerprint` | 该最小冻结来源的 hash |
| `link_hash` | 规范化链接自身的 hash，便于审计 |

对业务来源均使用普通标识字段，而非会在 Application、Note、Event、Resume 或 Attempt 删除时级联破坏历史的外键。Version 到 Story 的关系使用数据库外键；第一期没有 Story 物理删除，因此历史不会被无意删除。

### 4.4 `interview_story_user_assertions`

用户本次明确确认的原始陈述属于 Story 领域，不进入 Knowledge 或 Memory。确认 Version 时，对于每个被选择的陈述创建不可变 `user_assertion`：

| 字段 | 语义 |
| --- | --- |
| `id` | 稳定 assertion identity |
| `story_version_id` | 创建它的不可变 Version |
| `statement_text` / `statement_hash` | 原文及 SHA-256 |
| `confirmed_at` | 用户确认时间 |

`StoryVersionEvidenceLink.source_kind=user_assertion` 只能指向该表，路径固定为 `/statement`，摘录必须是其原文的逐字连续片段。它只证明“用户作出该陈述”，不证明外部事实，也不得在 UI 中显示为“外部已验证”。

### 4.5 `interview_story_proposal_attempts`

Proposal Attempt 与已确认 Story/Version 分离。它保存：

- `id`、`target_story_id`（初次创建为空）、`idempotency_key`；
- `entrypoint=ui|pilot` 与最小 `entry_context_json`（仅记录本次用户已选择的来源上下文，不记录 Chat 文本或模型原文）；
- `attempt_status`、`generation_revision`、`provider_call_token`、`provider_lease_until`；
- `input_snapshot_json`、`source_fingerprint`、`proposal_json`、`proposal_hash`、脱敏失败类别；
- `repair_count=0|1`：本 Attempt 已发生的有界内部 JSON 格式修复次数。它只用于脱敏执行审计，既不保存模型原文，也不改变证据、确认或用户重试语义；
- `confirmation_token_hash`、确认 payload hash、`confirmed_story_id`、`confirmed_story_version_id` 与确认时间。

`idempotency_key` 使用表内全局 `UNIQUE(idempotency_key)`。当前工作区没有持久化 `workspace_scope`，且 SQLite 对 nullable 复合唯一键的语义不能承担新建 Story 的作用域约束；目标 Story identity 始终纳入请求 fingerprint。全局 key 冲突但 fingerprint 不同稳定返回 `409`，且不得改写原 Attempt。所有 Attempt 快照均保留，不能把模型原文或 Provider 密钥写入其中。

### 4.6 Story Usage 的未来边界

第一期不建 `StoryUsage` 表、API、前端入口或模型字段，也不在 `InterviewStory`/`InterviewStoryVersion` 中预留 usage count、last-used 等冗余列。未来若经单独设计批准，可新建独立关联表，以 `story_version_id + application_event_id` 连接一次用户确认的使用记录；不会回填或猜测第一期历史。

## 5. 来源资格、快照与状态

### 5.1 允许来源

Story 只能使用用户显式选中的以下候选人事实来源：

1. 已保存的 `Resume Version` 的字符串叶子；
2. 已保存面试复盘的原文/Transcript 的显式片段；
3. 已完成 Mock Interview 的已完成 Turn，且只引用问题或回答的冻结文本；
4. 本次确认中用户明确提交的原始 `user_assertion`。

第一期**不接受任何 `KnowledgeEvidence`**。当前模型虽然包含 `KnowledgeSource.source_kind`、Captured Source 和面试知识沉淀路径，但尚未提供能对所有既有 Evidence 机械证明“这是候选人事实、而非外部知识”的封闭资格谓词。实施不得用标签、标题或文本猜测来源资格。未来只有在单独设计出当前模型可执行的 closed allowlist 与完整负向测试后，才能新增该来源。Imported external Knowledge、市场信息、公司政策、Memory、旧 AI Proposal、Application/JD 和未选中来源一律不得证明“候选人做过某件事”。

### 5.2 最小冻结与 Provider 投影

每次 Proposal 只冻结和发送：选定来源的最小文本、稳定身份、规范路径、逐字摘录和可选用户陈述。不得发送：

- 未选择的完整简历、全部复盘、全部 Mock transcript、Knowledge 或 Memory；
- 旧 Proposal、历史 Story、Application/JD、Offer、Chat 记录；
- 内部数据库 ID 以外的私密运行时信息、Provider 诊断或用户未选择内容。

服务端从冻结输入派生确定性 evidence catalog，按 `source_kind`、稳定身份、规范路径排序。模型仅可从该目录引用；服务端仍逐项重解路径并逐字验证，不信任模型声明的 hash 或来源状态。

### 5.3 冻结属性与读取时派生状态

每个已确认 Evidence Link 都有不可变的**冻结属性**：保存的来源快照、路径、摘录和 fingerprint。这不是状态枚举。

读取 Story Version 时，服务端对每个非 `user_assertion` 链接重新验证当前可见来源，并派生以下互斥状态：

| 派生状态 | 条件 |
| --- | --- |
| `current` | 当前来源可见、类型/归属合法，最小来源 fingerprint 与冻结 fingerprint 相同 |
| `changed` | 当前来源可见但文本、版本、归属或可引用位置与冻结来源不再一致 |
| `missing` | 当前来源不存在、不可见、删除，或不能安全解析 |

`user_assertion` 永远展示为“已冻结的用户确认陈述”，不参加 `current/changed/missing` 比较。所有 Version 都展示“冻结摘录”；UI 不得把 `frozen` 与上述三种读取时派生状态混成同一枚举，也不得把派生状态回写到 Evidence Link。

来源变化绝不自动覆盖 Story 或 Version。`changed`/`missing` 只提示风险、保留历史可读，并允许用户主动从当前来源创建新 Version。

## 6. Proposal、AI 与确认契约

### 6.1 生成资格与输入

用户确认 AI 请求后，服务端创建或恢复 Attempt。每个请求包括严格 ASCII 幂等键、显式 source selections、可选的用户原始陈述，以及对已有 Story 修订时的 `expected_current_version_id` 和 `expected_story_revision`。客户端不能提交 source fingerprint、模型输出或任意快照。

幂等键沿用当前 Proposal 流程的格式：`^[A-Za-z0-9_-]{16,128}$`。同一 key 的请求 fingerprint 覆盖目标 Story（或新建作用域）、目标 Version CAS 值、按稳定顺序的来源选择和原始 user assertion；不包含 UI 时间、显示状态、Provider 配置或模型输出。

服务端在短事务中验证来源资格、构建冻结输入、计算 canonical JSON 指纹并 claim Attempt；之后关闭数据库 session 再调用 Provider。生成期间不持有 SQLite 连接。最终写入使用 Attempt revision/token 和冻结来源的 CAS，避免迟到结果覆盖新 owner。

Attempt 的状态转换固定如下：

| 状态 | 含义与允许后继状态 |
| --- | --- |
| `generating` | 已冻结且一个 owner 持有 lease；可到 `ready`、`safe_empty`、`provider_unknown`、`contract_failed` 或 `invalidated` |
| `provider_unknown` | 调用结果未知；同 key 仅可在安全 lease/CAS 规则下重放或接管 |
| `ready` | 含可确认的正常 Proposal；可到 `confirmed` |
| `safe_empty` | 含固定空 Proposal；只读，不能确认成 Story |
| `contract_failed` | 不可验证的语义终态；同 key 只重放稳定失败，不再调用 Provider |
| `invalidated` | 冻结来源或目标 Story CAS 已失效；同 key 不可恢复 |
| `confirmed` | 已经创建唯一 Story Version；只读重放 |

### 6.2 严格输出

Proposal 顶层固定为：

```json
{
  "title": {"text": "string", "evidence_refs": []},
  "blocks": [],
  "capability_labels": [],
  "applicable_questions": [],
  "fact_gap_codes": []
}
```

- 每个事实性区块、能力标签和适用问题都必须含 1–5 条合法 `evidence_refs`；
- `evidence_ref` 精确为 `source`、`path`、`excerpt`，拒绝额外字段、跨 source 路径、未知 source、非规范路径和不匹配摘录；
- `reflection` 只能标为用户观点，且必须由用户陈述或允许来源支持；
- `capability_labels` 和 `applicable_questions` 是可审计的组织信息，不是评分、排名、录用判断或“最佳故事”结论；
- `fact_gap_codes` 使用服务器版本化枚举与固定中文模板，例如 `missing_result` 渲染为“请补充可验证的结果或影响。”，不接受模型自由编造事实缺口文本；
- 缺少 Result 时应使用 `missing_result`，不得补造量化指标或业务结果。

Provider 只返回 raw JSON。原生 JSON Schema 仅为能力优化；所有 Provider 都必须经过服务端重复键拒绝、严格 shape 校验、数组/文本上限、证据路径白名单、逐字 excerpt 与来源资格校验。

### 6.3 失败语义

| 情况 | Attempt 结果 | 客户端 key |
| --- | --- | --- |
| 前置输入/选择非法，且未创建 Attempt | `422` | 清理 key |
| 来源/Story 不可见，且未创建 Attempt | 稳定 `404` | 清理 key |
| 同 key 不同冻结输入 | 稳定 `409`，不改写旧 Attempt | 保留旧 key 仅用于原输入恢复；新输入须新 key |
| 来源或 Story revision CAS 冲突 | `409 story_source_conflict` | 清理本次生成 key，重新选择/确认 |
| Provider/网络/超时/响应丢失 | `502 story_provider_error` 或 `202` pending | 保留 key、冻结输入，只允许同 key 重试 |
| 纯 JSON/对象/字段类型/额外字段错误 | 最多一次同输入格式修复 | key 不变 |
| 两次可修复结构失败或合法但无可验证内容 | 持久化 `safe_empty` | 同 key 稳定重放；不能确认成 Story |
| 伪造来源、非法路径、摘录不匹配、越界/评分/决策语言、超限等语义失败 | `contract_failed` 与稳定 `502 story_unverifiable` | 同 key 不再调用 Provider；用户新建尝试 |

修复提示只包含机器失败类别、固定 JSON 契约和允许 evidence 对象形状；不得回传模型原文、冻结来源、用户内容、密钥或 Provider 请求 ID。

### 6.4 确认与保存

确认请求包含 Attempt ID/key、确认 token、最终选择/编辑的 Version payload、expected Story revision/current version，以及确认幂等键。服务端在一个 `BEGIN IMMEDIATE` 事务中：

1. 先处理同 token 的已确认重放，稳定返回同一 Story/Version；
2. 验证 Attempt 为可确认 `ready` Proposal、token 未过期且未被其他确认消费；
3. 重新验证所有非 assertion 的冻结来源；若漂移则返回 `409 story_source_conflict`，不创建 Version；
4. 验证编辑后的事实性区块仍有合规 Evidence Link；新添原始陈述必须显式进入 `user_assertion`，并被标为用户陈述；
5. 新建 Story 时原子创建 `Story + Version + Links + Assertions`；修订既有 Story 时只新增不可变 `Version + Links + Assertions`；
6. 对 Story 执行 `story_revision` 与 `expected_current_version_id` CAS，更新 current Version 指针、标题投影和 revision；
7. 标记 Attempt 已确认并保存最终 payload hash。

首次创建的 `expected_current_version_id` 必须为 `null`，修订必须为严格正整数。归档与恢复也必须携带 `expected_story_revision`；冲突只返回 `409`，不覆盖更晚状态。

## 7. API 与前端边界

实施阶段将以单独的 `/api/interview-stories` 路由族承载：

- Story 列表、详情、Version 历史与来源只读展示；
- 手工创建首个 Version、创建后续 Version、归档与恢复；
- Proposal 创建/恢复、详情和确认。

路由职责固定为：

| 路由 | 职责 |
| --- | --- |
| `GET /api/interview-stories` | 只读列表，返回元数据、当前 Version 摘要与读取时来源状态，不返回未决 Proposal |
| `POST /api/interview-stories` | 用户明确手动保存首个 Version；无 AI |
| `GET /api/interview-stories/{story_id}` | 只读 Story 与当前 Version |
| `GET /api/interview-stories/{story_id}/versions` / `.../{version_id}` | 只读 Version 与冻结 Evidence Links |
| `POST /api/interview-stories/{story_id}/versions` | 用户明确手动保存后续 Version；无 AI，要求 Story CAS |
| `POST /api/interview-stories/{story_id}/archive` / `restore` | 生命周期写入，要求 Story revision CAS |
| `POST /api/interview-story-proposals` | 经确认后创建或重放 Story Proposal Attempt |
| `GET /api/interview-story-proposals/{attempt_id}` | 只读/恢复同一 Attempt，不调用 AI |
| `POST /api/interview-story-proposals/{attempt_id}/confirm` | 二次 HITL，原子创建首个或后续 Version |

字段上限、路由注册顺序和稳定 error code 在实施计划中细化；本设计已固定以下不变量：

- 读取、搜索、展开 Evidence、查看历史绝不调用 AI 或产生业务写入；
- 手动保存是用户显式写操作但不调用 AI；
- Proposal 请求与确认是两次独立的人工确认点；自动批准设置不得绕过确认保存；
- 历史读取使用保存的 Version 和 Evidence Links，不会替换当前未决 Attempt/key；
- 归档故事仍可只读查看历史，恢复只改变 Story lifecycle，不改写 Version；
- UI 与 Pilot 只导航/调用同一 API，不复制领域规则。

前端将把故事库置于顶层“面试”模块。页面维持现有布局语言：故事数量说明、搜索、`active/archived` 筛选、卡片、能力标签、适用问题、冻结来源/来源风险、事实缺口和最近确认 Version。详情使用既有 Drawer/详情模式，展示 STAR、Evidence、版本历史和归档操作；不把 Application Detail 改造成综合工作台。

最终实现的所有固定文案使用中文。若进入前端实现和发布验收，必须提供亮色、中文、宽屏截图；本设计阶段不生成截图。

## 8. 并发、恢复与历史不变量

- 同幂等 key、同规范输入只产生一个 Attempt/Provider owner，稳定重放同一状态；
- 同 key、不同输入稳定 `409`，不得失效或覆盖仍可恢复的原 Attempt；
- 生成 Claim、Provider 回写和确认均使用 revision/token fencing；旧 owner 的迟到结果不能覆盖新 owner；
- Provider 结果未知时，前端由 `AppShell` 或等价上层状态按 `target_story_id`/新建作用域保存完整 draft、来源选择、原 key 与 token，卸载重进后只能恢复原尝试；
- 确认响应丢失时，保留确认 token、最终 payload 和选择状态，重放同 token；不得创建第二个 Version；
- 查看历史绝不改写进行中的草稿或其未知结果；
- 新 Version 确认后，旧 Version 和其 Evidence Links 永远只读；
- `current/changed/missing` 只在读取时派生，来源变更不自动重生成、不自动替换、不自动写 Knowledge/Memory。

## 9. 测试与验收口径

实施计划至少应覆盖：

1. 手动新建、修订新 Version、版本历史、归档/恢复和 CAS 冲突；
2. 从单条已保存面试复盘的显式片段生成 Proposal；
3. Resume Version、复盘片段、已完成 Mock Turn 和 `user_assertion` 的来源路径；任何 `KnowledgeEvidence`、外部 Knowledge 或无法机械验证的来源均被拒绝；
4. `user_assertion` 只能显示为用户陈述，绝不写入 Knowledge/Memory、绝不伪装为外部事实；
5. STAR 各区块引用门控、Reflection 用户观点标注、缺少 Result 的固定 fact gap、数字/范围/团队规模/业务结果不可伪造；
6. `current/changed/missing` 读取派生与独立冻结属性；来源删除或变化后历史仍可读但不能静默覆盖；
7. 同 key 重放、同 key 冲突、双 SQLite connection 并发确认、Provider 迟到结果、未知结果重挂载恢复；
8. Proposal 失败、安全空 Proposal 和确认失败均不产生 Story/Version；
9. 确认保存只写 Story 领域表，零 Knowledge、Memory、Application、Event、Mock 或 Chat 领域写入；
10. UI/Pilot 同一 Proposal/确认契约，Pilot 未主动触发时零 Story 写入；
11. 读取和浏览器挂载零 Provider、零业务写入；中文、emoji、空白、超限、非法 JSON、非法 path、伪造 excerpt 与来源越权边界；
12. 隔离真实 AI、中文亮色宽屏浏览器闭环、临时数据库/服务/浏览器/配置清理，以及完整后端/前端门禁、Ruff、Mypy、构建、smoke、local verify、real-AI verify。

## 10. 与 JD Version 分支的并行与合并策略

故事库从当前干净 `main` 的 `a7b660d` 创建，绝不基于 `feat/20260805-application-jd-versions`。审计得到 JD 分支相对其真实 fork point `b4363b0` 的中心变更包括：

| 共享中心文件 | JD Version 分支 | Story Library 的后续策略 |
| --- | --- | --- |
| `src/offerpilot/api.py` | 已修改 | 新路由注册集中在独立函数/连续路由块；合并时人工重放并验证 route order |
| `src/offerpilot/models.py` | 已修改 | 新增独立 Story 表族；不改 JD 表字段和删除语义 |
| `src/offerpilot/schemas.py` | 已修改 | 新增独立 schema 区；不复用或改写 JD payload |
| `web/src/layout/AppShell.tsx` | 已修改 | Story draft/state 转入独立 feature hook，AppShell 仅最小注册与导航接线 |
| `src/offerpilot/repositories/mock_interviews.py` | 已修改 | Story 仅以只读完成 Turn 来源适配器读取，不在 Mock repository 塞 Story 写入 |
| 面试准备/材料/机会评估前端与 repository | 已修改 | 第一期开端不把 Story 接入这些消费链路；只在未来单独设计 |

实施前必须重新计算双方相对各自真实 fork point 的文件集合交集，并在实施计划中记录。若交集含以上中心文件，按“新模块优先、中心文件最小接线、逐文件人工合并与双分支回归”执行；不得以隐藏交集宣称无冲突。

## 11. 破坏性变化、风险与后续触发条件

第一期新增独立 Story 数据表与 API，不修改现有 Knowledge、Interview、Mock、Application 或 JD 的已有写入语义；现有数据不回填为 Story。迁移可新增表，不应重写历史业务记录。

主要风险是将候选人自述误读为外部验证事实，或让自由模型输出绕过来源门控。前者通过独立 `user_assertion`、明确 UI 标签和不写入 Knowledge 限制；后者通过最小来源目录、严格 JSON、路径/摘录复核、一次受限修复和安全空/终态失败语义控制。

后续只有在第一期真实使用证明确有需求时，才单独设计 Story Usage、面试准备/模拟面试消费、练习、故事选择或跨设备偏好持久化。它们不得反向改变第一期的确认、版本和证据不变量。

## 12. 设计自检

- 无未决空白项、无“自动写入”例外，也无将 Story 归入 Knowledge 或 Memory 的表述；
- `frozen` 仅为已确认快照属性，`current/changed/missing` 仅在读取时派生；
- `user_assertion` 是 Story 内不可变来源，只证明用户陈述；
- 初建与修订的原子写入边界、版本不可变性和 Story revision CAS 已分别定义；
- 第一期开端不创建或预留 Story Usage 的持久化字段；
- UI/Pilot、并发恢复、来源变化、证据门控、跨分支冲突与验收边界均已固定；
- 本文是设计规格，不构成实施计划，也不授权产品代码改动。
