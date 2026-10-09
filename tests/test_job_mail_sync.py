import json
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from offerpilot.db import init_database
from offerpilot.job_mail.extraction import ParsedMail
from offerpilot.job_mail.models import JobMailConnection, JobMailSuggestion, JobMailSyncRun
from offerpilot.job_mail.review import JobMailError
from offerpilot.job_mail.sync import JobMailSyncService
from offerpilot.job_mail.transport import FakeIMAPTransport, TransportUnavailable


def mail(uid=1, folder="INBOX", generation="1", fingerprint=None):
    return ParsedMail(uid, generation, folder, f"<notice-{uid}@example.invalid>",
        datetime.now(timezone.utc).isoformat(), "hr@example.invalid", "面试邀请",
        "公司：合成公司\n岗位：测试\n面试2026-10-15T15:00:00+08:00\n时长：30分钟",
        fingerprint or str(uid))


@pytest.fixture
def setup(tmp_path):
    sessions = init_database(tmp_path / "test.db")
    transport = FakeIMAPTransport()
    service = JobMailSyncService(sessions, transport)
    yield sessions, transport, service
    service.shutdown()


def connect(service, folders=None):
    return service.connect({"provider": "synthetic", "folders": folders or ["INBOX"]})


def wait(service):
    for _ in range(200):
        result = service.status()
        if result["run"] and result["run"]["status"] != "running":
            return result
        time.sleep(.01)
    raise AssertionError("sync did not finish")


def unthrottle(sessions):
    with sessions() as session:
        connection = session.scalar(select(JobMailConnection))
        connection.not_before_at = None
        session.commit()


def test_connect_defaults_manual_no_reads_and_new_folders_not_included(setup):
    sessions, transport, service = setup
    transport.messages = [mail()]
    status = connect(service)
    assert transport.read_calls == []
    assert status["connection"]["sync_mode"] == "manual"
    transport.generations["Private"] = "1"
    transport.messages += [mail(2), mail(3, "Private")]
    service.tick()
    assert transport.read_calls == []
    service.start_sync()
    status = wait(service)
    assert status["run"]["status"] == "completed"
    assert transport.read_calls == ["INBOX"]
    assert status["run"]["progress"]["candidates"] == 1
    with sessions() as session:
        assert len(list(session.scalars(select(JobMailSuggestion)))) == 1


def test_manual_and_automatic_share_throttle_and_dedup(setup):
    sessions, transport, service = setup
    connect(service)
    transport.messages = [mail()]
    service.start_sync()
    wait(service)
    with pytest.raises(JobMailError) as err:
        service.start_sync()
    assert err.value.code == "sync_throttled"
    service.update_settings({"sync_mode": "automatic", "interval_minutes": 5})
    service.update_settings({"sync_mode": "manual", "interval_minutes": 5})
    assert service.status()["connection"]["next_run_at"] is None
    unthrottle(sessions)
    service.start_sync()
    assert wait(service)["run"]["progress"]["candidates"] == 0


def test_scope_change_disconnect_fences_running_result(setup):
    sessions, transport, service = setup
    connect(service)
    entered, release = threading.Event(), threading.Event()
    original = transport.read
    transport.messages = [mail()]
    def slow(*args, **kwargs):
        entered.set()
        release.wait(2)
        return original(*args, **kwargs)
    transport.read = slow
    first = service.start_sync()
    assert entered.wait(1)
    assert service.start_sync()["id"] == first["id"]
    service.disconnect()
    release.set()
    time.sleep(.1)
    assert service.status()["run"]["status"] == "cancelled"
    with sessions() as session:
        assert list(session.scalars(select(JobMailSuggestion))) == []


def test_turn_off_auto_does_not_cancel_current_manual_run(setup):
    sessions, transport, service = setup
    connect(service)
    service.update_settings({"sync_mode": "automatic", "interval_minutes": 15})
    entered, release = threading.Event(), threading.Event()
    original = transport.read
    def slow(*args, **kwargs):
        entered.set()
        release.wait(2)
        return original(*args, **kwargs)
    transport.read = slow
    service.start_sync()
    assert entered.wait(1)
    service.update_settings({"sync_mode": "manual", "interval_minutes": 15})
    assert service.status()["run"]["status"] == "running"
    release.set()
    assert wait(service)["run"]["status"] == "completed"


def test_budget_shared_and_cursor_does_not_skip_deferred(setup):
    sessions, transport, service = setup
    connect(service)
    transport.messages = [mail(1), mail(2)]
    with sessions() as session:
        connection = session.scalar(select(JobMailConnection))
        connection.budget_json = json.dumps({"day": datetime.now(timezone.utc).date().isoformat(), "used": 99})
        session.commit()
    service.start_sync()
    status = wait(service)
    assert status["budget"]["used"] == 100
    assert status["run"]["progress"]["deferred"] == 1
    with sessions() as session:
        connection = session.scalar(select(JobMailConnection))
        assert json.loads(connection.cursor_json)["INBOX"]["uid"] == 1
    service.tick()
    assert len(transport.read_calls) == 1


def test_uidvalidity_reset_rescans_bounded_and_dedups(setup):
    sessions, transport, service = setup
    connect(service)
    source = mail()
    transport.messages = [source]
    service.start_sync()
    wait(service)
    transport.generations["INBOX"] = "2"
    transport.messages = [replace(source, uidvalidity="2")]
    unthrottle(sessions)
    service.start_sync()
    assert wait(service)["run"]["progress"]["duplicates"] == 1


def test_three_failures_pause_automatic(setup):
    sessions, transport, service = setup
    connect(service)
    service.update_settings({"sync_mode": "automatic", "interval_minutes": 5})
    def fail(*a, **k):
        raise TransportUnavailable("private body must not leak")
    transport.read = fail
    for _ in range(3):
        unthrottle(sessions)
        service.start_sync()
        assert wait(service)["run"]["status"] == "failed"
    status = service.status()
    assert status["connection"]["sync_mode"] == "manual"
    assert status["connection"]["last_success_at"] is None
    assert "private body" not in str(status)


def test_remote_and_model_default_fail_closed(setup):
    sessions, _, _ = setup
    service = JobMailSyncService(sessions)
    assert not service.status()["capabilities"]["real_connection"]
    with pytest.raises(JobMailError):
        service.connect({"provider": "qq", "email": "test@qq.com", "folders": ["INBOX"]})
    service.shutdown()


def test_expired_lease_recovers_on_manual_click_only(setup):
    sessions, transport, service = setup
    connect(service)
    with sessions() as session:
        connection = session.scalar(select(JobMailConnection))
        run = JobMailSyncRun(id="dead-worker", connection_id=connection.id,
            scope_version=connection.scope_version, status="running", owner_token="dead",
            lease_until=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1))
        session.add(run)
        session.commit()
    service.tick()
    assert transport.read_calls == []
    service.start_sync()
    wait(service)
    with sessions() as session:
        assert session.get(JobMailSyncRun, "dead-worker").status == "interrupted"


def test_provider_work_does_not_lock_cancel_or_publish_late(setup):
    sessions, transport, service = setup
    entered, release = threading.Event(), threading.Event()
    class SlowExtractor:
        def extract(self, mail):
            from offerpilot.job_mail.extraction import recognize
            entered.set()
            release.wait(2)
            return recognize(mail)
    service.extractor = SlowExtractor()
    connect(service)
    transport.messages = [mail()]
    run = service.start_sync()
    assert entered.wait(1)
    started = time.monotonic()
    service.cancel_sync(run["id"])
    assert time.monotonic() - started < .5
    release.set()
    time.sleep(.1)
    with sessions() as session:
        assert list(session.scalars(select(JobMailSuggestion))) == []
    assert service.status()["budget"]["used"] == 1
    # A new explicit sync preserves unknown-result evidence, but cannot repeat
    # the provider call whose result may already have incurred a charge.
    class NoRetry:
        def extract(self, mail):
            raise AssertionError("unknown extraction was automatically replayed")
    service.extractor = NoRetry()
    unthrottle(sessions)
    service.start_sync()
    status = wait(service)
    assert status["budget"]["used"] == 1
    with sessions() as session:
        suggestions = list(session.scalars(select(JobMailSuggestion)))
        assert len(suggestions) == 1
        assert suggestions[0].status == "manual_required"


def test_unknown_provider_failure_preserves_source_and_budget(setup):
    sessions, transport, service = setup
    class FailExtractor:
        def extract(self, mail):
            raise TimeoutError("private provider output")
    service.extractor = FailExtractor()
    connect(service)
    transport.messages = [mail()]
    service.start_sync()
    status = wait(service)
    assert status["budget"]["used"] == 1
    assert status["run"]["progress"]["recognition_failed"] == 1
    assert "private provider" not in str(status)
    unthrottle(sessions)
    service.start_sync()
    assert wait(service)["budget"]["used"] == 1


def test_manual_paste_does_not_inherit_model_or_transmit_content(setup):
    sessions, transport, service = setup
    class NeverCall:
        def extract(self, mail):
            raise AssertionError("manual paste inherited provider")
    service.extractor = NeverCall()
    result = service.import_text({"body_text": "面试时间另行通知", "received_at": "2026-10-09T12:00:00Z"})
    assert result["items"][0]["status"] == "manual_required"


def test_cancelled_extraction_payload_has_bounded_retention(setup):
    from offerpilot.job_mail.models import JobMailExtractionAttempt
    sessions, transport, service = setup
    connect(service)
    with sessions() as session:
        connection = session.scalar(select(JobMailConnection))
        run = JobMailSyncRun(id="old-run", connection_id=connection.id, scope_version=1,
                            status="cancelled")
        session.add(run)
        session.flush()
        session.add(JobMailExtractionAttempt(id="old-attempt", scope_id=connection.scope_id,
            source_key="old-mail", connection_id=connection.id, run_id=run.id,
            payload_json='{"body_text":"private source"}', state="running",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=91)))
        session.commit()
    service.tick()
    with sessions() as session:
        attempt = session.get(JobMailExtractionAttempt, "old-attempt")
        assert attempt.payload_json == "{}"
        assert attempt.state == "unknown"


def test_empty_date_filtered_batch_advances_metadata_cursor(setup):
    sessions, transport, service = setup
    connect(service)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    transport.messages = [replace(mail(uid), received_at=(cutoff - timedelta(hours=1)).isoformat()) for uid in range(1, 51)]
    transport.messages.append(mail(51))
    with sessions() as session:
        connection = session.scalar(select(JobMailConnection))
        connection.start_at = cutoff.replace(tzinfo=None)
        session.commit()
    service.start_sync()
    first = wait(service)
    assert first["run"]["progress"]["deferred"] == 1
    with sessions() as session:
        connection = session.scalar(select(JobMailConnection))
        assert json.loads(connection.cursor_json)["INBOX"]["uid"] == 50
    unthrottle(sessions)
    service.start_sync()
    assert wait(service)["run"]["progress"]["candidates"] == 1


def test_cancelled_but_live_worker_does_not_spawn_another(setup):
    sessions, transport, service = setup
    entered, release = threading.Event(), threading.Event()
    class Blocked:
        def extract(self, mail):
            entered.set()
            release.wait(2)
            return []
    service.extractor = Blocked()
    connect(service)
    transport.messages = [mail()]
    run = service.start_sync()
    assert entered.wait(1)
    service.cancel_sync(run["id"])
    unthrottle(sessions)
    with pytest.raises(JobMailError) as error:
        service.start_sync()
    assert error.value.code == "worker_still_stopping"
    release.set()
