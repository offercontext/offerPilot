# 早期面试版本范围（历史记录）

状态：历史规划摘录，2026-09-10 从 `AGENTS.md` 的领域红线移入。
以下是原阶段划分，不是当前功能禁令、完成状态或新的产品授权。
当前范围按 [AGENTS.md §4](../../AGENTS.md#4-事实源) 的 PRD / ADR 及相关实施契约核对。

- v0.1：左栏展示面试入口，进入后展示空状态 / 占位页。保存操作可以 no-op 或保存本地占位状态，但不创建正式 `interview_notes` 或 `mock_sessions` 数据。
- v0.2：面试笔记 CRUD、Agent 追问、事件绑定、弱点信号写入。
- v0.3：模拟面试、谈薪、录音 / 转写能力。

[P0 清单](../p0-release-checklist.md#browser-product-walkthrough)中的面试空状态走查
属于当时的 v0.1 验收快照，不应据此回退或禁止后续已按产品契约实现的功能。
HITL、数据安全和恢复链路约束仍由现行 [AGENTS.md](../../AGENTS.md) 维护。
