"""The browser fixture must expose only its fixed safety counters before SPA fallback."""
import runpy
import socket
from pathlib import Path

from fastapi.testclient import TestClient


def test_browser_fixture_counters_precede_spa_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFERPILOT_MAIL_FIXTURE_DATA", str(tmp_path))
    original_connect = socket.socket.connect
    try:
        fixture = runpy.run_path(str(Path(__file__).parent / "fixtures" / "job_mail_app.py"))
        with TestClient(fixture["app"], base_url="http://127.0.0.1",
                        client=("127.0.0.1", 50001)) as client:
            result = client.get("/api/job-mail/fixture-safety")
            assert result.status_code == 200
            assert result.json() == {"synthetic_only": True, "body_read_attempts": 0,
                                     "vault_entries": 0}
            assert client.get("/api/job-mail/not-a-fixture-route").status_code == 404
    finally:
        socket.socket.connect = original_connect
