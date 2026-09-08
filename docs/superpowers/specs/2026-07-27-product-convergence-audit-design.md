# OfferPilot 产品收敛审计与设计

**日期**：2026-07-27
**基线**：当前分支 `4ac7f38` 及其 `origin/main` 祖先
**范围**：本轮只修订设计文档，不改业务代码、API、迁移或测试。

## 1. 审计口径与事实证据

事实来自当前 React 入口、FastAPI 路由、repository、测试、`scripts/local-smoke.ps1` 和既有隔离浏览器 harness。主 Wiki 与本地架构/发布文档只用于标记一致性，不用旧路线图推断已实现。状态定义：`正式可用` 有稳定入口和闭环；`隐藏可用` 只能从详情/Pilot 进入；`占位` 有入口但不承载正式数据；`未挂载` 有地基但无稳定入口；`遗留候选` 存在旧入口/死分支，待单独决定隐藏或移除。

## 2. 能力审计矩阵

| 能力 | 入口、恢复与数据/证据 | 写入、确认与外部动作 | 状态、代码/测试/真实证据 | Wiki/本地文档及分类 |
|---|---|---|---|---|
| 投递 | 左栏“投递”看板/列表/日历，详情可恢复；Application、JD 文本、Events、Resume/Material Kit | 手工 CRUD/状态变更；Chat 写入另有 pending confirmation；`AddApplicationForm` 只保存 `job_url`。当前 UI 的 `JDAnalyzeModal` 和 `ResumeMatchModal` 只传 `jd_text` | 正式可用但 URL 分支待收紧；`ApplicationDetail.tsx`、`AddApplicationForm.tsx`；`tests/test_applications_api.py`、`tests/test_events_api.py`、`tests/test_jd_resume_ai_api.py`、`scripts/local-smoke.ps1`；`/api/jd/analyze` 与 `/api/resumes/{id}/match` 仍能以 `jd_url` 进入 `httpx.get` | Wiki 的“不做招聘平台访问”与现状冲突，属于已存在 P0 安全缺陷；历史 `job_url` 仍是展示/审计数据 |
| 材料包 | 投递详情“材料包”，Pilot handoff 后打开 `MaterialKitDrawer`，冻结预填可恢复；Material Kit/JD/Resume/Evidence Bundle | Proposal 逐项人工接受才派生 Resume 和更新 Kit；不访问招聘平台、不自动投递 | 正式可用；`material_proposals.py`、`material_revision_proposals.py`、`MaterialKitDrawer.tsx`；`tests/test_material_revision_proposals_{ai,api,repository}.py`、`web/src/components/MaterialProposalReviewModal.test.tsx`、`scripts/local-smoke.ps1`，既有隔离 Provider 验收 | Wiki 列为 v0.2，代码提前可用；属文档滞后，不是新增路线图 |
| 机会评估 | 详情“岗位评估”或绑定 Application 的 Pilot；冻结 JD/Resume/用户断言，可恢复历史 Triage/Deep | 只写不可变 Review，Triage/Deep 需用户确认，材料交接仍手动；无 URL/平台访问/投递状态写入 | 正式可用但隐藏；`opportunity_fit_reviews.py`、`PilotOpportunityFitCard.tsx`、`AppShell.tsx`；`tests/test_opportunity_fit_reviews_{ai,api,repository}.py`、`web/src/features/pilot/{opportunityFitDraft,pilotOpportunityFitLifecycle,PilotOpportunityFitCard}.test.*`、`scripts/pilot-real-ai-browser-harness.ps1` | Wiki 的 Pilot/投递辅助方向一致；旧版本表滞后 |
| 面试复盘 | 顶层 `InterviewV01View` 仍为空状态；实际从投递详情 interview event 的 `ReviewManagementView` 进入；历史 Proposal 可读 | InterviewNote 手工保存；Review Proposal 需人工确认，逐条引用冻结复盘；来源变更保留历史只读 | 隐藏可用、顶层占位；`notes.py`、`interview_review_proposals.py`、`InterviewReviewProposalDrawer.tsx`、`ReviewManagementView.tsx`；`tests/test_interview_review_proposals_{ai,api,repository,migrations}.py`、`web/src/layout/AppShell.interviewReview.test.tsx`、Drawer interaction tests | Wiki 把正式笔记列为 v0.2；代码已有内部闭环，顶层入口是文档滞后/产品入口缺陷 |
| 面试知识沉淀 | 复盘 capture drawer/知识库入口；用户选择 note 原始片段，可重进未确认 Attempt | 可选 AI 预览；仅用户确认事务创建 Captured Source/Evidence/Note Version；不自动题库、Memory 或能力画像 | 隐藏可用；`interview_knowledge_capture.py`、`InterviewKnowledgeCaptureDrawer.tsx`；`tests/test_interview_knowledge_capture_{ai,api,fragments,repository,migrations}.py`、`web/src/components/InterviewKnowledgeCaptureDrawer.interaction.test.tsx`、`scripts/local-smoke.ps1` | 与 `knowledge-system.md` 的 Source→Evidence→Note Version 一致；主 Wiki 的自动沉淀仍是后置 |
| 面试准备 | 详情 interview event 的“面试准备建议”，Pilot 可引导；事件、冻结 JD、选定 Resume、显式 Knowledge Evidence | Proposal 只读/复制；用户断言留在快照但不发送 Provider；不写业务数据 | 隐藏可用；`interview_preparation_proposals.py`、`InterviewPreparationProposalDrawer.tsx`、`ApplicationDetail.tsx`；`tests/test_interview_preparation_{ai,api,repository,migrations}.py`、`tests/test_smoke.py`、Drawer interaction tests、`scripts/interview-preparation-real-ai-browser-harness.ps1` | Wiki 将面试前简报/复习列后置；当前是证据门控的提前实现，范围不扩张 |
| Knowledge | 左栏“知识库”，查看 Source/Evidence/Note Version；Imported/Captured Source 是事实底座 | 用户主动导入或明确确认写入；Captured interview source 只读；自动 Brief 按 ADR-0003 不进入 V1 | 正式可用（V1 基础）；`KnowledgeSourcesView.tsx`、`src/offerpilot/knowledge/*`；`tests/test_knowledge_sources_api.py`、`tests/test_knowledge_ki11_acceptance.py`、`tests/test_knowledge_kv1_01_no_auto_brief.py`、`tests/test_knowledge_kv1_03_acceptance_profile.py`、`scripts/local-smoke.ps1` | 与 `docs/architecture/knowledge-system.md`、ADR-0003/0004 一致；Wiki 自动总结/Memory 是后续 |
| Pilot | 左栏 Pilot、业务页右侧栏、Command Palette；按 Application/页面上下文恢复会话 | Conversation、附件、pending tool action；读工具可执行，写工具应逐次确认 | 正式可用；`AppShell.tsx`、`features/pilot/*`、`ai/agent.py`、`repositories/chat.py`；`tests/test_ai_agent.py`、`tests/test_ai_tools.py`、`tests/test_chat_api.py`、`tests/test_chat_repository.py`、`scripts/pilot-real-ai-browser-harness.ps1` | 与 Wiki 双形态 Pilot/审批卡一致；`auto_approve` 是真实缺陷 |
| Offer | 投递模块 Offer 中心、详情/Command Palette；Offer 手工字段可恢复 | 仅手工保存；现有金额、周期、签字费、权益、截止期，无 AI 自动写入 | 正式可用但比较未收敛；`OfferCenterView.tsx`、`AddOfferForm.tsx`、`OfferCompareDrawer.tsx`、`repositories/offers.py`、`/api/offers/compare`；`tests/test_offers_api.py`、`scripts/local-smoke.ps1` | Wiki Offer 中心 v0.2 一致；现有比较允许少于两个且无口径校验（缺陷） |
| 谈薪 | 未发现稳定的独立入口、服务或测试；Offer `notes/assessment` 不是谈薪工作流 | 不产生 AI 谈判结论、不外联 | 未挂载；仅有 `Offer` 字段和旧规划证据 | Wiki/子 PRD 放 v0.3；本轮明确延后 |
| Dashboard/Reminders | Dashboard、Reminders、Command Palette；从 Application/Event/Offer/Material Kit/题库统计派生，点击后可恢复业务入口 | 当前不直接写域数据 | 正式可用但规则分散；`DashboardView.tsx`、`RemindersView.tsx`、`CommandPalette.tsx`、`lib/actionItems.ts`、`lib/missionControl.ts`、`lib/pipelineInsights.ts`；`web/src/features/dashboard/DashboardView.test.ts`、`web/src/lib/{actionItems,missionControl,pipelineInsights}.test.ts`、`web/src/layout/CommandPalette.test.ts`、`scripts/local-smoke.ps1` | Wiki 要求今日行动/提醒；代码已实现，统一事实源是 P1 |
| Chat Agent | Pilot 三栏/右侧栏；会话、附件、工具调用和 pending confirmation 可恢复 | Agent 可读，写动作经过 pending/confirm；当前配置 `chat_auto_approve_writes` 仍可传入 Agent | 正式可用但写权限未收敛；`ai/agent.py`、`repositories/chat.py`、Chat routes；`tests/test_ai_agent.py`、`tests/test_ai_tools.py`、`tests/test_chat_api.py`、`tests/test_chat_repository.py`、`web/src/services/chat.test.ts` | Wiki 工具/审批/恢复一致；自动确认与 P0 不替用户决定冲突 |

### 2.1 URL 分支的可达性结论

已核实的调用链是：前端 `JDAnalyzeModal.tsx → services/ai.ts → POST /api/jd/analyze`，前端实际只传 `jd_text`；`ResumeMatchModal.tsx → services/resumes.ts → POST /api/resumes/{id}/match`，也只传 `jd_text`。但服务端两路在缺少文本时读取 `jd_url`，进入 `api.py:_fetch_text_from_url` 的 `httpx.get`，所以直接 HTTP 调用仍可外联。目标契约固定为：服务端只接受非空 `jd_text`；带 `jd_url` 且文本为空返回稳定 `422 jd_text_required`，带 URL 但文本非空也返回稳定 `422 jd_url_not_supported`；两种路径均不得创建 AI 分析或发起外部请求。历史 `job_url` 只用于展示/审计。回归必须 mock/拦截 `httpx.get`，证明两路 API 的 URL 输入均零外联；移除 URL fallback 后再更新 API/服务层负向测试。

## 3. P0 收敛设计

### 3.1 不替用户决定：按入口迁移旧契约

| 现有语义 | 新展示/输出语义 | 动作边界 |
|---|---|---|
| Opportunity Fit `recommendation=advance` | “证据支持继续核对/准备”的条件摘要，附证据、风险、问题和可选下一步 | 新生成不得把它显示为模型裁决；历史快照保留原枚举并标记历史，只读，不触发状态变更 |
| Opportunity Fit `hold` | “当前信息不足，需先确认”及具体问题 | 不自动暂停 Application，也不创建提醒 |
| Opportunity Fit `decline` | “存在待核对的阻塞条件”及证据/风险 | 不自动拒绝、放弃或关闭投递 |
| Chat “是否值得接受”/“哪个 Offer 更值得接受” | 改为逐字段事实、口径差异、风险和待确认问题 | 不输出最优/接受裁决，不调用状态写工具 |
| Dashboard/行动项的“接受/拒绝/放弃”措辞 | “打开记录”“核对口径”“补充信息”“由用户决定” | 点击只打开原生表单或查看页，任何写入仍需用户确认 |
| Material Proposal 的“接受/拒绝变更”按钮 | 保留 | 这是用户逐项确认 AI 改写，不是机会判断或 Offer/Application 决策，不应删除 |

新生成的 Opportunity Fit 不再写入旧 `recommendation` 字段，也不接受 `advance/hold/decline` 作为新模型输出。兼容读取旧快照时只读映射；新响应、Chat 提示词、Proposal schema、后端校验、API 类型和前端类型必须同批迁移。

### 3.2 Opportunity Fit 新生成严格契约

同一严格契约分别用于两个阶段；顶层必须额外包含 `stage`，且只能是 `triage` 或 `deep_review`，不得用一个无阶段的 Proposal 替代现有流程。

新 Proposal 顶层只允许 `schema_version: 2`、`source`、`summary`、`conditions`、`risks`、`questions`、`next_steps`；禁止 `recommendation`、score、probability、accept/decline 等裁决字段。`source` 必须是精确对象 `{"kind":"opportunity_fit","contract_version":"opportunity_fit.v2","snapshot_version":"1"}`；`summary` 必须是 `{text:string,rationale:string,evidence_refs:EvidenceRef[]}`，文本和理由各最多 1000 字符，正常摘要至少一条引用，只有固定安全空摘要允许零引用。`conditions`、`risks`、`next_steps` 最多各 8 条，`questions` 最多 6 条；前三类每项只能有 `{id,text,rationale,evidence_refs}`，问题只能有 `{question_id,text,evidence_refs}`，所有字符串非空且最多 1000 字符，`evidence_refs` 最多 4 条，所有条目的 `id/question_id` 在其版本规则下唯一。

EvidenceRef 只能是 `{source,path,excerpt}`：`source` 仅为 `jd`、`resume` 或 `user_assertion`；JD 路径只能指向冻结 `/jd_text`，Resume 路径必须是冻结 `content_json` 的规范 JSON Pointer 字符串叶子节点，用户断言路径只能是 `/user_assertions/<index>/text`；`excerpt` 必须是对应冻结字符串的非空、逐字连续子串。`conditions` 和 `risks` 必须至少一条真实引用；`next_steps` 只要是具体动作也必须引用。无上下文前提的问题只能使用服务端版本化 allowlist：`opportunity_fit.question.v1.jd_success_criteria`（“请确认该岗位最重要的成功标准是什么？”）、`opportunity_fit.question.v1.team_expectations`（“请确认该岗位团队希望优先解决的问题是什么？”）、`opportunity_fit.question.v1.interview_process`（“请确认面试流程、参与者和后续安排是什么？”）、`opportunity_fit.question.v1.missing_candidate_detail`（“请补充当前资料中尚未说明、但你希望核对的信息。”）；服务端按 `question_id` 派生固定文本。其他问题必须至少一条逐字引用，不能把“面试官/岗位一定会”藏进无引用问题。没有可验证依据时只返回固定中文安全空结构“当前资料不足，无法给出可验证的继续建议”，不打分、不预测。

**持久化兼容方案固定如下，并保留 Triage → Deep Review 两阶段。** 当前 `opportunity_fit_reviews` 表没有独立 `recommendation` 列，旧值位于 `triage_json` 内；旧表只增量加入 `proposal_schema_version INTEGER NOT NULL DEFAULT 1`，不改写、不重算或删除现有 `triage_json`、`triage_sha256`、`deep_review_json`、`deep_review_sha256`。现有行固定为 v1，旧双阶段照原样读取，`recommendation=advance|hold|decline` 只能作为历史事实展示。

v2 明确使用独立的 `opportunity_fit_review_sessions` 作为评估会话根记录，不复用旧 `opportunity_fit_reviews` 行。根表的 `id` 即稳定 `review_id`，并保存 `application_id`、唯一的 `triage_idempotency_key`、`proposal_schema_version=2`、`created_at` 和会话状态；新增 `opportunity_fit_review_stages` 每个阶段一行：`id`、必填 `review_id`、`application_id`、`resume_id`、可空 `parent_triage_stage_id`、`stage`（仅 `triage|deep_review`）、`proposal_schema_version=2`、`idempotency_key`、`source_snapshot_json`、`source_fingerprint_sha256`、`proposal_json`、`proposal_sha256`、`status`、`stage_generation`、`confirmation_token_hash`、`confirmation_expires_at`、`confirmed_at`、`created_at`。Triage 首次请求未命中同投递、同阶段、同 key 时，在一个 SQLite 短事务内原子创建根记录与首个 Triage stage；重试命中同 key 且快照相同则返回原 `review_id`/stage，不创建第二个 root 或 stage。数据库同时强制 `UNIQUE(application_id, triage_idempotency_key)` 与 `UNIQUE(application_id, stage, idempotency_key)`；并发冲突事务必须回滚孤儿 root，再读取获胜事务的 root/stage。不得出现无 stage 的孤儿 root。Deep 还须绑定同一 `review_id` 的 Triage stage。旧 v1 表及其双阶段列保持原值和原删除语义；v2 不回填旧表。

每个 v2 stage 都以 `review_id` 归属根会话。Deep 请求必须同时校验 `review_id`、`parent_triage_stage_id`、`application_id`：父 stage 必须属于该 root、是 Triage、属于同一投递且已确认；任一跨投递或跨 review 组合都稳定拒绝。列表和详情按 root 聚合为一条会话：一个 Triage、零到多个按创建时间排序的 Deep 重生成 stage；所有 stage 均为不可变、可审计历史，旧 v1 记录作为另一种只读联合类型返回。

Triage 生成创建不可变 `stage=triage` 行，状态为 `ready` 但未确认，`stage_generation=1`，并设置固定 `confirmation_expires_at`。token 使用现有服务端确认机制的 constant-time 比较，并采用服务端 secret 的 HMAC-SHA256 签名；签名载荷严格为 `review_id + triage_stage_id + stage_generation + expires_at`。token 明文只在 API 响应中短暂存在，数据库只保存 `confirmation_token_hash`，不保存 token 或 secret。独立确认接口先校验签名、绑定对象和过期时间，再以 CAS 同时匹配 `stage_id`、`stage_generation`、`status=ready`、`confirmed_at IS NULL` 和 token hash，将该 stage 转为 `confirmed`，只消费一次，不改 Proposal JSON 或哈希。过期返回稳定 `410 opportunity_fit_triage_confirmation_expired`；并发确认只有一个 CAS 成功，另一个返回已确认状态。未确认的同 key 重放可恢复同一 stage 与同一未过期 token，不重新生成 token；确认后只返回已确认状态，绝不重新签发可消费 token，并以稳定 `409 opportunity_fit_triage_confirmation_consumed` 表示重复消费。确认成功后首次 Deep 使用自己的 Deep `idempotency_key`，不重用或再次传递 Triage token；Deep 重生成必须由新的用户点击产生新的 Deep key 和新 stage。Provider 未知只重试同一个 Deep key，按同一 root/父 Triage 幂等返回或继续完成，绝不创建第二个 Deep stage。

Triage 的冻结输入是 Deep 的唯一来源：Deep snapshot 必须逐字段继承已确认 Triage 的 JD、Resume、用户断言及其哈希，并额外记录 `parent_triage_stage_id` 与 Triage fingerprint；服务端在调用 Provider 前重新计算并要求全部字段哈希相等，禁止把当前已变化的来源混入旧 Triage。确认后任一受控来源变化时，首次 Deep 和 Deep 重生成均返回稳定 `409 opportunity_fit_source_conflict`，不创建 Deep stage，用户必须新建 Triage/root。旧 stage 保留只读并标 `source_changed`；Application 软删除或不可见仍为 `404`。每个阶段同 key 且快照相同返回原行，同 key 快照不同返回 `409 opportunity_fit_idempotency_conflict`；Provider/网络未知保留对应 key。

v1 的 canonical JSON、指纹、阶段哈希和幂等读取全部保持数据库原值；v2 每个 stage 的 `proposal_json` 使用 UTF-8、紧凑分隔符、排序键、拒绝重复键/非有限数值的 canonical JSON，`proposal_sha256` 是该字节串的 SHA-256，`source_fingerprint_sha256` 只哈希该阶段冻结输入。API 与前端使用 `schema_version` 与 `stage` 判别联合：v1 响应保留现有 `recommendation`、`triage`、`deep_review` 字段并只读展示；v2 响应分别返回 Triage/Deep stage、各自的 `proposal`、`source_fingerprint`、`proposal_hash`、状态和时间字段，不返回 `recommendation`。`OpportunityFitReviewV1`、`OpportunityFitTriageV2`、`OpportunityFitDeepReviewV2` 是三套明确类型；新建、确认、Deep API、Chat prompt、后端 schema/校验和前端类型必须同批切换。严格解析拒绝额外字段、重复键、非有限数值、空白摘录、伪造路径、跨 Application 来源和超限数组；材料 Proposal 的“接受/拒绝变更”只表示用户确认某条 Resume 改写，继续保留并与机会判断模型语义隔离。
现有 Triage POST 路径接收 `schema_version=2` 后，在同一事务创建 v2 root 和 Triage stage，返回稳定 `review_id`、`stage_id` 和一次性 `triage_confirmation_token`；独立确认请求消费 token。确认成功后，Deep Review 路径必须接收 `schema_version=2`、`review_id`、已确认的 `parent_triage_stage_id`、同一 `application_id` 和 Deep 专用 `idempotency_key`，服务端先校验 root/父 stage/来源继承，再调用 Provider。列表/详情按 root 返回 v1 历史行与 v2 会话聚合；任何旧 v1 行都不能通过 v2 Deep 路径改写。

### 3.3 Agent 写入状态机、审计和配置迁移

“不落库”只指不写入 Application、Event、Resume、Offer、Material Kit、Knowledge、Question、Memory 等业务领域数据。控制面可以保存 pending action：当前已有 Conversation 的 pending 字段、确认 token、generation 和消息流，作用是恢复、一次性消费、过期和审计。批准、拒绝、取消、过期均追加不含模型原文/密钥的控制事件或消息，然后 CAS 清空 pending；拒绝/取消/过期绝不执行领域写入。网络/超时结果未知不清空 pending，显示“结果待确认”，继续使用原 token 重试。

`chat_auto_approve_writes` 的最终处理为固定 `false`：保留旧字段以兼容读取旧 `config.json`，加载时归一为 false；设置保存、备份导出和恢复导入均忽略 true 并写回 false，不能由备份重新启用。后续可删除 UI 字段，但不以删除字段破坏旧配置启动。所有写工具仍必须经过 pending→逐次确认→CAS；手工点击保存不经过 Agent gate。

### 3.4 顶层面试索引契约

索引仅返回当前可见 Application 的 interview events，以及其可见绑定 InterviewNote、历史 Proposal 摘要、已确认 Knowledge Note 摘要和可用准备入口。Application `deleted_at != null` 直接 `404`，不返回孤儿记录；Note/事件被删除或解绑时，不阻止历史 Proposal/Knowledge 的只读审计，但标记 `source_changed`，禁止新生成和 handoff。没有当前 Application 的 standalone note 不从投递面试索引出现，仍由既有 notes 列表按其可见性处理。

契约：服务端分页默认 `limit=50`、最大 `200`，按 `scheduled_at` 非空优先、再 `scheduled_at ASC`、`created_at DESC`、id DESC；返回 `items`、`next_cursor` 和每项 `application_id/event_id/note_id`。无记录显示固定中文空状态和“去投递创建面试事件”；跳转后资源变为不可见统一 `404`、清理当前 Pilot/Drawer，不产生 handoff。`ReviewManagementView` 先按 props、查询和 404/来源状态做复用评估；不能仅因文件存在就正式挂载。若不能复用，则保留投递详情入口并将顶层 Review 列为替换候选；MockStudio 没有稳定顶层闭环，保持隐藏/移除候选。

### 3.5 Offer 安全分两阶段

**P0 只做护栏**：现有 `GET /api/offers/compare` 已真实存在，但前后端都必须拒绝少于两个 Offer；隐藏当前 `OfferCenterView` 的单选比较和没有可靠口径的平均值、排名、最高聚合。旧字段原样展示，不猜币种、周期或总包，不做税后/权益价值/“最优 Offer”。错误用稳定 `422 offer_comparison_requires_two_offers` 或中性中文提示，不新增 API。

**P1 才做口径模型**：另行设计 nullable 的 currency、pay_period、amount_basis 等明确手工字段；旧数据不重算，缺字段进入 `not_comparable`。仅同币种、同周期、同口径且至少两条才允许用户请求逐字段比较；平均值/排名仍需用户明确启用且仅基于原始现金字段。P0 回归覆盖 0/1/2、API 绕过 UI、删除后比较；P1 再覆盖混币种/周期/口径、非负金额和迁移。

## 4. P1 收敛设计

### 4.1 行动提示单一事实源

现状不是只有 `derivePipelineInsights`/`deriveMissionControl`：`lib/actionItems.ts` 也独立定义了 Offer deadline 7 天、面试 72 小时、投递无更新 7/14 天、题目到期和材料包未完成规则；Command Palette 还有静态动作标签。默认阈值必须只放在规则模块的版本化常量中，Dashboard、Reminders、Command Palette 动态提示和 Pilot 只读卡统一消费 `ActionHint`。每条提示携带稳定 id、输入字段、阈值、原因、优先级、目标入口和确认要求；静态快捷命令明确标为命令，不冒充动态提醒。先锁定 `web/src/lib/{actionItems,missionControl,pipelineInsights}.test.ts`、`DashboardView.test.ts`、`CommandPalette.test.ts` 的边界行为，再接线。

### 4.2 五类证据门控 Proposal 矩阵

| 流程 | 输入/证据 | Provider/网络未知 | 模型契约失败 | 合法空/无证据 |
|---|---|---|---|---|
| 材料 | Application、Kit、Resume、可选 Evidence Bundle/断言 | `502`，保留 key/冻结输入，可用原 key 重试 | 保持既有 `502 material_proposal_unverifiable`，不写 Proposal，不把失败伪装为 safe_empty | 仅模型返回经过严格校验的合法 `changes=[]` 才是 `201 safe_empty`；输入无证据时固定“资料不足”，不调用 Provider |
| 机会评估 | 当前 Application、JD、用户选 Resume/断言 | 结果未知，保留 attempt key | 按现有领域错误码返回不可验证，不写 Review | 固定资料不足结果，不产生决策 |
| 面试复盘 | Note 与 interview event 元数据 | `502`，保留 key | 两次失败后按既有复盘错误语义落安全空 Proposal `201` | 空建议只能说暂无可验证建议 |
| 面试知识沉淀 | 用户选 note 原始片段 | 预览未知保留 Attempt；确认未知不得删除 | AI 预览失败不阻塞 direct 保存 | Direct 预览/空预览不写资产，未确认可删 |
| 面试准备 | interview event、冻结 JD、Resume、显式 Knowledge Evidence | `202/502` 保留 key、冻结输入，lease/CAS 防双调用 | 两次失败后安全空 Proposal `201` | 五数组为空；不能猜测面试官问题 |

四类终态必须分别测试和映射：Provider/网络/超时是结果未知；JSON/字段/证据/上限失败是契约失败；经过服务端校验的固定空结构是安全空结果；输入没有任何可用证据是无证据可用，返回中文资料不足而不调用 Provider。各流程保留自己的输入白名单、证据路径和错误码，只抽取最小 JSON/证据/CAS 底层，不做大一统重构。

### 4.3 URL 与系统文案

URL 安全已经属于 P0-A 的目标契约：`job_url/jd_url` 统一为用户记录的来源字符串；保存、展示、历史快照可保留，但不代表抓取、登录或访问招聘平台。实现顺序是先移除两个服务端 URL fallback，再让 `/api/jd/analyze` 和 `/api/resumes/{id}/match` 对空文本或任意 URL 稳定返回 `422`；用 mock/拦截 `httpx.get` 证明两路零外联。`tests/test_jd_resume_ai_api.py` 需覆盖空文本+URL、非空文本+URL、URL-only 两路以及无 Provider 调用；历史 `job_url` 数据不迁移、不删除。

中文扫描只断言版本化固定系统短语：标题、按钮、加载/空状态、状态、证据来源、无障碍标签和安全错误。禁止扫描任意英文；JD、公司/职位、Resume 标题、用户断言、AI 正文和证据摘录原样保留。

## 5. 明确不做与实施顺序

不做 Offer 决策助手、模拟面试、语音/录音转写、外部平台访问、自动投递、知识库自动扩张、长期 Memory/能力画像自动写入、提醒通知系统、税后或权益价值推断；谈薪和 Mock 只记录为后续独立设计依赖。

顺序：P0-A URL 零外联、Agent 写权限和旧决策语义适配（回归 URL、Chat/工具/旧 Proposal 快照）；P0-B 顶层面试索引与可见性/404；P0-C Offer 两条护栏；P1-A 统一行动规则；P1-B 五类 Proposal 差异矩阵与最小公共校验；P1-C 固定中文扫描。每阶段风险和回归范围如上，最终还需 `uv run pytest`、`uv run ruff check .`、`uv run mypy src`、前端测试/构建、`scripts/local-smoke.ps1`、隔离 local/real-AI verify 和真实浏览器走查。真实走查只能使用临时数据目录，断言无跨领域写入、无招聘平台请求并清理残留。本轮不执行实现测试。

## 6. 一致性结论与复审项

代码已领先主 Wiki 的旧版本标记，尤其材料、机会评估、面试复盘/知识/准备；这是文档滞后，不是新增能力。Knowledge Source/Evidence/Note Version 与本地架构及 ADR-0003/0004 一致。待复审确认的最小决策是：URL 先按 P0 零外联收紧，Agent 控制面保留审计但业务零写入，旧决策枚举只读兼容，Offer 护栏先于口径模型，面试索引先复用可见性契约。通过后再写测试先行计划，当前不进入代码调整。
