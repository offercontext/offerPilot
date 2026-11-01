# 业务代码地图

用途：从用户描述的功能或故障，逐层找到业务边界、执行链、关键符号与回归测试。Agent 排查先读本页，再只读命中的分支。

核对基线：2026-10-28，`454af3d7`。本页记录该版本的源码导航，不代表线上版本、功能验收或发布状态。切换分支后先核对路径与符号；不要套用其他 worktree 的实现。

## 1. 如何进入

1. 记录问题发生的版本、用户入口、具体动作、预期与实际结果；可获得时记录请求路径、错误码、业务 ID。
2. 在下面按业务进入领域页，再按功能/症状下钻；模拟问答另有[深层排查链](interview-mock.md)。只有请求路径时先查[路由归属](route-coverage.md)。
3. 根据功能页的调用顺序查前端 service、HTTP handler、业务实现和持久化；用符号搜索定位，不依赖易漂移的行号。
4. 对照文档所链接的需求/契约、代码旁注释和相关测试，区分预期规则与当前实现。冲突单独记录，不能用当前代码自动证明预期正确。
5. 先找第一处与预期不符的观测，再决定修复位置。地图是调查入口，不是已有根因；没有日志、请求或复现证据时保留未知。

本层保存全局分流；十个领域页提供功能、调用链、失败/恢复边界和测试。当前核对了全部显式 API 注册、十四个页面 view、纯前端业务及桌面/CLI 入口，口径见[覆盖清单](route-coverage.md)。覆盖入口不等于每个函数都有注释，也不表示业务已通过运行验收。

## 2. 按业务定位

| 用户说法 / 业务域 | 首个入口与下钻方向 | 规则、测试或深层地图 |
|---|---|---|
| 投递、岗位、JD、创建重复、历史材料、结果、沟通草稿 | [投递与日程地图](applications.md) → 创建回执、版本、生命周期、提交快照及结果 | [原子录入 ADR](../decisions/0006-reliable-single-application-intake.md)、[创建测试](../../../tests/test_application_creation.py) |
| 简历导入、体检、事实补充、版本比较、材料包、改写建议 | [简历与材料地图](materials.md) → 原文分类、派生版本、模型分析、改写与提交证据 | [结构化导入测试](../../../tests/test_resume_structured_import.py)、[改写测试](../../../tests/test_material_revision_proposals_api.py) |
| 岗位匹配、初筛、深入分析、证据不足 | [匹配与 Offer 地图](opportunity-offers.md#2-岗位匹配-v2-执行与恢复) → 初筛生成、一次性确认、继承快照深入分析 | [匹配 API](../../../tests/test_opportunity_fit_reviews_api.py)、[AI 契约](../../../tests/test_opportunity_fit_reviews_ai.py) |
| 日程、事件、日历、提醒 | [投递日程](applications.md#3-生命周期与关联写入)；有日历事项却无待办时进入[提醒地图](workbench-automation.md) | [事件测试](../../../tests/test_events_api.py)、[日历测试](../../../tests/test_calendar_api.py)；事件语义见 [AGENTS §6](../../../AGENTS.md#6-领域红线) |
| 面试准备、模拟、追问、复盘、专项练习、经历故事 | [面试地图](interview.md) → 按功能和症状分流 | [模拟问答排查链](interview-mock.md) |
| 刷题、题库、复习 | [题库地图](questions.md) → 笔记生成、文本去重、评分和复习统计 | [题库 API](../../../tests/test_questions_api.py)；不要按模拟面试 Attempt 排查 |
| Offer、比较、谈薪 | [Offer 与谈薪地图](opportunity-offers.md#3-offer-比较与谈薪) → 维度、缺失值、提案生成与本地提纲确认 | [Offer API](../../../tests/test_offers_api.py)、[谈薪 API](../../../tests/test_offer_negotiation_api.py) |
| 知识资料、Evidence、知识笔记、记忆 | [Knowledge 与 Memory 地图](knowledge-memory.md) → 导入、抽取、生命周期、检索与上下文消费 | [Knowledge 架构](../knowledge-system.md)是领域事实源；产品知识功能与本代码地图职责不同 |
| Pilot 对话、工具调用、待确认、停止、恢复、图片 | [Pilot 地图](pilot-runtime.md) → 协议、接纳、来源、权限、执行、保存、确认与观察 | [Runtime 测试](../../../tests/pilot_runtime/)、[写操作测试](../../../tests/test_write_operations.py) |
| 邮件岗位、邮箱设置、同步、建议确认 | [邮件地图](job-mail.md) → 连接、同步、抽取、预览、明确确认与回执 | [同步测试](../../../tests/test_job_mail_sync.py)、[审阅测试](../../../tests/test_job_mail_review.py) |
| 模型设置、认证、技能、引导、备份、桌面更新 | [设置与桌面地图](settings-desktop.md) → 配置、公共入口、CLI、原生进程与安装保护 | [设置测试](../../../tests/test_settings_api.py)、[认证测试](../../../tests/test_auth_api.py) |
| 工作台、今日行动、下一步、后台主动任务、Wakeup | [工作台与任务地图](workbench-automation.md) → 页面聚合、只读候选、主动队列与标记派发 | [候选测试](../../../tests/test_reminder_candidates.py)、[队列测试](../../../tests/test_proactive_queue.py) |

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
