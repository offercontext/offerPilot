import json
from datetime import datetime, timezone
from pathlib import Path
import re

import pytest
from sqlalchemy import select

from offerpilot.ai.mock_interview import MockInterviewContractError, SAFE_EMPTY_FEEDBACK, validate_feedback
from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import (
    Application,
    ApplicationEvent,
    MockInterviewAttempt,
    MockInterviewFeedbackProposal,
    MockInterviewReviewDraft,
    MockInterviewTurn,
    Resume,
)
from offerpilot.smoke import (
    _assert_mock_interview_failed_attempt_clean,
    _assert_real_ai_smoke_data_clean,
    _capture_real_ai_browser_domain_baseline,
    _cleanup_real_ai_smoke_records,
    _assert_mock_interview_attempt_restart_state,
    _latest_mock_interview_failure_diagnostic,
    _mock_interview_attempt_state,
    _mock_interview_browser_failure_diagnostics,
    _select_mock_interview_browser_success,
)


def test_mock_interview_smoke_requires_four_array_safe_empty_shape():
    assert set(SAFE_EMPTY_FEEDBACK) == {
        "schema_version", "proposal_status", "strengths", "practice_points",
        "follow_up_questions", "next_practice_steps",
    }
    assert all(SAFE_EMPTY_FEEDBACK[field] == [] for field in (
        "strengths", "practice_points", "follow_up_questions", "next_practice_steps"
    ))


def test_browser_harness_requires_real_two_turn_draft_and_browser_network_evidence():
    script = (Path(__file__).parents[1] / "scripts" / "mock-interview-real-ai-browser-harness.ps1").read_text(encoding="utf-8")
    assert "Count -ge 2" in script
    assert "review_draft" in script
    assert "sec_fetch_mode" in script
    assert "provider_proxy_connect" in script or "provider-egress-proxy.py" in script


def test_browser_harness_allows_three_same_context_attempts_and_selects_success_by_id():
    script = (Path(__file__).parents[1] / "scripts" / "mock-interview-real-ai-browser-harness.ps1").read_text(encoding="utf-8")
    auditor = (Path(__file__).parents[1] / "scripts" / "browser-network-audit.py").read_text(encoding="utf-8")
    api = (Path(__file__).parents[1] / "src" / "offerpilot" / "api.py").read_text(encoding="utf-8")
    assert "$maxBrowserAttempts = 3" in script
    assert "flowBase" in script
    assert "createIndexes" in script
    assert "successfulAttemptId" in script
    assert "attempt_id" in script
    assert "history.items | Where-Object" in script
    assert "createRecords" in script
    assert "jd_text_sha256" in script
    assert "request_context" in auditor
    assert "attemptLifecycleDiagnostics" in script
    assert "_mock_interview_attempt_state" in script
    assert "_latest_mock_interview_failure_diagnostic" in script
    assert "mock_interview_{kind}_failure" in api
    assert "category=" in script
    assert "_assert_mock_interview_attempt_restart_state" in script


def test_browser_harness_does_not_swallow_lifecycle_failures_and_checks_final_attempt():
    script = (Path(__file__).parents[1] / "scripts" / "mock-interview-real-ai-browser-harness.ps1").read_text(encoding="utf-8")
    loop_end = script.index("if ($null -eq $history)")
    assert "Test-TransientHistoryError" in script
    assert "StatusCode" in script
    assert "return $null -eq $statusCode" in script
    assert "Browser created more than*" not in script
    assert script.rfind("foreach ($attemptId in @($knownAttemptIds") < loop_end
    assert "checkedAttemptIds -notcontains $_" in script


def test_browser_harness_prefers_modern_http_status_code_property():
    script = (Path(__file__).parents[1] / "scripts" / "mock-interview-real-ai-browser-harness.ps1").read_text(encoding="utf-8")
    status_property = script.index("$statusCode = $exception.StatusCode")
    legacy_data = script.index("$statusCode = $exception.Data['StatusCode']")
    assert status_property < legacy_data


def test_browser_harness_requires_contract_failed_attempt_to_remain_retained():
    _assert_mock_interview_attempt_restart_state(
        "contract", "contract_failed", "retained:contract_failed"
    )
    with pytest.raises(RuntimeError, match="retained-terminal"):
        _assert_mock_interview_attempt_restart_state("contract", "contract_failed", "deleted")


def test_browser_harness_rejects_deleted_provider_unknown_attempt():
    with pytest.raises(RuntimeError, match="provider-unknown"):
        _assert_mock_interview_attempt_restart_state("provider", "provider_unknown", "deleted")


def test_browser_harness_accepts_composite_provider_failure_category_when_retained():
    _assert_mock_interview_attempt_restart_state("provider", "provider_error,timeout", "retained:provider_unknown")


def test_browser_harness_rejects_provider_attempt_with_wrong_retained_state():
    with pytest.raises(RuntimeError, match="provider-unknown"):
        _assert_mock_interview_attempt_restart_state(
            "provider", "provider_http_5xx", "retained:contract_failed"
        )


def test_browser_harness_uses_attempt_scoped_provider_and_contract_diagnostics(tmp_path):
    log_path = tmp_path / "logs" / "offerpilot.log"
    log_path.parent.mkdir()
    log_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "level": "WARNING",
                        "message": 'mock_interview_provider_failure {"attempt_id":301,"stage":"feedback","failure_category":"provider_http_5xx"}',
                    }
                ),
                json.dumps(
                    {
                        "level": "WARNING",
                        "message": 'mock_interview_contract_failure {"attempt_id":302,"stage":"question","failure_category":"unknown_evidence_ref"}',
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    assert _latest_mock_interview_failure_diagnostic(tmp_path, 301, "feedback") == {
        "kind": "provider",
        "category": "provider_http_5xx",
    }
    assert _latest_mock_interview_failure_diagnostic(tmp_path, 302, "question") == {
        "kind": "contract",
        "category": "unknown_evidence_ref",
    }
    assert _latest_mock_interview_failure_diagnostic(tmp_path, 301, "question") is None


@pytest.mark.parametrize(
    "category",
    ["network_timeout", "provider_http_5xx", "proxy_failure", "response_lost", "provider_exception"],
)
def test_browser_harness_provider_failure_categories_require_retention(category):
    _assert_mock_interview_attempt_restart_state("provider", category, "retained:provider_unknown")
    with pytest.raises(RuntimeError, match="provider-unknown"):
        _assert_mock_interview_attempt_restart_state("provider", category, "deleted")


def test_browser_harness_contract_failure_requires_retention():
    _assert_mock_interview_attempt_restart_state(
        "contract", "unknown_evidence_ref", "retained:contract_failed"
    )
    with pytest.raises(RuntimeError, match="retained-terminal"):
        _assert_mock_interview_attempt_restart_state("contract", "unknown_evidence_ref", "deleted")


def test_browser_harness_source_conflict_requires_retention():
    _assert_mock_interview_attempt_restart_state(
        "contract", "source_conflict", "retained:source_conflict"
    )
    with pytest.raises(RuntimeError, match="source-conflict"):
        _assert_mock_interview_attempt_restart_state("contract", "source_conflict", "deleted")


def test_browser_harness_missing_failure_diagnostic_is_not_treated_as_unverifiable_fallback():
    with pytest.raises(RuntimeError, match="missing"):
        _assert_mock_interview_attempt_restart_state("", "", "retained:provider_unknown")


def test_browser_harness_runtime_output_is_ascii_and_has_no_encoded_prompt_path():
    script = (Path(__file__).parents[1] / "scripts" / "mock-interview-real-ai-browser-harness.ps1").read_text(encoding="utf-8")
    executable_output = [
        line for line in script.splitlines()
        if "Write-Host" in line or "throw " in line
    ]
    assert executable_output
    assert all(all(ord(character) < 128 for character in line) for line in executable_output)
    assert "FromBase64String" not in script


def test_browser_harness_records_failed_attempt_cleanup_or_retention(tmp_path):
    data_dir = tmp_path / "data"
    factory = session_factory_for_data_dir(data_dir)
    with factory() as session:
        application = Application(company_name="Smoke", position_name="QA")
        session.add(application)
        session.flush()
        event = ApplicationEvent(application_id=application.id, event_type="interview")
        resume = Resume(title="Smoke", content_json=json.dumps({}))
        session.add_all([event, resume])
        session.flush()
        attempt = MockInterviewAttempt(
            application_id=application.id, event_id=event.id, resume_id=resume.id,
            idempotency_key="attempt", input_snapshot_json="{}", source_fingerprint="source",
            attempt_status="provider_unknown", generation_revision=1,
            provider_call_token="token", transcript_fingerprint="transcript",
        )
        session.add(attempt)
        session.commit()
        application_id, event_id, resume_id, attempt_id = application.id, event.id, resume.id, attempt.id
    assert _mock_interview_attempt_state(
        data_dir, application_id, event_id, resume_id, attempt_id
    ) == "retained:provider_unknown"


def test_safe_empty_cleanup_allows_an_older_retained_terminal_attempt(tmp_path):
    data_dir = tmp_path / "data"
    factory = session_factory_for_data_dir(data_dir)
    with factory() as session:
        application = Application(company_name="Smoke", position_name="QA")
        session.add(application)
        session.flush()
        event = ApplicationEvent(application_id=application.id, event_type="interview")
        resume = Resume(title="Smoke", content_json=json.dumps({}))
        session.add_all([event, resume])
        session.flush()
        retained = MockInterviewAttempt(
            application_id=application.id,
            event_id=event.id,
            resume_id=resume.id,
            idempotency_key="retained",
            input_snapshot_json="{}",
            source_fingerprint="source",
            attempt_status="contract_failed",
            generation_revision=1,
            transcript_fingerprint="transcript-retained",
        )
        deleted = MockInterviewAttempt(
            application_id=application.id,
            event_id=event.id,
            resume_id=resume.id,
            idempotency_key="safe-empty",
            input_snapshot_json="{}",
            source_fingerprint="source",
            attempt_status="feedback_ready",
            generation_revision=1,
            transcript_fingerprint="transcript-deleted",
        )
        session.add_all([retained, deleted])
        session.commit()
        application_id, event_id, resume_id = application.id, event.id, resume.id
        deleted_id = deleted.id
    baseline = _capture_real_ai_browser_domain_baseline(
        data_dir, application_id, [event_id], [resume_id]
    )
    with factory() as session:
        session.delete(session.get(MockInterviewAttempt, deleted_id))
        session.commit()
    bind = factory.kw.get("bind")
    if bind is not None:
        bind.dispose()

    _assert_mock_interview_failed_attempt_clean(
        data_dir,
        application_id,
        event_id,
        resume_id,
        deleted_id,
        baseline,
    )


def test_browser_harness_fake_cdp_two_failures_then_success_selects_third_attempt():
    flow_base = "/api/applications/7/events/8/mock-interview/attempts"
    cdp_records = [
        {"method": "POST", "url": f"http://127.0.0.1{flow_base}"},
        {"method": "POST", "url": f"http://127.0.0.1{flow_base}/101/turns"},
        {"method": "POST", "url": f"http://127.0.0.1{flow_base}"},
        {"method": "POST", "url": f"http://127.0.0.1{flow_base}/102/turns"},
        {"method": "POST", "url": f"http://127.0.0.1{flow_base}"},
        {"method": "POST", "url": f"http://127.0.0.1{flow_base}/103/turns"},
    ]
    create_records = [record for record in cdp_records if record["url"].endswith(flow_base)]
    answer_attempt_ids = [
        int(re.search(r"/attempts/(\d+)/turns$", record["url"]).group(1))
        for record in cdp_records
        if re.search(r"/attempts/(\d+)/turns$", record["url"])
    ]
    assert len(create_records) == 3
    assert answer_attempt_ids == [101, 102, 103]
    history = [
        {"attempt_id": 101, "turns": [], "proposal_status": "unverifiable", "review_draft": None},
        {"attempt_id": 102, "turns": [{"turn_no": 1}], "proposal_status": "unverifiable", "review_draft": None},
        {
            "attempt_id": 103,
            "turns": [{"turn_no": 1}, {"turn_no": 2}],
            "proposal_status": "normal",
            "review_draft": {"status": "confirmed"},
        },
    ]
    assert _select_mock_interview_browser_success(history, [101, 102, 103])["attempt_id"] == 103
    assert _mock_interview_browser_failure_diagnostics(history[:2], [101, 102]) == [
        "attempt_101:unverifiable",
        "attempt_102:unverifiable",
    ]


def test_browser_harness_fake_cdp_three_failures_has_no_success():
    flow_base = "/api/applications/7/events/8/mock-interview/attempts"
    cdp_records = [
        {"method": "POST", "url": f"http://127.0.0.1{flow_base}"},
        {"method": "POST", "url": f"http://127.0.0.1{flow_base}/201/turns"},
        {"method": "POST", "url": f"http://127.0.0.1{flow_base}"},
        {"method": "POST", "url": f"http://127.0.0.1{flow_base}/202/turns"},
        {"method": "POST", "url": f"http://127.0.0.1{flow_base}"},
        {"method": "POST", "url": f"http://127.0.0.1{flow_base}/203/turns"},
    ]
    assert sum(record["url"].endswith(flow_base) for record in cdp_records) == 3
    history = [
        {"attempt_id": attempt_id, "turns": [], "proposal_status": "unverifiable", "review_draft": None}
        for attempt_id in (201, 202, 203)
    ]
    assert _select_mock_interview_browser_success(history, [201, 202, 203]) is None
    assert _mock_interview_browser_failure_diagnostics(history, [201, 202, 203]) == [
        "attempt_201:unverifiable",
        "attempt_202:unverifiable",
        "attempt_203:unverifiable",
    ]


def test_browser_harness_rejects_non_https_provider_before_starting_proxy():
    script = (Path(__file__).parents[1] / "scripts" / "mock-interview-real-ai-browser-harness.ps1").read_text(encoding="utf-8")
    assert "requires an HTTPS provider endpoint" in script


def test_browser_harness_requires_cdp_network_audit():
    script = (Path(__file__).parents[1] / "scripts" / "mock-interview-real-ai-browser-harness.ps1").read_text(encoding="utf-8")
    auditor = (Path(__file__).parents[1] / "scripts" / "browser-network-audit.py").read_text(encoding="utf-8")
    assert "MOCK_INTERVIEW_CDP_URL" in script
    assert "Network.requestWillBeSent" in auditor
    assert "CDP browser request audit is missing" in script


def test_browser_harness_binds_cdp_to_local_target_and_ready_handshake():
    script = (Path(__file__).parents[1] / "scripts" / "mock-interview-real-ai-browser-harness.ps1").read_text(encoding="utf-8")
    auditor = (Path(__file__).parents[1] / "scripts" / "browser-network-audit.py").read_text(encoding="utf-8")
    assert "--expected-url" in script
    assert "--ready-file" in script
    assert "--expected-url" in auditor
    assert "--ready-file" in auditor
    assert "ExitCode" in script
    assert "/json/version" in auditor
    assert "Target.createTarget" in auditor
    assert "Target.setAutoAttach" in auditor
    assert "Target.setDiscoverTargets" in auditor
    assert "Page.navigate" in auditor
    assert "Find-FlowRequestIndex" in script
    assert "review-drafts" in script
    assert "turns/[0-9]+/question" in script
    assert "answerPathMatch" in script
    assert "target_id" in script
    assert "session_id" in script


def test_mock_interview_smoke_rejects_untraceable_turn_evidence():
    proposal = {
        **SAFE_EMPTY_FEEDBACK,
        "proposal_status": "normal",
        "strengths": [{
            "id": "s1", "text": "亮点",
            "evidence_ids": ["ev_999"],
        }],
    }
    with pytest.raises(MockInterviewContractError, match="unknown_evidence_ref"):
        validate_feedback(proposal, [
            {"id": "ev_001", "source": "turn", "path": "/turns/001/answer", "value": "真实回答"},
        ])


def test_mock_interview_smoke_rejects_provider_copied_citation_shape():
    proposal = {
        **SAFE_EMPTY_FEEDBACK,
        "proposal_status": "normal",
        "strengths": [{
            "id": "s1", "text": "亮点",
            "evidence_refs": [{"source": "turn", "path": "/turns/001/answer", "excerpt": "真实回答"}],
        }],
    }
    with pytest.raises(MockInterviewContractError, match="item_shape"):
        validate_feedback(proposal, [
            {"id": "ev_001", "source": "turn", "path": "/turns/001/answer", "value": "真实回答"},
        ])


def test_mock_interview_cleanup_deletes_draft_children_before_attempt(tmp_path):
    data_dir = tmp_path / "data"
    factory = session_factory_for_data_dir(data_dir)
    with factory() as session:
        application = Application(company_name="Smoke", position_name="QA")
        session.add(application)
        session.flush()
        event = ApplicationEvent(
            application_id=application.id,
            event_type="interview",
            scheduled_at=datetime(2026, 7, 28, 10, tzinfo=timezone.utc),
        )
        resume = Resume(title="Smoke", content_json=json.dumps({}))
        session.add_all([event, resume])
        session.flush()
        attempt = MockInterviewAttempt(
            application_id=application.id, event_id=event.id, resume_id=resume.id,
            idempotency_key="attempt", input_snapshot_json="{}", source_fingerprint="source",
            attempt_status="feedback_ready", generation_revision=1, provider_call_token="token",
            transcript_fingerprint="transcript",
        )
        session.add(attempt)
        session.flush()
        turn = MockInterviewTurn(
            attempt_id=attempt.id, turn_no=1, question_idempotency_key="question",
            turn_status="answered", answer_text="answer",
        )
        proposal = MockInterviewFeedbackProposal(
            attempt_id=attempt.id, idempotency_key="feedback", input_snapshot_json="{}",
            source_fingerprint="source", transcript_fingerprint="transcript",
            proposal_json=json.dumps(SAFE_EMPTY_FEEDBACK), proposal_hash="hash",
            proposal_status="safe_empty",
        )
        session.add_all([turn, proposal])
        session.flush()
        session.add(MockInterviewReviewDraft(
            attempt_id=attempt.id, proposal_id=proposal.id, confirmation_idempotency_key="confirm",
            application_id=application.id, event_id=event.id, selected_blocks_json="[]",
            content_hash="content", source_fingerprint="source",
        ))
        session.commit()
        application_id = application.id
        resume_id = resume.id
    bind = factory.kw.get("bind")
    if bind is not None:
        bind.dispose()

    _cleanup_real_ai_smoke_records(data_dir, application_id, [resume_id])
    _assert_real_ai_smoke_data_clean(data_dir)
    factory = session_factory_for_data_dir(data_dir)
    with factory() as session:
        assert session.scalar(select(MockInterviewAttempt.id)) is None
