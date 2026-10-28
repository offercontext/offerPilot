# 业务覆盖与路由归属核对

[返回业务总图](README.md)

核对基线：2026-10-28，`454af3d7`。这是当前源码的静态导航覆盖，不是线上接口探测、测试通过率或发布证明。

## 1. 核对口径

- 遍历 `src/offerpilot/**/*.py` 的 HTTP 路由装饰器，展开 `runtime_base_path`；再展开 `context_sources/readiness_api.py` 的 `_PATHS` 和 `add_api_route` 方法列表。
- 按“方法 + 路径”的注册项计数。只纳入显式 `/api/` 入口；FastAPI 自带 OpenAPI/docs、静态资源和 SPA catch-all 不属于本表。
- 同一路径不同方法分别计数；Readiness 两组路径的别名均保留，不把静态覆盖当作接口应否兼容的产品决定。
- 路由下钻到对应业务页，再按符号找到真实实现与测试。表格按注册文件/前缀聚合，同一前缀可能分属不同业务，不能只按 URL 第一段决定归属。

本次核对 **261** 个 API 方法/路径注册项：装饰器 251，动态 Readiness 10；未归属项 **0**。另有 1 个 SPA catch-all，不计入业务 API。

| 业务地图 | API 注册项 |
|---|---|
| [投递、JD、日程和结果](applications.md) | 23 |
| [简历、分析与材料](materials.md) | 29 |
| [岗位匹配、Offer 与谈薪](opportunity-offers.md) | 23 |
| [面试全链路](interview.md) | 66 |
| [题库与复习](questions.md) | 9 |
| [Knowledge 与 Memory](knowledge-memory.md) | 29 |
| [Pilot、上下文和确认](pilot-runtime.md) | 44 |
| [邮件同步与审阅](job-mail.md) | 15 |
| [工作台、提醒与任务](workbench-automation.md) | 9 |
| [设置、认证与公共入口](settings-desktop.md) | 14 |

## 2. 注册位置与下钻

| 业务 | 注册文件 / 前缀 | 数量 | 定位符号示例 |
|---|---|---|---|
| [投递、JD、日程和结果](applications.md) | [api.py](../../../src/offerpilot/api.py) `/api/application-events` | 5 | `list_application_events` |
| [投递、JD、日程和结果](applications.md) | [api.py](../../../src/offerpilot/api.py) `/api/application-statuses` | 1 | `list_application_statuses` |
| [投递、JD、日程和结果](applications.md) | [api.py](../../../src/offerpilot/api.py) `/api/applications` | 16 | `list_applications` |
| [投递、JD、日程和结果](applications.md) | [api.py](../../../src/offerpilot/api.py) `/api/calendar` | 1 | `get_calendar` |
| [面试全链路](interview.md) | [api.py](../../../src/offerpilot/api.py) `/api/applications` | 17 | `list_interview_preparation_proposals` |
| [面试全链路](interview.md) | [api.py](../../../src/offerpilot/api.py) `/api/interview` | 3 | `list_voice_coaching_snapshots` |
| [面试全链路](interview.md) | [api.py](../../../src/offerpilot/api.py) `/api/interview-notes` | 3 | `get_review_readiness_candidates` |
| [面试全链路](interview.md) | [api.py](../../../src/offerpilot/api.py) `/api/interview-practice` | 6 | `get_readiness_practice_focus` |
| [面试全链路](interview.md) | [api.py](../../../src/offerpilot/api.py) `/api/interview-practice-cases` | 14 | `create_interview_practice_case` |
| [面试全链路](interview.md) | [api.py](../../../src/offerpilot/api.py) `/api/interview-stories` | 9 | `list_interview_stories` |
| [面试全链路](interview.md) | [api.py](../../../src/offerpilot/api.py) `/api/interview-story-proposals` | 4 | `create_interview_story_proposal` |
| [面试全链路](interview.md) | [api.py](../../../src/offerpilot/api.py) `/api/interview-story-sources` | 1 | `list_interview_story_sources` |
| [面试全链路](interview.md) | [api.py](../../../src/offerpilot/api.py) `/api/interviews` | 2 | `list_interviews` |
| [面试全链路](interview.md) | [api.py](../../../src/offerpilot/api.py) `/api/notes` | 7 | `list_notes` |
| [邮件同步与审阅](job-mail.md) | [api.py](../../../src/offerpilot/job_mail/api.py) `/api/job-mail` | 13 | `status` |
| [邮件同步与审阅](job-mail.md) | [secure_setup.py](../../../src/offerpilot/job_mail/secure_setup.py) `/api/job-mail` | 2 | `status` |
| [Knowledge 与 Memory](knowledge-memory.md) | [api.py](../../../src/offerpilot/api.py) `/api/knowledge` | 20 | `list_confirmed_interview_knowledge_notes` |
| [Knowledge 与 Memory](knowledge-memory.md) | [api.py](../../../src/offerpilot/api.py) `/api/notes` | 3 | `create_interview_knowledge_preview` |
| [Knowledge 与 Memory](knowledge-memory.md) | [api.py](../../../src/offerpilot/confirmed_memory/api.py) `/api/confirmed-memory` | 4 | `list_memory` |
| [Knowledge 与 Memory](knowledge-memory.md) | [note_api.py](../../../src/offerpilot/knowledge/note_api.py) `/api/knowledge` | 2 | `list_manageable_notes` |
| [简历、分析与材料](materials.md) | [api.py](../../../src/offerpilot/api.py) `/api/applications` | 11 | `get_application_material_kit` |
| [简历、分析与材料](materials.md) | [api.py](../../../src/offerpilot/api.py) `/api/jd` | 3 | `analyze_jd` |
| [简历、分析与材料](materials.md) | [api.py](../../../src/offerpilot/api.py) `/api/material-kits` | 1 | `update_material_kit` |
| [简历、分析与材料](materials.md) | [api.py](../../../src/offerpilot/api.py) `/api/resumes` | 14 | `create_resume` |
| [岗位匹配、Offer 与谈薪](opportunity-offers.md) | [api.py](../../../src/offerpilot/api.py) `/api/applications` | 5 | `create_opportunity_fit_review` |
| [岗位匹配、Offer 与谈薪](opportunity-offers.md) | [api.py](../../../src/offerpilot/api.py) `/api/offer-negotiation` | 2 | `get_offer_negotiation_proposal` |
| [岗位匹配、Offer 与谈薪](opportunity-offers.md) | [api.py](../../../src/offerpilot/api.py) `/api/offers` | 16 | `list_offers` |
| [Pilot、上下文和确认](pilot-runtime.md) | [api.py](../../../src/offerpilot/api.py) `/api/applications` | 1 | `recover_product_action_rejection_control` |
| [Pilot、上下文和确认](pilot-runtime.md) | [api.py](../../../src/offerpilot/api.py) `/api/chat` | 15 | `get_pilot_turn` |
| [Pilot、上下文和确认](pilot-runtime.md) | [api.py](../../../src/offerpilot/api.py) `/api/pilot` | 8 | `submit_runtime_turn` |
| [Pilot、上下文和确认](pilot-runtime.md) | [api.py](../../../src/offerpilot/api.py) `/api/product-actions` | 3 | `get_product_action` |
| [Pilot、上下文和确认](pilot-runtime.md) | [chat_image_routes.py](../../../src/offerpilot/chat_image_routes.py) `/api/chat` | 3 | `capability` |
| [Pilot、上下文和确认](pilot-runtime.md) | [api.py](../../../src/offerpilot/context_sources/api.py) `/api/context-policies` | 2 | `read_policies` |
| [Pilot、上下文和确认](pilot-runtime.md) | [readiness_api.py](../../../src/offerpilot/context_sources/readiness_api.py) `/api/chat/conversations/{conversation_id}/readiness-context[/clear]` | 5 | `read_context` |
| [Pilot、上下文和确认](pilot-runtime.md) | [readiness_api.py](../../../src/offerpilot/context_sources/readiness_api.py) `/api/conversations/{conversation_id}/readiness-context[/clear]` | 5 | `read_context` |
| [Pilot、上下文和确认](pilot-runtime.md) | [summary_api.py](../../../src/offerpilot/context_sources/summary_api.py) `/api/conversations` | 2 | `generate_summary` |
| [题库与复习](questions.md) | [api.py](../../../src/offerpilot/api.py) `/api/questions` | 9 | `list_questions` |
| [设置、认证与公共入口](settings-desktop.md) | [api.py](../../../src/offerpilot/api.py) `/api/auth` | 1 | `auth_status` |
| [设置、认证与公共入口](settings-desktop.md) | [api.py](../../../src/offerpilot/api.py) `/api/backups` | 1 | `export_backup` |
| [设置、认证与公共入口](settings-desktop.md) | [api.py](../../../src/offerpilot/api.py) `/api/health` | 1 | `health` |
| [设置、认证与公共入口](settings-desktop.md) | [api.py](../../../src/offerpilot/api.py) `/api/logs` | 1 | `get_logs` |
| [设置、认证与公共入口](settings-desktop.md) | [api.py](../../../src/offerpilot/api.py) `/api/onboarding` | 2 | `get_onboarding` |
| [设置、认证与公共入口](settings-desktop.md) | [api.py](../../../src/offerpilot/api.py) `/api/settings` | 4 | `get_settings` |
| [设置、认证与公共入口](settings-desktop.md) | [api.py](../../../src/offerpilot/api.py) `/api/skills` | 3 | `list_skills` |
| [设置、认证与公共入口](settings-desktop.md) | [chat_image_routes.py](../../../src/offerpilot/chat_image_routes.py) `/api/settings` | 1 | `test_image` |
| [工作台、提醒与任务](workbench-automation.md) | [api.py](../../../src/offerpilot/api.py) `/api/dashboard` | 1 | `get_dashboard` |
| [工作台、提醒与任务](workbench-automation.md) | [api.py](../../../src/offerpilot/api.py) `/api/wakeups` | 3 | `list_wakeups` |
| [工作台、提醒与任务](workbench-automation.md) | [api.py](../../../src/offerpilot/proactive/api.py) `/api/proactive` | 4 | `settings` |
| [工作台、提醒与任务](workbench-automation.md) | [api.py](../../../src/offerpilot/proactive/api.py) `/api/reminders` | 1 | `reminder_candidates` |

## 3. 非 HTTP 覆盖

| 核对面 | 范围与业务地图 |
|---|---|
| 全部 ViewMode | [navigation.ts](../../../web/src/layout/navigation.ts)：dashboard/reminders → [工作台](workbench-automation.md)；board/applications-list/calendar → [投递](applications.md)；interview/reviews → [面试](interview.md)；questions → [题库](questions.md)；offers → [Offer](opportunity-offers.md)；resumes → [材料](materials.md)；knowledge → [Knowledge](knowledge-memory.md)；pilot → [Pilot](pilot-runtime.md)；settings → [设置](settings-desktop.md) |
| 没有独立 API 的业务 | [沟通草稿](applications.md#4-归档反馈与沟通草稿)、[体检/事实补充/版本比较](materials.md#3-不经过独立模型接口的前端业务)、[下一步建议](workbench-automation.md#2-展示候选与前端建议) |
| 业务持久化 | `repositories/` 按上述领域拆分；共用 session_binding/json_contract/lease_heartbeat 由实际调用方下钻，不视作新的产品域 |
| 模型、Agent 和运行记录 | `ai/`、`agent_runtime/`、`pilot_runtime/`、`reliability/` → [Pilot](pilot-runtime.md)，业务专用 AI 继续进入对应领域 |
| 来源与知识 | `knowledge/`、`confirmed_memory/`、`context_sources/`、`context_projector/` → [知识与可选上下文](knowledge-memory.md) |
| 跨领域确认 | `product_actions/`、`review_readiness/` → [Pilot 确认](pilot-runtime.md#3-确认恢复和停止边界)和[面试闭环](interview.md#5-复盘建议与下一轮准备) |
| 任务和导入 | `proactive/` → [工作台](workbench-automation.md)；`job_mail/` → [邮件](job-mail.md) |
| 桌面、CLI、全局设置 | `desktop/`、cli.py、config.py、skills.py、db.py → [设置与桌面](settings-desktop.md) |

## 4. 保持地图有效

新增页面/API、改变调用归属、业务状态或恢复边界时更新对应领域页；新增路由需重新核对本表。可先用 `rg -n 'add_api_route|include_router|register_.*routes|runtime_base_path' src/offerpilot` 检查装配，再用 AST 枚举装饰器，不能只搜索主 api.py。

就近注释覆盖非显然的业务规则和失败边界，已有清晰说明沿用；不要求给每个 getter/序列化函数重复添加注释。本表的“未归属项 0”只证明清单中的入口都有地图归属，不表示每行代码都有注释或所有业务已通过运行验收。
