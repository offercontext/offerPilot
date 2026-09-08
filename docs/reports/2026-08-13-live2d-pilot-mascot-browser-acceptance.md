# Live2D Pilot 看板娘浏览器验收

日期：2026-08-13
分支：`feat/20260813-live2d-pilot-mascot`

## 验收环境

- 使用本地实际 OfferPilot 配置与数据启动 API。
- 前端使用亮色模式，宽屏视口 `1536 × 1000`。
- Live2D、Haru 模型与应用请求均从本地资源或本地 API 加载。

## 验收结果

- Haru 在桌面宽屏显示，点击可打开既有 Pilot 对话框。
- Pilot 打开时 Haru 缩小，不遮挡输入区或上下文栏。
- 右键菜单可隐藏角色；隐藏动作不会关闭正在使用的 Pilot。
- 用户主动关闭 Pilot 后恢复既有 Pilot 侧边栏。
- 设置页“显示 Haru”开关可恢复角色，偏好写入本地存储。
- 键盘菜单、Escape 焦点恢复、运行时失败回退、StrictMode 串行初始化、资源清理与 reduced-motion 由自动化测试覆盖。

## 自动化验证

- 看板娘定向回归：8 个测试文件、31 项测试通过。
- 前端分组门禁：10 组、136 个测试文件、1,008 项测试通过，aggregate 校验通过。
- TypeScript 与生产构建通过。
- `git diff --check` 通过。
- 独立代码复审未发现 P0/P1/P2。

## 截图

| 文件 | 尺寸 | SHA-256 |
|---|---:|---|
| `01-haru-idle.png` | 1521 × 990 | `dcb3347c1a6b1b3fd04718316cb32b67b2debed298b6a18fdc6d35ce3d8047e3` |
| `02-haru-pilot-open.png` | 1536 × 1000 | `2d3e5bd3010e4b780d658dce5bad014750ef764d70af3550100b08a95f670b7a` |
| `03-haru-context-menu.png` | 1536 × 1000 | `5fec397c7d9d8fa269af9b070dde0be37e7c137231fd55dd3877af5a501a356f` |
| `04-default-pilot-after-hide.png` | 1521 × 990 | `1e1fbf018357ef0dc9d8ed4674b196c15d3278063e3a7a810de68572070dde52` |
| `05-settings-restore-haru.png` | 1521 × 990 | `f1e04de7ee4bd41586884ad0643a882d9d7beef2a2d89c861d612ca2cb674712` |

截图位于 `artifacts/2026-08-13-live2d-pilot-mascot/`。

## 发布边界

- 本期固定使用 Haru 受付版，不支持角色切换、用户导入、语音、口型或摄像头联动。
- README 与随包 NOTICE 已使用 Live2D 样例条款要求的完整指定声明。
- 正式发布前仍须由维护者按当前 Cubism SDK Publication License 留档确认发布主体与应用类别是否需要额外许可。
