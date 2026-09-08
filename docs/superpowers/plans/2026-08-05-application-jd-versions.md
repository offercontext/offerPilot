# Application JD Versions Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Each step uses checkbox syntax and must be completed in order.

**Goal:** Add an immutable, Application-bound JD version chain and make every new Application-bound workflow consume and freeze the current confirmed JD without URL fetching, implicit AI writes, or cross-domain side effects.

**Architecture:** Create one application_jd_versions table and one shared ApplicationJDService/Repository. The save transaction uses idempotency replay before an expected_current_version_id CAS; UI and Pilot supply a server-owned source_kind while sharing the same service. New domain rows store a plain integer jd_version_id plus a self-contained immutable snapshot, and parent workflows inherit the parent snapshot instead of accepting a second JD.

**Tech Stack:** Python, SQLAlchemy, SQLite migrations, FastAPI, pytest, FastAPI TestClient, React, TypeScript, Vitest, existing Pilot tool/confirmation infrastructure, PowerShell browser harnesses.

---

## 0. Execution boundary and immutable implementation baseline

**Files:**
- Read: AGENTS.md
- Read: docs/superpowers/specs/2026-08-05-application-jd-versions-design.md
- No product file changes in this task.

- [ ] **Step 1: Verify the worktree and capture a stable baseline.**

Run from D:\Users\yuqi.chen\offerpilot\.worktrees\feat-20260805-application-jd-versions before changing code. The plan commit, not a hard-coded hash, is the implementation baseline:

~~~powershell
$planPath = 'docs/superpowers/plans/2026-08-05-application-jd-versions.md'
$baselineFile = Join-Path $env:TEMP 'offerpilot-application-jd-versions-baseline.txt'
$implementationBase = (git log -1 --format=%H -- $planPath).Trim()
if (-not $implementationBase) { throw 'Cannot resolve approved plan baseline' }
if (@(git status --short).Count -ne 0) { throw 'Worktree must be clean before implementation' }
git cat-file -e "$implementationBase^{commit}"
if ($LASTEXITCODE -ne 0) { throw 'Implementation baseline is not a commit' }
$implementationBase | Set-Content -LiteralPath $baselineFile -Encoding ascii
~~~

The plan and design become read-only after this step. Every subsequent PowerShell process must load and validate the same file before running an allowlist or diff check:

~~~powershell
$baselineFile = Join-Path $env:TEMP 'offerpilot-application-jd-versions-baseline.txt'
$implementationBase = (Get-Content -LiteralPath $baselineFile -Raw).Trim()
git cat-file -e "$implementationBase^{commit}"
if ($LASTEXITCODE -ne 0) { throw 'Recorded implementation baseline is invalid' }
~~~

- [ ] **Step 2: Freeze the implementation allowlist.**

Only these product files may change in the implementation slice:

~~~powershell
$allowlistFile = Join-Path $env:TEMP 'offerpilot-application-jd-versions-allowlist.txt'
@'
src/offerpilot/db.py
src/offerpilot/models.py
src/offerpilot/repositories/application_jd_versions.py
src/offerpilot/repositories/jd.py
src/offerpilot/repositories/opportunity_fit_reviews.py
src/offerpilot/repositories/material_kits.py
src/offerpilot/repositories/material_revision_proposals.py
src/offerpilot/repositories/interview_preparation_proposals.py
src/offerpilot/repositories/mock_interviews.py
src/offerpilot/api.py
src/offerpilot/cli.py
src/offerpilot/ai/tools.py
src/offerpilot/ai/agent.py
src/offerpilot/smoke.py
tests/test_applications_repository.py
tests/test_api_contract.py
tests/test_ai_tools.py
tests/test_chat_api.py
tests/test_cli.py
tests/test_opportunity_fit_reviews_repository.py
tests/test_material_revision_proposals_repository.py
tests/test_interview_preparation_repository.py
tests/test_mock_interview_repository.py
tests/test_application_jd_smoke.py
tests/test_application_jd_browser_harness.py
web/src/components/ApplicationDetail.tsx
web/src/components/ApplicationDetail.module.css
web/src/layout/AppShell.tsx
web/src/layout/AppShell.applicationJdVersions.test.tsx
web/src/components/MaterialKitDrawer.tsx
web/src/components/MaterialKitDrawer.module.css
web/src/components/MaterialKitDrawer.evidenceBundles.test.tsx
web/src/components/OpportunityFitReviewDrawer.tsx
web/src/components/OpportunityFitReviewDrawer.test.tsx
web/src/components/InterviewPreparationProposalDrawer.tsx
web/src/components/InterviewPreparationProposalDrawer.test.tsx
web/src/components/InterviewPreparationProposalDrawer.interaction.test.tsx
web/src/components/MockInterviewDrawer.tsx
web/src/components/MockInterviewDrawer.safety.test.ts
web/src/components/MockInterviewDrawer.cleanup.interaction.test.tsx
web/src/services/materialKits.ts
web/src/services/materialKits.test.ts
web/src/services/materialRevisionProposals.ts
web/src/services/materialRevisionProposals.test.ts
web/src/services/opportunityFitReviews.ts
web/src/services/opportunityFitReviews.test.ts
web/src/services/interviewPreparationProposals.ts
web/src/services/interviewPreparationProposals.test.ts
web/src/services/mockInterviews.ts
web/src/services/mockInterviews.test.ts
web/src/types/materialKit.ts
web/src/types/materialRevisionProposal.ts
web/src/types/opportunityFitReview.ts
web/src/types/interviewPreparationProposal.ts
web/src/types/mockInterview.ts
web/src/services/applicationJdVersions.ts
web/src/services/applicationJdVersions.test.ts
web/src/types/applicationJdVersion.ts
tests/test_application_jd_versions_migrations.py
tests/test_application_jd_versions_repository.py
tests/test_application_jd_versions_api.py
tests/test_jd_resume_ai_api.py
tests/test_material_kits_api.py
tests/test_material_revision_proposals_api.py
tests/test_opportunity_fit_reviews_api.py
tests/test_interview_preparation_api.py
tests/test_mock_interview_api.py
web/src/components/ApplicationDetail.jdVersions.test.tsx
web/src/components/ApplicationDetail.jdVersions.integration.test.tsx
web/src/components/ChatPanel/PilotTaskCard.tsx
web/src/components/ChatPanel/MessageBubble.tsx
web/src/components/ChatPanel/model.ts
web/src/components/ChatPanel/PilotApplicationJdCard.test.tsx
web/src/features/pilot/applicationJdVersion.ts
web/src/features/pilot/applicationJdVersion.test.ts
web/src/features/pilot/PilotOpportunityFitV2Card.tsx
web/src/features/pilot/PilotOpportunityFitV2Card.test.tsx
web/src/features/pilot/pilotOpportunityFitLifecycle.ts
web/src/features/pilot/pilotOpportunityFitLifecycle.test.ts
web/src/features/pilot/materialKitHandoff.ts
web/src/features/pilot/materialKitHandoff.test.ts
scripts/application-jd-real-ai-browser-harness.ps1
scripts/browser-network-audit.py
scripts/application_jd_stage_diagnostic.py
tests/test_application_jd_stage_diagnostic.py
docs/superpowers/plans/2026-08-05-application-jd-versions.md
docs/reports/2026-08-05-application-jd-versions-release-verification.md
'@ | Set-Content -LiteralPath $allowlistFile -Encoding ascii
~~~

Any other changed path fails the implementation gate. Do not touch the root worktree's existing uncommitted tests/test_smoke.py; all work stays in this worktree. The approved plan is already the review artifact; execution must not create another plan commit. After Step 1 captures the baseline, the design and plan are read-only until the final report is committed. The here-string above is the single allowlist source; every later PowerShell process loads `$allowlistFile` and must not redeclare a second path list.

## 1. Migration and model contract

**Files:**
- Create: tests/test_application_jd_versions_migrations.py
- Modify: src/offerpilot/models.py
- Modify: src/offerpilot/db.py
- Modify: tests/test_applications_repository.py for the model registration and deletion regression.

- [ ] **Step 1: Add failing migration tests from real prior DDL.**

Create a fixture that builds the last pre-feature schema, including schema_migrations through 0017_offer_comparison_negotiation, applications, and every table receiving jd_version_id. Do not call create_all before the upgrade. Assert the pre-upgrade DDL has no application_jd_versions table and no new columns.

Add tests with these exact assertions:

~~~python
assert _table_columns(db_path, 'application_jd_versions') == {
    'id', 'application_id', 'version_number', 'jd_text', 'content_sha256',
    'source_url', 'source_kind', 'idempotency_key',
    'request_fingerprint_sha256', 'created_at',
}
assert _table_columns(db_path, 'mock_interview_attempts') >= {'jd_version_id'}
assert _table_columns(db_path, 'opportunity_fit_review_stages') >= {'jd_version_id'}
assert _schema_migration_count(db_path, '0018_application_jd_versions') == 1
~~~

Also assert the added identity columns are nullable ordinary integers with no foreign-key entry, while application_jd_versions.application_id is the only new JD-version foreign key and uses ON DELETE CASCADE. Test the migration twice and compare all pre-existing application, JD analysis, resume match, Proposal, and Attempt bytes before and after.

Add one deletion test that creates an Application, one JD version, a JDAnalysis, and a ResumeMatch, then deletes the Application. Assert the JD version is cascaded, JDAnalysis/ResumeMatch remain with application_id set to NULL while their plain-integer jd_version_id and snapshot bytes remain, and no deletion is blocked by any added identity column. Assert the snapshot-bearing history rows retain their original bytes whenever their existing parent deletion rules retain them.

Run:

~~~powershell
uv run pytest tests/test_application_jd_versions_migrations.py -q
~~~

Expected before implementation: FAIL because the table, columns, and migration do not exist.

- [ ] **Step 2: Add the SQLAlchemy model and migration.**

Add ApplicationJDVersion to src/offerpilot/models.py with these constraints:

~~~python
__table_args__ = (
    UniqueConstraint('application_id', 'version_number'),
    UniqueConstraint('application_id', 'idempotency_key'),
    Index('idx_application_jd_versions_app_version', 'application_id', 'version_number'),
)
~~~

Use Text for jd_text, String for hashes and metadata, and a nullable source URL. Add nullable Integer columns named jd_version_id to the exact tables in the approved design without ForeignKey declarations. Add JDAnalysis.jd_version_id and ResumeMatch.jd_version_id as nullable ordinary integers too.

Extend src/offerpilot/db.py with 0018_application_jd_versions: create the new table and use the existing migration helpers to add missing columns idempotently. Preserve old rows as NULL; do not infer a version from any existing jd_text, jd_snapshot, or Proposal snapshot. Keep Application deletion behavior exactly as the existing parent tables define it.

- [ ] **Step 3: Run migration and model tests.**

~~~powershell
uv run pytest tests/test_application_jd_versions_migrations.py -q
uv run pytest tests/test_applications_repository.py -q
~~~

Expected result: all new migration tests and existing application repository tests pass; the migration is idempotent and old bytes are unchanged.

- [ ] **Step 4: Commit the migration slice.**

~~~powershell
git add src/offerpilot/models.py src/offerpilot/db.py tests/test_application_jd_versions_migrations.py tests/test_applications_repository.py
git commit -m "feat: AI add application JD version storage"
~~~

## 2. JD version service, repository, and deterministic validation

**Files:**
- Create: src/offerpilot/repositories/application_jd_versions.py
- Create: tests/test_application_jd_versions_repository.py

- [ ] **Step 1: Add failing repository tests for validation and fingerprinting.**

The new module must expose ApplicationJDService; its public operations must be named and typed as:

~~~python
get_current(application_id: int) -> ApplicationJDVersion | None
require_current_version(
    application_id: int,
    requested_jd_version_id: int,
) -> ApplicationJDVersion
list_versions(application_id: int, offset: int, limit: int) -> list[ApplicationJDVersionSummary]
get_version(application_id: int, version_id: int) -> ApplicationJDVersion | None
freeze(version: ApplicationJDVersion) -> FrozenApplicationJD
create_version(
    application_id: int,
    *,
    jd_text: str,
    source_url: str | None,
    source_kind: Literal['ui', 'pilot'],
    expected_current_version_id: int | None,
    idempotency_key: str,
) -> VersionCreateResult
~~~

Add tests that reject all of the following with no row inserted: non-string or blank JD, UTF-8 bytes over 60,000, non-string or invalid URL, relative URL, javascript:, data:, file:, missing host, and idempotency keys not matching ^[A-Za-z0-9_-]{16,128}$.

Use explicit Python assertions proving bool, string, 0, and negative values are rejected for expected_current_version_id, while a strict positive int and None are accepted:

~~~python
valid = {
    'application_id': app_id,
    'jd_text': '后端工程师\\n负责 API 设计',
    'source_url': None,
    'source_kind': 'ui',
    'idempotency_key': 'jd-key-00000001',
}
with pytest.raises(JDVersionValidationError):
    service.create_version(**valid, expected_current_version_id=True)
with pytest.raises(JDVersionValidationError):
    service.create_version(**valid, expected_current_version_id='1')
with pytest.raises(JDVersionValidationError):
    service.create_version(**valid, expected_current_version_id=0)
~~~

Add a fingerprint table test for raw CJK/emoji/newline/leading-space bytes, None/empty/whitespace URL normalization, and source_kind differences. Assert the stored content_sha256 hashes the exact original UTF-8 bytes and request_fingerprint_sha256 follows the approved Base64-plus-canonical-JSON algorithm.

The service must never silently replace a caller-supplied version with the current one. `require_current_version(application_id, requested_jd_version_id)` rejects a non-positive/non-integer ID, a version belonging to another Application, and an older version from the same Application unless that ID is exactly current. These cases map to the stable 409 `application_jd_source_conflict` response at API boundaries. The first-save path remains `create_version(... expected_current_version_id=None)`; this helper is for Application-bound consumers that already have a requested version.

- [ ] **Step 2: Add failing CAS and idempotency tests.**

Cover these exact outcomes:

1. First save with expected_current_version_id=None returns version 1.
2. Same key and identical normalized input returns the original row with no new version.
3. Same key and different JD/source/entry fingerprint returns idempotency conflict semantics without mutating the original row.
4. Two connections both expecting v1 produce exactly one v2 and one application_jd_stale_current_version; no v3 is created.
5. A successful key replay still returns its original row even after another key creates a newer current version.

- [ ] **Step 3: Implement the minimum service and repository.**

Implement strict validators, URL normalization, the 240-code-point preview helper, and the fingerprint helper in the new repository module. The transaction must execute in this order:

~~~text
BEGIN IMMEDIATE
  load application and reject invisible/deleted application
  load (application_id, idempotency_key)
  if found: compare request_fingerprint_sha256 and replay or raise idempotency conflict
  load current version
  compare expected_current_version_id using strict integer/null semantics
  allocate max(version_number) + 1
  insert immutable row
COMMIT
~~~

source_kind is a trusted service argument only; it is never parsed from the public JSON body. The repository returns summaries without jd_text and returns full text only from get_version/get_current detail data.

- [ ] **Step 4: Run repository tests.**

~~~powershell
uv run pytest tests/test_application_jd_versions_repository.py -q
~~~

Expected result: all validation, fingerprint, CAS, replay, ordering, and preview tests pass.

- [ ] **Step 5: Commit the repository slice.**

~~~powershell
git add src/offerpilot/repositories/application_jd_versions.py tests/test_application_jd_versions_repository.py
git commit -m "feat: AI enforce application JD version CAS"
~~~

## 3. JD version API and ApplicationDetail data contract

**Files:**
- Create: tests/test_application_jd_versions_api.py
- Modify: src/offerpilot/api.py
- Create: web/src/types/applicationJdVersion.ts
- Create: web/src/services/applicationJdVersions.ts
- Create: web/src/services/applicationJdVersions.test.ts

- [ ] **Step 1: Add failing API tests for route shape and status semantics.**

Cover:

~~~text
GET  /api/applications/{id}/job-description
GET  /api/applications/{id}/job-description/versions?offset=0&limit=50
GET  /api/applications/{id}/job-description/versions/{version_id}
POST /api/applications/{id}/job-description/versions
~~~

Assert current-empty returns 200 with {"current": null}, current/detail return full text, history returns metadata plus preview but never jd_text, and detail rejects another Application's version with 404.

Assert request bodies containing source_kind are rejected rather than trusted. UI requests always persist source_kind='ui'; the response includes it. Assert 201, idempotent 200, stable 404, 409 application_jd_idempotency_conflict, 409 application_jd_stale_current_version, and 422 validation errors. There must be no 202 response for this synchronous save.

Send expected_current_version_id as true, '1', 0, and -1 through TestClient and assert each returns 422 without an inserted version; send null and a positive JSON integer through the same route and assert they reach the repository.

- [ ] **Step 2: Implement API serializers, route order, and UI service types.**

Define discriminated TypeScript shapes:

~~~typescript
export type ApplicationJdVersionSummary = {
  id: number;
  application_id: number;
  version_number: number;
  content_sha256: string;
  source_url: string | null;
  source_kind: 'ui' | 'pilot';
  utf8_byte_length: number;
  preview: string;
  created_at: string;
};

export type ApplicationJdVersion = ApplicationJdVersionSummary & { jd_text: string };
~~~

The public UI POST body contains jd_text, source_url, expected_current_version_id, and idempotency_key, but not source_kind. Register static job-description routes before any dynamic application child route that could consume versions as an ID. Map errors to fixed Chinese copy and preserve the original key/input on network, timeout, response-loss, and bare-5xx unknown outcomes.

- [ ] **Step 3: Run API tests and type/service tests.**

~~~powershell
uv run pytest tests/test_application_jd_versions_api.py -q
cd web
npm.cmd test -- --run src/services/applicationJdVersions.test.ts
cd ..
~~~

Expected result: API and client contract tests pass; no existing endpoint response changes outside the new JD routes.

- [ ] **Step 4: Commit the API contract slice.**

~~~powershell
git add src/offerpilot/api.py tests/test_application_jd_versions_api.py web/src/types/applicationJdVersion.ts web/src/services/applicationJdVersions.ts web/src/services/applicationJdVersions.test.ts
git commit -m "feat: AI expose application JD version API"
~~~

## 4. Application-bound domain handoff and legacy write closure

**Files:**
- Modify: src/offerpilot/api.py
- Modify: src/offerpilot/repositories/opportunity_fit_reviews.py
- Modify: src/offerpilot/repositories/material_kits.py
- Modify: src/offerpilot/repositories/material_revision_proposals.py
- Modify: src/offerpilot/repositories/interview_preparation_proposals.py
- Modify: src/offerpilot/repositories/mock_interviews.py
- Modify: src/offerpilot/repositories/jd.py
- Modify: tests/test_jd_resume_ai_api.py
- Modify: tests/test_material_kits_api.py
- Modify: tests/test_material_revision_proposals_api.py
- Modify: tests/test_opportunity_fit_reviews_api.py
- Modify: tests/test_interview_preparation_api.py
- Modify: tests/test_mock_interview_api.py
- Modify: tests/test_opportunity_fit_reviews_repository.py
- Modify: tests/test_material_revision_proposals_repository.py
- Modify: tests/test_interview_preparation_repository.py
- Modify: tests/test_mock_interview_repository.py
- Modify: tests/test_cli.py
- Modify: src/offerpilot/cli.py
- Modify: web/src/components/MaterialKitDrawer.tsx
- Modify: web/src/components/MaterialKitDrawer.module.css
- Modify: web/src/components/MaterialKitDrawer.evidenceBundles.test.tsx
- Modify: web/src/components/OpportunityFitReviewDrawer.tsx
- Modify: web/src/components/InterviewPreparationProposalDrawer.tsx
- Modify: web/src/components/MockInterviewDrawer.tsx
- Modify: web/src/services/materialKits.ts
- Modify: web/src/services/materialRevisionProposals.ts
- Modify: web/src/services/opportunityFitReviews.ts
- Modify: web/src/services/interviewPreparationProposals.ts
- Create: web/src/services/interviewPreparationProposals.test.ts
- Modify: web/src/services/mockInterviews.ts
- Create: web/src/services/mockInterviews.test.ts
- Modify: web/src/types/materialKit.ts
- Modify: web/src/types/materialRevisionProposal.ts
- Modify: web/src/types/opportunityFitReview.ts
- Modify: web/src/types/interviewPreparationProposal.ts
- Modify: web/src/types/mockInterview.ts
- Modify: web/src/features/pilot/PilotOpportunityFitV2Card.tsx
- Modify: web/src/features/pilot/pilotOpportunityFitLifecycle.ts
- Modify: web/src/features/pilot/materialKitHandoff.ts
- Modify: web/src/features/pilot/PilotOpportunityFitV2Card.test.tsx
- Modify: web/src/features/pilot/pilotOpportunityFitLifecycle.test.ts
- Modify: web/src/features/pilot/materialKitHandoff.test.ts

- [ ] **Step 1: Add failing integration tests for the version matrix.**

Create v1 and v2 JD records for one Application and verify:

- Triage accepts only current v2, persists its jd_version_id and snapshot, and Deep Review accepts only the confirmed Triage parent, inheriting the same version.
- Triage POST without schema_version=2, with schema_version=1, or with another version returns 410 opportunity_fit_v1_write_disabled, makes zero Provider calls, and creates zero records.
- Material Kit freezes the current version; Material Proposal ignores client version input and inherits Kit; stale Kit generation returns the existing source conflict without Provider work.
- Interview Preparation accepts current version only and persists the version in its explicit column and input snapshot.
- Mock Interview claim stores the current version atomically; preparing from a proposal with a different version returns 409; after claim, changing the current JD does not invalidate the Attempt, and its next question/feedback still use the frozen JD.

Use two SQLite connections and Provider barriers for Triage, Material Kit, and Interview Preparation. For each barrier, the short claim transaction must commit and close before the blocked call; update the Application JD while blocked; final source CAS must return `application_jd_source_conflict` and must not create a ready result. For Mock, the post-claim barrier instead asserts the intentional exception: a JD update does not invalidate the Attempt, the remaining turns use its frozen JD, and history reports `source_changed`.

- [ ] **Step 2: Add failing frontend handoff tests.**

Before changing any downstream UI, add mounted Vitest tests at the existing component/service boundaries. Mock only HTTP responses and Provider calls; do not satisfy these tests with source-string scans. Assert all of the following:

- `MaterialKitDrawer`: with a confirmed current JD, the create request carries `application_id` and `jd_version_id` and does not carry free `jd_text`; with no current version, the generation control is disabled; `source_url` is displayed as copy-only text and no browser navigation or URL fetch occurs.
- `OpportunityFitReviewDrawer` and the Pilot v2 card: Triage sends `schema_version=2` plus the selected current `jd_version_id` and no legacy `jd_text`; Deep Review receives its version only from the confirmed Triage parent; a stale/changed source is rendered as a Chinese source-change state and cannot silently submit a new version.
- `InterviewPreparationProposalDrawer`: the preparation request uses the current `jd_version_id`, disables generation when no current version is loaded, and preserves the frozen version/source state when the current JD changes while the request is blocked.
- `MockInterviewDrawer`: the initial claim uses the current version, later turns use the claimed frozen version, a selected preparation proposal inherits its version, and an updated Application JD is shown as `source_changed` without replacing the Attempt context.
- Pilot handoff helpers: `PilotOpportunityFitV2Card`, `pilotOpportunityFitLifecycle`, and `materialKitHandoff` preserve the same version context and perform no source URL request or cross-domain write before confirmation.

Run the failing tests first:

~~~powershell
cd web
npm.cmd test -- --run src/components/MaterialKitDrawer.evidenceBundles.test.tsx src/components/OpportunityFitReviewDrawer.test.tsx src/components/InterviewPreparationProposalDrawer.test.tsx src/components/InterviewPreparationProposalDrawer.interaction.test.tsx src/components/MockInterviewDrawer.safety.test.ts src/components/MockInterviewDrawer.cleanup.interaction.test.tsx src/features/pilot/PilotOpportunityFitV2Card.test.tsx src/features/pilot/pilotOpportunityFitLifecycle.test.ts src/features/pilot/materialKitHandoff.test.ts src/services/materialKits.test.ts src/services/materialRevisionProposals.test.ts src/services/opportunityFitReviews.test.ts src/services/interviewPreparationProposals.test.ts src/services/mockInterviews.test.ts
cd ..
~~~

Expected result before implementation: these tests fail because the downstream payloads still accept temporary `jd_text`, do not carry the version identity, or do not expose the required source-change/disabled states.

- [ ] **Step 3: Add failing legacy endpoint tests.**

Assert:

~~~text
POST /api/jd/analyze without application_id + jd_text -> existing standalone behavior
POST /api/resumes/{id}/match without application_id + jd_text -> existing standalone behavior
same endpoints with application_id + jd_text -> 422 application_jd_version_required
same endpoints with application_id + current jd_version_id -> use version text
Application-bound material/fit/preparation/mock old jd_text -> 422, zero Provider calls
~~~

For Opportunity Fit, any new POST not carrying exact schema_version=2 returns 410 opportunity_fit_v1_write_disabled, including a body that carries jd_version_id; the v1 GET list/detail schema remains read-only.

- [ ] **Step 4: Implement one shared handoff helper, domain adapters, and the tested frontend handoffs.**

The helper must provide these operations without exposing internal IDs to Provider payloads:

~~~python
current = jd_versions.require_current_version(
    application_id,
    requested_jd_version_id,
)
frozen = jd_versions.freeze(current)
assert frozen.jd_version_id == requested_jd_version_id
~~~

The first-claim adapters (Opportunity Fit Triage, Material Kit, Interview Preparation, and the initial Mock Interview claim) must call this exact helper. Deep Review takes only its confirmed Triage row; Material Proposal takes only its Kit parent; neither accepts a client JD version. Mock starts with the selected current version and, once claim commits, keeps that frozen context for every question and feedback step even if the Application receives a newer JD version.

Every Provider-backed adapter uses this explicit lifecycle: a short transaction validates the requested current version and persists the immutable frozen snapshot; it commits and closes its Session before any Provider call; the final write opens a new short transaction and performs a source/revision CAS against the frozen identity. Add Provider barrier tests for Triage, Material Kit, and Interview Preparation: update the Application JD while the Provider is blocked, then assert the final source CAS returns the stable conflict and no ready result is written. The Mock exception is intentional: after a successful Attempt claim, a later JD update marks history `source_changed` but does not invalidate the Attempt or prevent its remaining frozen-context turns.

Update `src/offerpilot/cli.py` only for the explicitly specified standalone-versus-Application-bound behavior and cover that behavior in `tests/test_cli.py`; do not add a CLI JD write path. Remove application-bound use of free jd_text while preserving standalone JD analysis and Resume Match.

- [ ] **Step 5: Run the handoff, legacy, and frontend suites.**

~~~powershell
uv run pytest tests/test_jd_resume_ai_api.py tests/test_material_kits_api.py tests/test_material_revision_proposals_api.py tests/test_opportunity_fit_reviews_api.py tests/test_interview_preparation_api.py tests/test_mock_interview_api.py tests/test_opportunity_fit_reviews_repository.py tests/test_material_revision_proposals_repository.py tests/test_interview_preparation_repository.py tests/test_mock_interview_repository.py tests/test_cli.py -q
cd web
npm.cmd test -- --run src/components/MaterialKitDrawer.evidenceBundles.test.tsx src/components/OpportunityFitReviewDrawer.test.tsx src/components/InterviewPreparationProposalDrawer.test.tsx src/components/InterviewPreparationProposalDrawer.interaction.test.tsx src/components/MockInterviewDrawer.safety.test.ts src/components/MockInterviewDrawer.cleanup.interaction.test.tsx src/features/pilot/PilotOpportunityFitV2Card.test.tsx src/features/pilot/pilotOpportunityFitLifecycle.test.ts src/features/pilot/materialKitHandoff.test.ts src/services/materialKits.test.ts src/services/materialRevisionProposals.test.ts src/services/opportunityFitReviews.test.ts src/services/interviewPreparationProposals.test.ts src/services/mockInterviews.test.ts
cd ..
~~~

Expected result: new handoff tests pass, all old v1 write tests are updated to the stable disabled error, and no Provider is called on rejected requests.

- [ ] **Step 6: Commit the domain handoff slice.**

~~~powershell
git add src/offerpilot/api.py src/offerpilot/cli.py src/offerpilot/repositories/jd.py src/offerpilot/repositories/opportunity_fit_reviews.py src/offerpilot/repositories/material_kits.py src/offerpilot/repositories/material_revision_proposals.py src/offerpilot/repositories/interview_preparation_proposals.py src/offerpilot/repositories/mock_interviews.py tests/test_cli.py tests/test_jd_resume_ai_api.py tests/test_material_kits_api.py tests/test_material_revision_proposals_api.py tests/test_opportunity_fit_reviews_api.py tests/test_interview_preparation_api.py tests/test_mock_interview_api.py tests/test_opportunity_fit_reviews_repository.py tests/test_material_revision_proposals_repository.py tests/test_interview_preparation_repository.py tests/test_mock_interview_repository.py web/src/components/MaterialKitDrawer.tsx web/src/components/MaterialKitDrawer.module.css web/src/components/MaterialKitDrawer.evidenceBundles.test.tsx web/src/components/OpportunityFitReviewDrawer.tsx web/src/components/OpportunityFitReviewDrawer.test.tsx web/src/components/InterviewPreparationProposalDrawer.tsx web/src/components/InterviewPreparationProposalDrawer.test.tsx web/src/components/InterviewPreparationProposalDrawer.interaction.test.tsx web/src/components/MockInterviewDrawer.tsx web/src/components/MockInterviewDrawer.safety.test.ts web/src/components/MockInterviewDrawer.cleanup.interaction.test.tsx web/src/services/materialKits.ts web/src/services/materialKits.test.ts web/src/services/materialRevisionProposals.ts web/src/services/materialRevisionProposals.test.ts web/src/services/opportunityFitReviews.ts web/src/services/opportunityFitReviews.test.ts web/src/services/interviewPreparationProposals.ts web/src/services/interviewPreparationProposals.test.ts web/src/services/mockInterviews.ts web/src/services/mockInterviews.test.ts web/src/types/materialKit.ts web/src/types/materialRevisionProposal.ts web/src/types/opportunityFitReview.ts web/src/types/interviewPreparationProposal.ts web/src/types/mockInterview.ts web/src/features/pilot/PilotOpportunityFitV2Card.tsx web/src/features/pilot/pilotOpportunityFitLifecycle.ts web/src/features/pilot/materialKitHandoff.ts web/src/features/pilot/PilotOpportunityFitV2Card.test.tsx web/src/features/pilot/pilotOpportunityFitLifecycle.test.ts web/src/features/pilot/materialKitHandoff.test.ts
git commit -m "feat: AI hand off current JD to application workflows"
~~~

## 5. ApplicationDetail UI, history, CAS recovery, and zero-write behavior

**Files:**
- Modify: web/src/layout/AppShell.tsx
- Create: web/src/layout/AppShell.applicationJdVersions.test.tsx
- Modify: web/src/components/ApplicationDetail.tsx
- Modify: web/src/components/ApplicationDetail.module.css
- Create: web/src/components/ApplicationDetail.jdVersions.test.tsx
- Create: web/src/components/ApplicationDetail.jdVersions.integration.test.tsx

- [ ] **Step 1: Add failing mounted tests.**

Mount the real ApplicationDetail with the existing router/query providers and mock only the HTTP boundary. Cover these states:

- no current version: neutral empty state and “添加 JD”;
- current v1: read-only text, version/source metadata, update/history buttons;
- history list: metadata/240-code-point preview only, opening one version fetches full text, historical content stays unchanged after v2;
- stale CAS: v1 editor opened in two mounted instances, first save succeeds, second shows Chinese “岗位资料已更新，请重新加载后再保存”, keeps its unsaved text, clears the stale save key, and only creates a new key after the user reloads the current version;
- unknown response: editor stays frozen with original key and input; no second key is generated;
- source URL is plain text/copy-only and no window.open, navigation, AI, Provider, or mutation service is called.

Use actual button clicks and request spies; do not satisfy these tests with JSX/source string scanning.

- [ ] **Step 2: Implement the controlled JD module.**

`AppShell` is the owner of remount-safe drafts. Keep a keyed state/ref map by `applicationId`, with this complete shape:

~~~typescript
type ApplicationJdDraft = {
  jdText: string;
  sourceUrl: string;
  expectedCurrentVersionId: number | null;
  idempotencyKey: string | null;
  resultUnknown: boolean;
  pendingOperation: 'save' | null;
};
~~~

`ApplicationDetail` receives the controlled draft and an `onDraftChange(applicationId, patch)` callback; it must not own the retry key. The callback merges from the current `AppShell` ref entry, not a stale render closure. Generate one ASCII key matching `^[A-Za-z0-9_-]{16,128}$` when a save attempt begins. Use this fixed save-result table:

| Result | Draft/key behavior | Next action |
|---|---|---|
| `200` idempotent replay or `201` created | Clear key, `resultUnknown=false`, update current version | None |
| network/timeout/response loss or bare `5xx` | Keep original text, expected ID, key and `pendingOperation='save'`; freeze editing and set `resultUnknown=true` | Retry the same endpoint with the same key/input |
| `409 application_jd_stale_current_version` | Keep unsaved text but clear the stale key and unfreeze after showing the source-change message | User reloads current JD; AppShell updates expected ID; the next submit creates a new key |
| `409 application_jd_idempotency_conflict` | Clear the conflicting key, preserve text for review, and do not replay it | User edits or explicitly starts a new save with a new key |
| `404` application/version not found or `422` validation | No version was created by this request; clear key and pending state, preserve only editable text where the form remains valid | Fix/reload and submit a fresh key |

Only the network/unknown row preserves a key for retry. A stale 409 is deterministic and never retries with its old key. Add a real unmount/remount test that keeps the same `applicationId`, verifies the original JD text, expected version, key, frozen/unknown state, and that retry calls the same endpoint with the same key only for the network/unknown row. Disable all downstream Application-bound generation controls when no current JD exists.

Render source_url as non-link text with copy action, render list preview using the shared 240-code-point helper, and never claim a version is current unless the API response contains that version.

- [ ] **Step 3: Run mounted UI tests.**

~~~powershell
cd web
npm.cmd test -- --run src/components/ApplicationDetail.jdVersions.test.tsx src/components/ApplicationDetail.jdVersions.integration.test.tsx
cd ..
~~~

Expected result: mounted tests pass, write-service spies remain at zero for read/navigation-only actions, and stale/unknown recovery preserves the original draft.

- [ ] **Step 4: Commit the ApplicationDetail slice.**

~~~powershell
git add web/src/layout/AppShell.tsx web/src/layout/AppShell.applicationJdVersions.test.tsx web/src/components/ApplicationDetail.tsx web/src/components/ApplicationDetail.module.css web/src/components/ApplicationDetail.jdVersions.test.tsx web/src/components/ApplicationDetail.jdVersions.integration.test.tsx
git commit -m "feat: AI add application JD history UI"
~~~

## 6. Pilot confirmation and shared write path

**Files:**
- Modify: src/offerpilot/ai/tools.py
- Modify: src/offerpilot/ai/agent.py
- Modify: src/offerpilot/api.py
- Modify: web/src/components/ChatPanel/PilotTaskCard.tsx
- Modify: web/src/components/ChatPanel/MessageBubble.tsx
- Modify: web/src/components/ChatPanel/model.ts
- Create: web/src/features/pilot/applicationJdVersion.ts
- Create: web/src/features/pilot/applicationJdVersion.test.ts
- Create: web/src/components/ChatPanel/PilotApplicationJdCard.test.tsx
- Modify: tests/test_api_contract.py
- Modify: tests/test_ai_tools.py
- Modify: tests/test_chat_api.py

- [ ] **Step 1: Add failing Pilot contract tests.**

Use three separate contract cases. (a) Opening Pilot without sending a message must create zero Chat rows, make zero Provider calls, and create zero JD rows. (b) A non-JD user message keeps the existing Chat message and Provider behavior, but creates zero JD-domain rows and no downstream-domain rows. (c) An explicit JD intent such as “给这个投递补充 JD” or “查看 JD 历史” may enter the flow; before confirmation it creates no JD version, and one confirmation creates exactly one version. Replaying the same confirmation key creates no second version. When context_type=application does not identify one Application, the Pilot asks the user to choose and does not guess or enumerate-and-write.

The confirmation payload must contain only application_id, raw JD input, normalized source URL, frozen expected_current_version_id, and the original idempotency key. The client cannot send source_kind; the server-side Pilot handler passes source_kind='pilot' to the shared service. Confirmation after a new version appears returns application_jd_stale_current_version and does not write a new version.

- [ ] **Step 2: Implement Pilot using the existing pending-confirmation mechanism.**

The flow is exactly:

~~~text
explicit JD intent -> choose one Application when context is ambiguous -> collect JD/URL
-> pending confirmation card -> user confirms -> shared ApplicationJDService(source_kind='pilot')
-> read-only success/history card
~~~

Do not add a Chat jd_version_id field or a second persistence state machine. Do not trigger Fit, Material, Interview, Knowledge, Offer, Reminder, Application status, or Provider writes after a successful JD save.

- [ ] **Step 3: Run Pilot and API contract tests.**

~~~powershell
uv run pytest tests/test_api_contract.py tests/test_ai_tools.py tests/test_chat_api.py -q
cd web
npm.cmd test -- --run src/features/pilot/applicationJdVersion.test.ts src/components/ChatPanel/PilotApplicationJdCard.test.tsx
cd ..
~~~

Expected result: UI and Pilot use the same save service contract, only explicit confirmation writes a JD version, and the server owns source_kind.

- [ ] **Step 4: Commit the Pilot slice.**

~~~powershell
git add src/offerpilot/ai/tools.py src/offerpilot/ai/agent.py src/offerpilot/api.py web/src/components/ChatPanel/PilotTaskCard.tsx web/src/components/ChatPanel/MessageBubble.tsx web/src/components/ChatPanel/model.ts web/src/features/pilot/applicationJdVersion.ts web/src/features/pilot/applicationJdVersion.test.ts web/src/components/ChatPanel/PilotApplicationJdCard.test.tsx tests/test_api_contract.py tests/test_ai_tools.py tests/test_chat_api.py
git commit -m "feat: AI add Pilot JD confirmation"
~~~

## 7. Isolated smoke and browser evidence

**Files:**
- Create: scripts/application-jd-real-ai-browser-harness.ps1
- Modify: scripts/browser-network-audit.py to record the existing CDP request method, target/session identity, URL, status, and stable response code needed by this harness; do not add domain logic.
- Modify: src/offerpilot/smoke.py only for the isolated JD smoke flow and cleanup assertions.
- Create: tests/test_application_jd_browser_harness.py
- Create: tests/test_application_jd_smoke.py

- [ ] **Step 1: Add fake-audit tests before harness implementation.**

The harness must run two separately reported stages for one synthetic Application. It must fail unless the JD-only stage observes these exact requests and response facts:

~~~text
UI: POST JD v1 -> GET current -> GET history/detail
UI: POST JD v2 with expected_current_version_id=v1
Pilot: POST /api/chat with explicit JD intent -> POST /api/chat/confirm -> confirmation response contains created JD v3 with source_kind=pilot
Pilot: history reads v3; the browser does not call the UI JD-save endpoint and no downstream write occurs automatically
~~~

The `all` harness first creates one synthetic Application, one Resume, and one valid interview Event in the isolated database; those setup rows are included in the pre-Stage-A baseline and are never counted as Stage-A writes. The JD-only stage permits only the expected `application_jd_versions` rows plus the Chat conversation/message rows caused by the explicit Pilot dialogue. It must assert zero writes to `jd_analyses`, Resume Match, Material, Fit, Interview, Mock, Knowledge, Offer, Reminder, Question, Memory, and Application status. Browser network auditing allows only local page/API URLs; server egress allows only configured Provider endpoints and rejects `source_url`, recruiting domains, and unapproved targets.

After Stage A succeeds, take a new baseline and run Stage B automatically for all three consumers in this fixed order: `triage`, `material-kit`, `interview-preparation`. The publication harness runs this entire sequence in one `-Stage all` process, so the same in-memory isolation context carries `application_id`, `resume_id`, `event_id`, and v3 between stages; there is no cross-process ID handoff. A test-only `-Stage jd-only` mode may stop after Stage A, but publication never invokes separate consumer processes. Never combine an expected downstream write with the JD-only zero-write assertion.

For `triage`, the request sequence is `POST /api/applications/{id}/opportunity-fit-reviews` with `schema_version=2` and `jd_version_id=v3`, then `POST /api/applications/{id}/opportunity-fit-reviews/{review_id}/triage/{stage_id}/confirm`; the only permitted domain deltas are `opportunity_fit_review_sessions` and `opportunity_fit_review_stages`, and both rows must carry v3. For `material-kit`, the sequence is `POST /api/applications/{id}/material-kit/generate` with `jd_version_id=v3`, followed by readback; the only permitted domain delta is `application_material_kits`, and the created Kit must carry v3. For `interview-preparation`, the sequence is `POST /api/applications/{id}/interview-preparation-proposals` with `jd_version_id=v3`, followed by readback; the only permitted domain delta is `interview_preparation_proposals`, and the created proposal must carry v3. After each consumer readback, cleanup deletes only that run's generated consumer rows in dependency order, then asserts the consumer table and all unrelated tables match the post-Stage-A baseline except for the retained JD v1/v2/v3 history. A consumer run fails on any unrelated write, source URL egress, missing v3 identity, or cleanup residue. Each Stage A/B run records its own baseline, allowed-write set, observed deltas, request sequence, and cleanup result. Any explicit Mock or Deep/Material child-flow evidence uses the same per-consumer baseline discipline.

- [ ] **Step 2: Implement isolated API smoke and cleanup.**

Run local and real-AI smoke in a temporary data directory. Copy the existing config.json silently; never print or persist the secret in logs/reports. Use Chinese synthetic values and a non-routable example URL. Assert the URL produces zero network events. The single `-Stage all` harness owns one temporary service, browser target, Provider proxy, Application, Resume, Event, and in-memory ID context. Its `finally` path must stop the service/proxy/auditor, close the browser target, delete consumer rows then the synthetic Event/Resume/Application/JD rows in dependency order, remove the temporary data/config directory, and assert the process list and database have no residue. The same cleanup assertions run after every failure and after successful completion.

- [ ] **Step 3: Implement the browser harness.**

Use the established browser-level CDP target binding and network audit. Add strict parameters `-Stage all` and test-only `-Stage jd-only`; the `all` path must create one temporary Application, one synthetic Resume, and one valid Interview Event before Stage A, retain their IDs in an in-memory isolation context, and execute Stage A followed by all three Stage B consumers before its single `finally` cleanup. The harness must not treat API smoke as browser proof, must bind the same target used for all UI/Pilot actions, and must report only stage, consumer, HTTP status, stable error code, and hashed Provider request ID. For Pilot, audit `/api/chat` and `/api/chat/confirm`; the created JD v3 is asserted from the confirmation response and database/readback, not from a client call to the UI JD-save endpoint. It must not log JD text, resume content, configuration, secret, or model output.

- [ ] **Step 4: Run isolated tests and smoke.**

~~~powershell
uv run pytest tests/test_application_jd_smoke.py tests/test_application_jd_browser_harness.py -q
uv run oc smoke --static-dir web/dist
uv run oc verify --profile local --static-dir web/dist
~~~

Expected result: local smoke passes with zero external URL access and zero cross-domain writes. Real AI is run only after the deterministic suite passes:

~~~powershell
uv run oc verify --profile real-ai --static-dir web/dist
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\application-jd-real-ai-browser-harness.ps1 -Stage all
~~~

A Provider failure is reported as a failure category; it is never converted into a pass by weakening the JD contract.

- [ ] **Step 5: Commit the isolated verification slice.**

~~~powershell
git add scripts/application-jd-real-ai-browser-harness.ps1 scripts/browser-network-audit.py src/offerpilot/smoke.py tests/test_application_jd_browser_harness.py tests/test_application_jd_smoke.py
git commit -m "test: AI verify application JD isolation"
~~~

## 8. Full gate, independent review, and release report

**Files:**
- Create/modify: docs/reports/2026-08-05-application-jd-versions-release-verification.md
- No further product changes after this task unless a gate finds a reproducible defect.

- [ ] **Step 1: Run the affected backend suites.**

~~~powershell
uv run pytest tests/test_application_jd_versions_migrations.py tests/test_application_jd_versions_repository.py tests/test_application_jd_versions_api.py tests/test_jd_resume_ai_api.py tests/test_material_kits_api.py tests/test_material_revision_proposals_api.py tests/test_opportunity_fit_reviews_api.py tests/test_interview_preparation_api.py tests/test_mock_interview_api.py -q
uv run pytest tests/test_applications_repository.py tests/test_api_contract.py tests/test_ai_tools.py tests/test_chat_api.py tests/test_cli.py tests/test_opportunity_fit_reviews_repository.py tests/test_material_revision_proposals_repository.py tests/test_interview_preparation_repository.py tests/test_mock_interview_repository.py -q
uv run ruff check .
uv run mypy src
~~~

Expected result: exit code 0 with no new warnings that hide failures.

- [ ] **Step 2: Run the complete backend grouped gate.**

Run the existing Windows five-group gate from the repository root. The aggregate must use the current complete manifest and reject duplicate node IDs, missing tests, stale markers, non-zero group exits, and anything beyond the four approved Windows symlink permission skips. Record collection counts, group counts, exit codes, skip node IDs/reasons, and aggregate result.

~~~powershell
.\scripts\windows-pytest-groups.ps1
~~~

- [ ] **Step 3: Run the complete frontend gate and build.**

~~~powershell
cd web
npm.cmd test -- --run
npm.cmd run build
cd ..
~~~

Run scripts/windows-vitest-groups.ps1 as the grouped frontend gate and verify its manifest/source fingerprint against the current web/src, configuration, lockfile, and gate script before aggregating.

- [ ] **Step 4: Run final local and real-AI verification.**

~~~powershell
uv run oc smoke --static-dir web/dist
uv run oc verify --profile local --static-dir web/dist
uv run oc verify --profile real-ai --static-dir web/dist
~~~

Do not claim real-AI success for a timeout, 4xx/5xx, contract failure, or missing audit evidence. All temporary data, Provider proxy, service processes, CDP targets, and copied configuration must be removed in finally blocks; source data must match its pre-run snapshot byte-for-byte.

- [ ] **Step 5: Perform independent code review.**

Use the repository's `requesting-code-review` workflow to start an independent subagent review after implementation and before the release-report commit. Give the reviewer the final diff, the recorded baseline, the exact allowlist below, and all gate outputs. Do not mark review complete until the subagent returns; resolve every P0/P1 and either fix or explicitly record any accepted P2.

- [ ] **Step 6: Machine-check the final diff and commit the release report.**

Update the report only after all tests, both browser stages, and the independent review finish. The report must include commands, exit codes, test counts, migration result, CAS/idempotency evidence, per-stage browser request sequence, allowed-write deltas, zero-network result, cleanup result, and remaining Provider risk. It must not include secrets, JD/resume text, model output, or raw request IDs. Because docs/* is ignored, stage it explicitly. Before staging, run this from the repository root; it must compare the actual changed set, not just print it:

~~~powershell
$baselineFile = Join-Path $env:TEMP 'offerpilot-application-jd-versions-baseline.txt'
$implementationBase = (Get-Content -LiteralPath $baselineFile -Raw).Trim()
git cat-file -e "$implementationBase^{commit}"
if ($LASTEXITCODE -ne 0) { throw 'Recorded implementation baseline is invalid' }
git add -f docs/reports/2026-08-05-application-jd-versions-release-verification.md

$allowlistFile = Join-Path $env:TEMP 'offerpilot-application-jd-versions-allowlist.txt'
$allowedExact = @(Get-Content -LiteralPath $allowlistFile | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$changed = @(
  (git diff --name-only "$implementationBase..HEAD")
  (git diff --cached --name-only)
  (git diff --name-only)
) | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Sort-Object -Unique
$unexpected = @($changed | Where-Object { $allowedExact -notcontains $_ })
if ($unexpected.Count -gt 0) { throw "Unexpected changed paths: $($unexpected -join ', ')" }

git diff --check "$implementationBase..HEAD"
$diffCheckExit = $LASTEXITCODE
if ($diffCheckExit -ne 0) { throw 'Final diff check failed' }
git diff --check --cached
$stagedDiffCheckExit = $LASTEXITCODE
if ($stagedDiffCheckExit -ne 0) { throw 'Release report diff check failed' }
git commit -m "docs: AI record application JD release verification"
if ($LASTEXITCODE -ne 0) { throw 'Release report commit failed' }
~~~

After the report commit, repeat the same allowlist comparison and save the diff-check exit code before any status command. Delete the baseline only if that final comparison, `git diff --check`, and clean status all succeed; otherwise retain it for repair and rerun:

~~~powershell
$baselineFile = Join-Path $env:TEMP 'offerpilot-application-jd-versions-baseline.txt'
$allowlistFile = Join-Path $env:TEMP 'offerpilot-application-jd-versions-allowlist.txt'
$implementationBase = (Get-Content -LiteralPath $baselineFile -Raw).Trim()
git cat-file -e "$implementationBase^{commit}"
if ($LASTEXITCODE -ne 0) { throw 'Recorded implementation baseline is invalid' }
$allowedExact = @(Get-Content -LiteralPath $allowlistFile | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$changed = @(git diff --name-only "$implementationBase..HEAD" | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Sort-Object -Unique)
$unexpected = @($changed | Where-Object { $allowedExact -notcontains $_ })
if ($unexpected.Count -gt 0) { throw "Unexpected changed paths after report commit: $($unexpected -join ', ')" }
git diff --check "$implementationBase..HEAD"
$diffCheckExit = $LASTEXITCODE
$status = @(git status --short)
if ($diffCheckExit -ne 0) { throw 'Final diff check failed' }
if ($status.Count -ne 0) { throw 'Final worktree is dirty' }
Remove-Item -LiteralPath $baselineFile -Force
Remove-Item -LiteralPath $allowlistFile -Force
~~~

No push or merge is part of this plan.

## Self-review checklist

- [ ] Migration task covers 0018_application_jd_versions, all explicit jd_version_id columns, ordinary-integer deletion semantics, and real prior-DDL upgrade.
- [ ] Repository/API tasks cover ^[A-Za-z0-9_-]{16,128}$, strict positive integer/null CAS input, replay-before-CAS ordering, normalized fingerprint, URL validation, 240-code-point preview, and no 202 save state.
- [ ] Domain task covers strict requested-version validation, short-session freeze/close/CAS lifecycle, Triage/Deep inheritance, Material Kit/Proposal inheritance, Interview Preparation, Mock claim freezing, v1 write closure, CLI/standalone compatibility, and Provider barriers.
- [ ] UI/Pilot tasks cover AppShell-owned remount-safe drafts, confirmed writes only, server-owned source_kind, stale/unknown recovery, no link navigation, and the three distinct Pilot write-boundary cases.
- [ ] Browser/gate tasks cover separate JD-only and explicit-consumer stages, isolated config, zero URL egress, complete request sequence, cleanup, grouped backend/frontend gates, machine allowlist comparison, independent review, and report commit.
- [ ] No unresolved placeholder or unspecified implementation step remains in this plan.
