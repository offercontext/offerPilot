# 证据门控的事件级面试准备建议实施计划

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. Keep implementation inline in the current worktree; perform an independent CR before delivery.

Goal: 在当前可见投递的已安排面试事件上，基于用户明确选择的简历、JD、Knowledge Evidence 和可选断言，生成可审阅、可追溯的不可变面试准备建议。

Architecture: 新增 Application-scoped interview_preparation_proposals 领域。服务端在短 SQLite 事务中验证资格、构建冻结快照并以 application_id、event_id、idempotency_key 原子创建 generating lease；事务关闭后调用 AI，再用 revision/token CAS 回写严格校验后的 Proposal 或安全空 Proposal。前端由 AppShell 按 applicationId、eventId 持有受控草稿，详情页和 Pilot 只打开同一个结构化 Drawer，不产生任何跨领域写入。

Tech Stack: Python、FastAPI、SQLAlchemy、SQLite、Pydantic、现有 ChatModel/response_format 能力、pytest、React、TypeScript、TanStack Query、Vitest、内置浏览器和隔离 real-AI harness。

Design source: docs/superpowers/specs/2026-07-24-evidence-gated-interview-preparation-design.md

---

## 执行约束

- 开发前确认 git status --short --branch，继续使用当前 worktree 和分支，不新建分支、不合并、不推送。
- 每项实现严格遵循“先写失败测试 → 运行确认失败 → 最小实现 → 定向测试 → 小步提交”。提交标题使用 type: AI English subject。
- 不修改既有 Interview Review Proposal 的 API/数据语义；新接口只增加本功能契约。
- 不增加模型工具调用，不读取旧 AI 建议、完整旧复盘、Memory、聊天历史、外部网页或招聘平台数据。
- 不创建或修改 Application、ApplicationEvent、Resume、Material Kit、Question、Knowledge、Memory、提醒或投递状态；Proposal 仅保存自身快照。Proposal 的 application_id、application_event_id 和 resume_id 都是不可变的普通整数标识，不建立会阻止删除或随删除级联的外键。
- lease 到期接管必须在同一个 BEGIN IMMEDIATE 短事务内完成：条件匹配、attempt_status=generating、generation_revision 加一、新随机 token、新 lease 一次提交；两个到期并发请求只允许一个接管，另一个返回 202。
- 设计第 6.1 节的最终“无漂移写入 ready”步骤已顺延为第 8 步；实现和测试统一使用 attempt_status，不引入另一个 status 字段。
- 未知结果保留客户端 key 和服务端 lease；只有稳定确定失败、来源冲突或用户明确点击“重新生成”才结束当前尝试。

## 文件地图

| 文件 | 责任 |
| --- | --- |
| src/offerpilot/models.py | 新增 Proposal attempt 字段和约束 |
| src/offerpilot/db.py | 新库建表、旧库兼容、0012_interview_preparation_proposals 迁移记录 |
| src/offerpilot/ai/interview_preparation_proposals.py | 快照输入、JSON Schema、严格验证、一次修复、安全空 Proposal、诊断 |
| src/offerpilot/repositories/interview_preparation_proposals.py | 资格读取、幂等行、lease、CAS、来源漂移、历史读取 |
| src/offerpilot/schemas.py | 新接口的请求、201/200、202 和历史响应模型 |
| src/offerpilot/api.py | Application-scoped 列表、详情、生成路由和安全错误映射 |
| tests/test_interview_preparation_migrations.py | 新库/旧库迁移和表约束 |
| tests/test_interview_preparation_ai.py | 严格 JSON、Evidence 引用、Provider 能力与安全空结果 |
| tests/test_interview_preparation_repository.py | 快照、幂等、双连接 lease/CAS、来源漂移和历史 |
| tests/test_interview_preparation_api.py | HTTP 契约、稳定错误码、202/201/200 分支 |
| web/src/types/interviewPreparationProposal.ts | 前端输入、Proposal、来源和 attempt 类型 |
| web/src/services/interviewPreparationProposals.ts | API 请求和安全中文错误映射 |
| web/src/components/InterviewPreparationProposalDrawer.tsx | 选择输入、确认、历史、证据展示和未知结果重试 |
| web/src/components/InterviewPreparationProposalDrawer.test.tsx | 固定中文文案和结构契约 |
| web/src/components/InterviewPreparationProposalDrawer.interaction.test.tsx | 选择、确认、重试、来源变化和错误交互 |
| web/src/layout/AppShell.tsx | 按 applicationId、eventId 持有完整草稿和 key |
| web/src/components/ApplicationDetail.tsx | 面试事件入口和 Drawer 挂载 |
| web/src/features/pilot/PilotOpportunityFitCard.tsx | Pilot Application-context 入口，不复制表单 |
| src/offerpilot/smoke.py / tests/test_smoke.py | HTTP real-AI 合成数据、证据校验、清理和泄漏断言 |
| scripts/interview-preparation-real-ai-browser-harness.ps1 | 临时数据目录、端口/进程归属、浏览器闭环和失败传播 |

---

### Task 1: 新增 Proposal 模型与 0012 增量迁移

Files:
- Modify: src/offerpilot/models.py
- Modify: src/offerpilot/db.py
- Create: tests/test_interview_preparation_migrations.py

- [ ] Step 1: 写迁移失败测试

覆盖全新数据库和旧库升级，断言存在 interview_preparation_proposals、迁移记录 0012_interview_preparation_proposals、唯一键 application_id/event_id/idempotency_key，以及 attempt_status、generation_revision、token、lease、invalidation_reason 和快照/hash 字段。用 PRAGMA foreign_key_list 断言 application_event_id、resume_id 没有外键；删除事件或 Resume 后 Proposal 行仍存在，历史读取依靠冻结快照而不是当前外键对象。

测试名称：

    test_fresh_database_creates_interview_preparation_schema_and_0012
    test_existing_database_upgrade_is_idempotent_and_preserves_data
    test_attempt_constraints_match_status_and_idempotency_contract

- [ ] Step 2: 运行迁移 RED 测试

运行：uv run pytest tests/test_interview_preparation_migrations.py -q

预期：失败，因为模型和 0012 迁移尚未实现。

- [ ] Step 3: 实现模型和迁移

新增 InterviewPreparationProposal，至少包含 application_id、application_event_id、resume_id、idempotency_key、attempt_status、proposal_status、generation_revision、provider_call_token、provider_lease_until、invalidation_reason、input_snapshot_json、source_fingerprint、proposal_json、proposal_hash、created_at。

application_id、application_event_id 和 resume_id 使用不可变普通整数列，不建立 Application/Event/Resume 外键；创建与历史读取通过显式 Application 可见性查询和快照中的 ID 完成。这样物理删除事件或 Resume 不会阻止删除，也不会级联删除 Proposal；软删除 Application 仍由 API/Repository 显式返回 404。db.py 先 Base.metadata.create_all(engine)，再兼容旧结构，最后创建本功能索引/唯一约束并记录唯一版本 0012_interview_preparation_proposals，不得复用 0010 或 0011。

- [ ] Step 4: 运行 GREEN 测试并提交

运行：uv run pytest tests/test_interview_preparation_migrations.py -q

预期：全部通过。

提交：
    git add src/offerpilot/models.py src/offerpilot/db.py tests/test_interview_preparation_migrations.py
    git commit -m "feat: AI add interview preparation proposal schema"

### Task 2: 实现严格 AI 契约和安全空 Proposal

Files:
- Create: src/offerpilot/ai/interview_preparation_proposals.py
- Create: tests/test_interview_preparation_ai.py
- Reference: src/offerpilot/ai/interview_review_proposals.py、src/offerpilot/ai/workflows.py、src/offerpilot/repositories/json_contract.py

- [ ] Step 1: 写 AI 契约 RED 测试

固定五个顶层数组 preparation_directions、story_prompts、review_points、interviewer_questions、items_to_clarify。五类各最多 8 项；每项只含 id/text/evidence_refs；Evidence ref 为 1–5 个对象，严格校验 JD /jd/text、Resume 字符串叶子 JSON Pointer 和本次选择的 Knowledge Evidence canonical ID。

测试名称：

    test_rejects_non_leaf_resume_pointer_and_unicode_rewritten_excerpt
    test_rejects_unselected_knowledge_evidence_and_empty_refs
    test_rejects_fenced_json_non_finite_duplicate_keys_and_extra_fields
    test_contract_failure_repairs_once_with_machine_category
    test_provider_failure_is_called_once_and_not_repaired
    test_two_contract_failures_return_validated_safe_empty
    test_only_explicit_true_capability_receives_response_format
    test_user_assertions_are_saved_in_snapshot_but_absent_from_provider_payload

- [ ] Step 2: 运行 AI RED 测试

运行：uv run pytest tests/test_interview_preparation_ai.py -q

预期：失败，因为模块、Schema 和 validator 不存在。

- [ ] Step 3: 实现快照 validator、Schema 和一次修复

实现 build_interview_preparation_snapshot、validate_interview_preparation 和 generate_interview_preparation_proposal。snapshot 只含事件最小字段、原始 JD、冻结 Resume、显式 Evidence 和断言；Provider payload 只含 JD、所选 Resume 和 Knowledge Evidence，绝不含 user_assertions，断言也不能成为 Evidence ref。解析阶段拒绝 fenced JSON、NaN/Infinity、重复键和额外字段；首次契约失败只重试一次并携带 invalid_json、unexpected_field、missing_evidence_ref 等机器类别。Provider 异常不重试；两次契约失败后严格生成五个空数组的安全空 Proposal。supports_json_schema 只有真实布尔 True 才传原生 Schema。

- [ ] Step 4: 运行 GREEN 测试并提交

运行：uv run pytest tests/test_interview_preparation_ai.py tests/test_ai_client.py -q

预期：全部通过，且已有 AI client 测试不回归。

提交：
    git add src/offerpilot/ai/interview_preparation_proposals.py tests/test_interview_preparation_ai.py
    git commit -m "feat: AI enforce interview preparation evidence contract"

### Task 3: 实现两段式 Repository、首次 lease 和到期接管 CAS

Files:
- Create: src/offerpilot/repositories/interview_preparation_proposals.py
- Create: tests/test_interview_preparation_repository.py
- Reference: src/offerpilot/repositories/interview_review_proposals.py、src/offerpilot/models.py

- [ ] Step 1: 写 Repository RED 测试

使用同一 SQLite 文件的两个独立 SessionFactory/connection 和可控 Provider barrier，覆盖：

    test_first_request_without_old_row_creates_lease_before_provider_and_calls_once
    test_provider_unknown_cas_preserves_token_and_unexpired_lease
    test_same_key_before_unknown_lease_expiry_returns_202_without_second_provider
    test_expired_lease_takeover_atomically_bumps_revision_and_only_one_wins
    test_late_provider_writeback_with_old_revision_or_token_is_discarded
    test_ready_different_snapshot_returns_409_and_original_ready_remains_stable
    test_unfinished_different_snapshot_invalidates_with_reason_and_blocks_old_token
    test_resume_event_knowledge_drift_invalidates_before_ready
    test_event_delete_keeps_history_readable_and_source_changed
    test_resume_delete_keeps_history_readable_and_source_changed
    test_knowledge_change_keeps_history_readable_and_source_changed
    test_soft_deleted_application_returns_not_found_for_history

到期接管测试必须让两个独立连接同时接管已过期 lease：只有一个写入 attempt_status=generating、revision 加一、新 token、新 lease；另一个读取新 lease 返回 202，Provider 调用次数为 1，不能用单线程顺序调用替代。

- [ ] Step 2: 运行 Repository RED 测试

运行：uv run pytest tests/test_interview_preparation_repository.py -q

预期：失败，因为 Repository 和 attempt 生命周期尚未实现。

- [ ] Step 3: 实现首次原子插入和短事务快照

首次请求在 BEGIN IMMEDIATE 中完成 Application/event/resume/Knowledge 可见性检查、snapshot/fingerprint 计算、同 key 查询；无旧行时同一事务插入 generating、revision=1、随机 token 和未过期 lease，提交并关闭 session 后才调用 Provider。已有 ready 原快照直接返回，不能解析 Provider 配置；ready 遇不同快照只返回 409 且不改变原行。

- [ ] Step 4: 实现 provider_unknown、到期接管和回写 CAS

未知异常用新短事务按旧 revision/token CAS 为 provider_unknown，保留 token 和 lease；lease 未过期的同 key 重试只返回 202。到期接管必须在同一 BEGIN IMMEDIATE UPDATE 中匹配旧 lease，原子设置 attempt_status=generating、revision 加一、换新 token、新 lease。回写按 revision/token/状态 CAS，迟到结果丢弃。来源冲突只使未完成行 invalidated 并写 source_conflict；幂等冲突写 idempotency_conflict；invalidated 重放统一返回 interview_preparation_attempt_invalidated。

- [ ] Step 5: 运行 GREEN 测试并提交

运行：uv run pytest tests/test_interview_preparation_repository.py -q

预期：全部通过，特别是无旧行双连接首请求和到期双连接接管测试。

提交：
    git add src/offerpilot/repositories/interview_preparation_proposals.py tests/test_interview_preparation_repository.py
    git commit -m "feat: AI add interview preparation lease lifecycle"

### Task 4: 增加 schema、API 路由和安全错误映射

Files:
- Modify: src/offerpilot/schemas.py
- Modify: src/offerpilot/api.py
- Create: tests/test_interview_preparation_api.py

- [ ] Step 1: 写 API RED 测试

覆盖缺 event/resume/JD、非 interview/跨投递/不可见 event、显式 Knowledge 选择、未知请求字段、ready 复用、202、来源漂移、历史读取和安全错误。

测试名称：

    test_missing_event_resume_or_jd_returns_422_without_provider_call
    test_unknown_request_fields_and_forged_source_fields_return_422
    test_create_returns_201_then_same_key_returns_200_without_provider_resolution
    test_same_key_unexpired_generating_or_provider_unknown_returns_202_schema
    test_event_resume_knowledge_drift_returns_409_and_no_ready_proposal
    test_history_is_readable_with_source_changed_and_soft_delete_is_404
    test_safe_error_mapping_never_exposes_exception_or_model_text

202 只允许 attempt_status、application_id、event_id、idempotency_key、generation_revision、retry_after_ms；201/200 才返回 Proposal。伪造 current_jd_hash 不改变服务端 jd=not_checked。

- [ ] Step 2: 运行 API RED 测试

运行：uv run pytest tests/test_interview_preparation_api.py -q

预期：失败，因为 schemas、routes 和映射尚未存在。

- [ ] Step 3: 实现 Application-scoped routes

新增：
    GET  /api/applications/{application_id}/interview-preparation-proposals
    GET  /api/applications/{application_id}/interview-preparation-proposals/{proposal_id}
    POST /api/applications/{application_id}/interview-preparation-proposals

请求只接受 event_id、resume_id、jd_text、knowledge_selections、user_assertions、idempotency_key，不接受 job_url、jd_url、source_fingerprint、snapshot、proposal。Provider 配置解析必须发生在同 key ready 命中之后。所有异常仅映射设计文档中的稳定 error_code 和中文 message，不透传 Python、Axios、Provider 或用户数据。

- [ ] Step 4: 运行 GREEN 测试并提交

运行：uv run pytest tests/test_interview_preparation_api.py tests/test_interview_review_proposals_api.py -q

预期：新 API 和既有 Interview Review API 均通过。

提交：
    git add src/offerpilot/schemas.py src/offerpilot/api.py tests/test_interview_preparation_api.py
    git commit -m "feat: AI expose interview preparation proposal API"

### Task 5: 前端类型、服务层和结构化 Proposal Drawer

Files:
- Create: web/src/types/interviewPreparationProposal.ts
- Create: web/src/services/interviewPreparationProposals.ts
- Create: web/src/components/InterviewPreparationProposalDrawer.tsx
- Create: web/src/components/InterviewPreparationProposalDrawer.test.tsx
- Create: web/src/components/InterviewPreparationProposalDrawer.interaction.test.tsx

- [ ] Step 1: 写前端 RED 测试

覆盖固定中文五区域、显式简历/JD/Knowledge 选择、确认弹窗、安全空状态、Evidence 展示、202/超时/网络未知重试、404/409/422/502 和未知错误映射。确认弹窗必须明确写“仅 JD、所选简历和已确认 Knowledge Evidence 会发送给 AI；用户断言仅保存于本次快照，不会发送给 AI，也不作为建议依据”。扫描只断言本功能已知固定英文短语不出现，不禁止英文 JD、公司名、简历、Evidence 摘录或 AI 正文。

测试名称：

    shows_the_five_chinese_preparation_sections_and_source_labels
    requires_explicit_resume_and_jd_confirmation_before_generation
    shows_safe_empty_state_without_rendering_model_failure_text
    maps_known_and_unknown_errors_to_safe_chinese_copy
    keeps_the_original_key_after_pending_or_unknown_result
    shows_every_item_evidence_path_and_excerpt
    renders_the_assertion_privacy_boundary_and_provider_payload_excludes_assertions

- [ ] Step 2: 运行前端 RED 测试

运行：cd web; npm.cmd test -- --run InterviewPreparationProposalDrawer

预期：失败，因为类型、service 和 Drawer 尚未存在。

- [ ] Step 3: 实现类型和 service

类型必须区分 201/200 的 InterviewPreparationProposalResponse 与 202 的 InterviewPreparationPendingResponse，并使用 attempt_status，不定义泛化 status。service 只按 error_code/HTTP 状态返回固定中文；无稳定错误码的 502 使用“AI 服务暂不可用，结果待确认，请使用原尝试重试”，未知错误使用统一兜底。

- [ ] Step 4: 实现 Drawer

Drawer 先展示事件最小上下文、简历选择、原始 JD 输入、显式 Knowledge Evidence 选择和独立用户断言；确认弹窗明确说明仅 JD、所选简历和已确认 Knowledge Evidence 发送给 AI，用户断言只进入本次服务端快照，不发送给 AI，也不作为建议依据。生成后按五个固定区域展示，紧邻显示“岗位描述/选定简历/已确认 Knowledge Evidence”标签、路径和逐字摘录；安全空 Proposal 只显示“暂无可验证的面试准备建议”。不提供接受、修改投递、创建题目、练习、提醒、Knowledge 或 Memory 的按钮。

- [ ] Step 5: 运行 GREEN 测试并提交

运行：cd web; npm.cmd test -- --run InterviewPreparationProposalDrawer

预期：全部通过。

提交：
    git add web/src/types/interviewPreparationProposal.ts web/src/services/interviewPreparationProposals.ts web/src/components/InterviewPreparationProposalDrawer.tsx web/src/components/InterviewPreparationProposalDrawer.test.tsx web/src/components/InterviewPreparationProposalDrawer.interaction.test.tsx
    git commit -m "feat: AI add interview preparation proposal drawer"

### Task 6: AppShell 持久草稿、投递详情入口和 Pilot 入口

Files:
- Modify: web/src/layout/AppShell.tsx
- Modify: web/src/components/ApplicationDetail.tsx
- Modify: web/src/features/pilot/PilotOpportunityFitCard.tsx
- Create/Modify: web/src/layout/AppShell.interviewPreparation.test.tsx
- Create/Modify: web/src/components/ApplicationDetail.interviewPreparation.test.tsx
- Create/Modify: web/src/features/pilot/PilotOpportunityFitCard.interviewPreparation.test.tsx

- [ ] Step 1: 写卸载和入口 RED 测试

使用真实 React 挂载/卸载而非源码字符串断言，覆盖：

    keeps_the_full_draft_and_original_key_after_drawer_unmount_and_remount
    does_not_create_a_new_key_after_pending_or_unknown_result
    clears_a_completed_attempt_only_when_user_explicitly_regenerates
    opens_the_same_native_drawer_from_interview_event_and_pilot_context
    pilot_does_not_call_proposal_apis_or_create_cross_domain_writes
    switching_application_or_event_does_not_reuse_another_context_draft

- [ ] Step 2: 运行前端 RED 测试

运行：cd web; npm.cmd test -- --run AppShell.interviewPreparation ApplicationDetail.interviewPreparation PilotOpportunityFitCard.interviewPreparation

预期：失败，因为 AppShell 尚未持有该功能的受控 draft。

- [ ] Step 3: 在 AppShell 增加按上下文键控的 draft reducer

draft 至少保存 resumeId、jdText、显式 Knowledge selections、user assertions、idempotencyKey、attemptStatus、resultUnknown、历史/当前 Proposal 和来源状态。key 为 applicationId:eventId；关闭 Drawer、详情切换或 Pilot 重挂载只改变视图，不删除 generating/provider_unknown 草稿。确定 404/422/来源冲突才清除不可继续的尝试；成功/安全空结果结束当前 key，用户点击“重新生成”才生成新 key。

- [ ] Step 4: 接入 ApplicationDetail 和 Pilot

ApplicationDetail 仅在当前 Application 的 interview 事件上显示入口，传递 Application context 给 Drawer。Pilot 仅调用 AppShell 的打开面试准备回调，不复制表单、不伪造消息、不直接调用新 API。应用切换时先隔离旧 draft，再打开新 applicationId:eventId draft。

- [ ] Step 5: 运行 GREEN 测试并提交

运行：cd web; npm.cmd test -- --run AppShell.interviewPreparation ApplicationDetail.interviewPreparation PilotOpportunityFitCard.interviewPreparation

预期：全部通过。

提交：
    git add web/src/layout/AppShell.tsx web/src/components/ApplicationDetail.tsx web/src/features/pilot/PilotOpportunityFitCard.tsx web/src/layout/AppShell.interviewPreparation.test.tsx web/src/components/ApplicationDetail.interviewPreparation.test.tsx web/src/features/pilot/PilotOpportunityFitCard.interviewPreparation.test.tsx
    git commit -m "feat: AI connect interview preparation to application context"

### Task 7: real-AI HTTP smoke、隔离浏览器 harness 和清理

Files:
- Modify: src/offerpilot/smoke.py
- Modify: tests/test_smoke.py
- Create: scripts/interview-preparation-real-ai-browser-harness.ps1

- [ ] Step 1: 写 smoke RED 测试

覆盖：

    test_real_ai_interview_preparation_smoke_requires_three_non_empty_cases
    test_smoke_payload_excludes_old_proposal_review_memory_and_external_urls
    test_browser_harness_uses_isolated_data_and_checks_native_exit_codes

HTTP smoke 创建临时 Application、一个 interview event、一个非空 Resume、一个已确认 Knowledge Evidence 和三组非空合成输入。每组 POST 必须是 201；至少一组返回至少一条有效 JD/Resume/Knowledge Evidence 引用，其余可为固定安全空 Proposal，但不得返回未处理异常或 502。响应不得包含 input_snapshot_json、完整 JD/Resume/Knowledge 内容、旧 AI 正文或 location。

- [ ] Step 2: 运行 smoke RED 测试

运行：uv run pytest tests/test_smoke.py -q -k interview_preparation

预期：失败，因为 smoke helper 和 harness 尚未增加。

- [ ] Step 3: 实现 HTTP smoke 和隔离数据清理

复用现有 session_factory_for_data_dir 和合成数据 cleanup 模式，清理必须只作用于临时目录；停服后删除 Proposal、合成 Knowledge、Event、Resume、Application，再断言临时库无残留，不能触碰 source data。网络断言只允许本地 /api、静态资源和已配置 AI Provider。

- [ ] Step 4: 实现浏览器 harness

脚本创建临时数据目录、仅复制现有 config.json，确认端口未被占用，启动 OFFERPILOT_DATA=<temp> 服务；启动后验证监听 PID 属于本次进程树，未通过则停止本次服务并禁止打开浏览器。浏览器从根页面进入投递详情，定位合成面试事件，打开“面试准备建议”，完成选择简历/JD/Knowledge、确认生成、查看 Proposal 和历史；断言没有自动写入任何跨领域对象。每个 uv/原生命令后检查 LASTEXITCODE 并 throw，外层 finally 停止精确进程树、删除合成记录和临时目录、恢复环境变量。

- [ ] Step 5: 运行 smoke GREEN 测试并提交

运行：uv run pytest tests/test_smoke.py -q -k interview_preparation

预期：全部通过。

提交：
    git add src/offerpilot/smoke.py tests/test_smoke.py scripts/interview-preparation-real-ai-browser-harness.ps1
    git commit -m "test: AI add interview preparation real AI smoke"

### Task 8: 全量回归、构建和独立 CR

Files:
- Review all changed implementation, frontend, smoke, script and test files.

- [ ] Step 1: 运行后端专项和静态检查

    uv run pytest tests/test_interview_preparation_migrations.py tests/test_interview_preparation_ai.py tests/test_interview_preparation_repository.py tests/test_interview_preparation_api.py -q
    uv run ruff check src tests
    uv run mypy src

预期：专项测试、ruff、mypy 均退出码 0；若环境基线失败，记录精确命令、失败项和是否与本功能无关，不得宣称全量通过。

- [ ] Step 2: 运行前端测试和构建

    Set-Location web
    npm.cmd test -- --run
    npm.cmd run build
    Set-Location ..

固定英文扫描只检查本功能已知 UI 短语，动态英文数据不作为失败。

- [ ] Step 3: 运行隔离 smoke 和 browser harness

    uv run oc smoke --static-dir web/dist
    uv run oc verify --profile local --static-dir web/dist
    uv run oc verify --profile real-ai --static-dir web/dist
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\interview-preparation-real-ai-browser-harness.ps1

预期：local 不调用真实 Provider；real-ai 至少三组非空输入中有一组有效证据建议，其余只能是安全空结果；浏览器完成生成、人工确认和历史查看；临时目录清理后无残留，正式数据目录无变化，无招聘平台请求。

- [ ] Step 4: 启动独立 CR

独立审查只检查代码和测试，不代替实现。CR 必须重点复现：无旧行双连接首请求、未过期 provider_unknown 只返回 202、到期双连接只有一个 CAS 接管、ready 稳定重放、来源漂移、伪造 Evidence、未知结果重试、无跨域写入和隔离清理。发现的问题在当前分支修复后重新运行对应回归。

- [ ] Step 5: 最终工作区和提交审计

    git diff --check origin/main..HEAD
    git status --short --branch
    git log --oneline origin/main..HEAD
    git diff --name-only origin/main..HEAD

预期：工作区干净，变更仅限本计划覆盖的实现/测试/脚本/文档；最终报告列出每个命令结果、真实 AI 是否返回安全空结果和任何剩余基线风险；不推送、不合并。
