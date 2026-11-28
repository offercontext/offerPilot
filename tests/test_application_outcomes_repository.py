from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from offerpilot.application_strategy.api import register_application_strategy_routes
from offerpilot.application_strategy.models import ApplicationStrategyReceipt
from offerpilot.application_strategy.repository import (
    ApplicationStrategyRepository,
    ConfirmApplied,
    DecideChange,
    ProposeChange,
    StrategyCommand,
    StrategyConflict,
    StrategyWriteResult,
)

from offerpilot.db import init_database
from offerpilot.models import ApplicationOutcome, ApplicationSubmissionSnapshot
from offerpilot.repositories.application_jd_versions import ApplicationJDService
from offerpilot.repositories.application_outcomes import (
    ApplicationOutcomeConflict,
    ApplicationOutcomeValidationError,
    ApplicationOutcomesRepository,
    OutcomeCreate,
    SubmissionSnapshotCreate,
)
from offerpilot.repositories.application_strategy_evidence import (
    StrategyEvidenceUnavailable,
    list_strategy_target_outcomes,
    resolve_strategy_origin,
    resolve_strategy_target,
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


def test_strategy_only_compares_the_selected_later_submission(tmp_path: Path) -> None:
    factory, app, resume, jd, _kit = _fixture(tmp_path)
    apps = ApplicationsRepository(factory)
    jds = ApplicationJDService(factory)
    repo = ApplicationOutcomesRepository(factory)

    def when(hour: int) -> datetime:
        return datetime(2026, 8, 12, hour, tzinfo=timezone.utc)

    def snapshot(application_id: int, jd_id: int, hour: int) -> ApplicationSubmissionSnapshot:
        return repo.create_snapshot(
            SubmissionSnapshotCreate(
                application_id=application_id,
                resume_id=resume.id,
                jd_version_id=jd_id,
                material_kit_id=None,
                submitted_at=when(hour),
                note="",
                source_kind="ui",
                idempotency_key=f"test-strategy-submission-{hour}",
            )
        ).value

    def record(
        submission: ApplicationSubmissionSnapshot, hour: int, *, stage: str = "screening", result: str = "advanced"
    ) -> ApplicationOutcome:
        return repo.create_outcome(
            OutcomeCreate(
                application_id=submission.application_id,
                submission_snapshot_id=submission.id,
                application_event_id=None,
                stage=stage,
                result=result,
                feedback_text="",
                reflection_text="",
                next_action_text="补一句可核实的项目结果",
                feedback_tags=(),
                occurred_at=when(hour),
                source_kind="ui",
                idempotency_key=f"test-strategy-outcome-{submission.id}-{hour}-{stage}-{result}",
            )
        ).value

    def other_application(company: str) -> tuple[int, int]:
        created = apps.create(ApplicationCreate(company_name=company, position_name="应用工程师"))
        spec = jds.create_version(
            created.id,
            jd_text="负责应用交付",
            source_url=None,
            source_kind="ui",
            expected_current_version_id=None,
            idempotency_key=f"test-strategy-jd-{created.id}",
        ).version
        return created.id, spec.id

    a = snapshot(app.id, jd.id, 8)
    origin_record = record(a, 9, result="rejected")
    bad_origin = record(a, 7, result="rejected")
    b_id, b_jd_id = other_application("山河科技")
    b1 = snapshot(b_id, b_jd_id, 10)
    b2 = snapshot(b_id, b_jd_id, 12)
    older = snapshot(b_id, b_jd_id, 7)
    future = snapshot(b_id, b_jd_id, 22)
    c_id, c_jd_id = other_application("行知网络")
    c = snapshot(c_id, c_jd_id, 13)

    record(b1, 14)
    record(c, 15)
    record(b2, 11)  # Claimed occurrence before this version was submitted.
    record(b2, 14, stage="interview")
    record(b2, 15, result="no_response")
    record(b2, 21)  # Claimed occurrence still in the future.
    now = when(20)

    with factory() as session:
        with pytest.raises(StrategyEvidenceUnavailable):
            resolve_strategy_origin(session, application_id=app.id, outcome_id=bad_origin.id, now=now)
        with pytest.raises(StrategyEvidenceUnavailable):
            resolve_strategy_origin(session, application_id=b_id, outcome_id=origin_record.id, now=now)
        origin = resolve_strategy_origin(session, application_id=app.id, outcome_id=origin_record.id, now=now)
        for target_app, snapshot_id in ((app.id, a.id), (b_id, c.id), (b_id, older.id), (b_id, future.id)):
            with pytest.raises(StrategyEvidenceUnavailable):
                resolve_strategy_target(session, origin=origin, application_id=target_app, snapshot_id=snapshot_id, now=now)
        target = resolve_strategy_target(session, origin=origin, application_id=b_id, snapshot_id=b2.id, now=now)
        assert list_strategy_target_outcomes(session, target=target, now=now) == []

    strategy = ApplicationStrategyRepository(factory, clock=lambda: now)
    assert strategy.get(app.id, origin_record.id).change is None

    def command(request_key: str, value: StrategyCommand) -> StrategyWriteResult:
        return strategy.execute(app.id, origin_record.id, request_key=request_key, command=value)

    propose = ProposeChange("补一句可核实的项目结果")
    confirm = ConfirmApplied(0, b_id, b2.id, "说明用 30 个任务回归核对数据隔离")
    assert not command("strategy-propose-0001", propose).replayed
    assert command("strategy-propose-0001", propose).replayed
    with pytest.raises(StrategyConflict, match="different input"):
        command("strategy-propose-0001", ProposeChange("不可复用非原始请求"))
    with pytest.raises(StrategyEvidenceUnavailable):
        command("strategy-confirm-0001", ConfirmApplied(0, b_id, c.id, confirm.applied_detail))

    def interrupted_commit(_session) -> None:
        raise RuntimeError("simulated interruption before commit")

    event.listen(factory, "before_commit", interrupted_commit)
    try:
        with pytest.raises(RuntimeError, match="interruption"):
            command("strategy-confirm-0001", confirm)
    finally:
        event.remove(factory, "before_commit", interrupted_commit)
    assert strategy.get(app.id, origin_record.id).target is None
    assert not command("strategy-confirm-0001", confirm).replayed  # Rolled-back key is reusable.
    replay = command("strategy-confirm-0001", confirm)  # Simulate losing a committed response.
    assert replay.replayed and replay.state.target is not None and replay.state.target.id == b2.id
    assert replay.state.comparable_outcomes == ()
    with pytest.raises(StrategyConflict, match="revision"):
        command("strategy-confirm-0002", ConfirmApplied(0, b_id, b1.id, confirm.applied_detail))

    first = record(b2, 16)
    second = record(b2, 17, result="rejected")
    with factory() as session:
        origin = resolve_strategy_origin(session, application_id=app.id, outcome_id=origin_record.id, now=now)
        target = resolve_strategy_target(session, origin=origin, application_id=b_id, snapshot_id=b2.id, now=now)
        assert [item.id for item in list_strategy_target_outcomes(session, target=target, now=now)] == [second.id, first.id]

    wrong = next(item for item in repo.list_outcomes(b_id) if item.submission_snapshot_id == b1.id)
    with pytest.raises(StrategyConflict, match="exact comparable"):
        command("strategy-decision-001", DecideChange(1, wrong.id, "keep"))
    assert [item.id for item in strategy.get(app.id, origin_record.id).comparable_outcomes] == [second.id, first.id]
    decision = DecideChange(1, first.id, "keep")
    persisted = command("strategy-decision-001", decision)
    assert persisted.state.change is not None and persisted.state.change.decision == "keep"
    record(b2, 18)
    assert command("strategy-decision-001", decision).replayed
    assert [item.id for item in strategy.get(app.id, origin_record.id).comparable_outcomes] == [first.id]
    with pytest.raises(StrategyConflict, match="different input"):
        command("strategy-decision-001", DecideChange(1, first.id, "drop"))
    with factory() as session:
        receipts = list(session.scalars(select(ApplicationStrategyReceipt)))
        assert len(receipts) == 3 and all(len(item.request_key) == len(item.fingerprint) == 64 for item in receipts)
    assert repo.list_outcomes(app.id)[0].next_action_text == propose.proposal

    apps.delete(b_id)
    with pytest.raises(StrategyEvidenceUnavailable):
        command("strategy-confirm-0001", confirm)
    with factory() as session:
        origin = resolve_strategy_origin(session, application_id=app.id, outcome_id=origin_record.id, now=now)
        with pytest.raises(StrategyEvidenceUnavailable):
            resolve_strategy_target(session, origin=origin, application_id=b_id, snapshot_id=b2.id, now=now)

    apps.delete(app.id)
    with factory() as session, pytest.raises(StrategyEvidenceUnavailable):
        resolve_strategy_origin(session, application_id=app.id, outcome_id=origin_record.id, now=now)
    factory.kw["bind"].dispose()



def test_strategy_http_projects_only_selected_evidence_metadata(tmp_path: Path) -> None:
    factory, app, resume, jd, _kit = _fixture(tmp_path)
    repo = ApplicationOutcomesRepository(factory)

    def when(hour: int) -> datetime:
        return datetime(2026, 8, 12, hour, tzinfo=timezone.utc)

    a = repo.create_snapshot(SubmissionSnapshotCreate(
        app.id, resume.id, jd.id, None, when(8), "私人投递备注", "ui", "http-origin-snapshot",
    )).value
    outcome = repo.create_outcome(OutcomeCreate(
        app.id, a.id, None, "screening", "rejected", "私人原始反馈", "", "", (), when(9), "ui", "http-origin-outcome",
    )).value
    b = ApplicationsRepository(factory).create(ApplicationCreate(company_name="路远科技", position_name="应用技术"))
    bjd = ApplicationJDService(factory).create_version(
        b.id, jd_text="不应下发的目标正文", source_url=None, source_kind="ui",
        expected_current_version_id=None, idempotency_key="http-target-jd-key",
    ).version
    def target(hour: int) -> int:
        return repo.create_snapshot(SubmissionSnapshotCreate(
            b.id, resume.id, bjd.id, None, when(hour), "内部材料备忘", "ui", f"http-target-snapshot-{hour}",
        )).value.id

    target(7)
    b1, b2 = target(10), target(15)
    result = repo.create_outcome(OutcomeCreate(
        b.id, b2, None, "screening", "advanced", "不应下发的反馈正文", "", "", (), when(16), "ui",
        "http-target-outcome",
    )).value
    app_server = FastAPI()
    register_application_strategy_routes(app_server, factory)
    path = f"/api/applications/{app.id}/outcomes/{outcome.id}/strategy"
    with TestClient(app_server) as client:
        data = {"action": "propose", "idempotency_key": "http-proposal-key", "proposal": "补充结果"}
        assert client.post(path, json=data, headers={"Origin": "https://third-party.invalid"}).status_code == 403
        assert client.post(path, content='{"action":"propose"}', headers={"Content-Type": "text/plain"}).status_code == 422
        assert client.get(path).json()["change"] is None
        invalid = client.post(path, json={**data, "unexpected": "不回显私人输入"})
        assert invalid.status_code == 422 and "私人" not in invalid.text
        response = client.post(path, json=data, headers={"Origin": "http://testserver"})
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
        assert response.json()["origin"]["has_external_feedback"] and "私人" not in response.text
        targets = client.get(f"{path}/target-snapshots", params={"target_application_id": b.id})
        assert targets.status_code == 200
        assert [item["id"] for item in targets.json()] == [b2, b1]
        assert [item["submitted_at"] for item in targets.json()] == [when(15).isoformat(), when(10).isoformat()]
        assert all(item["jd_version_number"] == bjd.version_number for item in targets.json())
        assert "不应下发" not in targets.text and "内部材料" not in targets.text and "raw_text" not in targets.text

        confirmation = {
            "action": "confirm_applied", "idempotency_key": "http-confirmation-key", "expected_revision": 0,
            "target_application_id": b.id, "target_snapshot_id": b2, "applied_detail": "追加核验记录",
        }
        assert client.post(path, json={**confirmation, "target_snapshot_id": True}).status_code == 422
        response = client.post(path, json=confirmation)
        assert response.status_code == 200
        assert response.json()["target"]["id"] == b2 and response.json()["target"]["company_name"] == b.company_name
        assert [item["id"] for item in response.json()["comparable_outcomes"]] == [result.id]
        assert "不应下发" not in response.text
        decision = {
            "action": "decide", "idempotency_key": "http-decision-key", "expected_revision": 1,
            "target_outcome_id": result.id, "decision": "keep",
        }
        assert client.post(path, json=decision).json()["change"]["decided_outcome_id"] == result.id
        assert client.post(path, json=decision).json()["replayed"]
        assert client.get(path).json()["change"]["decision"] == "keep"

    factory.kw["bind"].dispose()
