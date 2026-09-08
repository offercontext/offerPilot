# Harness P0 验证记录

日期：2026-09-08。基线：`4978f6f245bfdd7b396c3f8b221a4d698d29a218`。
范围：修改确认后的确定性回执；不涵盖 P1—P5。
结论：本期相关回归、静态检查、独立审查与有界真实模型确认验收完成；已发现的测试失败均已修正并复验。

## 原问题与迁移的失败证据

- 在原基线只加入修改后的确认测试，`test_edited_confirmation_projects_effective_call_but_preserves_proposal`
  失败：实际模型调用 2 次，预期仅提案调用 1 次。同步与流式新路径随后均通过。
- 新库迁移测试最初失败：`0031_edited_confirmation_receipt` marker 数量为 0。
  修复后空库、历史库、三处逐列中断及缺 marker 重启共 6 项通过；历史字段逐列逐行保持不变。

## 回归与静态检查

| 命令 / 范围 | 实际结果 |
|---|---|
| `pytest -q tests/pilot_runtime/test_confirmation.py tests/pilot_runtime/test_confirmation_cutover.py tests/test_write_operations.py` | 初轮 128 passed、1 failed；唯一失败为已废弃的模型编辑说明预算断言，改为验证确认后不进入上下文投影 |
| `pytest -q tests/agent_loop tests/test_write_operation_acceptance_matrix.py tests/test_create_offer_write_operation_migration.py tests/tool_pipeline/test_confirmation_claim_schema.py` | 初轮 157 passed、1 failed；历史测试直接调用 0029 helper，未按当前启动顺序补新列；修正测试准备后迁移文件 2 passed |
| `pytest -q tests/test_confirmation_receipt_integrity.py` | 17 passed；覆盖策略删改、未知版本、原操作/请求替换、重算非密钥摘要仍不能替换提交事实 |
| `pytest -q tests/pilot_runtime/test_receipt_delivery_recovery.py` | 2 passed；真实 spawn 进程恢复，重复及 sync/SSE 交叉重放，Provider 与 executor 调用均为 0 |
| 最新 cutover 与迁移增补 | cutover 38 项、新回执迁移 6 项通过；重启恢复两项再次通过。曾新增过严的测试断言只接受 5xx，已按原 409 冲突语义修正，仍断言失败响应不包含未保存回执 |
| `pytest -q tests/tool_authority/test_migration_0028.py tests/test_review_to_readiness_migration_0029.py` | 150 passed；0029 回滚测试准备也补齐当前增量列后，整组复跑通过 |
| `ruff check .` | 通过 |
| `mypy src` | 167 个源文件通过 |
| `npm test -- --maxWorkers=2 --minWorkers=1`（web） | 237 个文件、2246 项测试通过 |
| `npm run build`（web） | 通过；保留既有大 chunk 提示 |
| `oc verify --profile local --static-dir web/dist` | 通过；实际本地 HTTP 进程，含普通确认、Pending 清理、SPA 和业务 CRUD |

默认并发的前端初轮出现 worker 退出与 30 秒性能上限失败；降低 worker 数后的完整重跑
通过，性能用例耗时 20.401 秒。未改阈值或删除用例。

Windows 的依赖导入与集成测试较慢，后端长回归使用 WSL Python 3.12，依赖版本来自
同一 `uv.lock`，离线 wheel 的 SHA 与锁文件逐项核验；静态检查、前端和 HTTP 验收使用
本机环境。没有修改依赖版本。

## 真实模型：保留所有样例

固定同步、SSE 两个隔离虚构 Offer，总模型调用上限预设为 8。首轮各 2 次调用后只返回
文字确认，没有产生 Pending，不能作为 P0 确认验收成功。随后在原对话各补一次固定的
明确确认，两个样例均产生 `update_offer` Pending；所有前后结果均保留。

- 总模型调用 **6 / 8**；修改确认及重放阶段调用 **0**，被拦截的额外调用尝试 **0**。
- 实际保存签字费 8000、每周远程一天；原提案为 10000、每周两天。
- sync → SSE、SSE → sync 重放返回同一原操作结果；每个原操作只保存一对 Tool/Assistant 消息。
- [完整脱敏结果](real-ai-report.json) 的原始 `passed=false` 表示首轮未到 Pending；
  `fixed_follow_up.passed=true` 表示原对话补充明确确认后的实际 P0 验收通过。
- 可用 `uv run python scripts/edited-confirmation-real-ai-check.py --text-confirmation-follow-up`
  重复相同协议；每轮仍固定两个场景与最多 8 次调用，不自动挑选成功样本。

## 审查与限制

独立子代理检查了 Ledger 策略 HMAC、终态校验、回执恢复、SSE 交付和独立进程恢复测试，
当前未发现新的可稳定复现阻断问题；曾指出的 naive UTC 等价问题已修正。

本次不是发布交付：未执行整个仓库的 `pytest`、完整 `release-gate.sh`、Docker 和安装门禁，
不声明已满足全量发布门禁。未修改前端行为，沿用现有展示，P1 单独推进。
模型只用文字请求确认的入口行为仍存在，属于独立问题；P0 保证从合法编辑确认成功开始
不再调用模型，而不保证每次首次请求都能让模型产生系统 Pending。

破坏性数据变化：无。兼容限制：已绑定新策略的操作不能由不认识该策略的旧二进制接管；
回滚须保留读取与恢复能力，或采用前向修复。
该限制由部署流程保证；没有能拦截任意旧二进制的数据库启动锁，直接降级仍有重新进入旧续答逻辑的风险。
