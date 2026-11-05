# Windows 桌面中英文验证包

状态：限定范围的独立 CI 构建入口；未签名，不是生产发行版，也不代表完整已安装桌面 UI 或完整发布验收通过。本文描述工作流执行范围，不声明尚未完成的 Windows run 通过。

## 来源与隔离

[desktop-localization.yml](../../.github/workflows/desktop-localization.yml) 只自动响应 `feat/20261019-localization-integration` 上相关桌面、前端、后端、测试、锁文件或本工作流/说明的 push。另提供 `workflow_dispatch`；新工作流仅在功能分支时，手动入口受 GitHub 默认分支注册限制，不为此自动修改 master。首次构建可由已授权的限定分支 push 启动。`desktop/localization-ui/**` 未纳入 package push 路径：仅修 harness 的提交不会重复冻结/构建；如真实 run 需要重试，再建立明确绑定该新 run、artifact、source SHA 和 installer SHA256 的独立同包重试入口，不预设或使用旧 PIN。

本次整合基于 `8fdc4f0aecf9612731bdd0590bbd0966af873279`，逐项移植网页本地化 `f6c19f1a` 至 `02b35bc6` 和桌面 `05fcd54a` 的语言增量。状态消息修复 `e74fcfa7` 与网页 `64a18a76` patch-id 相同，且最新主线已含其行为，不重复应用。保留新主线 Haru X 收起回角色、右键隐藏/置顶、默认置顶与显式 false 偏好。

每次运行从当前 checkout 新构建，记录完整 source SHA、ref、run、attempt 和锁文件 SHA256。历史运行与旧 installer 不作为本次通过证据。公开 `v0.1.0-desktop.1` tag 和附件不在本任务修改范围。

- 只有一个 Windows job；`contents: read`，没有并发取消或自动替换运行。
- 不修改或调用旧 [desktop-windows.yml](../../.github/workflows/desktop-windows.yml) 的 20 分片/完整回归链路。
- 不下载旧 installer、不读取或更新 installed-ui PIN、不安装旧 installed-ui helper、不复用旧 artifact 作为产品来源。
- 不调用真实 AI、真实邮箱或发布服务，不创建 release，不自动发布或安装更新。
- 构建依赖及 tokenizer 缓存需要网络；这不是离线构建承诺。Python 回归沿用仓库 loopback-only pytest 防护，冻结 smoke 沿用现有代理防误下载检查，后者不是网络沙箱。

## 必须串行通过的检查

Windows 2022 x64 runner 使用 Python 3.12、uv 0.12.19、Node 22。锁定安装为 `uv sync --frozen`、`npm ci --prefix web`、`npm ci --prefix desktop`。

1. 原生 Node：`npm test --prefix desktop`，包括语言偏好落盘/恢复、异常写入不广播、owner/Haru IPC 权限、可信 frame/origin、preload 白名单、菜单和启动错误翻译，以及现有桌面回归。另执行 `node --test desktop/localization-ui/test/*.test.mjs` 检查新 installed-UI harness 的安全/证据契约。
2. Renderer：`src/i18n` 全目录，`DesktopHaruWindow`、`useDesktopHaruOwner`、`ConversationControlsLocalization`、`useDesktopUpdateSafety`、`ChatPanel/localization` 与 `tests/localization/mounted.test.tsx`。验证语言权威来源、广播/旧回复竞态、主窗口与 Haru 的控制边界及已选组件中英文行为。Vitest 固定最多两个 worker，保留 JUnit。
3. Python：`test_desktop.py`、`test_auth_api.py`、`test_static_frontend.py`、`test_desktop_mail_localization_integration.py`、`test_job_mail_api.py`、`test_job_mail_secure_setup.py`，保留 JUnit。组合回归覆盖 desktop token、Web auth、邮件内层 Origin 与桌面外层 Origin 边界；使用隔离临时数据与 fake transport/extractor。
4. 对上述 Python 测试及 desktop/API/mail 实现执行定向 ruff；对 desktop/API/mail 实现执行 mypy。`npm run build --prefix web` 已包含 `tsc -b` 与生产 Vite 构建，不重复单独运行同一 TypeScript 检查。
5. 原样调用 [build-backend.py](../../desktop/build-backend.py)，固定 PyInstaller 6.16.0、hooks-contrib 2025.9，在 Windows 冻结当前后端。
6. 原样调用 [smoke-backend.py](../../desktop/smoke-backend.py)，检查 `desktop/backend-dist/offerpilot-backend/offerpilot-backend.exe` 与 `web/dist`。
7. `npm run build:win --prefix desktop -- --config.win.artifactName=...` 沿用 `electron-builder --win nsis --x64 --publish never`，仅由工作流覆盖产物文件名为 `OfferPilot-Desktop-localization-<12 位 SHA>-win-x64-setup.exe`；不修改 package.json。禁用签名自动发现，生成新的未签名 NSIS。
8. 再对 `desktop/dist/win-unpacked/resources/backend/offerpilot-backend.exe` 与 `desktop/dist/win-unpacked/resources/web` 执行相同 smoke。
9. 只接受该 SHA 对应的唯一新安装包，生成 SHA256 并复制至证据目录。
10. 使用新的 `desktop/localization-ui/smoke.mjs` 安装同一新包并运行限定已安装 UI 检查；新包路径、完整 source SHA、同构建 `win-unpacked`、独立证据目录和全新安装目录通过 `LOCALIZATION_*` 环境变量传入。详细范围见下节。
11. 构建、打包资源 smoke 与 SHA256 成功后，无论 installed-UI 通过、失败或未完成，都生成明确状态 manifest 并保留新实验 artifact，便于对同一字节重试；失败仍使工作流失败。构建或 hash 未成功时不上传 installer。任何失败、跳过、取消或待定结果都不构成验收通过。

冻结 smoke 的精确行为沿用 [桌面验证说明](desktop-validation.md#自动-smoke-的证据范围)：真实子进程启动、依赖自检、loopback/token/Origin/Host、资源读取、隔离数据写入及重启、换 token、退出与失败清理。它没有执行安装程序或启动 Electron UI。

## 产物与证据

- 新安装包 artifact：`offerpilot-windows-localization-experimental-<完整 SHA>-<attempt>`。
- 新证据 artifact：`windows-localization-evidence-<完整 SHA>-<attempt>`。
- 安装包内含新生成的 `OfferPilot-Desktop-localization-<12 位 SHA>-win-x64-setup.exe`、相邻 `.sha256`、`LOCALIZATION-VALIDATION-NOTES.txt` 与 `LOCALIZATION-VALIDATION-STATUS.json`；不沿用旧 artifact 名称。JSON 记录 installer hash、installed UI 的真实 outcome/是否通过及 `releaseReady: false`；artifact 存在不表示 installed UI 通过。
- 证据位于 `desktop/localization-ci-evidence/`，含来源/锁文件 hash、分阶段 transcript、pytest/Vitest JUnit、最终状态，以及成功生成时的 installer hash 和范围说明；新 installed-UI harness 的截图、动作和 payload 身份证据在其 `installed-ui/` 子目录。
- 证据上传使用 `always()`；保留 14 天。超时、强制取消或 runner 故障可能截断日志，缺失证据不能当作通过。
- `result.txt` 是证据上传前的状态快照，最终结论仍应核对 GitHub run/job 的最终状态。

## 同包 installed-UI 门禁与验收边界

新的独立 harness 安装本次刚构建的 NSIS，并与同构建 `win-unpacked` 比较安装后的可执行文件、app.asar、Web 和后端资源身份。它要求真实 `%APPDATA%/OfferPilot Desktop` 尚不存在，不通过另设 `--user-data-dir` 伪造偏好持久化，也不覆盖已有个人 profile。CI 传入全新临时安装目录，不下载旧包或复用旧 helper/PIN。

限定目标为真实安装后 zh/en 切换、完整退出重启后的偏好恢复、owner/Haru 和原生菜单语言同步，以及安全禁用凭据设置时的邮件只读/粘贴/预览与 Origin 边界。使用隔离合成内容，不录入凭据、不提交真实邮件导入、不调用 AI 或邮箱。菜单通过真实 Electron Menu 对象与 Tray 更新观察核对；这不是操作系统菜单的指针交互截图。真实安装 EXE 使用 Playwright Inspector 启动，保留 sandbox/CSP/security 默认值，但尚非未调试启动或 SmartScreen 验收。各动作实际结果以 harness 报告与截图为准；源文件或 CI 配置存在不等于检查已通过。

工作流要求 harness 成功退出才将限定 installed UI 标为通过；失败时仍保留已构建且已验证 hash 的同一实验 installer、明确未通过的状态 manifest、UI 报告与截图，便于只重试同包安装验收。保留 artifact 不会把失败的 job 变绿，也不是发布。该限定检查不替代安装/卸载/升级、完整桌面功能、所有菜单和系统对话框等全面验收。完整发布 gate 与真实 AI/邮箱验收仍未运行，不能套用旧 installed-ui artifact 的成功记录。

### Electron 证书修复与精确版本验证（2026-11-05）

当前构建锁定 Electron `44.10.2`，修复 [GHSA-jr5w-w6x2-84p9](https://github.com/electron/electron/security/advisories/GHSA-jr5w-w6x2-84p9) 所述受损 eMudhra 中间证书信任问题；44.x 的最低修复版本是 `44.9.1`。Electron 虽列在 devDependencies，仍是安装包的运行时；模型下载经其 Chromium TLS，不能按纯开发依赖忽略。更新源码依赖不等于用户已安装的旧包已升级。

`security.mjs` 要求实际 `process.versions.electron` 与本次产品源码锁定版本精确相同。`devTools` getter 缺失仅接受已审查的 `44.5.1`（历史同包重试）与 `44.10.2`，不接受版本范围或仅凭 undefined 推断关闭。新版本的 [`SaveLastPreferences()`](https://github.com/electron/electron/blob/v44.10.2/shell/browser/web_contents_preferences.cc#L389-L411) 仍省略该字段，且 [`OpenDevTools()`](https://github.com/electron/electron/blob/v44.10.2/shell/browser/api/electron_api_web_contents.cc#L3499-L3527) 在禁用时直接返回。必须同时保留安装 ASAR/源码身份、owner/Haru 构造策略及两窗口五项负向行为探针；其他版本、版本不符、证据缺失或任一打开观察都失败。

旧 `installed-ui` 和 `real-ai-validation` 的 installer PIN、摘要及历史运行记录不随依赖升级改写。历史同包重试仍要求 harness/product lock 完全相同，因此本升级分支不能直接重试旧 `44.5.1` 包；保留旧版本策略不等于允许混用新依赖与旧安装器。新包仍须记录自己的 source SHA、安装器与安装资源摘要及 Windows 实装结果；本节不是新包已通过或发布就绪的声明。

`ui-localization.yml` 是另外的 Web 合成浏览器截图入口；此 Windows job 不依赖或启动它。Mounted/jsdom 测试不等于真实浏览器截图，更不等于 installed Electron UI。

此工作流没有运行完整 pytest/前端回归、完整 release gate、真实 AI、真实邮箱、代码签名、更新发布或生产分发。完成实验包不免除 [AGENTS.md §7](../../AGENTS.md#7-验证与-code-review) 与 [Windows 人工验收](desktop-validation.md#windows-人工验收清单) 的相关义务。

## 本次源码与验收记录（2026-10-19）

本次源码检查和真实运行结果以独立 PR、当前 source SHA 的 CI 和证据 artifact 为准。原生桌面、renderer 语言同步及新 Haru 行为有回归测试；另做独立 IPC/会话控制权审查。网页通过真实 Chromium 加合成 API 截图验收；Windows 通过新 NSIS 安装包同包实装验收。两者都不等于真实 AI 或真实邮箱验收。

当前云命令环境不允许 Chromium 创建本地 IPC socket，提权启动也被环境初始化错误阻断；因此真实浏览器检查由同源 GitHub Actions Ubuntu runner 执行。不可把该环境错误或旧截图计作通过。完整 PR Windows 回归入口保持不变，结果必须等其终态后另行记录。
