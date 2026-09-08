from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from offerpilot.db import init_database
from offerpilot.repositories.application_jd_versions import ApplicationJDService
from offerpilot.repositories.application_outcomes import (
    ApplicationOutcomeConflict,
    ApplicationOutcomeValidationError,
    ApplicationOutcomesRepository,
    OutcomeCreate,
    SubmissionSnapshotCreate,
)
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.material_kits import MaterialKitCreate, MaterialKitsRepository
from offerpilot.repositories.resumes import ResumeCreate, ResumesRepository


def _fixture(tmp_path: Path):  # type: ignore[no-untyped-def]
    factory = init_database(tmp_path / "outcomes.db")
    apps = ApplicationsRepository(factory)
    resumes = ResumesRepository(factory)
    jds = ApplicationJDService(factory)
    kits = MaterialKitsRepository(factory)
    app = apps.create(ApplicationCreate(company_name="云栖智能", position_name="AI 应用工程师"))
    resume = resumes.create(
        ResumeCreate(title="筱哲-岗位版", content_json={"raw_text": "五年 AI 产品经验"})
    )
    jd = jds.create_version(
        app.id,
        jd_text="负责 AI 应用设计与交付",
        source_url=None,
        source_kind="ui",
        expected_current_version_id=None,
        idempotency_key="jd-version-key-0001",
    ).version
    kit = kits.create(
        MaterialKitCreate(
            application_id=app.id,
            resume_id=resume.id,
            jd_snapshot=jd.jd_text,
            jd_version_id=jd.id,
            content_json='{"cover_letter":"您好"}',
        )
    )
    return factory, app, resume, jd, kit


def test_freezes_sources_replays_and_derives_source_changes(tmp_path: Path) -> None:
    factory, app, resume, jd, kit = _fixture(tmp_path)
    repo = ApplicationOutcomesRepository(factory)
    payload = SubmissionSnapshotCreate(
        application_id=app.id,
        resume_id=resume.id,
        jd_version_id=jd.id,
        material_kit_id=kit.id,
        submitted_at=datetime(2026, 8, 12, 9, 30, tzinfo=timezone.utc),
        note="官网投递",
        source_kind="ui",
        idempotency_key="snapshot-key-000001",
    )

    created = repo.create_snapshot(payload)
    replayed = repo.create_snapshot(payload)

    assert created.replayed is False
    assert replayed.replayed is True
    assert replayed.value.id == created.value.id
    view = repo.list_snapshots(app.id)[0]
    assert view.source_states == {"resume": "current", "jd": "current", "material": "current"}
    assert "五年 AI 产品经验" in view.value.resume_snapshot_json
    assert view.value.jd_snapshot == "负责 AI 应用设计与交付"

    ResumesRepository(factory).update(resume.id, {"content_json": {"raw_text": "六年 AI 产品经验"}})
    changed = repo.list_snapshots(app.id)[0]
    assert changed.source_states["resume"] == "changed"

    with pytest.raises(ApplicationOutcomeConflict, match="idempotency"):
        repo.create_snapshot(SubmissionSnapshotCreate(**{**payload.__dict__, "note": "内推"}))

    factory.kw["bind"].dispose()


def test_records_append_only_outcomes_and_summarizes_user_tags(tmp_path: Path) -> None:
    factory, app, resume, jd, _kit = _fixture(tmp_path)
    repo = ApplicationOutcomesRepository(factory)
    snapshot = repo.create_snapshot(
        SubmissionSnapshotCreate(
            application_id=app.id,
            resume_id=resume.id,
            jd_version_id=jd.id,
            material_kit_id=None,
            submitted_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            note="",
            source_kind="ui",
            idempotency_key="snapshot-key-000002",
        )
    ).value
    data = OutcomeCreate(
        application_id=app.id,
        submission_snapshot_id=snapshot.id,
        application_event_id=None,
        stage="interview",
        result="advanced",
        feedback_text="技术深度扎实，沟通结构还可更清晰",
        reflection_text="示例不够聚焦",
        next_action_text="用 STAR 重写项目案例",
        feedback_tags=("communication", "technical_depth", "communication"),
        occurred_at=datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
        source_kind="pilot",
        idempotency_key="outcome-key-0000001",
    )

    first = repo.create_outcome(data)
    replay = repo.create_outcome(data)

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.value.id == first.value.id
    assert len(repo.list_outcomes(app.id)) == 1
    assert repo.summary(app.id) == {
        "total": 1,
        "stage_counts": {"interview": 1},
        "result_counts": {"advanced": 1},
        "feedback_tag_counts": {"communication": 1, "technical_depth": 1},
        "next_actions_pending": 1,
    }

    with pytest.raises(ApplicationOutcomeValidationError, match="stage"):
        repo.create_outcome(OutcomeCreate(**{**data.__dict__, "stage": "hired"}))

    factory.kw["bind"].dispose()
