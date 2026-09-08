from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.db import init_database
from offerpilot.models import ApplicationEvidenceBundle, ApplicationEvent
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.evidence_bundles import (
    EvidenceBundleConflictError,
    EvidenceBundleNotFound,
    EvidenceBundleValidationError,
    EvidenceBundlesRepository,
    canonical_json,
    parse_json_object,
    sha256_text,
)
from offerpilot.repositories.material_kits import MaterialKitCreate, MaterialKitsRepository
from offerpilot.repositories.resumes import ResumeCreate, ResumesRepository


SUBMITTED_AT = datetime(2026, 7, 14, tzinfo=timezone.utc)


def _create_ready_application(tmp_path, *, status: str = "pending"):
    session_factory = init_database(tmp_path / "data.db")
    applications = ApplicationsRepository(session_factory)
    resumes = ResumesRepository(session_factory)
    material_kits = MaterialKitsRepository(session_factory)
    app = applications.create(
        ApplicationCreate(
            company_name="Acme",
            position_name="Go Engineer",
            job_url="https://jobs.example.test/go",
            source="campus",
            status=status,
        )
    )
    resume = resumes.create(
        ResumeCreate(title="Backend Resume", content_json={"summary": "Go developer"})
    )
    kit = material_kits.create(
        MaterialKitCreate(
            application_id=app.id,
            resume_id=resume.id,
            jd_analysis_id=None,
            jd_snapshot="Build Go services",
            content_json=json.dumps({"body": "Hello"}),
        )
    )
    return session_factory, applications, material_kits, app, kit


def test_preview_confirm_and_get_preserve_the_original_material_snapshot(tmp_path):
    session_factory, applications, material_kits, app, kit = _create_ready_application(tmp_path)
    bundles = EvidenceBundlesRepository(session_factory)

    preview = bundles.preview(app.id)

    assert preview.ready is True
    assert preview.issues == []
    assert preview.snapshot == {
        "schema_version": 1,
        "application": {
            "id": app.id,
            "company_name": "Acme",
            "position_name": "Go Engineer",
            "job_url": "https://jobs.example.test/go",
            "source": "campus",
        },
        "jd": {
            "text": "Build Go services",
            "sha256": sha256_text("Build Go services"),
            "jd_analysis_id": None,
        },
        "resume": {
            "resume_id": 1,
            "title": "Backend Resume",
            "content_json": {"summary": "Go developer"},
            "sha256": sha256_text(canonical_json({"summary": "Go developer"})),
        },
        "material_kit": {
            "material_kit_id": kit.id,
            "content_json": {"body": "Hello"},
            "sha256": sha256_text(canonical_json({"body": "Hello"})),
        },
    }
    assert preview.bundle_sha256 == sha256_text(canonical_json(preview.snapshot))

    bundle, created = bundles.confirm(
        app.id,
        SUBMITTED_AT,
        "91b8e9f1-71cf-4597-832a-b273a15dfec1",
        preview.bundle_sha256,
    )
    assert created is True
    assert bundle.sequence == 1

    material_kits.update(
        kit.id,
        MaterialKitCreate(
            application_id=app.id,
            resume_id=kit.resume_id,
            jd_snapshot="Build Rust services",
            content_json=json.dumps({"body": "Changed"}),
        ),
    )

    stored = bundles.get(app.id, bundle.id)

    assert stored is not None
    stored_snapshot = json.loads(stored.snapshot_json)
    assert stored_snapshot["jd"]["text"] == "Build Go services"
    assert stored_snapshot["material_kit"]["content_json"]["body"] == "Hello"
    current = applications.get(app.id)
    assert current is not None
    assert current.status == "applied"
    assert current.first_applied_at == SUBMITTED_AT
    with session_factory() as session:
        events = list(
            session.scalars(
                select(ApplicationEvent).where(ApplicationEvent.application_id == app.id)
            )
        )
    assert [(event.event_type, event.subtype, event.tags) for event in events] == [
        ("custom", "submission_confirmed", ["submission_evidence", f"bundle:{bundle.id}"])
    ]


def test_preview_rejects_missing_and_soft_deleted_applications(tmp_path):
    session_factory, applications, _material_kits, app, _kit = _create_ready_application(tmp_path)
    bundles = EvidenceBundlesRepository(session_factory)

    with pytest.raises(EvidenceBundleNotFound):
        bundles.preview(999)

    applications.delete(app.id)

    with pytest.raises(EvidenceBundleNotFound):
        bundles.preview(app.id)


def test_preview_reports_unready_sources_and_invalid_json(tmp_path):
    session_factory, _applications, material_kits, app, kit = _create_ready_application(tmp_path)
    bundles = EvidenceBundlesRepository(session_factory)

    material_kits.update(
        kit.id,
        MaterialKitCreate(
            application_id=app.id,
            resume_id=kit.resume_id,
            jd_snapshot="",
            content_json="[]",
        ),
    )

    preview = bundles.preview(app.id)

    assert preview.ready is False
    assert preview.snapshot is None
    assert preview.bundle_sha256 is None
    assert preview.issues
    with pytest.raises(ValueError, match="resume content_json must be a JSON object"):
        parse_json_object("resume", "[]")


def test_confirm_rejects_a_stale_preview_and_replays_the_same_idempotency_key(tmp_path):
    session_factory, _applications, material_kits, app, kit = _create_ready_application(tmp_path)
    bundles = EvidenceBundlesRepository(session_factory)
    preview = bundles.preview(app.id)

    material_kits.update(
        kit.id,
        MaterialKitCreate(
            application_id=app.id,
            resume_id=kit.resume_id,
            jd_snapshot="Changed source",
            content_json=json.dumps({"body": "Hello"}),
        ),
    )

    with pytest.raises(EvidenceBundleConflictError) as exc_info:
        bundles.confirm(
            app.id,
            SUBMITTED_AT,
            "1ee57ba4-f2fe-436b-86ca-a1eeaa361cb6",
            preview.bundle_sha256,
        )
    assert str(exc_info.value) == "提交材料已变化，请重新核对"

    current_preview = bundles.preview(app.id)
    created_bundle, created = bundles.confirm(
        app.id,
        SUBMITTED_AT,
        "b3bf0524-78bf-4961-9a0e-b3f784f1339d",
        current_preview.bundle_sha256,
    )
    replayed_bundle, replayed = bundles.confirm(
        app.id,
        SUBMITTED_AT,
        "b3bf0524-78bf-4961-9a0e-b3f784f1339d",
        "not-the-current-hash",
    )

    assert created is True
    assert replayed is False
    assert replayed_bundle.id == created_bundle.id


def test_confirm_preserves_later_statuses_and_uses_descending_monotonic_sequences(tmp_path):
    session_factory, applications, _material_kits, app, _kit = _create_ready_application(tmp_path)
    bundles = EvidenceBundlesRepository(session_factory)
    preview = bundles.preview(app.id)
    first, first_created = bundles.confirm(
        app.id,
        SUBMITTED_AT,
        "4d49d898-16f5-46d7-a904-d90b27843330",
        preview.bundle_sha256,
    )

    interview = applications.update_full(
        app.id,
        ApplicationCreate(company_name="Acme", position_name="Go Engineer", status="interview"),
    )
    assert interview is not None
    second_preview = bundles.preview(app.id)
    second, second_created = bundles.confirm(
        app.id,
        SUBMITTED_AT,
        "4b20997c-8bd1-4571-a6c2-87967273d5e4",
        second_preview.bundle_sha256,
    )
    after_interview = applications.get(app.id)

    assert first_created is True
    assert second_created is True
    assert (first.sequence, second.sequence) == (1, 2)
    assert after_interview is not None
    assert after_interview.status == "interview"
    assert [bundle.sequence for bundle in bundles.list(app.id)] == [2, 1]

    closed = applications.update_full(
        app.id,
        ApplicationCreate(
            company_name="Acme",
            position_name="Go Engineer",
            status="closed",
            closed_reason="Position filled",
        ),
    )
    assert closed is not None
    closed_preview = bundles.preview(app.id)
    bundles.confirm(
        app.id,
        SUBMITTED_AT,
        "be0d1a24-ae04-4f5a-9caa-f03d98456374",
        closed_preview.bundle_sha256,
    )
    after_closed = applications.get(app.id)
    assert after_closed is not None
    assert after_closed.status == "closed"


def test_confirm_retries_a_distinct_key_after_an_injected_sequence_collision(tmp_path, monkeypatch):
    session_factory, applications, _material_kits, app, _kit = _create_ready_application(tmp_path)
    bundles = EvidenceBundlesRepository(session_factory)
    preview = bundles.preview(app.id)
    competing_engine = create_engine(f"sqlite:///{tmp_path / 'data.db'}")
    competing_factory = sessionmaker(bind=competing_engine, expire_on_commit=False)
    competing_bundles = EvidenceBundlesRepository(competing_factory)
    original_flush = Session.flush
    collision_injected = False

    def inject_competing_confirmation(session, *args, **kwargs):
        nonlocal collision_injected
        if (
            not collision_injected
            and session.get_bind() is session_factory.kw["bind"]
            and any(isinstance(pending, ApplicationEvidenceBundle) for pending in session.new)
        ):
            collision_injected = True
            competing_bundle, created = competing_bundles.confirm(
                app.id,
                SUBMITTED_AT,
                "20c338af-95b8-46b5-adc4-3fb037ac144d",
                preview.bundle_sha256,
            )
            assert created is True
            assert competing_bundle.sequence == 1
        return original_flush(session, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", inject_competing_confirmation)

    retried_bundle, created = bundles.confirm(
        app.id,
        SUBMITTED_AT,
        "d80d1982-74fd-40ca-859a-f2d1b08d7e22",
        preview.bundle_sha256,
    )

    assert collision_injected is True
    assert created is True
    assert retried_bundle.sequence == 2
    assert [bundle.sequence for bundle in bundles.list(app.id)] == [2, 1]
    current_app = applications.get(app.id)
    assert current_app is not None
    assert current_app.status == "applied"
    with session_factory() as session:
        events = list(
            session.scalars(
                select(ApplicationEvent).where(ApplicationEvent.application_id == app.id)
            )
        )
    assert len(events) == 2
    assert {(event.event_type, event.subtype) for event in events} == {
        ("custom", "submission_confirmed"),
    }
    assert sorted(event.tags for event in events) == sorted([
        ["submission_evidence", "bundle:1"],
        ["submission_evidence", "bundle:2"],
    ])


def test_confirm_normalizes_aware_submission_times_and_rejects_naive_times(tmp_path):
    session_factory, applications, _material_kits, app, _kit = _create_ready_application(tmp_path)
    bundles = EvidenceBundlesRepository(session_factory)
    preview = bundles.preview(app.id)

    with pytest.raises(EvidenceBundleValidationError, match="submitted_at must be timezone-aware"):
        bundles.confirm(
            app.id,
            datetime(2026, 7, 14, 1),
            "f43d1091-495a-4e84-a494-782fff539a24",
            preview.bundle_sha256,
        )

    bundle, created = bundles.confirm(
        app.id,
        datetime(2026, 7, 14, 9, tzinfo=timezone(timedelta(hours=8))),
        "ee2beae3-e24a-4b9c-9ae5-b4cf8454a480",
        preview.bundle_sha256,
    )
    expected_submitted_at = datetime(2026, 7, 14, 1, tzinfo=timezone.utc)
    stored = bundles.get(app.id, bundle.id)
    listed = bundles.list(app.id)
    current = applications.get(app.id)

    assert created is True
    assert bundle.submitted_at == expected_submitted_at
    assert bundle.submitted_at.tzinfo == timezone.utc
    assert bundle.confirmed_at.tzinfo == timezone.utc
    assert stored is not None
    assert stored.submitted_at == expected_submitted_at
    assert stored.submitted_at.tzinfo == timezone.utc
    assert stored.confirmed_at.tzinfo == timezone.utc
    assert listed[0].submitted_at == expected_submitted_at
    assert listed[0].submitted_at.tzinfo == timezone.utc
    assert listed[0].confirmed_at.tzinfo == timezone.utc
    assert current is not None
    assert current.first_applied_at == expected_submitted_at


@pytest.mark.parametrize(
    ("constant", "value"),
    [
        ("NaN", float("nan")),
        ("Infinity", float("inf")),
        ("-Infinity", float("-inf")),
    ],
)
def test_canonical_json_rejects_non_finite_numbers(constant, value):
    with pytest.raises(EvidenceBundleValidationError):
        canonical_json({"value": value})


@pytest.mark.parametrize("source", ["resume", "material_kit"])
@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_preview_rejects_non_finite_json_constants_without_persisting(source, constant, tmp_path):
    session_factory, _applications, material_kits, app, kit = _create_ready_application(tmp_path)
    bundles = EvidenceBundlesRepository(session_factory)
    invalid_content = f'{{"value":{constant}}}'

    if source == "resume":
        updated = ResumesRepository(session_factory).update(
            kit.resume_id,
            {"content_json": invalid_content},
        )
        assert updated is not None
    else:
        material_kits.update(
            kit.id,
            MaterialKitCreate(
                application_id=app.id,
                resume_id=kit.resume_id,
                jd_snapshot=kit.jd_snapshot,
                content_json=invalid_content,
            ),
        )

    preview = bundles.preview(app.id)

    assert preview.ready is False
    assert preview.snapshot is None
    assert preview.bundle_sha256 is None
    assert preview.issues
    with pytest.raises(EvidenceBundleValidationError):
        parse_json_object(source, invalid_content)
    with pytest.raises(EvidenceBundleValidationError):
        bundles.confirm(
            app.id,
            SUBMITTED_AT,
            f"strict-{source}-{constant}",
            "",
        )
    assert bundles.list(app.id) == []
