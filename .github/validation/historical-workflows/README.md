# 历史验证工作流快照

本目录不是 GitHub Actions 的工作流入口。文件仅供离线契约回归与历史审查，不得当作本次集成的运行请求或验收证据。

- `desktop-installed-ui.yml`：固定安装包的真实 Windows UI 验证，绑定旧 source/build/run/artifact。
- `desktop-layout-retry.yml`：固定产品快照的一次布局与打包重试。
- `desktop-browser-cleanup-diagnostic.yml`：固定失败运行的有界离线清理诊断。
- `desktop-real-ai.yml`：固定旧包的 MOCK / 受保护付费验证。旧请求、价格窗口与累计费用边界均不变；本次集成不激活它、不更新报价、不提供凭据。
- `haru-ui-preview.yml`：旧 Haru 功能分支的合成浏览器与源码 Electron 预览。
- `desktop-windows.yml`：归档前完整工作流快照，仅供上述历史 routing 测试读取。当前完整门禁以 `../../workflows/desktop-windows.yml` 为准。

相关 helper、固定 request/product 和测试夹具保留在原目录，避免破坏预算、身份和失败传播回归；固定元数据不是当前候选的授权或证明。它们不会由生产桌面包的显式文件清单打包。旧一次性完整恢复记录位于 `../historical-requests/windows-full-request.json`，同样不激活任何运行。

当前 CI 只运行这些 helper 的离线单测及真实 React/jsdom 契约测试，不下载历史安装包、不调用付费执行入口。它使用当前 GitHub SHA 构建并验证自己的 NSIS 安装产物。任何将来的真实模型或固定包 UI 验收需要独立范围、来源和授权。
