# Haru 独立桌面小窗

状态：实验分支本地实现；2026-10-08。源码与 mock 回归已覆盖，不等同于 Windows 安装包或真实窗口验收。没有开机启动、系统设置改动或 Win+D 桌面层保证。

## 窗口与退出

- 主窗口继续持有唯一 `AssistantSurfaceProvider`、Pilot controller、请求租约、审批与恢复状态。主窗最小化或关闭收托盘，不卸载 owner；关闭 Haru 只隐藏小窗，不中止任务。
- Haru 是透明、无边框、独立 BrowserWindow。标题栏拖动，提供展开/收起、显隐、可选置顶及打开主窗。托盘可重新打开两个窗口；“退出并停止本地服务”走原有有界后端关闭流程。
- 系统托盘创建失败时，主窗关闭直接退出，避免无入口的后台进程。owner renderer 崩溃仍按原致命错误路径退出；Haru renderer 崩溃可从托盘重建，不能恢复或重放请求。
- 位置与置顶偏好只写应用 userData 内 `haru-window.json`；恢复、展开与屏幕移除/缩放变化时按真实 display workArea 限位，支持负坐标屏幕。默认不置顶。
- Haru 隐藏后释放 Live2D ticker，恢复后读取最新镜像；空闲采用静态单帧。主窗内现有 mascot 在 document 隐藏时释放 runtime。小窗入口分块加载，不创建第二份 AppShell 或业务服务。

## 身份与通信边界

主窗保留原持久 session。Haru 使用独立、非持久 partition，不共享 localStorage、IndexedDB、cookie 或 BroadcastChannel。两者均 sandbox、context isolation、禁用 Node integration；Haru 禁止所有权限与下载。

桌面随机 token 只留主进程。每次请求先剥离调用方伪造的 token：主 session 只向仍存活的可信 owner webContents 的精确本地 origin 注入；Haru session 只允许 GET/HEAD 的显式静态 assets/live2d 路径及固定顶层入口，拒绝 API、编码路径、额外 query、子 frame 与远程请求。Haru 同时采用 onBeforeRequest 白名单及 header 注入白名单。

preload 不暴露通用 IPC、文件、shell、token 或审批 API。每次 IPC 都校验当前窗口对象、顶层 frame 及精确 origin。owner 仅发布有界最近消息文本、上下文标签、运行/停止/待审批标志；不复制 pending action、工具参数、provider 配置或确认凭据。

小窗仅转发发送、停止、打开待审批页面。审批在原 Pilot 工作区完成。每条命令携带 owner generation 与控制状态 version；owner-only 签名覆盖真实上下文身份、附件、draftContext、执行身份与选择过程，签名不传出窗口。流式文字变化不改变控制 version，避免阻止正常 Stop。owner 重载/失联会使镜像失效并禁用操作。

main 只容许一条待 ACK IPC；owner 命令 ID 去重、同步核对实时 refs，并复用 controller 的唯一 request lease。发送只在取得 lease 后 ACK；被 ignored 的发送不能被标为成功。超时/连接丢失不自动重试，小窗保留输入并要求用户到主窗核对。Haru 输入不会覆盖主窗已有未发送草稿。

## 验证与限制

相关入口：

- `node --test desktop/test/*.test.cjs`：包括 DTO 白名单、非法命令、frame/origin、双 session token 边界、reload epoch、并发/失联不重放、托盘关闭、隐藏广播节流和多屏限位。
- `npm --prefix web test -- src/features/assistantSurface src/features/pilotMascot`：包括小窗交互、相同名称不同目标/附件变化、流式 Stop、执行身份变化、render 前选择锁、ignored send、主窗草稿保护与隐藏动画释放。
- `npm --prefix web run build`：TypeScript 与生产构建。

本地 Chromium mock 截图启动被宿主 socket 权限阻止；云浏览器与当前工作区不共享 localhost，访问 mock 端口被拒绝。因此本次没有真实浏览器截图或 Windows 原生窗口结果，也未调用真实模型、读取配置密钥、生成安装包或推送远端。

安装包验收还需在 Windows 证明：透明/拖动/多 DPI 屏幕限位、主窗关闭后 Haru 连续请求及停止、审批仅从主窗执行、托盘恢复与明确退出清理、真实 session 存储隔离、隐藏/空闲 CPU 与动画暂停。应在最新整合代码上重新打包与固定 provenance，不可沿用旧安装包的 UI 通过结论。

相关：[桌面验证说明](desktop-validation.md)、[固定安装包 UI 验证](desktop-installed-ui-validation.md)、[仓库验收规则](../../AGENTS.md#7-验证与-code-review)。
