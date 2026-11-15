"""Fake upstream, real process CLI/uvicorn: no paid calls or model injection."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scripts.simplification_ai_validation import driver


def fake_completion(body, operation):
    """Only the upstream HTTP server is fake; product parsers remain real."""
    user = body["messages"][-1]["content"]
    if operation.endswith("questions"):
        reply = {"questions": [
            {"question": f"Explain the {operation} synthetic design and testing trade-off.",
             "difficulty": "medium", "category": "technical", "tags": ["synthetic"]},
        ]}
    elif operation.endswith("analyze") or operation.endswith("match"):
        reply = {"summary": "Synthetic experience matches the supplied role.", "match_score": 80}
    else:
        payload = json.loads(user)
        catalog = payload["evidence_catalog"]
        turns = [entry for entry in catalog if entry["source"] == "turn"]
        if operation.endswith("feedback"):
            assert len(turns) == 2
            reply = {
                "schema_version": "mock-interview-feedback-v1", "proposal_status": "normal",
                "strengths": [{"id": "strength", "text": "The answer names a concrete test.",
                               "evidence_ids": [turns[0]["id"]]}],
                "practice_points": [{"id": "practice", "text": "Specify the failure threshold.",
                                     "evidence_ids": [turns[1]["id"]]}],
                "follow_up_questions": [],
                "next_practice_steps": [{"id": "next", "text": "Run the proposed repeated test.",
                                         "evidence_ids": [turns[1]["id"]]}],
            }
        else:
            assert bool(turns) == operation.endswith("next")
            reply = {"question": "How would you repeat the p95 validation with failure injection?"
                     if turns else "Describe your synthetic project and validation approach.",
                     "evidence_ids": [turns[-1]["id"] if turns else catalog[0]["id"]]}
    return {
        "id": "synthetic-completion", "object": "chat.completion", "created": 1,
        "model": "deepseek-flash",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(reply)},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200,
                  "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 100},
    }


@contextmanager
def fake_gate(data_dir, *, fail_operation=None):
    state = {"active": None, "token": None, "operations": [], "requests": [], "denied": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            operation = state["active"]
            if (operation is None or self.path != "/v1/chat/completions"
                    or self.headers.get("Authorization") != f"Bearer {state['token']}"):
                state["denied"] += 1
                self.send_response(403)
                self.end_headers()
                return
            state["requests"].append(operation)
            reply = fake_completion(body, operation)
            if operation == fail_operation:
                reply["choices"][0]["message"]["content"] = json.dumps({
                    "schema_version": "mock-interview-feedback-v1", "proposal_status": "safe_empty",
                    "strengths": [], "practice_points": [], "follow_up_questions": [],
                    "next_practice_steps": [],
                })
            raw = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    @contextmanager
    def stage(operation):
        assert operation in driver.OPERATIONS
        assert operation not in state["operations"]
        state["operations"].append(operation)
        state["active"] = operation
        state["token"] = f"test-only-loopback-{len(state['operations'])}"
        profile = {
            "id": "bounded", "provider": "openai_compatible", "enabled": True,
            "api_key": state["token"], "model": "deepseek-flash",
            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            "context_window": 1048576, "max_output_tokens": 4096, "supports_json_schema": False,
        }
        config = {"providers": [profile], "active_provider_id": "bounded",
                  "fallback_provider_ids": [], "fallback_provider_id": "",
                  "confirmation_secret": "synthetic-local-confirmation-only", "log_level": "ERROR",
                  "chat_auto_approve_writes": False}
        (data_dir / "config.json").write_text(json.dumps(config))
        try:
            yield
        finally:
            state["active"] = None

    try:
        yield stage, state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_real_cli_and_http_matrix_uses_exactly_twelve_upstream_calls(tmp_path, monkeypatch):
    # These process-global sentinels must not enter any product subprocess.
    monkeypatch.setenv("OPENAI_API_KEY", "test-do-not-inherit-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path / "must-not-touch"))
    data_dir = tmp_path / "owned"
    data_dir.mkdir()
    with fake_gate(data_dir) as (stage, state):
        result = driver.run_matrix(data_dir, stage)
    assert result["passed"]
    assert state["operations"] == list(driver.OPERATIONS)
    assert state["requests"] == list(driver.OPERATIONS)
    assert state["denied"] == 0  # Replays/negative cases do not even call the closed broker.
    assert result["counts"] == {"analyses": 2, "matches": 2, "questions": 2, "attempts": 2,
                                "turns": 4, "feedback": 2, "confirmed_event_drafts": 1}
    assert not (tmp_path / "must-not-touch").exists()
    summary = json.dumps(result)
    assert "test-do-not-inherit-secret" not in summary
    assert "raw_text" not in summary and "proposal" not in summary


def test_environment_is_constructed_without_parent_secrets(tmp_path, monkeypatch):
    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY",
                 "OFFERPILOT_PROVIDER_REQUEST_TRACE", "HTTPS_PROXY", "SSLKEYLOGFILE", "PYTHONPATH"):
        monkeypatch.setenv(name, "parent-sensitive-value")
    environment = driver.child_environment(tmp_path)
    assert "parent-sensitive-value" not in json.dumps(environment)
    assert environment["OFFERPILOT_DATA"] == str(tmp_path)
    assert environment["HOME"].startswith(str(tmp_path))
    assert environment["PYTHONNOUSERSITE"] == "1"


def test_subprocess_guard_blocks_nonloopback_before_connect(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", driver._GUARDED_ENTRY +
         "import socket; socket.create_connection(('198.51.100.1', 443))"],
        env=driver.child_environment(tmp_path), cwd=driver.ROOT,
        capture_output=True, timeout=30, check=False,
    )
    assert result.returncode != 0
    assert b"OfflineNetworkDenied" in result.stderr


def test_nonempty_data_dir_is_refused_before_any_stage(tmp_path):
    existing = tmp_path / "config.json"
    existing.write_text("must-not-read-or-replace")
    with pytest.raises(driver.MatrixFailure, match="data_dir_not_empty"):
        driver.run_matrix(tmp_path, lambda _: pytest.fail("must not open a stage"))
    assert existing.read_text() == "must-not-read-or-replace"


@pytest.mark.parametrize("status,items,check", [
    ("safe_empty", [], "normal_feedback_required"),
    ("normal", [], "nonempty_feedback_required"),
    ("normal", [{"text": "Unsupported assertion", "evidence_refs": []}],
     "feedback_turn_evidence_required"),
    ("normal", [{"text": "Wrong answer", "evidence_refs": [
        {"source": "turn", "path": "/turns/001/answer", "excerpt": "Not the submitted answer"},
    ]}], "feedback_turn_evidence_required"),
])
def test_feedback_evidence_failures_are_not_reported_as_success(status, items, check):
    body = {"proposal_status": status, "proposal": {
        "proposal_status": status, "strengths": items, "practice_points": [],
        "follow_up_questions": [], "next_practice_steps": [],
    }}
    with pytest.raises(driver.MatrixFailure, match=check):
        driver._feedback(body, "quick_feedback", {1: "The submitted synthetic answer"})


def test_cli_failure_does_not_expose_captured_provider_output(tmp_path, monkeypatch):
    def failed(*args, **kwargs):
        return subprocess.CompletedProcess(args, 1, b"private-provider-output", b"secret-token")
    monkeypatch.setattr(subprocess, "run", failed)
    with pytest.raises(driver.MatrixFailure) as captured:
        driver._cli(tmp_path, "cli_analyze", ["analyze"])
    assert str(captured.value) == "cli_analyze: cli_process_nonzero"
