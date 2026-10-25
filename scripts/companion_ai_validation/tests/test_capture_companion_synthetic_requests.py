"""Mandatory Linux-only fixture tests; excluded from the default Windows suite."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

from scripts import capture_companion_synthetic_requests as capture


def _payload():
    return {
        "model": "offline-model", "max_tokens": 512, "stream": True,
        "stream_options": {"include_usage": True}, "temperature": 0.75,
        "messages": [
            {"role": "system", "content": "## 结论\n## 下一步\nraw active"},
            {"role": "user", "content": "以下是角色日常参考。active "
             "12345678-1234-1234-1234-123456789abc 1792576800.0"},
            {"role": "assistant", "content": "完整历史，不截断。"},
            {"role": "user", "content": "问候，不删除引语中的 active。"},
        ],
        "tools": [{"type": "function", "function": {
            "name": "synthetic_tool", "description": "## 结论 is tool schema content",
            "parameters": {"type": "object", "properties": {
                "text": {"type": "string", "description": "retain all schemas"},
            }, "required": ["text"], "additionalProperties": False},
        }}],
        "tool_choice": "auto", "unknown_future_parameter": {"preserved": True},
    }


def test_complete_body_is_retained_byte_for_byte_and_no_headers_are_accepted():
    payload = _payload()
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode()
    record = capture.capture_body(raw, "synthetic_operation", "synthetic-capture-fixture")
    assert record["payload"] == payload
    assert record["body_utf8"].encode() == raw
    assert record["received_body_sha256"] == record["stored_body_sha256"] == (
        hashlib.sha256(raw).hexdigest())
    assert record["received_body_bytes"] == len(raw)
    assert record["synthetic_capability_redactions"] == 0
    assert set(record) == {
        "operation", "body_utf8", "payload", "received_body_bytes", "received_body_sha256",
        "stored_body_sha256", "synthetic_capability_redactions",
    }
    assert "headers" not in record and "Authorization" not in json.dumps(record)
    with pytest.raises(TypeError):
        capture.capture_body(raw, "synthetic_operation", "synthetic-capture-fixture", headers={})


def test_only_exact_synthetic_capability_is_redacted_not_semantic_content():
    payload = _payload()
    payload["messages"][-1]["content"] += " synthetic-capture-fixture token secret api_key"
    record = capture.capture_body(capture._encode(payload), "synthetic", "synthetic-capture-fixture")
    assert record["synthetic_capability_redactions"] == 1
    assert "synthetic-capture-fixture" not in record["body_utf8"]
    assert "[REDACTED_SYNTHETIC_CAPABILITY] token secret api_key" in record["body_utf8"]
    assert record["payload"]["tools"] == payload["tools"]
    assert record["payload"]["messages"][:-1] == payload["messages"][:-1]
    with pytest.raises(RuntimeError, match="synthetic_capability_required"):
        capture.capture_body(capture._encode(payload), "synthetic", "not-synthetic")


def test_marker_report_points_to_actual_messages_not_tool_schema():
    record = capture.capture_body(capture._encode(_payload()), "synthetic", "synthetic-capture-fixture")
    summary = capture.summarize_request(record)
    markers = summary["markers"]
    assert summary["tool_count"] == 1 and summary["message_count"] == 4
    assert [item["path"] for item in markers["conclusion_heading"]] == ["messages/0/content"]
    assert [item["surface"] for item in markers["raw_active"]] == [
        "system_policy", "companion_reference", "current_user",
    ]
    assert markers["raw_uuid"][0]["path"] == "messages/1/content"
    assert markers["raw_timestamp"][0]["matches"][0]["text"] == "1792576800.0"


def test_environment_is_discarded_without_reading_any_credentials(monkeypatch):
    class NeverRead(dict):
        def get(self, *_args, **_kwargs):
            pytest.fail("must not read inherited environment")

        def __getitem__(self, _key):
            pytest.fail("must not read inherited environment")

        def pop(self, *_args, **_kwargs):
            pytest.fail("must not read inherited credentials")

    environment = NeverRead(OPENAI_API_KEY="never-read", HOME="real-user-home",
                            OFFERPILOT_DATA="real-user-data", HTTPS_PROXY="real-proxy")
    monkeypatch.setattr(capture.os, "environ", environment)
    capture.clean_environment()
    assert "never-read" not in json.dumps(environment)
    assert "HOME" not in environment and "OFFERPILOT_DATA" not in environment
    assert "OPENAI_API_KEY" not in environment and "HTTPS_PROXY" not in environment


@pytest.mark.parametrize("kwargs,code", [
    ({"fake_only": False, "label": "snapshot"}, "fake_only_required"),
    ({"fake_only": 1, "label": "snapshot"}, "fake_only_required"),
    ({"fake_only": True, "label": "../user-data"}, "fixed_label_required"),
])
def test_capture_rejects_non_fake_or_caller_selected_paths_before_setup(monkeypatch, kwargs, code):
    monkeypatch.setattr(capture, "clean_environment", lambda: pytest.fail("setup must not start"))
    with pytest.raises(RuntimeError, match=code):
        capture.run_capture(**kwargs)


@pytest.mark.parametrize("argument", ["--live", "--data-dir", "--config", "--endpoint", "--api-key",
                                     "--output-dir", "--admission-file"])
def test_cli_has_no_live_credentials_or_user_path_arguments(argument, capsys):
    secret = "SYNTHETIC_SECRET_MUST_NOT_BE_ECHOED"
    with pytest.raises(SystemExit) as error:
        capture.main(["--fake-only", argument, secret])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    assert output.err == "synthetic capture: unsupported or missing argument\n"


def test_unsupported_platform_fails_before_environment_or_data_setup(monkeypatch):
    monkeypatch.setattr(capture.sys, "platform", "win32")
    monkeypatch.setattr(capture, "clean_environment", lambda: pytest.fail("setup must not start"))
    with pytest.raises(RuntimeError, match="unsupported_platform"):
        capture.run_capture(fake_only=True, label="snapshot")


def test_fake_boundary_rejects_a_caller_owned_or_existing_data_directory(tmp_path):
    (tmp_path / "config.json").write_text("do not read or overwrite")
    with pytest.raises(RuntimeError, match="owned_empty_data_required"):
        capture.SyntheticUpstream(tmp_path, ("only",), fake_only=True)
    assert (tmp_path / "config.json").read_text() == "do not read or overwrite"


def test_fake_upstream_retains_full_body_returns_no_tools_and_refuses_retries():
    with capture.owned_data_dirs() as data:
        fake = capture.SyntheticUpstream(data["synthetic_week"], ("only",), fake_only=True)
        try:
            with pytest.raises(RuntimeError, match="one_completed_fake_request_required"):
                with fake.stage("only"):
                    assert fake.active is not None
                    _, capability = fake.active
                    config = json.loads((data["synthetic_week"] / "config.json").read_bytes())
                    assert len(config["providers"]) == 1
                    assert config["providers"][0]["model"] == "offline-model"
                    assert config["providers"][0]["base_url"] == (
                        f"http://127.0.0.1:{fake.server.server_port}/v1")
                    assert config["providers"][0]["api_key"] == capability
                    assert config["fallback_provider_ids"] == []
                    assert config["chat_auto_approve_writes"] is False
                    raw = capture._encode(_payload())
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{fake.server.server_port}/v1/chat/completions", raw,
                        {"Authorization": "Bearer " + capability,
                         "X-Do-Not-Capture": "synthetic-private-header"},
                    )
                    with urllib.request.urlopen(request, timeout=5) as response:
                        body = response.read()
                    assert b"tool_calls" not in body and b'"finish_reason":"stop"' in body
                    with pytest.raises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request, timeout=5)
                    assert error.value.code == 400
            assert len(fake.requests) == 1 and fake.requests[0]["body_utf8"].encode() == raw
            assert "synthetic-private-header" not in json.dumps(fake.requests)
            assert capability not in json.dumps(fake.requests)
            assert fake.active is None
        finally:
            fake.close()
    assert not capture._OWNED_DATA_DIRS


def test_full_capture_uses_real_product_without_env_credentials_or_tool_execution(tmp_path):
    # The fresh process clears inherited values before product imports. This also
    # tests the standalone CLI rather than monkeypatching away product assembly.
    forbidden = "SYNTHETIC_ENV_CREDENTIAL_MUST_NOT_BE_READ_OR_CAPTURED"
    result = subprocess.run(
        [sys.executable, "-m", "scripts.capture_companion_synthetic_requests", "--fake-only",
         "--label", "final"],
        cwd=capture.ROOT, capture_output=True, timeout=420,
        env={"PATH": str(Path(sys.executable).parent), "PYTHONPATH": str(capture.ROOT / "src"),
             "OPENAI_API_KEY": forbidden, "DEEPSEEK_API_KEY": forbidden,
             "OFFERPILOT_DATA": str(tmp_path / "forbidden-real-user-data"),
             "HOME": str(tmp_path / "forbidden-real-user-home"), "HTTPS_PROXY": forbidden},
    )
    assert result.returncode == 0, result.stdout.decode()
    assert forbidden.encode() not in result.stdout + result.stderr
    status = json.loads(result.stdout)
    evidence = Path(status["evidence_dir"])
    print(f"synthetic_request_evidence={evidence}")
    report = json.loads((evidence / "requests.json").read_bytes())
    assert report["synthetic_only"] and report["fake_upstream_only"]
    assert not report["historical_paid_payloads"]
    assert report["provider_calls"] == report["tool_executions"] == 0
    assert report["request_count"] == 12 + len(capture.HOLDOUTS)
    assert report["historical_failure_case_numbers"] == [2, 7, 10, 12]
    assert report["source"]["working_tree_sha256"]["src/offerpilot/companion/context.py"]
    for record in report["requests"]:
        assert json.loads(record["body_utf8"]) == record["payload"]
        assert record["synthetic_capability_redactions"] == 0
        assert record["payload"]["messages"][0]["role"] == "system"
        assert record["payload"]["tools"]
    week = json.loads((evidence / "synthetic_week.json").read_bytes())
    holdouts = json.loads((evidence / "unseen_holdouts.json").read_bytes())
    assert week["assertions"]["no_tool_records"] and week["assertions"]["no_business_records_or_writes"]
    assert holdouts["no_tool_records"] and holdouts["no_write_operations"]
    assert not list(tmp_path.iterdir())
    for path in evidence.iterdir():
        assert forbidden not in path.read_text()
        assert "synthetic-capture-confirmation-only" not in path.read_text()
        assert path.stat().st_mode & 0o777 == 0o600
