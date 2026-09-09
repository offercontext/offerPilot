# ADR-0009：持久化执行权限并围栏化 Pilot 停止操作

- Status: Accepted
- 日期：2026-09-09
- Decider：用户授权继续实施的 Harness 交付路线图 `5b3bbb2` §8（P3A）
- 依据：[P2 请求与时间线](0008-persist-pilot-turns-and-timeline.md)、[P3A 实施计划](../../superpowers/plans/2026-09-08-harness-p3a-turn-control.md)

## Context

取消某个页面的 HTTP 请求不能证明运行时已失去业务提交权限，也不能控制另一页面发起的执行。
Pending 批准会继续同一逻辑任务；旧页面、重试和迟到的 worker 必须明确区分各次实际执行。
本期增加持久控制，不引入后台任务队列、断连后自动续跑或重连订阅。

## Decision

### 身份与状态

业务 SQLite 新增 `pilot_executions` 与 `pilot_interrupt_commands`。执行身份为 Turn、Conversation、
递增 generation 和仅服务端持有的 owner token。复合外键限制 Turn/Conversation 归属；部分唯一索引
保证每个 Conversation 至多一个 `running` 执行。事务内递增的 `conversation_sequence` 排序不同 Turn，
不依赖墙钟或某个 Turn 的 generation 大小。

初始 generation 与请求接纳、用户消息在同一事务中创建。等待确认时 execution 为
`waiting_confirmation`，不持有可执行权限；有效批准取得同一 Turn 的下一 generation。
终态重放仅读取原结果，不新增 generation，不执行工具或 Provider。

| 执行状态 | 语义 |
| --- | --- |
| running | owner 在有限租约内拥有执行权限 |
| waiting_confirmation | 当前 Pending 属于本 Turn，等待显式批准或拒绝 |
| completed / failed / interrupted | 本次实际执行已结束 |
| stopped | 停止事务已撤销受保护提交权限 |
| result_unknown | 租约失效、时钟异常或无法确认最终结果；不自动执行 |

P2 的 `PilotTurnRecord.state` 仅保留历史记录兼容。有 execution 时，任务读取和时间线以最新 execution
为准，增量展示版本包括 generation 与状态。读取在短事务中固定观察到的过期状态；墙钟恢复不能把
已判定失效的 owner 重新激活。进程重启只读取、核对已有记录。

### 精确停止协议

`GET /api/chat/conversations/{id}/execution` 返回内容最小化的 Turn、Conversation、generation 和状态。
`POST /api/chat/turns/{turn_id}/interrupt` 接收 UUID v4 `command_id` 与正整数 `expected_generation`。
两者使用现有工作区 API 认证；客户端不能提交 owner token 或自行声明租户。

停止收据与撤权在一个 `BEGIN IMMEDIATE` 中提交。同命令同目标原样重放收据；同命令不同目标/代次
返回 409。不同命令可收敛到同一个 `stopped`。旧代次返回 `generation_changed`，不会停止新代次。
已结束返回 `already_ended`；Pending 暂停返回 `no_active_execution`，不消费确认 token、不隐式拒绝。
租约失效返回 `result_unknown`。503 表示结果未知，客户端保留原 command ID 重试。

命令表仅保存身份、摘要和结果，不保存对话正文、工具参数或 Provider 内容。删除 Conversation 级联
清除执行记录，命令留下最小收据，不允许旧身份控制后来重用数据库 ID 的对话。

### 实际提交边界

执行身份通过 ContextVar 显式传播到 sync/SSE worker。业务 Engine 的 commit hook 在原 connection
和原事务中执行带 owner、generation、Conversation、状态与租约条件的 UPDATE，取得 SQLite 写锁，
随后重新检查 UTC 与本地单调时限。围栏失败时显式回滚 DBAPI 事务，避免 SQLAlchemy commit 事件
抛错后把未回滚的写入遗留给连接池。现有 Ledger、delivery、domain revision、HITL 条件继续同时生效。

批准预检后先取得持久 generation；typed 与 legacy 路径在实际业务事务内、handler 入口前再次执行
不可绕过的围栏，与 Pending claim 同处 `BEGIN IMMEDIATE`。停止先取得锁时不进入 handler；业务
事务先取得权限时可提交，随后停止保留这次已提交结果。最终 commit 仍检查租约，不能靠入口检查
绕过执行中失效。

| 写入/副作用 | 控制边界 |
| --- | --- |
| 已发布的本地 Application/Event/Note/Offer/JD 等写工具 | 同事务入口及 commit 围栏，叠加原 Ledger/domain 条件 |
| 初始/续接 Pending、审批 claim、最终消息 | 主业务 Engine 围栏；原确认与 delivery CAS 保留 |
| Turn 终态 | owner/generation CAS；旧 finally 不覆盖停止或新执行 |
| 标题生成 | 仅最新成功/待确认执行可开始；标题写入再次校验原身份与最新 sequence |
| 固定超时消息、已提交回执整理 | 独立受信任终态 scope；仅 interrupted/result_unknown、同 owner 且最新 sequence，仍受 delivery CAS 限制 |
| GET/Timeline 修复、租约续期、停止收据 | 受信任控制事务，不产生 Provider 或工具执行 |
| Journal | 独立诊断 Engine，不作为执行事实源，不阻碍控制收敛 |
| 用户显式 Undo/其他独立手工 API 写入 | 保留自身授权、幂等和补偿条件，不继承被停止 worker 的权限 |
| 已发出的 Provider 请求 | 不承诺杀死线程、撤回供应商请求或停止计费；迟到结果不能提交为原任务输出 |

超时整理使用全新的 scope，只由 Runtime 的固定 timeout/delivery convergence 入口授予。
原 worker 不共享这个 grant；终态 scope 不能进入写工具 handler。显式停止或新 execution 出现后，
旧超时整理与旧标题提交也会被拒绝。不得把此入口扩展为自动重试执行器。

租约默认 30 秒，心跳最多每 5 秒续期。续租取时发生在取得数据库锁后；返回时再次检查本地单调期限。
续租失败或迟到不会恢复旧权限。shutdown 只撤销并清理本进程控制，不调度替代执行。

### 两个界面的行为

Pilot 与 Haru 共享控制器。原请求只绑定响应明确返回的 Turn/generation；轮询结果不能给本地请求
猜测归属。控制结果只作用于精确匹配的执行，切换会话或开始新代次后，旧响应不得中止新请求。
当前页面每两秒读取选中会话执行事实；另一页面已停止同一执行时，结束本地订阅并读取保存记录。
切换到其他会话时提供返回原执行会话的入口；返回后旧订阅完成时按会话和可见代次回读持久记录，
不依赖可选 Timeline 接口。服务端读到 running 时，两个外壳和共享入口均禁止再次发送。

停止命令按 command ID 分别写入本地存储，记录仅含身份；结果未知可刷新后重试原命令。
清理一条收据不会覆盖另一窗口的待核对命令。停止结果、进行中与未确认分别展示；Pending 暂停
不提供停止代替拒绝。已提交修改仍可通过原操作记录撤销。

## Consequences

停止与提交具有明确的数据库先后关系，进程重启、页面重试和时钟变化不能授予旧 worker 新权限。
新增表与索引兼容 P2 数据库，不 reset 或删除已有业务数据。

普通消息序列及部分超时 fallback 仍可能由多个 repository atom 组成。停止可以保留已提交前缀；
任务状态明确为停止、中断或待核对，不能将前缀宣称为完整成功。后续 atom 被围栏拒绝时，不回滚已经
提交的业务事实，不用自动执行补齐。需要撤销时使用原业务 Undo。

SQLite 锁竞争可能让控制请求等待或失败；失败时客户端保留命令核对，不能把超时当成已停止。
未来新增文件、子进程、第三方 mutation 或新 Engine 的工具必须重新审查保护矩阵；本期已发布写工具
的业务 mutation 在同一 SQLite 边界内。P3B 的后台存活、预算、订阅重连及 P4/P5 不在本期范围。

## Alternatives Considered

1. **仅取消浏览器请求并设置进程内标志**：不能跨页面/进程确认结果，无法阻止已经开始的迟到事务。
2. **只在调用工具前检查数据库状态**：检查与 commit 间存在竞态，无法涵盖 Pending、消息和内部写入。
3. **在本期引入完整后台执行队列**：将改变断连、生命周期和费用边界，超出已划定的 P3A 范围。

## Related

- [请求与增量时间线](0008-persist-pilot-turns-and-timeline.md)
- [修改确认确定性回执](0006-edited-confirmation-receipts.md)
- [P3A 实施与验证](../../superpowers/plans/2026-09-08-harness-p3a-turn-control.md)
- [仓库施工协议](../../../AGENTS.md)
