# Harness 可靠性契约实施计划

日期：2026-08-17
分支：`feat/20260817-harness-reliability-contract`
基线：`origin/main@e30004d`

## 任务分解（TDD）

1. **漂移证明测试（先失败）**
   - `tests/test_recovery_policy.py`：
     - 契约 JSON 存在且每个错误码字段齐全、code 唯一、disposition 合法；
     - API 实际响应（受控 Provider 触发各错误码）的 `error_code`/`http_status` 与契约一致；
     - `retry_same_key` 类错误后同 key 重放：key、输入指纹不变；
     - `terminal_no_retry` 类错误后同 key 重放不再调用 Provider；
     - trace envelope 字段齐全且无原始敏感内容。
   - `web/src/lib/recoveryPolicy/recoveryPolicy.test.ts`：
     - 前端 resolver 覆盖契约中全部错误码；
     - `mock_interview_provider_error` → retry_same_key（当前落入猜测分支，先失败）；
     - `mock_interview_idempotency_conflict` → restart_new_attempt（当前提示「使用原 key 对账」，先失败）；
     - 未知错误码 fail-closed。
2. **契约与生成器**
   - `contracts/recovery-policy.v1.json`；
   - `scripts/generate_recovery_contract.py`：校验（缺字段/重复 code/未知 disposition 非零退出）+ 确定性生成；
   - 生成 `src/offerpilot/reliability/recovery_policy_generated.py` 与
     `web/src/lib/recoveryPolicy/generatedRecoveryPolicy.ts`；
   - 测试：生成器重跑零差异。
3. **Python 消费**
   - `src/offerpilot/reliability/policy.py`：查询助手 + `recovery_error_response`；
   - `api.py` mock-interview 端点改用契约助手（含补齐缺失 error_code 的对齐修正）；
4. **前端消费**
   - `web/src/lib/recoveryPolicy/recoveryPolicy.ts`：`resolveErrorRecovery`；
   - `InterviewStudio.tsx`：`errorDetails` 改为按 disposition 决定按钮/文案/key 生命周期；删除按 422/409 猜测的分支。
5. **Harness 消费**
   - `smoke.py`：real-AI 失败处理按 disposition（terminal→删除重建；retry_same_key→至多一次同 key 重放；其余→终止并报 disposition）；`_assert_mock_interview_attempt_restart_state` 改为契约驱动。
6. **Trace Envelope**
   - `src/offerpilot/reliability/trace.py`；api.py 在 question/feedback 操作处记录成功/修复/终态/未知四类结局。
7. **Feedback 证据 ID**
   - `ai/mock_interview.py::generate_feedback` 改 evidence_ids；更新受控 Provider 测试。
8. **门禁**
   - `uv run pytest`、`uv run ruff check .`、`uv run mypy src`、
     `cd web && npm test -- --run`、`npm run build`、
     `uv run oc smoke --static-dir web/dist`；
   - real-AI 有界验收一次；
   - 子代理 CR。

## 边界

允许新增/修改清单见任务卡；禁止数据库迁移、公开路径变化、无界重试、跨领域恢复语义、外部 Trace 服务、敏感原文、重写全部 Harness。
