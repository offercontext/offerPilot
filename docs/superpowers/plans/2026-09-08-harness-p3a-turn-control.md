# Harness P3A：持久执行控制与停止实施计划

> 状态：已实现并完成定向验证，保留独立分支交付。基线为已复核的 P2 `9ef06a0b`；独立分支 `feat/20260908-durable-turn-control`。
> 按任务逐项执行，使用 TDD 与独立子代理审查；已有实施授权，不另设执行方式审批。

**Goal:** 停止精确指定的 Turn execution generation，并在持久提交边界阻止失去权限的执行者继续写入。

**Architecture:** SQLite 保存执行代次、有限租约及幂等停止命令。运行时传播不可由客户端伪造的执行身份；业务数据库在实际提交事务内同时检查 Turn 权限与既有 Ledger/delivery/domain 条件。Pending 暂停与批准续跑仍归属同一 Turn，停止不等于拒绝或 Undo。

**Tech Stack:** Python、SQLAlchemy/SQLite、FastAPI、现有 Pilot Runtime、React/TypeScript。

**依据：** 用户已授权的 `2026-09-08-harness-delivery-roadmap.md` §8（原路线图提交 `5b3bbb2`，原仓库文档工作树），[P2 ADR](../../architecture/decisions/0008-persist-pilot-turns-and-timeline.md) 与当前 Ledger/Runtime 提交边界。本期保留断连取消；后台执行、重连订阅属于 P3B。

## 1. 状态、身份和租约

新增 `pilot_executions`，每行对应 `(turn_id, execution_generation)`，保存 conversation、owner、状态及 UTC 到期/续租时刻。部分唯一约束保证同 Conversation 最多一个 running generation。

| 状态 | 事实与允许后续 |
| --- | --- |
| running | 已认领且未失去权限；可完成、暂停、失败、停止或变成待核对 |
| waiting_confirmation | 执行结束且当前 Pending 属于本 Turn；原 generation 无权限，批准可取得下一代次 |
| completed / failed / interrupted | 持久的本次执行结果；不重新认领原 generation |
| stopped | 已原子撤销受保护提交权限；已提交写入仍保留 |
| result_unknown | 租约过期、时钟回退或未能核实执行结果；不自动补跑 |

`PilotTurnRecord` 保留 P2 旧字段存储兼容；有 execution 的 Turn 读取和时间线使用最新执行事实。旧对话确认以明确 operation/message 归属补建 Turn，不按“最新任务”猜测。

租约为有限 UTC 时段，续租间隔小于租期；运行时另用单调时钟限制本地有效期。续租失败停止本地提交权限，旧 owner 过期后不能恢复原 generation；UTC 回退不延长已有权限。进程重启只读取/核对，不启动旧任务。

## 2. 模块与提交边界

| 文件 | 责任 |
| --- | --- |
| `src/offerpilot/models.py` | 执行和命令的新表、唯一约束、级联/最小墓碑 |
| `src/offerpilot/pilot_control.py` | 接纳/认领/续租/停止/读取、执行身份及事务围栏 |
| `src/offerpilot/pilot_runtime/turn_control.py` | 进程控制适配、有限心跳、本地权限、上下文传播 |
| `src/offerpilot/chat_transport.py` | 显式控制注入、sync/SSE worker 传播与清理 |
| `src/offerpilot/api.py` | 精确 Turn 控制 API、初始认领、确认控制、结果身份 |
| `src/offerpilot/pilot_runtime/continuation.py`、`deterministic.py` | 审批校验后、实际执行前认领下一代次；终态重放不认领 |
| `src/offerpilot/pilot_timeline.py`、`pilot_timeline_projection.py` | execution 元数据和状态展示，保持只读恢复 |
| `web/src/services/chat.ts`、`web/src/types/chat.ts` | 停止命令/执行读取协议 |
| `web/src/features/assistantSurface/`、`web/src/components/ChatPanel/` | 共享控制、跨页面读取、停止状态与旧代次保护 |

受保护提交包括业务 mutation、Pending 创建/替换、批准执行、最终消息和 Turn 终态。业务数据库提交钩子在同一 connection/transaction 中使用条件 UPDATE 取得写锁并检查执行 owner/generation/状态/租约；与 Ledger、delivery 和业务 revision 的既有 CAS 同时成立。事务外工具副作用单独分类，不把“已停止”解释为撤回外部操作或终止供应商计费。

停止命令与执行撤权原子提交；命令重放仅返回已记录效果。业务先持有提交权限则其结果保留，停止先撤权则旧事务不得提交。原确认 token 和 Pending CAS 不由停止命令消费。只读回执/Timeline 修复不授予 Worker 执行权限。

## 3. 分步实施与验证

### Task 1：仓库状态机与命令

- [x] 新建 `tests/test_pilot_control.py`，先运行失败用例：同会话并发认领唯一、停止重放、同命令不同内容冲突、旧 generation、不活跃 Pending、过期不能续租。
- [x] 实现新表及仓库公开契约，所有状态更新使用短事务：

```python
lease = controls.claim_start(turn_id)
result = controls.interrupt(command_id, turn_id, lease.generation)
assert result["status"] == "stopped"
assert controls.interrupt(command_id, turn_id, lease.generation) == result
assert not controls.renew(lease)
```

- [x] 重建仓库实例回读同一结果；删除 Conversation 清除执行内容，旧命令不得影响重用 ID 的新任务。
- [x] 运行 `pytest --noconftest tests/test_pilot_control.py -q`，确认通过。

### Task 2：实际提交围栏与租约

- [x] 用两个 connection 和屏障编写停止/提交交错测试，不用随机 sleep：停止先完成后旧业务 commit 必须回滚；已获写锁的业务先提交必须保留。
- [x] 安装仅针对受控执行的业务数据库 commit 围栏，并传播执行身份；直接 Connection 和 ORM Session 路径均覆盖。
- [x] 测试 owner/generation 伪造、迟到消息、Pending 创建、时间前跳/回退、续租失败、单调期限、异常清理。
- [x] 审查并验证所有受保护提交点；不纳入严格边界的外部副作用必须在状态/文案中明确为不可撤回或待核对。

### Task 3：初始任务与确认续跑

- [x] 在 `tests/test_pilot_control_api.py` 先写屏障模型：请求已启动时读取其身份，从另一客户端停止，释放模型后不得新增 assistant/Pending/业务写入。
- [x] sync/SSE 传入持久控制，worker 继承执行上下文；正常/失败/断连/超时路径停止心跳并只更新匹配 owner。
- [x] 初始任务认领与 P2 接纳衔接；当前 Conversation 已活跃时在用户消息提交前拒绝新任务。
- [x] 审批身份验证后认领下一代次；等待确认不持有活跃 lease。重复确认和终态重放不得新增 generation 或 Provider 调用。
- [x] 覆盖初始 → Pending → 批准 → 同 Turn 下一代次，以及停止旧代次、拒绝/批准竞争、已提交结果与 Undo 保留。

### Task 4：API 和双页面控制

- [x] 提供 `POST /api/chat/turns/{turn_id}/interrupt`，输入 `command_id` 和 `expected_generation`；提供当前对话执行事实读取。使用现有认证边界并核对 Turn/Conversation 归属。
- [x] 持久同命令重放不受其他窗口当前选择影响；JSON/SSE 身份包含 execution generation。
- [x] 前端先写服务与交互失败测试；两外壳共享原 Turn 控制，停止请求保留同一个 command ID 供未知结果重试。
- [x] UI 分别显示提交中、停止中、已停止、原代次已结束、代次已变化和待核对；迟到控制响应不能影响新选择或新执行。
- [x] 明确已提交修改仍保留；等待确认没有活跃执行者时不提供隐式拒绝。

### Task 5：集成审查与交付

- [x] 独立子代理审查提交围栏、确认/重放、租约、权限、前端所有权及持久化。根据真实失败修复并复测受影响范围。
- [x] 运行新增与受影响后端测试、ruff、mypy、前端测试及构建；内置浏览器使用临时数据和屏障模型验收停止/恢复。
- [x] 写 ADR 记录状态转换、保护范围和失效策略；计划回填实际结果和未运行的发布项。
- [x] `git diff --check`，检查文档链接，分开执行 `git add`、暂存检查和中文 conventional commit。保留独立分支，不将 P3B/P4/P5 混入本次提交。

## 4. 实际验证与交付边界（2026-09-09）

- 后端广泛回归：contracts、deterministic、event_sink、execution_host、persistence、provider_errors、receipt_delivery_recovery、start_turn、stream_preparation，共 **335 passed**。最终围栏与恢复修改后追加 `tests/test_pilot_control.py tests/test_pilot_control_api.py tests/test_pilot_timeline.py tests/pilot_runtime/test_confirmation_cutover.py tests/pilot_runtime/test_transport.py`，**118 passed**（634.24 秒）。使用 WSL Python 3.12 与隔离 venv，`PYTHONPATH=src LITELLM_LOCAL_MODEL_COST_MAP=True pytest --noconftest ... -q --tb=short`。
- 竞争测试覆盖停止先提交 / 业务先持锁、handler 入口前撤权、真实 ORM / Connection 提交、owner / generation / conversation 伪造、锁等待期间租约过期、观察过期后时钟回退、迟到心跳、终态超时整理与迟到标题；新增缺口先用失败测试复现。
- 前端最终相关六文件合计 **111 项**通过：chat 16、layout 55、usePilotExecution 8、PendingStartRecovery 6、AssistantSurfaceProvider 15、HaruChatWindow 11。最后修复后复跑后四个相关控制/布局文件为 89 passed，随后共享发送门禁断言复跑 Provider 为 15 passed。保留既有 jsdom 网络和 act 警告。
- `ruff check` 检查全部 16 个新增/修改 Python 文件通过；`mypy --follow-imports=silent` 检查 13 个修改的生产文件通过。默认递归 mypy 命令发现既有 `agent_runtime/keyring.py:167,173` 在 Linux 下的 `ctypes.WinDLL/get_last_error` 两处类型错误，未修改无关平台代码，也不宣称全仓 mypy 通过。
- `npm run build`（含 TypeScript）通过，4004 modules；保留既有大于 1500 kB chunk 提示。
- 内置浏览器使用临时 SQLite、假 Provider 与最终前端构建：Pilot 请求执行后停止，服务端已撤权但注入 503 模拟响应丢失；读取 stopped 后结束本地等待，刷新后重新打开原对话，切 Haru 重试成功。原 command ID 唯一记录、模型调用保持 1、仅保留 user 消息，迟到 assistant 未保存；已知 stopped 不再显示待恢复 start。验收服务及自建页面已清理。
- 独立后端 CR 已复审围栏、租约、确认、标题和超时整理；前端 CR 发现的 Enter 门禁与返回会话回填缺口已修复，并增加定向测试；最终独立复审未发现新增阻断。部分超时 fallback 多 atom 可保留已提交前缀，约束见 [ADR-0009](../../architecture/decisions/0009-fence-pilot-execution-control.md)。
- **破坏性变化：无。** 增量创建执行/命令表及索引；不 reset 既有业务数据。本次是 P3A 定向实施交付，未执行完整 `scripts/release-gate.sh`、Docker、安装或真实 Provider 验收，不作为发布认证；未调用收费模型，未实现 P3B/P4/P5。
