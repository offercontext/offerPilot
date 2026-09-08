# 自适应面试练习发布验证

日期：2026-08-12
分支：`feat/20260812-adaptive-interview-practice`

## 交付范围

- 将已验收的控件级 UI 优化快进合入本地 `main@95f48a4`，未推送远端。
- 基于已保存的面试复盘与已确认 `practice_focuses`，确定性派生训练建议。
- 用户显式确认后创建练习；支持回答、练后复盘、语义化自评与只读完成历史。
- 保存冻结来源，并在读取时派生 `current / changed / missing`。
- 首页建议与练习工作区使用同一数据源；训练开始后从待练建议中消费对应 focus。
- 未引入综合能力分、录用概率、自动 AI 调用、Story Usage 或跨领域写入。

## 自动化验证

- 后端 Windows 五组门禁：`2061` collected；agent `454`、domain `73`、knowledge `659`（含 4 个既定 Windows symlink 权限 skip）、proposals `418`、misc `457`；aggregate 通过，分组并集与 manifest 一致。
- 前端 Windows 十组门禁：`125` 个文件、`947` 项测试；aggregate 通过，无遗漏或重复。
- 自适应练习 Repository/API 专项：`8 passed`。
- 自适应练习前端挂载专项：`5 passed`；面试列表内边距回归 `4 passed`。
- `uv run ruff check ...`、`uv run mypy src`、TypeScript、生产构建、`uv run oc smoke --static-dir web/dist`、`uv run oc verify --profile local --static-dir web/dist` 均通过。
- 独立代码复审最终未发现 P0/P1/P2。

## 中文亮色宽屏浏览器验收

使用隔离临时数据，候选人案例为“筱哲”，页面为亮色模式，视口 `1455×1200`：

1. 面试首页展示下一项复盘训练，并导航至练习工作区。
2. 显式确认后创建练习；填写中文回答与复盘，选择“更清楚了”。
3. 完成后关闭式刷新数据，完成历史可重新展开并显示冻结来源。
4. 浏览器控制台错误为 0。
5. 数据库仅新增 1 条 `adaptive_practice_plans`；Question、Knowledge、Interview Story、Application 与 Event 未产生额外写入。
6. 面试事件卡片复验中，卡片计算样式恢复为左右 `14px` 内边距，标题与边框实测间距为 `15px`（包含 `1px` 边框）。

| 截图 | 尺寸 | SHA-256 |
|---|---:|---|
| `01-interview-recommendation.png` | 1455×1200 | `a1f839dabe1c98bc9060d3ecb22f83d8b02d03a3bad7a2b6d66d997715f81219` |
| `02-active-practice.png` | 1455×1200 | `0d2a06ec72d031b4950e2b1636f31939b4bda339f1a4313d125648b22b6f4701` |
| `03-completed-history.png` | 1455×1200 | `830bdb8746648d703e039e33aa4e6e5f05be27e0f16ac9ef0f85201ae1002273` |
| `04-interview-card-padding-fix.png` | 1455×1200 | `08558579b2da0668d105d5306fa62cf52f1df253d31d3a7b0ca6f2aa4d838e14` |

## 破坏性变化与剩余风险

- 破坏性变化：无；仅新增迁移 `0022_adaptive_interview_practice`、新 API 与新前端工作区。
- 本功能不调用 Provider，因此不受外部模型稳定性影响；未执行真实 AI 验收，也不将其作为本功能通过证据。
- 仓库现有 `npm audit` 依赖告警与本功能无关，本轮未升级依赖。
