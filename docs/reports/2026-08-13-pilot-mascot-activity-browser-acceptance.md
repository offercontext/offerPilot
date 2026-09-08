# Pilot 看板娘活动与后台回答浏览器验收

日期：2026-08-13

分支：`feat/20260813-pilot-mascot-activity`

## 验收环境

- 使用隔离临时数据目录启动本地生产构建，地址为 `http://127.0.0.1:65410/`。
- 静默复制本机现有 Ark 配置；报告、截图与日志均未记录密钥。
- 浏览器为 Codex 内置浏览器，亮色模式，视口截图尺寸 `1265 × 712`。
- 浏览器页面仅访问本地静态资源与 `/api`；AI 请求由本地后端按隔离配置出站。

## 验收结果

1. 顶层 Pilot 页面显示紧凑 Haru，点击行为聚焦 Pilot 输入框，不再缺失角色。
2. 在工作台打开 Pilot，发送一次只读中文问题后立即关闭抽屉；抽屉关闭期间 ChatPanel 保持挂载，Haru 进入独立的 `thinking` 动作并显示“正在思考”。
3. Ark 回答完成后，Haru 显示“处理完成”；服务器日志仅记录一次 `POST /api/chat/stream`，随后读取会话 `487`。
4. 点击完成通知重新打开 Pilot，并准确展示本次问题及回答；通知同步清除。
5. 右键菜单可把角色从 `100%` 放大至 `130%`，菜单状态与画面同步。
6. 右键隐藏后恢复经典 Pilot 侧栏入口；在设置中重新启用 Haru 后角色正常恢复。
7. 浏览器控制台错误数为 `0`。
8. 验收页、隔离服务及进程树均已关闭，端口 `65410` 已释放，临时数据目录已删除。

## 截图证据

| 文件 | 场景 | 尺寸 | SHA-256 |
| --- | --- | --- | --- |
| `01-pilot-page-haru.png` | Pilot 顶层页面紧凑 Haru | 1265 × 712 | `79ba7b44cff0d481a00666788bce5bdbc58fbdc95cc2dd9d4942dfd448ec4a77` |
| `02-background-thinking.png` | 关闭抽屉后的思考动作与状态气泡 | 1265 × 712 | `ebcc476e6cd5d1ba465ed8b1facaaf4618fcc0e7391f4f0b1d246b0ad778b414` |
| `03-reply-complete-notification.png` | 后台回答完成通知 | 1265 × 712 | `edd463d274f48fb077df7cf12631aaeba23ae86f7a26ca4f2f920786ccc0e678` |
| `04-zoom-menu-enlarged-haru.png` | 右键缩放菜单与 130% 角色 | 1265 × 712 | `dcca52169ceb81cfa334be93aebe57eb01bdec40523b4964a1f169f1cbbe37b6` |

截图目录：`artifacts/2026-08-13-pilot-mascot-activity`

## 边界

- 本轮没有修改后端、API、数据库、Provider 配置、提示词或 Live2D 模型资源。
- 后台延续只覆盖普通 Pilot 对话；用户显式新建/替换对话时仍会中止旧的普通请求。
- 角色通知仅保留在当前会话，不持久化回答正文或额外业务状态。

## 自动化验证

- 定向前端：8 个文件、199 项测试通过。
- TypeScript 项目构建：通过。
- 前端生产构建：通过，Vite 转换 3910 个模块。
- `git diff --check`：通过。
- 定向测试保留既有的 React `hasSider` 与 jsdom `getComputedStyle` 警告，不影响测试结果；真实浏览器未出现对应错误。
