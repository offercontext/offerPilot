# 固定 Windows 安装包的 UI 自验

状态：限定分支上的实验验证辅助工具。2026-10-07 扩展逐屏脚本已加入；下列新 pin 已由成功构建的 metadata、本地 ZIP 摘要/CRC 与安装器 SHA256 核验。真实安装 UI 尚待执行，不能把组件渲染的 36 项通过视为全界面验收。实现与 Linux 辅助单测不等于 Windows 执行通过；最终结论必须查看实际 UI run 的步骤和证据。此工作不改变应用源码、桌面安全配置、构建脚本或原完整回归结果。

## 固定输入与独立路由

- 源提交：`d853bd2eb117929e73530bb5036256801278b235`。
- 构建 run：[37630142396](https://github.com/offercontext/offerPilot/actions/runs/37630142396)。完整回归归属独立 run [37626960710](https://github.com/offercontext/offerPilot/actions/runs/37626960710)；本流程不重跑、不取消、不替代它。
- Artifact：`11487515170`，名称 `offerpilot-windows-ui-retry-d853bd2eb117929e73530bb5036256801278b235-16a8f2f7eb12350288caf15af29593cfdb6f583e`。
- Artifact 元数据摘要：`sha256:bcdcd7447bed7f0be11e51030c8cfc6aa8eba79eebdb3ae4f9fe5a4a6fc4ae53`。
- 安装包：`OfferPilot-Desktop-0.1.0-desktop.1-win-x64-setup.exe`。
- 安装包 SHA256：`09edde023a586cffb7212afe13337d37e9e02e8a9fb8647188ab90984a8bd004`。

schema 2 明确区分三种身份：`commit` 是产品源码；`buildCommit` / `buildWorkflow` / `runId` 是产出安装包的构建激活提交、工作流与 run；`fullRegressionRunId` 是独立完整回归的来源 run。当前产品提交为 `d853bd2eb117929e73530bb5036256801278b235`，构建激活提交为 `16a8f2f7eb12350288caf15af29593cfdb6f583e`，构建工作流为 `desktop-layout-retry.yml`，构建 run 为 `37630142396`，独立完整回归 run 为 `37626960710`。

普通构建要求 `buildCommit === commit` 且构建与完整回归 run 相同。限定重试工作流 `.github/workflows/desktop-layout-retry.yml` 要求产品与激活 SHA 不同、构建与完整回归 run 不同；GitHub 的 build run 与 artifact.workflow_run.head_sha 必须匹配 `buildCommit`，独立 full-gate run 的 head_sha 必须匹配产品 `commit`。只接受既有精确仓库/分支、push 事件及上述两个审核过的 workflow 路径，不能通过 request 添加任意工作流、分支或下载地址。

`source.json`、`result.json` 与 `coverage.json` 分别标注产品 SHA、构建 SHA/工作流/run 和 full-gate run；来源代码 checkout 及已安装 main/lifecycle 对比始终使用产品 `commit`，不是 helper 激活提交。安装包哈希、payload 字节校验、摘要与成功打包 job 检查不变。完整回归 metadata 仅校验来源归属，仍记 `not-certified-by-this-job`，不能从打包成功或本次 UI 通过推导 full-gate 通过。

`desktop/installed-ui/contract.mjs` 固定以上值。执行要求 `desktop/installed-ui/request.json` 的全部键和值精确匹配，不接受 URL、任意 run、输入参数或额外键；缺少请求文件直接失败。只读 job token 分别验证构建 run 与独立完整回归 run 的仓库、分支、head SHA、workflow 路径，再验证安装包 job 已成功，以及 artifact ID、名称、摘要、未过期状态与构建归属。完整回归是否通过不能从安装包 job 推导。

下载由官方 `actions/download-artifact@v4` 使用固定仓库、run 与名称完成。验证 metadata digest 并不伪装成本地重算 ZIP：下载 action 解压 artifact，执行前另对安装包字节计算硬编码 SHA256，任一不符都不执行。没有应用重建步骤。

首次建立 UI 路由时采用两次提交、两次独立 push 激活（现已完成；本轮仅更新既有 UI 辅助路径及精确 pin，可直接单次激活）：

1. 先提交 helper、独立 workflow、本文和原 workflow 的 3 条窄 `paths-ignore`；标题包含 `[skip ci]`，避免路由引导提交启动旧全量工作流。此提交不包含 `request.json`。先单独 push，并核对远端已出现该 bootstrap 提交且没有新增旧验证 run。
2. 确认第一步后，再仅加入经过审阅的 `request.json`，单独第二次 push，触发 `.github/workflows/desktop-installed-ui.yml`；旧 `desktop-windows.yml` 因忽略该路径而不重跑。

不能把这两个提交合并到一次 push：同一 push 的改动范围会包含旧 workflow 文件，从而重新触发旧全量验证。

两个 workflow 的 push 都只接受精确分支 `feat/20261005-windows-desktop-validation`，并保留可选 dispatch。独立 UI job 额外检查仓库和分支，dispatch 不含参数；默认分支尚无此 workflow 时，不保证 GitHub 手动入口可用。没有自动取消或自动替换已有 run。

原 workflow 仅忽略 `desktop/installed-ui/**`、`.github/workflows/desktop-installed-ui.yml`、本文，以及限定布局重试的 `desktop/layout-retry/**` 和 `.github/workflows/desktop-layout-retry.yml`。它自己的配置、产品源码、桌面 package/锁文件及所有构建路径仍触发原验证。UI workflow 的 push 仅监听上述三个路径；混合产品和 UI 改动会触发两个流程。路由单测覆盖这些情况。

## 执行内容

辅助依赖位于独立 package/lock：`playwright-core` 1.63.0、`@electron/fuses` 2.1.3、`@electron/asar` 4.3.1、`yaml` 2.8.1。只装该锁定包，使用 `npm ci --ignore-scripts`；不安装额外浏览器，不修改根 desktop 依赖。

1. 要求真实 Windows。真实 `%APPDATA%\OfferPilot Desktop` 在安装和首启前必须不存在；如果存在就失败，绝不删除或替换。应用主动设置 userData，因此既不伪造 APPDATA，也不用 `--user-data-dir`。
2. 核对安装包 SHA256；用 runner 已有 7-Zip 只读提取 NSIS 内嵌 `app-64.7z`。这份 payload 从已核对的安装包派生，不是假设旧 artifact 含有 manifest。
3. 使用 NSIS `/S /currentuser /D=<全新目录>` 安装，`/D` 最后且不加引号；目标位于 RUNNER_TEMP，路径包含中文和空格。要求安装退出码 0 且没有自动启动。
4. 检查实际安装的 exe、app.asar、冻结后端与 `_internal`、前端 assets、LICENSE；逐文件 SHA256 对比解包 payload。app.asar 的 main/lifecycle 另与固定源提交对比，源码文本仅将 CRLF 规范化为 LF；二进制 payload 对比始终严格逐字节。记录安装 exe 摘要、资源数量和版本核对结果。
5. 只读检查现有 `nodeCliInspect` fuse 已开启；若关闭则失败，不翻转 fuse。启动前后 exe 摘要必须一致。
6. 通过 Playwright `_electron.launch({ executablePath })` 启动真实安装 exe，临时使用 Node inspect/CDP。明确 `chromiumSandbox: true`、`bypassCSP: false`，不加入 `--no-sandbox`，不改变 app 的 devTools、webSecurity、Node integration、context isolation、sandbox、CSP 或权限处理。检查运行时保护值及 debug/backend 监听仅为 loopback。DevTools 禁用按下述严格行为探针验证。
7. 通过主进程 `process.pid` 取得实际 Electron PID，再用 Windows CIM 的 exe 路径、父 PID 和创建时间独立识别后端。Playwright `process()` 在 Windows 可能是 shell，不把它当 Electron PID，不信任后端自报 PID。
8. 先在空白 profile 扫描全部 13 个根页面，再从“添加第一条投递”进入表单，输入固定中文合成公司、岗位与备注，保留“准备投递”和“稍后补充 JD”。执行“核对并检查重复”，必须看到“未发现符合规则的重复记录”，再点击“确认保存”；不使用“仍然创建”兜底。之后通过公开 UI 创建额外合成记录，流程和边界见下节。不会配置 provider 或真实凭据。
9. 初始投递只保留这次 UI 发起 POST 回执的 ID、公司、岗位、备注、状态五个字段。核对详情标题和备注，返回上一层，经主导航“投递”进入“列表”，搜索并核对恰好一条、相同 ID 的记录。执行新增逐屏检查后，通过 UI 切换明暗模式，继续原来的关闭重启验收。
10. 正常调用窗口关闭路径，要求主进程、冻结后端及渲染进程全部退出，后台与临时调试端口关闭后才重新启动。重新打开同一安装 exe、真实 profile、相同保存端口；要求新主/后端 PID、创建时间以及相同记录 ID、中文详情与主题。
11. 再次正常退出并核对进程/端口清理与 exe 完整性。任何启动、保护、持久化、正常关闭或清理问题均为失败；失败后的清理不能改成成功。

### 逐屏与交互覆盖

`coverage-model.mjs` 列出 R01–R13 根页面、S01–S31 主要子界面及结果枚举；`screen-coverage.mjs` 驱动真实安装窗口中的控件。`coverage-recorder.mjs` 使用 `BrowserWindow.setContentSize` 设置 900、1008、1280、1440 内容宽度，并读取 `innerWidth/innerHeight` 核对；不使用浏览器 viewport 模拟、Vite fixture、React 状态注入、API 造数或数据库写入。

- 13 个根页面分别保留空白基线和已通过本地 UI 建立数据后的暗色四宽度截图；亮色根页面为 1280。大页面另滚动到下部截图。根页面的 PASS 是 `kind=visual`，只表示导航标记/可见内容/几何断言通过，不能计入功能通过数。
- 通过 UI 建立 11 条额外投递，加初始记录共 12 条。包括超长中文和不间断英文名称，验证搜索、分页、详情分段、Back 与实际 `popstate` 前进/后退。按真实 POST ID 定位，不假设 ID=1 或记录在第一页。
- 主要流程包括：添加校验/取消重开、JD 两版保存/历史、日程创建/编辑取消/保存、已完成合成面试与手动复盘、题目手动保存、简历分章/JSON 校验/保存重开/复制对比、知识粘贴导入/四个详情页、手动故事证据绑定/版本历史、两份 Offer 与薪酬算术/比较选项、Pilot 未发送草稿，以及设置中的安全只读/外观路径。
- 知识 V1 的 `api.py` 不注册 `on_extraction_succeeded` Brief 回调；正文导入是本地处理，仍核对入库响应 `brief_status=not_started`，不调用生成或 rebuild。简历“和 Haru 创建初稿”只建立空结构。所有数据使用 `QA-20261007-<run>` 标识；不访问 example.invalid 来源网址。
- 自然发生的 Haru 失败保留截图、失败属性与 fallback 尺寸；不会用 stub 替换 Live2D，也不放松 CSP。每次截图检查文档水平溢出和可见控件中心点是否被 Haru 截获。此类几何失败不能覆盖已有截图或改成通过。新增窄窗口真实左右键横滚检查，断言到达两端并确认右侧 Pilot 操作可触达。
- AI 会话、生成结果、语音/模型下载和备份下载/恢复以 BLOCKED 单列；不存在的 Help/Brief 为 N/A。原始诊断日志不截图、不上传，保留固定分类的 renderer/CSP/资源错误计数，各 case 记录增量。自己的 API 4xx/5xx/非正常传输失败会失败，只有源码明确支持的 GET material-kit 404 缺失记录例外；100 条证据上限不会截断独立错误计数。CSP/Haru/graphics 等受限运行错误记 BLOCKED，不把整体 runtime-health 写成通过。模型正常启动、语音端到端等能力不会因界面可见而被判定通过。

每个 case 记录 `surfaceId/caseId/uiPath/kind/outcome/assertions/screenshots`，每张截图记录实测 viewport、主题、合成记录 ID；顶层记录精确源码/安装包/实际 EXE 摘要。单个界面失败会保存白名单诊断和现场截图，然后继续独立界面；只在没有未决 UI 写请求时使用正常 reload 恢复。写请求传输失败会保留 sticky 未知结果屏障，阻止后续 UI 操作；不会将 requestfailed 当成已确认未写入。未走到的根页面/子界面在收尾列为 NOT RUN，不补造 PASS。

`coverage.json` 的 summary 区分 `visualPasses` 与 `functionalPasses`；存在 FAIL 时最终进程失败，即使后续生命周期检查通过。只有 BLOCKED/NOT RUN 时，运行状态为 `passed-with-coverage-limitations`，coverage 为 `incomplete`。`humanVisualReview=required-not-automated` 明确图片仍需人工检查，不把几何断言等同于“每个功能无问题”。根页面常规截图使用900px内容高度；关键子界面、长列表与横滚回归额外使用689px真实窗口内容高度，并记录实测尺寸。截图先结束有限CSS过渡再读几何，避免把切换动画当最终布局。Windows job 上限扩为 60 分钟，独立全量回归路由不变。

### Electron 44.5.1 的 DevTools 观测限制

固定版本的 [`SaveLastPreferences()`](https://github.com/electron/electron/blob/v44.5.1/shell/browser/web_contents_preferences.cc#L362-L383) 不返回 `devTools` 键，因此不能把 `getLastWebPreferences().devTools === undefined` 当成产品打开了 DevTools，也不能把 undefined 默认为 false。原 helper 对该 getter 的 false 断言会造成假失败。

本 helper 保留精确安装 payload/源入口匹配与 `app.isPackaged === true`，并用公开 API 做独立禁用探针：先采样 `isDevToolsOpened()` 和 `devToolsWebContents` 是否存在；注册 `devtools-opened` 监听后尝试 `openDevTools({ mode: 'detach', activate: false })`，固定观察 1 秒，再采样。前后打开状态、前后 contents 存在状态与 opened 事件五项必须全为 false；缺项、出现事件或创建 contents 都失败，不能靠随后关闭变成通过。监听在 finally 移除，失败仍走既有清理。官方固定版本 [`OpenDevTools()`](https://github.com/electron/electron/blob/v44.5.1/shell/browser/api/electron_api_web_contents.cc#L3201-L3245) 在禁用时直接返回；这不需要修改任何 app 保护设置。

getter 仍仅作为布尔/缺失枚举诊断保存；若它实际返回值，也必须为 false。所有安全观测先保存再断言，错误字段只用固定名称，不能输出原始 preferences。外部网络观测使用独立失败阶段，避免与安全属性混淆。此探针不把临时 CDP 测试启动变成正常无调试启动的证明。

正常关闭通过 `BrowserWindow.close()` 走窗口关闭与 app 的 before-quit 路径，不以强杀作为成功退出。失败才允许清理已由 CIM 证实身份的本次测试进程，清理前重新核对路径、父子关系及创建时间，防止 PID 复用。不会删除测试 profile；runner 生命周期负责最终环境销毁。

## 证据与不能声称的结论

成功与失败都尝试上传固定白名单：scope、源验证 JSON、结构化结果 JSON、coverage.json、原有 5 张生命周期截图和独立 screens/*.png。截图文件名只允许有限 ASCII 标识，不能用路径穿越扩大白名单。不会上传 userData、数据库、配置、真实求职材料、原始应用日志、token、headers、HAR、trace 或 debug websocket URL。异常只报告固定的细分失败阶段、白名单错误类别/代码和辅助命令退出码，不序列化可能含敏感信息的 Playwright/API 错误消息、堆栈或 stderr。

运行报告保留包装来源、真实进程身份、端口、保护布尔值、合成记录、已通过阶段与失败阶段。附件缺失、超时、取消、跳过或清理后仍有遗留进程都不是通过证据。截图只反映该测试的合成数据。

GitHub hosted Windows runner 通常使用管理员环境，UAC 已由平台关闭；本流程不更改 UAC、SmartScreen、Defender、沙箱或任何操作系统防护。它不能证明普通用户安装提示、UAC/SmartScreen 行为、没有开发工具的干净账户体验、桌面快捷方式或正常无调试启动已通过。临时 inspect/CDP 测试启动与正常用户启动必须分别描述。

本项补充“安装后实际 UI 保存与完整退出重启”证据，不是生产 release pass，也不覆盖真实 AI、语音、签名、升级/卸载、真实用户数据恢复或全部人工清单。原 [Windows 验证说明](desktop-validation.md) 的人工验收与发布义务继续适用。

## 本地辅助验证

```sh
npm ci --prefix desktop/installed-ui --ignore-scripts
npm test --prefix desktop/installed-ui
node --check desktop/installed-ui/smoke.mjs
node --check desktop/installed-ui/verify-artifact.mjs
node --check desktop/installed-ui/screen-coverage.mjs
node --check desktop/installed-ui/coverage-recorder.mjs
```

这些命令可在 Linux 检验 pin、错误传播、证据字段、进程归属/监听约束及 YAML 路由。PowerShell、NSIS、CIM 和真实 Electron UI 必须以 Windows run 结果验证，不用 Linux 结果替代。

官方行为依据：[Playwright Electron](https://playwright.dev/docs/api/class-electron)、[ElectronApplication](https://playwright.dev/docs/api/class-electronapplication)、[Electron fuse 只读 API](https://packages.electronjs.org/fuses/v2.1.1/functions/getCurrentFuseWire.html)、[NSIS 命令行](https://nsis.sourceforge.io/Docs/Chapter3.html)、[GitHub artifact 下载](https://github.com/actions/download-artifact)、[hosted runner 权限](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#administrative-privileges)、[跳过 push CI](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs)。
