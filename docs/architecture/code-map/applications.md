# 投递、JD 与日程地图

[返回业务总图](README.md) · [材料与简历](materials.md) · [工作台与提醒](workbench-automation.md)

核对基线：2026-10-28，`454af3d7`。以下是源码导航与当前行为，不代表线上或发布验收。需求以 [AGENTS §4](../../../AGENTS.md#4-事实源) 为准。

## 1. 业务分流

| 用户说法 | 对象与边界 | 前端入口 |
|---|---|---|
| 新建投递、重复岗位、看板阶段 | `Application`；重复提示与创建幂等分开 | [AddApplicationForm](../../../web/src/components/AddApplicationForm.tsx)、[ApplicationDetail](../../../web/src/components/ApplicationDetail.tsx)、[applications service](../../../web/src/services/applications.ts) |
| 岗位描述变了、历史 JD | `ApplicationJDVersion`；追加版本，不覆盖历史 | [JD service](../../../web/src/services/applicationJdVersions.ts) |
| 笔试、测评、面试、截止、日历 | `ApplicationEvent`；`event_type + subtype + tags`，测评是 written_test 的 subtype | [events](../../../web/src/services/events.ts)、[calendar](../../../web/src/services/calendar.ts) |
| 当时投了哪份简历、反馈、结果 | `ApplicationSubmissionSnapshot` 与 `ApplicationOutcome`；不是实时简历或 Application 状态 | [ApplicationOutcomeDrawer](../../../web/src/components/ApplicationOutcomeDrawer.tsx)、[outcomes service](../../../web/src/services/applicationOutcomes.ts) |
| 跟进邮件、感谢信草稿 | 前端根据已选事实生成的可编辑文案；无发送接口 | [ApplicationCommunicationDraftPanel](../../../web/src/components/ApplicationCommunicationDraftPanel.tsx) |

## 2. 创建和版本链

1. `GET /api/applications/creation-context` 获取创建范围；`/duplicates` 只供用户判断。前端恢复见 [applicationCreationRecovery](../../../web/src/services/applicationCreationRecovery.ts)。
2. [api.py](../../../src/offerpilot/api.py) `create_application` → [ApplicationCreationService.create](../../../src/offerpilot/repositories/application_creation.py)：校验请求身份后，同事务保存投递、可选首版 JD 与创建回执。
3. 同 key 同请求恢复回执；不同输入不能复用旧 key。网络结果未知先查询/恢复原请求，不能靠重复点击换 key。
4. 后续 JD 通过 `create_application_jd_version` → [ApplicationJDService.create_version](../../../src/offerpilot/repositories/application_jd_versions.py) 追加；新请求携带 `expected_current_version_id`，旧指针冲突。
5. JD 分析、岗位匹配、准备建议和材料生成各自关联该版本；JD 更新后的来源失效要到消费方检查，见[材料](materials.md)、[岗位匹配](opportunity-offers.md)、[面试](interview.md)。

依据：[可靠单次录入 ADR](../decisions/0006-reliable-single-application-intake.md)、[JD 版本设计](../../superpowers/specs/2026-08-05-application-jd-versions-design.md)。设计文档保留历史状态，字段以当前代码和测试核对。

## 3. 生命周期与关联写入

| 动作 / 症状 | 调用位置 | 关键边界与观察点 |
|---|---|---|
| 关闭后改回阶段、阶段时间异常 | [ApplicationsRepository.update_full](../../../src/offerpilot/repositories/applications.py) | 当前 closed 是不可重开终态；首次关闭原因、首次阶段时间与后续编辑时间分开检查 |
| 删除后关联内容是否可见 | `ApplicationsRepository.delete` / `delete_if_matches` | 软删除父投递；撤销创建还检查原快照和新增关联业务，不能抹掉后续工作 |
| Agent 操作错投递 | `list_applications_scoped`、[ApplicationEventsRepository](../../../src/offerpilot/repositories/application_events.py) 的 scoped 方法 | 授权范围进入最终 SQL；请求的 application_id 不构成授权；参考[工具作用域](pilot-runtime.md) |
| 删除日程后笔记消失或仍绑定 | `ApplicationEventsRepository.delete_application_event_scoped` → `_delete_application_event_owned` | 同事务解绑笔记、增加修订号并删除事件；同一所有权条件覆盖两种修改 |
| 日历有事项，今日待办没有 | [api.py](../../../src/offerpilot/api.py) `get_calendar` 与 [提醒候选](../../../src/offerpilot/proactive/candidates.py) | 日历事件列表和具备时间/状态资格的候选不同，继续按[提醒地图](workbench-automation.md)查 |

事件基础接口为 `/api/application-events`，不是长期兼容 `/events`；面试问答的事件嵌套路由见[面试地图](interview.md)。

## 4. 归档、反馈与沟通草稿

`create_application_submission_snapshot` → [ApplicationOutcomesRepository.create_snapshot](../../../src/offerpilot/repositories/application_outcomes.py) 冻结简历/JD/可选材料包与正文摘要；后来改源不重写历史。`create_outcome` 要求快照和可选日程属于同一投递，只追加结果，不自动改变投递阶段。

[buildApplicationCommunicationDraft](../../../web/src/lib/applicationCommunicationDraft.ts) 只用选中的档案、面试结果、可选日程和用户填写的亮点生成跟进/感谢文案。复制到剪贴板不代表邮件发送，也不创建后台任务。来源失效后旧草稿的 UI 清理在 `ApplicationCommunicationDraftPanel`。

依据：[结果反馈设计](../../superpowers/specs/2026-08-12-application-outcome-feedback-design.md)、[沟通草稿设计](../../superpowers/specs/2026-08-12-application-communication-drafts-design.md)。

## 5. 症状与回归入口

| 问题 | 最小定位证据 | 回归入口 |
|---|---|---|
| 重复创建 / 结果未知 | scope、idempotency_key、原请求指纹、回执、application_id | [创建事务](../../../tests/test_application_creation.py)、[创建前端](../../../web/src/services/applicationCreation.test.ts)、[恢复](../../../web/src/services/applicationCreationRecovery.test.ts)、[投递 API](../../../tests/test_applications_api.py) |
| JD 版本冲突 | expected/current version、消费方冻结版本 | [版本事务](../../../tests/test_application_jd_versions_repository.py)、[API](../../../tests/test_application_jd_versions_api.py)、[JD service 测试](../../../web/src/services/applicationJdVersions.test.ts) |
| 事件越权 / 删除解绑 | 父投递可见性、事件 ID、笔记 revision、事务结果 | [事件 API](../../../tests/test_events_api.py)、[工具写入范围](../../../tests/tool_authority/test_scoped_writes.py)、[日历](../../../tests/test_calendar_api.py) |
| 历史材料或结果不符 | snapshot ID、来源 ID/hash、outcome 所属关系 | [归档 Repository](../../../tests/test_application_outcomes_repository.py)、[API](../../../tests/test_application_outcomes_api.py) |
| 沟通文案不符 | 选中 snapshot/outcome/event 和用户输入 | [草稿规则](../../../web/src/lib/applicationCommunicationDraft.test.ts)、[页面交互](../../../web/src/components/ApplicationCommunicationDraftPanel.test.tsx) |
