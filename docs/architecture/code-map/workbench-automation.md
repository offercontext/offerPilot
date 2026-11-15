# 工作台、提醒与后台任务地图

[返回业务总图](README.md) · [投递日程](applications.md) · [Pilot Runtime](pilot-runtime.md)

核对基线：2026-10-28，`454af3d7`。依据：[有界主动任务 ADR](../decisions/0012-bound-local-proactive-jobs.md)及其“共享提醒事实”修订。

## 1. 用户看到的四种能力

| 用户说法 | 实现入口 | 职责边界 |
|---|---|---|
| 今日概览、看板漏斗、下一步 | [dashboard](../../../web/src/features/dashboard/)、[pipelineInsights](../../../web/src/lib/pipelineInsights.ts)、[nextStepSuggestions](../../../web/src/lib/nextStepSuggestions.ts)；`get_dashboard` 在 [api.py](../../../src/offerpilot/api.py) | 页面聚合/建议，不自动执行推荐动作 |
| 今日待办、截止、面试提醒 | [RemindersView](../../../web/src/features/reminders/RemindersView.tsx)、[reminders service](../../../web/src/services/reminders.ts) → [proactive API](../../../src/offerpilot/proactive/api.py) `/api/reminders/candidates` | 只读候选，不要求启用主动任务，不花模型预算 |
| 自动提醒、后台准备草稿 | [ProactiveSettings](../../../web/src/components/ProactiveSettings.tsx)、[proactive service](../../../web/src/services/proactive.ts) → [ProactiveRepository](../../../src/offerpilot/proactive/repository.py) / [ProactiveRuntime](../../../src/offerpilot/proactive/runtime.py) | 用户范围、静默时间、预算、队列与执行权决定是否运行 |
| Wakeup 到期派发 | `/api/wakeups` / `dispatch_due_wakeups` → [WakeupsRepository.dispatch_due](../../../src/offerpilot/repositories/wakeups.py) | 标记到期记录 dispatched 并返回；不是主动队列，也不在这里发送通知或执行 payload |

## 2. 展示候选与前端建议

[proactive/candidates.py](../../../src/offerpilot/proactive/candidates.py) 是共享资格入口：`reminder_source` / `source_candidate` / `upcoming_event_candidate` / `offer_candidate` → `list_display_candidates`。主体可为 application/application_event/offer，各有来源 revision、展示窗口和投递窗口；后两者不能合并。

Offer 日期截止按请求 IANA 展示时区解释；Offer 和部分日程候选只有展示资格，没有后台投递窗口。普通日历有事件也不保证当前存在待办候选。规则固定样例见 [reminder-candidates.v1.json](../../../contracts/reminder-candidates.v1.json)。

前端 `deriveMaterialKitFact` 把未加载完整/读取失败保留为 unknown；完整且没有匹配项才是 known-null。下一步建议不能因请求失败就推荐重建已存在成果。页面推荐的目标跳转、草稿所有者和后续用户动作仍需单独检查。

## 3. 主动任务执行链

`ProactiveRuntime.tick` → `discover` → `claim` → `begin` → 确定性提醒或 `_dispatch` 模型草稿 → `publish`。

- 本地服务运行时才检查，启动不补发长期积压；单次 tick 派发有上限。
- 普通提醒不调用模型；面试草稿用共享有界 Runtime，不带工具，不自动写正式面试/Knowledge 成果。
- 领取前、执行前、发布前均检查启用范围和来源。`publish` 同时核验 owner/turn/generation/租约/来源摘要/期限/静默时段及预算。
- `_dispatch` 的未知结果保留待核对；不能退款重置预算后静默再调用。关闭功能或源变化要取消持久任务并请求停止，迟到结果仍由发布条件拒绝。
- `stop_cancelled` 先持久化来源失效，再请求执行器停止；只改展示状态不能代替撤销提交权。

## 4. 症状、证据与回归

| 症状 | 首查证据 | 测试 |
|---|---|---|
| 今日无提醒 / 重复显示 | subject/reason、source_revision、display/delivery 窗口、请求时区、读取状态 | [候选规则](../../../tests/test_reminder_candidates.py)、[前端 service](../../../web/src/services/reminders.test.ts)、[页面](../../../web/src/features/reminders/RemindersView.test.tsx) |
| 关设置后仍显示结果 / 重启重复花费 | policy revision、job state、预算、owner/lease/turn/generation、来源变化 | [队列](../../../tests/test_proactive_queue.py)、[设置页面](../../../web/src/components/ProactiveSettings.test.ts) |
| 推荐了错误下一步 | known/unknown/known-null、目标投递、读取完整性与草稿 owner | [建议规则](../../../web/src/lib/nextStepSuggestions.test.ts)、[投递交互](../../../web/src/components/ApplicationDetail.nextStepSuggestions.test.tsx) |
| Wakeup 标记完成但未收到通知 | 实际调用是否只有 dispatch_due，是否存在独立消费者 | [Wakeup API](../../../tests/test_wakeups_api.py)；不能从 dispatched 推导外部发送成功 |
