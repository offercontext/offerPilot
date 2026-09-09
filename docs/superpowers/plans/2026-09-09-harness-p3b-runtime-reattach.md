# Harness P3B：Runtime 持有执行与重新订阅

> 状态：实现、本地验证和独立 CR 完成；完整门禁剩余项见总计划 §5。使用 subagent-driven-development 与 TDD 按任务执行；用户已授权连续推进，无阶段审批。

**Goal:** 同一任务在关闭窗口、断线和重新打开后继续受控执行，并通过只读订阅恢复，不产生重复启动或预算重置。

**Architecture:** 新协议由单服务 Runtime manager 持有有界工作池和等待队列。P2 负责请求/Turn 接纳与 Timeline，P3A 负责执行代次和所有提交围栏；订阅只读，不拥有任务生命周期。进程重启不启动旧任务。

**Tech Stack:** Python、SQLite、FastAPI、现有 Runtime/Agent Driver、React/TypeScript。

## 1. 冻结的行为

| 场景 | 结果 |
| --- | --- |
| 新客户端提交 | 可靠接纳后返回原任务身份，再订阅；响应丢失用原请求身份核对 |
| 新客户端关闭/断线 | 只结束订阅；在服务存活及预算有效时继续执行 |
| 重开/多个订阅者 | 读取一致快照/游标及后续变化，不重新调用 Provider |
| 用户停止 | 复用 P3A command ID 与 expected generation；已提交事实保留 |
| 等待确认与批准 | 同 Turn 的新 generation；权限与 token/Fingerprint/CAS 仍由原协议控制 |
| 服务退出/重启 | 不再接纳并收敛在途；未核实保留中断/未知，只恢复事实 |
| 容量或预算耗尽 | 明确返回限制；不得无限等待、无限创建线程或通过重连重置 |
| 旧 API 消费者 | 继续原连接生命周期，不偷偷开启后台运行 |

## 2. 模块职责

| 文件/区域 | 本期职责 |
| --- | --- |
| `src/offerpilot/pilot_runtime/managed_execution.py`（新） | 执行注册、有限池/等待、deadline、观察缓存、清理与关闭 |
| `src/offerpilot/pilot_runtime/execution_budget.py`（新） | 模型/工具调用预算，与观察连接分离；不使用 Journal 预算 |
| `src/offerpilot/api.py` | 新协议接纳与读取路由、认证/来源门禁、现有运行时适配 |
| `src/offerpilot/models.py`、`pilot_control.py`、`pilot_timeline.py` | 必要的协议身份/命令关联及只读恢复；不保存可重执行的授权载荷 |
| `src/offerpilot/ai/agent_loop.py` 及现有实际 Provider/工具边界 | 新管理 scope 下的调用预算，旧调用不改变 |
| `web/src/services/chat.ts` 与新增订阅适配 | 一次提交后订阅，有限重连只读，沿用上层 ChatResponse 契约 |
| `web/src/features/assistantSurface/` | 外壳共享、关闭仅退订、原任务重订、远端完成回填 |
| `tests/test_managed_execution*.py`、前端对应 `*.test.*` | 真实可控并发/故障与用户入口验证 |

## 3. 实施任务

### Task 1：有界运行管理

- [x] 用屏障任务先证明缺少零订阅者执行与物理并发边界；明确测试启动次数和实际存活 worker 数。
- [x] 实现一次认领/排队；固定实际 worker 上限，等待队列有限。超时的底层调用未返回时仍占实际容量。
- [x] deadline 从接纳算起；独立 watchdog 撤销到期控制，订阅和重连不能延长。
- [x] 临时事件按条数和字节限额，有缺口标识；不缓存完整私有异常/凭据，不因慢订阅者阻塞提交。
- [x] shutdown 停止接纳、取消排队、撤销在途权限；不等待不可控模型无限返回，不自动续跑。

### Task 2：预算与既有 Runtime 接入

- [x] 在实际模型及工具执行边界添加新 scope 的调用预算，覆盖 fallback/重试，不以循环次数代替实际调用次数。
- [x] 初始任务与审批续跑复用当前 Runtime 入口、Ledger、delivery 和 P3A；任何 timeout/stop 的迟到输出不得越过围栏。
- [x] 预算耗尽有安全、可理解的终态或待核对说明；已提交内容仍可恢复，不重新执行来补齐。
- [x] 测试无订阅、断开、反复连接、超时、停止、队列耗尽与 Provider 未返回时的容量不漂移。

### Task 3：新协议与恢复

- [x] 新协议明确版本、命令/请求身份和 Turn/generation；保持所有旧端点行为。
- [x] 接纳与容量预留衔接；相同键重放只返回原身份；批准验证后取得执行身份，不猜测下一代次。
- [x] 快照高水位、历史变更与实时事件衔接；旧 revision 丢弃，缓冲缺口回读 P2，不 POST 重跑。
- [x] 读取/订阅核验认证、Conversation/Turn 归属、删除/来源失效；重启读取不调用模型或工具。
- [x] API 屏障测试证明接纳响应丢失、并发重连、确认重放、来源撤回和旧客户端兼容。

### Task 4：Pilot/Haru

- [x] 服务层先写一次提交/只读重连、断线和身份不匹配测试，再切换新协议。
- [x] 新请求立即绑定服务端身份，切换外壳复用控制器；关闭窗口仅退订，明确显示任务仍可继续。
- [x] 重开原会话恢复 Timeline/订阅；没有本地 Promise 的 running 也可停止并接收最终内容。
- [x] 新会话或新代次出现后，旧订阅/回读不得覆盖或取消新执行；保留原 stop 重试命令。
- [x] 暂时断开不显示业务失败；用户重新发送属于明确新任务，不用于恢复旧任务。

### Task 5：审查与交付

- [x] 规格覆盖与代码质量独立审查，修复并复测实际缺口。
- [x] 已执行后端相关测试、ruff/mypy、前端测试/构建及假 Provider 浏览器走查；通过项和既有失败见[总计划](2026-09-09-harness-p3b-p5-delivery.md)。
- [x] ADR 和公开关闭窗口行为说明；记录迁移/回滚、协议兼容、调用预算及未执行的真实验收。
- [x] 暂存检查与中文 conventional commit；本次与 P4/P5 整合收口。

## 4. 基线证据

`PYTHONPATH=src LITELLM_LOCAL_MODEL_COST_MAP=True pytest --noconftest tests/test_pilot_control.py tests/pilot_runtime/test_contracts.py -q --tb=short`：71 passed（35.78 秒）。WSL Python 3.12 隔离 venv。
