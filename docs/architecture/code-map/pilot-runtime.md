# Pilot 对话、确认与运行恢复地图

[返回业务总图](README.md) · [知识与可选来源](knowledge-memory.md) · [设置与桌面](settings-desktop.md)

核对基线：2026-10-28，`454af3d7`。依据：[Runtime 所有权 ADR](../decisions/0010-own-pilot-execution-in-runtime.md)、[可选上下文 ADR](../decisions/0011-bound-optional-context-sources.md)、[图片消息 ADR](../decisions/0013-persist-native-image-messages.md)。

## 1. 先判定协议与对象

| 用户说法 | 首查入口 | 不能混淆的对象 |
|---|---|---|
| 发消息、Haru 回答、关窗继续 | [pilotRuntime service](../../../web/src/services/pilotRuntime.ts)、[chatSubmission](../../../web/src/services/chatSubmission.ts) → [api.py](../../../src/offerpilot/api.py) `submit_runtime_turn` | 新 `/api/pilot/runtime/v1` 由 Runtime 持有任务；旧 `/api/chat` / stream 仍有旧传输生命周期 |
| 历史、切会话、上下文错误 | [chat service](../../../web/src/services/chat.ts) → [ChatRepository](../../../src/offerpilot/repositories/chat.py) | conversation、message、turn、execution generation 各自有身份；使用 context_type/context_ref |
| 待确认、重复执行、修改后确认 | [actionPresentation](../../../web/src/features/actionPresentation/) → Runtime confirmation / [write_operations](../../../src/offerpilot/ai/write_operations.py) | Pending、operation ledger、用户决策与模型建议分开 |
| 练习重点/经历故事的确认和撤销 | [reviewReadiness service](../../../web/src/features/reviewReadiness/service.ts)、[product_actions](../../../src/offerpilot/product_actions/) | Product Action 的 proposal/decision/terminal 与普通聊天 Pending 有各自契约；不能互换 token |
| 停止、超时、重新连接 | [managed_execution](../../../src/offerpilot/pilot_runtime/managed_execution.py)、[turn_control](../../../src/offerpilot/pilot_runtime/turn_control.py)、[runtime_transport](../../../src/offerpilot/runtime_transport.py) | 观察订阅、执行所有权、Provider 物理调用不是同一状态 |
| 图片上传、历史识图 | [chatImages](../../../web/src/services/chatImages.ts) → [chat_image_routes](../../../src/offerpilot/chat_image_routes.py) → [ChatImageStore](../../../src/offerpilot/chat_images.py) | 上传附件、绑定消息、历史引用和模型能力分别核对 |
| DSH / 外部 Agent 查询投递/简历/笔记/Knowledge，提出业务维护，独立用户审阅 | [external_agents](../../../src/offerpilot/external_agents/) 的 `server.build_server` → `ExternalAgentService.call` → `business.prepare/read`；独立 `oc mcp review` → `review_in_terminal` / `decide` | [接入与信任边界](../external-agent-mcp.md)、[ADR-0016](../decisions/0016-expose-scoped-external-agent-mcp.md)；不冒用 Chat Pending / Product Action |

## 2. 一次请求的调用链

1. HTTP 校验 request_id 与上下文，在原请求身份下接纳；`RuntimeExecutionManager` 控制容量、deadline 和预算。容量拒绝与已接纳后未知结果分开。
2. [PilotRuntime](../../../src/offerpilot/pilot_runtime/service.py) 负责验证、会话、来源、投影、执行与持久化的因果顺序；[composition](../../../src/offerpilot/pilot_runtime/composition.py) 装配依赖。旧流式路径 `prepare_stream` 在响应头边界前准备，不等于立即调用模型。
3. [context_projector](../../../src/offerpilot/context_projector/) 和[可选来源](knowledge-memory.md#4-pilot-消费链) 生成模型输入；历史 provider_blocks、来源指纹、预算裁剪和工具权限需分别检查。
4. [Agent Loop](../../../src/offerpilot/ai/agent_loop.py) 执行模型/工具；[tool_runtime](../../../src/offerpilot/ai/tool_runtime/) 管工具管线，[tool_authority](../../../src/offerpilot/ai/tool_authority/) 管授权范围。工具参数里的业务 ID 不能扩大已授范围。
5. [persistence](../../../src/offerpilot/pilot_runtime/persistence.py)、[ChatRepository](../../../src/offerpilot/repositories/chat.py)、[agent_runs](../../../src/offerpilot/repositories/agent_runs.py) 保存结果、Pending、运行记录；事件通知之后才能依赖这些事实恢复。

## 3. 确认、恢复和停止边界

- 写操作默认经 HITL；开发时被授权改代码不等于产品 `chat_auto_approve_writes` 已开启。确认绑定原 Pending、token、参数指纹和操作身份；失败后不要重建一个“等价”新写入。
- `/turns/{id}`、`/snapshot`、`/events` 是观察路径。重连读取原 Turn，游标失效重取快照；不能重发初始消息来恢复展示。
- 停止携带 command ID 和 expected generation；撤销提交资格不保证 Provider 立刻退出或停止计费，物理调用仍占容量。
- 服务重启核对保存事实，不自动重做未知 Provider/工具。确认写入已提交但响应超时时，先查 ledger 和保存回执，不能因没有最终文本就认定未写入。
- Product Action 的确认和补偿继续跟随 [coordinator](../../../src/offerpilot/product_actions/coordinator.py)、[repository](../../../src/offerpilot/product_actions/repository.py) 与具体领域 Handler；撤销必须确认原操作和当前状态允许，不能用新写操作模拟撤销。
- 共用恢复词汇以 [recovery-policy.v1.json](../../../contracts/recovery-policy.v1.json) 和[策略消费者](../../../src/offerpilot/reliability/policy.py)为准；单凭 HTTP 500 不能选“安全重试”。
- 外部 MCP 提案另有连接范围、显式 capability、15 分钟有效期、原 key 幂等与回执；`decide` 在 SQLite 写锁内重查来源，`business.recheck/execute` 绑定领域 Repository 同事务写入。全局 Resume/Knowledge 不由 workspace 或投递范围隐式授权，旧 grant 不扩权。MCP 不提供确认；独立终端不是 OS 沙箱或真人身份证明。2026-10-29 扩展核对：测试[事务/确认](../../../tests/test_external_agents.py)、[真实 stdio](../../../tests/test_external_agents_stdio.py)、[业务维护](../../../tests/test_external_agent_business.py)、[业务 stdio](../../../tests/test_external_agent_business_stdio.py)、[迁移保留/回滚](../../../tests/test_external_agent_business_migration.py)。
- 名称重合不意味着共用 authority：[架构门禁](../../../tests/tool_metadata/test_deletion_gates.py) 只为独立 MCP 协议声明和精确路由节点提供 AST 角色例外，其他 Pilot 名称分类/反射仍按全源码扫描；[边界检查](../../../tests/tool_metadata/_external_mcp_boundary.py) 阻止 AI/Pilot authority 与外部 registry 的双向静态/动态引用。CLI 与 db 是合法 owner/存储装配入口，不借此给 Provider 传确认能力。

## 4. 图片与上下文

`ChatImageStore.upload` 先完整保存规范化文件，再发布数据库附件；`resolve_in_session` 校验作用域/内容，`bind_in_session` 将附件绑定消息并冻结引用；历史模型读取还检查尺寸/哈希及当前授权。已绑定图片跟随会话归属，未绑定草稿受创建范围限制。

图片 Provider 能力测试与真实会话调用分开；设置通过不代表历史消息完整性或授权通过。会话来源删除和暂时不可读也应分开调查，不能把临时锁错误解释成真实删除。

## 5. 症状和回归入口

| 症状 | 关键证据 | 测试 |
|---|---|---|
| 关窗/重连后重复或丢结果 | request_id、turn_id、generation、实例/游标、保存终态 | [managed API](../../../tests/test_managed_runtime_api.py)、[manager](../../../tests/pilot_runtime/test_managed_execution.py)、[transport](../../../tests/pilot_runtime/test_transport.py) |
| 确认重复/过期/结果未知 | conversation、operation ID、Pending 身份、参数摘要、ledger/receipt | [confirmation](../../../tests/pilot_runtime/test_confirmation.py)、[回执恢复](../../../tests/pilot_runtime/test_receipt_delivery_recovery.py)、[write operations](../../../tests/test_write_operations.py)、[Product Actions](../../../tests/product_actions/) |
| 上下文或工具越权 | context_type/ref、scope、source revision、catalog/执行权限 | [上下文门控](../../../tests/test_context_projector_source_gates.py)、[工具授权](../../../tests/tool_authority/)、[接纳围栏](../../../tests/test_runtime_admission_fences.py) |
| 模型超时仍有迟到内容 | owner/generation、deadline、预算、持久结果和物理 worker | [执行预算](../../../tests/pilot_runtime/test_execution_budget.py)、[源暂时不可读](../../../tests/test_runtime_source_unavailable.py) |
| 图片可看但模型读不到 | attachment/message ID、绑定会话、内容哈希、能力和实际模型输入 | [图片存储](../../../tests/test_chat_images.py)、[图片准入](../../../tests/test_image_vision_admission.py) |

运行日志分页和设置读取另见[设置地图](settings-desktop.md)。本页测试入口用于选择回归范围，不表示本次文档任务执行过这些业务测试。
