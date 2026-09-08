# Harness 可靠性契约设计（Mock Interview 首期）

日期：2026-08-17
状态：已采纳（实现中）
关联调研：`docs/reports/2026-08-16-agent-harness-research.md`

## 问题

同一个 `error_code` 的恢复动作分散在四层，各自维护、各自解释：

1. API 层：`src/offerpilot/api.py` 每个 endpoint 手写 `error_response(status, message, code=...)`；
2. Repository 层：`mock_interviews.py` 的 Attempt 状态（`provider_unknown` / `contract_failed` / `source_conflict`）隐含恢复语义；
3. 前端：`InterviewStudio.tsx::errorDetails` 硬编码少量 code，其余按 HTTP 422/409 猜测；
4. Harness：`smoke.py` / PS 脚本 / browser harness 各自重写「provider 保留、contract 删除」的规则。

已观察到的漂移：

- 同样 `mark_provider_unknown`，快速练习返回 `mock_interview_question_result_unknown` / `mock_interview_feedback_result_unknown`，application 端点返回 `mock_interview_provider_error`；前端没有后者的映射，落到「网络待确认，保留原 key」的猜测分支。
- `mock_interview_idempotency_conflict`（409，key 已绑定其他输入）在前端被提示为「使用原 key 对账」——同 key 重放必然再次冲突。
- `mock_interview_transcript_conflict`、`mock_interview_answer_required` 响应没有 `error_code` 字段，前端只能按状态码猜。

## 方案

版本化 JSON 契约（`contracts/recovery-policy.v1.json`）+ 确定性生成器（`scripts/generate_recovery_contract.py`）产出 Python / TypeScript 适配层。四层只消费生成物，禁止再按 HTTP 状态码猜测。

### 契约字段

每个错误码固定：

- `error_code`：稳定 API 错误码；
- `http_status`：唯一 HTTP 状态码（同一 code 不允许多状态）；
- `disposition`：`retry_same_key | restart_new_attempt | reload_source | edit_input | terminal_no_retry`；
- `attempt_retention`：`retained_reconcile | retained_terminal | retained | absent`；
- `input_frozen`：失败后冻结输入是否保持有效；
- `preserve_idempotency_key`：恢复动作是否沿用原 key；
- `provider_retry_allowed`：是否允许（有界、lease 保护下的）Provider 重试；
- `user_action`：面向用户的动作键。

顶层附 `unknown_code_policy`：未知错误码 fail-closed（`terminal_no_retry`、禁止 Provider 重试）。

### 范围

- 首期只迁移 Mock Interview（含快速练习与 application event 两个 endpoint 家族）。
- 不改公开 API 路径；不放宽重试、证据校验；不引入数据库迁移。
- 少量对齐性修正（均为收敛到既事后端语义，不是新语义）：
  - `mock_interview_transcript_conflict`、`mock_interview_answer_required`、mock-interview 端点内 422 校验错误补齐 `error_code`；
  - 快速练习 question 写入 CAS 失败从 `mock_interview_context_mismatch`(409) 收敛为 `mock_interview_transcript_conflict`(409)；
  - 快速练习 start 的非 archived `ValueError` 从 `mock_interview_context_mismatch`(422) 收敛为 `mock_interview_invalid_payload`(422)。

### Trace Envelope（脱敏）

Mock Interview 每次受控/真实 Provider 操作记录一条结构化 JSONL
（`<data_dir>/logs/mock_interview_trace.jsonl`）：
`run_id, scenario_id, operation_id, attempt_id, generation_revision,
idempotency_key_hash, provider, model, capability_snapshot_hash,
input_fingerprint, schema_fingerprint, started_at, first_byte_ms,
completed_ms, provider_outcome, validator_stage, failure_category,
repair_count, final_disposition, response_error_code`。

禁止记录 JD/简历/回答原文、Prompt、模型原文、API key、完整幂等键、未脱敏请求响应体。
API 响应与错误 details 携带 `operation_id`，CDP 浏览器请求据此与 trace 关联。

### Feedback 证据 ID

Feedback 引用从「模型逐字复制 source/path/excerpt」迁移为与提问一致的
「模型选 `evidence_ids`，服务端展开为冻结引用」：
未知 ID、重复 ID、跨目录 ID 严格拒绝；持久化与公开契约仍是
`source/path/excerpt`；不放宽 turn 证据等既有语义校验。

## 验收

见 `docs/superpowers/plans/2026-08-17-harness-reliability-contract.md`。
