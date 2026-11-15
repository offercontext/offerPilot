"""Synthetic, content-free observations through real read and scheduled paths."""
import json
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest
from sqlalchemy import select

from offerpilot.db import init_database
from offerpilot.job_mail import sync as sync_module
from offerpilot.job_mail.diagnostics import ReadDiagnostics
from offerpilot.job_mail.models import JobMailConnection, JobMailSyncRun
from offerpilot.job_mail.sync import JobMailSyncService
from offerpilot.job_mail.transport import FakeIMAPTransport, QQIMAPTransport, TransportUnavailable

SENTINEL = "PRIVATE_SENTINEL_address-body-header-id-credential"
SINCE = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
UNTIL = datetime(2026, 10, 9, 14, tzinfo=timezone.utc)


class Protocol:
    def __init__(self):
        self.ids = b"1 2"
        self.validity = b"1"
        self.next_uid = b"3"
        self.commands = []
        self.failure = None
        self.dates = {"1": "09-Oct-2026 01:00:00 +0000", "2": "09-Oct-2026 13:00:00 +0000"}
        self.subject = "面试邀请"
        self.body = "公司：合成公司\n岗位：测试\n面试2026-10-15T15:00:00+08:00\n时长：30分钟"

    def select(self, *args, **kwargs):
        self.commands.append(("select", args, kwargs))
        if self.failure == "select":
            raise RuntimeError(SENTINEL)
        return "OK", [b"2"]

    def response(self, name):
        return name, [self.validity if name == "UIDVALIDITY" else self.next_uid]

    def uid(self, operation, *args):
        self.commands.append((operation, *args))
        if self.failure == operation:
            return "NO", [SENTINEL.encode()]
        if operation == "search":
            return "OK", [self.ids]
        uid = args[0]
        date = self.dates.get(uid, self.dates["2"])
        if args[1] == "(RFC822.SIZE INTERNALDATE)":
            if self.failure == "date":
                date = SENTINEL
            return "OK", [f'1 (RFC822.SIZE 500 INTERNALDATE "{date}")'.encode()]
        if self.failure == "body":
            return "NO", [SENTINEL.encode()]
        raw = EmailMessage()
        raw["Subject"] = self.subject
        raw["From"] = SENTINEL + "@example.invalid"
        raw["Message-ID"] = "<" + SENTINEL + ">"
        raw.set_content(self.body + "\n" + SENTINEL)
        return "OK", [(b"raw protocol " + SENTINEL.encode(), raw.as_bytes())]

    def logout(self):
        self.commands.append(("logout",))


@pytest.fixture
def transport(monkeypatch):
    protocol = Protocol()
    # Bypass network/login entirely, retaining actual SELECT/SEARCH/FETCH logic.
    instance = QQIMAPTransport("synthetic@qq.com", "fake", enabled=True, vault=object())
    monkeypatch.setattr(instance, "_open", lambda: protocol)
    return instance, protocol


def test_search_zero_and_already_read_are_distinct_observations(transport):
    instance, protocol = transport
    protocol.ids = b""
    empty = instance.read("INBOX", "1", 1, since=SINCE, until=UNTIL)
    assert empty.diagnostics.search_returned == 0
    assert empty.diagnostics.uid_filtered == 0
    protocol.ids = b"1"
    filtered = instance.read("INBOX", "1", 1, since=SINCE, until=UNTIL)
    assert filtered.diagnostics.search_returned == 1
    assert filtered.diagnostics.uid_filtered == 1
    assert empty.messages == filtered.messages == []
    assert empty.last_uid == filtered.last_uid == 1
    assert not any(command[0] == "fetch" for command in protocol.commands)


def test_dates_generation_and_limit_are_bounded_observations(transport):
    instance, protocol = transport
    protocol.validity = b"2"
    batch = instance.read("INBOX", "1", 99, since=SINCE, until=UNTIL)
    observation = batch.diagnostics.safe_dict()
    assert observation["uidvalidity_reset"] is True
    assert observation["effective_after_uid"] == 0
    assert observation["server_uidvalidity"] == 2
    assert observation["server_uidnext"] == 3
    assert observation["before_since"] == 1
    assert observation["body_fetched"] == observation["messages_returned"] == 1
    assert SENTINEL not in json.dumps(observation)
    assert batch.last_uid == 2 and len(batch.messages) == 1
    protocol.dates["2"] = "09-Oct-2026 15:00:00 +0000"
    batch = instance.read("INBOX", "2", 0, since=SINCE, until=UNTIL)
    assert batch.diagnostics.after_until_observed == 1
    assert batch.diagnostics.before_since == 1
    assert batch.messages == [] and batch.last_uid == 1 and batch.has_more
    protocol.ids = b" ".join(str(i).encode() for i in range(1, 102))
    limited = instance.read("INBOX", "2", 0, limit=1, since=SINCE, until=UNTIL)
    assert limited.diagnostics.batch_limit_deferred == 100
    assert len(json.dumps(limited.diagnostics.safe_dict())) < 700
    assert not any(isinstance(value, list) for value in limited.diagnostics.safe_dict().values())


@pytest.mark.parametrize(("failure", "stage", "code"), [
    ("select", "select", "mail_read_failed"),
    ("search", "search", "folder_search_failed"),
    ("fetch", "metadata", "message_metadata_failed"),
    ("date", "metadata", "mail_read_failed"),
    ("body", "body", "message_fetch_failed"),
])
def test_failures_only_keep_fixed_stage_code_and_observed_counts(transport, failure, stage, code):
    instance, protocol = transport
    protocol.failure = failure
    with pytest.raises(TransportUnavailable) as result:
        instance.read("INBOX", "1", 0, since=SINCE, until=UNTIL)
    observation = result.value.diagnostics.safe_dict()
    assert observation["stage"] == stage
    assert observation["error_code"] == code
    assert SENTINEL not in json.dumps(observation)
    assert SENTINEL not in str(result.value)
    assert observation["messages_returned"] is None
    if failure in {"select", "search"}:
        assert observation["search_returned"] is None
        assert observation["body_fetched"] is None
    assert protocol.commands[-1] == ("logout",)


def test_untrusted_diagnostic_fields_cannot_serialize_text():
    diagnostic = ReadDiagnostics()
    for key in vars(diagnostic):
        setattr(diagnostic, key, SENTINEL)
    diagnostic.extra_header = SENTINEL
    safe = diagnostic.safe_dict()
    assert SENTINEL not in json.dumps(safe)
    assert safe["stage"] == "read" and safe["error_code"] == "mail_read_failed"
    assert safe["search_returned"] is None


def drain(service):
    with service._thread_lock:
        workers = list(service._threads)
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    return service.status()["run"]


@pytest.mark.parametrize("automatic", [False, True])
def test_manual_and_actual_tick_persist_identical_read_contract(tmp_path, monkeypatch, transport, automatic):
    instance, protocol = transport
    sessions = init_database(tmp_path / "diagnostics.db")
    clock = SINCE.replace(tzinfo=None)
    monkeypatch.setattr(sync_module, "_now", lambda: clock)
    service = JobMailSyncService(sessions, FakeIMAPTransport())
    service.connect({"provider": "synthetic", "folders": ["INBOX"]})
    service.transport = instance
    try:
        with sessions() as session:
            connection = session.scalar(select(JobMailConnection))
            connection.start_at = SINCE.replace(tzinfo=None)
            connection.cursor_json = json.dumps({"INBOX": {"uidvalidity": "1", "uid": 1}})
            session.commit()
        if automatic:
            service.update_settings({"sync_mode": "automatic", "interval_minutes": 5})
        clock = UNTIL.replace(tzinfo=None)
        # Cursor 1, server returns only UID 1: observed filtering, no root-cause guess.
        protocol.ids = b"1"
        if automatic:
            service.tick()
        else:
            service.start_sync()
        run = drain(service)
        assert run["trigger"] == ("automatic" if automatic else "manual")
        assert run["status"] == "completed"
        assert run["progress"]["scanned"] == 0
        record = run["progress"]["read_diagnostics"][0]
        assert record["since_utc"] == SINCE.isoformat()
        assert record["until_utc"] == UNTIL.isoformat()
        assert record["cursor_before"] == record["cursor_after"] == 1
        assert record["read"]["uid_filtered"] == 1
        assert record["read"]["search_returned"] == 1
        assert protocol.commands == [
            ("select", ('"INBOX"',), {"readonly": True}),
            ("search", "UID", "2:*", "SINCE", "09-Oct-2026"), ("logout",),
        ]
        with sessions() as session:
            stored = session.scalar(select(JobMailSyncRun))
            assert json.loads(stored.progress_json) == run["progress"]
        assert SENTINEL not in json.dumps(run["progress"])
    finally:
        service.shutdown()


@pytest.mark.parametrize("case", ["not_job_related", "budget_deferred", "duplicates", "failed"])
def test_zero_candidates_distinguish_processing_and_failure(tmp_path, monkeypatch, transport, case):
    instance, protocol = transport
    sessions = init_database(tmp_path / "outcomes.db")
    clock = UNTIL.replace(tzinfo=None)
    monkeypatch.setattr(sync_module, "_now", lambda: clock)
    service = JobMailSyncService(sessions, FakeIMAPTransport())
    service.connect({"provider": "synthetic", "folders": ["INBOX"]})
    service.transport = instance
    protocol.ids = b"2"
    if case == "not_job_related":
        protocol.subject = "daily newsletter"
        protocol.body = "ordinary synthetic text"
    if case == "budget_deferred":
        monkeypatch.setattr(service, "_budget", lambda _: {"used": 100})
    if case == "failed":
        protocol.failure = "search"
    try:
        with sessions() as session:
            connection = session.scalar(select(JobMailConnection))
            connection.start_at = SINCE.replace(tzinfo=None)
            session.commit()
        service.start_sync()
        run = drain(service)
        if case == "duplicates":
            assert run["progress"]["candidates"] == 1
            with sessions() as session:
                connection = session.scalar(select(JobMailConnection))
                connection.cursor_json = json.dumps({"INBOX": {"uidvalidity": "1", "uid": 0}})
                connection.not_before_at = None
                session.commit()
            clock += timedelta(minutes=1)
            service.start_sync()
            run = drain(service)
        assert run["progress"]["candidates"] == 0
        record = run["progress"]["read_diagnostics"][0]
        if case == "failed":
            assert run["status"] == "failed"
            assert record["read"]["stage"] == "search"
            assert record["read"]["error_code"] == "folder_search_failed"
            assert record["read"]["search_returned"] is None
        else:
            assert run["status"] == "completed"
            assert record["read"]["messages_returned"] == 1
            assert record["outcomes"][case] == 1
            assert record["outcomes"]["scanned"] == int(case != "budget_deferred")
            assert record["cursor_after"] == (0 if case == "budget_deferred" else 2)
        assert SENTINEL not in json.dumps(run["progress"])
    finally:
        service.shutdown()


def test_connection_failure_records_connect_without_provider_text(transport, monkeypatch):
    instance, _ = transport
    def fail():
        raise TransportUnavailable(SENTINEL)
    monkeypatch.setattr(instance, "_open", fail)
    with pytest.raises(TransportUnavailable) as result:
        instance.read("INBOX", "1", 0)
    assert result.value.diagnostics.stage == "connect"
    assert result.value.diagnostics.error_code == "mail_read_failed"
    assert result.value.diagnostics.search_returned is None
    assert SENTINEL not in str(result.value)


def test_parse_failure_does_not_leak_fetched_body(transport, monkeypatch):
    instance, _ = transport
    def fail(*args, **kwargs):
        raise ValueError(SENTINEL)
    monkeypatch.setattr("offerpilot.job_mail.transport.parse_mail", fail)
    with pytest.raises(TransportUnavailable) as result:
        instance.read("INBOX", "1", 0, since=SINCE, until=UNTIL)
    observation = result.value.diagnostics.safe_dict()
    assert observation["stage"] == "parse"
    assert observation["body_fetched"] == 1
    assert observation["messages_returned"] is None
    assert SENTINEL not in json.dumps(observation)


def test_untrusted_batch_diagnostics_do_not_change_success(tmp_path, monkeypatch):
    from dataclasses import replace
    sessions = init_database(tmp_path / "untrusted.db")
    transport = FakeIMAPTransport()
    service = JobMailSyncService(sessions, transport)
    service.connect({"provider": "synthetic", "folders": ["INBOX"]})
    original = transport.read
    diagnostic = ReadDiagnostics()
    for field in vars(diagnostic):
        setattr(diagnostic, field, SENTINEL)
    diagnostic.safe_dict = lambda: (_ for _ in ()).throw(ValueError(SENTINEL))
    monkeypatch.setattr(transport, "read", lambda *args, **kwargs:
                        replace(original(*args, **kwargs), diagnostics=diagnostic))
    try:
        service.start_sync()
        run = drain(service)
        assert run["status"] == "completed"
        assert SENTINEL not in json.dumps(run["progress"])
        assert run["progress"]["read_diagnostics"][0]["read"]["search_returned"] is None
    finally:
        service.shutdown()


def test_diagnostic_numeric_bounds_preserve_zero_and_unknown():
    from offerpilot.job_mail.diagnostics import number, uid_number, utc
    for value in (None, False, True, -1, 0x100000000, SENTINEL, [], {}):
        assert number(value) is None
    assert number(0) == 0
    assert number(0xFFFFFFFF) == 0xFFFFFFFF
    assert uid_number("0") == 0
    assert uid_number("4294967295") == 0xFFFFFFFF
    for value in ("4294967296", "1" * 100, "１２３", SENTINEL):
        assert uid_number(value) is None
    assert utc(SENTINEL) is None
    assert utc(SINCE.replace(tzinfo=None)) == SINCE.isoformat()
    assert ReadDiagnostics().safe_dict()["search_returned"] is None
    assert ReadDiagnostics(search_returned=0).safe_dict()["search_returned"] == 0


def test_corrupt_legacy_folder_count_cannot_expand_diagnostic_storage(tmp_path):
    sessions = init_database(tmp_path / "bounded.db")
    transport = FakeIMAPTransport()
    service = JobMailSyncService(sessions, transport)
    service.connect({"provider": "synthetic", "folders": ["INBOX"]})
    # Settings validate <=20. Inject invalid legacy state without changing the
    # production folder selection algorithm: all 21 still read, only 20 records.
    folders = [f"synthetic-{i}" for i in range(21)]
    transport.generations.update({folder: "1" for folder in folders})
    with sessions() as session:
        connection = session.scalar(select(JobMailConnection))
        connection.folders_json = json.dumps(folders)
        session.commit()
    try:
        service.start_sync()
        run = drain(service)
        assert run["status"] == "completed"
        assert transport.read_calls == folders
        observations = run["progress"]["read_diagnostics"]
        assert len(observations) == 20
        assert [entry["folder_index"] for entry in observations] == list(range(20))
        assert "synthetic-" not in json.dumps(observations)
    finally:
        service.shutdown()
