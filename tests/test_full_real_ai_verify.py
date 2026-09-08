import json
from pathlib import Path

from scripts.full_real_ai_verify import (
    _build_summary,
    _prepare_temp_config,
    _safe_config_summary,
    run_full_verify,
)
from offerpilot.ai import client as ai_client
from offerpilot.ai.client import ConfiguredAIClient
from offerpilot.ai.types import Message
from offerpilot.config import AIProviderProfile, Config
from offerpilot.smoke import run_http_smoke
from offerpilot.smoke import _full_verify_client


def test_prepare_temp_config_overrides_only_isolated_active_model(tmp_path: Path) -> None:
    source_data = tmp_path / "source"
    source_data.mkdir()
    (source_data / "config.json").write_text(
        json.dumps(
            {
                "active_provider_id": "primary",
                "api_key": "secret-key",
                "model": "legacy-model",
                "providers": [
                    {
                        "id": "primary",
                        "provider": "openai_compatible",
                        "api_key": "secret-key",
                        "base_url": "https://provider.example/v1",
                        "model": "formal-model",
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    isolated = tmp_path / "isolated"

    _prepare_temp_config(source_data, isolated, "deepseek-v4-pro")

    isolated_config = json.loads((isolated / "config.json").read_text(encoding="utf-8"))
    source_config = json.loads((source_data / "config.json").read_text(encoding="utf-8"))
    assert isolated_config["providers"][0]["model"] == "deepseek-v4-pro"
    assert isolated_config["model"] == source_config["model"]
    assert source_config["providers"][0]["model"] == "formal-model"
    safe = _safe_config_summary(isolated / "config.json", isolated)
    assert safe["model"] == "deepseek-v4-pro"
    assert "secret-key" not in json.dumps(safe)


def test_build_summary_keeps_only_redacted_provider_and_failure_metadata(tmp_path: Path) -> None:
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    (report_dir / "provider-request-audit.jsonl").write_text(
        json.dumps(
            {
                "kind": "provider_request_metadata",
                "provider_id": "primary",
                "provider_type": "openai_compatible",
                "model": "deepseek-v4-pro",
                "litellm_model": "openai/deepseek-v4-pro",
                "endpoint": {"scheme": "https", "host": "provider.example", "port": 443},
                "input_fingerprint_sha256": "a" * 64,
                "schema_fingerprint_sha256": "b" * 64,
                "request_body_bytes": 1234,
                "request_body_scope": "serialized_provider_payload_without_auth_or_endpoint",
                "message_count": 3,
                "message_bytes": 456,
                "response_mode": "text_json",
                "explicit_max_tokens": None,
                "explicit_timeout_seconds": None,
                "private_prompt": "must not appear",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    inner = {
        "status": "failed",
        "failure_category": "invalid_item_shape",
        "failure_categories": ["invalid_item_shape"],
        "structure_summaries": [{"payload_type": "object", "top_level_keys": []}],
        "provider_request_id_hash": "b" * 12,
    }

    summary = _build_summary(
        config_summary={
            "provider_id": "primary",
            "provider_type": "openai_compatible",
            "model": "deepseek-v4-pro",
        },
        child_data_dir=tmp_path / "child-data",
        report_dir=report_dir,
        exit_code=1,
        stdout="ok http_health: ready\nprivate prompt text\n",
        stderr="RuntimeError: provider response failed\nsecret-key\n",
        elapsed_ms=123456,
        inner_diagnostic=inner,
    )

    encoded = json.dumps(summary, ensure_ascii=True)
    assert summary["status"] == "failed"
    assert summary["exit_code"] == 1
    assert summary["last_completed_step"] == "http_health"
    assert summary["provider"] == "openai_compatible"
    assert summary["model"] == "deepseek-v4-pro"
    assert summary["input_fingerprints"] == ["a" * 64]
    assert summary["schema_fingerprints"] == ["b" * 64]
    assert summary["request_metadata_complete"] is True
    assert summary["provider_request_id_hash"] == "b" * 12
    assert summary["failure_category"] == "invalid_item_shape"
    assert summary["elapsed_ms"] == 123456
    assert "private prompt text" not in encoded
    assert "secret-key" not in encoded


def test_build_summary_marks_missing_provider_schema_metadata_incomplete(tmp_path: Path) -> None:
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    (report_dir / "provider-request-audit.jsonl").write_text(
        json.dumps(
            {
                "kind": "provider_request_metadata",
                "provider_id": "primary",
                "provider_type": "openai_compatible",
                "model": "model",
                "input_fingerprint_sha256": "a" * 64,
                "request_body_bytes": 1,
                "message_count": 1,
                "message_bytes": 1,
                "response_mode": "text_json",
                "explicit_max_tokens": None,
                "explicit_timeout_seconds": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = _build_summary(
        config_summary={"provider": "openai_compatible", "model": "model"},
        child_data_dir=tmp_path / "child-data",
        report_dir=report_dir,
        exit_code=0,
        stdout="ok interview_preparation: ready\n",
        stderr="",
        elapsed_ms=10,
    )

    assert summary["status"] == "incomplete"
    assert summary["request_metadata_complete"] is False
    assert summary["verification_error"] == "provider_request_metadata_incomplete"


def test_real_ai_smoke_persists_inner_failure_before_isolation_cleanup(monkeypatch, tmp_path: Path) -> None:
    source_data = tmp_path / "source"
    source_data.mkdir()
    (source_data / "config.json").write_text(
        json.dumps(
            {
                "active_provider_id": "primary",
                "providers": [
                    {
                        "id": "primary",
                        "provider": "openai_compatible",
                        "api_key": "secret-key",
                        "base_url": "https://provider.example/v1",
                        "model": "deepseek-v4-pro",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report_dir = tmp_path / "report"
    monkeypatch.setenv("OFFERPILOT_FULL_VERIFY_REPORT_DIR", str(report_dir))

    observed: dict[str, Path] = {}

    def fail_http_smoke(data_dir: Path, static_dir: Path | None, *, real_ai: bool):
        observed["data_dir"] = data_dir
        raise RuntimeError("provider response failed after private prompt")

    monkeypatch.setattr("offerpilot.smoke._run_http_smoke", fail_http_smoke)

    try:
        run_http_smoke(source_data, real_ai=True)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected isolated full verify failure")

    diagnostic_path = report_dir / "full-verify-inner-diagnostic.json"
    assert diagnostic_path.is_file()
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    encoded = diagnostic_path.read_text(encoding="utf-8")
    assert diagnostic["status"] == "failed"
    assert diagnostic["exception_category"] == "provider_error"
    assert diagnostic["config"]["model"] == "deepseek-v4-pro"
    assert "private prompt" not in encoded
    assert "secret-key" not in encoded
    assert not observed["data_dir"].exists()


def test_run_full_verify_records_actual_child_environment_and_exit(monkeypatch, tmp_path: Path) -> None:
    source_data = tmp_path / "source"
    source_data.mkdir()
    formal_config = {
        "active_provider_id": "primary",
        "providers": [
            {
                "id": "primary",
                "provider": "openai_compatible",
                "api_key": "secret-key",
                "base_url": "https://provider.example/v1",
                "model": "formal-model",
            }
        ],
    }
    config_path = source_data / "config.json"
    config_path.write_text(json.dumps(formal_config), encoding="utf-8")
    report_dir = tmp_path / "report"

    class FakeProcess:
        pid = 12345
        returncode = 1

        def poll(self):
            return self.returncode

        def communicate(self, timeout=None):
            return ("ok http_health: ready\n", "provider response failed")

    def fake_popen(args, **kwargs):
        env = kwargs["env"]
        assert env["OFFERPILOT_DATA"] != str(source_data)
        assert env["OFFERPILOT_FULL_VERIFY_REPORT_DIR"] == str(report_dir)
        assert env["OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE"].startswith(str(report_dir))
        Path(env["OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE"]).write_text(
            json.dumps(
                {
                    "kind": "provider_request_metadata",
                    "provider_id": "primary",
                    "provider_type": "openai_compatible",
                    "model": "deepseek-v4-pro",
                    "input_fingerprint_sha256": "c" * 64,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return FakeProcess()

    monkeypatch.setattr("scripts.full_real_ai_verify.subprocess.Popen", fake_popen)
    formal_before = config_path.read_bytes()

    summary = run_full_verify(
        source_data=source_data,
        static_dir=tmp_path / "dist",
        report_dir=report_dir,
        timeout_seconds=1,
    )

    assert summary["status"] == "failed"
    assert summary["exit_code"] == 1
    assert summary["model"] == "deepseek-v4-pro"
    assert summary["input_fingerprints"] == ["c" * 64]
    assert summary["formal_config_unchanged"] is True
    assert config_path.read_bytes() == formal_before


def test_build_summary_identifies_the_first_failed_operation(tmp_path: Path) -> None:
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    (report_dir / "full-verify-operation-audit.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "provider_request_result",
                        "operation": "interview_preparation",
                        "provider_type": "openai_compatible",
                        "model": "deepseek-v4-pro",
                        "status": "error",
                        "elapsed_ms": 90000,
                        "http_status": None,
                        "provider_request_id_hash": "d" * 12,
                        "failure_category": "network_timeout",
                    }
                ),
                json.dumps(
                    {
                        "kind": "api_request",
                        "operation": "interview_preparation",
                        "method": "POST",
                        "path": "/api/applications/1/interview-preparation-proposals",
                        "http_status": 502,
                        "duration_ms": 90010,
                        "response_attempt_status": None,
                        "error_category": None,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = _build_summary(
        config_summary={"provider": "openai_compatible", "model": "deepseek-v4-pro"},
        child_data_dir=tmp_path / "child-data",
        report_dir=report_dir,
        exit_code=1,
        stdout="",
        stderr="",
        elapsed_ms=90020,
        inner_diagnostic={"exception_category": "network_timeout"},
    )

    assert summary["first_failed_operation"] == {
        "kind": "provider_request_result",
        "operation": "interview_preparation",
        "provider_type": "openai_compatible",
        "model": "deepseek-v4-pro",
        "status": "error",
        "elapsed_ms": 90000,
        "http_status": None,
        "provider_request_id_hash": "d" * 12,
        "failure_category": "network_timeout",
    }


def test_provider_result_audit_records_operation_duration_and_request_hash(monkeypatch, tmp_path: Path) -> None:
    audit_path = tmp_path / "operations.jsonl"
    monkeypatch.setenv("OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE", str(audit_path))
    monkeypatch.setenv("OFFERPILOT_FULL_VERIFY_OPERATION", "interview_preparation")

    def fake_completion(**kwargs):
        return {"id": "provider-request-123", "choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(
        Config(
            providers=[
                AIProviderProfile(
                    id="primary",
                    provider="openai_compatible",
                    api_key="secret-key",
                    base_url="https://provider.example/v1",
                    model="deepseek-v4-pro",
                )
            ],
            active_provider_id="primary",
        )
    )

    client.complete([Message(role="user", content="private prompt")], [])

    records = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    result = next(record for record in records if record["kind"] == "provider_request_result")
    assert result["operation"] == "interview_preparation"
    assert result["status"] == "success"
    assert result["model"] == "deepseek-v4-pro"
    assert result["elapsed_ms"] >= 0
    assert len(result["provider_request_id_hash"]) == 12
    assert "private prompt" not in audit_path.read_text(encoding="utf-8")
    assert "secret-key" not in audit_path.read_text(encoding="utf-8")


def test_full_verify_client_accepts_an_explicit_real_ai_timeout(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs):
            observed.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr("offerpilot.smoke.httpx.Client", FakeClient)

    with _full_verify_client("http://127.0.0.1:12345", timeout_seconds=180):
        pass

    assert observed["timeout"] == 180.0
