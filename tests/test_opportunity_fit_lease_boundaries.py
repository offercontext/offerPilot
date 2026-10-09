from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from sqlalchemy import event, text, update

from offerpilot.models import OpportunityFitReviewStage
from offerpilot.repositories.lease_heartbeat import LeaseHeartbeat
from offerpilot.repositories.opportunity_fit_reviews import (
    _delete_v2_unconfirmed_stage,
    _mark_v2_provider_unknown,
    _v2_live_owner_conditions,
)
from tests.test_lease_heartbeat import _stage, _wait_until


def _set_expiry(factory, stage_id, expiry, storage):  # type: ignore[no-untyped-def]
    with factory() as session:
        if storage == "orm":
            stage = session.get(OpportunityFitReviewStage, stage_id)
            assert stage is not None
            stage.lease_expires_at = expiry
        else:
            # Existing heartbeats wrote UTC-aware datetime values through an
            # untyped statement, so databases can contain this legacy format.
            session.execute(
                text("UPDATE opportunity_fit_review_stages SET lease_expires_at=:expiry WHERE id=:id"),
                {"expiry": expiry, "id": stage_id},
            )
        session.commit()
        return session.execute(text(
            "SELECT lease_expires_at FROM opportunity_fit_review_stages WHERE id=:id"
        ), {"id": stage_id}).scalar_one()


@pytest.mark.parametrize("storage", ["orm", "raw"])
@pytest.mark.parametrize("microsecond", [0, 123456])
@pytest.mark.parametrize("offset_us", [-1, 0, 1])
def test_heartbeat_strict_expiry_boundary(tmp_path, monkeypatch, storage, microsecond, offset_us):
    factory, stage_id, _ = _stage(tmp_path)
    expiry = datetime(2026, 10, 9, tzinfo=timezone.utc).replace(microsecond=microsecond)
    stored = _set_expiry(factory, stage_id, expiry, storage)
    now = expiry + timedelta(microseconds=offset_us)

    class Clock:
        @staticmethod
        def now(_timezone):  # type: ignore[no-untyped-def]
            return now

    monkeypatch.setattr("offerpilot.repositories.lease_heartbeat.datetime", Clock)
    heartbeat = LeaseHeartbeat(
        factory, stage_id=stage_id, stage_generation=1, provider_call_token="owner-token",
        lease_seconds=0.5, interval_seconds=0.1,
    )
    renewed = heartbeat._renew_once()
    assert renewed is (offset_us < 0), (stored, now.isoformat(), renewed)
    assert heartbeat.lost_ownership is (offset_us >= 0)
    with factory() as session:
        stage = session.get(OpportunityFitReviewStage, stage_id)
        assert stage is not None and stage.lease_expires_at is not None
        persisted = stage.lease_expires_at.replace(tzinfo=timezone.utc)
        assert persisted == (now + timedelta(seconds=0.5) if renewed else expiry)


@pytest.mark.parametrize("storage", ["orm", "raw"])
@pytest.mark.parametrize("microsecond", [0, 123456])
@pytest.mark.parametrize("offset_us", [-1, 0, 1])
def test_finalize_strict_expiry_boundary(tmp_path, storage, microsecond, offset_us):
    factory, stage_id, _ = _stage(tmp_path)
    expiry = datetime(2026, 10, 9, tzinfo=timezone.utc).replace(microsecond=microsecond)
    stored = _set_expiry(factory, stage_id, expiry, storage)
    now = expiry + timedelta(microseconds=offset_us)
    with factory() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        stage = session.get(OpportunityFitReviewStage, stage_id)
        assert stage is not None
        result = session.execute(
            update(OpportunityFitReviewStage)
            .where(*_v2_live_owner_conditions(stage, "owner-token", now))
            .values(status="ready", provider_call_token="", lease_expires_at=None)
            .execution_options(synchronize_session=False)
        )
        assert result.rowcount == int(offset_us < 0), (stored, now.isoformat(), result.rowcount)
        session.commit()


@pytest.mark.parametrize("operation", [_mark_v2_provider_unknown, _delete_v2_unconfirmed_stage])
@pytest.mark.parametrize("offset_us", [-1, 0, 1])
def test_error_cleanup_strict_legacy_expiry_boundary(tmp_path, monkeypatch, operation, offset_us):
    factory, stage_id, _ = _stage(tmp_path)
    expiry = datetime(2026, 10, 9, microsecond=123456, tzinfo=timezone.utc)
    _set_expiry(factory, stage_id, expiry, "raw")
    now = expiry + timedelta(microseconds=offset_us)

    class Clock:
        @staticmethod
        def now(_timezone):  # type: ignore[no-untyped-def]
            return now

    monkeypatch.setattr("offerpilot.repositories.opportunity_fit_reviews.datetime", Clock)
    operation(factory, stage_id, 1, "owner-token")
    with factory() as session:
        stage = session.get(OpportunityFitReviewStage, stage_id)
        if offset_us < 0 and operation is _delete_v2_unconfirmed_stage:
            assert stage is None
        else:
            assert stage is not None
            assert stage.status == ("provider_unknown" if offset_us < 0 else "generating")
            assert stage.stage_generation == 1
            assert stage.provider_call_token == "owner-token"
            assert stage.lease_expires_at == expiry


def test_heartbeat_checks_expiry_after_acquiring_sqlite_write_lock(tmp_path, monkeypatch):
    factory, stage_id, _ = _stage(tmp_path)
    expiry = datetime(2026, 10, 9, microsecond=123456, tzinfo=timezone.utc)
    _set_expiry(factory, stage_id, expiry, "raw")
    now = expiry - timedelta(microseconds=1)
    sampled_times = []
    requested = Event()
    acquired = Event()
    engine = factory.kw["bind"]

    class Clock:
        @staticmethod
        def now(_timezone):  # type: ignore[no-untyped-def]
            sampled_times.append(now)
            return now

    monkeypatch.setattr("offerpilot.repositories.lease_heartbeat.datetime", Clock)
    heartbeat = LeaseHeartbeat(
        factory, stage_id=stage_id, stage_generation=1, provider_call_token="owner-token",
        lease_seconds=0.5, interval_seconds=0.1,
    )
    with engine.connect() as holder:
        holder.execute(text("BEGIN IMMEDIATE"))

        @event.listens_for(engine, "before_cursor_execute")
        def request(_connection, _cursor, statement, _parameters, _context, _executemany):
            if statement == "BEGIN IMMEDIATE":
                requested.set()

        @event.listens_for(engine, "after_cursor_execute")
        def acquire(_connection, _cursor, statement, _parameters, _context, _executemany):
            if statement == "BEGIN IMMEDIATE":
                acquired.set()

        heartbeat.start()
        try:
            assert requested.wait(timeout=3)
            assert not acquired.is_set()
            assert sampled_times == []
            # Release the actual SQLite write lock only after the lease expires.
            now = expiry + timedelta(microseconds=1)
            holder.rollback()
            _wait_until(lambda: heartbeat.lost_ownership)
        finally:
            holder.rollback()
            heartbeat.stop()

    assert not heartbeat.is_alive
    assert acquired.is_set()
    assert sampled_times == [now]
    with factory() as session:
        stage = session.get(OpportunityFitReviewStage, stage_id)
        assert stage is not None
        assert stage.lease_expires_at == expiry


def test_heartbeat_stop_during_expiry_read_does_not_report_lost_owner(tmp_path, monkeypatch):
    factory, stage_id, _ = _stage(tmp_path)
    expiry = datetime(2026, 10, 9, microsecond=123456, tzinfo=timezone.utc)
    _set_expiry(factory, stage_id, expiry, "raw")
    stopped_during_read = Event()

    class Clock:
        @staticmethod
        def now(_timezone):  # type: ignore[no-untyped-def]
            return expiry

    monkeypatch.setattr("offerpilot.repositories.lease_heartbeat.datetime", Clock)
    heartbeat = LeaseHeartbeat(
        factory, stage_id=stage_id, stage_generation=1, provider_call_token="owner-token",
        lease_seconds=0.5, interval_seconds=0.1,
    )

    @event.listens_for(factory.kw["bind"], "after_cursor_execute")
    def stop_after_read(_connection, _cursor, statement, _parameters, _context, _executemany):
        if "FROM opportunity_fit_review_stages" in statement and not stopped_during_read.is_set():
            # Stop from the worker at the read boundary, before it can classify
            # the expired row. stop() must also remain safe on its own thread.
            heartbeat.stop()
            stopped_during_read.set()

    heartbeat.start()
    try:
        assert stopped_during_read.wait(timeout=3)
        _wait_until(lambda: not heartbeat.is_alive)
    finally:
        heartbeat.stop()

    assert not heartbeat.lost_ownership
    with factory() as session:
        stage = session.get(OpportunityFitReviewStage, stage_id)
        assert stage is not None
        assert stage.lease_expires_at == expiry
