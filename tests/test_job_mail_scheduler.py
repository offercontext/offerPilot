"""Exercise scheduled admission through tick with no real clock, IMAP, or AI."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from offerpilot.db import init_database
from offerpilot.job_mail import sync as sync_module
from offerpilot.job_mail.extraction import ParsedMail
from offerpilot.job_mail.models import JobMailConnection, JobMailSuggestion, JobMailSyncRun
from offerpilot.job_mail.sync import JobMailSyncService
from offerpilot.job_mail.transport import FakeIMAPTransport, TransportUnavailable


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 12, 8)

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)

    def due(self, service):
        value = service.status()["connection"]["next_run_at"]
        assert value is not None
        self.now = datetime.fromisoformat(value).replace(tzinfo=None)


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(sync_module, "_now", lambda: clock.now)
    sessions = init_database(tmp_path / "scheduler.db")
    transport = FakeIMAPTransport()
    service = JobMailSyncService(sessions, transport)
    service.connect({"provider": "synthetic", "folders": ["INBOX"]})
    service.update_settings({"sync_mode": "automatic", "interval_minutes": 5})
    yield sessions, transport, service, clock
    service.shutdown()


def runs(sessions):
    with sessions() as session:
        return list(session.scalars(select(JobMailSyncRun).order_by(JobMailSyncRun.started_at)))


def connection(sessions):
    with sessions() as session:
        result = session.scalar(select(JobMailConnection))
        assert result is not None
        return result


def drain(service):
    # Joining the actual workers avoids real-time sleeps and the race between
    # observing a terminal database status and the worker physically exiting.
    with service._thread_lock:
        workers = list(service._threads)
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive(), "mail worker did not finish"
    return service.status()


@contextmanager
def blocked_read(transport, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = transport.read

    def read(*args, **kwargs):
        batch = original(*args, **kwargs)
        entered.set()
        if not release.wait(timeout=10):
            raise TimeoutError("test did not release fake IMAP read")
        return batch

    with monkeypatch.context() as patch:
        patch.setattr(transport, "read", read)
        try:
            yield entered, release
        finally:
            release.set()


def test_tick_waits_until_due_then_runs_one_automatic_pipeline(scheduler):
    sessions, transport, service, clock = scheduler
    first_due = connection(sessions).next_run_at
    assert first_due == clock.now + timedelta(minutes=5)
    transport.messages = [ParsedMail(
        1, "1", "INBOX", "<scheduler@example.invalid>",
        clock.now.replace(tzinfo=timezone.utc).isoformat(),
        "hr@example.invalid", "面试邀请",
        "公司：合成公司\n岗位：测试\n面试2026-10-15T15:00:00+08:00\n时长：30分钟",
        "scheduler-mail",
    )]
    clock.now = first_due - timedelta(microseconds=1)
    service.tick()
    assert runs(sessions) == []
    assert transport.read_calls == []

    clock.now = first_due
    service.tick()
    status = drain(service)
    assert status["run"]["trigger"] == "automatic"
    assert status["run"]["status"] == "completed"
    assert status["run"]["progress"]["candidates"] == 1
    assert connection(sessions).last_attempt_at == first_due
    assert connection(sessions).next_run_at == first_due + timedelta(minutes=5)
    with sessions() as session:
        assert len(list(session.scalars(select(JobMailSuggestion)))) == 1

    service.tick()
    assert len(runs(sessions)) == 1
    assert transport.read_calls == ["INBOX"]


def test_overdue_tick_does_not_replay_missed_intervals(scheduler):
    sessions, transport, service, clock = scheduler
    clock.advance(hours=2)
    service.tick()
    assert drain(service)["run"]["status"] == "completed"
    assert connection(sessions).next_run_at == clock.now + timedelta(minutes=5)
    for _ in range(3):
        service.tick()
    assert len(runs(sessions)) == 1
    assert transport.read_calls == ["INBOX"]


def test_manual_request_and_due_tick_admit_one_worker(scheduler, monkeypatch):
    sessions, transport, service, clock = scheduler
    clock.due(service)
    barrier = threading.Barrier(2)

    def manual_request():
        barrier.wait(timeout=5)
        return service.start_sync()

    def scheduled_request():
        barrier.wait(timeout=5)
        service.tick()

    with blocked_read(transport, monkeypatch) as (entered, release):
        with ThreadPoolExecutor(max_workers=2) as requests:
            manual = requests.submit(manual_request)
            scheduled = requests.submit(scheduled_request)
            manual_result = manual.result(timeout=5)
            scheduled.result(timeout=5)
        assert entered.wait(timeout=5)
        active = runs(sessions)
        assert len(active) == 1
        assert active[0].id == manual_result["id"]
        assert active[0].status == "running"
        assert active[0].trigger in {"manual", "automatic"}
        assert transport.read_calls == ["INBOX"]
        release.set()
        assert drain(service)["run"]["status"] == "completed"
    assert len(runs(sessions)) == 1


def test_disabling_automatic_keeps_current_run_and_allows_manual(scheduler, monkeypatch):
    sessions, transport, service, clock = scheduler
    clock.due(service)
    with blocked_read(transport, monkeypatch) as (entered, release):
        service.tick()
        assert entered.wait(timeout=5)
        automatic_id = service.status()["run"]["id"]
        status = service.update_settings({"sync_mode": "manual"})
        assert status["connection"]["next_run_at"] is None
        assert status["run"]["id"] == automatic_id
        assert status["run"]["trigger"] == "automatic"
        assert status["run"]["status"] == "running"
        release.set()
        assert drain(service)["run"]["status"] == "completed"

    clock.advance(minutes=5)
    service.tick()
    assert len(runs(sessions)) == 1
    assert transport.read_calls == ["INBOX"]
    manual = service.start_sync()
    assert manual["id"] != automatic_id
    assert manual["trigger"] == "manual"
    assert drain(service)["run"]["status"] == "completed"
    assert connection(sessions).sync_mode == "manual"
    assert connection(sessions).next_run_at is None


def test_cancelling_automatic_run_preserves_future_schedule(scheduler, monkeypatch):
    sessions, transport, service, clock = scheduler
    clock.due(service)
    with blocked_read(transport, monkeypatch) as (entered, release):
        service.tick()
        assert entered.wait(timeout=5)
        current = service.status()["run"]
        next_due = connection(sessions).next_run_at
        assert current["trigger"] == "automatic"
        assert service.cancel_sync(current["id"])["status"] == "cancelled"
        assert connection(sessions).sync_mode == "automatic"
        assert connection(sessions).next_run_at == next_due
        service.tick()
        assert len(runs(sessions)) == 1
        release.set()
        assert drain(service)["run"]["status"] == "cancelled"

    clock.now = next_due - timedelta(microseconds=1)
    service.tick()
    assert len(runs(sessions)) == 1
    clock.now = next_due
    service.tick()
    following = drain(service)["run"]
    assert following["id"] != current["id"]
    assert following["trigger"] == "automatic"
    assert following["status"] == "completed"
    assert transport.read_calls == ["INBOX", "INBOX"]


@pytest.mark.parametrize("failure, error_code", [
    (TransportUnavailable, "folder_read_failed"),
    (RuntimeError, "sync_failed"),
])
def test_three_automatic_failures_require_explicit_resume(
    scheduler, monkeypatch, failure, error_code,
):
    sessions, transport, service, clock = scheduler
    original = transport.read
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(clock.now)
        raise failure("synthetic transport fault")

    monkeypatch.setattr(transport, "read", fail)
    for count in range(1, 4):
        clock.due(service)
        service.tick()
        status = drain(service)
        assert status["run"]["trigger"] == "automatic"
        assert status["run"]["status"] == "failed"
        assert status["run"]["error_code"] == error_code
        assert connection(sessions).failure_count == count
        if count < 3:
            assert status["connection"]["sync_mode"] == "automatic"
            assert connection(sessions).next_run_at == clock.now + timedelta(minutes=5)

    assert len(attempts) == 3
    assert len(runs(sessions)) == 3
    assert connection(sessions).sync_mode == "manual"
    assert connection(sessions).next_run_at is None
    clock.advance(minutes=5)
    service.tick()
    assert len(attempts) == 3
    assert len(runs(sessions)) == 3

    monkeypatch.setattr(transport, "read", original)
    assert service.start_sync()["trigger"] == "manual"
    assert drain(service)["run"]["status"] == "completed"
    assert connection(sessions).failure_count == 0
    assert connection(sessions).sync_mode == "manual"
    assert connection(sessions).next_run_at is None
    clock.advance(days=1)
    service.tick()
    assert len(runs(sessions)) == 4
    assert transport.read_calls == ["INBOX"]

    service.update_settings({"sync_mode": "automatic"})
    service.tick()
    assert len(runs(sessions)) == 4
    clock.due(service)
    service.tick()
    resumed = drain(service)["run"]
    assert resumed["trigger"] == "automatic"
    assert resumed["status"] == "completed"
    assert len(runs(sessions)) == 5


def test_automatic_success_resets_consecutive_failure_streak(scheduler, monkeypatch):
    sessions, transport, service, clock = scheduler
    original = transport.read

    def fail(*args, **kwargs):
        raise TransportUnavailable("temporary fake outage")

    monkeypatch.setattr(transport, "read", fail)
    for _ in range(2):
        clock.due(service)
        service.tick()
        assert drain(service)["run"]["status"] == "failed"
    assert connection(sessions).failure_count == 2
    monkeypatch.setattr(transport, "read", original)
    clock.due(service)
    service.tick()
    assert drain(service)["run"]["status"] == "completed"
    success_at = clock.now
    assert connection(sessions).failure_count == 0
    assert connection(sessions).last_success_at == success_at

    monkeypatch.setattr(transport, "read", fail)
    clock.due(service)
    service.tick()
    assert drain(service)["run"]["status"] == "failed"
    assert connection(sessions).failure_count == 1
    assert connection(sessions).last_success_at == success_at
    assert connection(sessions).sync_mode == "automatic"
    assert connection(sessions).next_run_at == clock.now + timedelta(minutes=5)
    assert {run.trigger for run in runs(sessions)} == {"automatic"}


def test_due_tick_recovers_expired_foreign_worker_lease(scheduler):
    sessions, transport, service, clock = scheduler
    saved = connection(sessions)
    with sessions() as session:
        session.add(JobMailSyncRun(
            id="expired-previous-process", connection_id=saved.id,
            scope_version=saved.scope_version, trigger="automatic", status="running",
            owner_token="previous-process", started_at=clock.now - timedelta(minutes=5),
            lease_until=clock.now - timedelta(seconds=1),
        ))
        session.commit()

    service.tick()
    assert runs(sessions)[0].status == "running"
    assert transport.read_calls == []
    clock.due(service)
    service.tick()
    status = drain(service)
    old, recovered = runs(sessions)
    assert old.status == "interrupted"
    assert old.error_code == "lease_expired"
    assert old.finished_at == clock.now
    assert recovered.id == status["run"]["id"]
    assert recovered.trigger == "automatic"
    assert recovered.status == "completed"
    assert transport.read_calls == ["INBOX"]


def test_due_tick_waits_for_expired_local_worker_before_recovery(scheduler, monkeypatch):
    sessions, transport, service, clock = scheduler
    clock.due(service)
    with blocked_read(transport, monkeypatch) as (entered, release):
        service.tick()
        assert entered.wait(timeout=5)
        first_id = service.status()["run"]["id"]
        clock.due(service)  # Both the interval and the blocked worker's lease expire.
        service.tick()
        assert len(runs(sessions)) == 1
        assert transport.read_calls == ["INBOX"]
        assert connection(sessions).next_run_at == clock.now
        release.set()
        assert drain(service)["run"]["status"] == "interrupted"

    service.tick()
    recovered = drain(service)["run"]
    assert recovered["id"] != first_id
    assert recovered["trigger"] == "automatic"
    assert recovered["status"] == "completed"
    old, _ = runs(sessions)
    assert old.status == "interrupted"
    assert old.error_code == "lease_expired"
    assert transport.read_calls == ["INBOX", "INBOX"]
