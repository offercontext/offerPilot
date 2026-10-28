# 业务代码地图

用途：从用户描述的功能或故障，逐层找到业务边界、执行链、关键符号与回归测试。Agent 排查先读本页，再只读命中的分支。

核对基线：2026-10-28，`054bf311`。本页记录该版本的源码导航，不代表线上版本、功能验收或发布状态。切换分支后先核对路径与符号；不要套用其他 worktree 的实现。

## 1. 如何进入

1. 记录问题发生的版本、用户入口、具体动作、预期与实际结果；可获得时记录请求路径、错误码、业务 ID。
2. 在下面按业务找入口；面试继续进入[面试地图](interview.md)，模拟问答继续进入[模拟问答排查链](interview-mock.md)。
3. 根据功能页的调用顺序查前端 service、HTTP handler、业务实现和持久化；用符号搜索定位，不依赖易漂移的行号。
4. 对照文档所链接的需求/契约、代码旁注释和相关测试，区分预期规则与当前实现。冲突单独记录，不能用当前代码自动证明预期正确。
5. 先找第一处与预期不符的观测，再决定修复位置。地图是调查入口，不是已有根因；没有日志、请求或复现证据时保留未知。

本层只保存主要业务入口；面试已有子功能映射与模拟问答深层链路，其他领域暂为首层导航，不表示已覆盖其全部逻辑。

## 2. 按业务定位

| 用户说法 / 业务域 | 首个入口与下钻方向 | 规则、测试或深层地图 |
|---|---|---|
| 投递、岗位、JD、创建重复、岗位资料版本 | [applications service](../../../web/src/services/applications.ts) → [创建协调](../../../src/offerpilot/repositories/application_creation.py)、[投递存储](../../../src/offerpilot/repositories/applications.py)、[JD 版本](../../../src/offerpilot/repositories/application_jd_versions.py) | [原子录入 ADR](../decisions/0006-reliable-single-application-intake.md)、[API 测试](../../../tests/test_applications_api.py) |
| 简历导入、简历版本、材料包、改写建议 | [resumes service](../../../web/src/services/resumes.ts)、[材料 service](../../../web/src/services/materialKits.ts)、[改写 service](../../../web/src/services/materialRevisionProposals.ts) → [简历存储](../../../src/offerpilot/repositories/resumes.py)、[改写提案](../../../src/offerpilot/repositories/material_revision_proposals.py) | [简历测试](../../../tests/test_resumes_api.py)、[改写 API 测试](../../../tests/test_material_revision_proposals_api.py) |
| 日程、事件、日历、提醒 | [events service](../../../web/src/services/events.ts)、[calendar service](../../../web/src/services/calendar.ts) → [事件存储](../../../src/offerpilot/repositories/application_events.py)；[reminders service](../../../web/src/services/reminders.ts) → [proactive API](../../../src/offerpilot/proactive/api.py) 的候选提示。另有 [Wakeup 存储](../../../src/offerpilot/repositories/wakeups.py)，需按实际请求区分 | [事件测试](../../../tests/test_events_api.py)、[日历测试](../../../tests/test_calendar_api.py)；事件语义见 [AGENTS §6](../../../AGENTS.md#6-领域红线) |
| 面试准备、模拟、追问、复盘、专项练习、经历故事 | [面试地图](interview.md) → 按功能和症状分流 | [模拟问答排查链](interview-mock.md) |
| 刷题、题库、复习 | [questions service](../../../web/src/services/questions.ts) → [题库存储](../../../src/offerpilot/repositories/questions.py) | [题库 API 测试](../../../tests/test_questions_api.py)；不要按模拟面试 Attempt 排查 |
| Offer、比较、谈薪 | [offers service](../../../web/src/services/offers.ts) → [Offer 存储](../../../src/offerpilot/repositories/offers.py)、[比较](../../../src/offerpilot/repositories/offer_comparison.py)、[谈薪](../../../src/offerpilot/repositories/offer_negotiation.py) | [Offer API 测试](../../../tests/test_offers_api.py)、[谈薪 API 测试](../../../tests/test_offer_negotiation_api.py) |
| 知识资料、Evidence、知识笔记、记忆 | [Knowledge 架构](../knowledge-system.md) → [knowledge service](../../../web/src/services/knowledge.ts)、[Knowledge 实现](../../../src/offerpilot/knowledge/)、[确认记忆](../../../src/offerpilot/confirmed_memory/) | [知识来源测试](../../../tests/test_knowledge_sources_api.py)；这是产品知识功能，与本代码地图职责不同 |
| Pilot 对话、工具调用、待确认、停止、恢复、图片 | [pilotRuntime service](../../../web/src/services/pilotRuntime.ts)、[chat service](../../../web/src/services/chat.ts)、[图片 service](../../../web/src/services/chatImages.ts) → [Pilot Runtime](../../../src/offerpilot/pilot_runtime/)、[Agent Loop](../../../src/offerpilot/ai/agent_loop.py)、[写操作](../../../src/offerpilot/ai/write_operations.py) | [Runtime 测试](../../../tests/pilot_runtime/)、[写操作测试](../../../tests/test_write_operations.py)、[历史源码导读](../../learn/advanced/README.md) |
| 邮件岗位、邮箱设置 | [jobMail service](../../../web/src/services/jobMail.ts) → [job_mail API](../../../src/offerpilot/job_mail/api.py) → [job_mail](../../../src/offerpilot/job_mail/) | 在 [HTTP 装配](../../../src/offerpilot/api.py) 注册；按实际请求区分邮件建议、导入与投递创建 |
| 模型设置、认证、运行配置 | [AISettingsDrawer](../../../web/src/components/AISettingsDrawer.tsx)、[chat service 中的 settings 请求](../../../web/src/services/chat.ts)、[auth service](../../../web/src/services/auth.ts) → [配置](../../../src/offerpilot/config.py)、[HTTP 装配](../../../src/offerpilot/api.py) | [设置测试](../../../tests/test_settings_api.py)、[认证测试](../../../tests/test_auth_api.py) |
| 工作台、今日行动、下一步、后台主动任务 | [dashboard](../../../web/src/features/dashboard/)、[reminders service](../../../web/src/services/reminders.ts)、[proactive service](../../../web/src/services/proactive.ts) → [proactive](../../../src/offerpilot/proactive/) | 先区分页面聚合、时间派生提示与实际后台执行，再查对应 API |

## 3. 共用的定位方法

多数 HTTP handler 仍集中在 [api.py](../../../src/offerpilot/api.py) 的 `create_app` 内；部分功能通过 `register_*_routes` 注册。先读前端 service 的真实路径，再搜索路由，不假设每个业务已有独立 router 文件。

示例，在仓库根目录执行：

```powershell
rg -n 'mock-interview/attempts|generate_mock_interview_question' src/offerpilot/api.py
rg -n 'def (claim_question|complete_question)|class MockInterviewRepository' src/offerpilot/repositories/mock_interviews.py
rg -n 'duplicate_question|missing_latest_turn_evidence' src/offerpilot/ai/mock_interview.py tests/test_mock_interview_ai.py
```

| 要回答的问题 | 应查的证据 |
|---|---|
| 页面有没有发起正确操作？ | UI 事件、service 参数、HTTP 请求与响应；不要仅看按钮文字 |
| 后端是否允许执行？ | 输入解析、归属/可见性、来源版本、确认和幂等分支 |
| 模型实际读了什么？ | 快照构造、Provider 输入投影、证据目录、提示词和校验器 |
| 结果是否真正保存？ | Repository 提交条件、业务记录、状态与回执；模型返回成功不等于落库 |
| 重试应该做什么？ | [恢复策略契约](../../../contracts/recovery-policy.v1.json)、前后端消费者及原操作身份 |
| 什么验证能证明修复？ | 功能页指出的失败边界测试，加上 [AGENTS §7](../../../AGENTS.md#7-验证与-code-review) 的影响范围要求 |

## 4. 文档与代码注释怎样分工

- 地图保存“业务词汇 → 入口 → 调用关系 → 关键规则所在位置 → 测试”，不复制整份 PRD、接口字段表或所有函数。
- 功能页说明正常链路、失败/恢复分支，以及每个阶段要查看的标识和状态。
- 代码旁注释解释局部判断的业务原因、不变量和副作用边界，并回链功能页。避免逐行翻译代码，也不把暂时推测写成业务规则。
- 需求依据沿用 [AGENTS §4](../../../AGENTS.md#4-事实源) 的飞书及相关实施契约；本地图不成为另一份产品事实源。[Python rewrite 契约](../../python-rewrite-contract.md)及教程的历史基线不能当作当前全量接口目录。

同步维护要求见[文档规范：业务代码地图](../documentation-rules.md#7-业务代码地图与就近注释)。新增深层页面时必须从本页或对应业务页可达。
