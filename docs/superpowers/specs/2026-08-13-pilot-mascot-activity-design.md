# Pilot 看板娘状态、后台提醒与缩放设计

状态：已复审通过

日期：2026-08-13

基线：`feat/20260813-dashboard-action-cards@d963765`

## 1. 背景与目标

当前 Haru 已经是宽屏页面上的 Pilot 入口，并能显示 `idle / thinking / success / error` 文案状态，但仍存在四个体验缺口：

- `thinking` 只改变状态圆点，角色本身没有区别于待机的思考反馈；
- 顶层 Pilot 页面主动排除了 Haru；
- 普通对话在关闭 Pilot 抽屉时会中止，无法在后台完成后由角色提醒；
- 运行时只按容器自动适配，不允许用户调整角色大小。

本切片在不改变 Chat、Provider、HITL、证据和持久化契约的前提下，让 Haru 成为可感知 Pilot 工作状态的会话级入口。关闭界面不再等同于取消普通回复；真正取消仍由“停止生成”完成。

## 2. 技术选型

继续使用现有 `pixi-live2d-display` 运行时，不直接引入 `hacxy/l2d-widget`。

调研结论：`l2d-widget` 适合向普通网页快速注入独立挂件，提供一键创建、多模型切换、提示气泡、打字口型、隐藏和销毁能力，并采用 MIT 许可证；但其 Widget 公共控制面主要是 `switchModel / sleep / destroy`，缩放是模型初始化参数，也不负责 OfferPilot 的对话请求生命周期。

OfferPilot 已经具备 React 生命周期、StrictMode 串行挂载、失败资源清理、`prefers-reduced-motion`、键盘菜单、本地隐藏偏好和 AppShell/Pilot 联动。整体替换会重复或削弱这些已验证边界，且不能解决后台回答提醒。因此仅借鉴以下交互：

- 状态气泡与角色动作同步；
- 菜单式角色控制；
- 用户偏好持久化；
- 后续多模型切换的控制器边界。

参考：

- <https://github.com/hacxy/l2d-widget>
- <https://github.com/hacxy/l2d-widget/blob/main/src/types.ts>

`l2d-widget` 的 MIT 许可只覆盖该项目代码，不替代 Live2D SDK、Cubism Core 和 Haru 样例资产的独立条款。现有 NOTICE 与发布许可门禁保持不变。

## 3. 用户体验

### 3.1 角色状态动画

Haru 继续使用四种业务状态：

| 状态 | 角色表现 | 文字表现 |
|---|---|---|
| `idle` | 原有自然待机动作 | “随时待命” |
| `thinking` | 从 Haru 现有 motion 中选择与普通待机可明显区分的轻量循环动作 | “正在思考”及当前已有加载说明 |
| `success` | 播放一次完成反馈动作，结束后保持轻量成功表情 | “回答已完成，点击查看”或现有确认成功文案 |
| `error` | 播放一次受阻反馈并保持非惊扰错误表情 | “回答未完成，点击查看”或现有错误文案 |

动作只使用当前 Haru 资产已有的 `Idle`、`Tap` motions 和 expressions，不新增或修改模型文件。动作选择以浏览器预览中“状态可区分、不过度跳动、不遮挡内容”为验收标准。

动作或表情调用失败只降级为当前文字、颜色和状态圆点，不影响 Pilot。开启减少动态效果时不切换或循环角色动作，只保留静态文字状态；不能靠动画或颜色单独传递状态。

### 3.2 顶层 Pilot 页面

用户偏好为“显示 Haru”且屏幕宽度允许时，顶层 Pilot 页面也展示 Haru：

- 使用紧凑尺寸，固定在 Pilot 工作区右下侧，不改变现有聊天与机会评估布局；
- 无未读结果时，点击角色把焦点移到输入框；
- 有完成或失败提醒时，点击角色打开对应会话并定位到最新结果；
- 顶层 Pilot 页面不再因为 `view === 'pilot'` 而排除角色；
- 用户隐藏角色时继续保持现有完整 Pilot 页面，不额外显示第二个入口。

### 3.3 关闭抽屉后的后台完成提醒

本期支持当前浏览器会话内的一条活动普通 Pilot 回复：

1. 用户在 contextual Pilot 抽屉发送消息；
2. 用户关闭抽屉，ChatPanel 保持挂载，请求继续且不重试；
3. Haru 显示 `thinking`；
4. 回复完成后，Haru 切换为 `success` 并显示固定提醒“Pilot 已完成回答，点击查看”；
5. 用户点击角色后重新打开原抽屉、选中原 conversation，并清除未读提醒；
6. 回复失败时使用 `error` 和固定提醒，不在气泡中展示 Provider 原文、回答正文或敏感上下文。

关闭抽屉不再调用普通请求的 abort；以下行为仍会取消或废止请求：

- 用户点击“停止生成”；
- 用户明确开始新的对话并替代当前活动请求；
- 上下文所有者被删除或失效；
- AppShell 真正卸载或浏览器刷新。

确认类请求继续沿用现有锁和回读语义，不因本切片改变。后台完成不触发第二次 API/Provider 调用，不创建新的 Chat 消息，也不自动执行任何确认动作。

本期提醒是会话内内存状态，不写数据库和 `localStorage`，刷新后不恢复。若用户隐藏 Haru，既有 Pilot rail 使用未读状态提示承接结果，不能因为隐藏角色而丢失通知。

### 3.4 角色缩放

右键菜单增加：

- “缩小角色”；
- “恢复默认大小”；
- “放大角色”。

缩放范围固定为 `80%–130%`，每次调整 `10%`。运行时使用 `自动适配基准 scale × 用户 zoom`，不直接累乘当前 scale，避免容器 resize 后漂移。偏好写入新的本地键并在刷新、隐藏/恢复和普通页面/Pilot 页面之间共享。

达到上下限时相应按钮禁用；菜单展示当前百分比。缩放不得改变 Pilot 抽屉宽度，也不得让可点击区域覆盖主要表单。窄屏继续不显示 Haru，不新增触屏手势或滚轮拦截。

## 4. 组件与状态边界

### 4.1 Live2D 运行时控制器

`PilotMascotRuntime.mount()` 从只返回 disposer 收紧为返回控制器：

```ts
type PilotMascotRuntimeController = {
  setActivity(activity: PilotMascotActivity): void;
  setZoom(zoom: number): void;
  dispose(): void;
};
```

控制器只管理模型呈现，不读取 Chat、路由或存储。`setActivity` 负责 motion/expression 的幂等切换；`setZoom` 负责在下一次 fit 及 ResizeObserver 回调中继续应用用户倍率。局部加载或 motion 失败均必须可清理且不抛到 AppShell render。

### 4.2 偏好与通知

- `pilotMascotPreference.ts` 增加安全读写 zoom 的纯函数；非法、非有限和越界值回退到 `100%`。
- `AppShell` 持有角色可见性、zoom、activity 和一次性 completion notification。
- `ChatPanel` 通过只读回调上报活动请求、conversation ID、完成和失败，不直接操作 Haru。
- 通知必须携带实际 conversation ID；打开提醒时按 ID 恢复，禁止猜测“当前第一条会话”。

### 4.3 ChatPanel 生命周期

contextual ChatPanel 在非 Pilot 页面始终保持同一个已挂载实例，`open=false` 时只隐藏视图。关闭动作不再废止普通请求的可见代次；流事件仍更新该实例，但不自动重新打开 UI。

顶层 Pilot 页面继续使用 page variant。若进入顶层 Pilot 时已有后台结果，只通过 conversation ID 读取已保存会话，不复制内存 turns，也不创建第二次请求。跨页面切换期间不能同时存在两个 owner 对同一请求写入 UI 状态。

## 5. 无障碍与隐私

- 动作状态始终配套中文 `aria-live` 文本；相同提醒再次发生时也必须产生可播报的状态变化。
- 缩放按钮为真实 button，提供当前值、上下限禁用态和键盘焦点。
- 顶层 Pilot 页角色的 accessible name 区分“聚焦输入框”和“查看已完成回答”。
- 后台通知只展示固定状态文案，不展示用户输入、模型回答、岗位、公司、简历或证据内容。
- `prefers-reduced-motion: reduce` 下停止主动 motion 切换和循环，缩放与提醒仍可用。

## 6. 明确不做

- 不引入 `l2d-widget` 包或 CDN；
- 不新增角色切换、用户模型导入、拖拽位置、语音、口型或系统通知；
- 不新增后端、API、数据库、AI 请求、重试或后台任务队列；
- 不让角色自动发送消息、自动打开 Pilot 或自动确认写操作；
- 不把 completion notification 持久化到刷新后的新会话。

## 7. 测试与验收

### 7.1 纯函数与运行时

- zoom 默认值、合法范围、越界、`NaN/Infinity`、异常 storage；
- resize 后仍按 `base scale × zoom` 计算，不累计漂移；
- 四种 activity 的幂等切换、一次性成功/失败动作、动作失败降级；
- reduced-motion 不启动主动动作；
- StrictMode、异步 mount、模型失败、observer 失败和 dispose 后晚到调用均不泄漏资源。

### 7.2 组件与 AppShell 挂载

- `thinking` 不只改变状态圆点，runtime 收到 activity；
- 顶层 Pilot 页显示紧凑角色，点击空闲角色聚焦输入框；
- 关闭抽屉后普通请求继续，完成后显示未读提醒；
- 点击提醒恢复精确 conversation 并清理提醒；
- 显式停止仍 abort，关闭不 abort；
- 失败提醒、角色隐藏后的 rail 提醒、刷新不恢复提醒；
- 缩小、重置、放大、上下限、持久化、菜单键盘与焦点恢复；
- 上述交互不新增 Chat/AI/API 写调用，不重复 Provider 请求。

### 7.3 浏览器验收

使用亮色中文宽屏，至少保存以下截图：

1. 顶层 Pilot 页面紧凑 Haru；
2. 抽屉关闭后 Haru 正在思考；
3. 回答完成后的“点击查看”提醒；
4. 右键缩放菜单及放大后的角色。

实际走查：发送一条只读问题，立即关闭抽屉，确认请求不中止且只调用一次；完成后点击 Haru 回到原会话。随后验证隐藏角色由 rail 承接提醒、减少动态效果、缩放上下限、控制台无新增错误。浏览器和临时服务在 `finally` 中清理。

## 8. 破坏性变化与发布边界

无 API、数据库或模型资产破坏性变化。唯一行为变化是“关闭 contextual Pilot 抽屉”从“取消普通回复”改为“仅隐藏、继续当前回复”；用户仍可通过“停止生成”明确取消。

发布前继续执行既有 Live2D NOTICE、样例资产声明和 SDK Publication License 门禁。本设计不因参考 MIT 项目而放宽任何 Live2D 许可要求。
