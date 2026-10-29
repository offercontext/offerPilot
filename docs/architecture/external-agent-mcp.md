# 外部 Agent MCP 接入

首批范围：读取投递和事件，提出面试创建/改期，独立用户确认后回读。仅 stdio，不调用 OfferPilot 模型，不需要 DSH 专用原生插件。架构与信任边界见 [ADR-0016](decisions/0016-expose-scoped-external-agent-mcp.md)。

## 1. 安装和明确授权

源码安装：

```powershell
uv sync --frozen --extra mcp
uv run oc start
```

先用新版应用正常启动一次，完成新增表的迁移；此后可以退出 HTTP 应用，stdio 服务独立工作。使用已安装的 wheel 时安装其 `mcp` extra，例如 `pip install "offerpilot-0.1.0-py3-none-any.whl[mcp]"`。官方 Python MCP SDK 约束为 `>=1.26,<2`，仓库 lock 固定当前解析版本。普通安装不强制依赖 MCP。

以下命令使用当前 `OFFERPILOT_DATA`，默认 `~/.offerpilot`。要连接其他工作区，在每条命令追加同一 `--data-dir`；桌面测试版数据目录与源码版不同，不要误连。服务要求既有 `data.db`，不会创建空工作区或重置业务数据。

用户在独立终端创建连接：

```powershell
oc mcp connect dsh --application-id 12 --application-id 18
```

默认只读；要允许面试提案，创建时显式加 `--allow-scheduling`。要读取整个工作区，用 `--workspace` 替换所有 `--application-id`；两者必选其一且不能并用。范围授权允许读取其中的岗位、笔记与事件，不能将“接入外部 Agent”理解为默认授权全部资料。

命令返回 `connection_id`。连接 UUID 是本机进程选择器，不是能抵御同机代码执行的秘密认证凭据。权限不可由 MCP 参数扩大；需要新范围时另建连接，旧连接可以撤销。

## 2. DSH 配置

把返回的真实连接 UUID 代入：

```powershell
oc mcp config <connection_id> --format dsh --output offerpilot.cordis.yml
dsh web --patch ./offerpilot.cordis.yml
```

生成的是可直接加载的 Cordis `insert` overlay（JSON 是合法 YAML），包括绝对 Python 路径、数据目录、连接 ID、stdio transport 和超时；不会覆盖已有文件。必须保留该 Python 环境及其 OfferPilot 安装。配置里没有审批工具、审批 token 或模型凭据。

DSH 使用其通用 `@deepseek-ai/dsh-mcp-client`，工具名形如 `mcp__offerpilot__list_applications`。不要把 overlay 当作可安装的 OfferPilot 原生插件包，也不要覆盖 DSH 现有用户 patch；用 `--patch` 追加即可。

兼容性依据固定到官方源码 `f3803c96974ad4a94195fb97e5c637e9c2a3afe3`，npm 组件验证目标为 `@deepseek-ai/dsh-mcp-client@0.2.1-rc.2` / `@deepseek-ai/dsh@0.2.1-rc.2`。master 快速变化，其他版本需重新验证。官方此快照支持 stdio / Streamable HTTP，不支持 elicitation / task execution；本服务不依赖这些未支持能力。

其他客户端：

```powershell
oc mcp config <connection_id> --format mcp --output offerpilot-mcp.json
```

生成常见 `mcpServers` 配置。将其中 command/args 合并到客户端实际维护的位置；不同客户端的配置根结构可能不同。

## 3. 完整操作链

1. `list_applications` / `get_application` 找到授权范围中的投递。
2. `list_events` 可按 application_id 和 event_type 筛选；列表按 ID 分页，`next_after_id=null` 表示结束。读取某场面试用 `get_event`，保留返回的 revision。
3. `propose_interview` 创建面试提案；`propose_reschedule` 用原 event_id、application_id 和 expected_revision 提出改期。时间必须带时区，结果统一 UTC。改期保留 subtype、tags、round、notes、status，并将提醒保持原提前量。完整前后值在用户预览中可见。
4. 提案只产生 pending 请求，不生成或修改日程。输入必须带规范 UUID 格式的 `idempotency_key`；请求含 `request_id`、15 分钟有效期、摘要及独立审阅命令。模型结果不回传本地数据目录；操作员从自己的连接配置核对目录。
5. **用户本人在独立可信终端**执行返回的 `oc mcp review <request_id> --data-dir <实际目录>`。核对公司、岗位、原安排和拟保存安排；按提示输入 `approve <完整request_id>` 或 `reject <完整request_id>`。不提供 `--yes`、管道或确认布尔值。关闭终端、Ctrl+C、错误短语都不会批准。
6. 外部 Agent 调用 `get_request`。只有 `status=committed` 才表示已保存；返回冻结的提交回执。再用 `get_event` 读取当前业务记录，区分提交后被用户进一步编辑的情况。

MCP 工具没有 confirm。不要让模型执行独立审阅命令或把终端交给模型代操作。TTY 只阻止意外自动化，不能识别真人；拥有同一 OS 用户下任意 shell、Python 或数据目录权限的 Agent，可以绕过整个本机应用。此版本没有 OS 隔离或用户存在性认证，不能宣称防住该威胁；需要强隔离时使用独立系统身份/受限运行环境和单独的用户审批通道。

## 4. 错误、重试与取消

MCP 结果同时包含文本 JSON 和 `structuredContent`，逐工具发布输入/输出 schema：

```json
{"ok": false, "data": null, "error": {"code": "service_unavailable", "retryable": true}}
```

| 情况 | 行为 |
|---|---|
| 缺少授权、越界、未知/撤销连接 | `scheduling_not_granted`、`scope_denied`、`connection_unavailable`；不写入 |
| schema 错误、额外 confirmed/authority 参数 | `invalid_input`；不接受模型自报确认 |
| 原 key 同输入重复 | 回放原请求和终态；不重复创建事件 |
| 原 key 异输入 | `idempotency_conflict`；不可把新意图附到旧 key |
| 提案前 revision 不符 / 不是该投递的面试 | `event_changed` / `interview_required` |
| 确认前来源改变、事件删除或父投递隐藏 | `conflict` 或明确的不可见错误；不覆盖当前记录 |
| 父投递隐藏后查询/取消旧请求 | owner 仍可查看状态和取消 pending；`source_available=false`，不回传冻结事件正文 |
| 拒绝、取消、过期 | `rejected` / `cancelled` / `expired`；后续批准不执行 |
| 重复或并发批准 | 原子返回同一 committed 回执，只创建/修改一次 |
| MCP 超时、连接断开或取消通知 | 可能未收到已保存提案的结果；使用**原 key 和原输入**恢复，不能换 key 重做 |
| 显式 `cancel_request` | 仅取消 pending；终态返回原状态，committed 不会被撤销 |
| DB 锁定/不可用 | `service_unavailable`，有界等待；原事务回滚或通过原请求查询结果，不隐式重试 |

用户撤销连接：`oc mcp revoke <connection_id> --data-dir <实际目录>`。这会同时阻止该连接后续读取、提案和旧请求批准；同事务把未到期 pending 请求终止为 cancelled，已到期请求保留 expired 语义。重复撤销无副作用。没有后台自动批准，也不继承 `chat_auto_approve_writes`。

普通业务列表/详情使用短读事务，可与 SQLite RESERVED 写锁并存；请求查询会持久化过期状态，因此声明为有状态工具。提案、取消、审阅和批准使用有界写锁。

## 5. 验证边界

回归入口：

```powershell
uv run --extra mcp pytest tests/test_external_agents.py tests/test_external_agents_stdio.py -q
uv run --extra mcp ruff check src/offerpilot/external_agents tests/test_external_agents.py tests/test_external_agents_stdio.py
uv run --extra mcp mypy src/offerpilot/external_agents src/offerpilot/cli.py src/offerpilot/db.py
```

测试覆盖事务、权限、幂等、拒绝/取消/过期、来源冲突、重启回执、并发、故障回滚、独立终端输入、真实官方 SDK stdio 往返及取消后的同 key 恢复。测试的操作员输入由测试模拟，不是真人身份认证。

DSH overlay 是实际配置产物；DSH 客户端组件往返、完整 DSH profile 启动、真实模型会话、用户手工确认和发布 gate 是不同验证层。交付时按实际命令分别报告，不用配置解析或组件测试冒充完整 DSH 用户验收。无需付费模型即可验证 stdio 与 DSH bridge。
