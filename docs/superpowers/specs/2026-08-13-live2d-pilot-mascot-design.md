# Live2D Pilot 看板娘设计

状态：已确认，可实施
日期：2026-08-13
基线：`main@aeb054b`

## 1. 目标

在不改变 Pilot 业务能力、对话契约和现有页面布局的前提下，为桌面宽屏增加 Live2D Haru 受付版入口。角色是 Pilot 的轻量入口与状态反馈，不是新的聊天实现。

## 2. 用户体验

- 桌面宽屏且不在顶层 Pilot 页面时，默认显示 Haru，替代空闲态的常驻 Pilot 侧栏。
- 左键或键盘激活角色，打开或收起现有 Pilot 对话框。
- 角色只映射现有 Pilot 的 `idle / thinking / success / error` 状态，不自行发请求。
- 右键打开应用内菜单；选择“隐藏角色”后立即恢复现有 Pilot 侧栏。
- 隐藏偏好写入本机 `localStorage`，刷新后保持；设置页提供“显示 Haru”开关用于恢复。
- 窄屏、顶层 Pilot 页面和模型加载失败时保持既有 Pilot 体验，不阻断任何业务操作。

## 3. 边界

- 第一期固定 Haru 受付版，不提供换角色、上传模型、语音、口型、摄像头或自动弹窗。
- 不新增后端、API、数据库、AI 调用、埋点或跨领域写入。
- Live2D 运行时通过独立适配层加载，后续更换模型不进入 AppShell 业务逻辑。
- 角色点击只复用现有 ChatPanel；隐藏角色后默认侧栏语义完全不变。

## 4. 技术结构

- `pilotMascotPreference.ts`：安全读取/保存显示偏好，默认显示。
- `live2dRuntime.ts`：封装 Pixi/Cubism 模型加载和销毁，组件不直接依赖全局细节。
- `PilotMascot.tsx`：画布、状态气泡、键盘交互与右键菜单。
- `ChatPanel` 增加只读状态回调；加载或确认中为 thinking，确认成功为 success，错误或降级为 error，其余为 idle。
- `AppShell` 持有显示偏好和状态；显示角色时隐藏空闲 rail，打开对话时继续使用现有 drawer。
- `SettingsView` 提供恢复开关与版权说明。

## 5. 可访问性与失败处理

- 角色入口是可聚焦按钮，提供随开关状态变化的中文 accessible name。
- 右键菜单支持 Escape 关闭；隐藏后焦点不落入不存在节点。
- 状态不能只依赖颜色；气泡包含文字。
- 模型加载失败时显示轻量 Pilot 占位图标，点击仍能打开对话。
- 动效尊重 `prefers-reduced-motion`。

## 6. 资产与许可

- 使用 Live2D 官方 Haru 受付版样例及 Cubism Core；第三方资产不声明为 OfferPilot 自有许可。
- 在模型目录 NOTICE、设置页和 README 中保留 Live2D/Cubism 版权与官方条款链接。
- 正式分发前再次确认 Live2D SDK Publication License；第一期不提供模型扩展能力。

## 7. 验收

- 单元测试覆盖偏好、左键、右键、键盘、加载失败和四种状态。
- 真实 AppShell 挂载覆盖：默认角色、打开/关闭 Pilot、隐藏后 rail 恢复、刷新保持、设置恢复和窄屏回退。
- 前端测试、TypeScript、生产构建与差异检查通过。
- 内置浏览器在亮色宽屏完成角色关闭、Pilot 打开、右键菜单和隐藏后侧栏截图；控制台无新增错误。
