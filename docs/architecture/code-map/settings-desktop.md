# 设置、认证、桌面与公共入口地图

[返回业务总图](README.md) · [Pilot](pilot-runtime.md) · [邮件](job-mail.md) · [完整路由归属](route-coverage.md)

核对基线：2026-10-28，`454af3d7`。本页把横跨业务的设置与运行入口纳入导航，产品约束仍见 [AGENTS](../../../AGENTS.md)、[工程规则](../rules.md)和相关 ADR。

## 1. 设置与认证

| 用户说法 | 前端 / API / 实现 | 排查边界与测试 |
|---|---|---|
| 模型配置、连接测试、fallback | [AISettingsDrawer](../../../web/src/components/AISettingsDrawer.tsx)、[chat service](../../../web/src/services/chat.ts) → [api.py](../../../src/offerpilot/api.py) `get_settings` / `update_settings` / `test_settings_provider` → [config](../../../src/offerpilot/config.py)、[ConfiguredAIClient](../../../src/offerpilot/ai/client.py) | 保存配置、测试连接、实际功能调用分别验证；某一链路不一定启用 fallback；[设置 API](../../../tests/test_settings_api.py) |
| 登录、401/503、桌面与浏览器不同 | [auth](../../../web/src/services/auth.ts)、[authToken](../../../web/src/services/authToken.ts)、[http](../../../web/src/services/http.ts) → `_auth_guard_response` / `cors_middleware` | health/auth-status 豁免；启用 auth 但无 token 与错误 token 不同；桌面 transport authenticator 不修改 web 配置；[认证 API](../../../tests/test_auth_api.py) |
| 技能不生效、是否受信任 | `/api/skills` → [skills.py](../../../src/offerpilot/skills.py) `register_skill` / `update_skill` / `loaded_skill_ids` | 此层登记元数据；只有 trusted 且 enabled 才 loaded；登记不代表下载或执行；[技能 API](../../../tests/test_skills_api.py) |
| 首次引导、跳错模块 | [onboarding service](../../../web/src/services/onboarding.ts)、[actionRouting](../../../web/src/features/onboarding/actionRouting.ts) → [onboarding_payload](../../../src/offerpilot/onboarding.py) | 引导状态/动作与真实业务完成分开；[Onboarding API](../../../tests/test_onboarding_api.py) |
| 日志、备份、诊断分页 | [SettingsView](../../../web/src/components/SettingsView.tsx) → `get_logs` / `export_backup` / `get_settings_backup` | 日志只证明记录内容，备份导出不证明可成功恢复；[设置分页](../../../web/src/components/SettingsView.pagination.test.tsx) |

设置改变写工具自动批准、Provider 选择或预算会影响真实执行，不能通过 UI 显示“保存成功”推断所有在途任务已采用新配置。

## 2. 桌面启动与更新

| 链路 | 稳定入口 | 关键边界 |
|---|---|---|
| 启动后端与窗口 | [desktop/main.cjs](../../../desktop/main.cjs) `start` → [backend_entry.py](../../../desktop/backend_entry.py)；[lifecycle.cjs](../../../desktop/lifecycle.cjs) `waitForReady` / `checkHealth` | 本机 origin、就绪协议、健康检查及进程退出；窗口出现不证明后端可用 |
| IPC 和 Haru 窗口 | [preload.cjs](../../../desktop/preload.cjs)、[haru-protocol.cjs](../../../desktop/haru-protocol.cjs)、[haru.cjs](../../../desktop/haru.cjs) | 来源 frame 与 capability 边界；Haru 观察任务不自动取得新的业务写权限 |
| 检查 / 下载更新 | [updater.cjs](../../../desktop/updater.cjs) `createUpdater` / `registerUpdaterIPC` → [update-integrity](../../../desktop/update-integrity.cjs)、[update-signature](../../../desktop/update-signature.cjs) | 检查、下载、验证、安装是不同状态；下载成功不等于安装资格成立 |
| 安装前保护任务 | [update-safety.cjs](../../../desktop/update-safety.cjs) `createInstallSafety` → [前端安装安全](../../../web/src/features/desktopUpdates/installSafety.ts) / [保留工作](../../../web/src/features/desktopUpdates/retainedWorkSafety.ts) | 就绪、未保存草稿、运行/未知任务、待批准必须核验；确认前后两次快照及页面代次约束 |
| 备份 / 停后端 / 安装 | [update-backup](../../../desktop/update-backup.cjs)、[update-install](../../../desktop/update-install.cjs) `stopBackendForUpdate` | 安装属于实际退出/修改环境边界；自动检测不覆盖所有未保存编辑，按现有用户确认流处理 |

依据：[Windows 桌面验收 ADR](../decisions/0013-windows-desktop-validation.md)。测试入口：[lifecycle](../../../desktop/test/lifecycle.test.cjs)、[Haru](../../../desktop/test/haru.test.cjs)、[更新状态](../../../desktop/test/updater.test.cjs)、[完整性](../../../desktop/test/update-integrity.test.cjs)、[安装安全](../../../desktop/test/update-safety.test.cjs)、[备份](../../../desktop/test/update-backup.test.cjs)。

## 3. 页面导航和仅前端能力

[navigation.ts](../../../web/src/layout/navigation.ts) 的 `ViewMode` / `MODULE_TABS` → [AppShell](../../../web/src/layout/AppShell.tsx) → 页面/Drawer/service。七个 module 与十四个 view 不一一对应；会话中心、素材库、面试内刷题等入口先核对实际映射。

共用任务展示和保留草稿查 [coreTaskSurface](../../../web/src/features/coreTaskSurface/)、[actionPresentation](../../../web/src/features/actionPresentation/)；下一步推荐见[工作台地图](workbench-automation.md)。语言/文案查 [i18n](../../../web/src/i18n/)；Haru 展示/形象偏好查 [assistantSurface](../../../web/src/features/assistantSurface/) 和 [pilotMascot](../../../web/src/features/pilotMascot/)。这些 UI 状态不能作为业务提交证据。

## 4. CLI、迁移和验证入口

[cli.py](../../../src/offerpilot/cli.py) 是 CLI 入口；共享 JD/简历/题库 workflow 见 [ai/workflows.py](../../../src/offerpilot/ai/workflows.py)。若同能力 CLI 与 HTTP 表现不同，先核对参数解析，再核对是否进入同一业务服务，不用其中一端通过替另一端背书。

模型与表定义从 [models.py](../../../src/offerpilot/models.py)、[db.py](../../../src/offerpilot/db.py) 下钻；数据库初始化/迁移和正在运行进程使用的 data_dir 要分开检查。不要因另一 worktree 的文件已改就推断当前进程或本地库已升级。

[Python rewrite 契约](../../python-rewrite-contract.md)、[P0 清单](../../p0-release-checklist.md)保留各自适用范围；完整发布验证入口与风险矩阵见 [AGENTS §7](../../../AGENTS.md#7-验证与-code-review)。文档静态检查、局部测试、真实模型调用、桌面安装和 release gate 是不同证据层级。
