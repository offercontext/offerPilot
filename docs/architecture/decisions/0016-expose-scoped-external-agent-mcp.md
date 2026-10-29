# ADR-0016: 以受限 MCP 提案连接外部 Agent

Status: Accepted for implementation；本地验证范围见接入说明，不代表发布验收。

日期：2026-10-29。Decider：用户明确授权的外部 Agent 接入方向。

## Context

用户希望在 DSH 和其他 MCP 客户端中查询 OfferPilot 数据、安排面试，并由用户明确确认后保存、回读。CLI 继续服务终端和脚本，不以补齐所有 CLI/API 为前置，也不复制领域写入逻辑。

已核对飞书主文档 `Q353d2stRowjrFx8fmkc6uPmnQb` 的用户确认原则、本地领域契约和当前代码。Product Action 的封闭目录目前只有故事确认、复习信号两项；聊天 Pending 绑定 conversation、journal、provider call 和 sealed authority。外部提案不能冒用这些凭据。

DSH 官方源码核对版本为 `f3803c96974ad4a94195fb97e5c637e9c2a3afe3`；其 MCP 客户端支持 stdio、Streamable HTTP，目前不支持 elicitation 与 task execution。安装验证固定到 npm `@deepseek-ai/dsh-mcp-client@0.2.1-rc.2`；源码 master 与 npm 版本分别记录，不能视为同一快照。

## Decision

1. 首批只提供 stdio。OfferPilot 自有 adapter 调用领域 Repository；DSH 只配置通用 MCP 客户端，没有独立数据库写插件。
2. 用户通过独立 `oc mcp connect` 命令显式授予投递 ID 集合或 workspace 范围，默认只读。`--allow-scheduling` 仅允许提出面试创建、改期请求。连接身份固定在进程配置，工具输入不能改变权限。撤销连接同时阻断旧提案确认。
3. 提供四个业务读取、两个面试提案、请求查询和请求取消工具。声明封闭输入、逐工具输出 schema、结构化结果和稳定错误；stdout 仅承载 MCP 协议。
4. `external_agent_requests` 保存冻结前后安排、输入摘要、提案摘要、15 分钟有效期和终态回执。相同连接、key、规范化输入回放原请求；同 key 异输入拒绝。改期必须携带当前事件 revision，保留原事件语义、笔记、标签，并保持原提醒提前量。
5. MCP 不提供 approve/confirm；拒绝所有额外确认参数。用户在独立、可信的 OfferPilot 操作员终端执行 `oc mcp review`，审阅完整前后值后输入含完整 request ID 的批准或拒绝短语。不提供 `--yes`、确认布尔值或管道确认。
6. TTY 只能阻止意外管道自动化，**不能证明真人身份**。此边界保证的是受限 MCP 工具没有审批能力；同一 OS 用户下的任意 shell、Python、文件或数据库权限不在隔离保证内。不要让需要隔离的 Agent 同时控制操作员终端或 OfferPilot 数据目录。更强隔离需要独立 OS 身份/容器、用户界面认证或 OS 用户存在性机制，不能用提示词或一个布尔值宣称已经具备。
7. 批准在单一 SQLite `BEGIN IMMEDIATE` 事务中重新检查连接、作用域、父投递可见性、冻结目标信息、过期和事件 revision，再经 `ApplicationEventsRepository.bind(session)` 写事件，同事务保存终态回执。并发批准只提交一次；事件变化或所有权变化产生 conflict，不能覆盖。
8. MCP 取消中断观察，不声称撤销已开始的事务。未收到提案结果时，以原 key/输入恢复；显式 `cancel_request` 只取消 pending。终态不重新执行，已提交取消返回原 committed 回执。无后台自动批准或真实模型调用。
9. 新增两张表和迁移标记；不改已有事件/对话 schema、不迁移或删除业务数据。stdio 服务只打开既有数据库，缺失/锁定/不可用安全失败，不初始化新工作区。

## Consequences

通用 MCP 客户端可以复用首批业务能力，领域校验和业务事务保留在 OfferPilot。查询与提案具有明确授权范围，结果未知时可恢复原操作。

代价是用户需要在独立终端审阅，不能在 DSH 中让模型自行“代确认”。当前没有 HTTP MCP、多用户认证、OS 沙箱、全部 API 工具化、OfferPilot 专用 DSH 原生插件或用户界面审批收件箱。它们不是本期已实现能力。

## Alternatives Considered

- 全面包装 CLI/API：范围过大，普通写接口也没有这条外部提案的用户确认和幂等契约；没有采用。
- 原生 DSH 插件直接写数据库：复制业务逻辑并扩大平台耦合；通用 MCP 已满足首批需求，没有采用。
- 复用聊天 Pending / 扩大 Product Action：需要伪造会话/provider 执行来源，或打破当前封闭 action catalog；没有采用。复用底层领域 Repository 和事务，而不伪造既有审批来源。
- 在 MCP 中提供 confirm / `confirmed=true`：模型能够自调用，不构成可信用户确认；没有采用。
- 通过 elicitation 审批：核对的 DSH 版本不支持，无法作为这次可运行的确认通路。

## Related

- [接入、恢复与验证边界](../external-agent-mcp.md)
- [Pilot 与确认业务地图](../code-map/pilot-runtime.md)
- [仓库领域与验证规则](../../../AGENTS.md)
- [DSH MCP 官方文档固定快照](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/docs/subsystems/mcp.md)
- [DSH 客户端配置固定快照](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/packages/mcp/mcp-client/README.md)
