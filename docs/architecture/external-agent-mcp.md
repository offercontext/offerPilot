# 外部 Agent MCP 接入

范围：读取投递、事件、简历、面试原始笔记和当前 Knowledge；提出投递维护、面试创建/改期、简历维护和笔记维护，独立用户确认后回读。完整授权时共 23 个工具。仅 stdio，不调用 OfferPilot 模型，不需要 DSH 专用原生插件。架构与信任边界见 [ADR-0016](decisions/0016-expose-scoped-external-agent-mcp.md)。

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

默认仅可读取范围内投递及事件和查询/取消自己的请求。要读取全部投递，用 `--workspace` 替换所有 `--application-id`；两者必选其一且不能并用。workspace 不自动授予简历、面试笔记或 Knowledge 权限，旧连接升级后也不会增加新能力。

| 显式授权参数 | 工具与范围 |
|---|---|
| 无额外参数 | `list_applications`、`get_application`、`list_events`、`get_event`、`get_request`、`cancel_request` |
| `--allow-scheduling` | `propose_interview`、`propose_reschedule`；仅已授权投递 |
| `--allow-application-create` | `propose_application_create`；必须同时 `--workspace` |
| `--allow-application-writes` | `propose_application_update`、`propose_application_delete`；仅已授权投递 |
| `--allow-resumes` | `list_resumes`、`get_resume`；**独立的全局简历库读取权**，不受 application-id 集合过滤 |
| `--allow-resume-writes` | `propose_resume_create/update/copy/delete`；必须同时 `--allow-resumes` |
| `--allow-notes` | `list_notes`、`get_note`；仅已授权投递的面试原始笔记 |
| `--allow-note-writes` | `propose_note_create/update/delete`；必须同时 `--allow-notes` |
| `--allow-unbound-notes` | 额外包含未绑定投递的笔记；必须同时 `--workspace --allow-notes` |
| `--allow-knowledge` | `search_knowledge`；**独立的全局 Knowledge 读取权**，含当前已确认 Note 与独立 Evidence |

写授权只允许提案，所有保存仍需独立确认。按实际需要选择最小授权；例如限定两份投递、读取其笔记并允许维护：

```powershell
oc mcp connect assistant --application-id 12 --application-id 18 --allow-notes --allow-note-writes --allow-application-writes
```

额外授予全局简历或 Knowledge 时，即使仍指定 application-id，也会允许读取全局资料。模型不能在工具参数中追加 grant、source_ids、confirmed 来扩大权限。

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
6. 外部 Agent 调用 `get_request`。只有 `status=committed` 才表示已保存；返回冻结的提交回执。再用对应的 `get_*` 读取当前业务记录，区分提交后被用户进一步编辑的情况。删除回执保留状态，但隐藏来源后不再返回旧正文。

其他维护使用相同的 pending → 独立审阅 → 回读链。更新、复制、删除携带 `get_*` 的 `expected_revision`，只修改显式提供的字段：

- 投递创建仅保存基础信息，不接收 initial JD，不生成 HTTP 专属 JD intake receipt，也不提供重复岗位提示。外部请求表与投递在同事务形成幂等回执。关闭必须提供原因，closed 不能重开。删除为软删除，预览列出关联表数量并冻结关联内容摘要；提案后新增或修改关联内容时批准冲突。
- 简历仅支持文本/结构化内容维护，不上传或下载文件，不返回本地路径。创建时非空 text 优先，否则采用 content_json.raw_text；结构化更新未给 raw_text 时保留正文和解析状态。第一份默认主简历；不允许取消唯一主标记或删除非空主简历。主标记变化冻结整个活动简历库，空主简历删除的接任人会写入预览及回执。
- 原始面试笔记与 Knowledge Note 是不同对象。事件绑定必须是同投递的面试且一场只能绑定一份笔记；不能改投递归属，显式 application_event_id=null 可解绑。修改/删除笔记或隐藏父投递后，已捕获 Knowledge 因来源指纹/可见性变化停止检索，预览会说明，不会自动重新捕获。
- Knowledge 复用当前双通道召回和完整性检查，保留来源、版本、hash，不生成或写入知识、摘要或搜索 trace。limit 最多 20，max_bytes 为完整条目输出预算；超大条目整条省略并报告 omitted_count，不截断引用和 hash。预算不限制 SQL 排序扫描成本；共用召回最多构建 24 个 Note 和 24 个 Evidence，每个 Note 最多 160 条引用。此处接受既有有界构建成本，不宣称小输出预算能降低所有检索成本。

MCP 工具没有 confirm。不要让模型执行独立审阅命令或把终端交给模型代操作。TTY 只阻止意外自动化，不能识别真人；拥有同一 OS 用户下任意 shell、Python 或数据目录权限的 Agent，可以绕过整个本机应用。此版本没有 OS 隔离或用户存在性认证，不能宣称防住该威胁；需要强隔离时使用独立系统身份/受限运行环境和单独的用户审批通道。

## 4. 错误、重试与取消

MCP 结果同时包含文本 JSON 和 `structuredContent`，逐工具发布输入/输出 schema：

```json
{"ok": false, "data": null, "error": {"code": "service_unavailable", "retryable": true}}
```

| 情况 | 行为 |
|---|---|
| 缺少授权、越界、未知/撤销连接 | `capability_not_granted`、`scheduling_not_granted`、`scope_denied`、`connection_unavailable`；不写入 |
| schema 错误、额外 confirmed/authority 参数 | `invalid_input`；不接受模型自报确认 |
| 原 key 同输入重复 | 回放原请求和终态；不重复创建事件 |
| 原 key 异输入 | `idempotency_conflict`；不可把新意图附到旧 key |
| 提案前 revision 不符 / 不是该投递的面试 | `event_changed` / `interview_required` |
| 业务资源 revision 不符 / Knowledge 来源损坏 | `resource_changed` / `knowledge_integrity_failed`；不覆盖或返回损坏证据 |
| 确认前来源改变、事件删除或父投递隐藏 | `conflict` 或明确的不可见错误；不覆盖当前记录 |
| 父投递隐藏后查询/取消旧请求 | owner 仍可查看状态和取消 pending；`source_available=false`，不回传冻结事件正文 |
| 拒绝、取消、过期 | `rejected` / `cancelled` / `expired`；后续批准不执行 |
| 重复或并发批准 | 原子返回同一 committed 回执，只创建/修改一次 |
| MCP 超时、连接断开或取消通知 | 可能未收到已保存提案的结果；使用**原 key 和原输入**恢复，不能换 key 重做 |
| 显式 `cancel_request` | 仅取消 pending；终态返回原状态，committed 不会被撤销 |
| DB 锁定/不可用 | `service_unavailable`，有界等待；原事务回滚或通过原请求查询结果，不隐式重试 |

用户撤销连接：`oc mcp revoke <connection_id> --data-dir <实际目录>`。这会同时阻止该连接后续读取、提案和旧请求批准；同事务把未到期 pending 请求终止为 cancelled，已到期请求保留 expired 语义。重复撤销无副作用。没有后台自动批准，也不继承 `chat_auto_approve_writes`。

普通业务列表/详情使用短读事务，可与 SQLite RESERVED 写锁并存；请求查询会持久化过期状态，因此声明为有状态工具。提案、取消、审阅和批准使用有界写锁。

升级在正常应用初始化时执行 0036：为旧连接增加默认空 capability，事务内重建请求 action 约束并允许全局资源的 application_id 为空。重建前后逐行核对旧请求，失败回滚；旧事件提案的冻结 grant/digest 不变。不删除业务数据。服务进程不会代为初始化或升级数据库，升级前先备份，再用新版应用正常启动。

## 5. 验证边界

回归入口：

```powershell
uv run --extra mcp pytest tests/test_external_agents.py tests/test_external_agents_stdio.py -q
uv run --extra mcp pytest tests/test_external_agent_business.py tests/test_external_agent_business_stdio.py tests/test_external_agent_business_migration.py -q
uv run --extra mcp ruff check src/offerpilot/external_agents tests/test_external_agents.py tests/test_external_agents_stdio.py
uv run --extra mcp mypy src/offerpilot/external_agents src/offerpilot/cli.py src/offerpilot/db.py
```

测试覆盖事务、权限、幂等、拒绝/取消/过期、来源冲突、重启回执、并发、故障回滚、独立终端输入、真实官方 SDK stdio 往返及取消后的同 key 恢复。测试的操作员输入由测试模拟，不是真人身份认证。

安装门禁 `scripts/release-gate.ps1 -Install` / `scripts/release-gate.sh --install` 检查普通安装不依赖 MCP，再安装 mcp extra 并从安装环境通过真实 stdio 准备/回读 pending。门禁不批准请求；发布门禁调用时保留安装环境、数据和配置，失败也保留，路径输出到日志。与完整后端分片证据、前端、CLI/HTTP smoke 和 local verify 一起报告，不能只报局部测试。

DSH overlay 是实际配置产物；DSH 客户端组件往返、完整 DSH profile 启动、真实模型会话、用户手工确认和发布 gate 是不同验证层。交付时按实际命令分别报告，不用配置解析或组件测试冒充完整 DSH 用户验收。无需付费模型即可验证 stdio 与 DSH bridge。

## 6. 首版手工试用验收

使用一个**不存在的新目录**；准备脚本拒绝复用已有目录，避免初始化用户数据库：

```powershell
uv run --extra mcp python scripts/external-agent-acceptance.py prepare --data-dir D:\temp\offerpilot-manual-acceptance-1
```

脚本只创建虚构投递、连接配置和一个 15 分钟有效的 pending 请求，并经真实 stdio 检查日程仍为空。它会输出用户应在独立可信终端执行的完整审阅命令。**由用户本人**打开该终端、核对公司/岗位/北京时间 2026-11-01 15:00（UTC 07:00）/时长 60 分钟，再输入指定的完整批准短语。Agent 不代输入，不把自动化审阅测试标成人工验收。

确认后执行：

```powershell
uv run --extra mcp python scripts/external-agent-acceptance.py verify --data-dir D:\temp\offerpilot-manual-acceptance-1
```

必须得到 committed、且回执与实际日程列表完全一致、仅有一场面试。未确认时 verify 会失败；拒绝时可加 `--expected rejected`，仍须没有日程。超过 15 分钟需使用另一个新目录重做，旧尝试保留 expired/未知记录，不能改记成功。

第二款实际客户端可用 Claude Code 的 `claude mcp add --scope user ...` / `claude mcp list` / `claude mcp get offerpilot` 检查连接；PowerShell 中用 `claude.cmd`，以免 `.ps1` 包装吞掉 stdio 子命令的 `--`。新增配置应在独立 `CLAUDE_CONFIG_DIR` 中试验，不覆盖既有设置。客户端接入方式见 [Claude Code 官方 MCP 文档](https://code.claude.com/docs/en/mcp)。

本轮另以实际 Claude Code 2.1.284 可执行程序、禁用原生工具和仅绑定本机脚本 Provider 的方式执行了 MCP 发现与投递读取；这是第二款真实客户端的协议/工具运行验证，不是另一份 Python SDK 脚本，也不是真实模型能力或真人批准验收。DSH 0.2.1-rc.2 的官方 bridge、profile 启动和业务链路验证与此分别记录。

开发验收另在用户明确授权后，由 Agent 通过真实 PTY 操作独立审阅命令，并经 MCP 回读 committed、核对唯一事件。该证据标记为“用户授权下的自动化终端验收”；用户亲自操作终端未执行，不能把前者重标为真人交互验收。
