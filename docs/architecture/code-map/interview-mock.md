# 模拟问答排查链

[返回面试地图](interview.md) · [返回业务总图](README.md)

核对基线：2026-10-28，`054bf311`。关键词：快速模拟、事件模拟、下一题、重复追问、回答提交、结束复盘、结果未知、同 key 恢复。

## 1. 入口与正常链路

| 上下文 | 前置对象 | HTTP 路径前缀 | 开始 handler（均在 api.py） |
|---|---|---|---|
| `application_event` | 选定投递/面试事件、简历、JD 版本 | `/api/applications/{application_id}/events/{event_id}/mock-interview/attempts` | `start_mock_interview_attempt` |
| `quick_practice` | 冻结岗位/简历资料的 `InterviewPracticeCase` | `/api/interview-practice-cases/{case_id}/mock-interview/attempts` | `start_quick_practice_attempt` |

正常链路：用户确认资料 → 创建/重放 Attempt 并生成首题 → 提交回答 → 回答保存成功 → 生成下一题 → 结束并生成反馈 → 用户另行选择确认保存模拟复盘草稿。

| 阶段 | 前端符号 | HTTP handler | 后端主要调用 |
|---|---|---|---|
| 开始与首题 | `startInterviewStudioAttempt` | `start_mock_interview_attempt` / `start_quick_practice_attempt` | `create_or_replay_start` / `create_or_replay_quick_start` → `MockInterviewExecutionService.question` |
| 提交回答 | Studio `submitAnswer` → `submitInterviewStudioAnswer` | `answer_mock_interview_turn` / `answer_quick_practice_turn`；后缀 `/{attempt_id}/turns` | `MockInterviewRepository.submit_answer` |
| 下一题 | Studio `generateNextQuestion` → `generateInterviewStudioQuestion` | `generate_mock_interview_question` / `generate_quick_practice_question`；后缀 `/{attempt_id}/turns/{turn_no}/question` | `claim_question` → 重放/202，或 `MockInterviewExecutionService.question` → `generate_question` → `complete_question` |
| 结束反馈 | Studio `finish` → `finishInterviewStudio` | `finish_mock_interview` / `finish_quick_practice`；后缀 `/{attempt_id}/finish` | `get_feedback` / `claim_feedback` → `MockInterviewExecutionService.feedback` → `generate_feedback` → `complete_feedback` |

代码位置：[Studio](../../../web/src/features/interviewStudio/InterviewStudio.tsx)、[前端 service](../../../web/src/services/mockInterviews.ts)、[HTTP handlers](../../../src/offerpilot/api.py)、[执行服务](../../../src/offerpilot/mock_interview_execution.py)、[Repository](../../../src/offerpilot/repositories/mock_interviews.py)、[AI 生成与校验](../../../src/offerpilot/ai/mock_interview.py)。

## 2. 层间边界

1. **页面状态**：[interviewStudioController.ts](../../../web/src/features/interviewStudio/interviewStudioController.ts) 的 `reduceStudioState` / `shouldGenerateNextQuestion` 决定是否进入下一题。回答未确认、结果未知或轮数达到上限时不能据“回答框有文字”推定可以出题。
2. **请求与归属**：service 根据 `context.kind` 选路径；handler 核对事件或 case。快速模拟和事件绑定复用执行服务，不共用归属 ID。
3. **生成权与重放**：Repository 的 `claim_question` / `claim_feedback` 在事务中读取状态、冻结本次对话、处理租约和生成代次。无 claim 不等于调用 Provider 失败；可能正在生成或应返回已保存结果。
4. **模型执行**：`MockInterviewExecutionService` 只消费已领取的 claim，负责 Provider → 校验结果 → 条件提交 → 脱敏 trace；它不替 handler 重新选择来源或创建新 claim。
5. **来源与语义校验**：`generate_question` / `generate_feedback` 用冻结输入和本次 evidence catalog 校验。结构合法不等于业务证据合法。
6. **落库与恢复**：`complete_question` / `complete_feedback` 检查代次、token、对话指纹与来源。Provider 返回成功仍可能被拒绝提交；前端恢复动作由[恢复策略](../../../contracts/recovery-policy.v1.json)决定。

## 3. 关键业务规则在代码的哪里

| 规则 / 容易误判的地方 | 权威实现与依据 |
|---|---|
| 新一轮用新 question key；结果未知恢复保留原操作 key | [Reducer](../../../web/src/features/interviewStudio/interviewStudioController.ts) 的 `question_succeeded` 清理 key，`result_unknown` 保留 key；[Studio](../../../web/src/features/interviewStudio/InterviewStudio.tsx) 的 `generateNextQuestion` 复用未完成 key；[追问设计](../../superpowers/specs/2026-08-16-mock-interview-follow-up-quality-design.md) |
| 来源指纹与对话指纹分别表示冻结资料、已提交问答 | [Repository](../../../src/offerpilot/repositories/mock_interviews.py) 的 `submit_answer`、`_transcript_fingerprint`、`_assert_attempt_sources`；回答增加不应被误判成来源改变 |
| 新 JD 不必使已有冻结 Attempt 中断；来源对象失效仍须检查 | 同文件 `_assert_attempt_sources` / `source_status`；[冻结来源测试](../../../tests/test_mock_execution_contracts.py) `test_new_jd_or_archived_case_keeps_existing_frozen_attempt`、`test_source_withdrawal_during_generation_fails_closed`。不要把所有 `source_changed` 展示都视为可写或不可写的统一结论 |
| 后续题必须引用最近已回答轮次，并拒绝规范化后的完全重复问题 | [AI 模块](../../../src/offerpilot/ai/mock_interview.py) `generate_question`、`_normalize_question_text`；分类为 `missing_latest_turn_evidence` / `duplicate_question`。这不是通用语义相似度检测，也不能证明追问质量全面合格 |
| 模型选择 evidence ID，服务端展开冻结引用 | 同文件 `_provider_evidence_catalog`、`_expand_evidence_ids`；不能接受模型自报的任意路径或事实 |
| 格式修复有上限，语义失败不能靠反复调用掩盖 | 同文件 `should_retry_mock_interview_format`；生成函数最多一次允许类别的格式修复，重复问题/伪造引用等进入失败路径 |
| 租约接管后，旧生成结果不得覆盖当前代次 | [Repository](../../../src/offerpilot/repositories/mock_interviews.py) `claim_question`、`complete_question`、`claim_feedback`、`complete_feedback`；检查 `generation_revision`、`provider_call_token`、`transcript_fingerprint` |
| 模拟反馈与正式面试笔记是不同产物 | [模拟草稿 Repository](../../../src/offerpilot/repositories/mock_interview_review_drafts.py)；[事件模拟设计](../../superpowers/specs/2026-07-28-event-bound-mock-interview-design.md)。不要把反馈生成成功当作已写入 `InterviewNote` |

## 4. 从症状走到第一处异常

| 症状 | 按顺序检查 | 有判别力的现有测试 |
|---|---|---|
| 提交回答后没有下一题 | Studio 是否收到回答成功 → `shouldGenerateNextQuestion` 的 phase/turnNo/resultUnknown → 是否发起 question 请求 → handler 返回重放、202 还是错误 → claim 与提交 | [Reducer 测试](../../../web/src/features/interviewStudio/interviewStudioController.test.ts)、[Studio 交互](../../../web/src/features/interviewStudio/InterviewStudio.test.tsx)、[Repository 测试](../../../tests/test_mock_interview_repository.py) `test_next_question_claim_and_completion_persist_a_second_turn` |
| 重复题目 / 没有围绕回答追问 | 响应是否是同一 turn 的合法重放 → key 是否属于新操作 → Provider 实际收到的已答轮次 → 最近回答 evidence ID → `duplicate_question` / `missing_latest_turn_evidence` | [AI 测试](../../../tests/test_mock_interview_ai.py) `test_follow_up_rejects_a_normalized_duplicate_question_without_repair`、`test_follow_up_requires_evidence_from_the_latest_answer_without_repair` |
| 202、一直生成中、刷新后未知 | 原 attempt/key 是否保留 → `provider_lease_until` / 代次 → 是否已有保存的 turn/proposal → [recoveryPolicy](../../../web/src/lib/recoveryPolicy/recoveryPolicy.ts) 的处置。不能用新 key 掩盖原操作未知结果 | [执行矩阵](../../../tests/test_mock_execution_contracts.py) `test_provider_unknown_never_retries_automatically_or_leaks_text`、[Studio 交互](../../../web/src/features/interviewStudio/InterviewStudio.test.tsx) |
| 明明模型成功但页面失败 | trace 的 Provider 结果与 response error code → 提交时代次/token/对话指纹 → 来源是否失效；两类成功不能混用 | [执行矩阵](../../../tests/test_mock_execution_contracts.py) `test_late_owner_cannot_commit_or_overwrite_current_owner`、`test_source_withdrawal_during_generation_fails_closed` |
| 重试重复调用 / 重复写入 | key 和请求输入是否一致 → 首次创建与重放分支 → claim 赢家 → 已有结果是否优先返回 | 同矩阵 `test_success_and_same_key_replay_share_one_provider_call`、`test_concurrent_same_key_has_one_claim_winner` |
| 结束后没有正式复盘记录 | finish 响应是否仅是 Feedback Proposal → 是否经过选择确认 → 是否创建 MockInterviewReviewDraft；正式 InterviewNote 属于另一条链 | [草稿测试](../../../tests/test_mock_interview_review_drafts.py)、[真实复盘地图](interview.md#5-复盘建议与下一轮准备) |

这些是排查顺序，不是根据症状直接判定的根因。先保留失败证据，再做会改变状态的复现。

## 5. 数据与观测

数据模型统一见 [models.py](../../../src/offerpilot/models.py)：

| 模型 / 表 | 排查时关注 |
|---|---|
| `InterviewPracticeCase` / `interview_practice_cases` | 快速模拟的冻结 JD/简历、case 归属 |
| `MockInterviewAttempt` / `mock_interview_attempts` | `context_kind`、事件或 case、`attempt_status`、`source_fingerprint`、`transcript_fingerprint`、生成代次、租约 |
| `MockInterviewTurn` / `mock_interview_turns` | `turn_no`、`turn_status`、问题/回答是否保存、question/turn 幂等身份 |
| `MockInterviewFeedbackProposal` / `mock_interview_feedback_proposals` | 反馈状态、来源和对话指纹、原反馈操作 |
| `MockInterviewReviewDraft` / `mock_interview_review_drafts` | 用户确认后的独立模拟草稿 |

[trace.py](../../../src/offerpilot/reliability/trace.py) 写入 `<data_dir>/logs/mock_interview_trace.jsonl`。优先用 `operation_id` 对齐响应与 trace，再结合 `attempt_id`、`generation_revision`、`validator_stage`、`provider_outcome`、`failure_category`、`response_error_code`、`final_disposition`。重放或准入前失败不一定产生新的 Provider trace，缺失不能直接证明代码未执行。

日志保留脱敏标识，不加入原始简历/JD/回答、提示词、模型原文、密钥或完整幂等键。数据库核对默认只读；不要为排查直接修改租约、状态或失败记录。

## 6. 定向验证入口

按实际改动选择测试，不要求每次全部运行。下面是命令示例，不是本地图宣称已通过的验收结果。

在仓库根目录：

```powershell
uv run pytest -q tests/test_mock_interview_ai.py
uv run pytest -q tests/test_mock_interview_repository.py tests/test_mock_interview_api.py tests/test_interview_practice_case_api.py
uv run pytest -q tests/test_mock_execution_contracts.py tests/test_recovery_policy.py
```

在 `web` 目录：

```powershell
npm test -- src/features/interviewStudio/interviewStudioController.test.ts src/features/interviewStudio/InterviewStudio.test.tsx
```

AI 语义问题查第一组；来源、幂等、归属查第二组；两入口共享执行或未知结果恢复查第三组；前端状态查前端测试。若修改写入、权限、并发或恢复，仍须按 [AGENTS §7](../../../AGENTS.md#7-验证与-code-review) 扩大失败/恢复回归和独立审查；静态注释调整不等于真实模型验收。
