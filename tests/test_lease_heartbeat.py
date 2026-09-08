from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time

from sqlalchemy import select

from offerpilot.db import init_database
from offerpilot.models import OpportunityFitReviewSession, OpportunityFitReviewStage
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.lease_heartbeat import LeaseHeartbeat
from offerpilot.repositories.resumes import ResumeCreate, ResumesRepository


def _stage(tmp_path, *, generation: int = 1, token: str = "owner-token"):
    factory = init_database(tmp_path / "data.db")
    application = ApplicationsRepository(factory).create(
        ApplicationCreate(company_name="Acme", position_name="Backend")
    )
    resume = ResumesRepository(factory).create(
        ResumeCreate(title="Resume", parsed_data="Built APIs", content_json={"raw_text": "Built APIs"})
    )
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=0.5)
    with factory() as session:
        root = OpportunityFitReviewSession(
            application_id=application.id,
            triage_idempotency_key="heartbeat-test-root",
            proposal_schema_version=2,
        )
        session.add(root)
        session.flush()
        stage = OpportunityFitReviewStage(
            review_id=root.id,
            application_id=application.id,
            resume_id=resume.id,
            stage="triage",
            proposal_schema_version=2,
            idempotency_key="heartbeat-test-stage",
            source_snapshot_json="{}",
            source_fingerprint_sha256="fingerprint",
            proposal_json="{}",
            proposal_sha256="",
            status="generating",
            stage_generation=generation,
            provider_call_token=token,
            lease_expires_at=expires_at,
        )
        session.add(stage)
        session.commit()
        stage_id = stage.id
    return factory, stage_id, expires_at


def _wait_until(predicate, timeout: float = 3.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true")


def test_heartbeat_renews_matching_stage_owner_and_stops_cleanly(tmp_path) -> None:
    factory, stage_id, initial_expiry = _stage(tmp_path)
    heartbeat = LeaseHeartbeat(
        factory,
        stage_id=stage_id,
        stage_generation=1,
        provider_call_token="owner-token",
        lease_seconds=0.5,
        interval_seconds=0.1,
    )

    heartbeat.start()
    time.sleep(0.25)
    assert heartbeat.is_alive
    heartbeat.stop()

    assert not heartbeat.is_alive
    assert not heartbeat.lost_ownership
    with factory() as session:
        stage = session.scalar(select(OpportunityFitReviewStage).where(OpportunityFitReviewStage.id == stage_id))
        assert stage is not None
        assert stage.lease_expires_at is not None
        assert stage.lease_expires_at.replace(tzinfo=timezone.utc) > initial_expiry


def test_heartbeat_loses_ownership_when_generation_or_token_does_not_match(tmp_path) -> None:
    for generation, token in ((2, "owner-token"), (1, "other-token")):
        factory, stage_id, initial_expiry = _stage(tmp_path / f"{generation}-{token}")
        heartbeat = LeaseHeartbeat(
            factory,
            stage_id=stage_id,
            stage_generation=generation,
            provider_call_token=token,
            lease_seconds=0.5,
            interval_seconds=0.1,
        )

        heartbeat.start()
        _wait_until(lambda: heartbeat.lost_ownership)
        heartbeat.stop()

        assert not heartbeat.is_alive
        with factory() as session:
            stage = session.scalar(
                select(OpportunityFitReviewStage).where(OpportunityFitReviewStage.id == stage_id)
            )
            assert stage is not None
            assert stage.lease_expires_at is not None
            assert stage.lease_expires_at.replace(tzinfo=timezone.utc) == initial_expiry


def test_heartbeat_does_not_revive_expired_lease_after_database_lock_wait(tmp_path) -> None:
    factory, stage_id, _initial_expiry = _stage(tmp_path)
    with factory() as session:
        stage = session.scalar(select(OpportunityFitReviewStage).where(OpportunityFitReviewStage.id == stage_id))
        assert stage is not None
        stage.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=0.1)
        session.commit()
        original_expiry = stage.lease_expires_at

    class DelayedSession:
        def __init__(self, session) -> None:  # type: ignore[no-untyped-def]
            self._session = session

        def __enter__(self):  # type: ignore[no-untyped-def]
            time.sleep(0.25)
            return self._session.__enter__()

        def __exit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
            return self._session.__exit__(exc_type, exc_value, traceback)

    class DelayedFactory:
        def __call__(self):  # type: ignore[no-untyped-def]
            return DelayedSession(factory())

    heartbeat = LeaseHeartbeat(
        DelayedFactory(),  # type: ignore[arg-type]
        stage_id=stage_id,
        stage_generation=1,
        provider_call_token="owner-token",
        lease_seconds=0.5,
        interval_seconds=0.05,
    )

    heartbeat.start()
    _wait_until(lambda: heartbeat.lost_ownership)
    heartbeat.stop()

    with factory() as session:
        stage = session.scalar(select(OpportunityFitReviewStage).where(OpportunityFitReviewStage.id == stage_id))
        assert stage is not None
        assert stage.lease_expires_at is not None
        assert stage.lease_expires_at.replace(tzinfo=timezone.utc) == original_expiry
