# 投递事实档案与结果反馈闭环设计

状态：已批准实施

## 1. 目标

把一次真实投递使用的 JD、简历和材料冻结为不可变档案，并让用户持续追加外部结果、原始反馈和“下次怎么做”。历史永不覆盖；系统只展示可核验事实与确定性统计，不生成成功率、岗位排名或能力分数。

## 2. 产品位置与范围

- 投递详情继续只管理单个投递；新增“投递事实与结果”入口，以 Drawer 展示，不改变现有页面布局。
- UI 与 Pilot 共用相同 Repository、API、幂等和人工确认语义。
- 第一期只完成档案、结果记录、来源变化和应用内汇总；不自动改投递状态，不调用 AI，不写 Knowledge、Memory、Story、Offer 或面试领域。
- Pilot 入口是确定性动作：用户在 Drawer 填好表单后选择“交给 Pilot 确认”，Pilot 展示确认卡；确认前 Provider 调用数为零。

## 3. 数据模型

### 3.1 ApplicationSubmissionSnapshot

每条记录冻结一次实际投递事实：

- `application_id`
- `resume_id` 与完整 `resume_snapshot_json`
- `jd_version_id` 与完整 `jd_snapshot`
- 可选 `material_kit_id` 与 `material_snapshot_json`
- `submitted_at`、`note`、`source_kind=ui|pilot`
- `idempotency_key`、`request_fingerprint_sha256`

同一投递内幂等键唯一。同 key 同 fingerprint 稳定重放；同 key 不同输入返回 `application_archive_idempotency_conflict`。

### 3.2 ApplicationOutcome

结果是追加式事实，绑定一份投递档案：

- `application_id`、`submission_snapshot_id`
- 可选 `application_event_id`
- `stage`：`applied|screening|written_test|interview|offer|closed`
- `result`：`advanced|rejected|withdrawn|no_response|offer_received|other`
- `feedback_text`：外部原始反馈；不解释、不补全
- `reflection_text`、`next_action_text`：用户观点
- `feedback_tags_json`：用户显式选择的固定标签
- `occurred_at`、`source_kind=ui|pilot`
- `idempotency_key`、`request_fingerprint_sha256`

结果不允许更新或删除；更正通过追加新记录并在说明中注明。

## 4. 来源与一致性

创建档案在单一 `BEGIN IMMEDIATE` 事务中校验归属并冻结：

- Resume 必须存在且未删除；
- JD Version 必须属于 Application；
- Material Kit 若提供，必须属于 Application；
- 所有 JSON 使用稳定 canonical 序列化和 SHA-256。

读取档案时派生 `current|changed|missing`：Resume 内容 hash、当前 JD Version ID、Material Kit 内容 hash 分别比较。冻结内容始终保留，来源变化不覆盖历史。

创建结果时校验 Snapshot 和 Event 均属于当前 Application。所有 enum、时间、UTF-8 长度、幂等键在 API 和 Repository 双重校验。

## 5. API

- `GET /api/applications/{id}/submission-snapshots`
- `POST /api/applications/{id}/submission-snapshots`
- `GET /api/applications/{id}/outcomes`
- `POST /api/applications/{id}/outcomes`
- `GET /api/applications/{id}/outcome-summary`

POST 成功为 201，稳定重放为 200。404 表示资源不存在，409 区分归属/来源冲突与幂等冲突，422 表示确定性输入错误。

## 6. UI

`ApplicationOutcomeDrawer` 包含三部分：

1. “本次实际投递”：选择已加载 Resume，默认当前 JD Version，可选当前 Material Kit；展示冻结来源确认。
2. “结果反馈”：绑定一份档案，选择阶段、结果、日期和固定标签，填写原始反馈、个人复盘、下次行动。
3. “反馈摘要”：只显示记录数、阶段/结果计数、重复标签和仍待验证的下一步，不输出评分或建议结论。

历史卡展示冻结 Resume/JD/Material 摘要和来源状态。表单提供“直接保存”和“交给 Pilot 确认”。未知结果保留原 key 并冻结；确定性冲突清理 key、保留文本。

## 7. Pilot

新增两个不可由模型主动调用的确定性动作：

- `create_application_submission_snapshot`
- `record_application_outcome`

前端显式传入 `pilot_action`，服务端构造 PendingAction；确认卡允许编辑非身份字段，确认后调用与 UI 相同的 Repository。取消不写业务表，确认/重挂载复用原 token 与 key。普通对话不因关键词自动触发写操作。

## 8. 验收

- Repository/API：冻结、归属、幂等、并发、来源变化、追加历史、零跨域写入。
- 前端：真实挂载、直接保存、Pilot 确认、刷新回读、错误恢复、零隐式写入。
- 浏览器：亮色中文“筱哲”案例，完成 UI 档案 → UI 结果 → Pilot 结果确认 → 历史与摘要，并保存宽屏截图。
- 完整静态检查、后端/前端门禁、构建、local smoke/verify；该功能本身 Provider 调用必须为零。

## 9. 破坏性变化

无。迁移为加法式 `0020_application_outcome_feedback`；现有投递、JD、材料、Pilot、面试和 Story 语义保持不变。
