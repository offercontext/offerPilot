# Evidence-Gated Material Proposal 设计

日期：2026-07-15
状态：待实现；本文件是交给开发与审查的产品契约。
范围：OfferPilot 内部材料流；不改变 v0.1 已冻结的面试范围。

## 背景与决策

2026-07-14 已合入 `Application Evidence Bundle`：用户确认投递后，OfferPilot 会保存 Application、JD、Resume 和 Material Kit 的不可变内部快照。它回答“当时提交了什么”，但不能安全回答“如何基于已有事实生成下一份针对性材料”。

本迭代补齐另一条 P0 链路：

```text
内部事实 / 用户显式断言
  -> AI 提案
  -> 可核验的证据引用与差异
  -> 用户选择并确认
  -> 新 Resume 版本 + Application Material Kit 关联
```

采用“独立提案 + 接受时创建新 Resume”的方案，而不是让模型直接更新当前 Resume，也不将 Material Kit 草稿当成版本历史。现有 `Resume.parent_resume_id` 已能表达派生关系；但现有 `resume_rewrite_highlight` Agent 工具会直接改写一条 Resume，缺少 Application/JD 约束、来源快照、逐项审阅与冲突保护。因此本能力是独立的产品/API 流，不复用该写工具的直接写入语义。

## 第一性原理与不变量

### 可观察事实

- 当前可见的 Application；
- 该 Application 唯一的 `ApplicationMaterialKit`，其中有 `jd_snapshot` 与关联 `resume_id`；
- 未软删除的源 Resume 的 `content_json`、`parsed_data` 和标题；
- 该 Application 最新的 `ApplicationEvidenceBundle`（若已确认投递）；
- 用户在发起本次提案时明确输入的 `user_assertions`。

JD 是“改写方向”来源，不是候选人事实来源。候选人事实只能来自 Resume、最新 Evidence Bundle 中的 Resume/Material Kit 快照，或本次明确可见的用户断言。

### 系统必须保证

1. 无静默写入：生成、拒绝和关闭页面都不得修改 Resume 或 Material Kit；只有 `accept` 写入。
2. 证据可定位：每项可接受变更必须至少有一个存在于提案输入快照中的证据引用，UI 显示来源与原文摘录。
3. 来源不漂移：接受前重新计算当前来源指纹；Resume、Material Kit、JD 或作为事实输入的最新 Evidence Bundle 变化时返回 `409`，不得写任何结果。
4. 版本不覆盖：接受只创建 `parent_resume_id=<source_resume_id>`、`is_master=false` 的新 Resume；不得覆盖源 Resume，也不得自动设为主简历。
5. 原子与幂等：新 Resume、Material Kit 的 `resume_id` 更新和 `application_events` 事件在同一事务；接受重试只能返回第一次的结果。
6. 事实表述准确：界面只能称“AI 建议，需人工确认”。`user_asserted` 不是平台验证，模型也不能被表述成已证明语义真伪。

“引用存在”可以被程序确定性验证；“改写是否完全忠实于引用的语义”不能被程序或同一个模型绝对证明。因此人工审阅、可见 diff 和默认不自动接受是保留的控制，而不是后续工作遗漏。

## 范围

### 包含

- 仅对 Application 关联 Resume 生成针对该 Material Kit 的提案；
- 可选、逐条可见的用户断言；
- `career_intent`、`experience[*].highlights[*]`、`projects[*].highlights[*]`、`skills` 与 `raw_text` 的替换型改写；
- 逐项选择、证据审阅、接受/拒绝；
- 新 Resume 派生版本、Material Kit 重新指向新版本、Application 时间线投影；
- 使用既有配置的 AI provider；测试用 fake model，真实 AI 验证仅在既有配置且用户明确允许时运行。

### 不包含

- 招聘平台、浏览器、邮箱、PDF、自动投递或外部职位抓取；
- 自动采纳、自动设主简历、完整文档版本图谱、跨 Application 去重；
- 面试笔记、模拟面试、结果校准、自动 follow-up；
- Pilot Agent 的新写工具；本版本从 `MaterialKitDrawer` 进入，避免将多步材料审阅塞入聊天确认协议；
- 模型二次裁判、评分、渲染或 ATS gate（后续 P1）。

## 领域模型

新增追加式表 `material_revision_proposals`。不提供 update/delete API。

| 字段 | 说明 |
| --- | --- |
| `id` | 主键 |
| `application_id` | 所属且可见的 Application，`CASCADE` |
| `material_kit_id` | 生成时的 Material Kit，`CASCADE` |
| `source_resume_id` | 源 Resume，`SET NULL` |
| `source_fingerprint_sha256` | 生成时输入的 canonical JSON SHA-256 |
| `source_snapshot_json` | Application 摘要、JD、Resume、可选 latest evidence bundle 和用户断言 |
| `proposal_json` | 规范化后的 summary、changes 和完整提案元数据 |
| `proposal_sha256` | `proposal_json` 的 canonical JSON SHA-256 |
| `status` | `draft`、`accepted` 或 `rejected` |
| `accepted_change_ids_json` | 已接受 change id 的规范化 JSON；拒绝时为 `[]` |
| `result_resume_id` | 接受时创建的 Resume；同一提案唯一 |
| `accepted_at` / `rejected_at` / `created_at` | 服务端时间 |

索引：`Index(application_id, created_at)`；`result_resume_id` 唯一。仓储层强制：`accepted` 必须有 result id 与 accepted_at，其他状态不得有 result id。

生成时的 source snapshot：

```json
{
  "schema_version": 1,
  "application": {"id": 42, "company_name": "Acme", "position_name": "Backend Engineer"},
  "material_kit": {"id": 13, "jd_snapshot": "...", "content_json": {"...": "..."}},
  "resume": {"id": 7, "title": "主简历", "content_json": {"...": "..."}, "parsed_data": "..."},
  "latest_evidence_bundle": {"id": 3, "bundle_sha256": "...", "snapshot": {"...": "..."}},
  "user_assertions": [{"id": "assertion-1", "text": "我在 2025 年负责过..."}]
}
```

没有最新 Evidence Bundle 时，值为 `null`；提案生成不得为了凑来源而创建证据包。将现有 `canonical_json`、`sha256_text` 与严格 JSON object 解析从 `repositories.evidence_bundles` 抽到无业务语义的共享模块（建议 `repositories/json_contract.py`），避免两套 hash 规则漂移。

## 模型输出与证据契约

模型只返回严格 JSON，服务端使用 Pydantic/显式校验解析，不解析 Markdown，也不相信模型声称“已验证”。

```json
{
  "summary": "针对 Acme Backend Engineer 的材料建议",
  "changes": [
    {
      "id": "change-1",
      "path": "/experience/0/highlights/1",
      "before": "Built APIs",
      "after": "Built FastAPI APIs for internal workflow automation",
      "rationale": "使既有 API 经验与 JD 的后端职责对应",
      "evidence_refs": [
        {"source": "resume", "path": "/experience/0/highlights/1", "excerpt": "Built APIs"}
      ]
    }
  ]
}
```

服务器从 source Resume `content_json` 顺序应用全部合法 change 以派生 proposed content；不接受模型另给一份可能与 changes 矛盾的完整简历。

允许 path：

```text
/career_intent/target_roles/<index>
/experience/<index>/highlights/<index>
/projects/<index>/highlights/<index>
/skills/<index>
/raw_text
```

v1 只允许替换既有标量；不允许新增数组项、删除记录、改 contact/education/company/title/date、改变对象结构或重叠 path。源路径不存在、`before` 不等于快照当前值、`after` 为空、change id 重复、path 重叠、无证据、引用 path 不存在或 excerpt 不匹配，均为不可核验模型输出：不写 proposal，API 返回 `502`。

`evidence_refs.source` 只能为：

- `resume`：读取 `source_snapshot_json.resume.content_json`；
- `evidence_bundle`：读取 `source_snapshot_json.latest_evidence_bundle.snapshot`；
- `user_assertion`：读取 `/user_assertions/<index>/text`，excerpt 必须等于该用户输入。

用户断言每条非空、最多 500 字、最多 10 条，在 UI 中标记“用户本次明确补充”。提示词明确禁止创造未提供的数字、雇主、职位、日期、技术、责任或成果；没有可用路径时返回空 changes。JD 只可决定重点、措辞和排序。

## API 契约

| 路由 | 行为 |
| --- | --- |
| `POST /api/applications/{app_id}/material-revision-proposals` | 读取来源、调用模型、验证、写 `draft`，返回详情，`201`；请求 `{"instructions":"", "user_assertions":["..."]}`。|
| `GET /api/applications/{app_id}/material-revision-proposals` | 轻量历史，按 `created_at DESC`。|
| `GET /api/applications/{app_id}/material-revision-proposals/{proposal_id}` | 返回 summary、changes、证据摘录、状态；不原样返回 source snapshot。|
| `POST /api/applications/{app_id}/material-revision-proposals/{proposal_id}/accept` | 请求 `{"expected_proposal_sha256":"...", "selected_change_ids":["change-1"]}`；单事务创建新 Resume、更新 kit、写 event。首次 `201`，重试 `200`。|
| `POST /api/applications/{app_id}/material-revision-proposals/{proposal_id}/reject` | `draft` 原子转 `rejected`；重复拒绝 `200`；已接受为 `409`。|

错误：不存在/软删除 `404`；请求或选择非法 `422`；来源、proposal hash、状态或并发冲突 `409`；模型不可用/输出不可核验 `502`。错误维持 `{"error":"..."}`。

接受成功时间线投影：

```text
event_type=custom
subtype=material_proposal_accepted
tags=["material_proposal", "proposal:<proposal_id>", "resume:<new_resume_id>"]
status=done
```

事件不是材料权威记录；proposal 与新 Resume 才是。

## 接受算法

```text
加载可见 application + proposal
若已 accepted：返回保存的 result_resume（幂等）
若非 draft：409
验证 expected_proposal_sha256
验证 selected ids 非空、唯一且为 proposal changes 子集
重建当前 source snapshot，计算 fingerprint；不一致则 409
从 frozen source Resume content 应用 selected changes
创建 Resume(parent_resume_id=source_resume_id, is_master=false, source=manual)
  parsed_data = 新 raw_text（若为 string），否则源 parsed_data
  title = "<源标题> · <公司> <岗位>"
更新同一 Material Kit.resume_id
写 custom/material_proposal_accepted event
写 proposal accepted 状态、selected ids、result id、accepted_at
提交；任一步失败回滚
```

fingerprint 覆盖 application id/company/position、material kit id/JD/content、源 Resume id/title/content/parsed_data、最新 evidence bundle id/hash（或 null）与规范化 user assertions；不覆盖自然语言 instructions。Application 软删除后 proposal 不可读；拒绝不删除记录。

## UI

入口在 `MaterialKitDrawer`：只有已加载同一 Application 的 kit、有效关联 Resume 和非空 JD 时启用“生成证据约束的简历建议”。

`MaterialProposalReviewModal` 必须：

- 标识 `AI 建议，需人工确认`，显示 Application、源 Resume、JD 摘要和时间；
- 每项显示 checkbox、字段名、before/after、理由和可展开证据摘录；
- 对 user assertion 使用独立来源标签；默认全选，空选择禁用接受；
- 接受二次确认必须写明“将创建新的派生简历版本，不会覆盖源简历”；
- `409` 后保留内容、禁用接受并提示重新生成；
- 成功刷新 `resumes`、`application-material-kit`、evidence preview/history 与 `application-events` query；
- 不出现“已投递”“已验证”“平台回执”。

浏览器仅调本地 `/api`；AI 调用只发生在后端既有 provider 配置中。

## 验收与审查重点

1. 合法 fake model 生成 draft，源 Resume 与 kit 不变。
2. 无效 path/before/引用/重复或重叠 path 返回 `502`，没有任何 proposal 或材料写入。
3. 选择部分 changes 后只修改选中字段，创建 non-master child Resume，kit 指向它，源不变且事件正确。
4. 空选择 `422`；生成后修改 Resume、JD、kit content 或最新 Evidence Bundle，接受 `409` 且没有半成品。
5. 重复/并发接受只产生一条子 Resume 和一条事件；拒绝后不可接受。
6. 软删除 Application 后所有 proposal 路由 `404`。
7. UI 显示证据、用户断言、逐项选择、409 和成功 refresh；没有平台验证文案。
8. 真实 AI 仅使用既有、本地已配置且获用户授权的 provider；不得打印密钥或发送数据给招聘平台。

审查时重点检查 canonical hash 是否两端一致、接受事务与 rollback、JSON pointer 白名单和转义/下标、soft-delete、详情 API 是否泄露完整快照、以及 UI 的 409 处理。语义忠实性不能由程序绝对证明，是必须保留的人工确认风险。
