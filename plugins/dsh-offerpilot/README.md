# OfferPilot for DeepSeek Harness

本地 DSH 连接插件。复用 OfferPilot 的受限 MCP 和 DSH 官方 MCP client，提供投递、事件、简历、面试原始笔记和 Knowledge 工具；实际可见工具取决于用户已授予的权限。插件不附带 Python 后端，不创建数据库、授权或审批，不调用模型。

## 前提

- DSH **0.2.1-rc.5**，Node.js 22 或更高；本包锁定该预览版本，其他版本需重新验证。
- 已安装支持 `oc mcp` 的 OfferPilot 及其 `mcp` extra，且已正常启动完成数据库迁移。保留该 Python 环境。
- 已有自己的求职数据目录。不要让插件初始化空目录代替原数据。

## 连接和安装

在用户自己的终端中创建最小权限连接；以下 ID 和路径需要替换为自己的实际值：

```powershell
oc mcp connect dsh --application-id 12 --data-dir D:\OfferPilotData
oc mcp config <返回的connection_id> --format mcp --data-dir D:\OfferPilotData --output D:\OfferPilotConfig\mcp.json
```

输出目录应已存在，命令不会覆盖既有文件。默认只读投递与事件；按需创建带 `--allow-scheduling` 等显式权限的新连接。简历和 Knowledge 是独立全局授权，不能由投递权限推导。

从 [GitHub Release](https://github.com/offercontext/offerPilot/releases/tag/dsh-offerpilot-v0.1.0) 下载 `dsh-offerpilot-0.1.0.tgz`；同页的 `SHA256SUMS` 可用于核对下载文件。无需构建脚本或 npm 发布：

```powershell
dsh plugin --profile web add D:\Downloads\dsh-offerpilot-0.1.0.tgz
$env:OFFERPILOT_MCP_CONFIG = 'D:\OfferPilotConfig\mcp.json'
dsh web
```

macOS/Linux 在同一终端先执行 `export OFFERPILOT_MCP_CONFIG=/absolute/path/mcp.json`，安装和启动命令相同。路径按各自系统调整。环境变量仅对该终端及子进程生效；桌面端从其他入口启动时，可在 DSH 插件配置中设置 `configFile` 的绝对路径并重启。`configFile` 优先于环境变量。不要在公开插件包中放入个人配置。

如果已经加载旧的 `offerpilot.cordis.yml` overlay，先停止使用那个 overlay，再启用本插件；两者提供同一个 `mcp__offerpilot__*` 命名空间，不能重复加载。

## 从 DSH 插件市场安装

先完成上面的 OfferPilot 安装、连接授权和配置导出，再使用以下市场入口：

1. 在 DSH 的插件页面启用实验性「自定义插件市场」。
2. 打开「发现」→「添加索引源」，添加：

   ```text
   https://raw.githubusercontent.com/offercontext/offerPilot/chore/20261030-dsh-market/index.json
   ```

3. 搜索 `OfferPilot` 或 `求职`，选择 **OfferPilot 求职助手**，核对 `0.1.0`、GitHub 仓库与固定提交来源后安装。
4. 在插件配置中填入导出的 MCP JSON 绝对路径（`configFile`），然后重启 DSH；终端启动也可使用上面的 `OFFERPILOT_MCP_CONFIG`。

市场通过固定 Git 提交的 `plugins/dsh-offerpilot` 子目录安装，Release 提供同版本 `.tgz`。索引不是 DSH 内置市场，用户需要主动添加；版本不兼容时不能强行安装。本包已有 JavaScript 运行文件，无需批准安装时构建脚本。市场索引独立维护，后续版本会添加到同一索引地址。

## 使用

可以询问“列出我的投递”“本周有哪些面试”。如果已授予相关权限，也可读取简历、笔记和 Knowledge，或提出面试改期等业务维护请求。

写工具只生成 pending 提案。用户本人需在独立可信终端按返回提示运行 `oc mcp review`，核对内容后批准或拒绝。Agent 不代操作审批；只有回读到 `committed` 才能称为已保存。保留原 idempotency key 处理超时重试。当前没有插件内图形审批。

连接 UUID 是本机选择器，不是抵御同机任意代码执行的秘密凭据。同一系统用户的 shell/文件权限不在此插件的隔离保证内。

## 排错、停用和撤销

- 未配置、文件损坏或路径失效：插件启动失败，不回退到默认数据目录。重新用当前 OfferPilot 环境导出配置，并检查 `configFile`。
- 工具数量少：先核对连接的显式授权，不要手改 JSON 增权。
- 重复命名空间：检查是否仍加载旧 overlay 或另一份相同插件。
- 后端被移动/卸载：重新安装并导出配置；JSON 中记录了绝对 Python 路径。
- 停用或卸载插件后重启 DSH；卸载命令为 `dsh plugin --profile web remove dsh-offerpilot`。卸载不删除业务资料、不撤销既有授权、不取消 pending。
- 要阻止后续访问及旧请求批准，在独立终端执行 `oc mcp revoke <connection_id> --data-dir <实际目录>`。撤销和卸载是不同操作。

## 从源码打包

在本目录执行 `npm pack --pack-destination <已存在的输出目录>`。包仅包含运行时代码、配置层、说明和许可证，不包含数据、凭据、测试或 Python 环境。单元检查：`npm test`。

协议与审批契约在 OfferPilot 仓库的 `docs/architecture/external-agent-mcp.md` 维护。DSH 打包格式依据：[官方文档](https://deepseek-harness.github.io/deepseek-harness/develop/basic/publish)。
