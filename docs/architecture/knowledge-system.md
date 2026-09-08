<!-- 超限原因: 本文是跨 Knowledge、Memory、Interview、Conversation、Exercise 与 Pilot 的长期架构和数据流 SSOT。 -->
# OfferPilot Knowledge 系统：核心方向与架构设计

**Status**: Accepted
**Last updated**: 2026-07-18
**Decider**: 用户
**Document type**: Living Architecture Document
**Supersedes**: 以自动 Wiki Page 为中心的 Knowledge Rewrite 方向

## 1. 文档定位

本文是 OfferPilot Knowledge 系统的长期架构事实源，定义产品职责、上下文边界、领域模型、
核心数据流、质量不变量和演进约束。后续实施 Spec 必须引用本文，不得通过局部实现改变本文边界。

前一轮以自动 Wiki 为中心的 Spec、Plan 和 ADR 已删除，必要的调研结论、否决理由与决策过程
已归档在本文。ADR-0001 的 SQLite 单一事实源原则继续有效。具体 API、字段、迁移、任务和
发布日期由后续实施 Spec 定义。

## 2. 核心结论

OfferPilot Knowledge 是 Pilot 的可审计长期知识底座。它首先服务 Pilot 对话，其次服务练习
生成、答案解释和评分依据。

系统不再把“导入一份 Source 后自动创建或修改多个主题 Page”作为 Ingest 目标：

```text
Imported Source → Extraction Snapshot → Evidence
Captured Source → deterministic capture → Evidence

Evidence ───────────────┐
Knowledge Note Version ─┼→ Knowledge Context → Pilot / Exercise

Pilot 与用户讨论 → Note Preview → 用户确认 → Knowledge Note Version
```

核心原则：

1. Source 与 Evidence 是可追溯的事实底座。
2. Source Brief 不进入 V1；V1.1 再基于人工使用价值决定是否保留或继续开发。
3. Knowledge Note 是 Pilot 与用户共同确认的长期知识成果。
4. Knowledge Context 是面向 Pilot 和练习、按任务临时组装的统一输出。
5. Knowledge 不自动吸收其他业务模块，也不保存未确认的 Pilot 草稿。
6. Knowledge Note 首版不建立类型、标签、集合、主题树、Wikilink 或自动 Wiki Page。
7. 检索同时召回当前 Note 和 Evidence，不能让任一层遮蔽另一层。

## 3. 为什么重新设计

### 3.1 原始 LLM Wiki 模式的隐含前提

`docs/llm-wiki.md` 描述的是人类、Agent 与 Obsidian 协作维护 Wiki 的工作方式：一次处理一个
Source，人类阅读摘要并指导模型，Agent 更新多个 Markdown Page、索引和链接，人类再持续检查。

OfferPilot 将这一工作方式产品化成无人值守 Ingest Pipeline，却去掉了最关键的人类反馈环，
同时保留“一份 Source 自动修改多个 Page”的假设。工作流模式因此被误当成了产品规格。

### 3.2 五份真实 Source 暴露的问题

2026-07-12 对本地运行库的核查结果：

- 五份正式测试 Source 中四份进入 `done`，一份因 Analysis 与 Generation slug 漂移失败。
- 四次成功 Ingest 只生成 14 个 Page，却产生 52 个待处理 Review。
- 14 个 Page 中四个完全没有当前 Evidence。
- 一份高并发 Source 生成的两个 Page 合计近 1.3 万字符，但 citation 数为 0。
- 同一份 Source 生成 67 个 Wikilink 标记，并触发 34 条 broken-link Review。
- Index section 出现 `body`、`候选页`、`(new page)` 等模型过程文本。
- 部分 Page 大量重复引用少数 Evidence，citation 数量不能代表内容覆盖。

旧校验只能证明 citation ID 存在、Wikilink 语法可解析，不能证明每条事实得到支撑、引用真正支持
陈述、主要章节得到覆盖、链接目标存在，或者 `done` 的产物对 Pilot 有用。问题不只是 Prompt，
而是 Ingest 成功定义和核心知识对象错误。

## 4. 调研结论

### 4.1 `llm-wiki-generation`

该项目验证了 `analysis manifest → page operations → transactional index patch` 的职责拆分，
但 Source 全文直接进入 LLM，没有稳定 Evidence；Generation 逐页写盘，缺少完成性校验；
恢复和孤儿清理存在缺口；全局 slug 与分类型目录规则冲突。最值得借鉴的是类型化 Index patch
和内容寻址缓存，而不是三轮 LLM 本身。

### 4.2 `llm_wiki`

可借鉴机制包括多格式解析、图片缓存、结构感知长文分块、滚动 digest、持久 checkpoint、
Source identity、hash 缓存、持久队列、路径白名单和 frontmatter normalization。

其局限是 LLM 同时负责抽取、命名、拆页、合并和全局目录；文件逐个落盘，部分成功可能被当成成功；
相同路径 merge 无法处理同义主题；只有 Source 文件名级 provenance；Lint 和 dedup 不是提交门禁。

### 4.3 `java-study` 产物

- 246 个 Source 扩张成约 1200 个 Wiki Markdown Page。
- 每个 Source 平均产生约 7.87 个文件，最多 44 个。
- 6471 个 Wikilink 中有 658 个指向不存在页面，涉及 286 个唯一目标。
- 存在同标题重复、跨类型冲突和同义主题分叉。
- `index.md` 超过 1200 行，`overview.md` 的页面总数长期失真。
- 原始 prompt/思考痕迹也可能被当成 Source 并编译为知识页。

结论不是不能生成长期知识，而是不能在缺少用户目标和质量门禁时批量猜测知识结构。

### 4.4 主流知识库与 RAG

Dify、RAGFlow、Open WebUI、AnythingLLM、Khoj、FastGPT、QAnything 等项目共同证明：

- 原始片段检索、来源回指和检索测试不能被生成文章替代。
- 复杂文档应先保证 extraction 和片段质量可观察。
- 多路召回与重排应由评估结果驱动，而不是先选择重型框架。
- 图谱项目的启发是事实、时间和来源的结构化，不是立即引入图数据库。

OfferPilot 暂不直接依赖这些完整平台，也不在当前阶段引入 GraphRAG、外部向量库或事实图谱。

## 5. 产品职责

### 5.1 输入

Knowledge 只有两类 Source：

1. `Imported Source`：用户主动导入的完整外部资料或用户原始材料。
2. `Captured Source`：保存 Note 时，从 Business Record、Conversation、练习或网络调研中，
   经用户确认捕获的相关原始片段。

完整面试转写、Application、JD、Resume、普通对话、练习结果、临时网络搜索和未确认的 Pilot
总结不得自动成为 Source。它们可以产生 Note Preview；确认时只捕获相关原始片段。

### 5.2 输出

第一消费者是 Pilot，用于回答、解释、比较、追问，复用已确认 Note，通过 Evidence 核验结论，
并发现新 Evidence 与旧 Note 的冲突。

第二消费者是练习，用于生成题目、答案框架和追问，以 Evidence 核验答案并展示出处。
Memory 可以调整难度和表达，但不能证明知识事实。

后续可能包括面试前复习、模拟面试和知识缺口建议，但不得提前反向扩张已确认版本的数据模型。

### 5.3 用户可见的最终形态

Knowledge 不再以自动主题树作为首页，而是提供两个稳定入口：

```text
知识成果
└── Knowledge Note
    ├── 标题与正文
    ├── Evidence 引用与 Source
    ├── 来源 Conversation / Interview / Exercise
    ├── 当前版本与历史版本
    └── 继续讨论、生成练习、归档

资料来源
└── Source
    ├── Imported / Captured 标识
    ├── Source Brief（V1.1 候选）
    ├── 原文与资产
    ├── Evidence 和位置
    └── Extraction 状态与独立 Brief 状态
```

Pilot 对话中的“保存到知识库”先展示 Note Preview 与引用片段。用户一次确认后，系统完成
Captured Source、Evidence 和 Note Version 的原子保存，不要求用户先走一次独立 Source 导入。

Note 可以按页面形式阅读，但“页面”只是 UI 呈现，不是独立领域对象。Note 首版不承诺 Wiki 目录、
知识图谱或自动专题导航。

## 6. 上下文边界

**Knowledge**：从 Source 中提取或由用户确认沉淀的、关于外部世界且可复用和引用的认知。

**Memory**：Pilot 对用户本人形成的持续认知，包括偏好、目标、薄弱点和掌握程度。

**Business Record**：由 Interview、Application 等业务上下文持有、描述具体发生了什么的事实记录。

同一次面试复盘可以拆分为：

| 内容 | 所有者 |
|---|---|
| 面试录音、完整转写、公司和轮次 | Interview Business Record |
| “本轮问过 Kafka ISR”及对应转写片段 | Captured Source / Evidence |
| “Kafka ISR 的正确机制” | Knowledge Note，引用技术 Evidence |
| “用户没解释清楚 ISR 与 AR” | Memory |
| 下一次复习安排 | Exercise 或计划模块 |

三者可以在 Pilot 中联合使用，但不能混表或互相冒充。

## 7. 统一领域语言

### 7.1 Source

Knowledge 中不可变的知识来源。Source 只能是 Imported Source 或 Captured Source；
Pilot 生成的总结或综合结论不是 Source。

### 7.2 Imported Source

用户主动导入的完整外部资料或用户原始材料。其身份由原始字节、资产清单和 Source hash 确定。

### 7.3 Captured Source

从 Business Record、Conversation、练习或网络调研中，经用户确认捕获的原始片段，不保存 Pilot
改写。来源类型决定 Evidence 能证明什么：Interview capture 证明当时发生什么，Conversation
capture 证明用户确认某种表达，Web capture 支持外部事实但必须保留 URL、时间和捕获正文。

### 7.4 Source Bundle

一份主文本与其引用资产组成的单个原子 Source。具体格式和大小限制由实施 Spec 定义。

### 7.5 Extraction Snapshot

从 Source 确定性提取出的规范文本、结构位置和资产清单的版本化快照，由
`source_hash + extractor_version` 标识，是 Evidence 的稳定上游。

### 7.6 Evidence

Source 中可稳定定位、可原样回读的文本片段或资产，是 citation 和审计的最小单位。Evidence
不是摘要、事实结论、embedding 或检索 Chunk，也不保证 Source 本身正确。

文本 Evidence 优先沿自然结构生成，保留标题路径、段落、代码块和表格边界；过长段落按句子边界
切分；保存逻辑路径、位置、内容 hash 和 Snapshot 版本。Evidence ID 不应因检索策略变化而失效。

### 7.7 Source Brief

若 V1.1 决定保留，则优先采用用户显式触发、针对单个 Imported Source 生成的结构化导读。Brief 与
Imported Source 一对一，只能访问当前 Source Evidence，每条事实性陈述必须引用 Evidence。
它可重建、不是事实来源或 Knowledge Note；V1 只用于人类资料浏览，不能作为最终 citation，
也不作为 Pilot、练习或 Knowledge Context 的输入。

Brief 的正确性指忠实、完整、可追溯地反映 Source，不代表系统验证 Source 的客观真实性。

### 7.8 Knowledge Note

Pilot 基于 Evidence 整理、经用户确认保存的可版本化知识成果，取代旧 Wiki Page 作为长期知识对象。

Note 首版不设类型，不自动生成标签，不建立集合、专题分类或主题树，不使用 slug、Wikilink 或
Index section 作为身份，并且必须引用至少一条 Evidence。

### 7.9 Note Preview

Pilot 在 Conversation、Interview 或练习流程中生成、等待用户确认的临时草稿。它不属于正式
Knowledge，不参与检索。

### 7.10 Knowledge Note Version

用户确认后的 Note 完整快照。默认检索只使用当前有效版本；旧版本用于审计和回滚。

### 7.11 Knowledge Context

针对一次 Pilot 或练习任务临时组装的机器消费对象，可包含当前 Note、supporting Evidence、
尚未进入 Note 的 additional Evidence、Source 位置与时间、provenance 和冲突。

Knowledge Context 默认不沉淀为知识。检索 trace 可以记录用于评估，但不能反向污染 Knowledge。

### 7.12 Ingest

将 Imported Source 转换为可搜索、可引用、可审计的 Evidence 的过程。
Captured Source 在 Note 确认流中轻量创建，不运行完整 Ingest。Ingest 不自动创建或修改
Knowledge Note。

### 7.13 Supporting Terms

**Asset**：Source Bundle 中不可变的二进制内容。

**Ingest Job**：持久执行一次 Imported Source Ingest 的后台任务；Evidence 可搜索状态与 Brief
生成状态分别记录。

**Knowledge Ingest Worker**：执行确定性 Extraction、Evidence 索引和受约束 Brief 生成的领域服务，
与对话 Pilot 隔离。

**Retrieval Trace**：记录一次 Knowledge Context 召回、排序和引用结果的评估数据，不属于 Knowledge。

**Export**：从当前 Source、Brief、Note 和 Evidence 生成的只读快照，不回写运行时。

### 7.14 退出核心模型的旧术语

旧 `CONTEXT.md` 中的 Wiki Page、Page Version、Protected Page、Index Entry、Mutation Review、
Informational Review、Page Type、Subtype、Slug 和 Wikilink 均属于已否决的自动 Wiki 模型。

旧 Purpose/Schema 作为“驱动模型自动组织 Wiki”的配置层不进入当前路线。未来若 Brief 或 Note
确实需要可配置写作规则，应基于真实行为重新设计，不能直接恢复旧动态 Wiki Schema。

## 8. Imported Source Ingest

```text
1. Source Preflight
2. Deterministic Extraction
3. Evidence Generation
4. Evidence Indexing
5. Commit / Status Update
```

Preflight 在任何模型调用前验证格式、编码、空内容、大小、资产完整性和解析能力。Extraction 和
Evidence 必须确定性执行；Evidence 提交并可搜索即表示 Imported Source 达到 V1 可用状态。

Brief 不属于 Ingest 完成条件，也不属于 V1 产品和验收范围。现有 Brief 代码可以暂时保留，
但不得自动触发，不得改变 Source/Evidence 可用性，也不得阻塞 Knowledge V1 发布。

Source 可用状态与可选 Brief 状态分开：

| 状态 | 含义 |
|---|---|
| Source `extracted` | Evidence 已生成，可以在工作台搜索、定位和回读 |
| Brief `not_requested` | V1 的正常状态；不提示用户必须生成 Brief |
| Brief `pending` | V1.1 候选能力被显式触发后正在执行 |
| Brief `ready` | 候选能力已生成结果；不改变 Source 状态 |
| Brief `failed` | 候选能力失败；Source/Evidence 仍可用 |

Brief 可独立重试或重建，不删除 Source 和 Evidence。

## 9. Captured Source 与 Note 保存

```text
Business Record / Conversation / Exercise / Web
        ↓
Pilot 生成 Note Preview 与引用片段
        ↓
用户确认
        ↓ 单次原子操作
1. 捕获相关原始片段为 Captured Source
2. 确定性生成 Evidence
3. 创建或修订 Knowledge Note Version
4. 建立 Note Version → Evidence 引用
```

用户不需要先手动导入 Captured Source。Captured Source 不运行完整文章 Brief Pipeline，只保存
确认片段、来源位置和 hash。若 Note 引用已有 Evidence，则直接复用，不能把同一上游重复计数为
多个独立来源。

## 10. Knowledge Note 生命周期

Knowledge 只保存用户确认后的版本。Pilot 草稿留在原业务流程；创建和修订都需确认并生成新版本；
默认只召回当前版本；旧版本用于审计和回滚；Note 可归档，归档不同于隐私清除式删除。

新 Evidence 与 Note 冲突时，Pilot 应说明差异并提议修订，不能自动覆盖。

## 11. 检索与 Knowledge Context

```text
Query
  ├→ Note retrieval ─────┐
  └→ Evidence retrieval ─┴→ merge / dedupe / rank → Knowledge Context
```

- Note 提供用户已确认的结论和组织方式。
- Evidence 提供原始细节、核验依据和尚未进入 Note 的信息。
- Brief 不参与 V1 检索、排序或 Knowledge Context 组装。
- Source 原文仅在需要扩大上下文时按位置回读。
- Memory 只影响个性化、排序和难度，不证明知识结论。
- 新 Evidence 不能被旧 Note 遮蔽，旧 Note 也不能因存在 Evidence 而失去复用价值。

V1 先以 SQLite FTS 建立可评估基线。只有真实查询证明语义召回不足时，才增加 embedding、
向量召回或 rerank；领域模型不绑定某种向量实现。

“Brief 辅助 Source 粗排”保留为后续候选能力。只有真实检索 trace 和离线对照实验表明它对
召回或排序有稳定增益，才重新进入设计；不能因为 Brief 已存在就默认让机器消费。

## 12. 逻辑数据模型

```text
Source
├── Imported Source
│   ├── Extraction Snapshot
│   ├── Evidence
│   └── Source Brief
└── Captured Source
    └── Evidence

Knowledge Note
├── current version
└── Knowledge Note Version
      └── citations → Evidence

Knowledge Context
├── current Note Versions
├── supporting Evidence
├── additional Evidence
└── conflicts / provenance
```

建议表族由实施 Spec 细化：

```text
knowledge_sources
knowledge_source_assets
knowledge_extraction_snapshots
knowledge_evidence
knowledge_evidence_fts
knowledge_source_briefs
knowledge_notes
knowledge_note_versions
knowledge_note_evidence
knowledge_ingest_jobs
knowledge_logs
```

V1 核心模型不保留 `knowledge_wiki_pages`、Page Version、Index Entry、Wikilink、`page_type`、
`subtype`、slug、Protected Page 或自动 Page Mutation Review。

## 13. 物理存储

SQLite 继续作为 Knowledge 运行时唯一事实源。文件系统只保存 Source 原件、资产、临时文件和导出：

```text
$OFFERPILOT_DATA/
├── config.json
├── data.db
├── knowledge/
│   ├── sources/
│   │   └── <source-id>/
│   │       ├── <original-or-captured-content>
│   │       └── assets/
│   ├── staging/
│   ├── quarantine/
│   └── exports/
└── logs/
```

Note 和 Brief 运行时保存在 SQLite，不维护实时 Markdown Wiki。Obsidian/Markdown 只作为只读导出，
不回写运行时。Memory 可以共用 `data.db`，但必须使用独立表族和领域服务。

## 14. 为什么不是纯传统 RAG

传统 RAG 在查询时从 Chunk 重新生成答案。OfferPilot 保留 Source/Evidence 检索底座，同时增加
用户确认的 Knowledge Note：

- 重要的跨 Source 思考可以长期保存。
- 用户修正后的结论可以形成新版本。
- Pilot 与练习可以复用同一份确认成果。
- Note 与 Evidence 同时召回，沉淀不会遮蔽原文细节。

区别不在是否使用检索，而在真实使用中形成的、带 Evidence 且经确认的思考结果能否产生复利。

## 15. 为什么不继续自动 Wiki

- 第一消费者是 Pilot，而不是主动维护大型 Wiki 的用户。
- 导入时缺少用户问题，模型无法判断哪些主题值得长期维护。
- 自动拆页、命名、合并和断链的复杂度已超过可验证价值。
- Page 生成遗漏会遮蔽 Source 细节。
- `java-study` 已展示页面爆炸、重复、漂移和断链的规模化后果。

长期知识成果因此由 Pilot 的真实使用和用户确认驱动，而不是导入时批量猜测。

## 16. 发布阶段与暂缓项

### 16.1 Knowledge V1：Source / Evidence 工作台

- Imported Source 上传与不可变存储。
- 确定性 Extraction Snapshot 与 Evidence。
- Evidence 检索、回读和引用。
- 小规模、可回归的检索与 citation 评估集。
- Source 归档、删除、Job 恢复、数据完整性和无 Provider 可用性验证。
- Source 工作台中的导入、搜索、Evidence 定位、原文回读和处理状态。

V1 不包含 Brief、Captured Source、Knowledge Note、Knowledge Context、Pilot 或练习消费。

### 16.2 Knowledge V1.1 候选：Source Brief

V1 发布并获得人工使用反馈后，再决定是否提供 Brief。优先验证“用户是否真的需要单 Source 导读”，
再决定手动生成、质量门禁和状态机范围；不预先承诺自动 Brief 或 ADR-0003 的复杂设计。

### 16.3 Knowledge V2：内部消费链路

- Captured Source 原子捕获。
- Note Preview 的 HITL 确认。
- Knowledge Note 版本、归档和 Evidence 引用。
- Note 与 Evidence 并行召回。
- Pilot 与练习共享 Knowledge Context。

### 16.4 明确暂缓

- 自动主题 Page、Page/Note Type、Subtype、标签、主题树、Collection、Wikilink 和图谱 UI。
- 自动订阅 Interview、Application、Resume、JD 或 Exercise。
- 未经确认自动保存 Pilot 生成内容。
- 向量数据库、GraphRAG 和实体事实图谱。
- Obsidian 双向同步。
- 由 LLM 自动解决 Note 与新 Evidence 的冲突。
- 自动 Source Brief、Brief 辅助 Source 粗排，以及 Pilot/练习消费 Brief。

## 17. 架构不变量

1. Pilot 生成内容在用户确认前不能进入正式 Knowledge。
2. 每个 Knowledge Note Version 至少引用一条有效 Evidence。
3. Pilot 总结或 Note 不能被伪装成 Source 或原始 Evidence。
4. Captured Source 只保存确认相关的原始片段，不复制完整业务记录。
5. Evidence 必须稳定定位并回读到对应 Source Snapshot。
6. Brief 事实条目必须引用当前 Source Evidence；其质量流程不得进入 V1 核心发布门禁。
7. Brief 未生成或失败不能破坏 Source 和 Evidence 的可检索性；Brief 不能作为最终 citation，
   也不能进入 Pilot、练习或 Knowledge Context。
8. 默认检索只使用当前有效 Note Version。
9. Note 与 Evidence 必须并行召回，任一层都不能完全遮蔽另一层。
10. Memory 不能被当作知识事实依据。
11. Ingest 不自动创建或修改 Knowledge Note。
12. Note 首版不要求类型、标签、slug、Wikilink 或主题分类。
13. SQLite 是运行时 SSOT；导出文件不回写运行时。

## 18. 验证与演进触发条件

Ingest 评估至少覆盖 Extraction 结构保真、Evidence 回读稳定性、检索可用性，以及不配置 AI
Provider 时 Source 仍能完成导入。

检索评估至少跟踪 Note/Evidence Recall@K、MRR、Citation coverage，以及已有 Note
存在时新 Evidence 是否仍能被发现。

只有 FTS 在中文术语、同义词或长问题上持续低于验收目标，才引入 embedding/rerank。只有用户持续
要求按固定维度浏览、稳定过滤或 Note 数量难以管理，才增加标签、集合或主题视图。只有重复查询
反复产生相同综合，才考虑自动建议 synthesis Note；保存和修订仍需用户确认。

## 19. 决策过程摘要

| 曾考虑的方向 | 结论 | 原因 |
|---|---|---|
| 每次 Ingest 自动生成多个 Wiki Page | 否决 | 缺少用户目标，质量与结构不可控 |
| 只搜索 Wiki Page | 否决 | Page 遗漏会遮蔽原始细节 |
| 只做传统 RAG | 不完整 | 缺少用户确认知识的长期复利 |
| Wiki Page + Evidence 双层 | 被 Note + Evidence 取代 | Page 仍带来自动拆页和维护负担 |
| Pilot 整理稿作为 Curated Source | 否决 | 混淆原始来源与派生知识 |
| 无 Source 的 Knowledge Note | 否决 | 长期知识无法审计 |
| 整份业务记录复制进 Knowledge | 否决 | 数据重复、边界和隐私成本过高 |
| Captured Source 保存相关原始片段 | 接受 | 统一 Evidence 链且用户只需一次确认 |
| 自动 Source Brief | V1 不包含 | 先验证 Source/Evidence 主链路；V1.1 再决定是否值得投入 |
| 手动 Source Brief | V1.1 候选 | 保留已有代码不等于承诺发布，需由人工使用价值触发 |
| Brief 辅助 Source 粗排 | 后续候选 | 必须由真实检索数据证明收益后再设计 |
| Pilot / 练习消费 Knowledge | V2 | V1 先验证 Knowledge 自身可靠性，不提前扩张消费者 |
| Note 预设类型和标签 | 暂缓 | 当前无下游行为需要 |
| Note 草稿进入 Knowledge | 否决 | 未确认内容会污染正式知识 |
| Note 与 Evidence 并行召回 | 接受 | 同时复用沉淀成果与原文细节 |
| V1 直接使用向量库 | 暂缓 | 先用 FTS 和真实查询建立基线 |

## 20. 文档治理

- 本文是 Knowledge 核心方向和领域模型的 SSOT。
- 修改 Knowledge、Memory、Pilot retrieval 或练习消费前必须先阅读本文。
- 本文只在新的用户行为、运行数据或评估结果推翻现有假设时修订。
- 具体实施 Spec、ADR 和代码只引用本文，不复制完整定义。
- 不再维护根目录临时 `CONTEXT.md`。

## 21. 参考

- `docs/llm-wiki.md`
- `docs/architecture/knowledge-open-source-research-20260712.md`，历史调研快照
- `docs/architecture/decisions/0007-use-sqlite-as-knowledge-wiki-ssot.md`
- `/Users/xys/Github/llm-wiki-generation`
- `/Users/xys/Github/llm_wiki`
- `/Users/xys/Desktop/java-study`
- Dify、RAGFlow、Open WebUI、AnythingLLM、Khoj、FastGPT、QAnything
- LlamaIndex、Haystack、Microsoft GraphRAG、LightRAG、Graphiti、Mem0、Cognee

## 22. 变更记录

- 2026-07-18：V1 路线再次收缩为 Source/Evidence 工作台；Brief 进入 V1.1 候选，内部消费链路、
  Captured Source、Knowledge Note 和 Knowledge Context 进入 V2。
- 2026-07-12：基于真实 Ingest 样本、开源实现调研和完整设计访谈建立初版；用
  Source/Evidence/Brief/Note/Context 方向取代自动 Wiki Page 方向。
