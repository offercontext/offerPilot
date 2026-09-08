# Application Evidence Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user explicitly confirm an internal Material Kit submission and preserve an immutable, locally stored evidence bundle that later product capabilities can safely read.

**Architecture:** Add an append-only `application_evidence_bundles` table and a repository that canonicalizes current Application, JD, Resume, and Material Kit content inside one SQLite transaction. The API supplies a server-generated preview and confirms only the previewed hash; the Material Kit UI uses that contract to replace its mutable `submitted` state with a clear confirmation flow and read-only history.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, SQLite, Pydantic, pytest, React 18, TypeScript, TanStack Query, Ant Design, Vitest.

---

## File map

| File | Responsibility |
| --- | --- |
| `src/offerpilot/models.py` | ORM model, DB constraints, Application FK inventory. |
| `src/offerpilot/db.py` | Record the idempotent local-schema migration. |
| `src/offerpilot/application_status.py` | Share the first-status timestamp helper between existing updates and submission confirmation. |
| `src/offerpilot/repositories/evidence_bundles.py` | Canonical JSON/hash construction, source validation, immutable read operations, atomic confirmation. |
| `src/offerpilot/schemas.py` | API response models for preview, summaries and details. |
| `src/offerpilot/api.py` | Nested preview/read/confirm endpoints and error mapping. |
| `tests/test_evidence_bundles_repository.py` | Storage, immutability, lifecycle and transaction regression tests. |
| `tests/test_evidence_bundles_api.py` | HTTP contract tests including conflict and idempotency behavior. |
| `tests/test_conditional_delete_repositories.py` | Keep the direct Application-FK inventory exhaustive. |
| `web/src/types/evidenceBundle.ts` | Frontend API payload and view types. |
| `web/src/services/evidenceBundles.ts` | API client functions for preview, confirmation and history. |
| `web/src/services/evidenceBundles.test.ts` | URL, request-body and parsing tests for the new service. |
| `web/src/types/materialKit.ts` | Keep legacy server values representable but restrict new editing to `draft` and `ready`. |
| `web/src/components/MaterialKitDrawer.tsx` | Preview, confirm modal, legacy warning and read-only evidence history. |
| `web/src/components/MaterialKitDrawer.evidenceBundles.test.tsx` | Focused UI behavior tests using existing Vitest mock conventions. |
| `web/src/components/MaterialKitDrawer.module.css` | Styles for the confirmation source list and evidence history. |

## Task 1: Lock down the domain contract with failing repository tests

**Files:**

- Create: `tests/test_evidence_bundles_repository.py`
- Modify: `tests/test_conditional_delete_repositories.py`
- Test: `tests/test_evidence_bundles_repository.py`

- [ ] **Step 1: Add fixtures and a failing immutable-snapshot test.**

  Create a real Application, Resume and ApplicationMaterialKit through `init_database`; build an evidence preview, then confirm it. The test must update the source rows after confirmation and assert the persisted bundle still contains the original content and hashes.

  ```python
  def test_confirm_copies_internal_sources_and_preserves_them_after_edits(tmp_path):
      factory = init_database(tmp_path / "data.db")
      application = ApplicationsRepository(factory).create(
          ApplicationCreate(company_name="Acme", position_name="Backend", status="pending")
      )
      resume = ResumesRepository(factory).create(
          ResumeCreate(title="Backend CV", content_json='{"experience":["Go"]}')
      )
      kit = MaterialKitsRepository(factory).create(
          MaterialKitCreate(
              application_id=application.id,
              resume_id=resume.id,
              jd_snapshot="Build Go services",
              content_json='{"messages":[{"body":"Hello"}]}',
          )
      )
      repo = EvidenceBundlesRepository(factory)
      preview = repo.preview(application.id)
      bundle, created = repo.confirm(
          application.id,
          submitted_at=datetime(2026, 7, 14, 9, tzinfo=timezone.utc),
          idempotency_key="87a596a7-3ac2-4f7e-a557-3d18e3d9d554",
          expected_bundle_sha256=preview.bundle_sha256,
      )
      assert created is True
      assert bundle.sequence == 1
      assert bundle.confirmation_kind == "user_asserted"

      MaterialKitsRepository(factory).update(
          kit.id,
          MaterialKitCreate(
              application_id=application.id,
              resume_id=resume.id,
              jd_snapshot="Changed JD",
              content_json='{"messages":[{"body":"Changed"}]}',
          ),
      )
      detail = repo.get(application.id, bundle.id)
      assert detail is not None
      assert json.loads(detail.snapshot_json)["jd"]["text"] == "Build Go services"
      assert json.loads(detail.snapshot_json)["material_kit"]["content_json"]["messages"][0]["body"] == "Hello"
  ```

- [ ] **Step 2: Add failing tests for the remaining non-negotiable cases.**

  Add these named tests in the same file, with exact assertions:

  ```python
  def test_preview_reports_missing_or_invalid_internal_sources(tmp_path):
      repo = EvidenceBundlesRepository(init_database(tmp_path / "data.db"))
      with pytest.raises(EvidenceBundleNotFound):
          repo.preview(999)


  def test_confirm_rejects_a_stale_preview_and_replays_the_same_idempotency_key(tmp_path):
      assert "提交材料已变化，请重新核对" == str(conflict)
      assert retry.id == original.id
      assert retry_created is False


  def test_confirm_advances_pending_only_and_writes_a_submission_event(tmp_path):
      assert confirmed_application.status == "applied"
      assert confirmed_application.first_applied_at == submitted_at
      assert event.event_type == "custom"
      assert event.subtype == "submission_confirmed"
      assert f"bundle:{bundle.id}" in event.tags


  def test_confirm_keeps_later_and_closed_application_statuses(tmp_path):
      assert confirmed_interview.status == "interview"
      assert confirmed_closed.status == "closed"


  def test_confirm_assigns_monotonic_sequences_for_explicit_resubmissions(tmp_path):
      assert [bundle.sequence for bundle in repo.list(application.id)] == [2, 1]
  ```

  Replace the `raise AssertionError` line in the first test with a `pytest.raises(EvidenceBundleNotFound)` assertion after the repository API is introduced. Do not implement the repository before this red test exists.

- [ ] **Step 3: Extend the conditional-delete inventory test before adding the model.**

  Import `ApplicationEvidenceBundle` and add it to `_application_dependency` with a complete valid row:

  ```python
  if model is ApplicationEvidenceBundle:
      return model(
          application_id=application_id,
          sequence=1,
          submitted_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
          confirmed_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
          confirmation_kind="user_asserted",
          idempotency_key="87a596a7-3ac2-4f7e-a557-3d18e3d9d554",
          snapshot_json="{}",
          bundle_sha256="0" * 64,
      )
  ```

- [ ] **Step 4: Run the repository tests and confirm they fail for missing production symbols.**

  Run:

  ```powershell
  uv run pytest tests/test_evidence_bundles_repository.py tests/test_conditional_delete_repositories.py -q
  ```

  Expected: collection fails because `ApplicationEvidenceBundle` and `EvidenceBundlesRepository` do not yet exist.

## Task 2: Add append-only persistence and atomic confirmation

**Files:**

- Create: `src/offerpilot/repositories/evidence_bundles.py`
- Modify: `src/offerpilot/models.py:4-5, 436-448`
- Modify: `src/offerpilot/db.py:20-61`
- Modify: `src/offerpilot/application_status.py`
- Modify: `src/offerpilot/repositories/applications.py:175-185`
- Modify: `tests/test_conditional_delete_repositories.py`
- Test: `tests/test_evidence_bundles_repository.py`

- [ ] **Step 1: Add the ORM model, uniqueness constraints and FK inventory.**

  Extend the SQLAlchemy imports with `Integer` and `UniqueConstraint`, then add the following model immediately after `ApplicationMaterialKit`:

  ```python
  class ApplicationEvidenceBundle(Base):
      __tablename__ = "application_evidence_bundles"
      __table_args__ = (
          UniqueConstraint("application_id", "sequence", name="uq_evidence_bundle_sequence"),
          UniqueConstraint("application_id", "idempotency_key", name="uq_evidence_bundle_idempotency"),
          Index("idx_evidence_bundles_application", "application_id"),
      )

      id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
      application_id: Mapped[int] = mapped_column(
          ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
      )
      sequence: Mapped[int] = mapped_column(Integer, nullable=False)
      submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
      confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
      confirmation_kind: Mapped[str] = mapped_column(
          String, nullable=False, default="user_asserted", server_default="user_asserted"
      )
      idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
      snapshot_json: Mapped[str] = mapped_column(String, nullable=False)
      bundle_sha256: Mapped[str] = mapped_column(String, nullable=False)
      created_at: Mapped[datetime] = mapped_column(
          DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
      )
  ```

  Add this class to `APPLICATION_FOREIGN_KEY_MODELS`. Preserve the existing soft-delete behavior; a hidden Application must make its bundles unavailable through repository reads.

- [ ] **Step 2: Record the local schema migration.**

  After the application lifecycle migration block in `init_database`, add:

  ```python
  _record_migration(
      engine,
      "0006_application_evidence_bundles",
      "Add immutable application evidence bundles",
  )
  ```

  `Base.metadata.create_all(engine)` already creates the new SQLite table for both new and existing local databases. Do not reset existing Application, Material Kit, or event tables and do not synthesize bundles from legacy `submitted` rows.

- [ ] **Step 3: Extract the reusable status timestamp helper.**

  In `src/offerpilot/application_status.py`, import `datetime` and define a helper that keeps the first transition timestamp unchanged:

  ```python
  FIRST_STATUS_TIMESTAMP_ATTR = {
      "pending": "first_pending_at",
      "applied": "first_applied_at",
      "written_test": "first_written_test_at",
      "interview": "first_interview_at",
      "offer": "first_offer_at",
      "closed": "closed_at",
  }


  def mark_first_status_timestamp(application: object, status: str, occurred_at: datetime) -> None:
      attr = FIRST_STATUS_TIMESTAMP_ATTR[status]
      if getattr(application, attr) is None:
          setattr(application, attr, occurred_at)
  ```

  Import this helper in `repositories/applications.py`, replace its private `_mark_first_status_timestamp` calls, then remove that private function. This preserves existing application-update behavior while letting the evidence repository use `submitted_at` for a historical pending-to-applied transition.

- [ ] **Step 4: Implement canonical snapshot construction and the repository.**

  Create `src/offerpilot/repositories/evidence_bundles.py`. Keep hashing, validation and persistence in this focused module; do not let the API or React app produce canonical hashes.

  ```python
  import json
  from dataclasses import dataclass
  from datetime import datetime, timezone
  from hashlib import sha256
  from typing import Any

  from sqlalchemy import func, select
  from sqlalchemy.exc import IntegrityError
  from sqlalchemy.orm import Session, sessionmaker

  from offerpilot.application_status import mark_first_status_timestamp
  from offerpilot.models import (
      Application,
      ApplicationEvidenceBundle,
      ApplicationEvent,
      ApplicationMaterialKit,
      Resume,
  )

  def canonical_json(value: object) -> str:
      return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


  def sha256_text(value: str) -> str:
      return sha256(value.encode("utf-8")).hexdigest()


  def parse_json_object(name: str, value: str) -> dict[str, object]:
      try:
          parsed = json.loads(value)
      except json.JSONDecodeError as exc:
          raise EvidenceBundleValidationError(f"{name} content_json must be a JSON object") from exc
      if not isinstance(parsed, dict):
          raise EvidenceBundleValidationError(f"{name} content_json must be a JSON object")
      return parsed
  ```

  Give the module this explicit public contract before implementing the methods:

  ```python
  class EvidenceBundleNotFound(Exception):
      pass


  class EvidenceBundleValidationError(ValueError):
      pass


  class EvidenceBundleConflictError(ValueError):
      pass


  @dataclass(frozen=True)
  class EvidenceBundlePreview:
      application_id: int
      ready: bool
      issues: list[str]
      bundle_sha256: str | None
      snapshot: dict[str, Any] | None


  class EvidenceBundlesRepository:
      def __init__(self, session_factory: sessionmaker[Session]):
          self._session_factory = session_factory
  ```

  Implement these public methods with the signatures and return values stated here: `preview(self, application_id: int) -> EvidenceBundlePreview`; `confirm(self, application_id: int, submitted_at: datetime, idempotency_key: str, expected_bundle_sha256: str) -> tuple[ApplicationEvidenceBundle, bool]`; `list(self, application_id: int) -> list[ApplicationEvidenceBundle]`; and `get(self, application_id: int, bundle_id: int) -> ApplicationEvidenceBundle | None`.

  `preview(application_id)` must join/inspect a non-deleted Application, its unique Material Kit and its non-deleted Resume. It returns `ready=False` with concrete Chinese issues for missing Material Kit, Resume, JD or valid JSON; a missing/hidden Application raises `EvidenceBundleNotFound`. For a ready preview, construct exactly this content:

  ```python
  snapshot = {
      "schema_version": 1,
      "application": {
          "id": application.id,
          "company_name": application.company_name,
          "position_name": application.position_name,
          "job_url": application.job_url,
          "source": application.source,
      },
      "jd": {
          "text": kit.jd_snapshot,
          "sha256": sha256_text(kit.jd_snapshot),
          "jd_analysis_id": kit.jd_analysis_id,
      },
      "resume": {
          "resume_id": resume.id,
          "title": resume.title or resume.name,
          "content_json": resume_content,
          "sha256": sha256_text(canonical_json(resume_content)),
      },
      "material_kit": {
          "material_kit_id": kit.id,
          "content_json": material_kit_content,
          "sha256": sha256_text(canonical_json(material_kit_content)),
      },
  }
  bundle_sha256 = sha256_text(canonical_json(snapshot))
  ```

  `confirm` must re-read this preview inside one session transaction, compare `expected_bundle_sha256`, and create the evidence bundle before it creates the event so the event tag can use `bundle:<id>`. If the current status is `pending`, set it to `applied`, set the first applied timestamp with `submitted_at`, and update `updated_at`; never regress a later or closed status. Create `ApplicationEvent` directly in the same session with `event_type="custom"`, `subtype="submission_confirmed"`, `scheduled_at=submitted_at`, `duration_minutes=0`, `status="done"`, tags `['submission_evidence', f'bundle:{bundle.id}']`, and a concise Chinese note.

  Look up the visible Application before idempotency replay. On an existing `(application_id, idempotency_key)`, return the original bundle with `created=False`; otherwise compute `sequence` with `max(sequence) + 1`, flush the bundle, add the event, and commit once. Use a unique-constraint `IntegrityError` retry lookup only for concurrent duplicate idempotency keys.

- [ ] **Step 5: Run the targeted backend tests and make them green.**

  Run:

  ```powershell
  uv run pytest tests/test_evidence_bundles_repository.py tests/test_conditional_delete_repositories.py -q
  ```

  Expected: all selected tests pass, including the direct-FK inventory and post-confirmation immutability checks.

- [ ] **Step 6: Commit the persistence slice.**

  ```powershell
  git add src/offerpilot/models.py src/offerpilot/db.py src/offerpilot/application_status.py src/offerpilot/repositories/applications.py src/offerpilot/repositories/evidence_bundles.py tests/test_evidence_bundles_repository.py tests/test_conditional_delete_repositories.py
  git commit -m "feat: AI add application evidence persistence"
  ```

## Task 3: Expose the preview and immutable-read API

**Files:**

- Create: `tests/test_evidence_bundles_api.py`
- Modify: `src/offerpilot/schemas.py:213-230`
- Modify: `src/offerpilot/api.py:1-82, 162-180, 313-412, 4511-4655`
- Test: `tests/test_evidence_bundles_api.py`

- [ ] **Step 1: Write failing endpoint-contract tests.**

  Add tests with this behavior matrix:

  ```python
  def test_preview_then_confirm_returns_201_and_immutable_detail(client_with_ready_kit):
      preview = client_with_ready_kit.get("/api/applications/1/evidence-bundles/preview")
      assert preview.status_code == 200
      assert preview.json()["ready"] is True
      response = client_with_ready_kit.post(
          "/api/applications/1/evidence-bundles",
          json={
              "submitted_at": "2026-07-14T09:00:00Z",
              "idempotency_key": "87a596a7-3ac2-4f7e-a557-3d18e3d9d554",
              "expected_bundle_sha256": preview.json()["bundle_sha256"],
          },
      )
      assert response.status_code == 201
      detail = client_with_ready_kit.get(
          f"/api/applications/1/evidence-bundles/{response.json()['id']}"
      )
      assert detail.status_code == 200
      assert detail.json()["confirmation_kind"] == "user_asserted"
  ```

  Cover `200` idempotent replay, `409` stale preview, `422` invalid UUID/future timestamp/missing source, `404` wrong Application or nested bundle, descending list order, and explicit assertions that `PUT` and `DELETE` return `405`.

- [ ] **Step 2: Run the endpoint tests and confirm they fail because routes are absent.**

  Run:

  ```powershell
  uv run pytest tests/test_evidence_bundles_api.py -q
  ```

  Expected: route assertions fail with `404` before API implementation.

- [ ] **Step 3: Add response schemas and route serializers.**

  Add `EvidenceBundlePreviewOut`, `ApplicationEvidenceBundleSummaryOut`, and `ApplicationEvidenceBundleOut` to `schemas.py`. Use `dict[str, Any]` for `snapshot`, so the persisted, versioned JSON shape can evolve without introducing a second mutable model. Summary responses omit `snapshot`; details include it.

  ```python
  class ApplicationEvidenceBundleSummaryOut(BaseModel):
      model_config = ConfigDict(from_attributes=True)

      id: int
      application_id: int
      sequence: int
      submitted_at: datetime
      confirmed_at: datetime
      confirmation_kind: str
      bundle_sha256: str
      created_at: datetime


  class ApplicationEvidenceBundleOut(ApplicationEvidenceBundleSummaryOut):
      snapshot: dict[str, Any]


  class EvidenceBundlePreviewOut(BaseModel):
      application_id: int
      ready: bool
      issues: list[str]
      bundle_sha256: str | None = None
      sources: dict[str, Any]
  ```

  Implement API helpers with these stable payload shapes:

  ```python
  def _evidence_bundle_summary_json(bundle: ApplicationEvidenceBundle) -> dict[str, Any]:
      return ApplicationEvidenceBundleSummaryOut.model_validate(bundle).model_dump(mode="json")


  def _evidence_bundle_json(bundle: ApplicationEvidenceBundle) -> dict[str, Any]:
      payload = _evidence_bundle_summary_json(bundle)
      payload["snapshot"] = json.loads(bundle.snapshot_json)
      return payload


  def _evidence_preview_json(preview: EvidenceBundlePreview) -> dict[str, Any]:
      if not preview.ready or preview.snapshot is None:
          return EvidenceBundlePreviewOut(
              application_id=preview.application_id,
              ready=False,
              issues=preview.issues,
              sources={},
          ).model_dump(mode="json")
      snapshot = preview.snapshot
      return EvidenceBundlePreviewOut(
          application_id=preview.application_id,
          ready=True,
          issues=[],
          bundle_sha256=preview.bundle_sha256,
          sources={
              "application": snapshot["application"],
              "jd": {"sha256": snapshot["jd"]["sha256"], "characters": len(snapshot["jd"]["text"])},
              "resume": {"id": snapshot["resume"]["resume_id"], "title": snapshot["resume"]["title"], "sha256": snapshot["resume"]["sha256"]},
              "material_kit": {"id": snapshot["material_kit"]["material_kit_id"], "sha256": snapshot["material_kit"]["sha256"]},
          },
      ).model_dump(mode="json")
  ```

  The preview response must expose only source summaries, IDs, titles, character counts, hashes, readiness and issues; it must not return the full Resume or Material Kit JSON a second time.

- [ ] **Step 4: Add routes in safe declaration order.**

  Instantiate `EvidenceBundlesRepository(session_factory)` with the other repositories. Declare `preview`, `POST`, and list routes before the dynamic `{bundle_id}` detail route:

  ```python
  def _required_text(payload: dict[str, Any], name: str) -> str:
      value = payload.get(name)
      if not isinstance(value, str) or not value.strip():
          raise EvidenceBundleValidationError(f"{name} is required")
      return value.strip()


  def _parse_uuid(value: Any, name: str) -> UUID:
      try:
          return UUID(_required_text({name: value}, name))
      except ValueError as exc:
          raise EvidenceBundleValidationError(f"{name} must be a UUID") from exc


  def _parse_submission_time(value: Any) -> datetime:
      now = datetime.now(timezone.utc)
      if value in (None, ""):
          return now
      if not isinstance(value, str):
          raise EvidenceBundleValidationError("submitted_at must be an RFC3339 timestamp")
      try:
          parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
      except ValueError as exc:
          raise EvidenceBundleValidationError("submitted_at must be an RFC3339 timestamp") from exc
      if parsed.tzinfo is None:
          raise EvidenceBundleValidationError("submitted_at must include a timezone")
      parsed = parsed.astimezone(timezone.utc)
      if parsed > now:
          raise EvidenceBundleValidationError("submitted_at cannot be in the future")
      return parsed


  @app.get("/api/applications/{app_id}/evidence-bundles/preview")
  def preview_evidence_bundle(app_id: int) -> JSONResponse:
      return JSONResponse(_evidence_preview_json(evidence_bundles.preview(app_id)))


  @app.post("/api/applications/{app_id}/evidence-bundles")
  def confirm_evidence_bundle(app_id: int, payload: dict[str, Any] = Body()) -> JSONResponse:
      submitted_at = _parse_submission_time(payload.get("submitted_at"))
      idempotency_key = _parse_uuid(payload.get("idempotency_key"), "idempotency_key")
      expected_hash = _required_text(payload, "expected_bundle_sha256")
      bundle, created = evidence_bundles.confirm(
          app_id, submitted_at, str(idempotency_key), expected_hash
      )
      return JSONResponse(_evidence_bundle_json(bundle), status_code=201 if created else 200)


  @app.get("/api/applications/{app_id}/evidence-bundles")
  def list_evidence_bundles(app_id: int) -> JSONResponse:
      return JSONResponse([
          _evidence_bundle_summary_json(bundle)
          for bundle in evidence_bundles.list(app_id)
      ])


  @app.get("/api/applications/{app_id}/evidence-bundles/{bundle_id}")
  def get_evidence_bundle(app_id: int, bundle_id: int) -> JSONResponse:
      bundle = evidence_bundles.get(app_id, bundle_id)
      if bundle is None:
          return error_response(404, "Evidence bundle not found")
      return JSONResponse(_evidence_bundle_json(bundle))
  ```

  Convert repository not-found, validation and conflict exceptions into the exact `404`, `422`, and `409` error shapes specified by the design. Use `datetime.now(timezone.utc)` only when `submitted_at` is omitted; reject future values before calling the repository.

- [ ] **Step 5: Run the API tests and affected regression tests.**

  Run:

  ```powershell
  uv run pytest tests/test_evidence_bundles_api.py tests/test_material_kits_api.py tests/test_applications_api.py -q
  ```

  Expected: all selected tests pass, and old Material Kit routes retain their current behavior.

- [ ] **Step 6: Commit the HTTP slice.**

  ```powershell
  git add src/offerpilot/schemas.py src/offerpilot/api.py tests/test_evidence_bundles_api.py
  git commit -m "feat: AI expose evidence bundle API"
  ```

## Task 4: Add typed frontend API access before changing the UI

**Files:**

- Create: `web/src/types/evidenceBundle.ts`
- Create: `web/src/services/evidenceBundles.ts`
- Create: `web/src/services/evidenceBundles.test.ts`
- Modify: `web/src/types/materialKit.ts:1-59`
- Test: `web/src/services/evidenceBundles.test.ts`

- [ ] **Step 1: Write failing service tests with the existing HTTP mock pattern.**

  Mock `createApiClient` and assert these exact calls:

  ```ts
  expect(getMock).toHaveBeenCalledWith('/applications/7/evidence-bundles/preview');
  expect(postMock).toHaveBeenCalledWith('/applications/7/evidence-bundles', {
    submitted_at: '2026-07-14T09:00:00.000Z',
    idempotency_key: '87a596a7-3ac2-4f7e-a557-3d18e3d9d554',
    expected_bundle_sha256: 'a'.repeat(64),
  });
  expect(getMock).toHaveBeenCalledWith('/applications/7/evidence-bundles');
  expect(getMock).toHaveBeenCalledWith('/applications/7/evidence-bundles/3');
  ```

- [ ] **Step 2: Run the service test and confirm it fails for missing modules.**

  Run:

  ```powershell
  Set-Location web; npm test -- src/services/evidenceBundles.test.ts
  ```

  Expected: Vitest fails to resolve `evidenceBundles` before implementation.

- [ ] **Step 3: Define frontend types and service functions.**

  In `web/src/types/evidenceBundle.ts`, define `EvidenceBundlePreview`, `EvidenceBundleSummary`, `EvidenceBundleDetail`, and `ConfirmEvidenceBundleInput` with the API's snake_case fields. In `web/src/services/evidenceBundles.ts`, export:

  ```ts
  export async function getEvidenceBundlePreview(applicationID: number): Promise<EvidenceBundlePreview> {
    const { data } = await http.get<EvidenceBundlePreview>(
      `/applications/${applicationID}/evidence-bundles/preview`,
    );
    return data;
  }

  export async function confirmEvidenceBundle(
    applicationID: number,
    input: ConfirmEvidenceBundleInput,
  ): Promise<EvidenceBundleDetail> {
    const { data } = await http.post<EvidenceBundleDetail>(
      `/applications/${applicationID}/evidence-bundles`, input,
    );
    return data;
  }

  export async function listEvidenceBundles(applicationID: number): Promise<EvidenceBundleSummary[]> {
    const { data } = await http.get<EvidenceBundleSummary[]>(
      `/applications/${applicationID}/evidence-bundles`,
    );
    return data;
  }

  export async function getEvidenceBundle(
    applicationID: number,
    bundleID: number,
  ): Promise<EvidenceBundleDetail> {
    const { data } = await http.get<EvidenceBundleDetail>(
      `/applications/${applicationID}/evidence-bundles/${bundleID}`,
    );
    return data;
  }
  ```

  Keep `MaterialKitStatus` capable of reading the legacy server value `submitted`, but add `EditableMaterialKitStatus = 'draft' | 'ready'`; new controls must use only the editable type. Make `UpdateMaterialKitInput.status` optional so a legacy value can remain untouched when the user saves unrelated fields:

  ```ts
  export type EditableMaterialKitStatus = 'draft' | 'ready';

  export interface UpdateMaterialKitInput {
    resume_id?: number;
    jd_analysis_id?: number;
    jd_snapshot: string;
    status?: MaterialKitStatus;
    content_json: MaterialKitContent;
  }
  ```

- [ ] **Step 4: Run the frontend service test and TypeScript build.**

  Run:

  ```powershell
  Set-Location web; npm test -- src/services/evidenceBundles.test.ts
  npm run build
  ```

  Expected: the service test and production type-check/build both pass.

- [ ] **Step 5: Commit the typed-client slice.**

  ```powershell
  git add web/src/types/evidenceBundle.ts web/src/services/evidenceBundles.ts web/src/services/evidenceBundles.test.ts web/src/types/materialKit.ts
  git commit -m "feat: AI add evidence bundle web client"
  ```

## Task 5: Replace the mutable submitted control with user confirmation UX

**Files:**

- Modify: `web/src/components/MaterialKitDrawer.tsx:1-531`
- Modify: `web/src/components/MaterialKitDrawer.module.css`
- Create: `web/src/components/MaterialKitDrawer.evidenceBundles.test.tsx`
- Test: `web/src/components/MaterialKitDrawer.evidenceBundles.test.tsx`

- [ ] **Step 1: Write behavior-focused component tests before changing the drawer.**

  Follow the mock pattern in `EvidenceDestinationQueryState.test.tsx`: mock TanStack Query, Ant Design and the evidence-bundle service. Add exact tests that assert:

  ```ts
  expect(view.textContent).toContain('用户确认，非平台回执');
  expect(confirmButton.disabled).toBe(true);
  expect(view.textContent).toContain('缺少已选择的简历');
  expect(view.textContent).toContain('旧投递标记，缺少证据快照');
  expect(view.textContent).not.toContain('状态：已投递');
  expect(confirmMutation).toHaveBeenCalledWith(expect.objectContaining({
    expected_bundle_sha256: 'a'.repeat(64),
  }));
  ```

  Include a conflict test that changes the mocked confirm result to a `409` Axios-style error and asserts that the preview query is refetched with the user-facing message `提交材料已变化，请重新核对`.

- [ ] **Step 2: Run the new UI test and confirm it fails before the UI exists.**

  Run:

  ```powershell
  Set-Location web; npm test -- src/components/MaterialKitDrawer.evidenceBundles.test.tsx
  ```

  Expected: assertions fail because no evidence preview, confirmation button, legacy warning, or history is rendered.

- [ ] **Step 3: Implement the confirmation and history flow in `MaterialKitDrawer`.**

  Add `Modal` to the Ant Design import and use three queries keyed by Application ID:

  ```ts
  const previewQuery = useQuery({
    queryKey: ['application-evidence-bundle-preview', applicationID],
    queryFn: () => getEvidenceBundlePreview(applicationID!),
    enabled: open && Boolean(applicationID),
  });
  const historyQuery = useQuery({
    queryKey: ['application-evidence-bundles', applicationID],
    queryFn: () => listEvidenceBundles(applicationID!),
    enabled: open && Boolean(applicationID),
  });
  ```

  Make the confirmation mutation and legacy-preserving save payload explicit:

  ```ts
  const legacySubmitted = existingKit?.status === 'submitted';
  const confirmMutation = useMutation({
    mutationFn: (input: ConfirmEvidenceBundleInput) =>
      confirmEvidenceBundle(applicationID!, input),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['application-evidence-bundle-preview', applicationID] }),
        queryClient.invalidateQueries({ queryKey: ['application-evidence-bundles', applicationID] }),
        queryClient.invalidateQueries({ queryKey: ['application-events'] }),
        queryClient.invalidateQueries({ queryKey: ['applications'] }),
      ]);
    },
  });

  const savedStatus: MaterialKitStatus | undefined = legacySubmitted ? undefined : status;
  ```

  Change `SaveVariables.status` to `MaterialKitStatus | undefined`, forward `savedStatus` to `updateMaterialKit`, and have that service omit undefined JSON keys before the `PUT` request. The confirm action opens a modal; its primary button is disabled unless `preview.ready` is true. Its body renders the Application, JD, Resume and Material Kit summary/hashes returned by the preview, a `datetime-local` input whose submitted value is converted with `new Date(value).toISOString()`, and the literal label `用户确认，非平台回执`.

  On confirm, generate a fresh `crypto.randomUUID()` only once per modal opening, call `confirmEvidenceBundle`, and invalidate `application-evidence-bundle-preview`, `application-evidence-bundles`, `application-events`, and `applications` queries on success. Preserve the modal after a `409`, refetch the preview, and require a fresh user click. Display history as read-only sequence/time/hash rows; open detail only in a read-only panel or modal, never an editing form.

  Remove `submitted` from `STATUS_OPTIONS`. For a legacy `existingKit.status === 'submitted'`, show the exact warning from the design and omit `status` from save payloads so saving unrelated fields does not silently erase the legacy marker. New Material Kits can save only `draft` or `ready`.

- [ ] **Step 4: Add narrow, responsive styles without changing the global visual system.**

  Add scoped classes for the source summary, legacy warning and evidence history. Reuse `--op-surface`, `--op-border`, `--op-muted`, and existing eight-pixel radii; extend the existing `860px` and `560px` media queries so confirmation actions remain full-width on narrow viewports.

- [ ] **Step 5: Run the UI test, the material-kit-adjacent tests and the web build.**

  Run:

  ```powershell
  Set-Location web; npm test -- src/components/MaterialKitDrawer.evidenceBundles.test.tsx src/services/evidenceBundles.test.ts src/layout/workspaceDrilldown.test.ts
  npm run build
  ```

  Expected: focused tests pass and TypeScript reports no invalid Material Kit status use.

- [ ] **Step 6: Commit the user-facing slice.**

  ```powershell
  git add web/src/components/MaterialKitDrawer.tsx web/src/components/MaterialKitDrawer.module.css web/src/components/MaterialKitDrawer.evidenceBundles.test.tsx
  git commit -m "feat: AI confirm application evidence bundles"
  ```

## Task 6: Run the release-quality verification and review loop

**Files:**

- Modify only if verification identifies a concrete defect.
- Test: backend, frontend, static smoke and review findings.

- [ ] **Step 1: Run the required local verification matrix.**

  Run from the worktree root:

  ```powershell
  uv run pytest
  uv run ruff check .
  uv run mypy src
  Set-Location web; npm test -- --run
  npm run build
  Set-Location ..; uv run oc smoke --static-dir web/dist
  ```

  Expected: every command exits `0`. If Docker is unavailable, record the exact smoke command failure and do not claim the smoke passed.

- [ ] **Step 2: Perform focused manual browser acceptance.**

  Use the in-app browser against a local app instance and verify: a ready material kit previews sources, confirmation produces one history entry, a source edit causes a conflict/re-preview path, a legacy `submitted` kit shows the warning, and no screen calls it platform-verified. Inspect the browser network log during preview and confirmation: requests must stay under OfferPilot's local `/api` origin; this feature must not call a model, upload a file, contact a recruitment platform, or introduce data egress（不新增数据出境）.

- [ ] **Step 3: Request an independent code review.**

  Ask a fresh reviewer to inspect the final diff against `docs/superpowers/specs/2026-07-14-application-evidence-bundle-design.md`, concentrating on transaction atomicity, hash canonicalization, visibility after soft deletion, event semantics, idempotency races, and UI claims. Fix all Critical and Important findings, rerun the affected tests, then rerun Step 1.

- [ ] **Step 4: Commit any review fixes separately.**

  ```powershell
  git add src/offerpilot/models.py src/offerpilot/db.py src/offerpilot/application_status.py src/offerpilot/repositories/applications.py src/offerpilot/repositories/evidence_bundles.py src/offerpilot/schemas.py src/offerpilot/api.py tests/test_evidence_bundles_repository.py tests/test_evidence_bundles_api.py tests/test_conditional_delete_repositories.py web/src/types/evidenceBundle.ts web/src/services/evidenceBundles.ts web/src/services/evidenceBundles.test.ts web/src/types/materialKit.ts web/src/components/MaterialKitDrawer.tsx web/src/components/MaterialKitDrawer.module.css web/src/components/MaterialKitDrawer.evidenceBundles.test.tsx
  git commit -m "fix: AI address evidence bundle review"
  ```

  If review finds no issues, make no empty commit.

## Plan self-review

- Spec coverage: Tasks 1-2 implement immutable internal snapshots, hashes, idempotency, lifecycle and `application_events`; Task 3 defines the complete HTTP surface; Tasks 4-5 implement the internal-only confirmation UI and legacy status handling; Task 6 verifies boundaries and review requirements.
- No automatic migration from mutable `submitted` state, no external files, no provider call, no Pilot write tool, no new top-level event type, and no `offer_id` context are included.
- Terminology is consistent: `ApplicationEvidenceBundle`, `application_evidence_bundles`, `user_asserted`, `submission_confirmed`, `expected_bundle_sha256`, and `idempotency_key` are used consistently across all tasks.
