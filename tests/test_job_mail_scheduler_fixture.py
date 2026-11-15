"""Validate the isolated scheduler fixture before browser CI uses its controls."""
import runpy
import socket
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from offerpilot.api import create_app


def test_scheduler_fixture_controls_and_external_network_fence(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFERPILOT_MAIL_FIXTURE_DATA", str(tmp_path / "fixture"))
    fixtures = Path(__file__).parent / "fixtures"
    monkeypatch.syspath_prepend(str(fixtures))
    original_connect = socket.socket.connect
    try:
        fixture = runpy.run_path(str(fixtures / "job_mail_scheduler_app.py"))
        with TestClient(fixture["app"], base_url="http://127.0.0.1",
                        client=("127.0.0.1", 50001)) as client:
            assert client.get("/api/job-mail/fixture-scheduler").json() == {
                "synthetic_only": True, "read_attempts": 0}
            assert client.post("/api/job-mail/fixture-scheduler/due").status_code == 409
            assert client.post("/api/job-mail/fixture-scheduler/unknown").status_code == 400
            assert client.post("/api/job-mail/fixture-scheduler/fail").status_code == 200
            assert client.post("/api/job-mail/fixture-scheduler/recover").status_code == 200
            assert client.get("/api/job-mail/fixture-safety").json()["vault_entries"] == 0
            assert client.get("/api/job-mail/status").json()["run"] is None
            with socket.socket() as connection:
                try:
                    connection.connect(("203.0.113.1", 993))
                except OSError as exc:
                    assert str(exc) == "Synthetic acceptance fixture blocks external network"
                else:
                    raise AssertionError("Fixture admitted external IMAP")
    finally:
        socket.socket.connect = original_connect
        sys.modules.pop("job_mail_app", None)


def test_production_app_never_exposes_scheduler_fixture(tmp_path):
    app = create_app(data_dir=tmp_path)
    assert not any("fixture-scheduler" in getattr(route, "path", "")
                   for route in app.router.routes)
