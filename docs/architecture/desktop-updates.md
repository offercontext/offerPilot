# Windows 手动更新客户端

状态：客户端实现，正式渠道未启用，尚未完成 Windows 两版本实装验收。既有验证包不会因仓库更新而获得此功能。

## 用户流程与边界

仅 EXE 的设置显示“关于与更新”，显示 Electron 安装版本，不把后端版本当安装版本。用户分别点击检查、下载、安装；没有后台定时检查、自动下载、托盘退出自动安装或服务器部署功能。网页没有此卡片。

开发环境、不支持的平台或未配置签名源显示具体不可用原因，不声称已经最新。发布说明是纯文本；重复请求单飞；失败可重新检查。下载不是安装，安装前默认取消的原生对话框要求“已保存且可结束任务，关闭并安装”。已知草稿/附件、活动或状态不明的执行、待审批操作会阻止安装。检查不能覆盖所有编辑器或后台任务：Knowledge 列表排除删除中任务，Proactive 列表仅最近 100 条，execution 只读取当前及已知后台会话。确认明确告知检测不完整，要求用户确认已保存所有编辑、可结束后台任务并同意关闭应用安装；不能把当前 Pilot 空闲宣传成全应用任务已结束。已知阻塞与读取失败仍不能被确认绕过。

确认前与交互锁定后分别通过一次性 nonce 重新读取已知状态；主窗口丢失、导航、读取失败或超时均拒绝安装。更新 IPC 只允许 owner 主窗口的确切主 frame 与本机 origin；Haru、子 frame、外部导航均无权限。preload 不暴露任意 feed、路径、进程或通用 IPC。CSP 与远端访问边界不放宽。

## 退出、备份与失败

安装时锁定窗口交互，flush Electron storage，然后关闭后端 stdin 并等待真实正常退出。仅 code=0 且无终止信号可备份。不得把超时 resolve 当成功；不为升级强杀写入者。Python watchdog 的非零退出也是失败。

离线复制 `%APPDATA%\OfferPilot Desktop\data` 的完整工作区和 `desktop-port.json`、`haru-window.json` 到同级 `OfferPilot Desktop-update-backups` 的唯一子目录。配置可能含敏感数据，始终本地、不上传、不打印内容；备份不扩大文件权限：Windows 在空备份目录先移除继承授权，仅授予当前用户访问，权限设置失败则在复制前中止；POSIX 限制目录和文件权限。拒绝链接/junction/非普通文件，最后才写完成标记。不完整目录不是可恢复备份。无自动删除旧备份。

Electron 持久 partition 保留原路径且 flush，不把活跃 LevelDB 的普通复制当一致性快照。安装后端、网页静态资源与数据目录分离；保持 appId、产品名、per-user 安装范围、数据路径和端口。

检查、下载、完整性/签名检查、状态核对、退出或备份失败均不调用安装器。后端已停时尝试重启原版，提示用户核实是否启动，不谎称服务已经恢复。安装器启动错误会触发幂等恢复；NSIS 启动之后的权限拒绝、取消、断电或系统关机不具备原子 rollback 保证。electron-updater 的启动信号不是安装成功证据，特别需实测 ENOENT/openPath、UAC、关机中断等分支。备份可恢复不意味着旧程序能读取已迁移的新 schema。

## 更新源与信任

运行依赖固定为官方 npm `electron-updater@6.6.2`，保留现有 Electron 44.5.1 / builder 26.15.3；锁文件除新增运行依赖和其依赖归属外不更改既有版本。

`RELEASE_POLICY=null` 是明确的未发布状态，build 仍 `publish:null` 和 `--publish never`。未来启用需批准正式渠道和签名后修改受控构建配置，而非用户/renderer 输入源地址。计划渠道是固定的公开 GitHub `offercontext/offerPilot` stable Release；配置检查拒绝不同 owner/repo/host/protocol、token/private/url 以及缺失或不匹配 publisher。不得把 Actions 临时 artifact 当 feed，不在客户端放 GitHub token。

配置固定 `autoDownload=false`、`autoInstallOnAppQuit=false`、`allowPrerelease=false`、`allowDowngrade=false`、`disableWebInstaller=true`。不使用 v27 才有的 `autoInstallEvent`，不强行启用开发 feed。

SHA512 是完整性验证，不替代发布者身份。下载返回后（包括缓存命中）和实际安装前均重新读取文件计算 SHA512，并验 Windows Authenticode。默认旧签名器可能在 PowerShell 不可用时放行；本客户端替换为更严格的验证函数：固定系统 PowerShell、无 shell、编码命令、完整签名主体 DN 精确匹配、Status=Valid；工具异常、超时或异常输出全部拒绝。没有关闭 `verifyUpdateCodeSignature`。此路径必须用真实签名安装包在 Windows 再验。

## 正式启用前门禁

1. 独立安全审查、本地相关测试/前端构建、完整 release gate 与安装范围门禁。
2. 授权后准备签名 NSIS per-user x64 完整包、latest.yml、blockmap、真实版本与说明；全部齐备且校验后才发布稳定 Release。证书、密钥、发布操作不属于客户端实现授权。
3. 两个真实安装版本间验证：手动检查/下载/取消/重试/重启，Haru/托盘与单实例，错误签名/损坏缓存/无网络/磁盘满/非零退出/安装器启动失败。
4. 脱敏旧数据库 fixture 迁移与恢复验证，尤其已有 Knowledge legacy reset。数据、配置、附件、端口、浏览器状态和 Haru 设置保留；不得用干净安装代替升级证明。
5. 首次从无 updater 的旧验证包进入正式版本，用户需手动安装可信 bootstrap。不得宣称既有固定 EXE 已具备新功能。

参考：[v26 更新](https://www.electron.build/v26/docs/features/auto-update/)、[v26 API](https://www.electron.build/v26/docs/api/electron-updater.class.appupdater/)、[NSIS](https://www.electron.build/v26/docs/nsis/)、[Windows 签名](https://www.electron.build/docs/win/)。

现有后端退出契约证据：固定验证包 `16f31e47` 的 [Windows 构建 job](https://github.com/offercontext/offerPilot/actions/runs/37754883783/job/113236876811) 于 2026-10-08 09:23:21 UTC（冻结后端）与 09:25:22 UTC（win-unpacked resources）两次通过 `desktop/smoke-backend.py`，该脚本硬断言 stdin EOF 后退出码为 0 且端口关闭。此证据只证明原有后端正常退出路径，不覆盖本客户端更新集成。
