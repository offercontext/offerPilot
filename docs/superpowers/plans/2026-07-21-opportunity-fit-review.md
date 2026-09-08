# Opportunity Fit Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with TDD checkpoints.

**Goal:** 为已有 Application 增加基于不可变输入快照的 Triage 与 Deep Review 岗位决策漏斗，并保持证据、幂等、软删除和人工确认边界。

**Architecture:** 新增 `OpportunityFitReview` 聚合和 repository。生成阶段短 session 构建快照、关闭连接后调用严格 JSON AI；写入阶段新 session 使用 `BEGIN IMMEDIATE` 检查可见性并插入/更新。前端在 Application Detail 打开两阶段抽屉，历史只读，“去准备材料”只导航到 Material Kit。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy/SQLite、Pydantic、现有 `ChatModel`/`parse_json_reply`、React、TypeScript、TanStack Query、Vitest。

---

### Task 1: 保存设计与确认基线

**Files:**
- Create: `docs/superpowers/specs/2026-07-21-opportunity-fit-review-design.md`
- Create: `docs/superpowers/plans/2026-07-21-opportunity-fit-review.md`
- Test: baseline is `origin/main@4a865ef`

- [x] **Step 1: 建立隔离 worktree**

```powershell
git fetch origin main
git worktree add D:\Users\yuqi.chen\offerpilot\.worktrees\feat-20260721-opportunity-fit-review -b feat/20260721-opportunity-fit-review origin/main
```

- [x] **Step 2: 验证基线**

```powershell
uv run pytest -q
```

Expected: 基线测试通过；若结果不同，在实现报告中单独记录，不把基线失败归因于本功能。

- [ ] **Step 3: 提交设计文档**

```powershell
git add docs/superpowers/specs/2026-07-21-opportunity-fit-review-design.md docs/superpowers/plans/2026-07-21-opportunity-fit-review.md
git commit -m "docs: AI define opportunity fit review"
```

### Task 2: 先写严格 AI 契约失败测试

**Files:**
- Create: `src/offerpilot/ai/opportunity_fit_reviews.py`
- Create: `tests/test_opportunity_fit_reviews_ai.py`
- Reference: `src/offerpilot/ai/workflows.py`, `src/offerpilot/ai/material_proposals.py`, `src/offerpilot/repositories/json_contract.py`

- [ ] **Step 1: 写 RED 测试**

测试覆盖：合法 Triage/Deep Review；额外字段、fenced JSON、`NaN`、错误类型、非法 recommendation/status/path、JD 被当候选人事实、伪造 Resume path、错误 excerpt、空的 required evidence、`advance` 含 unmet、`hold` 无待确认问题、`decline` 无引用阻断理由；合法失败首次格式错误第二次成功；Provider 异常只调用一次。

```python
def test_triage_rejects_resume_fact_without_evidence(snapshot, fake_model):
    payload = valid_triage()
    payload["fit_signals"][0]["evidence_refs"] = []
    with pytest.raises(OpportunityFitModelError):
        validate_triage(payload, snapshot)


def test_triage_retries_only_invalid_output(fake_model, snapshot):
    fake_model.responses = [valid_triage_json(before="bad"), valid_triage_json()]
    result = generate_triage(fake_model, snapshot)
    assert result.payload["recommendation"] == "hold"
    assert fake_model.calls == 2


def test_provider_failure_is_not_retried(provider_error_model, snapshot):
    with pytest.raises(OpportunityFitModelError):
        generate_triage(provider_error_model, snapshot)
    assert provider_error_model.calls == 1
```

- [ ] **Step 2: 运行 RED**

```powershell
uv run pytest tests/test_opportunity_fit_reviews_ai.py -q
```

Expected: FAIL because the module and validators do not exist yet。

- [ ] **Step 3: 实现最小严格契约**

实现 `build_source_snapshot()` 使用 `canonical_json`/`sha256_text`；实现 `validate_triage()`、`validate_deep_review()`、`validate_evidence_ref()` 和 JSON pointer 读取；所有顶层与嵌套字段用精确 key 集合校验。使用 `parse_json_reply(..., allow_fenced=False, reject_non_finite=True)`；仅结构/证据错误重试一次，Provider 异常直接包装为 `provider_error`。

- [ ] **Step 4: GREEN 与重构**

```powershell
uv run pytest tests/test_opportunity_fit_reviews_ai.py -q
uv run ruff check src/offerpilot/ai/opportunity_fit_reviews.py tests/test_opportunity_fit_reviews_ai.py
```

### Task 3: 模型、迁移与不可变快照 repository

**Files:**
- Modify: `src/offerpilot/models.py`
- Modify: `src/offerpilot/db.py`
- Create: `src/offerpilot/repositories/opportunity_fit_reviews.py`
- Create: `tests/test_opportunity_fit_reviews_repository.py`

- [ ] **Step 1: 写 repository RED 测试**

覆盖：可见 Application/Resume 快照字段最小化；普通备注、其他 Resume、对话不进入快照；Resume/JD 后续变化不改变存储内容；同 idempotency 返回原记录；Application/Resume 软删除 404；模型调用期间软删除时第二阶段 `BEGIN IMMEDIATE` 不插入；Deep Review 首次保存、重复返回原记录且不再调用模型。

- [ ] **Step 2: 运行 RED**

```powershell
uv run pytest tests/test_opportunity_fit_reviews_repository.py -q
```

- [ ] **Step 3: 增加模型和迁移记录**

在 `models.py` 增加 `OpportunityFitReview`，联合唯一约束 `(application_id, idempotency_key)`、应用索引和 `deep_reviewed_at`；在 `init_database()` 的现有 `_record_migration` 区域追加 `0008_opportunity_fit_reviews`。不新增破坏性迁移，不改既有表。

- [ ] **Step 4: 实现 repository 两段式写入**

`create_triage()` 先读取可见来源构建 snapshot，关闭 session 后调用 AI，再开新 session 执行 `session.execute(text("BEGIN IMMEDIATE"))`、重新检查 Application、插入并提交。 `deep_review()` 先读取保存的 snapshot/triage，关闭 session 调 AI，再用新 session `BEGIN IMMEDIATE`、检查 Application/Review、更新并提交。IntegrityError 对幂等 key 转换为原记录返回。

- [ ] **Step 5: GREEN**

```powershell
uv run pytest tests/test_opportunity_fit_reviews_repository.py -q
```

### Task 4: API、稳定错误码与后端回归

**Files:**
- Modify: `src/offerpilot/schemas.py`
- Modify: `src/offerpilot/api.py`
- Create: `tests/test_opportunity_fit_reviews_api.py`

- [ ] **Step 1: 写 API RED 测试**

覆盖四个 endpoint 的 201/200/404/409/422/502：空 JD、断言 11 条、断言 501 字、非法 UUID、隐藏 Application/Resume、重复 Triage、重复 Deep Review、格式失败无记录、Provider 失败无记录、软删除竞态 404 无孤儿。响应不返回 `source_snapshot_json` 原文；详情只返回允许的结构化 Triage/Deep Review 与 hash。

- [ ] **Step 2: 实现 schemas 和 route 映射**

用 Pydantic 定义 `OpportunityFitReviewCreate`、`OpportunityFitReviewSummaryOut`、`OpportunityFitReviewOut`、`DeepReviewOut`；校验和归一化断言。创建 routes 只读取可见 Application，不调用旧 `POST /api/jd/analyze`。模型不可验证和 Provider 错误分别返回稳定 `code="opportunity_fit_unverifiable"` 或 `code="opportunity_fit_provider_error"`，不包含模型原文、JD、简历或密钥。

- [ ] **Step 3: 运行后端定向测试**

```powershell
uv run pytest tests/test_opportunity_fit_reviews_ai.py tests/test_opportunity_fit_reviews_repository.py tests/test_opportunity_fit_reviews_api.py -q
```

### Task 5: 前端类型、服务和两阶段抽屉

**Files:**
- Create: `web/src/types/opportunityFitReview.ts`
- Create: `web/src/services/opportunityFitReviews.ts`
- Create: `web/src/components/OpportunityFitReviewDrawer.tsx`
- Create: `web/src/components/OpportunityFitReviewDrawer.module.css`
- Create: `web/src/components/OpportunityFitReviewDrawer.test.tsx`
- Modify: `web/src/components/ApplicationDetail.tsx`

- [ ] **Step 1: 写前端 RED 测试**

覆盖：Application Detail 显示“评估岗位”；抽屉显示 provider 数据范围提示；Resume/JD/断言输入限制；结果分组和 evidence ref；用户断言独立标签；Deep Review 仅在 Triage 完成后可用；409/502 产品化提示；历史只读；“去准备材料”不调用 Material Kit 写 service。

- [ ] **Step 2: 实现 service/types**

按 API 定义 `createOpportunityFitReview`、`listOpportunityFitReviews`、`getOpportunityFitReview`、`createDeepReview`；错误优先读取 `response.data.error`。类型只暴露详情允许字段，不定义 snapshot 原文。

- [ ] **Step 3: 实现抽屉并接入 ApplicationDetail**

使用 TanStack Query 管理历史和当前评估；每个来源块显示引用 path/excerpt，user assertion 显示“用户提供，未外部核验”。按钮文案不出现百分比、录取概率、建议投递或平台验证。 `prepare_materials` 只关闭抽屉并将 frozen resume/JD 作为现有 MaterialKitDrawer 的初始导航参数，不发写请求。

- [ ] **Step 4: GREEN**

```powershell
Set-Location web
npm.cmd test -- --run src/components/OpportunityFitReviewDrawer.test.tsx
Set-Location ..
```

### Task 6: 移除旧匹配分主入口并保持兼容

**Files:**
- Modify: 当前 Resume Library 主入口文件（先由 `rg` 确认）
- Modify: `web/src/components/ResumeMatchModal.tsx` 仅在需要移除 import 时
- Test: 现有 command palette/resume library tests plus new assertions

- [ ] **Step 1: 写 RED UI contract test**

断言主 UI 不再渲染 `ResumeMatchModal` 或 0–100 匹配分入口，同时后端旧 Resume Match API tests remain unchanged。

- [ ] **Step 2: 删除主路径入口**

仅移除主 UI 入口和无用 import，不删除旧 API、schema、repository 或兼容测试。

- [ ] **Step 3: GREEN**

```powershell
Set-Location web
npm.cmd test -- --run
Set-Location ..
```

### Task 7: Smoke、真实 AI、浏览器与完整 gate

**Files:**
- Modify: `src/offerpilot/smoke.py`
- Modify: `tests/test_smoke.py`
- Possibly modify: web test fixtures only when required by actual UI contract

- [ ] **Step 1: 增加 real-ai 隔离 smoke**

只在 `--profile real-ai` 使用临时 data dir，复制现有 AI config，不复制用户数据库；创建合成 Application/Resume，调用 Triage 与 Deep Review，校验 201/201、公共响应白名单和引用 path/excerpt，清理并确认无残留。local profile 不调用真实 Provider。

- [ ] **Step 2: 运行 smoke 与部署验证**

```powershell
Set-Location web
npm.cmd run build
Set-Location ..
uv run oc smoke --static-dir web/dist
uv run oc verify --profile local --static-dir web/dist
uv run oc verify --profile real-ai --static-dir web/dist
```

真实 AI 只记录成功/失败类别、步骤和是否有安全结果，不输出 API key、完整简历、完整 JD 或模型原文。

- [ ] **Step 3: 浏览器走查**

使用内置浏览器打开本地服务，使用合成 Application 和非空 JD，完成 Triage、Deep Review、历史查看和“去准备材料”导航；检查网络请求只到本地 `/api` 与配置 Provider，没有 URL 抓取、招聘平台请求或自动写入 Material Kit。

- [ ] **Step 4: 完整验证**

```powershell
uv run pytest -q
uv run ruff check .
uv run mypy src
Set-Location web
npm.cmd test -- --run
npm.cmd run build
Set-Location ..
uv run oc smoke --static-dir web/dist
uv run oc verify --profile local --static-dir web/dist
uv run oc verify --profile real-ai --static-dir web/dist
```

- [ ] **Step 5: 独立 Code Review 与提交**

启动子代理复审 schema、AI 校验、API、SQLite 事务、前端入口和网络边界；修复所有 P0/P1/P2 后执行：

```powershell
git diff --check
git status --short --branch
git add src tests web docs/superpowers/specs/2026-07-21-opportunity-fit-review-design.md docs/superpowers/plans/2026-07-21-opportunity-fit-review.md
git commit -m "feat: AI add opportunity fit review"
```

## Self-review

- 快照、严格引用、模型重试、API 幂等、软删除并发、Deep Review 只读快照、前端两阶段入口和旧匹配 API 兼容均有明确任务。
- 计划没有使用 URL 抓取、外部平台、自动投递、Pilot 写工具或匹配分。
- `source_snapshot_json` 只在后端内部保存，API 输出不暴露完整快照。
- Triage 与 Deep Review 的模型调用都在 session 关闭后进行，第二阶段均以 `BEGIN IMMEDIATE` 重新检查 Application。
