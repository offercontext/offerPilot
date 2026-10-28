# 面试业务地图

[返回业务总图](README.md) · [模拟问答排查链](interview-mock.md)

核对基线：2026-10-28，`054bf311`。本页映射当前代码入口；相关 spec 保留各自历史状态，不单独证明功能已实现或验收。

## 1. 先区分用户说的“面试”

| 用户说法 | 对应对象 / 边界 | 首查位置 |
|---|---|---|
| 面试列表、待面试、已完成、找不到某场面试 | `ApplicationEvent(event_type=interview)` 的聚合展示 | [InterviewV01View](../../../web/src/components/InterviewV01View.tsx)、[interviews service](../../../web/src/services/interviews.ts)、[InterviewIndexRepository.list](../../../src/offerpilot/repositories/interview_index.py) |
| 面试准备、建议、资料不足 | 选定事件/JD/简历及可选资料后生成的准备 Proposal | 下文“准备建议” |
| 快速模拟、文字问答、下一题、重复追问、结束复盘 | `InterviewPracticeCase` 或事件绑定的 `MockInterviewAttempt` / `MockInterviewTurn` | [模拟问答排查链](interview-mock.md) |
| 录音、转写、连续语音、确认转写 | 语音采集/转写状态先于回答提交，最后汇入 Studio 问答链 | 下文“语音输入” |
| 真实面试复盘、原始记录 | `InterviewNote` | [ReviewFormDrawer](../../../web/src/components/ReviewFormDrawer.tsx)、[notes service](../../../web/src/services/notes.ts)、[NotesRepository](../../../src/offerpilot/repositories/notes.py) |
| AI 复盘建议、待确认结果 | `InterviewReviewProposal`，与原始复盘记录分开 | 下文“复盘建议与下一轮准备” |
| 练习重点、准备度、专项练习 | 复盘选择确认后的 readiness 信号，以及练习 plan | 下文“复盘建议与下一轮准备” |
| 知识沉淀、经历故事 | 知识捕获与故事版本各自的确认链 | 下文“相邻功能” |
| 刷题、背题、复习间隔 | Question 题库，不能按 Mock Interview Turn 调查 | [总图中的题库入口](README.md#2-按业务定位) |

页面从 [AppShell](../../../web/src/layout/AppShell.tsx) 装配。若问题是入口显示、切页或详情资料未加载，先查页面传参，再进入具体生成链。

## 2. 准备建议

| 层 | 位置与符号 | 本层需要确认的事实 |
|---|---|---|
| 页面入口 | [InterviewReadinessCenter](../../../web/src/features/interviewReadiness/InterviewReadinessCenter.tsx)、[InterviewPreparationProposalDrawer](../../../web/src/components/InterviewPreparationProposalDrawer.tsx) | 用户选择的事件、简历、JD 和可选资料，以及被禁用的真实原因 |
| 前端请求 | [interviewPreparationProposals.ts](../../../web/src/services/interviewPreparationProposals.ts) | 请求体与 `idempotency_key`，是否重用原尝试 |
| HTTP | [api.py](../../../src/offerpilot/api.py)：`create_interview_preparation_proposal`、`_interview_preparation_request_payload` | `POST /api/applications/{app_id}/interview-preparation-proposals`；参数解析、当前 JD 版本、前置校验发生在模型调用前 |
| 业务与恢复 | [InterviewPreparationProposalsRepository](../../../src/offerpilot/repositories/interview_preparation_proposals.py)：`preflight`、`create_generated`、`_handle_existing`、`_call_and_store` | 读取/恢复原尝试、冻结来源、生成权与来源有效性；不要把 pending 或未知当作安全创建新任务的信号 |
| 模型与证据 | [ai/interview_preparation_proposals.py](../../../src/offerpilot/ai/interview_preparation_proposals.py) | 只检查本功能构造的输入和证据，不能推定它等同于 Pilot 对话上下文 |
| 复盘重点选择 | [preparation_selection.py](../../../src/offerpilot/review_readiness/preparation_selection.py) | 请求是否显式选择了可用的 readiness feedback 版本 |

业务依据：[准备建议设计](../../superpowers/specs/2026-07-24-evidence-gated-interview-preparation-design.md)、[租约心跳设计](../../superpowers/specs/2026-08-04-interview-preparation-lease-heartbeat-design.md)、[复盘到下一轮准备设计](../../superpowers/specs/2026-08-30-review-to-readiness-feedback-loop-design.md)。早期设计中的字段和阶段以当前修订及代码/测试核对，不能直接照抄。

回归入口：[API](../../../tests/test_interview_preparation_api.py)、[Repository](../../../tests/test_interview_preparation_repository.py)、[AI 契约](../../../tests/test_interview_preparation_ai.py)、[前端交互](../../../web/src/components/InterviewPreparationProposalDrawer.interaction.test.tsx)。若是跨领域资料消费，继续查 [readiness 来源门控](../../../tests/test_review_to_readiness_source_gates.py)。

## 3. 模拟问答

两种入口共享 [InterviewStudio](../../../web/src/features/interviewStudio/InterviewStudio.tsx) 和 [MockInterviewExecutionService](../../../src/offerpilot/mock_interview_execution.py)，来源准入分别保留：

- 事件绑定：`application_id + event_id`，从选定事件进入；后端 `start_mock_interview_attempt`。
- 快速模拟：先创建冻结岗位/简历资料的 `InterviewPracticeCase`；后端 `start_quick_practice_attempt`。入口与 case 请求见 [InterviewPracticeHome](../../../web/src/components/InterviewPracticeHome.tsx)、[interviewPracticeCases service](../../../web/src/services/interviewPracticeCases.ts)、[case Repository](../../../src/offerpilot/repositories/interview_practice_cases.py)。

出题、回答、结束反馈、未知结果恢复以及数据表的详细映射在[模拟问答排查链](interview-mock.md)。不要因为 UI 共用 Studio，就把两类请求的归属 ID 混用。

## 4. 语音输入

排查顺序：麦克风/能力检查 → 采集/转写 → 用户核对文字 → Studio 提交回答 → 下一题。

| 症状 | 实现入口 | 测试入口 |
|---|---|---|
| 麦克风或语音能力不可用 | [voiceInterviewCapability](../../../web/src/features/mockInterviewVoice/voiceInterviewCapability.ts)、[voiceCaptureRuntime](../../../web/src/features/mockInterviewVoice/voiceCaptureRuntime.ts) | [能力测试](../../../web/src/features/mockInterviewVoice/voiceInterviewCapability.test.ts) |
| 离线模型加载或转写问题 | [offlineWhisperCoordinator](../../../web/src/features/mockInterviewVoice/offlineWhisperCoordinator.ts)、[offlineWhisperRuntime](../../../web/src/features/mockInterviewVoice/offlineWhisperRuntime.ts) | [离线协调测试](../../../web/src/features/mockInterviewVoice/offlineWhisperCoordinator.test.ts) |
| 连续语音停住、误提交、重复下一题 | [continuousVoiceSessionController](../../../web/src/features/mockInterviewVoice/continuousVoiceSessionController.ts)、[VoiceAnswerComposer](../../../web/src/features/mockInterviewVoice/VoiceAnswerComposer.tsx) → [Studio](../../../web/src/features/interviewStudio/InterviewStudio.tsx) | [连续状态测试](../../../web/src/features/mockInterviewVoice/continuousVoiceSessionController.test.ts)、[Studio 交互测试](../../../web/src/features/interviewStudio/InterviewStudio.test.tsx) |
| 语音表现反馈保存/展示 | [voiceCoaching service](../../../web/src/services/voiceCoaching.ts)、[voice_coaching Repository](../../../src/offerpilot/repositories/voice_coaching.py) | [语音反馈 API 测试](../../../tests/test_voice_coaching_api.py) |

语音反馈、回答保存与问题生成是不同操作；一处成功不能证明其他两处成功。归属、答案快照和重放查 `create_or_replay` / `create_or_replay_quick`，口头禅位置查 `_validate_fillers`，趋势口径查 `build_voice_coaching_trends` / `_filler_metric`；回归见[语音反馈 Repository 测试](../../../tests/test_voice_coaching_repository.py)。

## 5. 复盘建议与下一轮准备

真实复盘链：`InterviewNote → InterviewReviewProposal → 用户选择/确认重点 → readiness 信号 → 专项练习或下一轮准备`。每一步有独立入口；生成建议不等于确认跨领域写入。

| 功能 | 前端 → 后端 | 定位与验证重点 |
|---|---|---|
| 保存原始面试记录 | [ReviewFormDrawer](../../../web/src/components/ReviewFormDrawer.tsx) → [notes service](../../../web/src/services/notes.ts) → [notes Repository](../../../src/offerpilot/repositories/notes.py) | 事件归属与保存字段；[notes API 测试](../../../tests/test_notes_api.py) |
| 生成/读取复盘建议 | [InterviewReviewProposalDrawer](../../../web/src/components/InterviewReviewProposalDrawer.tsx) → [review service](../../../web/src/services/interviewReviewProposals.ts) → [api.py](../../../src/offerpilot/api.py) `create_interview_review_proposal` → [review Repository](../../../src/offerpilot/repositories/interview_review_proposals.py) `create_generated` → [review AI](../../../src/offerpilot/ai/interview_review_proposals.py) | `/api/notes/{note_id}/interview-review-proposals`；来源指纹、原幂等键、历史建议；[API 测试](../../../tests/test_interview_review_proposals_api.py) |
| 选择/确认/撤销练习重点 | [ReviewReadinessNextStep](../../../web/src/features/reviewReadiness/ReviewReadinessNextStep.tsx) → [api.py](../../../src/offerpilot/api.py) `propose_review_readiness_action`、`recover_review_readiness_action` → [review_readiness](../../../src/offerpilot/review_readiness/) / [product_actions](../../../src/offerpilot/product_actions/) | pending/confirm、原操作身份和撤销有效性；[readiness API 测试](../../../tests/test_review_readiness_api.py) |
| 创建与完成专项练习 | [AdaptiveInterviewPracticeWorkspace](../../../web/src/components/AdaptiveInterviewPracticeWorkspace.tsx) → [adaptive service](../../../web/src/services/adaptiveInterviewPractice.ts) → [api.py](../../../src/offerpilot/api.py) `start_adaptive_practice`、`complete_adaptive_practice` → [adaptive Repository](../../../src/offerpilot/repositories/adaptive_interview_practice.py) `start_v2`、`complete` | `/api/interview-practice/plans`；练习计划与模拟问答 Attempt 分开；[API 测试](../../../tests/test_adaptive_interview_practice_api.py) |
| 下一轮准备未带入重点 | 回到本页“准备建议”的 `preparation_selection.py` 与 Repository | 检查用户显式选择、版本与撤销状态；[来源门控测试](../../../tests/test_review_to_readiness_source_gates.py) |

业务依据：[复盘建议设计](../../superpowers/specs/2026-07-22-evidence-gated-interview-review-proposal-design.md)是早期设计快照；跨领域闭环继续参照[复盘到准备设计](../../superpowers/specs/2026-08-30-review-to-readiness-feedback-loop-design.md)和当前实现，不能沿用早期“尚无跨领域动作”的范围作为当前结论。

## 6. 相邻功能

| 功能 | 代码链 | 依据与测试 |
|---|---|---|
| 从复盘沉淀知识 | [InterviewKnowledgeCaptureDrawer](../../../web/src/components/InterviewKnowledgeCaptureDrawer.tsx) → [capture service](../../../web/src/services/interviewKnowledgeCapture.ts) → [api.py](../../../src/offerpilot/api.py) `create_interview_knowledge_preview` / `confirm_interview_knowledge_capture` → [capture Repository](../../../src/offerpilot/repositories/interview_knowledge_capture.py) / [capture AI](../../../src/offerpilot/ai/interview_knowledge_capture.py) | [Knowledge 架构](../knowledge-system.md)、[捕获 API 测试](../../../tests/test_interview_knowledge_capture_api.py) |
| 经历故事、版本、AI 草稿 | [InterviewStoryDrawer](../../../web/src/components/InterviewStoryDrawer.tsx) → [stories service](../../../web/src/services/interviewStories.ts) → [stories Repository](../../../src/offerpilot/repositories/interview_stories.py) / [stories AI](../../../src/offerpilot/ai/interview_stories.py) | [故事设计](../../superpowers/specs/2026-08-10-interview-story-library-design.md)、[stories API 测试](../../../tests/test_interview_stories_api.py) |

## 7. 就近规则与回归入口

下表沿用前文代码入口，进一步定位容易混淆的恢复与写入边界。

| 症状 | 关键符号 | 边界测试 |
|---|---|---|
| 准备建议重复调用、卡在未知、旧结果覆盖新结果 | `InterviewPreparationProposalsRepository._try_frozen_existing` / `_handle_existing` / `_call_and_store`，以及 Drawer 的 `generate` / `handleClose` | [租约、来源与重放](../../../tests/test_interview_preparation_repository.py)、[前端未知结果恢复](../../../web/src/components/InterviewPreparationProposalDrawer.interaction.test.tsx) |
| 笔记改过又改回、生成后无法保存复盘建议 | `NotesRepository.update` / `update_note_scoped`、`_revisioned_note_values`，`InterviewReviewProposalsRepository.create_generated` | [笔记修订与归属](../../../tests/test_notes_api.py)、[scoped 写入](../../../tests/tool_authority/test_scoped_writes.py)、[复盘来源二次检查](../../../tests/test_interview_review_proposals_repository.py) |
| 撤销重点失败、专项练习重复或完成冲突 | `ReadinessSignalRepository._retract_signal_authorized_in_session`，`AdaptivePracticeRepository.start_v2` / `complete` | [撤销授权与原子性](../../../tests/product_actions/test_compensation.py)、[计划指纹与版本](../../../tests/test_adaptive_interview_practice_repository.py) |
| 知识预览迟到、重复确认或证据不完整 | `InterviewKnowledgeCaptureRepository.claim_ai_preview` / `complete_ai_preview` / `confirm` | [预览生成权](../../../tests/test_interview_knowledge_capture_repository.py)、[确认与证据](../../../tests/test_interview_knowledge_capture_api.py) |
| 手动故事版本冲突、来源变后重放失败 | `InterviewStoriesRepository.create_manual_story` / `create_manual_version`：先核对原请求回执；新版本同时检查 current_version 和 story_revision，推进后使旧目标建议失效 | [故事 Repository](../../../tests/test_interview_stories_repository.py)、[跨领域确认](../../../tests/test_interview_story_product_actions.py) |
| 快速案例同 key 却无法恢复 | `InterviewPracticeCaseRepository.create_or_replay`：请求指纹含当前简历快照；同 key 下简历变化会冲突，案例本身不创建正式投递/日程 | [案例 Repository](../../../tests/test_interview_practice_cases.py)、[案例 API](../../../tests/test_interview_practice_case_api.py) |

知识成果的后续修订/归档/删除继续查[Knowledge 与 Memory 地图](knowledge-memory.md)；日程基础生命周期见[投递日程](applications.md)，跨功能 Pending/Product Action 的恢复见[Pilot 地图](pilot-runtime.md)。

## 8. 向排查 Agent 提供什么

最少给出：当前分支/提交、上表中的功能、用户动作、实际现象与预期。已有证据再补：事件或 case、attempt/turn/proposal ID、HTTP 状态与错误码、复现步骤。

Agent 输出应包含：走到的调用链、第一处异常证据、适用业务规则、尚未证实的假设、最小回归测试。先定位的请求不授权修改业务逻辑；不要为诊断自动触发真实模型、确认写入或重建未知结果任务。
