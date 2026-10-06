# Windows 桌面验证包

状态：未签名、人工验收用 pilot，不是生产发行版。目标是 Windows x64 上复用现有 React 和 Python/FastAPI 后端，验证安装、启动、写入和重启恢复；没有改写业务后端。本文件是执行说明，不代表验收已通过。

## 组成与边界

- `desktop/main.cjs` 启动一个 PyInstaller 后端子进程，等待 ready 协议及健康检查，再打开 Electron 窗口。
- Python 仍使用 `uv.lock` 的应用依赖。打包工具通过 `uv run --with` 单独固定为 PyInstaller 6.16.0、hooks-contrib 2025.9，不加入运行时依赖。这是一组固定的验证工具版本，不宣称是最新版；选择固定版本是为了便于复现本次构建，升级须重新执行冻结进程与目标 Windows 验收。
- `desktop/backend.spec` 收集 LiteLLM 数据、动态 provider 模块、依赖 metadata、tiktoken namespace 插件、SQLite dialect 和 uvicorn 动态实现。构建预热并打包 OpenAI tokenizer 缓存；下载或校验失败会阻止构建。固定 Knowledge tokenizer 为原来的 cl100k_base。
- 冻结入口使用随包 LiteLLM 模型价格表，避免启动时访问远端价格表。真实 AI 请求仍需要用户自行配置 provider、网络与凭据，未由此验收覆盖。
- Electron 使用 sandbox、context isolation、关闭 Node integration；会话密钥由主进程生成并注入本地请求，不写进 URL 或应用配置。后端只监听 `127.0.0.1`，校验 token、Host 和 Origin，普通外部浏览器不能直接访问。
- 首次由操作系统分配端口，随后保存并复用，保留同源 localStorage。已保存端口被占用时启动失败并提示，不能偷偷更换端口丢失前端状态。单实例避免两个桌面后端同时拥有工作区。
- 此 pilot 禁用弹窗、下载、麦克风权限及跨源页面导航；CSP 限制远端浏览器资源。因此语音、外部模型/素材、导出和外链流程不属于已支持验收范围。既有 Web/CLI 运行方式不受此桌面限制影响。
- 未提供代码签名、自动更新、升级/降级兼容保证或生产分发。后端 token 防护不能抵御同一 Windows 用户下的恶意程序；数据和 provider 配置仍是本机文件，不是加密保险库。

## 文件布局

构建输入：`src/offerpilot/`、`web/dist/`、`desktop/`。冻结输出：

```text
desktop/backend-dist/offerpilot-backend/offerpilot-backend.exe
desktop/backend-dist/offerpilot-backend/_internal/...
```

NSIS 安装包及 SHA256 输出：`desktop/dist/*-setup.exe`、`desktop/dist/*-setup.exe.sha256`。
安装后的只读资源为 `resources/backend/`、`resources/web/`、`resources/LICENSE`；不能只复制后端 `.exe` 而丢掉同目录 `_internal/`。

Windows 桌面数据独立存放于 `%APPDATA%\OfferPilot Desktop\`：

- `data\data.db`、`data\config.json` 及其他后端工作区文件
- `desktop-port.json` 保存本地端口；Electron persistent session 保存同源浏览器状态
- `desktop.log` 和轮转日志记录启动/退出诊断；不要向第三方发送含个人内容的完整日志或配置

不自动迁移原有 `~/.offerpilot`，也不复用安装目录写数据。使用干净账户验收；如需迁移真实数据，先单独备份并审查迁移方案。NSIS 配置保留卸载时的应用数据；其实际行为仍需人工验收。

## Windows 构建

使用 Windows x64、Python 3.12、uv 0.12.19 和 Node 22；构建机需要下载锁定依赖、Electron、NSIS 及 tokenizer 文件的网络。最终安装用户不需要 Python、Node 或 uv。

仓库根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File desktop/validate-windows.ps1
```

该脚本依次运行锁定依赖安装、仓库完整本地 release gate（含 `-Install`）、桌面生命周期测试、前端构建（包含在 release gate）、冻结后端、真实冻结进程 smoke、NSIS 打包、打包资源副本 smoke 及 SHA256 生成；任一步失败立即停止。`-SkipInstall` 仅跳过初始依赖安装，不跳过 gate。

单独重建冻结后端及 smoke：

```powershell
uv run --frozen --with pyinstaller==6.16.0 --with pyinstaller-hooks-contrib==2025.9 python desktop/build-backend.py
uv run --frozen python desktop/smoke-backend.py --backend desktop/backend-dist/offerpilot-backend/offerpilot-backend.exe --static-dir web/dist
npm.cmd run build:win --prefix desktop
```

开发启动先完成 `uv sync --frozen`、两个 npm 项目的 `npm ci` 和 `npm run build --prefix web`，再运行 `npm start --prefix desktop`。Windows 下网页依赖安装可设置 `$env:ONNXRUNTIME_NODE_INSTALL="skip"`，跳过此浏览器包不使用的 Node CUDA 下载；不等于语音功能已验收。

PyInstaller 必须在目标平台构建。在 Linux 构建并 smoke 通过，仅证明 Linux 冻结依赖与协议，不是 Windows `.exe` 或 NSIS 证据。

## 限定分支 CI：验证包与完整发布回归

`.github/workflows/desktop-windows.yml` 只对精确分支 `feat/20261005-windows-desktop-validation` 的 push 自动运行，同时保留可选 `workflow_dispatch`；不响应 master、其他分支、PR 或 release。推送该验证分支会启动 Windows 构建，执行前须有明确的“推送分支并运行工作流”授权。首次可通过这个限定分支 push 激活，无需为此修改默认分支。具体执行结果以对应 commit 和 run 的记录为准，本说明不声明任何尚未完成的检查通过。

可选人工运行入口受 GitHub 平台限制：`workflow_dispatch` 工作流需要先存在于默认分支；新功能分支上的同名配置并不自动满足这个条件。不为启用人工入口而自动写默认分支。见 [GitHub 手动运行说明](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)。也可以直接在 Windows 运行前面的本地脚本。

CI 使用两个相互独立的 Windows job，避免完整产品回归尚在执行时阻塞实验安装包构建：

1. `Experimental installer and desktop smoke`：锁定依赖安装后，必须通过 `tests/test_desktop.py`、`tests/test_auth_api.py`、`tests/test_static_frontend.py`，Node 桌面生命周期测试、全仓 ruff、桌面入口及 API 组合层 mypy、前端构建、PyInstaller 冻结、严格冻结进程 smoke、`electron-builder --win nsis --x64 --publish never` 及打包资源副本 smoke，才生成 SHA256 并上传 installer。任一步失败都不能上传安装包。该 job 不依赖完整回归，最长 60 分钟。
2. `Full release regression (required for release)`：原样执行 `scripts/release-gate.ps1 -Install`，保留完整未分片 pytest、ruff、mypy、前端测试和构建、真实 CLI/HTTP smoke、`oc verify --profile local` 与安装检查；不删测试、不因实验包成功而豁免，最长 90 分钟。保留 gate 原始输出，不通过环境变量改变 pytest 或嵌套 pytest 的参数；每分钟输出已运行时长与最近日志，并保存完整 stdout/stderr，便于观察长时间运行阶段。
3. 最后的 `Validation results (both jobs must pass)` 汇总两个真实结论。任一失败、取消或跳过均不能成为成功；超时同样没有通过证据。只有两个 job 都成功才能通过此状态检查，且仍不代表生产发布就绪。

安装包 artifact 名含 `experimental-validation`，附 `VALIDATION-NOTES.txt`，明确未签名、仅供验证、不是生产发行版；记录 commit、run 链接与 attempt，并要求查看同一 run 的独立完整回归结果。完整 gate 仍在运行或失败时，实验包可能已可下载，不能据此宣称完整验收通过。完整发布回归通过仍是发布就绪的必要条件，不能由局部检查替代。

每个 job 保存分阶段日志，实验包 job 另保存专项 pytest 的 JUnit 报告；成功与失败均尝试上传证据；强制取消或超时可能截断报告，需同时查看 GitHub 原始日志，缺失的报告不是通过证据。最终状态也独立保存。Artifact 保留 14 天、可见范围继承仓库设置，不创建 release 或发布到下载站。未配置自动取消或替换已有 run；启动后续 run 前先核对当前运行状态。取消旧 run 需要明确授权，并保留其实际取消状态及可取得的日志，不能将取消算作通过。

前面的本地 `desktop/validate-windows.ps1` 保持串行完整 gate 行为，不提供跳过完整回归的发布捷径。CI 独立构建只是尽早取得实验安装包；安装程序执行、Electron 渲染、UI 保存与重启仍必须完成下方人工验收。

## 自动 smoke 的证据范围

`desktop/smoke-backend.py` 从含空格及中文的临时目录启动真实冻结文件，移除 Python 路径环境，验证：

1. LiteLLM 入口导入与已缓存 tokenizer 可用；常见 HTTP 代理指向不可达地址，帮助发现意外下载，但这不是网络隔离证明。
2. ready 协议、子进程 PID、loopback origin、健康检查、SPA fallback 与实际前端 assets 读取。
3. 无 token、错误 token、恶意 Origin/Host、无凭据写入均被拒绝。
4. 经鉴权的 application 写入 SQLite；结束进程、用同数据目录和端口重新启动后读取一致。
5. 重启时换新 token，旧 token 被拒；关闭父 stdin 后正常退出且不遗留监听端口。
6. 无效 token 与已占用端口启动失败时，即使父 stdin 仍打开也能退出，输出结构化错误，不能先报告 ready。

这是 API/进程集成检查，不能替代安装程序、Electron 渲染、窗口交互或 UI 保存/重启。

## Windows 人工验收清单

由验收人在没有开发工具依赖的干净 Windows 账户或 VM 上执行。使用全新测试数据，不使用真实求职材料或 API key；未运行项保留未勾选。记录 Windows 版本、安装包 SHA256、commit、实际结果及问题截图。

- [ ] 核对 SHA256；安装包签名状态应为未签名。记录 SmartScreen/杀毒行为，若安全产品阻止安装，停止并报告，不将关闭防护作为通过条件。
- [ ] 普通用户完成 NSIS 安装，自选含空格的目录，桌面快捷方式可用；不需安装 Python/Node，不要求启动外部终端。
- [ ] 首次启动显示真实主界面，主要导航和本地静态素材可见，无空白窗口；通过 UI 完成必要首次引导。
- [ ] 用 UI 创建一条名称含中文的投递，保存后列表与详情一致。完全退出桌面 app 后重新启动，再从 UI 查到同一记录和内容。
- [ ] 验证引导/本地前端状态重启后保留，不重复无端重置；记录实际端口与数据位置，安装目录不新增数据库。
- [ ] 重复点击快捷方式只聚焦已有窗口，没有第二个写入进程；关闭窗口后 Task Manager 无遗留 OfferPilot 后端。
- [ ] 立即退出/重开至少三次，检查稳定端口可重新绑定；再验证有未结束连接时退出。Windows 独占 socket 在连接未完全释放时可能暂不能重用，不能以关闭独占保护作为修复。见 [Microsoft socket 说明](https://learn.microsoft.com/en-us/windows/win32/winsock/so-exclusiveaddruse)。
- [ ] 通过 UI 保存一条未配置真实 AI 的数据，显示可理解的未配置 AI 状态；无模型调用和费用。
- [ ] 测试副本中占用已保存端口，启动应显示明确错误并退出；释放占用后正常启动，原数据仍在。不要删除工作区来“修复”。
- [ ] 测试副本中临时移走后端文件/静态入口，启动应报错而非无限等待；恢复后可重新打开，数据未损坏。
- [ ] 杀掉后端子进程，窗口应提示并退出；再次启动可读已保存记录。测试前先备份，强制终止不保证未完成写入。
- [ ] 断网启动仍能打开主界面和既有本地数据；基础 CRUD 可用，外部 AI/语音不计入此项。
- [ ] 卸载后检查声明保留的数据；重装可恢复测试记录。只对隔离测试数据执行，生产迁移/回滚另行设计。

生产发行前仍需：目标 Windows 真机/UI 全项证据、代码签名与分发信任、权限及网络复审、第三方 license/素材分发核对、升级迁移及恢复策略、敏感配置存储评估，以及明确支持的功能矩阵。仓库既有验收义务见 [AGENTS.md §7](../../AGENTS.md#7-验证与-code-review)。
