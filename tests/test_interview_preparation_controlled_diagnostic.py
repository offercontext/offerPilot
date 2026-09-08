import pytest
from runpy import run_path
from pathlib import Path

_SCRIPT = run_path(
    str(
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "interview-preparation-controlled-provider-diagnostic.py"
    )
)
_validate_controlled_responses = _SCRIPT["_validate_controlled_responses"]
_validate_redacted_request_metadata = _SCRIPT["_validate_redacted_request_metadata"]


def _valid_metadata() -> dict[str, object]:
    return {
        "kind": "provider_request_metadata",
        "provider_id": "default",
        "provider_type": "openai_compatible",
        "endpoint": {"scheme": "http", "host": "127.0.0.1", "port": 1234},
        "model": "controlled",
        "litellm_model": "openai/controlled",
        "message_count": 2,
        "message_bytes": 10,
        "request_body_bytes": 20,
        "request_body_scope": "serialized_provider_payload_without_auth_or_endpoint",
        "input_fingerprint_sha256": "a" * 64,
        "schema_fingerprint_sha256": "b" * 64,
        "response_mode": "text_json",
        "explicit_max_tokens": None,
        "explicit_timeout_seconds": None,
    }


def test_controlled_diagnostic_rejects_missing_request_metadata():
    with pytest.raises(RuntimeError, match="request metadata"):
        _validate_redacted_request_metadata(
            [],
            expected_calls=3,
            expected_provider_type="openai_compatible",
            expected_model="controlled",
        )


def test_controlled_diagnostic_accepts_complete_request_metadata():
    _validate_redacted_request_metadata(
        [_valid_metadata()],
        expected_calls=1,
        expected_provider_type="openai_compatible",
        expected_model="controlled",
    )


def test_controlled_diagnostic_rejects_missing_request_size_field():
    record = _valid_metadata()
    del record["message_bytes"]
    with pytest.raises(RuntimeError, match="message_bytes"):
        _validate_redacted_request_metadata(
            [record],
            expected_calls=1,
            expected_provider_type="openai_compatible",
            expected_model="controlled",
        )


def test_controlled_diagnostic_rejects_invalid_request_size_field():
    record = _valid_metadata()
    record["request_body_bytes"] = True
    with pytest.raises(RuntimeError, match="request_body_bytes"):
        _validate_redacted_request_metadata(
            [record],
            expected_calls=1,
            expected_provider_type="openai_compatible",
            expected_model="controlled",
        )


def test_controlled_diagnostic_rejects_zero_calls():
    with pytest.raises(RuntimeError, match="expected calls"):
        _validate_redacted_request_metadata(
            [],
            expected_calls=3,
            expected_provider_type="openai_compatible",
            expected_model="controlled",
        )


def test_controlled_diagnostic_rejects_missing_provider_responses():
    with pytest.raises(RuntimeError, match="Provider responses"):
        _validate_controlled_responses([], expected_calls=3)


def test_controlled_diagnostic_accepts_provider_responses():
    _validate_controlled_responses(
        [
            {
                "request_body_bytes": 10,
                "response_status": 200,
                "request_id_hash": "a" * 12,
            }
        ],
        expected_calls=1,
    )
