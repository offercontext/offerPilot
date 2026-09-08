# Opportunity Fit Review 设计

日期：2026-07-21
状态：已确认，进入实现
范围：Application 上游岗位决策漏斗；不重做 Evidence Bundle 与 Evidence-gated Material Proposal。

## 目标与边界

用户只能对已经手动创建且未软删除的 Application 发起岗位评估。系统接收用户粘贴的 JD、用户选择的 Resume 和最多 10 条候选人断言，先生成可审计的 Triage，再由用户显式触发 Deep Review。两个阶段都是只读分析记录：不改变 Application 状态、不创建或更新 Material Kit、不访问 URL 或招聘平台、不接入 Pilot 写工具。

决策链路为：

```text
已有 Application → Triage → Deep Review → 用户选择是否去准备材料
```

“去准备材料”只把冻结的 Resume/JD 预填到现有 Material Kit 界面，仍由用户主动生成材料、审阅 Proposal 和确认投递。

## 不变量

1. 每条评估记录保存 Application、Resume、JD 和用户断言的最小不可变快照；来源后续变化不改变历史结果。
2. 所有模型主张必须引用冻结快照中的 JD、Resume 或用户断言；用户断言始终以独立来源标记“用户提供，未外部核验”。
3. JD 只决定岗位约束、分析方向和表达重点，不能作为候选人经历事实。
4. Triage 的 `advance` 不得包含 `unmet` 硬约束；`hold` 必须包含未确认项或待回答问题；`decline` 必须有带引用的阻断理由。
5. 模型输出严格 JSON，拒绝 fenced Markdown、额外字段、非字符串字段、非有限数值、非法路径、错误摘录和未提供的事实；仅格式/结构失败允许一次修复调用，Provider 失败不重试。
6. 模型调用期间来源发生变化不影响已生成的历史快照；写入阶段使用 `BEGIN IMMEDIATE` 重新检查 Application 可见性，软删除竞态返回 404 且不留下孤儿记录。
7. 相同 `(application_id, idempotency_key)` 的 Triage 请求幂等返回原记录；已有 Deep Review 时重复请求返回同一记录，不重复调用模型或写记录。

## 数据模型

新增 `opportunity_fit_reviews` 表和 `OpportunityFitReview` 模型：

| 字段 | 说明 |
| --- | --- |
| `id` | 自增主键 |
| `application_id` | `applications.id` 外键；Application 软删除后所有评估读取不可见 |
| `resume_id` | 可空外键，保留 provenance；Resume 软删除后快照仍可读 |
| `idempotency_key` | 与 `application_id` 联合唯一的 UUID |
| `source_fingerprint_sha256` | 冻结快照 canonical JSON SHA-256 |
| `source_snapshot_json` | 最小输入快照 |
| `triage_json` / `triage_sha256` | 严格校验后的 Triage |
| `deep_review_json` / `deep_review_sha256` | 可空；严格校验后的 Deep Review |
| `created_at` / `deep_reviewed_at` | 审计时间 |

快照只包含：

```json
{
  "schema_version": 1,
  "application": {"id": 42, "company_name": "示例公司", "position_name": "后端工程师"},
  "resume": {"id": 7, "title": "后端简历", "content_json": {}, "sha256": "..."},
  "jd": {"source_label": "招聘方页面复制", "text": "用户粘贴的 JD", "sha256": "..."},
  "candidate_assertions": [{"index": 0, "text": "可在上海全职工作"}]
}
```

不发送 Application 普通备注、对话历史、其他 Resume、账号信息或 URL 内容。

使用现有加法迁移记录 `0008_opportunity_fit_reviews`；不修改 Evidence Bundle、Material Kit、Application 或已有 Resume Match 数据语义。

## 模型契约

Triage 顶层字段严格为 `summary`、`recommendation`、`hard_constraints`、`fit_signals`、`gaps`、`deadline`、`next_questions`。推荐值仅为 `advance | hold | decline`。数组对象字段集合固定：

- `hard_constraints`: `id`, `requirement`, `status`, `explanation`, `evidence_refs`；status 为 `met | unmet | unknown`。
- `fit_signals`: `id`, `statement`, `evidence_refs`。
- `gaps`: `id`, `requirement`, `kind`, `candidate_status`, `evidence_refs`；kind 为 `required | preferred`，candidate_status 为 `met | unmet | unknown`。
- `deadline`: `status`, `text`, `evidence_refs`；status 为 `stated | not_stated`。
- `next_questions`: 非空字符串数组。

证据引用字段固定为 `source`, `path`, `excerpt`：`source` 只能是 `jd | resume | user_assertion`；JD 只能引用 `/text`，Resume 只能引用 `content_json` 的相对 JSON path，用户断言只能引用 `/user_assertions/<index>/text`。路径必须存在，摘录必须逐字符等于冻结快照值。未知/未找到的项目可以为空引用；所有已声明事实和明确 gap 必须有引用。

Deep Review 顶层字段严格为 `strengths`, `gaps_to_address`, `questions_to_clarify`, `recommended_path`, `next_actions`；推荐路径仅为 `prepare_materials | clarify_first | do_not_pursue`。每个分析条目只允许 `id`, `statement`, `evidence_refs`；行动只允许 `id`, `label`, `kind`，kind 为 `open_material_kit | add_assertion | record_deadline`，且只产生本地导航/准备建议。

## API 与生命周期

```text
POST /api/applications/{app_id}/opportunity-fit-reviews
GET  /api/applications/{app_id}/opportunity-fit-reviews
GET  /api/applications/{app_id}/opportunity-fit-reviews/{review_id}
POST /api/applications/{app_id}/opportunity-fit-reviews/{review_id}/deep-review
```

POST 请求为 `resume_id`, `jd_text`, `jd_source_label`, `candidate_assertions`, `idempotency_key`。服务端规范化断言并验证最多 10 条、每条最多 500 字；JD 去除空白后不得为空；Resume 和 Application 必须可见。先冻结快照并调用模型，成功后在新短 session 中 `BEGIN IMMEDIATE`、再次确认 Application 可见、插入评估并提交。

Deep Review 只读取已保存 Triage 的快照和结果；已有 `deep_review_json` 时返回 200。首次生成后用新短 session `BEGIN IMMEDIATE` 检查 Application 与评估可见性，再写 Deep Review。模型不可验证返回 502 且不写记录；Provider 失败也返回 502 且不重试。Application/Resume 不存在或软删除返回 404；输入错误返回 422；重复 Triage 返回 200。

## 前端

在 `ApplicationDetail` 增加“评估岗位”入口和 `OpportunityFitReviewDrawer`。第一步选择 Resume、粘贴 JD、输入断言，明确提示数据会发送给当前配置的 AI Provider；结果按硬约束、已证实匹配、gap、未知项、截止日期和证据片段分组。第二步只在 Triage 完成后启用，展示 Deep Review、待确认问题、建议路径和本地行动卡。历史记录只读并标注快照来源。

“去准备材料”只关闭评估抽屉并将冻结 Resume/JD 预填到现有 Material Kit UI，不直接写 Material Kit。主 UI 移除 Resume Library 中 `ResumeMatchModal` 的 0–100 分入口，但保留后端兼容 API 和回归测试。任何界面不得出现匹配分、录取概率、“建议投递”“已验证”或平台回执文案。

## 验收
- 修改 Resume/JD 后，旧 Triage 与 Deep Review 内容和引用保持不变，新评估才读取新来源。
- 非法路径、伪造事实、错误 excerpt、JD URL、额外字段、非有限值和 Provider 异常均安全失败且不写评估。
- Triage/Deep Review 幂等、软删除并发、重复 Deep Review 均有回归测试。
- 无 URL 或招聘平台网络请求；用户断言独立展示。
- “去准备材料”不创建/更新 Material Kit；既有 Material Proposal、Evidence Bundle、Application 状态和事件测试保持全绿。
- 使用临时隔离数据目录完成一次真实 Triage 和 Deep Review 验收，不输出密钥、完整简历或完整 JD。
