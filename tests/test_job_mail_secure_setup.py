"""All setup/credential/QQ traffic is synthetic; never contact a real vault or server."""
import asyncio
import json
import threading
from datetime import timedelta
from email.message import EmailMessage
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request
from typer.testing import CliRunner

from offerpilot.db import init_database
from offerpilot.job_mail.api import register_job_mail_routes
from offerpilot.job_mail.models import JobMailConnection, JobMailCredentialOperation, JobMailSuggestion
from offerpilot.job_mail.review import JobMailError, utcnow, workspace_id
from offerpilot.job_mail.secure_setup import (
    COOKIE, MAX_SETUP_BYTES, DeleteInput, JobMailSecureSetup, SaveInput,
    protected_payload,
)
from offerpilot.job_mail.sync import JobMailSyncService
from offerpilot.job_mail.transport import DisabledTransport, QQIMAPTransport, parse_folder_list

SECRET = "synthetic-private-code-no-real-credential"
ORIGIN = "http://127.0.0.1:8080"
FOLDERS = [parse_folder_list(b'(\\HasNoChildren) "/" "INBOX"'),
           parse_folder_list(b'(\\Noselect) NIL "Parent"'),
           parse_folder_list(b'(\\Sent) "/" "Sent"')]


class Vault:
    backend_name = "synthetic-native-vault"

    def __init__(self):
        self.values = {}
        self.calls = []
        self.fail_delete = False
        self.after_set = None

    def get(self, reference):
        self.calls.append(("get", reference))
        return self.values[reference]

    def set(self, reference, secret):
        self.calls.append(("set", reference))
        self.values[reference] = secret
        if self.after_set:
            self.after_set()

    def delete(self, reference):
        self.calls.append(("delete", reference))
        if self.fail_delete:
            raise RuntimeError(SECRET)
        self.values.pop(reference, None)


class SetupTransport(QQIMAPTransport):
    instances = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = []
        self.instances.append(self)

    def discover(self):
        self._check_cancelled()
        assert self._vault.get(self._reference) == SECRET
        self.calls.append("list")
        return FOLDERS

    def baseline(self, folder):
        self._check_cancelled()
        self.calls.append(("baseline", folder))
        return "42", 9

    def read(self, *args, **kwargs):
        raise AssertionError("Setup/recovery must never read mail")


@pytest.fixture
def setup(tmp_path):
    sessions = init_database(tmp_path / "mail.db")
    app = FastAPI()
    runtime = register_job_mail_routes(app, sessions, local_setup_enabled=True)
    secure = app.state.job_mail_secure_setup
    vault = Vault()
    secure.vault_factory = lambda: vault
    secure.transport_factory = SetupTransport
    SetupTransport.instances = []
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 12345)) as client:
        yield SimpleNamespace(app=app, client=client, secure=secure, vault=vault,
                              service=runtime.service, sessions=sessions, tmp_path=tmp_path)
    runtime.stop()
    sessions.kw["bind"].dispose()


def post(setup, step, **payload):
    return setup.client.post("/api/job-mail/secure-setup/" + step,
                             headers={"Origin": ORIGIN}, json=payload)


def start(setup, purpose="connect"):
    response = post(setup, "start", explicit_setup_consent=True, purpose=purpose)
    assert response.status_code == 200, response.text
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    return response.json()["setup_token"]


def prepare_tested_session(setup):
    token = start(setup)
    response = post(setup, "test", setup_token=token, email="synthetic@qq.com",
                    authorization_code=SECRET, explicit_test_consent=True)
    assert response.status_code == 200, response.text
    assert SECRET not in response.text
    return token


def saved(setup):
    token = prepare_tested_session(setup)
    response = post(setup, "save", setup_token=token, folder_ids=[FOLDERS[0]["id"]],
                    explicit_save_consent=True)
    assert response.status_code == 200, response.text
    return response


def disconnect(setup):
    token = start(setup, "disconnect")
    return setup.client.post("/api/job-mail/disconnect", headers={"Origin": ORIGIN},
                            json={"setup_token": token, "explicit_delete_consent": True})


def test_three_steps_require_consent_and_cookie_and_never_fetch_body(setup):
    capability = setup.client.get("/api/job-mail/secure-setup/status").json()
    assert capability["credential_input_allowed"]
    assert setup.vault.calls == []
    assert post(setup, "start", explicit_setup_consent=False).status_code == 422
    token = prepare_tested_session(setup)
    assert SetupTransport.instances[0].calls == ["list"]
    assert setup.vault.calls == []
    other = TestClient(setup.app, base_url=ORIGIN, client=("127.0.0.1", 23456))
    bad = other.post("/api/job-mail/secure-setup/save", headers={"Origin": ORIGIN}, json={
        "setup_token": token, "folder_ids": [FOLDERS[0]["id"]], "explicit_save_consent": True})
    assert bad.status_code == 410
    assert post(setup, "save", setup_token=token, folder_ids=[FOLDERS[0]["id"]],
                explicit_save_consent=False).status_code == 422
    response = post(setup, "save", setup_token=token, folder_ids=[FOLDERS[0]["id"]],
                    explicit_save_consent=True)
    assert response.status_code == 200
    connection = response.json()["connection"]
    assert connection["sync_mode"] == "manual"
    assert connection["next_run_at"] is None
    assert connection["ai_enabled"] is False
    assert connection["folders"] == ["INBOX"]
    assert SetupTransport.instances[0].calls == ["list", ("baseline", "INBOX")]
    assert SECRET not in response.text
    assert COOKIE not in setup.client.cookies
    with setup.sessions() as session:
        row = session.scalar(select(JobMailConnection))
        assert json.loads(row.cursor_json)["INBOX"] == {"uidvalidity": "42", "uid": 9}
        op = session.scalar(select(JobMailCredentialOperation))
        assert op.state == "committed"
        assert op.credential_ref == row.credential_ref
    assert SECRET.encode() not in (setup.tmp_path / "mail.db").read_bytes()
    assert not setup.secure._sessions
    assert SetupTransport.instances[0]._cancelled.is_set()
    setup.service.tick()


@pytest.mark.parametrize("selection", [["INBOX"], [FOLDERS[1]["id"]], ["imap:forged"]])
def test_selection_is_bound_to_discovery_catalog(setup, selection):
    token = prepare_tested_session(setup)
    response = post(setup, "save", setup_token=token, folder_ids=selection, explicit_save_consent=True)
    assert response.status_code == 422
    assert setup.vault.calls == []


@pytest.mark.parametrize("headers", [
    {}, {"Origin": "http://evil.invalid"}, {"Origin": "null"},
    {"Origin": ORIGIN, "Forwarded": "for=127.0.0.1"},
    {"Origin": ORIGIN, "X-Forwarded-For": "127.0.0.1"},
    {"Origin": ORIGIN, "Host": "evil.invalid"},
])
def test_local_browser_guard_precedes_secret_input(setup, headers):
    response = setup.client.post("/api/job-mail/secure-setup/start", headers=headers,
                                 content=SECRET)
    assert response.status_code == 403
    assert SECRET not in response.text
    assert setup.vault.calls == []


def test_remote_client_and_host_are_rejected(setup):
    client = TestClient(setup.app, base_url=ORIGIN, client=("10.0.0.7", 12345))
    assert not client.get("/api/job-mail/secure-setup/status").json()["available"]
    assert client.post("/api/job-mail/secure-setup/start", headers={"Origin": ORIGIN},
                       json={"explicit_setup_consent": True}).status_code == 403


def test_chunked_body_limited_before_json_and_auth_guard_does_not_read_bytes():
    async def invoke(headers, chunks):
        reads = []
        async def receive():
            reads.append(True)
            body = chunks.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(chunks)}
        request = Request({"type": "http", "method": "POST", "path": "/api/job-mail/secure-setup/test",
                           "scheme": "http", "server": ("127.0.0.1", 8080), "client": ("127.0.0.1", 1),
                           "query_string": b"", "headers": headers}, receive)
        with pytest.raises(JobMailError) as error:
            await protected_payload(request)
        return error.value.status, len(reads)
    common = [(b"host", b"127.0.0.1:8080"), (b"content-type", b"application/json")]
    assert asyncio.run(invoke(common, [SECRET.encode()])) == (403, 0)
    local = common + [(b"origin", ORIGIN.encode())]
    assert asyncio.run(invoke(local, [b"a" * 4096, b"b" * (MAX_SETUP_BYTES - 4095)])) == (413, 2)
    assert asyncio.run(invoke(local + [(b"content-length", b"999999")], [b"x"])) == (413, 0)


def test_cancel_expiry_and_replacement_clear_temporary_secrets(setup):
    token = prepare_tested_session(setup)
    entry = setup.secure._sessions[token]
    transport = entry.transport
    assert post(setup, "cancel", setup_token=token).json() == {"cancelled": True}
    assert entry.secret is None and transport._cancelled.is_set()
    token = prepare_tested_session(setup)
    entry = setup.secure._sessions[token]
    start(setup)
    assert entry.secret is None and token not in setup.secure._sessions
    token = prepare_tested_session(setup)
    entry = setup.secure._sessions[token]
    setup.secure.cancel_expired(token)
    assert entry.secret is None and not setup.secure._sessions
    assert post(setup, "save", setup_token=token, folder_ids=[FOLDERS[0]["id"]],
                explicit_save_consent=True).status_code == 410
    assert setup.vault.calls == []


def test_save_failure_keeps_durable_cleanup_reference_and_manual_retry(setup):
    token = prepare_tested_session(setup)
    setup.vault.fail_delete = True
    setup.vault.after_set = lambda: setup.secure.cancel_expired(token)
    response = post(setup, "save", setup_token=token, folder_ids=[FOLDERS[0]["id"]], explicit_save_consent=True)
    assert response.status_code == 409
    assert SECRET not in response.text
    with setup.sessions() as session:
        operation = session.scalar(select(JobMailCredentialOperation))
        assert operation.state == "delete_pending"
        assert operation.credential_ref in setup.vault.values
        assert session.scalar(select(JobMailConnection)) is None
    assert setup.client.get("/api/job-mail/secure-setup/status").json()["deletion_pending"]
    assert post(setup, "start", explicit_setup_consent=True).status_code == 409
    setup.vault.fail_delete = False
    response = disconnect(setup)
    assert response.status_code == 200
    assert setup.vault.values == {}
    assert not setup.client.get("/api/job-mail/secure-setup/status").json()["deletion_pending"]


def test_disconnect_failure_stops_auto_keeps_reference_and_retries(setup):
    saved(setup)
    setup.service.update_settings({"sync_mode": "automatic", "interval_minutes": 5})
    real_transport = setup.service.transport
    setup.vault.fail_delete = True
    assert disconnect(setup).status_code == 409
    assert real_transport._cancelled.is_set()
    assert isinstance(setup.service.transport, DisabledTransport)
    with setup.sessions() as session:
        connection = session.scalar(select(JobMailConnection))
        reference = connection.credential_ref
        assert connection.status == "credential_delete_pending"
        assert connection.sync_mode == "manual" and connection.next_run_at is None
        assert reference in setup.vault.values
    setup.vault.fail_delete = False
    assert disconnect(setup).status_code == 200
    with setup.sessions() as session:
        connection = session.scalar(select(JobMailConnection))
        assert connection.status == "disconnected" and connection.credential_ref is None


def test_recovery_is_local_and_default_disabled_even_with_saved_automatic(setup):
    saved(setup)
    setup.service.update_settings({"sync_mode": "automatic", "interval_minutes": 5})
    with setup.sessions() as session:
        connection = session.scalar(select(JobMailConnection))
        connection.next_run_at = utcnow() - timedelta(seconds=1)
        session.add(JobMailCredentialOperation(id="interrupted-write", scope_id=workspace_id(session),
                    credential_ref="opaque-crash-reference", state="storing"))
        session.commit()
    def forbidden():
        raise AssertionError("disabled startup must not touch vault")
    service = JobMailSyncService(setup.sessions)
    secure = JobMailSecureSetup(service, vault_factory=forbidden)
    service.tick()
    assert isinstance(service.transport, DisabledTransport)
    assert service.status()["run"] is None
    assert not service.status()["capabilities"]["real_connection"]
    assert "不会读取邮件" in service.status()["notice"]
    with pytest.raises(JobMailError):
        service.start_sync()
    with setup.sessions() as session:
        assert session.get(JobMailCredentialOperation, "interrupted-write").state == "storing"
    secure.shutdown()
    service.shutdown()


def test_enabled_restart_does_not_replay_manual_sync_or_vault_writes(setup):
    saved(setup)
    setup.secure.shutdown()
    calls = list(setup.vault.calls)
    service = JobMailSyncService(setup.sessions)
    secure = JobMailSecureSetup(service, enabled=True, vault_factory=lambda: setup.vault,
                                transport_factory=SetupTransport)
    assert isinstance(service.transport, SetupTransport)
    service.tick()
    assert service.status()["run"] is None
    assert setup.vault.calls == calls
    secure.shutdown()
    service.shutdown()


def test_disabled_app_never_admits_setup_body_or_env_override(tmp_path, monkeypatch):
    from offerpilot.api import create_app
    monkeypatch.setenv("OFFERPILOT_ENABLE_LOCAL_MAIL_SETUP", "true")
    with TestClient(create_app(data_dir=tmp_path), base_url=ORIGIN,
                    client=("127.0.0.1", 12345)) as client:
        assert client.get("/api/job-mail/secure-setup/status").json()["reason"] == "local_setup_not_enabled"
        response = client.post("/api/job-mail/secure-setup/start", headers={"Origin": ORIGIN}, content=SECRET)
        assert response.status_code == 409 and SECRET not in response.text


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "example.invalid", "::"])
def test_cli_rejects_nonliteral_loopback_when_enabled(tmp_path, monkeypatch, host):
    from offerpilot.cli import app
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    calls = []
    monkeypatch.setattr("offerpilot.cli.uvicorn.run", lambda *a, **k: calls.append(k))
    result = CliRunner().invoke(app, ["start", "--enable-local-mail-setup", "--host", host])
    assert result.exit_code != 0 and not calls


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_cli_explicit_enable_disables_proxy_headers(tmp_path, monkeypatch, host):
    from offerpilot.cli import app
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    calls, apps = [], []
    monkeypatch.setattr("offerpilot.cli.uvicorn.run", lambda *a, **k: calls.append(k))
    monkeypatch.setattr("offerpilot.cli.create_app", lambda **k: apps.append(k))
    result = CliRunner().invoke(app, ["start", "--enable-local-mail-setup", "--host", host])
    assert result.exit_code == 0, result.output
    assert calls == [{"host": host, "port": 8080, "proxy_headers": False}]
    assert apps[0]["job_mail_local_setup_enabled"] is True


@pytest.mark.parametrize("vault_unavailable", [False, True])
def test_disconnect_during_first_body_closes_session_and_never_reads_next_or_publishes(setup, monkeypatch, vault_unavailable):
    saved(setup)
    entered, release = threading.Event(), threading.Event()
    protocols = []

    class BlockingProtocol:
        def __init__(self, *args, **kwargs):
            self.commands = []
            self.stopped = False
            protocols.append(self)

        def login(self, address, secret):
            assert secret == SECRET

        def select(self, folder, readonly):
            assert readonly
            return "OK", [b"2"]

        def response(self, name):
            return name, [b"42" if name == "UIDVALIDITY" else b"12"]

        def uid(self, command, *args):
            self.commands.append((command, *args))
            if command == "search":
                return "OK", [b"10 11"]
            date = (utcnow() - timedelta(seconds=1)).strftime("%d-%b-%Y %H:%M:%S +0000").encode()
            if args[1] == "(RFC822.SIZE INTERNALDATE)":
                return "OK", [b'10 (RFC822.SIZE 500 INTERNALDATE "' + date + b'")']
            entered.set()
            assert release.wait(5)
            raw = EmailMessage()
            raw["Subject"] = "面试邀请"
            raw.set_content("面试时间另行通知")
            return "OK", [(b'10 (INTERNALDATE "' + date + b'")', raw.as_bytes())]

        def shutdown(self):
            self.stopped = True

        def logout(self):
            pass

    monkeypatch.setattr("imaplib.IMAP4_SSL", BlockingProtocol)
    with setup.sessions() as session:
        connection = session.scalar(select(JobMailConnection))
        connection.start_at = utcnow() - timedelta(days=1)
        reference = connection.credential_ref
        session.commit()
    setup.service.transport = QQIMAPTransport("synthetic@qq.com", reference, enabled=True, vault=setup.vault)
    setup.service.update_settings({"sync_mode": "automatic", "interval_minutes": 5})
    run = setup.service.start_sync()
    assert entered.wait(2)
    if vault_unavailable:
        def unavailable():
            raise RuntimeError(SECRET)
        setup.secure.vault_factory = unavailable
    try:
        result = disconnect(setup)
        assert result.status_code == (409 if vault_unavailable else 200), result.text
        assert protocols[0].stopped
        assert setup.service.status()["connection"]["sync_mode"] == "manual"
        assert setup.service.status()["connection"]["next_run_at"] is None
    finally:
        release.set()
        for worker in setup.service._threads:
            worker.join(3)
    assert setup.service.status()["run"]["id"] == run["id"]
    assert setup.service.status()["run"]["status"] == "cancelled"
    assert not any(command[0] == "fetch" and command[1] == "11" for command in protocols[0].commands)
    with setup.sessions() as session:
        assert list(session.scalars(select(JobMailSuggestion))) == []
        if vault_unavailable:
            connection = session.scalar(select(JobMailConnection))
            assert connection.credential_ref == reference
            assert connection.status == "credential_delete_pending"
            assert session.scalar(select(JobMailCredentialOperation)).state == "delete_pending"


@pytest.mark.parametrize("control", ["\r", "\n", "\0", "\x7f"])
def test_secret_control_characters_never_reach_transport(setup, control):
    token = start(setup)
    response = post(setup, "test", setup_token=token, email="synthetic@qq.com",
                    authorization_code=SECRET + control, explicit_test_consent=True)
    assert response.status_code == 422 and SECRET not in response.text
    assert SetupTransport.instances == []


def test_vault_lost_at_save_clears_secret_immediately(setup):
    token = prepare_tested_session(setup)
    entry = setup.secure._sessions[token]
    def unavailable():
        raise RuntimeError(SECRET)
    setup.secure.vault_factory = unavailable
    response = post(setup, "save", setup_token=token, folder_ids=[FOLDERS[0]["id"]], explicit_save_consent=True)
    assert response.status_code == 409
    assert entry.secret is None and not setup.secure._sessions
    assert SECRET not in response.text


def test_disconnect_waits_for_inflight_vault_write_before_declaring_deleted(setup):
    token = prepare_tested_session(setup)
    cookie = setup.client.cookies[COOKIE]
    entered, release, fenced = threading.Event(), threading.Event(), threading.Event()
    results = []
    original_set = setup.vault.set
    def blocked_set(reference, secret):
        entered.set()
        assert release.wait(5)
        original_set(reference, secret)
    setup.vault.set = blocked_set
    def save_worker():
        try:
            setup.secure.save(SaveInput(setup_token=token, folder_ids=[FOLDERS[0]["id"]],
                                        explicit_save_consent=True), cookie)
        except JobMailError as error:
            results.append(error.code)
    saver = threading.Thread(target=save_worker)
    saver.start()
    assert entered.wait(2)
    deletion_token = setup.secure.start(cookie="delete-cookie", purpose="disconnect", consent=True)["setup_token"]
    original_cleanup = setup.secure._cleanup_reference
    def observed_cleanup(*args):
        fenced.set()
        return original_cleanup(*args)
    setup.secure._cleanup_reference = observed_cleanup
    def delete_worker():
        results.append(setup.secure.disconnect(DeleteInput(setup_token=deletion_token,
            explicit_delete_consent=True), "delete-cookie"))
    deleter = threading.Thread(target=delete_worker)
    deleter.start()
    assert fenced.wait(2)
    with setup.sessions() as session:
        operation = session.scalar(select(JobMailCredentialOperation))
        assert operation.state == "deleting"
        assert operation.credential_ref
    assert setup.vault.calls == []  # delete cannot race ahead of the blocked write
    release.set()
    saver.join(3)
    deleter.join(3)
    assert not saver.is_alive() and not deleter.is_alive()
    assert setup.vault.values == {}
    assert "secure_credential_write_failed" in results
    with setup.sessions() as session:
        assert session.scalar(select(JobMailCredentialOperation)).state == "deleted"
        assert session.scalar(select(JobMailConnection)) is None


def test_replaying_cancelled_run_does_not_cancel_new_transport(setup):
    from offerpilot.job_mail.models import JobMailSyncRun
    saved(setup)
    with setup.sessions() as session:
        connection = session.scalar(select(JobMailConnection))
        session.add(JobMailSyncRun(id="old", connection_id=connection.id,
                                  scope_version=connection.scope_version, status="cancelled"))
        session.commit()
    current = setup.service.transport
    setup.service.cancel_sync("old")
    assert setup.service.transport is current
    assert not current._cancelled.is_set()


@pytest.mark.parametrize("path,method", [("suggestions", "get"), ("status", "get"), ("sync", "post")])
def test_enabled_app_rejects_dns_rebinding_even_when_origin_matches_host(tmp_path, path, method):
    from offerpilot.api import create_app
    with TestClient(create_app(data_dir=tmp_path, job_mail_local_setup_enabled=True),
                    base_url="http://evil.example:8080", client=("127.0.0.1", 12345)) as client:
        response = client.request(method, "/api/job-mail/" + path, headers={"Origin": "http://evil.example:8080"})
        assert response.status_code == 403
        assert response.json()["error_code"] == "local_browser_required"


def test_existing_0033_database_gets_credential_intent_table_without_data_reset(tmp_path):
    from sqlalchemy import inspect, text
    path = tmp_path / "existing.db"
    sessions = init_database(path)
    with sessions() as session:
        scope = workspace_id(session)
        session.add(JobMailConnection(id="existing", scope_id=scope, provider="synthetic", status="disconnected"))
        session.commit()
    engine = sessions.kw["bind"]
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT 1 FROM schema_migrations WHERE version='0033_job_mail_review'")) == 1
        connection.execute(text("DROP TABLE job_mail_credential_operations"))
    engine.dispose()
    restored = init_database(path)
    engine = restored.kw["bind"]
    assert "job_mail_credential_operations" in inspect(engine).get_table_names()
    with restored() as session:
        assert session.get(JobMailConnection, "existing").scope_id == scope
        session.add(JobMailCredentialOperation(id="new-intent", scope_id=scope,
            credential_ref="opaque-upgrade-ref", state="delete_pending"))
        session.commit()
    engine.dispose()


def test_cancel_between_committed_scope_fence_and_read_cannot_use_rebuilt_transport(setup):
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from offerpilot.job_mail.transport import FolderBatch
    saved(setup)
    paused, release = threading.Event(), threading.Event()
    reads = []
    once = False

    class ObservedTransport(SetupTransport):
        def read(self, *args, **kwargs):
            self._check_cancelled()
            reads.append(self)
            return FolderBatch("42", [], 9, False)

    with setup.sessions() as session:
        reference = session.scalar(select(JobMailConnection)).credential_ref
    original = ObservedTransport("synthetic@qq.com", reference, enabled=True, vault=setup.vault)
    setup.service.transport = original
    setup.secure.transport_factory = ObservedTransport

    def after_commit(session):
        nonlocal once
        if threading.current_thread().name == "offerpilot-mail-sync" and not once:
            once = True
            paused.set()
            assert release.wait(5)
    event.listen(Session, "after_commit", after_commit)
    try:
        run = setup.service.start_sync()
        assert paused.wait(2)
        setup.service.cancel_sync(run["id"])
        replacement = setup.service.transport
        assert replacement is not original
        assert original._cancelled.is_set() and not replacement._cancelled.is_set()
    finally:
        release.set()
        for worker in setup.service._threads:
            worker.join(3)
        event.remove(Session, "after_commit", after_commit)
    assert reads == []
    assert setup.service.status()["run"]["status"] == "cancelled"


def test_second_runtime_cannot_recover_or_control_first_owners_connection(setup):
    from offerpilot.job_mail.models import JobMailSyncRun
    saved(setup)
    with setup.sessions() as session:
        connection = session.scalar(select(JobMailConnection))
        session.add(JobMailSyncRun(id="live-owner", connection_id=connection.id,
            scope_version=connection.scope_version, status="running", owner_token=setup.service.owner,
            lease_until=utcnow() + timedelta(seconds=90)))
        session.add(JobMailCredentialOperation(id="live-write", scope_id=connection.scope_id,
            credential_ref="opaque-live-write", state="storing"))
        session.commit()
    def forbidden():
        raise AssertionError("non-owner cannot access native vault")
    second = JobMailSyncService(setup.sessions)
    secure = JobMailSecureSetup(second, enabled=True, vault_factory=forbidden)
    assert not secure.enabled and secure.status(local=True)["reason"] == "local_setup_in_use"
    for action in [lambda: secure.start(cookie="cookie", purpose="disconnect", consent=True),
                   lambda: second.update_settings({"sync_mode": "automatic"}),
                   lambda: second.cancel_sync("live-owner"), lambda: second.start_sync()]:
        with pytest.raises(JobMailError):
            action()
    secure.recover()
    with setup.sessions() as session:
        assert session.get(JobMailCredentialOperation, "live-write").state == "storing"
        assert session.get(JobMailSyncRun, "live-owner").status == "running"
    secure.shutdown()
    second.shutdown()


def test_shutdown_keeps_owner_lock_until_late_vault_write_is_compensated(setup):
    from offerpilot.job_mail.process_lock import LocalMailProcessLock
    token = prepare_tested_session(setup)
    cookie = setup.client.cookies[COOKIE]
    entered, release = threading.Event(), threading.Event()
    original_set = setup.vault.set
    def blocked_set(reference, secret):
        entered.set()
        assert release.wait(5)
        original_set(reference, secret)
    setup.vault.set = blocked_set
    results = []
    def save_worker():
        try:
            setup.secure.save(SaveInput(setup_token=token, folder_ids=[FOLDERS[0]["id"]],
                                        explicit_save_consent=True), cookie)
        except JobMailError as error:
            results.append(error.code)
    worker = threading.Thread(target=save_worker)
    worker.start()
    assert entered.wait(2)
    setup.secure.shutdown()
    database = str(setup.tmp_path / "mail.db")
    assert LocalMailProcessLock.acquire(database) is None
    release.set()
    worker.join(3)
    assert not worker.is_alive() and results == ["secure_credential_write_failed"]
    assert setup.vault.values == {}
    recovered = LocalMailProcessLock.acquire(database)
    assert recovered is not None
    recovered.close()


def test_historical_real_mail_keeps_local_host_guard_after_disabled_restart(tmp_path):
    from offerpilot.api import create_app
    sessions = init_database(tmp_path / "data.db")
    with sessions() as session:
        session.add(JobMailConnection(id="former-qq", scope_id=workspace_id(session),
                                      provider="qq", status="disconnected"))
        session.commit()
    sessions.kw["bind"].dispose()
    with TestClient(create_app(data_dir=tmp_path), base_url="http://evil.example",
                    client=("127.0.0.1", 12345)) as client:
        response = client.get("/api/job-mail/suggestions", headers={"Origin": "http://evil.example"})
        assert response.status_code == 403


def test_earlier_observer_locks_down_when_another_runtime_saves_real_mail(setup):
    from offerpilot.job_mail.api import job_mail_origin_guard_response
    observer = JobMailSyncService(setup.sessions)
    observer_setup = JobMailSecureSetup(observer)
    app = FastAPI()
    app.state.job_mail_secure_setup = observer_setup
    request = Request({"type": "http", "method": "GET", "path": "/api/job-mail/suggestions",
                       "scheme": "http", "server": ("127.0.0.1", 8080), "client": ("127.0.0.1", 1),
                       "query_string": b"", "app": app,
                       "headers": [(b"host", b"evil.example"), (b"origin", b"http://evil.example")]})
    assert job_mail_origin_guard_response(request) is None
    saved(setup)
    assert job_mail_origin_guard_response(request).status_code == 403
    assert observer_setup.local_only
    observer_setup.shutdown()


def test_test_request_transfers_secret_and_cancel_drops_live_session_copy(setup):
    from offerpilot.job_mail.secure_setup import TestInput as SetupTestInput
    token = start(setup)
    entry = setup.secure._sessions[token]
    command = SetupTestInput(setup_token=token, email="synthetic@qq.com",
                             authorization_code=SECRET, explicit_test_consent=True)
    setup.secure.test(command, setup.client.cookies[COOKIE])
    assert command.authorization_code.get_secret_value() == ""
    setup.secure.cancel(token, setup.client.cookies[COOKIE])
    assert entry.secret is None


def test_settings_cannot_commit_after_shutdown_yields_runtime_ownership(setup):
    saved(setup)
    entered, release = threading.Event(), threading.Event()
    original = setup.service.transport
    errors = []
    def blocked_baseline(folder):
        entered.set()
        assert release.wait(5)
        return "42", 9
    original.baseline = blocked_baseline
    def settings_worker():
        try:
            setup.service.update_settings({"folders": ["INBOX", "Sent"]})
        except JobMailError as error:
            errors.append(error.code)
    worker = threading.Thread(target=settings_worker)
    worker.start()
    assert entered.wait(2)
    setup.secure.shutdown()
    second = JobMailSyncService(setup.sessions)
    second_setup = JobMailSecureSetup(second, enabled=True, vault_factory=lambda: setup.vault,
                                      transport_factory=SetupTransport)
    assert second_setup.enabled
    release.set()
    worker.join(3)
    assert errors == ["local_setup_not_enabled"]
    assert second.status()["connection"]["folders"] == ["INBOX"]
    assert not second.transport._cancelled.is_set()
    second_setup.shutdown()


def test_database_commit_ack_failure_does_not_delete_a_committed_credential(setup):
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    token = prepare_tested_session(setup)
    fail = False
    fired = False
    def arm_after_store():
        nonlocal fail
        fail = True
    setup.vault.after_set = arm_after_store
    def unknown_commit(session):
        nonlocal fired
        if fail and not fired:
            fired = True
            raise RuntimeError("synthetic lost commit acknowledgement")
    event.listen(Session, "after_commit", unknown_commit)
    try:
        response = post(setup, "save", setup_token=token, folder_ids=[FOLDERS[0]["id"]], explicit_save_consent=True)
    finally:
        event.remove(Session, "after_commit", unknown_commit)
    assert response.status_code == 409
    assert response.json()["error_code"] == "secure_save_result_unknown"
    with setup.sessions() as session:
        connection = session.scalar(select(JobMailConnection))
        operation = session.scalar(select(JobMailCredentialOperation))
        assert connection.status == "connected" and operation.state == "committed"
        assert connection.credential_ref in setup.vault.values
