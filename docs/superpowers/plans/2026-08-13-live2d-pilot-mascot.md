# Live2D Pilot 看板娘实施计划

## Task 1：偏好与状态契约（TDD）

- 新增偏好纯函数与测试，覆盖默认显示、合法值、异常存储与持久化。
- 新增 Pilot mascot activity 联合类型，并为 ChatPanel 状态回调补测试。

## Task 2：Live2D 运行时和组件（TDD）

- 安装 `pixi.js` 与 `pixi-live2d-display`，引入官方 Cubism Core 和 Haru 受付版资产。
- 先写 PilotMascot 交互测试，再实现运行时适配、画布、状态气泡、失败占位和右键菜单。
- 覆盖卸载销毁、Escape、键盘激活、隐藏及 reduced-motion。

## Task 3：接入 AppShell 与设置（TDD）

- 先写挂载回归，再在 AppShell 接入显示偏好、drawer 切换与 rail 回退。
- SettingsView 增加“显示 Haru”开关与版权链接。
- 顶层 Pilot 和窄屏保持现状。

## Task 4：版权、验证与截图

- 增加模型 NOTICE，并在 README 补充第三方资产说明。
- 运行定向与全量前端测试、TypeScript、生产构建、`git diff --check`。
- 独立代码复审，修复所有 P0/P1/P2。
- 使用内置浏览器完成亮色宽屏验收，保存并回读截图，清理服务与临时数据。
