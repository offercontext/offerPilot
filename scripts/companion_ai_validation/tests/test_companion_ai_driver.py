"""Offline process integration: real product API, disposable broker capabilities."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sqlite3
import threading
from typing import Any
from uuid import uuid4

import pytest

from offerpilot.config import AIProviderProfile, Config, save_config
from scripts.companion_ai_validation import driver


class FakeStage:
    """One loopback HTTP upstream per stage; never a real provider or key."""

    def __init__(self, data_dir: Path, *, failure: str = "",
                 fail_operation: str = driver.OPERATIONS[0]):
        self.data_dir, self.failure = data_dir, failure
        self.fail_operation = fail_operation
        self.requests: list[dict[str, Any]] = []
        self.entered: list[str] = []
        self.exited: list[str] = []
        self.unexpected: list[str] = []
        self.active: tuple[str, str] | None = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                if self.path != "/v1/chat/completions" or not 0 < length <= 262144:
                    owner.unexpected.append("unexpected_path_or_bound")
                    self.respond(400, {"error": {"message": "fixture rejected"}})
                    return
                payload = json.loads(self.rfile.read(length))
                if owner.active is None or self.headers.get("Authorization") != f"Bearer {owner.active[1]}":
                    owner.unexpected.append("closed_or_wrong_stage")
                    self.respond(403, {"error": {"message": "fixture revoked"}})
                    return
                operation = owner.active[0]
                if any(item["operation"] == operation for item in owner.requests):
                    owner.unexpected.append("second_upstream_request")
                    self.respond(400, {"error": {"message": "fixture consumed"}})
                    return
                owner.requests.append({"operation": operation, "payload": payload, "raw_utf8_bytes": length})
                if owner.failure == "provider_error" and operation == owner.fail_operation:
                    # Deliberately private-like error data must not escape driver errors.
                    self.respond(400, {"error": {"message": "DO_NOT_RECORD_PROVIDER_ERROR_BODY"}})
                    return
                if owner.failure == "tool_call" and operation == owner.fail_operation:
                    # The real broker validates complete upstream JSON before local SSE.
                    # A tool-bearing upstream completion is rejected, never executed.
                    self.respond(400, {"error": {"message": "fixture rejected tool response"}})
                    return
                self.stream_reply({"role": "assistant", "content": f"合成普通回复：{operation}。"})

            def stream_reply(self, delta, finish="stop"):
                chunks = [
                    {"id": "offline", "object": "chat.completion.chunk", "created": 0,
                     "model": "offline-model", "choices": [{"index": 0, "delta": delta,
                                                           "finish_reason": None}]},
                    {"id": "offline", "object": "chat.completion.chunk", "created": 0,
                     "model": "offline-model", "choices": [{"index": 0, "delta": {},
                                                           "finish_reason": finish}]},
                ]
                body = ("".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
                                for chunk in chunks) + "data: [DONE]\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def respond(self, code, payload):
                body = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def configure(self, token: str) -> None:
        save_config(self.data_dir, Config(providers=[AIProviderProfile(
            id="offline-fixture", label="Synthetic local stage", provider="openai_compatible",
            api_key=token, base_url=f"http://127.0.0.1:{self.server.server_port}/v1",
            model="offline-model", context_window=65536, max_output_tokens=512,
        )], active_provider_id="offline-fixture", fallback_provider_ids=[],
            confirmation_secret="synthetic-only-confirmation-secret"))

    @contextmanager
    def stage(self, operation: str):
        assert self.active is None
        assert operation == driver.OPERATIONS[len(self.entered)]
        self.entered.append(operation)
        self.active = operation, f"synthetic-ticket-{uuid4()}"
        self.configure(self.active[1])
        try:
            yield
        finally:
            self.active = None
            self.configure("closed-fixture-capability")
            self.exited.append(operation)

    def close(self) -> None:
        self.active = None
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def fake_stage(tmp_path):
    fake = FakeStage(tmp_path)
    try:
        yield fake
    finally:
        fake.close()


def _context(payload):
    contexts = []
    for message in payload["messages"]:
        content = message.get("content", "")
        if isinstance(content, str) and content.startswith("以下是带来源的角色日常参考数据"):
            contexts.append(content)
    assert len(contexts) <= 1
    return contexts[0] if contexts else None


def _failure_metadata(data_dir, fake):
    """Only durable enum states/counts, never content, configuration, or errors."""
    metadata = {"upstream_operations": [r["operation"] for r in fake.requests],
                "stage_entries": fake.entered, "stage_exits": fake.exited,
                "gate_rejections": fake.unexpected}
    path = data_dir / "data.db"
    if not path.is_file():
        metadata["database"] = "missing"
        return metadata
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            metadata["turn_states"] = connection.execute(
                "SELECT conversation_id, state FROM pilot_turns ORDER BY rowid LIMIT 80"
            ).fetchall()
            metadata["execution_states"] = connection.execute(
                "SELECT conversation_id, state, generation, renewed_at_ms, lease_until_ms "
                "FROM pilot_executions ORDER BY rowid LIMIT 80"
            ).fetchall()
            metadata["run_states"] = connection.execute(
                "SELECT conversation_id, status, recording_status, recording_error_count, "
                "CASE WHEN failure_code IS NULL THEN NULL "
                "WHEN failure_code IN ('source_load_failed', 'chat_agent_timeout', "
                "'turn_execution_failed', 'ai_provider_error', 'turn_execution_active', "
                "'turn_admission_failed', 'runtime_cancelled', 'chat_cancelled') "
                "THEN failure_code ELSE 'unclassified_failure' END "
                "FROM agent_runs ORDER BY rowid LIMIT 80"
            ).fetchall()
            metadata["message_counts"] = connection.execute(
                "SELECT conversation_id, role, COUNT(*), SUM(length(content)) "
                "FROM chat_messages GROUP BY conversation_id, role LIMIT 80"
            ).fetchall()
    except sqlite3.Error:
        metadata["database"] = "metadata_unavailable"
    return metadata


def test_full_matrix_uses_real_http_exactly_twelve_times_and_sources(fake_stage, tmp_path):
    recorded = []
    try:
        result = driver.run_matrix(tmp_path, fake_stage.stage, record_response=recorded.append)
    except driver.MatrixFailure as error:
        pytest.fail(str(error) + "; safe_state_metadata=" +
                    json.dumps(_failure_metadata(tmp_path, fake_stage), sort_keys=True), pytrace=False)
    assert recorded == result["responses"] and len(recorded) == 12
    assert result["passed"] is True and result["pass_scope"] == "mechanism_only"
    assert result["transport"] == "uvicorn:/api/chat"
    assert result["ordinary_count"] == 12 and result["tone_review_required"] is True
    assert tuple(item["operation"] for item in fake_stage.requests) == driver.OPERATIONS
    assert fake_stage.entered == fake_stage.exited == list(driver.OPERATIONS)
    assert fake_stage.active is None and fake_stage.unexpected == []
    assert all(result["assertions"].values())
    assert len(result["controls"]) >= 15
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= driver.RESULT_BYTES
    assert "synthetic-ticket-" not in json.dumps(result)
    for response, captured in zip(result["responses"], fake_stage.requests, strict=True):
        payload = captured["payload"]
        assert payload["model"] == "offline-model" and payload["max_tokens"] == 512
        assert payload["stream"] is True
        assert payload["messages"][-1]["role"] == "user"
        assert payload["messages"][-1]["content"] == response["user_input"]
        assert response["assistant_text"] == f"合成普通回复：{response['operation']}。"
        assert response["source_verified"] and response["ordinary_turn_read_only"]
        assert _context(payload) == response["expected_context"]
        assert set(payload) <= {"messages", "model", "max_tokens", "tools", "tool_choice", "stream"}
        assert all(message["role"] in {"system", "user", "assistant"} for message in payload["messages"])
        assert all(not message.get("tool_calls") for message in payload["messages"])
    contexts = {item["operation"]: item["source_facts_before_projection"]
                for item in result["responses"]}
    assert contexts["shared_wait_after_absence"]["actual_user_participation"]["status"] == "promised"
    assert contexts["actual_color_choice"]["recent_shared_choice"]["value"] == "蓝色"
    assert result["assertions"]["paused_context_remains_paused"]
    assert contexts["serious_mixed_work"] is None
    assert result["assertions"]["boat_not_newer_cloud"]
    assert result["assertions"]["cloud_exact_source"]
    assert contexts["same_type_ambiguity"]["recall_status"] == "ambiguous_activity"
    assert contexts["deleted_source_memory"]["recall_status"] == "source_unavailable"
    assert "棉花糖号" not in json.dumps(fake_stage.requests[5]["payload"], ensure_ascii=False)
    assert "小月亮号" not in json.dumps(fake_stage.requests[5]["payload"], ensure_ascii=False)
    assert result["assertions"]["withdrawal_context_not_completion"]
    requests = {r["operation"]: r["payload"] for r in fake_stage.requests}
    absent, present = (requests[key] for key in ("greeting_without_page", "greeting_with_page"))
    assert {k: v for k, v in absent.items() if k != "messages"} == {
        k: v for k, v in present.items() if k != "messages"}
    assert absent["messages"][0] == present["messages"][0]
    assert absent["messages"][-1] == present["messages"][-1] == {
        "role": "user", "content": driver.PAGE_PAIR_GREETING}
    assert result["assertions"]["page_pair_fresh_distinct_conversations"]
    assert result["assertions"]["page_pair_same_companion_state"]
    assert result["assertions"]["page_pair_same_reference"]
    # Actual HTTP upstream bodies, not just intended source snapshots. Both
    # fresh conversations have no history or projected ID/time differences.
    from offerpilot.api import _chat_page_context_messages
    expected_page = [{"role": message.role, "content": message.content}
                     for message in _chat_page_context_messages(driver.PAGE_PAIR_CONTEXT)]
    assert len(absent["messages"]) == 2
    assert present["messages"] == [absent["messages"][0], *expected_page,
                                   absent["messages"][1]]
    assert all("## 结论" not in p["messages"][0]["content"] for p in requests.values())
    assert '"view":"settings"' in json.dumps(present, ensure_ascii=False).replace('\\"', '"')
    assert contexts["explicit_work_format"] is None
    assert "‘结论’和‘下一步’" in requests["explicit_work_format"]["messages"][-1]["content"]
    assert "小周" in requests["third_party_experience"]["messages"][-1]["content"]
    assert "未来的提议" in requests["future_invitation"]["messages"][-1]["content"]


@pytest.mark.parametrize("failure", ["provider_error", "tool_call"])
def test_failures_revoke_stage_stop_matrix_and_reap_api(tmp_path, monkeypatch, failure):
    fake = FakeStage(tmp_path, failure=failure)
    processes = []
    original = subprocess.Popen

    def tracked_popen(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(driver.subprocess, "Popen", tracked_popen)
    try:
        with pytest.raises(driver.MatrixFailure) as error:
            driver.run_matrix(tmp_path, fake.stage)
        assert "DO_NOT_RECORD_PROVIDER_ERROR_BODY" not in str(error.value)
        assert "synthetic-ticket-" not in str(error.value)
        assert fake.entered == fake.exited == [driver.OPERATIONS[0]]
        assert fake.active is None and len(fake.requests) == 1
        assert fake.unexpected == []
        assert processes and all(process.poll() is not None for process in processes)
        assert all(process.stdout.closed and process.stderr.closed for process in processes)
    finally:
        fake.close()


def test_child_environment_allowlist_does_not_inherit_secrets(tmp_path, monkeypatch):
    for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "OTEL_EXPORTER_OTLP_ENDPOINT", "LANGCHAIN_API_KEY", "OFFERPILOT_DATA", "HOME"):
        monkeypatch.setenv(key, "FORBIDDEN_INHERITED_VALUE")
    env = driver.child_environment(tmp_path)
    assert "FORBIDDEN_INHERITED_VALUE" not in json.dumps(env)
    assert env["HOME"] == str(tmp_path / "process-home")
    assert env["OFFERPILOT_DATA"] == str(tmp_path)
    assert not any("KEY" in key or "TOKEN" in key or "SECRET" in key for key in env)
    assert {key for key in env if "PROXY" in key} == {"NO_PROXY"}


def test_existing_database_is_rejected_without_starting_process(tmp_path, monkeypatch):
    (tmp_path / "data.db").write_text("existing synthetic database")
    def forbidden(*_args, **_kwargs):
        pytest.fail("must not launch against existing database")
    monkeypatch.setattr(driver, "_api", forbidden)
    with pytest.raises(driver.MatrixFailure, match="fresh_data_dir"):
        driver.run_matrix(tmp_path, forbidden)


def test_synthetic_clock_does_not_change_global_or_provider_time(tmp_path, monkeypatch):
    import time
    import uvicorn
    import offerpilot.companion.chat as chat
    import offerpilot.companion.repository as repository
    from offerpilot.ai import client

    (tmp_path / driver.CLOCK_FILE).write_text("1234.5")
    actual_time = time.time
    monkeypatch.setattr(repository, "time", repository.time)
    monkeypatch.setattr(chat, "time", chat.time)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    driver._serve(tmp_path, 9999)
    assert repository.time.time() == chat.time.time() == 1234.5
    assert time.time is client.time.time is actual_time
    assert time.time() > 1234.5


def test_api_child_network_guard_blocks_before_nonloopback_connect(tmp_path):
    import sys
    result = subprocess.run(
        [sys.executable, "-c", "import tests._offline_network_guard; import socket; "
         "socket.create_connection(('198.51.100.1', 443))"],
        cwd=driver.ROOT, env=driver.child_environment(tmp_path), capture_output=True,
        check=False, timeout=30,
    )
    assert result.returncode != 0 and b"OfflineNetworkDenied" in result.stderr


def test_prior_success_is_recorded_once_when_later_stage_fails(tmp_path):
    fake = FakeStage(tmp_path, failure="provider_error", fail_operation=driver.OPERATIONS[1])
    recorded = []
    try:
        with pytest.raises(driver.MatrixFailure):
            driver.run_matrix(tmp_path, fake.stage, record_response=recorded.append)
        assert [item["operation"] for item in recorded] == [driver.OPERATIONS[0]]
        assert recorded[0]["source_verified"] and recorded[0]["ordinary_turn_read_only"]
        assert "DO_NOT_RECORD_PROVIDER_ERROR_BODY" not in json.dumps(recorded)
        assert fake.entered == fake.exited == list(driver.OPERATIONS[:2])
        assert len(fake.requests) == 2 and not fake.unexpected
    finally:
        fake.close()


def test_http_status_failure_is_numeric_only_and_does_not_read_error_body():
    import httpx

    class ForbiddenBody(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("provider/API error body must not be read")
            yield b"private"

    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(503, stream=ForbiddenBody())
    )) as client:
        with pytest.raises(driver.MatrixFailure, match="http_status_503_expected_200"):
            driver._request(client, "unpressured_leisure", "POST", "http://localhost/api/chat")


def test_failure_metadata_keeps_only_closed_error_categories(tmp_path):
    from types import SimpleNamespace
    with sqlite3.connect(tmp_path / "data.db") as connection:
        connection.executescript("""
            CREATE TABLE pilot_turns (conversation_id INTEGER, state TEXT);
            CREATE TABLE pilot_executions (conversation_id INTEGER, state TEXT, generation INTEGER,
                                          renewed_at_ms INTEGER, lease_until_ms INTEGER);
            CREATE TABLE agent_runs (conversation_id INTEGER, status TEXT, recording_status TEXT,
                                     recording_error_count INTEGER, failure_code TEXT);
            CREATE TABLE chat_messages (conversation_id INTEGER, role TEXT, content TEXT);
            INSERT INTO agent_runs VALUES (1, 'failed', 'healthy', 0, 'source_load_failed');
            INSERT INTO agent_runs VALUES (2, 'failed', 'healthy', 0, 'DO_NOT_READ_PROVIDER_BODY');
            INSERT INTO chat_messages VALUES (2, 'user', 'DO_NOT_COPY_MESSAGE_TEXT');
        """)
    fake = SimpleNamespace(requests=[], entered=[], exited=[], unexpected=[])
    metadata = _failure_metadata(tmp_path, fake)
    assert metadata["run_states"][0][-1] == "source_load_failed"
    assert metadata["run_states"][1][-1] == "unclassified_failure"
    encoded = json.dumps(metadata)
    assert "DO_NOT_READ_PROVIDER_BODY" not in encoded and "DO_NOT_COPY_MESSAGE_TEXT" not in encoded
