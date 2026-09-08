# Product Convergence Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for inline execution; do not dispatch subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收敛现有 AI 与面试/Offer 入口，先完成三个 P0-A 子任务、P0-B、P0-C，再单独完成 P1 的行动规则、Proposal 终态矩阵和固定中文扫描；不新增外部访问或业务能力。

**Architecture:** 先以测试锁定直接 API、Agent 控制面、Opportunity Fit v1/v2 持久化、面试索引和 Offer 比较的边界；每个 P0 任务独立提交并通过对应后端/前端回归后再进入下一个 P0。P1 任务不与 P0 混合，继续沿用各领域已有 repository、Proposal 和中文文案模块，只抽取必要的最小共享契约。

**Tech Stack:** Python 3、FastAPI、SQLAlchemy/SQLite、Pydantic、pytest；React/TypeScript、Vitest、Axios；PowerShell 隔离 smoke 与现有本地/真实 AI harness。

---

## 0. 执行约束与基线

**Files:**

- Read: `docs/superpowers/specs/2026-07-27-product-convergence-audit-design.md`
- Read: `src/offerpilot/api.py`, `src/offerpilot/models.py`, `src/offerpilot/schemas.py`
- Read: `tests/test_jd_resume_ai_api.py`, `tests/test_ai_agent.py`, `tests/test_ai_tools.py`, `tests/test_opportunity_fit_reviews_{ai,api,repository}.py`, `tests/test_offers_api.py`

- [ ] 确认 `git status --short --branch` 干净，确认工作在 `feat/20260724-evidence-gated-interview-preparation`，每个任务只修改其列出的文件。
- [ ] 每项先写失败测试，运行该项最小测试集确认失败，再写最小实现；每项通过后单独提交，使用 `<type>: AI <英文主题>` 的 Conventional Commit 格式。
- [ ] P0-A1、P0-A2、P0-A3、P0-B、P0-C 按顺序完成；P1-A、P1-B、P1-C 在 P0 全部通过后才开始。

## 1. P0-A1：JD/Resume 分析 URL fallback 零外联

**Files:**

- Modify: `src/offerpilot/api.py`, `src/offerpilot/ai/workflows.py`, `src/offerpilot/cli.py`
- Test: `tests/test_jd_resume_ai_api.py`, `tests/test_cli.py`, `tests/test_module_workflows.py`

- [ ] **Step 1: 写 URL 负向测试。** 在两个 HTTP 路由各加入四类断言：`{}` 与仅 URL 返回 `422`/`jd_text_required`，非空 `jd_text + jd_url` 返回 `422`/`jd_url_not_supported`，并用 `monkeypatch` 将 `httpx.get` 替换为抛错函数，证明它从未被调用；AI provider fake 也必须为零调用。

```python
def fail_external_request(*args: object, **kwargs: object) -> None:
    raise AssertionError("URL fallback must not issue an external request")

monkeypatch.setattr("httpx.get", fail_external_request)
response = client.post("/api/jd/analyze", json={"jd_url": "https://jobs.invalid/a"})
assert response.status_code == 422
assert response.json()["code"] == "jd_text_required"
```

- [ ] **Step 2: 运行失败测试。** 运行 `uv run pytest tests/test_jd_resume_ai_api.py -q`；当前 URL fallback 会尝试读取 URL，测试应失败。
- [ ] **Step 3: 写最小实现。** 在 API 入参校验最前面拒绝 URL；只接受 `isinstance(jd_text, str) and jd_text.strip()`，保留原始文本给快照；删除 `api.py` 和 `ai/workflows.py` 的 URL fallback。CLI 保留 `--jd-url` 参数以兼容解析，但把它定义为已废弃输入：`--jd-url` 单独使用输出一行结构化 `{"code":"jd_text_required","error":"jd_text is required; jd_url is record-only"}` 并退出码 2，`--jd + --jd-url` 输出 `{"code":"jd_url_not_supported","error":"jd_url is record-only"}` 并退出码 2；两条分支均在 `_build_ai_model`、workflow 和 HTTP client 之前返回。历史 `job_url` 保存、展示和审计不改。
- [ ] **Step 4: 补 CLI/workflow 回归并运行。** 对 `analyze` 和 `resume match` 分别覆盖 URL-only、JD+URL、文本-only 三种组合；URL 两种组合都 mock `_build_ai_model`、workflow/fetch 和 `httpx.get` 为不可调用，断言结构化输出、非零退出码和零调用。运行 `uv run pytest tests/test_jd_resume_ai_api.py tests/test_cli.py tests/test_module_workflows.py -q`；预期全绿。
- [ ] **Step 5: 提交。** 分开执行以下命令，不能合并成一条 shell 命令：

```powershell
git add src/offerpilot/api.py src/offerpilot/ai/workflows.py src/offerpilot/cli.py tests/test_jd_resume_ai_api.py tests/test_cli.py tests/test_module_workflows.py
git commit -m "fix: AI block JD URL analysis fallback"
```

**验收门槛：** 两个 HTTP 路由和 CLI 均不能触发 `httpx.get`；非空文本无 URL 的既有分析仍可调用 provider；历史 `job_url` 不被迁移或删除。

## 2. P0-A2：Agent 写工具逐次确认与旧配置归一

**Files:**

- Modify: `src/offerpilot/ai/agent.py`, `src/offerpilot/ai/tools.py`, `src/offerpilot/config.py`, `src/offerpilot/api.py`, `src/offerpilot/cli.py`
- Test: `tests/test_ai_agent.py`, `tests/test_ai_tools.py`, `tests/test_config.py`, `tests/test_settings_api.py`, `tests/test_chat_api.py`, `tests/test_cli.py`

- [ ] **Step 1: 写控制面失败测试。** 遍历 registry 中 `write is True` 的工具，在 `auto_approve=True` 时断言仍产生 pending confirmation 且 handler 未调用；批准后才调用一次。拒绝、取消、过期只写控制面审计并清除 pending，不改变 Application/Event/Resume/Offer/Material Kit/Knowledge/Question/Memory。审计断言只允许工具名、调用 id、结果、时间、generation 和安全错误类别；写入模型原文、provider API key、JD、Resume、用户断言或完整参数的测试必须失败。

```python
for name, tool in registry.items():
    if tool.get("write"):
        _, _, pending = runner.run_turn(script_for(name), auto_approve=True, max_iter=8)
        assert pending is not None
        assert writes[name] == 0
```

- [ ] **Step 2: 写旧配置和备份失败测试。** `Config` 从 `True`、`"true"`、`1` 读取都归一为 `False`；`GET/PUT /api/settings`、设置备份导出/恢复和 CLI config 更新永远返回/写入 false，手工保存接口不经过 Agent gate。
- [ ] **Step 3: 运行失败测试。** 运行 `uv run pytest tests/test_ai_agent.py tests/test_ai_tools.py tests/test_config.py tests/test_settings_api.py tests/test_chat_api.py tests/test_cli.py -q`；当前 `auto_approve` 分支和设置转换应暴露失败。
- [ ] **Step 4: 写最小实现。** 将写工具的 confirmation 判定固定为 true，保留 `always_confirm` 语义但不能被配置绕过；pending 的 approved/rejected/cancelled/expired 使用现有 generation/token CAS。加载配置时固定 `chat_auto_approve_writes=False`，设置、备份和恢复只读兼容旧字段并重新写 false，不让 `bool("false")` 绕过。
- [ ] **Step 5: 运行专项测试并提交。** 重新运行上面的 pytest 命令，再运行 `uv run ruff check src tests`；全绿后分开执行 `git add src/offerpilot/ai/agent.py src/offerpilot/ai/tools.py src/offerpilot/config.py src/offerpilot/api.py src/offerpilot/cli.py tests/test_ai_agent.py tests/test_ai_tools.py tests/test_config.py tests/test_settings_api.py tests/test_chat_api.py tests/test_cli.py` 和 `git commit -m "fix: AI require confirmation for every agent write"`。

**验收门槛：** 所有 registry 写工具都必须先 pending；网络/超时未知保持原 pending/token；任何自动批准配置不能改变该结论，旧 config/backup 仍可启动。

## 3. P0-A3：Opportunity Fit v2 中性化契约及 v1 历史兼容

**Files:**

- Modify: `src/offerpilot/models.py`, `src/offerpilot/schemas.py`, `src/offerpilot/db.py`, `src/offerpilot/ai/opportunity_fit_reviews.py`, `src/offerpilot/repositories/opportunity_fit_reviews.py`, `src/offerpilot/api.py`
- Modify: `web/src/types/opportunityFitReview.ts`, `web/src/services/opportunityFitReviews.ts`, `web/src/features/pilot/PilotOpportunityFitCard.tsx`, `web/src/components/ApplicationDetail.tsx`
- Create: `tests/test_opportunity_fit_reviews_migrations.py`
- Test: `tests/test_opportunity_fit_reviews_ai.py`, `tests/test_opportunity_fit_reviews_repository.py`, `tests/test_opportunity_fit_reviews_api.py`, `web/src/features/pilot/PilotOpportunityFitCard.test.tsx`, `web/src/features/pilot/pilotOpportunityFitLifecycle.test.ts`

- [ ] **Step 1: 写 v2 阶段契约失败测试。** 断言 Triage 和 Deep Review 都输出 `schema_version=2`、固定 `stage`（分别为 `triage`/`deep_review`）、`source`、`summary` 和四数组；拒绝 recommendation、score、概率、旧枚举、额外字段、重复 id、超限数组、伪造/空白/不连续 evidence excerpt；固定 `question_id` 由服务端派生文本，非 allowlist 问题必须引用。
- [ ] **Step 2: 先写真实旧库升级测试。** 在 `tests/test_opportunity_fit_reviews_migrations.py` 用原始 SQLite DDL 建立当前完整 v1 `opportunity_fit_reviews` 表、`schema_migrations` 和同时含 Triage/Deep canonical JSON 与哈希的旧行，不使用 `create_all()` 代替旧库；调用 `init_database()` 后断言旧行 JSON 字节、`source_fingerprint_sha256`、`triage_sha256`、`deep_review_sha256` 和 idempotency key 原样不变，旧表新增 `proposal_schema_version=1`。同时断言新建的 `opportunity_fit_review_sessions`（含 `triage_idempotency_key` 唯一约束）与 `opportunity_fit_review_stages`（含 `stage_generation`、`confirmation_token_hash`、`confirmation_expires_at` 及 `UNIQUE(application_id, stage, idempotency_key)`）表存在且没有回填旧 stage；全新库和第二次 `init_database()` 分别覆盖创建顺序、重复运行幂等、root/stage 唯一索引和迁移版本记录。
- [ ] **Step 3: 写 v2 root/stage 两阶段生命周期测试。** 覆盖 Triage 首次请求在一个事务内原子创建 `review_id` root 与首个 `stage=triage`，失败时不得留下无 stage root；使用两个独立 SQLite connection 和 barrier 并发首次请求，断言数据库约束最终只有一条 root 与一条 Triage stage，失败事务读取获胜记录。同 key 同快照重试返回同一 root/stage，不创建第二个 root；用户确认 token CAS、重复消费 token 稳定 409、未确认 Triage 调 Deep 被拒绝、确认后使用独立 Deep key 生成 `stage=deep_review`。Deep 必须同时校验 `review_id`、`parent_triage_stage_id`、`application_id`，跨 review/跨投递父 stage 一律拒绝；列表/详情按 root 聚合一个 Triage 与零到多个 Deep。两个 stage 各自保存 source snapshot/fingerprint、proposal hash 和 idempotency key。分别覆盖 token 篡改、跨 root/stage token、过期 token、并发确认只有一个 CAS 成功、确认后不重新签发可消费 token；覆盖同 key 同快照重放、同 key 不同快照 409、确认后来源变化时首次/重生成 Deep 均 409 且不落 Deep、Deep 重新生成使用新 key 新 stage 且不覆盖旧 Deep；Provider 未知使用同一 Deep key 重试且不新增 stage。v1 双阶段行仍按原字节/哈希只读展示。
- [ ] **Step 4: 写 API/前端联合类型失败测试。** v1 响应保留 `recommendation/triage/deep_review` 并只读显示；v2 Triage 响应含稳定 `review_id`、`stage=triage`、`stage_id`、一次性确认 token 和阶段字段，v2 Deep 响应含同一 `review_id`、`stage=deep_review`、`parent_triage_stage_id` 和独立阶段字段，均没有 recommendation。前端只能对 v1 显示“历史事实”，不渲染决策按钮；v2 必须先显示 Triage 确认，再显示 Deep Review；重试必须复用对应阶段 key，不能重复消费 Triage token。
- [ ] **Step 5: 运行失败测试。** 运行 `uv run pytest tests/test_opportunity_fit_reviews_ai.py tests/test_opportunity_fit_reviews_repository.py tests/test_opportunity_fit_reviews_api.py tests/test_opportunity_fit_reviews_migrations.py -q` 与 `cd web; npm.cmd test -- --run src/features/pilot/PilotOpportunityFitCard.test.tsx src/features/pilot/pilotOpportunityFitLifecycle.test.ts`，确认当前 v1-only 实现失败。
- [ ] **Step 6: 写最小实现和明确迁移顺序。** 当前仓库没有独立 migrations 目录，具体迁移固定为 `src/offerpilot/db.py` 中新增的 `_migrate_opportunity_fit_v2()`，使用未占用版本 `0013_opportunity_fit_v2`；顺序固定为：`_ensure_schema_migrations()` → `Base.metadata.create_all()`（全新库创建 v2 root/stage 表）→ 对既有 `opportunity_fit_reviews` 使用 `_ensure_column(..., "proposal_schema_version", "INTEGER NOT NULL DEFAULT 1")` → 以 SQLite 写事务 `CREATE TABLE IF NOT EXISTS` 确认 `opportunity_fit_review_sessions` 与 `opportunity_fit_review_stages`、`review_id`/父 stage 索引、`UNIQUE(application_id, triage_idempotency_key)` 和 `UNIQUE(application_id, stage, idempotency_key)` → `INSERT OR IGNORE` 记录 `0013_opportunity_fit_v2`。不重建旧表、不回填旧 stage、不改变旧 JSON/哈希。v2 root 的 `id` 即 `review_id`，Triage root+stage 和 `triage_idempotency_key` 唯一性在同一短事务由数据库约束兜底，冲突事务回滚后读取获胜记录；每个 stage 的 `proposal_json` 使用严格 canonical JSON，`proposal_sha256` 哈希该阶段，`source_fingerprint_sha256` 哈希该阶段冻结输入。Triage token 使用服务端 secret HMAC-SHA256，绑定 `review_id`、stage id、`stage_generation`、`confirmation_expires_at`，只存 token hash；确认 CAS 同时匹配 stage/generation/ready/未确认/token hash，过期、篡改、跨 root/stage 和并发确认均有稳定语义。Deep 前置条件同时校验 root/父 stage/application 和逐字段来源哈希，Deep 重生成用新 key、新 stage，Provider 未知按同 key CAS 重试；API 使用 `schema_version + stage` discriminator，Chat prompt、Pydantic schema、repository validator、API 和 TypeScript union 同批切换；历史 v1 只读，不产生状态动作。
- [ ] **Step 7: 运行专项并提交。** 运行后端专项、前端定向测试、`uv run ruff check src tests`、`uv run mypy src`；全绿后分开执行 `git add src/offerpilot/models.py src/offerpilot/schemas.py src/offerpilot/db.py src/offerpilot/ai/opportunity_fit_reviews.py src/offerpilot/repositories/opportunity_fit_reviews.py src/offerpilot/api.py web/src/types/opportunityFitReview.ts web/src/services/opportunityFitReviews.ts web/src/features/pilot/PilotOpportunityFitCard.tsx web/src/components/ApplicationDetail.tsx tests/test_opportunity_fit_reviews_ai.py tests/test_opportunity_fit_reviews_repository.py tests/test_opportunity_fit_reviews_api.py tests/test_opportunity_fit_reviews_migrations.py web/src/features/pilot/PilotOpportunityFitCard.test.tsx web/src/features/pilot/pilotOpportunityFitLifecycle.test.ts` 和 `git commit -m "feat: AI add neutral opportunity fit v2 contract"`。

**验收门槛：** v1 历史字节和哈希不变；新生成永不持久化 recommendation；证据门控、幂等冲突和历史只读均由后端强制，不能只靠 UI 隐藏。

## 4. P0-B：顶层面试索引

**Files:**

- Create: `src/offerpilot/repositories/interview_index.py`, `web/src/services/interviews.ts`, `web/src/types/interviewIndex.ts`
- Modify: `src/offerpilot/api.py`, `web/src/components/InterviewV01View.tsx`, `web/src/layout/AppShell.tsx`
- Create: `tests/test_interview_index_api.py`
- Test: `web/src/components/InterviewV01View.test.tsx`, `web/src/layout/AppShell.interviewReview.test.tsx`

- [ ] **Step 1: 写全局列表失败测试。** 为 `GET /api/interviews?limit=&cursor=` 锁定只返回可见 Application 的 interview event、可见绑定 Note、历史 Proposal/已确认 Knowledge 摘要和准备入口；某条 Application 软删除后列表仍返回 `200` 并排除该条目；事件/Note 删除或解绑保留历史摘要并标 `source_changed`，不允许新生成/handoff；standalone note 不进入索引。
- [ ] **Step 2: 写深链资源失败测试。** 对现有 Application/事件/Note 详情上下文分别覆盖软删除或不可见后的 `404`，断言错误码稳定、不会返回冻结内容，也不会创建 handoff；这组 404 与全局列表的 200/排除语义分开测试。
- [ ] **Step 3: 写分页/排序/错误测试。** 断言默认 limit 50、最大 200、`scheduled_at` 非空优先后升序、`created_at DESC`、id DESC；返回 `{items,next_cursor}`，越界参数 422。
- [ ] **Step 4: 写 UI 失败测试。** `InterviewV01View` 消费索引而非固定空状态；展示中文空状态、事件/历史只读状态并跳转详情；深链 404 清除当前卡片/Drawer 且不创建 handoff，全局列表中的软删除条目只消失不清空整个页面。先验证 `ReviewManagementView` 可复用，否则不挂载半可达 Mock/Review 页面。
- [ ] **Step 5: 运行失败测试。** 运行 `uv run pytest tests/test_interview_index_api.py -q` 与 `cd web; npm.cmd test -- --run src/components/InterviewV01View.test.tsx src/layout/AppShell.interviewReview.test.tsx`。
- [ ] **Step 6: 写最小实现并回归。** repository 统一可见性、排序和 cursor；全局 API 对软删除 Application 过滤后返回 200，详情上下文沿用现有 404 语义；API 只读组合已有 Application/Event/Note/Proposal/Knowledge 数据；AppShell 保持当前 Pilot/Drawer 的 handoff 和深链 404 清理语义，不新增领域写入。
- [ ] **Step 7: 提交。** 运行专项、`npm.cmd run build`，分开执行 `git add src/offerpilot/repositories/interview_index.py src/offerpilot/api.py web/src/services/interviews.ts web/src/types/interviewIndex.ts web/src/components/InterviewV01View.tsx web/src/layout/AppShell.tsx tests/test_interview_index_api.py web/src/components/InterviewV01View.test.tsx web/src/layout/AppShell.interviewReview.test.tsx` 和 `git commit -m "feat: AI add top-level interview index"`。

## 5. P0-C：Offer 比较护栏

**Files:**

- Modify: `src/offerpilot/api.py`, `web/src/services/offers.ts`, `web/src/components/OfferCompareDrawer.tsx`, `web/src/components/OfferCenterView.tsx`
- Create: `web/src/components/OfferCompareDrawer.test.tsx`, `web/src/components/OfferCenterView.test.tsx`
- Test: `tests/test_offers_api.py`

- [ ] **Step 1: 写 API/UI 失败测试。** `/api/offers/compare` 先按请求首次出现顺序去重 ID；重复同一 ID 后不足两个不同可见 Offer 返回稳定 `422 offer_comparison_requires_two_offers`。非整数/非正整数返回 `422 offer_comparison_invalid_ids`；缺失或不可见 ID 返回稳定 `404 offer_comparison_offer_not_found`，不返回部分结果；两个不同且可见 Offer 才保持用户请求顺序返回。UI 对 0/1 条不显示或禁用比较入口。
- [ ] **Step 2: 运行失败测试。** 运行 `uv run pytest tests/test_offers_api.py -q` 与对应 Vitest 文件，确认当前 endpoint 会接受少于两个结果。
- [ ] **Step 3: 写最小实现并验证。** API 解析 ID 后先去重，再验证每个 ID 的可见性，最后检查不同 Offer 数量；不再静默跳过缺失 ID。保持请求顺序，不增加 currency/pay_period/amount_basis 或平均/排名逻辑，不做税后、权益价值或“最优 Offer”推断。运行专项和 `npm.cmd run build`。
- [ ] **Step 4: 提交。** 分开执行 `git add src/offerpilot/api.py web/src/services/offers.ts web/src/components/OfferCompareDrawer.tsx web/src/components/OfferCenterView.tsx tests/test_offers_api.py web/src/components/OfferCompareDrawer.test.tsx web/src/components/OfferCenterView.test.tsx` 和 `git commit -m "fix: AI guard offer comparison inputs"`。

## 6. P1-A：行动提示单一事实源

**Files:**

- Create: `web/src/lib/actionHints.ts`, `web/src/lib/actionHints.test.ts`
- Modify: `web/src/lib/actionItems.ts`, `web/src/lib/missionControl.ts`, `web/src/lib/pipelineInsights.ts`
- Modify: `web/src/features/dashboard/DashboardView.tsx`, `web/src/features/reminders/RemindersView.tsx`, `web/src/layout/CommandPalette.tsx`
- Test: `web/src/lib/actionItems.test.ts`, `web/src/lib/missionControl.test.ts`, `web/src/lib/pipelineInsights.test.ts`, `web/src/features/dashboard/DashboardView.test.ts`, `web/src/layout/CommandPalette.test.ts`

- [ ] **Step 1: 写规则测试。** 先固定版本化阈值常量：Offer deadline 7 天、面试 72 小时、投递无更新 7/14 天、题目到期、材料包未完成；每个 `ActionHint` 必须带稳定 id、输入字段、阈值、原因、优先级、目标入口和确认要求。
- [ ] **Step 2: 运行失败测试。** 运行 `cd web; npm.cmd test -- --run src/lib/actionHints.test.ts src/lib/actionItems.test.ts src/lib/missionControl.test.ts src/lib/pipelineInsights.test.ts src/features/dashboard/DashboardView.test.ts src/layout/CommandPalette.test.ts`。
- [ ] **Step 3: 写最小实现。** 仅让 Dashboard、Reminders、Command Palette 的动态提示消费同一 `ActionHint` 规则；静态快捷命令保留为 command，不冒充提醒；点击只导航到原生查看/表单，不直接写领域数据。
- [ ] **Step 4: 提交。** 运行定向测试和 build，分开执行 `git add web/src/lib/actionHints.ts web/src/lib/actionHints.test.ts web/src/lib/actionItems.ts web/src/lib/missionControl.ts web/src/lib/pipelineInsights.ts web/src/features/dashboard/DashboardView.tsx web/src/features/reminders/RemindersView.tsx web/src/layout/CommandPalette.tsx web/src/lib/actionItems.test.ts web/src/lib/missionControl.test.ts web/src/lib/pipelineInsights.test.ts web/src/features/dashboard/DashboardView.test.ts web/src/layout/CommandPalette.test.ts` 和 `git commit -m "refactor: AI unify action hint rules"`。

## 7. P1-B：五类 Proposal 终态一致性矩阵

**Files:**

- Modify: `src/offerpilot/ai/material_proposals.py`, `src/offerpilot/ai/opportunity_fit_reviews.py`, `src/offerpilot/ai/interview_review_proposals.py`, `src/offerpilot/ai/interview_preparation_proposals.py`, `src/offerpilot/repositories/interview_knowledge_capture.py`, `src/offerpilot/api.py`, `src/offerpilot/smoke.py`
- Test: existing `tests/test_material_revision_proposals_{ai,api,repository}.py`, `tests/test_opportunity_fit_reviews_{ai,api,repository}.py`, `tests/test_interview_review_proposals_{ai,api,repository}.py`, `tests/test_interview_knowledge_capture_{ai,api,repository}.py`, `tests/test_interview_preparation_{ai,api,repository}.py`, `tests/test_smoke.py`

- [ ] **Step 1: 写每领域终态表测试。** 分别锁定 Provider/网络未知、模型契约失败、严格校验后的安全空结果、无证据可用四类结果：材料契约失败仍为 `502 material_proposal_unverifiable`，只有合法 `changes=[]` 才 safe_empty；机会评估契约失败不写 Review；复盘两次契约失败按既有安全空语义；知识沉淀 AI 失败不阻塞 direct save；面试准备保留 `202/502` key 并只对两次契约失败落 safe_empty。
- [ ] **Step 2: 写未知结果重试测试。** 断言所有 Provider/网络未知路径保留原 key、冻结输入和必要 lease；同 key 重试不生成第二条 Proposal/Attempt；禁止将未知结果映射成确定失败或清除 key。
- [ ] **Step 3: 运行失败测试。** 分别运行五组后端专项和 `uv run pytest tests/test_smoke.py -q`，确认矩阵中错误码/空结果不一致处失败。
- [ ] **Step 4: 写最小实现。** 只统一诊断字段和测试断言，不合并各领域输入快照、证据路径或 repository；保留各流程已有错误码、HITL、原子写入和外部访问禁止边界。
- [ ] **Step 5: 验证并提交。** 运行五组专项、smoke、Ruff、Mypy，分开执行 `git add src/offerpilot/ai/material_proposals.py src/offerpilot/ai/opportunity_fit_reviews.py src/offerpilot/ai/interview_review_proposals.py src/offerpilot/ai/interview_preparation_proposals.py src/offerpilot/repositories/interview_knowledge_capture.py src/offerpilot/api.py src/offerpilot/smoke.py tests/test_material_revision_proposals_ai.py tests/test_material_revision_proposals_api.py tests/test_material_revision_proposals_repository.py tests/test_opportunity_fit_reviews_ai.py tests/test_opportunity_fit_reviews_api.py tests/test_opportunity_fit_reviews_repository.py tests/test_interview_review_proposals_ai.py tests/test_interview_review_proposals_api.py tests/test_interview_review_proposals_repository.py tests/test_interview_knowledge_capture_ai.py tests/test_interview_knowledge_capture_api.py tests/test_interview_knowledge_capture_repository.py tests/test_interview_preparation_ai.py tests/test_interview_preparation_api.py tests/test_interview_preparation_repository.py tests/test_smoke.py` 和 `git commit -m "test: AI codify proposal terminal semantics"`。

## 8. P1-C：材料与面试 Proposal 流程固定文案中文扫描

**Files:**

- Modify: `web/src/components/materialFlowCopy.ts`, `web/src/components/opportunityFitCopy.ts`, `web/src/components/MaterialKitDrawer.tsx`, `web/src/components/InterviewReviewProposalDrawer.tsx`, `web/src/components/InterviewPreparationProposalDrawer.tsx`, `web/src/components/InterviewKnowledgeCaptureDrawer.tsx`
- Create: `web/src/components/systemCopyRegression.test.ts`
- Test: `web/src/components/materialFlowCopy.test.ts`, `web/src/components/opportunityFitCopy.test.ts`, affected component tests, `web/src/components/systemCopyRegression.test.ts`

- [ ] **Step 1: 写扫描测试。** 范围限定为材料包、Opportunity Fit、面试复盘/知识沉淀/面试准备 Proposal 流程，不宣称覆盖 Pilot、顶层面试、Offer、Dashboard、Reminders 或 Chat 的全部系统文案。只列该范围内版本化固定英文短语集合，覆盖标题、说明、按钮、加载/空状态、状态、证据来源、无障碍标签和安全错误；测试源代码/渲染固定文案不含这些短语，同时用英文 JD、职位名、Resume 标题、用户断言和证据摘录 fixture 证明任意英文不会被禁止。
- [ ] **Step 2: 写错误映射测试。** 仅按错误码/HTTP 状态映射中文；不透传 Axios、`response.data.error` 或 `Error.message`。覆盖材料/复盘/准备的未知、不可验证、冲突、404/409/422/502 与 safe_empty。
- [ ] **Step 3: 写最小实现并回归。** 文案只进入材料流程专用 copy 模块；固定证据来源按流程映射，`evidence_bundle` 显示“已确认的投递证据快照”；空 changes 只显示中文“暂无可用改写”且不渲染模型 summary。动态用户数据和证据摘录保持原文。
- [ ] **Step 4: 提交。** 运行该范围定向测试与 `npm.cmd run build`，分开执行 `git add web/src/components/materialFlowCopy.ts web/src/components/opportunityFitCopy.ts web/src/components/MaterialKitDrawer.tsx web/src/components/InterviewReviewProposalDrawer.tsx web/src/components/InterviewPreparationProposalDrawer.tsx web/src/components/InterviewKnowledgeCaptureDrawer.tsx web/src/components/systemCopyRegression.test.ts web/src/components/materialFlowCopy.test.ts web/src/components/opportunityFitCopy.test.ts` 和 `git commit -m "fix: AI finalize proposal flow Chinese copy"`。

## 9. 最终门禁与真实验收

- Create: `scripts/windows-pytest-groups.ps1`

- [ ] P0 每项单独验收后，按 P1-A、P1-B、P1-C 顺序运行完整后端专项、前端全量和构建；任何 P1 不得在 P0 提交中顺手修改。
- [ ] **先锁定全量收集集合。** 在 `scripts/windows-pytest-groups.ps1` 中先运行 `uv run pytest --collect-only -q --disable-warnings`，逐项检查 `$LASTEXITCODE`，将输出中所有以 `tests/` 开头的完整 node id 保存为临时 manifest，并记录总数；任何 collection 失败立即 `throw`。
- [ ] **按稳定文件组完整运行。** 脚本固定五组：`agent`（`test_ai_*.py`、`test_chat_*.py`、`test_config.py`、`test_settings_api.py`、`test_auth_api.py`、`test_cli.py`）、`domain`（`test_applications*.py`、`test_events*.py`、`test_notes*.py`、`test_resumes*.py`、`test_offers*.py`、`test_questions*.py`、`test_jd_resume_ai_api.py`、`test_module_workflows.py`）、`knowledge`（`test_knowledge*.py`、`test_ki*.py`）、`proposals`（`test_opportunity_fit*.py`、`test_interview*.py`、`test_material*.py`、`test_evidence*.py`、`test_smoke.py`）和动态 `misc`（所有未分配的 `tests/*.py`）。每组先 collect 再运行，命令返回码非 0 立即 `throw`；脚本比较各组 node id 的并集与第一次 manifest，必须完全相等且无重复，不允许“某组超时就跳过”。
- [ ] **限制 Windows 跳过项。** 只允许以下四个 symlink 安全测试在无法创建符号链接时 skip，并只接受 `WinError 1314`、`EACCES` 或 `EPERM`：`tests/test_knowledge_ingest_integrity.py::test_failed_commit_cleanup_does_not_follow_symlink`、`tests/test_knowledge_reset.py::test_cli_rejects_knowledge_root_symlink_with_external_sentinels`、`tests/test_knowledge_reset.py::test_cli_rejects_legacy_reset_root_symlink_with_external_sentinels`、`tests/test_knowledge_reset.py::test_cli_does_not_follow_nested_escape_symlink`。任何其他 skip、失败或权限错误都使该组失败；具备 symlink 权限的环境必须执行四个真实断言。
- [ ] 分组门禁完成后分别运行 `uv run ruff check .`、`uv run mypy src`、进入 `web` 运行 `npm.cmd test -- --run` 和 `npm.cmd run build`；每条原生命令后检查 `$LASTEXITCODE`，非零即 `throw`。再运行 `uv run oc smoke --static-dir web/dist` 和 `uv run oc verify --profile local --static-dir web/dist`，同样逐次检查退出码；不使用命令连接符合并执行。
- [ ] 使用临时隔离数据目录执行 real-AI verify 和浏览器走查；断言 URL 输入零外联、Agent 写入逐次确认、Opportunity Fit v1 历史/v2 新结果、面试索引列表排除与深链 404、Offer 0/重复/1/2 护栏，且无自动状态变更、投递、知识扩张或招聘平台请求。停止精确服务进程并清理临时目录，最后验证源数据目录未变化。
- [ ] 完成独立 CR 和问题回归后，再提交发布报告；本计划阶段不执行代码、不运行实现测试。

## 10. 实际发布验证收口（2026-07-27）

本次收口已进入发布验证阶段；未新增产品功能、API、数据库迁移或外部访问权限。

- [x] 后端全量按稳定文件组完成：总收集数 1514，分组为 agent 425、domain 71、knowledge 658、proposals 271、misc 89；并集覆盖 1514/1514，无遗漏或重复。各组退出码均为 0；knowledge 组 4 个符号链接能力测试按既定 Windows 权限条件跳过。
- [x] 静态与前端门禁完成：ruff、mypy、前端全量测试和生产构建均退出码 0。
- [x] `oc smoke`、隔离 `local verify`、隔离 `real-ai verify` 均退出码 0；real-AI 覆盖面试准备、材料提案、Opportunity Fit Triage/Deep、面试复盘建议、知识沉淀与 Chat 写入确认。local/real-AI 使用临时数据目录，源数据目录未写入。
- [ ] 真实浏览器双阶段尚未全部通过：阶段一“面试 → 准备面试 → 选择简历/JD → 人工确认 → 真实生成 → 关闭重开 → 历史查看”已完成并显示有效证据；阶段一边界未发现跨领域写入。阶段二浏览器 Triage 两次真实调用均返回 `502 opportunity_fit_unverifiable`，因此未声称 Triage/Deep 浏览器闭环通过；对应 API real-AI verify 已通过。发布前仍需重新完成阶段二浏览器闭环。
- [x] 临时浏览器服务、合成数据和配置副本已停止/清理；隔离库残留为零，正式数据目录未被修改。

本节记录实际命令和证据；全量包装脚本单次运行受本地工具时限限制，已使用相同 manifest 分组逻辑逐组完成并核对覆盖集合，未以单次超时作为通过依据。

## 11. 分组门禁脚本收口（2026-07-28）

- [x] `scripts/windows-pytest-groups.ps1` 现在为每组生成并解析 JUnit 报告，只有四个固定符号链接测试且原因精确为 Windows 无权限时才允许 skip；额外 skip、原因变化或非允许 node id 均失败。
- [x] 完整 manifest、各组 manifest 与跨组 node id 均在去重前检查重复；重复 node id 不再被 `Sort-Object -Unique` 掩盖。
- [x] 修复后重新执行五组：agent 425 passed/0 skip、domain 71 passed/0 skip、knowledge 654 passed/4 allowed skip、proposals 271 passed/0 skip、misc 89 passed/0 skip。
