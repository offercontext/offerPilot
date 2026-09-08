from offerpilot.ai.mock_interview import (
    build_mock_interview_diagnostic,
    should_retry_mock_interview_format,
)


def test_contract_failure_diagnostic_never_contains_model_input_or_raw_output():
    diagnostic = build_mock_interview_diagnostic(
        "excerpt_mismatch", True, 1, 42, "request-secret-value", "candidate resume text"
    )

    assert diagnostic == {
        "failure_category": "excerpt_mismatch",
        "repair_attempted": True,
        "repair_count": 1,
        "elapsed_ms": 42,
        "provider_request_id": "request-redacted-affa5adc6fb4",
    }
    assert "candidate resume text" not in str(diagnostic)
    assert "request-secret-value" not in str(diagnostic)


def test_provider_error_is_not_retried_as_format_repair():
    assert should_retry_mock_interview_format("provider_error") is False
    assert should_retry_mock_interview_format("invalid_json") is True


def test_format_repair_uses_same_snapshot_and_at_most_one_retry():
    assert should_retry_mock_interview_format("unexpected_field") is True
    assert should_retry_mock_interview_format("limit_exceeded") is False
