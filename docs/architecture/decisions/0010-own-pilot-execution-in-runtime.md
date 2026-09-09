# ADR-0010：由 Runtime 持有 Pilot 执行，客户端只订阅进度

- Status: Accepted（实施与验证见关联计划）
- 日期：2026-09-09
- Decider：用户授权连续实施的 Harness 交付路线图 `5b3bbb2` §9（P3B）
- 依据：[P2 时间线](0008-persist-pilot-turns-and-timeline.md)、[P3A 执行围栏](0009-fence-pilot-execution-control.md)

## Context

P3A 能停止精确执行代次，但旧流式请求仍持有执行生命周期。关闭窗口或网络断开可能中止任务；
读取保存记录只能恢复展示，不能让新页面观察仍在运行的原任务。

新行为必须显式版本化，保留旧客户端的断连语义。后台执行也必须受容量、时间和调用预算限制，
不能因为没有订阅者而无限运行，更不能把重新打开页面实现成重放初始请求。

## Decision

### 单实例执行所有权

单服务实例中的 Runtime manager 负责有界工作池、等待队列、deadline、调用预算及临时事件缓存。
构造应用及预留容量不启动线程；首次提交在锁内一次性启动固定工作池。线程启动失败关闭接纳，
唤醒已启动线程并由接纳层收敛持久状态，不能留下无人处理的队列。
业务 SQLite 仍是 Turn、execution generation、Pending、Ledger 和 Timeline 的事实源。
manager 不建立可重执行的持久消息副本，不保存新的授权或替代现有 Agent Loop。

实际尚未返回的 Provider 调用继续占用物理并发容量。逻辑超时或停止可以撤销提交权限，不能以此
假定供应商计算已退出，再无界启动替代线程。排队时间计入任务 deadline，重连不重置预算。

确认写入已提交后的固定超时回执，通过调用控制器的独立终态恢复 scope 收敛；不复用已撤销的 worker scope，也不移除提交围栏。恢复仍检查原 Turn/generation、显式停止及后续执行，不能启动新模型或业务写入。 超时后的旧 SSE 只交付已持久化的完成回执和不携带待确认操作的终态错误；迟到文本、未持久化结果及取消后的事件仍被过滤。

模型和工具预算在实际调用边界扣减，包括适用的 Provider fallback 与标题生成。Journal 是否开启
不影响预算。标题没有剩余额度时退回确定性标题，不扩大任务预算。

### 显式协议

协议前缀为 `/api/pilot/runtime/v1`，版本为 `pilot-runtime-v1`。

| 接口 | 职责 |
| --- | --- |
| `POST /turns` | 使用原 request ID 接纳一次任务，返回 Turn、Conversation、generation 与观察入口 |
| `GET /turns/{id}` | 读取执行状态、保存结果和恢复信息，不调用 Provider 或工具 |
| `GET /turns/{id}/snapshot` | 返回一致观察快照及高水位游标 |
| `GET /turns/{id}/events?after=…` | 只读订阅快照之后的进度，断连只删除订阅者 |
| `POST /turns/{id}/confirm` | 复用 Pending/token/指纹/CAS，批准后在同一 Turn 取得新 generation |

停止继续使用 P3A 的持久命令，必须携带原 command ID 和 expected generation。订阅者不能通过
断开、清空界面或关闭角色消费 Pending、撤销批准、停止其他 generation。

旧 `/api/chat`、`/api/chat/stream`、`/api/chat/confirm`、`/api/chat/confirm/stream` 保留原生命周期。
执行读取标注新协议，客户端仅对明确的新协议任务启用关闭后继续的交互语义。

### 恢复、隔离与有界缓存

临时 token/进度按条数与字节限制；溢出返回明确重同步信号。慢客户端不阻塞业务提交。
Pending、最终消息和提交事实必须先持久化，随后通知；通知丢失依靠 P2 保存记录恢复。
快照、高水位与订阅衔接必须覆盖注册窗口内的变化；客户端按身份、代次与 revision 丢弃旧内容。

观察游标绑定任务、代次和运行实例。旧实例或失效游标要求重新读取快照，不重新执行。
读取和重新订阅仍执行当前认证、Conversation 归属及删除/来源撤回检查。

Pilot/Haru 共用控制器。关窗取消当前订阅；重开读取原任务并重新订阅。其他页面完成、失败或
进入待确认后，当前页面可独立读取保存消息与 Pending，不依赖旧 Promise 仍存活。
切换会话或新执行代次后，迟到的订阅和保存记录不得覆盖新界面。

### 退出与未知结果

关闭服务停止接纳新任务，撤销排队及在途执行权限，尽力完成有界清理。服务重启只核对和展示
保存事实；不自动重跑 Provider、不重试未知工具、不恢复旧模型上下文。
无法确认结果时保留中断或待核对状态，不能将其当作“未执行”并自动补跑。

## Consequences

接纳先在有界等待的入口内核对持久幂等身份，再预留队列位置；成功预留后才保存用户消息和执行
身份。容量拒绝因此能明确表示尚未接纳。入口等待不持有 worker/watchdog 的锁，重连仍只读。

读取内存状态、快照与每条 SSE 帧前重新验证对话和原来源仍可见；持久执行终态优先于内存中的
运行状态。进程重启后的回复重建只使用绑定到原 Turn 的消息/操作，不能借用其他轮次的最新回复。

HTTP/SSE 适配器位于 `offerpilot/runtime_transport.py`，依赖 Runtime 的只读订阅接口；
Runtime 包本身不依赖 FastAPI/Starlette，保持执行与传输的依赖方向。

关闭窗口或网络断开不再决定新协议任务的生命周期，多个页面可以观察同一任务。代价是单实例
manager、观察缓存和提交围栏必须共同收敛；持久执行记录不代表支持跨进程自动续跑。

停止不保证供应商立即停止计算或计费。已经提交的业务事实继续保留，Undo 仍使用原补偿协议。
本期不引入外部队列、分布式调度、steer、follow-up 或 inject。

## Alternatives Considered

1. 继续由 SSE 请求持有执行：实现最少，但关闭或断网仍影响运行，无法满足重新订阅的产品行为。
2. 断线时自动重放初始 POST：容易重复创建任务、重置预算或重试未知副作用，不能作为恢复协议。
3. 引入外部持久队列并在重启后自动续跑：当前单实例范围不需要，且需要另行设计未知工具对账与授权恢复。

## Related

- [P3B 实施与验证计划](../../superpowers/plans/2026-09-09-harness-p3b-runtime-reattach.md)
- [P3B–P5 总计划](../../superpowers/plans/2026-09-09-harness-p3b-p5-delivery.md)
- [文档规范](../documentation-rules.md)
