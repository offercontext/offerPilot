# Application Outcome Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build immutable application submission snapshots and append-only outcome feedback with shared UI/Pilot confirmation paths.

**Architecture:** Add an isolated application-outcome repository that freezes current Resume/JD/Material data transactionally and derives source state on read. Expose narrow REST endpoints plus two deterministic, provider-free Pilot actions. Add one reusable Drawer in Application Detail without changing page layout.

**Tech Stack:** Python 3.12, SQLAlchemy, FastAPI, SQLite, React 19, TypeScript, TanStack Query, Ant Design, Vitest.

---

### Task 1: Schema and migration

**Files:**
- Modify: `src/offerpilot/models.py`
- Modify: `src/offerpilot/db.py`
- Create: `tests/test_application_outcome_migrations.py`

- [ ] Write migration tests asserting both new tables, indexes, foreign keys, unique application/key constraints, migration `0020_application_outcome_feedback`, idempotent upgrade, and coexistence with `0018`/`0019`.
- [ ] Run `uv run pytest tests/test_application_outcome_migrations.py -q` and confirm failure before implementation.
- [ ] Add `ApplicationSubmissionSnapshot` and `ApplicationOutcome` models and register both in the explicit application foreign-key inventory.
- [ ] Add additive `_ensure_application_outcome_schema()` and record migration `0020_application_outcome_feedback`.
- [ ] Re-run the migration test and commit with `feat: AI add application outcome schema`.

### Task 2: Repository and source-state derivation

**Files:**
- Create: `src/offerpilot/repositories/application_outcomes.py`
- Create: `tests/test_application_outcomes_repository.py`

- [ ] Write failing tests for atomic ownership checks, canonical snapshots, same-key replay, changed-input conflict, append-only outcomes, enum/length validation, source `current|changed|missing`, event ownership, stable ordering and deterministic summary counts.
- [ ] Implement `ApplicationOutcomesRepository`, `SubmissionSnapshotCreate`, `OutcomeCreate`, typed conflict/validation exceptions and canonical hashing helpers.
- [ ] Use `BEGIN IMMEDIATE`; never read external URLs and never write other domains.
- [ ] Run `uv run pytest tests/test_application_outcomes_repository.py -q` and commit with `feat: AI freeze application submission outcomes`.

### Task 3: REST API

**Files:**
- Modify: `src/offerpilot/schemas.py`
- Modify: `src/offerpilot/api.py`
- Create: `tests/test_application_outcomes_api.py`

- [ ] Write failing API tests for all five routes, 201/200 replay, stable 404/409/422 codes, source states, summary and no cross-domain writes.
- [ ] Add Pydantic output schemas and register repository in `create_app()`.
- [ ] Add route handlers with server-owned `source_kind=ui`, strict type checks and stable error mapping.
- [ ] Run `uv run pytest tests/test_application_outcomes_api.py -q` and commit with `feat: AI expose application outcome APIs`.

### Task 4: Deterministic Pilot confirmation

**Files:**
- Modify: `src/offerpilot/ai/deterministic_actions.py`
- Modify: `src/offerpilot/ai/tools.py`
- Modify: `src/offerpilot/api.py`
- Modify: `tests/test_chat_api.py`

- [ ] Write failing tests for explicit archive/outcome actions, provider call count zero, PendingAction persistence, approval/rejection, same token/key recovery, edited fields, application ownership and no keyword auto-trigger.
- [ ] Extend `parse_pilot_action` with discriminated payloads and builders for both tools.
- [ ] Add model-hidden, always-confirm tool definitions backed by the same repository.
- [ ] Route explicit actions through the existing deterministic chat/confirmation machinery; keep ordinary conversation unchanged.
- [ ] Run the focused chat tests and commit with `feat: AI confirm application outcomes in Pilot`.

### Task 5: Frontend types, services and Drawer

**Files:**
- Create: `web/src/types/applicationOutcome.ts`
- Create: `web/src/services/applicationOutcomes.ts`
- Create: `web/src/services/applicationOutcomes.test.ts`
- Create: `web/src/components/ApplicationOutcomeDrawer.tsx`
- Create: `web/src/components/ApplicationOutcomeDrawer.module.css`
- Create: `web/src/components/ApplicationOutcomeDrawer.test.tsx`
- Modify: `web/src/types/chat.ts`
- Modify: `web/src/components/ApplicationDetail.tsx`
- Modify: `web/src/layout/AppShell.tsx`

- [ ] Write failing service tests for exact routes and payloads.
- [ ] Write failing mounted Drawer tests for source selection, direct archive/outcome saves, summary, immutable history, Pilot handoff, loading/error/empty states and no hidden writes.
- [ ] Implement typed services and a light-theme responsive Drawer with fixed Chinese labels.
- [ ] Add the Application Detail entry and pass explicit `pilot_action` through AppShell with contextual initial messages.
- [ ] Run the focused frontend tests, `npm.cmd exec tsc -- -b`, and commit with `feat: AI add application outcome workspace`.

### Task 6: Smoke, browser acceptance and screenshots

**Files:**
- Modify: `src/offerpilot/smoke.py`
- Create: `docs/reports/2026-08-12-application-outcome-feedback-release-verification.md`
- Create: `artifacts/2026-08-12-application-outcome-feedback/*.png`

- [ ] Add a local smoke flow proving UI API creation, same-key replay, deterministic Pilot confirmation and zero Provider calls.
- [ ] Build and start an isolated deployment; create Chinese candidate “筱哲”, Resume, JD and Material Kit data.
- [ ] In a 1455×1200 light viewport, complete UI archive, UI feedback, Pilot feedback confirmation and history/summary reload.
- [ ] Save and visually inspect at least four screenshots: UI archive form, UI feedback/history, Pilot confirmation card, final summary.
- [ ] Assert browser requests are local-only and clean all processes, ports and temporary data in `finally`.

### Task 7: Final verification and integration

**Files:**
- Modify: `docs/reports/2026-08-12-application-outcome-feedback-release-verification.md`

- [ ] Run focused backend/frontend suites, Ruff, Mypy, TypeScript and production build.
- [ ] Run backend five-group manifest/aggregate gate and frontend grouped source-fingerprint gate; verify no duplicate node IDs and only the four established symlink skips.
- [ ] Run `uv run oc smoke --static-dir web/dist` and `uv run oc verify --profile local --static-dir web/dist`.
- [ ] Self-review security, ownership, idempotency, async state and cross-domain write boundaries; resolve every P0/P1/P2 finding.
- [ ] Update the report with exact counts, screenshot dimensions/hashes, breaking changes and remaining risks.
- [ ] Run `git diff --check`, verify a clean worktree, commit the report, then fast-forward merge the branch to local `main` without pushing.
