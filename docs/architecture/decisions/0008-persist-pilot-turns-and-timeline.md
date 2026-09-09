# ADR-0008：持久化 Pilot 请求身份与增量时间线

- Status: Accepted
- 日期：2026-09-08
- Decider：用户授权继续实施的 Harness 交付路线图 `5b3bbb2` §7
- 依据：[共享动作展示](0007-project-action-presentation.md)、[P2 实施计划](../../superpowers/plans/2026-09-08-harness-p2-durable-timeline.md)

## Context

P1 可以从消息和业务操作恢复展示，但不能识别一次响应丢失的原提交，也不能用展示指纹判断变化顺序。
Journal 是可关闭的诊断系统，不能承担请求接纳、业务身份或恢复执行的职责。

## Decision

### 接纳与执行

业务 SQLite 新增 `pilot_turns`、`pilot_request_receipts` 及消息、操作关系表。客户端每次逻辑提交
生成 UUID v4；错误提示的重试沿用原 ID 和冻结的上下文，独立新消息使用新 ID。

接纳事务在任何 Provider 或工具执行前完成：核对已存在的请求、验证原请求、创建或读取 Conversation、
冻结首次来源版本、保存用户消息及 Turn。唯一请求键与 `accepted → started` 数据库 CAS 决定唯一初始执行者。
请求摘要由服务端规范化输入计算，与首次来源版本分开保存。重复请求先读取接纳记录，不重读当前来源来比较摘要。

边界是通过现有 auth 的本地工作区数据目录与 `pilot-start-v1` 命令。客户端不能声明 tenant。
记录同时绑定提交时和解析后的 Conversation；首次使用 0 的请求可用返回 ID 重试，不能改绑其他对话。
同键同内容返回 `turn_recovered`，同键不同内容或不同对话返回 409。原对话已删除的请求返回 410；
普通不存在的对话或未找到请求返回 404。老调用方省略 request_id 时服务端生成新 ID，只有显式复用 ID 才提供重试幂等保证。

已接纳请求绝不因重试、读取或进程重启而再次启动。进程中断时缺失的终态保持不完整，不猜测执行成功。
已知初始化失败记录失败；运行中取消或超时记录中断。业务结果后的展示状态写入失败仅产生固定日志，
有限清理重试保留已知终态，不能把已经知道的完成改写为中断。

这不是后台执行承诺：断连取消、HITL、Pending/Confirm、补偿和 provider blocks 仍由现有运行时管理。
generation 与 fencing 已由 [P3A](0009-fence-pilot-execution-control.md) 落地；后台调度仍属后续 P3B。

### 展示与游标

`pilot_timeline_items` 保存当前展示；`pilot_timeline_changes` 保存已提交的版本；每个对话独立维护高水位。
稳定身份为 `turn:<turn_id>:<原 Item ID>`。DTO 包含类型、schema、source refs/revision、
单调 revision/display_revision、payload digest、ordinal 和 change_seq。

ordinal 只表示首次出现顺序。修改既有 Item 时保持 ordinal，增加 revision 和 change_seq；
因此读取到第 20 个 Item 后仍能收到第 5 个 Item 的撤销结果。Item、版本与高水位在同一个写事务提交。
快照及其分页固定 high watermark；后续更新在增量读取中返回。客户端按 item_id 和单调 revision 合并，
保留无内容墓碑防止迟到响应恢复已删除内容，不按 hash 或时间戳排序版本。

游标经每个对话的持久密钥签名，绑定协议版本、对话、分页种类及范围。非法、跨对话或失效快照返回
`timeline_resync_required`；客户端丢弃局部缓存并重新读取完整快照。读取失败时沿用 P1 的消息降级和刷新入口。

### 恢复与保留

`GET /api/chat/turns/{turn_id}` 与 `GET /api/chat/requests/{request_id}` 只读取身份和状态。
`GET /api/chat/conversations/{id}/timeline` 从持久 Message、Ledger 及 Turn 重建缺失展示；
只允许写展示表与缺失的历史归属关系，不执行 Provider、工具、审批或运行时恢复。
P1 `/presentation` 仍保持源数据只读。Product Action 保持独立身份和原确认通道。

历史记录只有明确的消息、operation 或 tool call 关联才合并；不能证明的过程标记为 incomplete。
每次修复有显式来源上限（4096 条消息、4096 个操作及最多 10000 个展示来源），超过上限报错并回退到
原消息读取，不悄悄截断时间线。这个保守上限需要在超长对话使用量增长时改为分批修复。

正常来源更新保留已提交的回执事实。这里的来源指 Message 和 Ledger 等投影权威记录；
关联业务对象的普通软删除不撤销历史安全回执。权威来源移除时生成墓碑、清理该 Item 的所有历史 payload，
使依赖旧内容的分页失效。删除 Conversation 通过外键级联清除 Turn、消息关系、Item 和 Change；
请求摘要墓碑保留，不包含原始请求正文。接纳记录在对话存续期间不自动过期；删除后墓碑同样不自动过期，
以免旧客户端请求重新执行。未来任何压缩策略都必须提供明确 resync 和保留期限契约。

客户端只在 localStorage 保存 request/turn/conversation ID，不保存请求正文、附件内容、token 或结果。
刷新后的恢复入口只 GET 原任务，再读取该对话。暂未找到任务时不补发执行。

## Consequences

相同逻辑请求的重传不会追加第二条用户消息或再次调用模型。Pilot 与 Haru 共享同一任务展示，
恢复不依赖 Journal。新增表通过现有数据库初始化创建，不删除本地开发数据。

SQLite `BEGIN IMMEDIATE` 将来源快照、投影更新和可见高水位串行化，带来有限写锁成本；
保留历史展示版本会增长数据库。当前不承诺无限长对话修复或后台执行存活。
业务运行中进程崩溃可能只留下 started；这种状态只能说明没有持久终态，不能触发自动重跑。

## Alternatives Considered

1. 用 Journal run/seq 作为任务身份和游标：诊断关闭、压缩或事件丢失会改变业务恢复语义，未采用。
2. 每次刷新全量 P1 快照并按内容 hash 合并：不能表达旧 Item 后续更新的提交顺序，未采用。
3. 所有领域仓库统一事件溯源并后台重放：扩大权限与执行边界，且不是 P2 的展示恢复需求，未采用。

## Related

- [P0 确定性确认回执](0006-edited-confirmation-receipts.md)
- [P1 共享展示](0007-project-action-presentation.md)
- [P2 实施与验证](../../superpowers/plans/2026-09-08-harness-p2-durable-timeline.md)
