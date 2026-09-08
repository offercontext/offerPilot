# 沉浸式模拟面试工作台发布验证报告

日期：2026-08-15
分支：`feat/20260814-immersive-interview-studio`
基线：`af8f0d1e`

## 结论

本轮完成了 Schema 失败诊断、正常复盘闭环、分组门禁、宽屏/窄屏浏览器复验和独立代码复审。当前未推送、未合并；建议在审核本报告后合并，不执行自动推送。

## Schema 诊断与复盘闭环

- Harness 的失败日志为 `mock_interview_contract_failure`、`stage=feedback`、`failure_category=wrong_schema_version`，响应没有满足产品严格反馈契约；根因是 Harness 响应不合规，不是产品契约过严。
- 新增正常反馈模型回归：严格校验 `strengths`、`practice_points`、`next_practice_steps` 及 evidence refs，并通过 Practice Case API 完成正常反馈持久化。
- 使用中文候选人“筱哲”完成真实投递 5 轮文本面试，回答确认、追问依据、完成复盘建议均正常展示；原有结果未知恢复入口仍保留。

## 本轮收口

- 题目生成改为返回已验证的结构化 `{question, evidence_refs}`，并写入现有题目来源快照；追问同时展示上一轮回答引用与冻结来源。
- 修复快速练习创建重试的幂等 key、快速反馈结果未知错误码、来源变更竞态和 202/StrictMode 工作状态竞态。
- 语音复盘未知保存保留原 attempt/key，并提供可执行重试；恢复信息写入 sessionStorage。
- 语音恢复信息同时保存 `attemptId`，Studio 重新挂载后仍可使用原 key 执行重试；存在未确认回答或待恢复语音保存时，Escape/退出按钮会先提示确认。
- 扩充 Haru Studio 安全区和 ResizeObserver 约束，增加题目、依据、追问入口的避让目标。
- Studio 证据栏改为受约束的独立抽屉滚动容器；1440 宽屏以及 390 窄屏下均不覆盖固定回答区。窄屏改为单一内容滚动，证据抽屉固定预览高度并可展开/滚动查看。
- 保留现有证据门控、人工确认、幂等键、lease/CAS/fencing、结果未知恢复和历史只读语义。

## 浏览器验收

内置浏览器使用亮色、中文候选人“筱哲”完成真实投递流程；快速练习流程与隔离边界截图保留在既有验收目录中。

- 1440×900：真实投递 5 轮、完成复盘；`scrollWidth=1440`，证据栏与回答区无几何重叠。
- 390×844：窄屏复验；`scrollWidth=390`，Haru 在窄屏断点隐藏，证据栏和固定回答区无几何重叠。
- 追问可见“上一轮回答 → 当前追问”、冻结 JD/简历依据和逐字摘录；普通页面与 Studio 位置记忆及拖动验收截图均已保留。

截图：

- [1440×900 完成复盘](../../artifacts/2026-08-14-immersive-interview-studio/36-acceptance-1440x900-normal-feedback-light-final.png)
- [390×844 窄屏无重叠](../../artifacts/2026-08-14-immersive-interview-studio/35-acceptance-390x844-normal-feedback-light-final.png)
- [窄屏完成复盘历史截图](../../artifacts/2026-08-14-immersive-interview-studio/31-acceptance-390x844-normal-feedback-light.png)
- [快速练习 Studio](../../artifacts/2026-08-14-immersive-interview-studio/19-system-closeout-quick-studio-light.png)

## 分组门禁

- 后端：Schema/Mock Interview/Practice Case 定向组 64 passed；浏览器 Harness/Studio API 组 43 passed；`test_smoke.py` 58 passed；Chat API 分 7 组共 250 passed；KI11 22 passed；其余既有 AI、知识、故事、投递、Offer、机会匹配、迁移和 worker 分组均通过，详见本次终端记录。
- 前端：Studio/Readiness/Mascot 及相关组件 14 files、91 tests passed；构建 `npm.cmd run build` 通过（3938 modules transformed）。
- `uv run ruff check .` 通过；`uv run mypy src` 通过（73 files）；`git diff --check` 通过。
- 完整仓库没有再使用单次全量命令；采用分组门禁避免超时。

已知前置条件：`tests/test_application_jd_browser_harness.py::test_application_jd_implementation_scope_is_machine_checked` 在 main 与本分支均需要外部提供 `OFFERPILOT_APPLICATION_JD_BASELINE_FILE` 与 `OFFERPILOT_APPLICATION_JD_ALLOWLIST_FILE`。README cutover 检查已在本分支及合入后的 main 通过；此前的文案差异已恢复为契约要求并保留语音说明。

## 破坏性变化与剩余风险

- 无产品破坏性变化；本轮没有数据库迁移，旧 Mock Interview 与 Voice Coaching 数据保留。
- 证据栏在窄屏/宽屏均是可滚动抽屉；长文本展开会增加抽屉内容高度，这是允许的第二个滚动容器。
- 当前浏览器验收使用本地 Harness 数据；真实 Provider、麦克风权限和离线模型未在本报告中宣称通过。
- 验收专用 `.tmp-interview-acceptance-data-20260814` 已在验证结束后删除，相关临时服务也已停止；截图和报告文件保留。

## 合并评估

独立代码复审发现并已修复语音恢复缺少 `attemptId` 及退出前缺少确认提示两个问题，复审后的结论未发现新的 P0/P1 阻塞项。分支已 fast-forward 合入本地 main，未推送。
