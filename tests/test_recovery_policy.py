"""Cross-layer recovery-contract tests: JSON contract, generated adapters, API behavior.

These tests are the drift proof required by the harness-reliability-contract plan:
the API, the Interview Studio frontend, and the smoke/browser harnesss must all
derive their recovery action from contracts/recovery-policy.v1.json instead of
guessing from HTTP status codes.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from offerpilot.ai.types import Assistant
from offerpilot.api import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "contracts" / "recovery-policy.v1.json"
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate_recovery_contract.py"
GENERATED_PY = REPO_ROOT / "src" / "offerpilot" / "reliability" / "recovery_policy_generated.py"
GENERATED_TS = REPO_ROOT / "web" / "src" / "lib" / "recoveryPolicy" / "generatedRecoveryPolicy.ts"

CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
CONTRACT_BY_CODE = {entry["error_code"]: entry for entry in CONTRACT["errors"]}


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_recovery_contract", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_contract(tmp_path: Path, contract: dict[str, Any]) -> Path:
    path = tmp_path / "recovery-policy.v1.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


class _ProviderFailureModel:
    supports_json_schema = False
    calls = 0

    def complete(self, messages, tools, **kwargs):
        self.calls += 1
        raise RuntimeError("provider unavailable")


class _ContractFailureModel:
    supports_json_schema = False
    calls = 0

    def complete(self, messages, tools, **kwargs):
        self.calls += 1
        return Assistant(content='{"unexpected":"raw model output"}')


class _WorkingModel:
    supports_json_schema = False

    def complete(self, messages, tools, **kwargs):
        if any("mock-interview-feedback-v1" in message.content for message in messages):
            return Assistant(
                content='{"schema_version":"mock-interview-feedback-v1","proposal_status":"safe_empty",'
                '"strengths":[],"practice_points":[],"follow_up_questions":[],"next_practice_steps":[]}'
            )
        return Assistant(content='{"question":"请结合 JD 说明你会如何准备。","evidence_ids":["ev_001"]}')


def _application_client(tmp_path, model=None) -> tuple[TestClient, str, str, str]:
    client = TestClient(create_app(data_dir=tmp_path, chat_model=model or _WorkingModel()))
    application = client.post(
        "/api/applications",
        json={"company_name": "Acme", "position_name": "Engineer", "status": "interview"},
    ).json()
    event = client.post(
        "/api/application-events",
        json={
            "application_id": application["id"],
            "event_type": "interview",
            "scheduled_at": "2026-08-18T10:00:00Z",
            "duration_minutes": 30,
        },
    ).json()
    resume = client.post("/api/resumes", json={"title": "Resume", "text": "Python engineer"}).json()
    client.post(
        f"/api/applications/{application['id']}/job-description/versions",
        json={
            "jd_text": "需要 Python",
            "source_url": None,
            "expected_current_version_id": None,
            "idempotency_key": "recovery-contract-jd-01",
        },
    )
    return client, application["id"], event["id"], resume["id"]


def _quick_case_client(tmp_path, model=None) -> tuple[TestClient, int]:
    client = TestClient(create_app(data_dir=tmp_path, chat_model=model or _WorkingModel()))
    resume = client.post("/api/resumes", json={"title": "Resume", "text": "Python engineer"}).json()
    case = client.post(
        "/api/interview-practice-cases",
        json={
            "idempotency_key": "recovery-contract-case-01",
            "position_name": "Engineer",
            "jd_text": "需要 Python",
            "resume_id": resume["id"],
        },
    ).json()
    return client, case["id"]


def _assert_contract_response(response, error_code: str) -> None:
    """The API response must match the recovery contract exactly."""
    entry = CONTRACT_BY_CODE[error_code]
    assert response.status_code == entry["http_status"], (
        f"{error_code}: HTTP {response.status_code} != contract {entry['http_status']}"
    )
    assert response.json().get("error_code") == error_code


# ---------------------------------------------------------------------------
# Contract file and generator
# ---------------------------------------------------------------------------


def test_contract_covers_required_mock_interview_codes() -> None:
    required = {
        "mock_interview_question_result_unknown",
        "mock_interview_feedback_result_unknown",
        "mock_interview_unverifiable",
        "mock_interview_source_conflict",
        "mock_interview_idempotency_conflict",
        "mock_interview_turn_idempotency_conflict",
        "mock_interview_attempt_confirmed",
        "mock_interview_attempt_not_found",
        "mock_interview_provider_error",
        "mock_interview_invalid_payload",
        "mock_interview_answer_required",
    }
    assert required <= set(CONTRACT_BY_CODE)


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_code",
        "missing_field",
        "unknown_disposition",
        "unknown_retention",
        "bad_status",
        "non_bool_flag",
    ],
)
def test_generator_rejects_invalid_contract(tmp_path, mutation) -> None:
    generator = _load_generator()
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if mutation == "duplicate_code":
        contract["errors"].append(dict(contract["errors"][0]))
    elif mutation == "missing_field":
        del contract["errors"][0]["disposition"]
    elif mutation == "unknown_disposition":
        contract["errors"][0]["disposition"] = "just_retry_forever"
    elif mutation == "unknown_retention":
        contract["errors"][0]["attempt_retention"] = "maybe_kept"
    elif mutation == "bad_status":
        contract["errors"][0]["http_status"] = 200
    elif mutation == "non_bool_flag":
        contract['errors'][0]['input_frozen'] = 'yes'
    path = _write_contract(tmp_path, contract)
    with pytest.raises(SystemExit):
        generator.load_and_validate(path)


def test_generator_is_deterministic_and_committed_outputs_are_current() -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATOR_PATH), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    generator = _load_generator()
    contract = generator.load_and_validate()
    assert GENERATED_PY.read_text(encoding="utf-8") == generator.render_python(contract)
    assert GENERATED_TS.read_text(encoding="utf-8") == generator.render_typescript(contract)


def test_generated_python_module_matches_contract_json() -> None:
    from offerpilot.reliability.recovery_policy_generated import RECOVERY_POLICIES

    assert set(RECOVERY_POLICIES) == set(CONTRACT_BY_CODE)
    for code, entry in CONTRACT_BY_CODE.items():
        generated = RECOVERY_POLICIES[code]
        assert generated.http_status == entry["http_status"]
        assert generated.disposition == entry["disposition"]
        assert generated.attempt_retention == entry["attempt_retention"]
        assert generated.input_frozen is entry["input_frozen"]
        assert generated.preserve_idempotency_key is entry["preserve_idempotency_key"]
        assert generated.provider_retry_allowed is entry["provider_retry_allowed"]
        assert generated.user_action == entry["user_action"]


def test_generated_typescript_module_matches_contract_json() -> None:
    source = GENERATED_TS.read_text(encoding="utf-8")
    blocks = dict(
        re.findall(r"  ([a-z0-9_]+): \{\n(.*?)\n  \},", source, re.DOTALL)
    )
    assert set(blocks) == set(CONTRACT_BY_CODE)
    for code, entry in CONTRACT_BY_CODE.items():
        block = blocks[code]
        assert f"http_status: {entry['http_status']}," in block
        assert f"disposition: '{entry['disposition']}'" in block
        assert f"attempt_retention: '{entry['attempt_retention']}'" in block
        assert f"input_frozen: {str(entry['input_frozen']).lower()}," in block
        assert f"preserve_idempotency_key: {str(entry['preserve_idempotency_key']).lower()}," in block
        assert f"provider_retry_allowed: {str(entry['provider_retry_allowed']).lower()}," in block


def test_every_api_mock_interview_error_code_has_a_contract_entry() -> None:
    api_source = (REPO_ROOT / "src" / "offerpilot" / "api.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'(?:code=|, )"((?:mock_interview|interview_practice_case|application_jd_version)[a-z0-9_]*)"', api_source))
    assert emitted, "api.py must still emit coded mock-interview errors"
    assert emitted <= set(CONTRACT_BY_CODE), sorted(emitted - set(CONTRACT_BY_CODE))


# ---------------------------------------------------------------------------
# API behavior matches the contract (controlled provider)
# ---------------------------------------------------------------------------


def test_application_provider_error_matches_contract_and_replay_keeps_same_key(tmp_path) -> None:
    model = _ProviderFailureModel()
    client, app_id, event_id, resume_id = _application_client(tmp_path, model)
    base = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    payload = {
        "resume_id": resume_id,
        "jd_version_id": 1,
        "attempt_idempotency_key": "provider-error-attempt",
        "initial_question_idempotency_key": "provider-error-question",
    }
    first = client.post(base, json=payload)
    _assert_contract_response(first, "mock_interview_provider_error")
    assert isinstance(first.json().get("operation_id"), str)
    entry = CONTRACT_BY_CODE["mock_interview_provider_error"]
    assert entry["disposition"] == "retry_same_key"
    assert entry["preserve_idempotency_key"] is True

    replay = client.post(base, json=payload)
    assert replay.status_code == 202
    assert replay.json()["attempt_status"] == "provider_unknown"
    assert replay.json()["attempt_id"] == first.json()["attempt_id"]
    calls_after_replay = model.calls
    assert calls_after_replay == 1  # same-key replay while lease is active never re-calls the provider
    from offerpilot.reliability.trace import hash_idempotency_key, read_mock_interview_traces

    trace = read_mock_interview_traces(tmp_path)[-1]
    assert trace["operation_id"] == first.json()["operation_id"]
    assert trace["idempotency_key_hash"] == hash_idempotency_key(
        payload["initial_question_idempotency_key"]
    )
    assert trace["provider"] == "_ProviderFailureModel"
    assert trace["first_byte_ms"] is None


def test_quick_provider_error_matches_contract_result_unknown(tmp_path) -> None:
    model = _ProviderFailureModel()
    client, case_id = _quick_case_client(tmp_path, model)
    payload = {
        "attempt_idempotency_key": "quick-provider-attempt",
        "initial_question_idempotency_key": "quick-provider-question",
    }
    first = client.post(
        f"/api/interview-practice-cases/{case_id}/mock-interview/attempts", json=payload
    )
    _assert_contract_response(first, "mock_interview_question_result_unknown")


def test_unverifiable_matches_contract_and_never_recalls_provider(tmp_path) -> None:
    model = _ContractFailureModel()
    client, app_id, event_id, resume_id = _application_client(tmp_path, model)
    base = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    payload = {
        "resume_id": resume_id,
        "jd_version_id": 1,
        "attempt_idempotency_key": "terminal-attempt",
        "initial_question_idempotency_key": "terminal-question",
    }
    first = client.post(base, json=payload)
    _assert_contract_response(first, "mock_interview_unverifiable")
    entry = CONTRACT_BY_CODE["mock_interview_unverifiable"]
    assert entry["disposition"] == "terminal_no_retry"
    assert entry["provider_retry_allowed"] is False
    calls_after_failure = model.calls
    replay = client.post(base, json=payload)
    _assert_contract_response(replay, "mock_interview_unverifiable")
    assert model.calls == calls_after_failure


def test_idempotency_conflict_matches_contract(tmp_path) -> None:
    client, app_id, event_id, resume_id = _application_client(tmp_path)
    base = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    payload = {
        "resume_id": resume_id,
        "jd_version_id": 1,
        "attempt_idempotency_key": "conflict-attempt",
        "initial_question_idempotency_key": "conflict-question",
    }
    assert client.post(base, json=payload).status_code == 201
    client.post(
        f"/api/applications/{app_id}/job-description/versions",
        json={
            "jd_text": "更换后的 JD 版本",
            "source_url": None,
            "expected_current_version_id": 1,
            "idempotency_key": "conflict-jd-02",
        },
    )
    conflicting = client.post(base, json={**payload, "jd_version_id": 2})
    _assert_contract_response(conflicting, "mock_interview_idempotency_conflict")
    assert CONTRACT_BY_CODE["mock_interview_idempotency_conflict"]["disposition"] == "restart_new_attempt"


def test_source_conflict_matches_contract(tmp_path) -> None:
    client, app_id, event_id, resume_id = _application_client(tmp_path)
    base = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    stale = client.post(
        base,
        json={
            "resume_id": resume_id,
            "jd_version_id": 99,
            "attempt_idempotency_key": "source-conflict-attempt",
            "initial_question_idempotency_key": "source-conflict-question",
        },
    )
    _assert_contract_response(stale, "mock_interview_source_conflict")
    assert CONTRACT_BY_CODE["mock_interview_source_conflict"]["disposition"] == "reload_source"


def test_attempt_not_found_matches_contract(tmp_path) -> None:
    client, app_id, event_id, resume_id = _application_client(tmp_path)
    response = client.post(
        f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts/999999/turns",
        json={"turn_no": 1, "answer_text": "回答", "turn_idempotency_key": "not-found-answer"},
    )
    _assert_contract_response(response, "mock_interview_attempt_not_found")


def test_answer_required_carries_contract_error_code(tmp_path) -> None:
    client, app_id, event_id, resume_id = _application_client(tmp_path)
    base = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    started = client.post(
        base,
        json={
            "resume_id": resume_id,
            "jd_version_id": 1,
            "attempt_idempotency_key": "answer-required-attempt",
            "initial_question_idempotency_key": "answer-required-question",
        },
    )
    attempt_id = started.json()["attempt_id"]
    finished = client.post(
        f"{base}/{attempt_id}/finish",
        json={"feedback_idempotency_key": "answer-required-feedback"},
    )
    _assert_contract_response(finished, "mock_interview_answer_required")
    assert CONTRACT_BY_CODE["mock_interview_answer_required"]["disposition"] == "edit_input"


def test_invalid_payload_carries_contract_error_code(tmp_path) -> None:
    client, app_id, event_id, resume_id = _application_client(tmp_path)
    response = client.post(
        f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts",
        json={
            "resume_id": resume_id,
            "jd_version_id": 1,
            "attempt_idempotency_key": "missing-question-key-attempt",
        },
    )
    _assert_contract_response(response, "mock_interview_invalid_payload")


def test_quick_attempt_not_found_matches_contract(tmp_path) -> None:
    client, case_id = _quick_case_client(tmp_path)
    response = client.post(
        f"/api/interview-practice-cases/{case_id}/mock-interview/attempts/999999/turns",
        json={"turn_no": 1, "answer_text": "回答", "turn_idempotency_key": "quick-not-found-answer"},
    )
    _assert_contract_response(response, "mock_interview_context_mismatch")


# ---------------------------------------------------------------------------
# Trace envelope
# ---------------------------------------------------------------------------


def test_trace_envelope_records_all_fields_and_no_sensitive_content(tmp_path) -> None:
    from offerpilot.reliability.trace import (
        TRACE_FIELDS,
        MockInterviewTraceEnvelope,
        read_mock_interview_traces,
        record_mock_interview_trace,
    )

    envelope = MockInterviewTraceEnvelope(
        run_id="run-1",
        scenario_id="mock_interview:application_event",
        operation_id="op-1",
        attempt_id=7,
        generation_revision=2,
        idempotency_key_hash="hash-1",
        provider="controlled",
        model="test-model",
        capability_snapshot_hash="cap-1",
        input_fingerprint="fingerprint-1",
        schema_fingerprint="schema-1",
        started_at="2026-08-17T00:00:00+00:00",
        first_byte_ms=120,
        completed_ms=340,
        provider_outcome="success",
        validator_stage="question",
        failure_category="",
        repair_count=0,
        final_disposition="success",
        response_error_code="",
    )
    with pytest.raises(ValueError):
        MockInterviewTraceEnvelope(
            **{**envelope.__dict__, "provider_outcome": {"raw": "response body"}}
        )
    record_mock_interview_trace(tmp_path, envelope)
    traces = read_mock_interview_traces(tmp_path)
    assert len(traces) == 1
    assert list(traces[0]) == list(TRACE_FIELDS)
    serialized = json.dumps(traces[0], ensure_ascii=False)
    for secret in ("api_key", "sk-", "prompt", "answer_text", "jd_text"):
        assert secret not in serialized


def test_trace_envelope_sanitizes_idempotency_key(tmp_path) -> None:
    from offerpilot.reliability.trace import hash_idempotency_key

    digest = hash_idempotency_key("attempt-secret-key-001")
    assert digest.startswith("idem-")
    assert "attempt-secret-key-001" not in digest
    assert hash_idempotency_key("attempt-secret-key-001") == digest


def test_feedback_trace_records_the_final_cas_disposition(tmp_path, monkeypatch) -> None:
    from offerpilot.reliability.trace import read_mock_interview_traces
    from offerpilot.repositories.mock_interviews import MockInterviewRepository

    client, app_id, event_id, resume_id = _application_client(tmp_path)
    base = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    started = client.post(
        base,
        json={
            "resume_id": resume_id,
            "jd_version_id": 1,
            "attempt_idempotency_key": "trace-cas-attempt",
            "initial_question_idempotency_key": "trace-cas-question",
        },
    )
    assert started.status_code == 201
    attempt_id = started.json()["attempt_id"]
    answered = client.post(
        f"{base}/{attempt_id}/turns",
        json={
            "turn_no": 1,
            "answer_text": "我通过回归测试验证修复。",
            "turn_idempotency_key": "trace-cas-answer",
        },
    )
    assert answered.status_code == 200

    monkeypatch.setattr(MockInterviewRepository, "complete_feedback", lambda *args, **kwargs: (None, False))
    finished = client.post(
        f"{base}/{attempt_id}/finish",
        json={"feedback_idempotency_key": "trace-cas-feedback"},
    )

    _assert_contract_response(finished, "mock_interview_transcript_conflict")
    trace = read_mock_interview_traces(tmp_path)[-1]
    assert trace["validator_stage"] == "feedback"
    assert trace["provider_outcome"] == "success"
    assert trace["response_error_code"] == "mock_interview_transcript_conflict"
    assert trace["final_disposition"] == "retry_same_key"


def test_provider_trace_survives_source_conflict_during_failure_finalization(
    tmp_path, monkeypatch
) -> None:
    from offerpilot.reliability.trace import read_mock_interview_traces
    from offerpilot.repositories.mock_interviews import (
        MockInterviewRepository,
        MockInterviewSourceChanged,
    )

    model = _ProviderFailureModel()
    client, app_id, event_id, resume_id = _application_client(tmp_path, model)

    def source_changed(*args, **kwargs):
        raise MockInterviewSourceChanged("source changed after provider operation")

    monkeypatch.setattr(MockInterviewRepository, "mark_provider_unknown", source_changed)
    response = client.post(
        f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts",
        json={
            "resume_id": resume_id,
            "jd_version_id": 1,
            "attempt_idempotency_key": "trace-source-conflict-attempt",
            "initial_question_idempotency_key": "trace-source-conflict-question",
        },
    )

    _assert_contract_response(response, "mock_interview_source_conflict")
    trace = read_mock_interview_traces(tmp_path)[-1]
    assert response.json()["operation_id"] == trace["operation_id"]
    assert trace["provider_outcome"] == "provider_error"
    assert trace["response_error_code"] == "mock_interview_source_conflict"
    assert trace["final_disposition"] == "reload_source"


def test_stale_contract_failure_finalization_is_reconcilable_not_terminal(
    tmp_path, monkeypatch
) -> None:
    from offerpilot.reliability.trace import read_mock_interview_traces
    from offerpilot.repositories.mock_interviews import MockInterviewRepository

    model = _ContractFailureModel()
    client, app_id, event_id, resume_id = _application_client(tmp_path, model)
    monkeypatch.setattr(
        MockInterviewRepository,
        "mark_contract_failure",
        lambda *args, **kwargs: None,
    )
    response = client.post(
        f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts",
        json={
            "resume_id": resume_id,
            "jd_version_id": 1,
            "attempt_idempotency_key": "stale-contract-attempt",
            "initial_question_idempotency_key": "stale-contract-question",
        },
    )

    _assert_contract_response(response, "mock_interview_transcript_conflict")
    trace = read_mock_interview_traces(tmp_path)[-1]
    assert trace["operation_id"] == response.json()["operation_id"]
    assert trace["response_error_code"] == "mock_interview_transcript_conflict"
    assert trace["final_disposition"] == "retry_same_key"


def test_successful_provider_source_drift_returns_correlated_trace(
    tmp_path, monkeypatch
) -> None:
    from offerpilot.reliability.trace import read_mock_interview_traces
    from offerpilot.repositories.mock_interviews import (
        MockInterviewRepository,
        MockInterviewSourceChanged,
    )

    client, app_id, event_id, resume_id = _application_client(tmp_path, _WorkingModel())

    def source_changed(*args, **kwargs):
        raise MockInterviewSourceChanged("source changed after provider success")

    monkeypatch.setattr(MockInterviewRepository, "complete_question", source_changed)
    response = client.post(
        f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts",
        json={
            "resume_id": resume_id,
            "jd_version_id": 1,
            "attempt_idempotency_key": "success-source-attempt",
            "initial_question_idempotency_key": "success-source-question",
        },
    )

    _assert_contract_response(response, "mock_interview_source_conflict")
    trace = read_mock_interview_traces(tmp_path)[-1]
    assert trace["operation_id"] == response.json()["operation_id"]
    assert trace["provider_outcome"] == "success"
    assert trace["response_error_code"] == "mock_interview_source_conflict"


def test_existing_attempt_replay_revalidates_frozen_sources(tmp_path, monkeypatch) -> None:
    from offerpilot.repositories.mock_interviews import (
        MockInterviewRepository,
        MockInterviewSourceChanged,
    )

    client, app_id, event_id, resume_id = _application_client(tmp_path, _WorkingModel())
    base = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    payload = {
        "resume_id": resume_id,
        "jd_version_id": 1,
        "attempt_idempotency_key": "revalidate-existing-attempt",
        "initial_question_idempotency_key": "revalidate-existing-question",
    }
    assert client.post(base, json=payload).status_code == 201

    def source_changed(*args, **kwargs):
        raise MockInterviewSourceChanged("frozen source is no longer visible")

    monkeypatch.setattr(
        MockInterviewRepository,
        "_assert_attempt_sources",
        staticmethod(source_changed),
    )
    replay = client.post(base, json=payload)

    _assert_contract_response(replay, "mock_interview_source_conflict")
