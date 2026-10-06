"""Security, startup, ownership, and persistence regression tests for desktop."""

from __future__ import annotations

import json
import os
import queue
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse

from offerpilot.api import create_app
from offerpilot.config import Config, load_config, save_config
from offerpilot.desktop import (
    TOKEN_ENV,
    TOKEN_HEADER,
    DesktopSecurity,
    DesktopSecurityMiddleware,
    DesktopStartupError,
    create_desktop_app,
)

TOKEN = "a1" * 32
PORT = 48271
ORIGIN = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def static_dir(tmp_path):
    root = tmp_path / "frontend"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>Desktop fixture</html>", encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log('desktop')", encoding="utf-8")
    return root


@pytest.fixture
def guarded_client():
    app = DesktopSecurityMiddleware(
        PlainTextResponse("accepted"), DesktopSecurity(token=TOKEN, port=PORT)
    )
    return TestClient(app, base_url=ORIGIN)


@pytest.mark.parametrize("path", ["/", "/assets/app.js", "/api/health", "/api/auth/status",
                                  "/api/settings"])
def test_desktop_token_is_required_for_every_path(guarded_client, path):
    assert guarded_client.get(path).status_code == 401
    assert guarded_client.get(path, headers={TOKEN_HEADER: "wrong"}).status_code == 401
    assert guarded_client.get(path, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401
    assert guarded_client.get(path, headers={TOKEN_HEADER: TOKEN}).status_code == 200


def test_preflight_requires_the_same_desktop_boundary(guarded_client):
    assert guarded_client.options("/api/settings").status_code == 401
    response = guarded_client.options("/api/settings", headers={
        TOKEN_HEADER: TOKEN, "Origin": "https://foreign.example",
        "Access-Control-Request-Method": "PUT",
    })
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("host", ["localhost:48271", "127.0.0.1", "127.0.0.1:48272",
                                  "foreign.example:48271", "127.0.0.1:48271.evil.example"])
def test_exact_loopback_host_is_required(guarded_client, host):
    response = guarded_client.get("/", headers={TOKEN_HEADER: TOKEN, "Host": host})
    assert response.status_code == 403


@pytest.mark.parametrize("origin", ["null", "", "https://foreign.example", "http://localhost:48271",
                                    "http://127.0.0.1:48272", ORIGIN + "/"])
def test_foreign_or_opaque_origins_are_rejected(guarded_client, origin):
    response = guarded_client.get("/", headers={TOKEN_HEADER: TOKEN, "Origin": origin})
    assert response.status_code == 403


@pytest.mark.parametrize("origin", [None, ORIGIN])
def test_same_origin_and_originless_requests_are_allowed(guarded_client, origin):
    headers = {TOKEN_HEADER: TOKEN}
    if origin is not None:
        headers["Origin"] = origin
    assert guarded_client.get("/", headers=headers).status_code == 200


@pytest.mark.parametrize("duplicate", ["Host", "Origin", TOKEN_HEADER])
def test_duplicate_security_headers_are_rejected(guarded_client, duplicate):
    values = {"Host": f"127.0.0.1:{PORT}", "Origin": ORIGIN, TOKEN_HEADER: TOKEN}
    headers = list(values.items()) + [(duplicate, values[duplicate])]
    assert guarded_client.get("/", headers=headers).status_code in {401, 403}


def test_non_ascii_token_is_rejected_without_internal_error(guarded_client):
    response = guarded_client.get("/", headers=[(TOKEN_HEADER.encode(), b"\xff" * 64)])
    assert response.status_code == 401


@pytest.mark.parametrize("token", ["", "secret", "f" * 63, "g" * 64, "A" * 64])
def test_missing_or_malformed_parent_token_is_rejected(token):
    with pytest.raises(DesktopStartupError, match="32-byte"):
        DesktopSecurity(token=token, port=PORT)


@pytest.mark.parametrize("web_token", ["web-only-secret", ""])
def test_desktop_auth_is_independent_of_editable_web_auth(tmp_path, static_dir, web_token):
    data_dir = tmp_path / "data"
    save_config(data_dir, Config(auth_enabled=True, auth_token=web_token))
    security = DesktopSecurity(token=TOKEN, port=PORT)
    app = create_desktop_app(data_dir=data_dir, static_dir=static_dir, security=security)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/api/health").status_code == 401
        assert client.get("/api/settings", headers={"X-OfferPilot-Token": web_token}).status_code == 401
        headers = {TOKEN_HEADER: TOKEN}
        assert client.get("/", headers=headers).status_code == 200
        assert client.get("/assets/app.js", headers=headers).status_code == 200
        assert client.get("/api/settings", headers=headers).json()["auth_enabled"] is True
        status = client.get("/api/auth/status", headers=headers)
        assert status.json() == {"auth_enabled": True, "authenticated": True}
        assert load_config(data_dir).auth_token == web_token
        assert load_config(data_dir).auth_enabled is True
        changed = client.put("/api/settings", headers=headers, json={"auth_enabled": False})
        assert changed.status_code == 200
        assert client.get("/api/settings").status_code == 401
        assert client.get("/api/settings", headers=headers).status_code == 200

    # A fresh ordinary web composition still enforces its own token. Supplying
    # the desktop header to a web server never enables the transport adapter.
    save_config(data_dir, Config(auth_enabled=True, auth_token=web_token))
    with TestClient(create_app(data_dir=data_dir)) as web:
        assert web.get("/api/health").status_code == 200
        assert web.get("/api/settings", headers=headers).status_code == (401 if web_token else 503)
        assert web.get("/api/auth/status", headers=headers).json()["authenticated"] is False


@contextmanager
def _backend(data_dir, static_dir, *, token=TOKEN, port=0, env_only=False, extra_env=None):
    env = os.environ.copy()
    env[TOKEN_ENV] = token
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    if extra_env:
        env.update(extra_env)
    command = [sys.executable, "-m", "offerpilot.desktop", "--port", str(port)]
    if env_only:
        env["OFFERPILOT_DESKTOP_DATA_DIR"] = str(data_dir)
        env["OFFERPILOT_DESKTOP_STATIC_DIR"] = str(static_dir)
    else:
        command.extend(["--data-dir", str(data_dir), "--static-dir", str(static_dir)])
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=stderr, text=True, encoding="utf-8",
        )
        messages = queue.Queue()

        def read_protocol():
            for line in process.stdout:
                messages.put(line)

        threading.Thread(target=read_protocol, daemon=True).start()
        try:
            try:
                line = messages.get(timeout=40)
            except queue.Empty:
                stderr.seek(0)
                pytest.fail(f"No desktop protocol message; stderr={stderr.read()}")
            assert token not in line or not token
            yield process, json.loads(line), messages, stderr
        finally:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()


def _close_parent(process):
    process.stdin.close()
    assert process.wait(timeout=20) == 0


def test_process_ready_security_persistence_and_parent_eof(tmp_path, static_dir):
    data_dir = tmp_path / "persistent-data"
    save_config(data_dir, Config(auth_enabled=True, auth_token="web-secret"))
    with _backend(data_dir, static_dir, env_only=True) as (process, ready, _, stderr):
        assert ready["type"] == "offerpilot.desktop.ready"
        assert ready["protocol"] == 1
        assert ready["pid"] == process.pid
        origin = ready["origin"]
        port = urlsplit(origin).port
        with httpx.Client(base_url=origin, trust_env=False) as client:
            assert client.get("/api/health").status_code == 401
            assert client.get("/").status_code == 401
            headers = {TOKEN_HEADER: TOKEN}
            assert client.get("/api/health", headers=headers).json() == {"status": "ok"}
            assert "Desktop fixture" in client.get("/", headers=headers).text
            assert client.get("/api/settings", headers={**headers, "Origin": "null"}).status_code == 403
            assert client.get("/api/settings", headers={**headers, "Host": "evil.example"}).status_code == 403
            created = client.post("/api/applications", headers=headers, json={
                "company_name": "Persistent desktop company", "position_name": "Engineer",
            })
            assert created.status_code == 201
            application_id = created.json()["id"]
            assert client.put("/api/settings", headers=headers, json={"model": "desktop-test-model"}).status_code == 200
        _close_parent(process)
        stderr.seek(0)
        assert TOKEN not in stderr.read()

    next_token = secrets.token_hex(32)
    with _backend(data_dir, static_dir, token=next_token, port=port) as (process, ready, _, _):
        assert ready["type"] == "offerpilot.desktop.ready"
        assert ready["origin"] == origin
        with httpx.Client(base_url=origin, headers={TOKEN_HEADER: next_token}, trust_env=False) as client:
            assert client.get("/api/health", headers={TOKEN_HEADER: TOKEN}).status_code == 401
            application = client.get(f"/api/applications/{application_id}")
            assert application.status_code == 200
            assert application.json()["company_name"] == "Persistent desktop company"
            assert client.get("/api/settings").json()["model"] == "desktop-test-model"
            assert client.get("/api/auth/status").json()["authenticated"] is True
        _close_parent(process)
    config_text = (data_dir / "config.json").read_text(encoding="utf-8")
    assert TOKEN not in config_text
    assert next_token not in config_text
    assert load_config(data_dir).auth_enabled is True
    assert load_config(data_dir).auth_token == "web-secret"


@pytest.mark.parametrize("failure", ["missing_frontend", "invalid_token", "invalid_config",
                                     "relative_data", "invalid_port"])
def test_startup_errors_are_structured_and_never_report_ready(tmp_path, static_dir, failure):
    data_dir = tmp_path / "data"
    token = TOKEN
    port = 0
    expected = failure
    if failure == "missing_frontend":
        static_dir = tmp_path / "missing"
    elif failure == "invalid_token":
        token = ""
    elif failure == "relative_data":
        data_dir = Path("relative-data")
        expected = "invalid_path"
    elif failure == "invalid_port":
        port = 65536
    else:
        data_dir.mkdir()
        (data_dir / "config.json").write_text("not valid json", encoding="utf-8")
        expected = "startup_failed"
    with _backend(data_dir, static_dir, token=token, port=port) as (process, error, messages, _):
        assert error["type"] == "offerpilot.desktop.error"
        assert error["code"] == expected
        assert process.wait(timeout=20) == 1
        assert messages.empty()


def test_occupied_persistent_port_fails_without_switching_origin(tmp_path, static_dir):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
        reserved.bind(("127.0.0.1", 0))
        reserved.listen()
        port = reserved.getsockname()[1]
        with _backend(tmp_path / "data", static_dir, port=port) as (process, error, _, _):
            assert error["type"] == "offerpilot.desktop.error"
            assert error["code"] == "port_unavailable"
            assert process.wait(timeout=20) == 1


def test_parent_eof_before_readiness_stops_startup_without_an_orphan(tmp_path, static_dir):
    env = os.environ.copy()
    env[TOKEN_ENV] = TOKEN
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    process = subprocess.Popen(
        [sys.executable, "-m", "offerpilot.desktop", "--data-dir", str(tmp_path / "data"),
         "--static-dir", str(static_dir)],
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    try:
        # communicate closes the ownership pipe before even importing the app,
        # while the first-run database migrations are still ahead of us.
        stdout, stderr = process.communicate(input="", timeout=20)
        assert process.returncode == 0, stderr
        assert stdout == ""
        assert TOKEN not in stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_parent_eof_bounds_a_stalled_application_startup(tmp_path, static_dir):
    env = os.environ.copy()
    env[TOKEN_ENV] = TOKEN
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    bootstrap = """
import sys
import time
from offerpilot import desktop
desktop.SHUTDOWN_TIMEOUT_SECONDS = 0.25
def stalled_startup(**kwargs):
    print("startup-entered", file=sys.stderr, flush=True)
    time.sleep(60)
desktop.create_desktop_app = stalled_startup
raise SystemExit(desktop.main())
"""
    process = subprocess.Popen(
        [sys.executable, "-c", bootstrap, "--data-dir", str(tmp_path / "data"),
         "--static-dir", str(static_dir)],
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    marker = queue.Queue()
    threading.Thread(target=lambda: marker.put(process.stderr.readline()), daemon=True).start()
    try:
        assert marker.get(timeout=10).strip() == "startup-entered"
        stdout, stderr = process.communicate(input="", timeout=10)
        assert process.returncode == 1
        assert stdout == ""
        assert TOKEN not in stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_asgi_startup_failure_never_emits_ready(tmp_path, static_dir):
    env = os.environ.copy()
    env[TOKEN_ENV] = TOKEN
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    bootstrap = """
from offerpilot import desktop
async def failed_app(scope, receive, send):
    assert (await receive())["type"] == "lifespan.startup"
    await send({"type": "lifespan.startup.failed", "message": "startup fixture failed"})
desktop.create_desktop_app = lambda **kwargs: failed_app
raise SystemExit(desktop.main())
"""
    process = subprocess.Popen(
        [sys.executable, "-c", bootstrap, "--data-dir", str(tmp_path / "data"),
         "--static-dir", str(static_dir)],
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    try:
        assert process.wait(timeout=10) == 1
        messages = [json.loads(line) for line in process.stdout]
        assert len(messages) == 1
        assert messages[0]["type"] == "offerpilot.desktop.error"
        assert messages[0]["code"] == "startup_failed"
    finally:
        process.stdin.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
