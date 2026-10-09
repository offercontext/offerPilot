"""Real SQLite lease boundaries across ORM and historical raw datetime writes."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, text

import offerpilot.db as database
import offerpilot.knowledge.repository as repository_module
from offerpilot.db import init_database
from offerpilot.knowledge.repository import JobCreateInput, KnowledgeRepository
from tests.test_knowledge_brief_lease_guards import _create_attempt, _setup


@pytest.fixture
def repository(tmp_path):
    return KnowledgeRepository(init_database(tmp_path / "data.db"))


def _claim(repository, expiry):
    repository.create_job(JobCreateInput(kind="extract", queue="extraction"))
    job = repository.claim_next_job(
        "extraction", lease_owner="owner", now=expiry - timedelta(seconds=30)
    )
    assert job is not None
    return job


def _store_expiry(repository, job_id, expiry, storage):
    if storage == "raw":
        with repository._session_factory() as session:
            session.execute(
                text("UPDATE knowledge_jobs SET lease_expires_at=:expiry WHERE id=:jid"),
                {"expiry": expiry, "jid": job_id},
            )
            session.commit()


def _snapshot(repository, job_id):
    with repository._session_factory() as session:
        return tuple(session.execute(
            text("SELECT * FROM knowledge_jobs WHERE id=:jid"), {"jid": job_id}
        ).one())


def _brief_snapshot(repository, source_id, attempt_id):
    with repository._session_factory() as session:
        source = tuple(session.execute(
            text("SELECT * FROM knowledge_sources WHERE id=:sid"), {"sid": source_id}
        ).one())
        attempt = tuple(session.execute(
            text("SELECT * FROM knowledge_brief_attempts WHERE id=:aid"), {"aid": attempt_id}
        ).one())
        return source, attempt


def _clock(monkeypatch, module, moment):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment if tz is not None else moment.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", FrozenDatetime)


@pytest.mark.parametrize("storage", ["orm", "raw"])
@pytest.mark.parametrize("microsecond", [0, 123456])
@pytest.mark.parametrize("delta", [-1, 0, 1])
@pytest.mark.parametrize("action", ["heartbeat", "complete"])
def test_active_writes_use_strict_microsecond_deadline(
    repository, storage, microsecond, delta, action
):
    expiry = datetime(2030, 1, 2, 12, microsecond=microsecond, tzinfo=timezone.utc)
    job = _claim(repository, expiry)
    _store_expiry(repository, job.id, expiry, storage)
    before = _snapshot(repository, job.id)
    moment = expiry + timedelta(microseconds=delta)
    if action == "heartbeat":
        accepted = repository.heartbeat_job(
            job.id, attempt_token=job.attempt_token, now=moment
        ) is not None
    else:
        accepted, _ = repository.complete_job(
            job.id, attempt_token=job.attempt_token, status="succeeded", now=moment
        )
    assert accepted is (delta < 0)
    if not accepted:
        assert _snapshot(repository, job.id) == before


@pytest.mark.parametrize("storage", ["orm", "raw"])
@pytest.mark.parametrize("microsecond", [0, 123456])
@pytest.mark.parametrize("delta", [-1, 0, 1, -3_600_000_000])
@pytest.mark.parametrize("action", ["startup", "requeue", "diagnose"])
def test_recovery_uses_same_exact_boundary(
    repository, tmp_path, monkeypatch, storage, microsecond, delta, action
):
    expiry = datetime(2030, 1, 2, 12, microsecond=microsecond, tzinfo=timezone.utc)
    job = _claim(repository, expiry)
    _store_expiry(repository, job.id, expiry, storage)
    before = _snapshot(repository, job.id)
    moment = expiry + timedelta(microseconds=delta)
    if action == "startup":
        _clock(monkeypatch, database, moment)
        database._recover_knowledge_runtime(repository._session_factory.kw["bind"], tmp_path)
    else:
        recovered = repository.recover_stale_running_jobs(
            now=moment, requeue=action == "requeue"
        )
        assert recovered == ([job.id] if delta >= 0 else [])
    result = repository.get_job(job.id)
    assert result.status == (
        ("failed" if action == "diagnose" else "pending") if delta >= 0 else "running"
    )
    if delta < 0:
        assert _snapshot(repository, job.id) == before


@pytest.mark.parametrize("storage", ["orm", "raw"])
@pytest.mark.parametrize("delta", [-1, 0, 1])
@pytest.mark.parametrize("action", ["success", "failure"])
def test_brief_publication_uses_exact_lease_gate(tmp_path, monkeypatch, storage, delta, action):
    repository, source_id, snapshot_id = _setup(tmp_path)
    attempt_id, job_id, token = _create_attempt(repository, source_id, snapshot_id)
    expiry = datetime(2030, 1, 2, 12, tzinfo=timezone.utc)
    with repository._session_factory() as session:
        from offerpilot.models import KnowledgeJob

        row = session.get(KnowledgeJob, job_id)
        row.lease_expires_at = expiry
        session.commit()
    _store_expiry(repository, job_id, expiry, storage)
    before = _snapshot(repository, job_id)
    before_brief = _brief_snapshot(repository, source_id, attempt_id)
    _clock(monkeypatch, repository_module, expiry + timedelta(microseconds=delta))
    if action == "success":
        accepted, _, _ = repository.commit_brief_attempt_success(
            attempt_id, job_id=job_id, attempt_token=token,
            payload_json="{}", validation_report_json="{}",
        )
    else:
        accepted, _, _ = repository.fail_brief_attempt(
            attempt_id, job_id=job_id, attempt_token=token,
            error_code="test_failure", error_message="test failure",
        )
    assert accepted is (delta < 0)
    if not accepted:
        assert _snapshot(repository, job_id) == before
        assert _brief_snapshot(repository, source_id, attempt_id) == before_brief
        assert repository.get_source_brief(source_id) is None


@pytest.mark.parametrize("action", ["heartbeat", "complete", "requeue", "startup", "brief_gate"])
def test_default_clock_is_sampled_after_writer_lock(
    repository, tmp_path, monkeypatch, action
):
    expiry = datetime(2030, 1, 2, 12, microsecond=123456, tzinfo=timezone.utc)
    job = _claim(repository, expiry)
    clock_module = database if action == "startup" else repository_module
    _clock(monkeypatch, clock_module, expiry - timedelta(microseconds=1))
    engine = repository._session_factory.kw["bind"]
    locked = []

    def after_execute(conn, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.upper().split())
        if not locked and (normalized == "BEGIN IMMEDIATE" or normalized.startswith("UPDATE KNOWLEDGE_JOBS")):
            locked.append(True)
            _clock(monkeypatch, clock_module, expiry + timedelta(microseconds=1))

    event.listen(engine, "after_cursor_execute", after_execute)
    try:
        if action == "heartbeat":
            assert repository.heartbeat_job(job.id, attempt_token=job.attempt_token) is None
        elif action == "complete":
            accepted, _ = repository.complete_job(
                job.id, attempt_token=job.attempt_token, status="succeeded"
            )
            assert accepted is False
        elif action == "brief_gate":
            with repository._session_factory() as session:
                assert not repository._lock_active_job_for_write(
                    session, job_id=job.id, attempt_token=job.attempt_token
                )
        elif action == "requeue":
            assert repository.recover_stale_running_jobs(requeue=True) == [job.id]
        else:
            database._recover_knowledge_runtime(engine, tmp_path)
            assert repository.get_job(job.id).status == "pending"
        assert locked
    finally:
        event.remove(engine, "after_cursor_execute", after_execute)


@pytest.mark.parametrize("action", ["heartbeat", "complete", "requeue", "startup"])
def test_lease_read_holds_writer_lock_against_other_connection(
    repository, tmp_path, monkeypatch, action
):
    expiry = datetime(2030, 1, 2, 12, microsecond=123456, tzinfo=timezone.utc)
    job = _claim(repository, expiry)
    moment = expiry - timedelta(microseconds=1)
    _clock(monkeypatch, database, moment)
    engine = repository._session_factory.kw["bind"]
    checked = []

    def after_execute(conn, cursor, statement, parameters, context, executemany):
        normalized = statement.upper()
        if not checked and "SELECT" in normalized and "LEASE_EXPIRES_AT" in normalized and "KNOWLEDGE_JOBS" in normalized:
            checked.append(True)
            with sqlite3.connect(tmp_path / "data.db", timeout=0) as other:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    other.execute("UPDATE knowledge_jobs SET canceled=1 WHERE id=?", (job.id,))

    event.listen(engine, "after_cursor_execute", after_execute)
    try:
        if action == "heartbeat":
            assert repository.heartbeat_job(job.id, attempt_token=job.attempt_token, now=moment)
        elif action == "complete":
            assert repository.complete_job(
                job.id, attempt_token=job.attempt_token, status="succeeded", now=moment
            )[0]
        elif action == "requeue":
            assert repository.recover_stale_running_jobs(now=moment, requeue=True) == []
        else:
            database._recover_knowledge_runtime(engine, tmp_path)
        assert checked
    finally:
        event.remove(engine, "after_cursor_execute", after_execute)


@pytest.mark.parametrize("action", ["requeue", "startup"])
@pytest.mark.parametrize("replace_token", [False, True])
def test_recovery_waiting_for_writer_observes_renewed_lease(
    repository, tmp_path, monkeypatch, action, replace_token
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    moment = datetime(2030, 1, 2, 12, tzinfo=timezone.utc)
    job = _claim(repository, moment - timedelta(seconds=1))
    _clock(monkeypatch, database, moment)
    engine = repository._session_factory.kw["bind"]
    waiting = Event()

    def before_execute(conn, cursor, statement, parameters, context, executemany):
        if statement == "BEGIN IMMEDIATE":
            waiting.set()

    def recover():
        if action == "startup":
            database._recover_knowledge_runtime(engine, tmp_path)
            return []
        return repository.recover_stale_running_jobs(now=moment, requeue=True)

    event.listen(engine, "before_cursor_execute", before_execute)
    try:
        with sqlite3.connect(tmp_path / "data.db") as writer:
            writer.execute("BEGIN IMMEDIATE")
            renewed = moment + timedelta(seconds=30)
            token = "replacement" if replace_token else job.attempt_token
            writer.execute(
                "UPDATE knowledge_jobs SET lease_expires_at=?, attempt_token=? WHERE id=?",
                (renewed.isoformat(sep=" "), token, job.id),
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(recover)
                try:
                    assert waiting.wait(timeout=5)
                finally:
                    writer.commit()
                assert pending.result(timeout=5) == []
        result = repository.get_job(job.id)
        assert result.status == "running"
        assert result.attempt_token == token
        assert result.lease_expires_at == renewed
    finally:
        event.remove(engine, "before_cursor_execute", before_execute)


@pytest.mark.parametrize("action", ["heartbeat", "complete"])
@pytest.mark.parametrize("change", ["cancel", "reclaim"])
def test_waiting_old_worker_cannot_overwrite_cancellation_or_new_owner(
    repository, tmp_path, action, change
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    moment = datetime(2030, 1, 2, 12, tzinfo=timezone.utc)
    job = _claim(repository, moment + timedelta(seconds=30))
    engine = repository._session_factory.kw["bind"]
    waiting = Event()

    def before_execute(conn, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.upper().split())
        if normalized == "BEGIN IMMEDIATE" or normalized.startswith("UPDATE KNOWLEDGE_JOBS"):
            waiting.set()

    def write():
        if action == "heartbeat":
            return repository.heartbeat_job(job.id, attempt_token=job.attempt_token, now=moment)
        return repository.complete_job(
            job.id, attempt_token=job.attempt_token, now=moment, status="succeeded"
        )[1]

    event.listen(engine, "before_cursor_execute", before_execute)
    try:
        with sqlite3.connect(tmp_path / "data.db") as writer:
            writer.execute("BEGIN IMMEDIATE")
            if change == "cancel":
                writer.execute(
                    "UPDATE knowledge_jobs SET canceled=1, lease_expires_at=NULL WHERE id=?",
                    (job.id,),
                )
            else:
                writer.execute(
                    "UPDATE knowledge_jobs SET attempt_token='replacement' WHERE id=?", (job.id,)
                )
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(write)
                try:
                    assert waiting.wait(timeout=5)
                finally:
                    writer.commit()
                assert pending.result(timeout=5) is None
        result = repository.get_job(job.id)
        assert result.status == "running"
        assert result.canceled is (change == "cancel")
        assert result.attempt_token == ("replacement" if change == "reclaim" else job.attempt_token)
        if change == "cancel":
            assert result.lease_expires_at is None
    finally:
        event.remove(engine, "before_cursor_execute", before_execute)
