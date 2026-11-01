"""Synthetic QA harness contract; no browsers, keys or provider requests."""
import json
import socket
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from scripts.persona_browser_server import FakeModel, Evidence, build_app, parse_args
from tests._offline_network_guard import OfflineNetworkDenied


@pytest.fixture
def static(tmp_path):
    directory = tmp_path / "static"
    directory.mkdir()
    (directory / "index.html").write_text("<html>Synthetic build fixture</html>")
    return directory


def test_requires_explicit_empty_directories_and_valid_port(tmp_path, static):
    with pytest.raises(SystemExit):
        parse_args([])
    with pytest.raises(SystemExit):
        parse_args(["--data-dir", str(tmp_path / "data"), "--evidence-dir", str(tmp_path / "evidence"), "--port", "0"])
    data = tmp_path / "existing"
    data.mkdir()
    (data / "data.db").write_text("must not touch")
    with pytest.raises(ValueError, match="refusing existing data"):
        build_app(data, tmp_path / "evidence", static)
    assert (data / "data.db").read_text() == "must not touch"
    link = tmp_path / "link"
    link.symlink_to(data, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        build_app(link, tmp_path / "evidence", static)
    with pytest.raises(ValueError, match="separate"):
        build_app(tmp_path / "empty", tmp_path / "empty", static)
    with pytest.raises(ValueError, match="frontend missing"):
        build_app(tmp_path / "empty", tmp_path / "evidence", tmp_path / "missing")


def test_fake_model_never_dispatches_tools_and_records_counts_only(tmp_path):
    from offerpilot.ai.types import Message

    fake = FakeModel(Evidence(tmp_path))
    envelope = {"source": "persona_profile", "fictional_background_is_fiction": True,
                "expression_data": {"display_name": "SyntheticName"}}
    result = fake.complete([Message(role="user", content=json.dumps(envelope))],
                           [{"name": "DO_NOT_EXECUTE"}])
    assert "SyntheticName" in result.content
    assert result.tool_calls == []
    evidence = json.loads((tmp_path / "fake-model-counts.json").read_text())
    assert evidence["chat_calls"] == 1 and evidence["provider_calls"] == 0
    assert set(evidence) == {"synthetic", "provider_calls", "chat_calls", "title_calls", "blocked_network_attempts"}


def test_server_app_uses_fresh_config_fake_models_and_real_routes(tmp_path, static, monkeypatch):
    # A poisoned default is never opened; only the explicit fresh directory is used.
    default = tmp_path / "owner-default"
    default.mkdir()
    (default / "config.json").write_text("not json; must never be read")
    monkeypatch.setenv("OFFERPILOT_DATA", str(default))
    data, evidence = tmp_path / "synthetic-data", tmp_path / "synthetic-evidence"
    evidence.mkdir()
    (evidence / "server.log").write_text("synthetic startup log")
    app = build_app(data, evidence, static)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/__persona_browser__/health").json() == {
            "synthetic": True, "fake_model": True, "isolated_data": True, "outbound_blocked": True}
        assert client.get("/api/companion").json()["settings"]["enabled"] is False
        assert client.get("/api/companion").json()["settings"]["proactivity"] == "off"
        original = client.get("/api/companion/persona").json()
        payload = {"profile_id": original["profile_id"], "expected_revision": original["revision"],
                   "command_id": str(uuid4()), "confirmed": True, "operation": "update",
                   "spec": {**original["spec"], "display_name": "SyntheticName"}}
        changed = client.post("/api/companion/persona/mutations", json=payload)
        assert changed.status_code == 200
        response = client.post("/api/chat", json={"request_id": str(uuid4()), "message": "你好，合成测试", "conversation_id": 0})
        assert response.status_code == 200
        assert "SyntheticName" in response.json()["message"]
        restored = client.post("/api/companion/persona/mutations", json={
            "profile_id": original["profile_id"], "expected_revision": changed.json()["revision"],
            "command_id": str(uuid4()), "confirmed": True, "operation": "restore", "target_revision": 0})
        assert restored.status_code == 200
        assert restored.json()["revision"] == 2
        assert restored.json()["spec"] == original["spec"]
    config = json.loads((data / "config.json").read_text())
    assert config["onboarding_dismissed"] is True
    assert not config.get("api_key")
    assert not config.get("providers")
    assert (default / "config.json").read_text() == "not json; must never be read"
    counts = json.loads((evidence / "fake-model-counts.json").read_text())
    assert counts["chat_calls"] >= 1
    assert counts["provider_calls"] == 0


def test_nonloopback_network_blocked_before_product_use():
    with pytest.raises(OfflineNetworkDenied):
        socket.getaddrinfo("example.com", 443)
    import httpx
    with httpx.Client(trust_env=False) as client, pytest.raises(OfflineNetworkDenied):
        client.get("https://example.com/")


def test_cli_always_binds_ipv4_loopback(tmp_path, monkeypatch):
    import scripts.persona_browser_server as server
    import uvicorn

    calls = []
    sentinel = object()
    monkeypatch.setattr(server, "build_app", lambda *args: sentinel)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    server.main(["--data-dir", str(tmp_path / "data"), "--evidence-dir", str(tmp_path / "evidence")])
    assert calls == [(sentinel, {"host": "127.0.0.1", "port": 18763, "access_log": False})]
    with pytest.raises(SystemExit):
        server.parse_args(["--data-dir", str(tmp_path / "data"), "--evidence-dir", str(tmp_path / "evidence"), "--host", "0.0.0.0"])


def test_workflow_is_feature_only_readonly_and_has_no_provider_secrets():
    from scripts.persona_browser_server import ROOT

    workflow = (ROOT / ".github/workflows/persona-browser.yml").read_text()
    assert ("branches: [feat/20261031-custom-companion-persona, "
            "feat/20261101-companion-integrated-candidate]") in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "workflow_dispatch:" not in workflow
    assert "pull_request:" not in workflow
    assert "paths:" not in workflow and "paths-ignore:" not in workflow
    assert "secrets." not in workflow
    assert "--real-ai" not in workflow
    assert "mktemp -d" in workflow and "trap 'kill" in workflow
    assert "path: artifacts/persona-browser" in workflow
    assert "retention-days: 14" in workflow
