"""Desktop transport and mail receipts share the real application composition.

The upgrade case archives the actual mail-enabled master, never synthesizes an
old schema by deleting tables, and uses only synthetic mail in temporary data.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from unittest.mock import patch
from uuid import uuid4
import zipfile

from fastapi.testclient import TestClient

from _offline_socketpair import socketpair_only_connect
from offerpilot.api import create_app
from offerpilot.config import Config, save_config
from offerpilot.db import init_database

ROOT = Path(__file__).resolve().parents[1]
MAIL_MASTER = "54d7f26b718000cbc0a4e607d0bb0470afadbeac"
TOKEN = "a1" * 32
PORT = 48271
ORIGIN = f"http://127.0.0.1:{PORT}"
MAIL_PAYLOAD = {
    "subject": "面试邀请",
    "sender": "hr@example.test",
    "received_at": "2026-10-09T10:00:00Z",
    "body_text": "面试时间 2026-10-20T10:00:00Z；时长60分钟\n公司：Example\n岗位：Engineer",
}


@contextmanager
def _offline_mail_guard():
    attempted = []

    def forbidden(*args, **kwargs):
        attempted.append("network, credentials, or model")
        raise AssertionError("Desktop mail integration must remain offline and credential-free")

    with ExitStack() as stack:
        stack.enter_context(patch.object(socket.socket, "connect", socketpair_only_connect(forbidden)))
        for target in (
            "socket.socket.connect_ex", "socket.socket.sendto",
            "socket.create_connection", "socket.getaddrinfo",
            "offerpilot.job_mail.transport.OSVault.__init__",
            "offerpilot.job_mail.transport.QQIMAPTransport.__init__",
            "litellm.completion", "litellm.acompletion",
        ):
            stack.enter_context(patch(target, forbidden))
        yield
    assert attempted == []


def _desktop_client(data_dir: Path, static_dir: Path) -> TestClient:
    # Import only in the integrated process: the archived master has no desktop module.
    from offerpilot.desktop import DesktopSecurity, TOKEN_HEADER, create_desktop_app

    security = DesktopSecurity(token=TOKEN, port=PORT)
    app = create_desktop_app(data_dir=data_dir, static_dir=static_dir, security=security)
    return TestClient(
        app, base_url=ORIGIN, client=("127.0.0.1", 38100),
        headers={TOKEN_HEADER: TOKEN, "Origin": ORIGIN},
    )


def _static_dir(tmp_path: Path) -> Path:
    static = tmp_path / "frontend"
    static.mkdir()
    (static / "index.html").write_text("<html>Synthetic desktop fixture</html>", encoding="utf-8")
    return static


def _confirm_mail(client: TestClient) -> dict:
    application_response = client.post("/api/applications", json={
        "company_name": "Example", "position_name": "Engineer",
    })
    assert application_response.status_code == 201, application_response.text
    application = application_response.json()
    imported = client.post("/api/job-mail/imports", json=MAIL_PAYLOAD)
    assert imported.status_code == 200, imported.text
    suggestion = imported.json()["items"][0]
    assert client.get("/api/application-events").json() == []
    base = {
        "operation_id": str(uuid4()), "suggestion_version": suggestion["version"],
        "application_id": application["id"], "edited_fields": {"location": "Synthetic room"},
    }
    endpoint = f'/api/job-mail/suggestions/{suggestion["id"]}'
    preview = client.post(endpoint + "/preview", json=base)
    assert preview.status_code == 200, preview.text
    payload = {**base, "preview_token": preview.json()["preview_token"]}
    assert client.post(endpoint + "/confirm", json=payload).status_code == 422
    assert client.get("/api/application-events").json() == []
    payload["explicit_confirmation"] = True
    confirmed = client.post(endpoint + "/confirm", json=payload)
    assert confirmed.status_code == 200, confirmed.text
    receipt = confirmed.json()
    assert receipt["replayed"] is False
    identity = {"suggestion_id": suggestion["id"], "payload": payload, "receipt": receipt}
    _assert_replay(client, identity)
    return identity


def _assert_replay(client: TestClient, identity: dict) -> None:
    replay = client.post(
        f'/api/job-mail/suggestions/{identity["suggestion_id"]}/confirm',
        json=identity["payload"],
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == {**identity["receipt"], "replayed": True}
    events = client.get("/api/application-events").json()
    assert len(events) == 1
    assert events[0]["id"] == identity["receipt"]["application_event_id"]
    receipt = client.get('/api/job-mail/receipts/' + identity["payload"]["operation_id"])
    assert receipt.status_code == 200, receipt.text
    assert receipt.json() == identity["receipt"]


def test_desktop_transport_preserves_mail_confirmation_and_replay(tmp_path: Path) -> None:
    from offerpilot.desktop import TOKEN_HEADER

    data_dir = tmp_path / "data"
    save_config(data_dir, Config(auth_enabled=True, auth_token="separate-web-token"))
    with _offline_mail_guard(), _desktop_client(data_dir, _static_dir(tmp_path)) as client:
        for method, path in (
            ("GET", "/api/job-mail/status"), ("GET", "/api/job-mail/suggestions"),
            ("POST", "/api/job-mail/imports"), ("OPTIONS", "/api/job-mail/imports"),
        ):
            request = client.build_request(method, path)
            del request.headers[TOKEN_HEADER]
            assert client.send(request).status_code == 401
            assert client.request(method, path, headers={"Origin": "https://foreign.example"}).status_code == 403
        assert client.get("/api/auth/status").json() == {"auth_enabled": True, "authenticated": True}
        assert client.get("/api/job-mail/status").status_code == 200
        invalid = client.post("/api/job-mail/connection", json={
            "provider": "qq", "authorization_code": "synthetic-private-sentinel",
        })
        assert invalid.status_code == 422
        assert "synthetic-private-sentinel" not in invalid.text
        _confirm_mail(client)


def _seed_mail_workspace(data_dir: Path) -> dict:
    """Called in a subprocess whose first import path is archived master source."""
    save_config(data_dir, Config(auth_enabled=True, auth_token="separate-web-token"))
    with _offline_mail_guard(), TestClient(
        create_app(data_dir=data_dir), base_url=ORIGIN, client=("127.0.0.1", 38101),
        headers={"X-OfferPilot-Token": "separate-web-token", "Origin": ORIGIN},
    ) as client:
        identity = _confirm_mail(client)
        pending = client.post("/api/job-mail/imports", json={
            **MAIL_PAYLOAD, "subject": "另一封待核对面试邀请",
            "body_text": "面试时间 2026-10-21T11:00:00Z；时长45分钟\n公司：Example\n岗位：Engineer",
        })
        assert pending.status_code == 200, pending.text
        identity["pending_id"] = pending.json()["items"][0]["id"]
        return identity


def _archive_and_seed_mail_master(tmp_path: Path, data_dir: Path) -> dict:
    baseline = subprocess.run(
        ["git", "rev-parse", "--verify", MAIL_MASTER + "^{commit}"],
        cwd=ROOT, capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()
    archive = subprocess.run(
        ["git", "archive", "--format=zip", baseline, "src"],
        cwd=ROOT, capture_output=True, check=True, timeout=30,
    )
    baseline_dir = tmp_path / "mail-master"
    with zipfile.ZipFile(io.BytesIO(archive.stdout)) as source:
        source.extractall(baseline_dir)
    manifest = tmp_path / "mail-identities.json"
    bootstrap = """
import json, pathlib, runpy, sys
source, test_file, data_dir, manifest, network_guard = sys.argv[1:]
sys.path.insert(0, source)
# The shared test guard is not product source; archived master remains first.
sys.path.insert(1, str(pathlib.Path(test_file).parent))
runpy.run_path(network_guard)
import offerpilot.db
assert pathlib.Path(offerpilot.db.__file__).resolve().is_relative_to(pathlib.Path(source))
namespace = runpy.run_path(test_file)
identity = namespace['_seed_mail_workspace'](pathlib.Path(data_dir))
pathlib.Path(manifest).write_text(json.dumps(identity), encoding='utf-8')
"""
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ}
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path), LITELLM_LOCAL_MODEL_COST_MAP="True")
    seed = subprocess.run(
        [sys.executable, "-I", "-c", bootstrap, str(baseline_dir / "src"),
         str(Path(__file__).resolve()), str(data_dir), str(manifest),
         str(ROOT / "tests/_offline_network_guard.py")],
        cwd=baseline_dir, env=env, capture_output=True, text=True, timeout=120,
    )
    assert seed.returncode == 0, seed.stderr
    return json.loads(manifest.read_text(encoding="utf-8"))


def _mail_snapshot(path: Path) -> dict:
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'job_mail_%'"
        )} | {"applications", "application_events", "application_creation_workspace"}
        # Every selected table's first column is its primary key; previews
        # deliberately use token rather than an id column.
        return {table: connection.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
                for table in tables}


def test_mail_enabled_master_survives_repeated_desktop_initialization(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    identity = _archive_and_seed_mail_master(tmp_path, data_dir)
    path = data_dir / "data.db"
    before = _mail_snapshot(path)
    assert len(before["job_mail_suggestions"]) == 2
    assert len(before["job_mail_receipts"]) == 1
    static = _static_dir(tmp_path)
    with _offline_mail_guard():
        for _ in range(2):
            sessions = init_database(path)
            sessions.kw["bind"].dispose()
            assert _mail_snapshot(path) == before
            with _desktop_client(data_dir, static) as client:
                _assert_replay(client, identity)
                pending = client.get('/api/job-mail/suggestions/' + identity["pending_id"])
                assert pending.status_code == 200
                assert pending.json()["status"] == "pending"
                assert pending.json()["evidence"]["snippet"]
                status = client.get("/api/job-mail/status").json()
                assert status["capabilities"]["real_connection"] is False
                assert status["capabilities"]["ai_recognition"] is False
            assert _mail_snapshot(path) == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT count(*) FROM schema_migrations WHERE version='0033_job_mail_review'"
        ).fetchone() == (1,)
