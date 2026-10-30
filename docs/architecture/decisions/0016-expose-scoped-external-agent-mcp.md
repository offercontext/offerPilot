# ADR-0016: 以受限 MCP 提案连接外部 Agent

Status: Accepted for implementation；本地验证范围见接入说明，不代表发布验收。

日期：2026-10-29。Decider：用户明确授权的外部 Agent 接入方向。

## Context

用户希望在 DSH 和其他 MCP 客户端中查询 OfferPilot 数据、安排面试、维护投递/简历/原始笔记、检索当前 Knowledge，并由用户明确确认后保存、回读。CLI 继续服务终端和脚本，不以补齐所有 CLI/API 为前置，也不复制领域写入逻辑。

已核对飞书主文档 `Q353d2stRowjrFx8fmkc6uPmnQb` 的用户确认原则、本地领域契约和当前代码。Product Action 的封闭目录目前只有故事确认、复习信号两项；聊天 Pending 绑定 conversation、journal、provider call 和 sealed authority。外部提案不能冒用这些凭据。

DSH 官方源码核对版本为 `f3803c96974ad4a94195fb97e5c637e9c2a3afe3`；其 MCP 客户端支持 stdio、Streamable HTTP，目前不支持 elicitation 与 task execution。安装验证固定到 npm `@deepseek-ai/dsh-mcp-client@0.2.1-rc.2`；源码 master 与 npm 版本分别记录，不能视为同一快照。

## Decision

1. 首批只提供 stdio。OfferPilot 自有 adapter 调用领域 Repository；DSH 只配置通用 MCP 客户端，没有独立数据库写插件。
2. 用户通过独立 `oc mcp connect` 命令显式授予投递 ID 集合或 workspace 范围，默认只读。`--allow-scheduling` 仅允许提出面试创建、改期请求。连接身份固定在进程配置，工具输入不能改变权限。撤销连接同时阻断旧提案确认。
3. 原 8 工具保留，另以显式 capability 提供投递增改删、简历读/增改复制删、原始面试笔记读/增改删和 Knowledge 读取，共 23 工具。完整矩阵维护在[接入说明](../external-agent-mcp.md#1-安装和明确授权)。旧连接 capability 为空，不自动扩权。Resume/Knowledge 为独立全局授权；投递集合不能暗授这些权限。Note 受投递范围约束，未绑定笔记另需 workspace 与专门 grant。写 grant 要求配套读 grant，仅允许提案。声明封闭输入、逐工具输出 schema、结构化结果和稳定错误；stdout 仅承载 MCP 协议。
4. `external_agent_requests` 保存冻结前后安排、输入摘要、提案摘要、15 分钟有效期和终态回执。相同连接、key、规范化输入回放原请求；同 key 异输入拒绝。改期必须携带当前事件 revision，保留原事件语义、笔记、标签，并保持原提醒提前量。
5. MCP 不提供 approve/confirm；拒绝所有额外确认参数。用户在独立、可信的 OfferPilot 操作员终端执行 `oc mcp review`，审阅完整前后值后输入含完整 request ID 的批准或拒绝短语。不提供 `--yes`、确认布尔值或管道确认。
6. TTY 只能阻止意外管道自动化，**不能证明真人身份**。此边界保证的是受限 MCP 工具没有审批能力；同一 OS 用户下的任意 shell、Python、文件或数据库权限不在隔离保证内。不要让需要隔离的 Agent 同时控制操作员终端或 OfferPilot 数据目录。更强隔离需要独立 OS 身份/容器、用户界面认证或 OS 用户存在性机制，不能用提示词或一个布尔值宣称已经具备。
7. 批准在单一 SQLite `BEGIN IMMEDIATE` 事务中重新检查连接、作用域、父投递可见性、冻结目标信息、过期和资源 revision，再经绑定同一 session 的领域 Repository 写入，同事务保存终态回执。Resume 主标记变化还核对活动集合；投递软删除还核对所有关联领域表摘要，不能让旧批准隐藏新增工作。并发批准只提交一次；来源或所有权变化产生 conflict，不能覆盖。
8. MCP 取消中断观察，不声称撤销已开始的事务。未收到提案结果时，以原 key/输入恢复；显式 `cancel_request` 只取消 pending。终态不重新执行，已提交取消返回原 committed 回执。无后台自动批准或真实模型调用。
9. 0035 新增两张外部请求表；0036 增加默认空 capability、扩展 action CHECK 并允许全局资源 application_id 为空。后者在单一写事务重建请求表，逐行核对保留旧请求、幂等 key、摘要和回执，失败回滚；旧 grant 的空 capability 不进入冻结 JSON，旧提案仍有效。不改已有事件/对话语义，不删除业务数据。stdio 服务只打开既有数据库，缺失/锁定/不可用安全失败，不初始化或升级工作区。
10. 投递创建仅保存基础信息，不包含 initial JD、HTTP 创建回执或重复提示；外部请求和领域创建同事务提供自己的幂等保证。简历文本规则与 HTTP 对齐，结构化局部更新不清空未改正文；非空主简历不可删除。Note 事件绑定复用领域校验，不改变归属或自动捕获 Knowledge。Knowledge 复用 `recall_knowledge` 的当前 Note/Evidence 双通道、指纹/引用校验和 provenance，在同一读快照执行，不产生搜索 trace 或其他写入。输出预算整条省略，接受既有最多 24+24 候选、每 Note 最多 160 引用的有界构建成本，不把 max_bytes 宣称为 SQL/计算成本限制。
11. MCP registry 独立于 Pilot 的 sealed authority catalog。架构门禁继续扫描全部源码；仅对 MCP 的具名协议/schema/描述映射、精确只读路由、应用回执回填和迁移 SQL CHECK 哨兵增加 AST 角色例外，不按目录或文件豁免。比较必须位于预期 If.test；只读路由和迁移分支体锁定结构指纹，随角色审查更新。双向依赖检查覆盖具体 AI/Pilot authority 与外部 registry 的静态导入、可解析别名、相对导入、importlib/__import__ 和 loader 入口、builtins 动态执行，以及 sys.modules/sys.__dict__/vars(sys) 缓存引用；已识别动态导入的目标无法解析时失败关闭。它是静态架构回归检查，不是任意 Python 求值或 OS 沙箱的证明。禁止内部模型执行入口另列边界，纯共享 AI 数据类型仍可使用。CLI/db 的 owner 与存储装配保留。反射分类、前缀路由、旧符号、第二 provider registry 与 fallback/shadow 门禁不变；反例覆盖把同类违规代码移到 external_agents 后仍失败，见[门禁测试](../../../tests/tool_metadata/test_deletion_gates.py)。

## Consequences

通用 MCP 客户端可以复用首批业务能力，领域校验和业务事务保留在 OfferPilot。查询与提案具有明确授权范围，结果未知时可恢复原操作。

代价是用户需要在独立终端审阅，不能在 DSH 中让模型自行“代确认”。当前没有 HTTP MCP、多用户认证、OS 沙箱、全部 API 工具化或用户界面审批收件箱。2026-10-30 增加可安装的 [DSH 薄连接包](../../../plugins/dsh-offerpilot/README.md)，只加载既有连接配置并复用官方 MCP client，仍不采用原生插件直接写数据库的方案。

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
