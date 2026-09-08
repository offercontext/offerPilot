from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from typer.testing import CliRunner

from offerpilot.ai.types import Assistant
from offerpilot.cli import app
from offerpilot.api import create_app
from offerpilot.ai.opportunity_fit_reviews import build_source_snapshot
from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import (
    Application,
    ApplicationEvent,
    ApplicationMaterialKit,
    Conversation,
    MaterialRevisionProposal,
    MockInterviewAttempt,
    MockInterviewFeedbackProposal,
    MockInterviewReviewDraft,
    MockInterviewTurn,
    OpportunityFitReview,
    OpportunityFitReviewSession,
    OpportunityFitReviewStage,
    Question,
    Resume,
    Wakeup,
)
from offerpilot.repositories.json_contract import canonical_json, sha256_text
from offerpilot.smoke import (
    SmokeStep,
    SmokeReport,
    _assert_real_ai_smoke_data_clean,
    _assert_real_ai_browser_no_cross_domain_writes,
    _capture_real_ai_browser_domain_baseline,
    _cleanup_real_ai_browser_records,
    _cleanup_real_ai_smoke_records,
    _dispose_smoke_app_database,
    _latest_mock_interview_failure_category,
    _run_real_ai_interview_review_smoke,
    _run_real_ai_interview_knowledge_capture_smoke,
    _run_real_ai_interview_preparation_smoke,
    _run_real_ai_mock_interview_smoke,
    _run_real_ai_material_proposal_smoke,
    _run_real_ai_opportunity_fit_smoke,
    _save_application_jd_version,
    _validate_interview_preparation_proposal_response,
    _validate_material_proposal_smoke_response,
    run_core_smoke,
    run_http_smoke,
)


class _MockInterviewAcceptanceModel:
    supports_json_schema = False

    def __init__(self, success_after: int | None = 5) -> None:
        self.feedback_calls = 0
        self.success_after = success_after

    def complete(self, messages, tools, **kwargs):
        if any("mock-interview-feedback-v1" in message.content for message in messages):
            self.feedback_calls += 1
            if self.success_after is None or self.feedback_calls < self.success_after:
                return Assistant(content='{"unexpected":true}')
            return Assistant(content=json.dumps({
                "schema_version": "mock-interview-feedback-v1",
                "proposal_status": "normal",
                "strengths": [{
                    "id": "strength-1",
                    "text": "回答包含具体经历。",
                    "evidence_ids": ["ev_003"],
                }],
                "practice_points": [],
                "follow_up_questions": [],
                "next_practice_steps": [],
            }, ensure_ascii=False))
        payload = json.loads(messages[-1].content)
        turn_evidence = [
            entry
            for entry in payload["evidence_catalog"]
            if entry.get("source") == "turn"
        ]
        is_follow_up = bool(turn_evidence)
        return Assistant(content=json.dumps({
            "question": (
                "你刚才提到回滚指标，具体如何验证它们一致？"
                if is_follow_up
                else "请继续说明这段经历。"
            ),
            "evidence_ids": [turn_evidence[-1]["id"] if is_follow_up else "ev_001"],
        }, ensure_ascii=False))


class _RecordingHttpClient:
    def __init__(self, client):
        self.client = client
        self.posts: list[tuple[str, dict[str, object]]] = []

    def post(self, path: str, json: dict[str, object]):
        self.posts.append((path, json.copy()))
        return self.client.post(path, json=json)

    def get(self, path: str):
        return self.client.get(path)

    def delete(self, path: str):
        return self.client.delete(path)


def _static_dir(tmp_path: Path) -> Path:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html><div id='root'></div></html>", encoding="utf-8")
    return static_dir


def test_core_smoke_runs_spa_api_and_hitl_loop(tmp_path):
    report = run_core_smoke(data_dir=tmp_path / "data", static_dir=_static_dir(tmp_path))

    assert report.ok is True
    assert [step.name for step in report.steps] == [
        "health",
        "spa",
        "create_application",
        "chat_pending",
        "confirm_action",
        "pending_cleared",
        "chat_create_application_card",
        "chat_create_event_card",
    ]


def test_cli_smoke_prints_checked_steps(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path / "data"))
    runner = CliRunner()

    result = runner.invoke(app, ["smoke", "--static-dir", str(_static_dir(tmp_path))])

    assert result.exit_code == 0
    assert "Smoke passed" in result.output
    assert "confirm_action" in result.output


def test_cli_mock_interview_verify_has_isolated_real_ai_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(
        "offerpilot.cli.run_mock_interview_real_ai_smoke",
        lambda data_dir, static_dir=None: SmokeReport(
            ok=True, steps=[SmokeStep("http_mock_interview", "complete")]
        ),
    )
    result = CliRunner().invoke(app, ["verify-mock-interview"])
    help_result = CliRunner().invoke(app, ["verify-mock-interview", "--help"])

    assert result.exit_code == 0
    assert "http_mock_interview" in result.output
    assert "隔离 Mock API 验收通过" in result.output
    assert "不替代完整 verify 或浏览器/CDP 发布证据" in result.output
    assert "隔离 Mock API 验收" in help_result.output
    assert "不替代完整" in help_result.output
    assert "浏览器/CDP" in help_result.output


def test_http_smoke_uses_real_http_and_cleans_test_application(tmp_path):
    report = run_http_smoke(data_dir=tmp_path / "data", static_dir=_static_dir(tmp_path), real_ai=False)

    assert report.ok is True
    assert [step.name for step in report.steps] == [
        "http_unconfigured_chat",
        "http_health",
        "http_settings",
        "http_spa",
        "http_create_application",
        "http_list_applications",
        "http_resume_crud",
        "http_application_event_crud",
        "http_proposal_terminal_matrix",
        "http_application_outcome_feedback",
        "http_chat_pending",
        "http_confirm_action",
        "http_pending_cleared",
        "http_chat_create_application_card",
        "http_chat_create_event_card",
        "http_cleanup",
    ]


def test_real_ai_mock_interview_smoke_restarts_unverifiable_attempts_and_confirms_success(tmp_path):
    model = _MockInterviewAcceptanceModel()
    data_dir = tmp_path / "data"
    with TestClient(create_app(data_dir=data_dir, chat_model=model)) as client:
        application = client.post(
            "/api/applications",
            json={"company_name": "Smoke", "position_name": "Engineer", "status": "interview"},
        ).json()
        steps: list[SmokeStep] = []
        recording_client = _RecordingHttpClient(client)

        _run_real_ai_mock_interview_smoke(
            recording_client, steps, application["id"], [], data_dir
        )

        resume_posts = [path for path, _ in recording_client.posts if path == "/api/resumes"]
        event_posts = [path for path, _ in recording_client.posts if path == "/api/application-events"]
        start_posts = [
            payload
            for path, payload in recording_client.posts
            if path.endswith("/mock-interview/attempts")
        ]
        assert resume_posts == ["/api/resumes"]
        assert event_posts == ["/api/application-events"]
        assert len(start_posts) == 3
        assert len({payload["resume_id"] for payload in start_posts}) == 1
        assert len({payload["jd_version_id"] for payload in start_posts}) == 1
        assert len({payload["attempt_idempotency_key"] for payload in start_posts}) == 3
        assert len({payload["initial_question_idempotency_key"] for payload in start_posts}) == 3
        round_keys = [
            value
            for _, payload in recording_client.posts
            for key, value in payload.items()
            if key.endswith("idempotency_key")
        ]
        assert len(round_keys) == len(set(round_keys))
        event_ids = {
            path.split("/")[5]
            for path, _ in recording_client.posts
            if "/mock-interview/attempts" in path
        }
        assert len(event_ids) == 1

    with session_factory_for_data_dir(data_dir)() as session:
        assert session.scalar(select(func.count()).select_from(MockInterviewAttempt)) == 3
        assert session.scalar(select(func.count()).select_from(MockInterviewTurn)) == 6
        assert session.scalar(select(func.count()).select_from(MockInterviewFeedbackProposal)) == 1
        assert session.scalar(select(func.count()).select_from(MockInterviewReviewDraft)) == 1
        draft = session.scalar(select(MockInterviewReviewDraft))
        assert draft is not None
        attempt = session.get(MockInterviewAttempt, draft.attempt_id)
        assert attempt is not None and attempt.attempt_status == "feedback_ready"
        assert str(attempt.event_id) in event_ids
        assert attempt.resume_id == start_posts[0]["resume_id"]
    assert "unverifiable" in steps[-1].detail
    assert "success" in steps[-1].detail
    assert model.feedback_calls == 5


def test_real_ai_mock_interview_smoke_fails_after_three_unverifiable_restarts(tmp_path):
    model = _MockInterviewAcceptanceModel(success_after=None)
    data_dir = tmp_path / "data"
    with TestClient(create_app(data_dir=data_dir, chat_model=model)) as client:
        application = client.post(
            "/api/applications",
            json={"company_name": "Smoke", "position_name": "Engineer", "status": "interview"},
        ).json()
        with pytest.raises(RuntimeError, match="did not complete"):
            _run_real_ai_mock_interview_smoke(client, [], application["id"], [], data_dir)

    with session_factory_for_data_dir(data_dir)() as session:
        assert session.scalar(select(func.count()).select_from(MockInterviewAttempt)) == 3
        assert session.scalar(select(func.count()).select_from(MockInterviewTurn)) == 6
        assert session.scalar(select(func.count()).select_from(MockInterviewFeedbackProposal)) == 0
        assert session.scalar(select(func.count()).select_from(MockInterviewReviewDraft)) == 0
        statuses = list(session.scalars(select(MockInterviewAttempt.attempt_status)).all())
        assert statuses == ["contract_failed", "contract_failed", "contract_failed"]
    assert model.feedback_calls == 6


def test_real_ai_mock_interview_smoke_reads_only_safe_failure_categories(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "offerpilot.log").write_text(
        'WARNING mock_interview_contract_failure '
        '{"attempt_id":77,"stage":"feedback","failure_category":"unknown_evidence_ref","failure_categories":'
        '["unknown_evidence_ref","excerpt_mismatch"],"provider_request_id":'
        '"request-redacted-abc123"}\n',
        encoding="utf-8",
    )

    assert _latest_mock_interview_failure_category(tmp_path, "feedback", 77) == (
        "unknown_evidence_ref,excerpt_mismatch"
    )


def test_real_ai_interview_preparation_smoke_retries_pending_results_with_same_request(monkeypatch):
    class Response:
        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class Client:
        def __init__(self) -> None:
            self.calls = 0
            self.proposal_requests: list[dict[str, object]] = []
            self.jd_calls = 0

        def post(self, path: str, json: dict[str, object]) -> Response:
            if path.endswith("/job-description/versions"):
                self.jd_calls += 1
                return Response(201, {"id": 12 + self.jd_calls})
            if path == "/api/resumes":
                return Response(201, {"id": 41})
            if path == "/api/application-events":
                return Response(201, _smoke_event_snapshot())
            self.calls += 1
            self.proposal_requests.append(dict(json))
            if self.calls == 2:
                return Response(
                    202,
                    {
                        "attempt_status": "provider_unknown",
                        "application_id": 7,
                        "event_id": 51,
                        "idempotency_key": "interview-preparation-smoke-2",
                        "generation_revision": 1,
                        "retry_after_ms": 1,
                    },
                )
            proposal = {
                "preparation_directions": [
                    {
                        "id": "direction-1",
                        "text": "Discuss reliable services.",
                        "evidence_refs": [
                            {
                                "source": "resume",
                                "path": "/raw_text",
                                "excerpt": "Built reliable API services; input_snapshot is a literal term here.",
                            }
                        ],
                    }
                ],
                "story_prompts": [],
                "review_points": [],
                "interviewer_questions": [],
                "items_to_clarify": [],
            }
            return Response(
                200 if self.calls == 3 else 201,
                _smoke_terminal_proposal_payload(
                    jd_version_id=int(json["jd_version_id"]),
                    proposal=proposal,
                    source_fingerprint=_smoke_request_source_fingerprint(json),
                ),
            )

    monkeypatch.setattr("offerpilot.smoke.time.sleep", lambda _: None)
    client = Client()
    steps: list[SmokeStep] = []
    _run_real_ai_interview_preparation_smoke(client, steps, 7, [])
    assert steps[-1].name == "http_interview_preparation_proposal"
    assert len(client.proposal_requests) == 4
    assert client.proposal_requests[1] == client.proposal_requests[2]


def _smoke_terminal_proposal_payload(
    *,
    application_id: int = 7,
    event_id: int = 51,
    resume_id: int = 41,
    jd_version_id: int = 13,
    proposal: dict[str, object] | None = None,
    proposal_status: str = "normal",
    source_fingerprint: str | None = None,
    proposal_hash: str | None = None,
) -> dict[str, object]:
    actual_proposal = proposal or {
        "preparation_directions": [],
        "story_prompts": [],
        "review_points": [],
        "interviewer_questions": [],
        "items_to_clarify": [],
    }
    snapshot = _smoke_evidence_snapshot()
    snapshot["event"] = _smoke_event_snapshot()
    snapshot["jd_version_id"] = jd_version_id
    snapshot["resume"] = {
        "id": resume_id,
        "content_json": snapshot["resume"]["content_json"],  # type: ignore[index]
    }
    return {
        "id": 61,
        "application_id": application_id,
        "event_id": event_id,
        "resume_id": resume_id,
        "jd_version_id": jd_version_id,
        "attempt_status": "ready",
        "proposal_status": proposal_status,
        "source_fingerprint": source_fingerprint or sha256_text(canonical_json(snapshot)),
        "source_status": "not_checked",
        "source_states": {"event": "current", "resume": "current", "jd": "current", "knowledge": "current"},
        "proposal": actual_proposal,
        "proposal_hash": proposal_hash or sha256_text(canonical_json(actual_proposal)),
        "created_at": "2026-07-24T10:00:00+00:00",
    }


def _smoke_event_snapshot() -> dict[str, object]:
    return {
        "id": 51,
        "application_id": 7,
        "event_type": "interview",
        "subtype": "technical",
        "round": 0,
        "scheduled_at": "2026-07-24T10:00:00",
        "duration_minutes": 45,
        "status": "todo",
    }


def _smoke_evidence_snapshot() -> dict[str, object]:
    return {
        "event": _smoke_event_snapshot(),
        "jd": {"text": "Build reliable Python services."},
        "jd_version_id": 13,
        "resume": {
            "id": 41,
            "content_json": {
                "raw_text": "Built reliable API services; input_snapshot is a literal term here.",
                "experience": [{"highlights": ["Built reliable API services", "Led a migration."]}],
            }
        },
        "knowledge_evidence": [],
        "user_assertions": [],
    }


def _smoke_request_source_fingerprint(request: dict[str, object]) -> str:
    snapshot = _smoke_evidence_snapshot()
    snapshot["event"] = _smoke_event_snapshot()
    jd_text_by_version = {
        13: "Build reliable Python services and explain operational tradeoffs.",
        14: "Design an API migration with safe rollback and observability.",
        15: "Review distributed systems failure handling and testing practices.",
    }
    version_id = request.get("jd_version_id")
    snapshot["jd"] = {"text": jd_text_by_version.get(version_id, "")}
    snapshot["jd_version_id"] = version_id
    snapshot["user_assertions"] = request["user_assertions"]
    snapshot["resume"] = {"id": 41, "content_json": snapshot["resume"]["content_json"]}  # type: ignore[index]
    return sha256_text(canonical_json(snapshot))


def _smoke_proposal_with_ref(evidence_ref: dict[str, str]) -> dict[str, object]:
    return {
        "preparation_directions": [
            {"id": "direction-1", "text": "Discuss the cited experience.", "evidence_refs": [evidence_ref]}
        ],
        "story_prompts": [],
        "review_points": [],
        "interviewer_questions": [],
        "items_to_clarify": [],
    }


@pytest.mark.parametrize(
    ("evidence_ref", "error_match"),
    [
        ({"source": "attacker", "path": "/raw_text", "excerpt": "Built reliable API services"}, "evidence"),
        ({"source": "resume", "path": "/missing", "excerpt": "Built reliable API services"}, "evidence"),
        ({"source": "jd", "path": "/jd/text", "excerpt": "forged requirement"}, "evidence"),
    ],
)
def test_real_ai_interview_preparation_smoke_rejects_untraceable_evidence(
    evidence_ref: dict[str, str], error_match: str
):
    with pytest.raises(RuntimeError, match=error_match):
        _validate_interview_preparation_proposal_response(
            _smoke_terminal_proposal_payload(proposal=_smoke_proposal_with_ref(evidence_ref)),
            application_id=7,
            event_id=51,
            resume_id=41,
            snapshot=_smoke_evidence_snapshot(),
        )


def test_real_ai_interview_preparation_smoke_rejects_invalid_terminal_metadata_and_status():
    snapshot = _smoke_evidence_snapshot()
    valid_ref = {"source": "resume", "path": "/raw_text", "excerpt": "Built reliable API services"}
    valid_proposal = _smoke_proposal_with_ref(valid_ref)
    invalid_bodies = [
        {**_smoke_terminal_proposal_payload(proposal=valid_proposal), "id": 0},
        {**_smoke_terminal_proposal_payload(proposal=valid_proposal), "source_fingerprint": 1},
        {**_smoke_terminal_proposal_payload(proposal=valid_proposal), "source_fingerprint": "forged"},
        {**_smoke_terminal_proposal_payload(proposal=valid_proposal), "proposal_hash": ""},
        {**_smoke_terminal_proposal_payload(proposal=valid_proposal), "proposal_hash": "forged"},
        {**_smoke_terminal_proposal_payload(proposal=valid_proposal), "source_status": {"state": "current"}},
        {
            **_smoke_terminal_proposal_payload(proposal=valid_proposal),
            "source_states": {"event": "unknown", "resume": "current", "jd": "current", "knowledge": "current"},
        },
        {**_smoke_terminal_proposal_payload(proposal=valid_proposal), "source_status": "source_changed"},
        {**_smoke_terminal_proposal_payload(proposal=valid_proposal), "created_at": "not-a-date"},
        _smoke_terminal_proposal_payload(proposal=valid_proposal, proposal_status="safe_empty"),
        _smoke_terminal_proposal_payload(proposal_status="normal"),
    ]
    for body in invalid_bodies:
        with pytest.raises(RuntimeError, match="terminal metadata|proposal status|empty proposal"):
            _validate_interview_preparation_proposal_response(
                body, application_id=7, event_id=51, resume_id=41, snapshot=snapshot
            )


def test_real_ai_interview_preparation_smoke_rejects_terminal_ownership_mismatch():
    class Response:
        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class Client:
        def __init__(self) -> None:
            self.calls = 0

        def post(self, path: str, json: dict[str, object]) -> Response:
            if path.endswith("/job-description/versions"):
                return Response(201, {"id": 13})
            if path == "/api/resumes":
                return Response(201, {"id": 41})
            if path == "/api/application-events":
                return Response(201, _smoke_event_snapshot())
            self.calls += 1
            if self.calls == 2:
                return Response(
                    202,
                    {
                        "attempt_status": "provider_unknown",
                        "application_id": 7,
                        "event_id": 51,
                        "idempotency_key": "interview-preparation-smoke-2",
                        "generation_revision": 1,
                        "retry_after_ms": 0,
                    },
                )
            return Response(
                200,
                _smoke_terminal_proposal_payload(application_id=999),
            )

    with pytest.raises(RuntimeError, match="proposal response ownership was invalid"):
        _run_real_ai_interview_preparation_smoke(Client(), [], 7, [])


def test_real_ai_interview_preparation_smoke_rejects_nested_snapshot_fields():
    class Response:
        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class Client:
        def post(self, path: str, json: dict[str, object]) -> Response:
            if path.endswith("/job-description/versions"):
                return Response(201, {"id": 13})
            if path == "/api/resumes":
                return Response(201, {"id": 41})
            if path == "/api/application-events":
                return Response(201, _smoke_event_snapshot())
            proposal = {
                "preparation_directions": [
                    {
                        "id": "direction-1",
                        "text": "Discuss the cited experience.",
                        "evidence_refs": [
                            {"source": "resume", "path": "/raw_text", "excerpt": "input_snapshot"}
                        ],
                        "input_snapshot": {"raw_text": "must not be returned"},
                    }
                ],
                "story_prompts": [],
                "review_points": [],
                "interviewer_questions": [],
                "items_to_clarify": [],
            }
            return Response(
                201,
                _smoke_terminal_proposal_payload(
                    proposal=proposal,
                    source_fingerprint=_smoke_request_source_fingerprint(json),
                ),
            )

    with pytest.raises(RuntimeError, match="proposal structure was invalid"):
        _run_real_ai_interview_preparation_smoke(Client(), [], 7, [])


def test_real_ai_interview_preparation_smoke_rejects_unknown_success_fields():
    class Response:
        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class Client:
        def post(self, path: str, json: dict[str, object]) -> Response:
            if path.endswith("/job-description/versions"):
                return Response(201, {"id": 13})
            if path == "/api/resumes":
                return Response(201, {"id": 41})
            if path == "/api/application-events":
                return Response(201, _smoke_event_snapshot())
            return Response(
                201,
                {
                    "id": 61,
                    "application_id": 7,
                    "event_id": 51,
                    "resume_id": 41,
                    "attempt_status": "ready",
                    "proposal_status": "empty",
                    "source_fingerprint": "fingerprint",
                    "source_status": "current",
                    "source_states": {},
                    "proposal": {},
                    "proposal_hash": "proposal-hash",
                    "created_at": "2026-07-24T10:00:00+00:00",
                    "input_snapshot": {"raw_text": "must not be returned"},
                },
            )

    with pytest.raises(RuntimeError, match="proposal response fields were invalid"):
        _run_real_ai_interview_preparation_smoke(Client(), [], 7, [])


def test_real_ai_interview_preparation_smoke_rejects_pending_snapshot_leak():
    class Response:
        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class Client:
        def post(self, path: str, json: dict[str, object]) -> Response:
            if path.endswith("/job-description/versions"):
                return Response(201, {"id": 13})
            if path == "/api/resumes":
                return Response(201, {"id": 41})
            if path == "/api/application-events":
                return Response(201, _smoke_event_snapshot())
            return Response(
                202,
                {
                    "attempt_status": "provider_unknown",
                    "application_id": 7,
                    "event_id": 51,
                    "idempotency_key": str(json["idempotency_key"]),
                    "generation_revision": 1,
                    "retry_after_ms": 0,
                    "input_snapshot": {"raw_text": "should never be returned"},
                },
            )

    with pytest.raises(RuntimeError, match="pending response fields were invalid"):
        _run_real_ai_interview_preparation_smoke(Client(), [], 7, [])


def test_real_ai_material_proposal_smoke_allows_empty_changes_and_hides_snapshot():
    class Response:
        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class Client:
        def __init__(self) -> None:
            self.created_resume_ids: list[int] = []
            self.deleted_resume_ids: list[int] = []

        def post(self, path: str, json: dict[str, object]) -> Response:
            if path.endswith("/job-description/versions"):
                return Response(201, {"id": 13})
            if path == "/api/resumes":
                resume_id = 41 if not self.created_resume_ids else 42
                self.created_resume_ids.append(resume_id)
                return Response(201, {"id": resume_id})
            if path.endswith("/material-kit/generate"):
                return Response(201, {"id": 7})
            if path.endswith("/material-revision-proposals"):
                return Response(
                    201,
                    {
                        "id": 8,
                        "application_id": 7,
                        "material_kit_id": 7,
                        "jd_version_id": 13,
                        "source_resume_id": 42,
                        "status": "draft",
                        "summary": "No safe changes.",
                        "proposal_sha256": "sha",
                        "result_resume_id": None,
                        "created_at": "2026-07-15T00:00:00Z",
                        "changes": [],
                        "accepted_change_ids": [],
                        "accepted_at": None,
                        "rejected_at": None,
                        "source": {
                            "application": {"id": 7, "company_name": "Smoke", "position_name": "QA"},
                            "material_kit": {"id": 7, "jd_version_id": 13, "jd_excerpt": "QA"},
                            "resume": {"id": 42, "title": "Smoke Resume"},
                            "latest_evidence_bundle": None,
                            "user_assertions": [],
                        },
                    },
                )
            raise AssertionError(path)

        def delete(self, path: str) -> Response:
            self.deleted_resume_ids.append(int(path.rsplit("/", 1)[-1]))
            return Response(200, {})

    client = Client()
    steps: list[SmokeStep] = []
    resume_ids: list[int] = []

    _run_real_ai_material_proposal_smoke(client, steps, 7, resume_ids)

    assert [step.name for step in steps] == ["http_material_proposal"]
    assert client.created_resume_ids == [41, 42]
    assert resume_ids == [41, 42]


def test_material_proposal_smoke_persists_redacted_response_diagnostic(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFERPILOT_FULL_VERIFY_REPORT_DIR", str(tmp_path))
    body = {
        "id": 8,
        "application_id": 7,
        "material_kit_id": 7,
        "jd_version_id": 13,
        "unexpected": "Secret Co",
        "source_resume_id": 42,
        "status": "draft",
        "summary": "No safe changes.",
        "proposal_sha256": "sha",
        "result_resume_id": None,
        "created_at": "2026-07-15T00:00:00Z",
        "changes": [
            {
                "id": "change-1",
                "path": "/raw_text",
                "before": "old",
                "after": "new",
                "rationale": "Use the source fact.",
                "evidence_refs": [{"source": "resume", "path": "/raw_text", "excerpt": "old"}],
            }
        ],
        "accepted_change_ids": [],
        "accepted_at": None,
        "rejected_at": None,
        "source": {
            "application": {"id": 7, "company_name": "Secret Co", "position_name": "QA"},
            "material_kit": {"id": 7, "jd_version_id": 13, "jd_excerpt": "QA"},
            "resume": {"id": 42, "title": "Resume"},
            "latest_evidence_bundle": None,
            "user_assertions": [],
        },
    }

    with pytest.raises(RuntimeError, match="leaked frozen source data"):
        _validate_material_proposal_smoke_response(body)

    diagnostic = json.loads((tmp_path / "material-proposal-diagnostic.json").read_text())
    assert diagnostic["failure_category"] == "response_root_shape"
    assert diagnostic["structure_summaries"][0]["fields"]["changes"]["length"] == 1
    assert diagnostic["evidence_counts"]["proposal"]["evidence_refs"] == 1
    assert diagnostic["repair_attempted"] is False
    assert "Secret Co" not in json.dumps(diagnostic, ensure_ascii=False)


def test_save_application_jd_version_uses_current_version_cas():
    class Response:
        status_code = 200

        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class Client:
        def __init__(self) -> None:
            self.payload: dict[str, object] | None = None

        def get(self, path: str) -> Response:
            assert path == "/api/applications/7/job-description"
            return Response({"current": {"id": 13}})

        def post(self, path: str, json: dict[str, object]) -> Response:
            assert path == "/api/applications/7/job-description/versions"
            self.payload = json
            response = Response({"id": 14})
            response.status_code = 201
            return response

    client = Client()
    assert _save_application_jd_version(client, 7, "Updated JD", "smoke-jd-cas-01") == 14
    assert client.payload is not None
    assert client.payload["expected_current_version_id"] == 13


def test_real_ai_http_smoke_isolates_config_and_removes_temporary_data(monkeypatch, tmp_path):
    import offerpilot.smoke as smoke

    source_data = tmp_path / "user-data"
    source_data.mkdir()
    config_text = '{"api_key":"not-for-output","model":"configured"}\n'
    (source_data / "config.json").write_text(config_text, encoding="utf-8")
    observed: dict[str, Path] = {}

    def fake_http_smoke(data_dir: Path, static_dir: Path | None, *, real_ai: bool) -> SmokeReport:
        observed["data_dir"] = data_dir
        assert real_ai is True
        assert data_dir != source_data
        assert (data_dir / "config.json").read_text(encoding="utf-8") == config_text
        return SmokeReport(ok=True, steps=[])

    monkeypatch.setattr(smoke, "_run_http_smoke", fake_http_smoke)

    report = run_http_smoke(source_data, real_ai=True)

    assert report.ok is True
    assert not observed["data_dir"].exists()


def test_local_http_smoke_isolates_user_data(monkeypatch, tmp_path):
    import offerpilot.smoke as smoke

    source_data = tmp_path / "user-data"
    source_data.mkdir()
    marker = source_data / "user-marker.txt"
    marker.write_text("must remain unchanged", encoding="utf-8")
    observed: dict[str, Path] = {}

    def fake_http_smoke(data_dir: Path, static_dir: Path | None, *, real_ai: bool) -> SmokeReport:
        observed["data_dir"] = data_dir
        assert real_ai is False
        assert data_dir != source_data
        (data_dir / "smoke-only.txt").write_text("isolated", encoding="utf-8")
        return SmokeReport(ok=True, steps=[])

    monkeypatch.setattr(smoke, "_run_http_smoke", fake_http_smoke)
    before = marker.read_bytes()

    report = run_http_smoke(source_data, real_ai=False)

    assert report.ok is True
    assert marker.read_bytes() == before
    assert not observed["data_dir"].exists()


def test_real_ai_smoke_does_not_cleanup_database_after_server_shutdown_failure(
    monkeypatch,
    tmp_path,
):
    from contextlib import contextmanager

    import offerpilot.smoke as smoke

    events: list[str] = []
    fake_app = SimpleNamespace(state=SimpleNamespace())

    @contextmanager
    def failed_server(app):
        raise smoke.SmokeServerShutdownError(
            "HTTP smoke server did not stop before database disposal"
        )
        yield  # pragma: no cover

    monkeypatch.setattr(smoke, "_copy_real_ai_config", lambda source, target: None)
    monkeypatch.setattr(smoke, "create_app", lambda **kwargs: fake_app)
    monkeypatch.setattr(smoke, "_running_server", failed_server)
    monkeypatch.setattr(smoke, "_dispose_smoke_app_database", lambda app: events.append("dispose"))
    monkeypatch.setattr(
        smoke,
        "_assert_real_ai_smoke_data_clean",
        lambda data_dir: events.append("assert_clean"),
    )

    with pytest.raises(smoke.SmokeServerShutdownError, match="did not stop before database disposal"):
        smoke.run_mock_interview_real_ai_smoke(tmp_path)

    assert events == []


def test_smoke_database_disposal_stops_background_knowledge_runtime(monkeypatch, tmp_path):
    app = create_app(data_dir=tmp_path / "isolated")
    calls: list[float | None] = []

    monkeypatch.setattr(
        app.state.knowledge_runtime,
        "stop",
        lambda timeout=None: (calls.append(timeout), True)[1],
    )

    _dispose_smoke_app_database(app)

    assert calls == [10]


def test_smoke_database_disposal_waits_before_engine_disposal(monkeypatch, tmp_path):
    app = create_app(data_dir=tmp_path / "isolated")
    runtime = app.state.knowledge_runtime
    calls: list[float | None] = []
    results = iter((False, True))

    monkeypatch.setattr(
        runtime,
        "stop",
        lambda timeout=None: (calls.append(timeout), next(results))[1],
    )

    _dispose_smoke_app_database(app)

    assert calls == [10, None]


def test_running_server_waits_for_shutdown_before_database_disposal(monkeypatch):
    import offerpilot.smoke as smoke

    events: list[str] = []

    class _Server:
        should_exit = False

        def run(self):
            return None

    class _Thread:
        running = True

        def start(self):
            events.append("start")

        def join(self, timeout=None):
            events.append(f"join:{timeout}")
            if timeout != 5:
                self.running = False

        def is_alive(self):
            return self.running

    server = _Server()
    thread = _Thread()
    monkeypatch.setattr(smoke, "_free_port", lambda: 18765)
    monkeypatch.setattr(smoke.uvicorn, "Config", lambda *args, **kwargs: object())
    monkeypatch.setattr(smoke.uvicorn, "Server", lambda config: server)
    monkeypatch.setattr(smoke.threading, "Thread", lambda **kwargs: thread)
    monkeypatch.setattr(
        smoke.httpx,
        "get",
        lambda *args, **kwargs: SimpleNamespace(status_code=200),
    )
    monkeypatch.setattr(
        smoke,
        "_dispose_smoke_app_database",
        lambda app: events.append(f"dispose:{thread.is_alive()}"),
    )

    with smoke._running_server(SimpleNamespace()):
        pass

    assert server.should_exit is True
    assert events[0:2] == ["start", "join:5"]
    assert events[2].startswith("join:") and events[2] != "join:None"
    assert events[3] == "dispose:False"


def test_stop_smoke_server_bounds_wait_and_preserves_live_database(monkeypatch):
    import offerpilot.smoke as smoke

    joins: list[float | None] = []
    disposed: list[bool] = []

    class _Server:
        should_exit = False

    class _Thread:
        def join(self, timeout=None):
            joins.append(timeout)

        def is_alive(self):
            return True

    monotonic_values = iter((100.0, 105.0))
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(smoke, "_dispose_smoke_app_database", lambda app: disposed.append(True))

    with pytest.raises(
        smoke.SmokeServerShutdownError,
        match="did not stop before database disposal",
    ):
        smoke._stop_smoke_server(_Server(), _Thread(), SimpleNamespace())

    assert joins == [5, 25]
    assert disposed == []


def test_smoke_database_disposal_orders_engines_after_worker_stop():
    events: list[str] = []

    class _Runtime:
        running = True

        def stop(self, timeout=None):
            events.append(f"stop:{timeout}")
            self.running = False
            return True

    class _Engine:
        def dispose(self):
            events.append("dispose")

    _dispose_smoke_app_database(SimpleNamespace(state=SimpleNamespace(
        knowledge_runtime=_Runtime(),
        db_engine=_Engine(),
        journal_db_engine=_Engine(),
    )))

    assert events == ["stop:10", "dispose", "dispose"]


def test_smoke_database_disposal_never_disposes_live_worker():
    events: list[str] = []

    class _Runtime:
        running = True

        def stop(self, timeout=None):
            events.append(f"stop:{timeout}")
            return False

    class _Engine:
        def dispose(self):
            events.append("dispose")

    with pytest.raises(RuntimeError, match="Knowledge worker did not stop"):
        _dispose_smoke_app_database(SimpleNamespace(state=SimpleNamespace(
            knowledge_runtime=_Runtime(),
            db_engine=_Engine(),
        )))

    assert events == ["stop:10", "stop:None"]


def test_smoke_database_disposal_waits_for_blocking_worker_before_dispose():
    events: list[str] = []
    release = Event()
    second_stop_entered = Event()

    class _Runtime:
        running = True

        def stop(self, timeout=None):
            events.append(f"stop:{timeout}")
            if timeout is not None:
                return False
            second_stop_entered.set()
            assert release.wait(5)
            self.running = False
            return True

    class _Engine:
        def dispose(self):
            events.append("dispose")

    runtime = _Runtime()
    engine = _Engine()
    thread = Thread(
        target=_dispose_smoke_app_database,
        args=(SimpleNamespace(
            state=SimpleNamespace(
                knowledge_runtime=runtime,
                db_engine=engine,
            )
        ),),
    )
    thread.start()
    assert second_stop_entered.wait(5)
    assert "dispose" not in events
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()

    assert events == ["stop:10", "stop:None", "dispose"]


def test_real_ai_smoke_cleanup_removes_material_records_and_active_resume(tmp_path):
    data_dir = tmp_path / "isolated"
    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        application = Application(company_name="Smoke", position_name="QA")
        session.add(application)
        session.flush()
        resume = Resume(
            title="Smoke Resume",
            is_master=True,
            content_json="{}",
            deleted_at=datetime.now(timezone.utc),
        )
        session.add(resume)
        session.flush()
        material_kit = ApplicationMaterialKit(
            application_id=application.id,
            resume_id=resume.id,
            content_json="{}",
        )
        session.add(material_kit)
        session.flush()
        session.add(
            MaterialRevisionProposal(
                application_id=application.id,
                material_kit_id=material_kit.id,
                source_resume_id=resume.id,
                source_fingerprint_sha256="source",
                source_snapshot_json="{}",
                proposal_json="{}",
                proposal_sha256="proposal",
            )
        )
        session.add(
            OpportunityFitReview(
                application_id=application.id,
                resume_id=resume.id,
                idempotency_key="f36f6d0b-1d1e-4e9a-aec1-9fef6b2f3b90",
                source_fingerprint_sha256="source",
                source_snapshot_json="{}",
                triage_json="{}",
                triage_sha256="triage",
            )
        )
        session.commit()
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()

    _cleanup_real_ai_smoke_records(data_dir, application.id, [resume.id])
    _assert_real_ai_smoke_data_clean(data_dir)


def test_real_ai_browser_cleanup_is_scoped_to_temp_data(tmp_path):
    source_data = tmp_path / "source"
    temp_data = tmp_path / "temp"
    records: dict[str, tuple[int, int]] = {}
    for name, data_dir in (("source", source_data), ("temp", temp_data)):
        session_factory = session_factory_for_data_dir(data_dir)
        with session_factory() as session:
            application = Application(company_name=f"{name} company", position_name="QA")
            resume = Resume(title=f"{name} resume", content_json="{}")
            session.add_all([application, resume])
            session.commit()
            records[name] = (application.id, resume.id)
        bind = session_factory.kw.get("bind")
        if bind is not None:
            bind.dispose()

    _cleanup_real_ai_browser_records(temp_data, records["temp"][0], [records["temp"][1]])
    _assert_real_ai_smoke_data_clean(temp_data)

    source_factory = session_factory_for_data_dir(source_data)
    with source_factory() as session:
        assert session.get(Application, records["source"][0]) is not None
        assert session.get(Resume, records["source"][1]) is not None
    bind = source_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()


def test_real_ai_browser_domain_baseline_detects_cross_domain_writes(tmp_path):
    data_dir = tmp_path / "data"
    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        application = Application(company_name="Browser smoke", position_name="QA")
        event = ApplicationEvent(application_id=0, event_type="interview", status="todo")
        resume = Resume(title="Browser smoke resume", content_json='{"skills":["Python"]}')
        session.add(application)
        session.flush()
        event.application_id = application.id
        session.add_all([event, resume])
        session.commit()
        application_id, event_id, resume_id = application.id, event.id, resume.id
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()

    baseline = _capture_real_ai_browser_domain_baseline(data_dir, application_id, [event_id], [resume_id])
    _assert_real_ai_browser_no_cross_domain_writes(
        data_dir, application_id, baseline, [event_id], [resume_id]
    )

    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        session.get(ApplicationEvent, event_id).status = "done"
        session.get(Resume, resume_id).content_json = '{"skills":["Python","SQLite"]}'
        session.commit()
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()

    with pytest.raises(RuntimeError, match="event_snapshot_hash"):
        _assert_real_ai_browser_no_cross_domain_writes(
            data_dir, application_id, baseline, [event_id], [resume_id]
        )

    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        session.get(Application, application_id).status = "offer"
        session.add(ApplicationMaterialKit(application_id=application_id, jd_snapshot="changed"))
        session.commit()
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()

    with pytest.raises(RuntimeError, match="application_snapshot_hash"):
        _assert_real_ai_browser_no_cross_domain_writes(
            data_dir, application_id, baseline, [event_id], [resume_id]
        )


def test_real_ai_browser_cleanup_removes_v2_parent_child_stages(tmp_path):
    data_dir = tmp_path / "data"
    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        application = Application(company_name="Browser smoke", position_name="QA")
        session.add(application)
        session.flush()
        review_session = OpportunityFitReviewSession(
            application_id=application.id,
            triage_idempotency_key="browser-v2-cleanup",
        )
        session.add(review_session)
        session.flush()
        triage = OpportunityFitReviewStage(
            review_id=review_session.id,
            application_id=application.id,
            stage="triage",
            idempotency_key="browser-v2-cleanup-triage",
            source_snapshot_json="{}",
            source_fingerprint_sha256="triage-fingerprint",
            proposal_json="{}",
            proposal_sha256="triage-proposal",
        )
        session.add(triage)
        session.flush()
        session.add(
            OpportunityFitReviewStage(
                review_id=review_session.id,
                application_id=application.id,
                parent_triage_stage_id=triage.id,
                stage="deep_review",
                idempotency_key="browser-v2-cleanup-deep",
                source_snapshot_json="{}",
                source_fingerprint_sha256="deep-fingerprint",
                proposal_json="{}",
                proposal_sha256="deep-proposal",
            )
        )
        session.commit()
        application_id = application.id
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()

    _cleanup_real_ai_browser_records(data_dir, application_id, [])
    _assert_real_ai_smoke_data_clean(data_dir)

    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(OpportunityFitReviewSession)) == 0
        assert session.scalar(select(func.count()).select_from(OpportunityFitReviewStage)) == 0
        assert session.scalar(select(func.count()).select_from(Application)) == 0
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()


def test_real_ai_browser_cleanup_removes_question_mock_and_reminder_records(tmp_path):
    data_dir = tmp_path / "data"
    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        application = Application(company_name="Browser smoke", position_name="QA")
        conversation = Conversation(title="browser smoke")
        session.add_all([application, conversation])
        session.flush()
        session.add_all(
            [
                Question(application_id=application.id, question="How?"),
                MockInterviewAttempt(
                    application_id=application.id,
                    event_id=0,
                    resume_id=0,
                    idempotency_key="browser-smoke",
                    input_snapshot_json="{}",
                    source_fingerprint="source",
                    attempt_status="cancelled",
                    generation_revision=1,
                    provider_call_token="token",
                    transcript_fingerprint="transcript",
                ),
                Wakeup(kind="browser-smoke", due_at=datetime(2026, 7, 27, tzinfo=timezone.utc)),
            ]
        )
        session.commit()
        application_id = application.id
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()

    _cleanup_real_ai_browser_records(data_dir, application_id, [])
    with pytest.raises(RuntimeError, match="reminders"):
        _assert_real_ai_smoke_data_clean(data_dir)

    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        session.execute(delete(Wakeup))
        session.commit()
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()
    _assert_real_ai_smoke_data_clean(data_dir)

    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Question)) == 0
        assert session.scalar(select(func.count()).select_from(MockInterviewAttempt)) == 0
        assert session.scalar(select(func.count()).select_from(Wakeup)) == 0
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()


def test_real_ai_smoke_data_clean_rejects_question_mock_and_reminder_residue(tmp_path):
    data_dir = tmp_path / "data"
    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        conversation = Conversation(title="browser smoke")
        session.add(conversation)
        session.flush()
        session.add_all(
            [
                Question(question="How?"),
                MockInterviewAttempt(
                    application_id=0,
                    event_id=0,
                    resume_id=0,
                    idempotency_key="browser-smoke",
                    input_snapshot_json="{}",
                    source_fingerprint="source",
                    attempt_status="cancelled",
                    generation_revision=1,
                    provider_call_token="token",
                    transcript_fingerprint="transcript",
                ),
                Wakeup(kind="browser-smoke", due_at=datetime(2026, 7, 27, tzinfo=timezone.utc)),
            ]
        )
        session.commit()
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()

    with pytest.raises(RuntimeError, match="questions"):
        _assert_real_ai_smoke_data_clean(data_dir)

    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        session.execute(delete(Question))
        session.commit()
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()
    with pytest.raises(RuntimeError, match="mock interview records"):
        _assert_real_ai_smoke_data_clean(data_dir)

    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        session.execute(delete(MockInterviewAttempt))
        session.commit()
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()
    with pytest.raises(RuntimeError, match="reminders"):
        _assert_real_ai_smoke_data_clean(data_dir)


def test_real_ai_browser_domain_baseline_covers_event_and_resume_file_paths(tmp_path):
    data_dir = tmp_path / "data"
    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        application = Application(company_name="Browser smoke", position_name="QA")
        event = ApplicationEvent(application_id=0, event_type="interview", status="todo")
        resume = Resume(title="Browser smoke resume", content_json="{}")
        session.add(application)
        session.flush()
        event.application_id = application.id
        session.add_all([event, resume])
        session.commit()
        application_id, event_id, resume_id = application.id, event.id, resume.id
        event_created_at, resume_created_at = event.created_at, resume.created_at
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()

    baseline = _capture_real_ai_browser_domain_baseline(data_dir, application_id, [event_id], [resume_id])
    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        session.get(ApplicationEvent, event_id).remind_at = datetime(2026, 7, 27, tzinfo=timezone.utc)
        session.commit()
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()
    with pytest.raises(RuntimeError, match="event_snapshot_hash"):
        _assert_real_ai_browser_no_cross_domain_writes(
            data_dir, application_id, baseline, [event_id], [resume_id]
        )

    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        session.get(ApplicationEvent, event_id).remind_at = None
        session.get(ApplicationEvent, event_id).created_at = datetime(2026, 7, 26, tzinfo=timezone.utc)
        session.commit()
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()
    with pytest.raises(RuntimeError, match="event_snapshot_hash"):
        _assert_real_ai_browser_no_cross_domain_writes(
            data_dir, application_id, baseline, [event_id], [resume_id]
        )

    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        session.get(ApplicationEvent, event_id).created_at = event_created_at
        session.get(Resume, resume_id).file_path = "C:/resume.pdf"
        session.commit()
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()
    with pytest.raises(RuntimeError, match="resume_snapshot_hash"):
        _assert_real_ai_browser_no_cross_domain_writes(
            data_dir, application_id, baseline, [event_id], [resume_id]
        )

    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        session.get(Resume, resume_id).file_path = ""
        session.get(Resume, resume_id).source_file_path = "C:/source.docx"
        session.get(Resume, resume_id).created_at = datetime(2026, 7, 26, tzinfo=timezone.utc)
        session.commit()
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()
    with pytest.raises(RuntimeError, match="resume_snapshot_hash"):
        _assert_real_ai_browser_no_cross_domain_writes(
            data_dir, application_id, baseline, [event_id], [resume_id]
        )

    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        resume = session.get(Resume, resume_id)
        resume.source_file_path = ""
        resume.created_at = resume_created_at
        session.commit()
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("company_name", "Changed company"),
        ("position_name", "Changed position"),
        ("job_url", "https://example.test/job"),
        ("status", "closed"),
        ("source", "changed-source"),
        ("notes", "changed notes"),
        ("applied_at", datetime(2026, 7, 26, tzinfo=timezone.utc)),
        ("first_pending_at", datetime(2026, 7, 26, tzinfo=timezone.utc)),
        ("first_applied_at", datetime(2026, 7, 26, tzinfo=timezone.utc)),
        ("first_written_test_at", datetime(2026, 7, 26, tzinfo=timezone.utc)),
        ("first_interview_at", datetime(2026, 7, 26, tzinfo=timezone.utc)),
        ("first_offer_at", datetime(2026, 7, 26, tzinfo=timezone.utc)),
        ("closed_reason", "changed reason"),
        ("closed_at", datetime(2026, 7, 26, tzinfo=timezone.utc)),
        ("deleted_at", datetime(2026, 7, 26, tzinfo=timezone.utc)),
        ("created_at", datetime(2026, 7, 26, tzinfo=timezone.utc)),
        ("updated_at", datetime(2026, 7, 26, tzinfo=timezone.utc)),
    ],
)
def test_real_ai_browser_domain_baseline_covers_application_fields(tmp_path, field, value):
    data_dir = tmp_path / "data"
    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        application = Application(company_name="Browser smoke", position_name="QA")
        session.add(application)
        session.commit()
        application_id = application.id
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()

    baseline = _capture_real_ai_browser_domain_baseline(data_dir, application_id)
    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        setattr(session.get(Application, application_id), field, value)
        session.commit()
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()

    with pytest.raises(RuntimeError, match="application_snapshot_hash"):
        _assert_real_ai_browser_no_cross_domain_writes(data_dir, application_id, baseline)


def test_real_ai_browser_domain_baseline_detects_added_application(tmp_path):
    data_dir = tmp_path / "data"
    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        application = Application(company_name="Browser smoke", position_name="QA")
        session.add(application)
        session.commit()
        application_id = application.id
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()

    baseline = _capture_real_ai_browser_domain_baseline(data_dir, application_id)
    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        session.add(Application(company_name="Unexpected application", position_name="QA"))
        session.commit()
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()

    with pytest.raises(RuntimeError, match="application_count"):
        _assert_real_ai_browser_no_cross_domain_writes(data_dir, application_id, baseline)


def test_real_ai_browser_domain_baseline_covers_opportunity_fit_v2(tmp_path):
    data_dir = tmp_path / "data"
    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        application = Application(company_name="Browser smoke", position_name="QA")
        session.add(application)
        session.commit()
        application_id = application.id
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()

    baseline = _capture_real_ai_browser_domain_baseline(data_dir, application_id)
    session_factory = session_factory_for_data_dir(data_dir)
    with session_factory() as session:
        review_session = OpportunityFitReviewSession(
            application_id=application_id,
            triage_idempotency_key="browser-v2-baseline",
        )
        session.add(review_session)
        session.flush()
        session.add(
            OpportunityFitReviewStage(
                review_id=review_session.id,
                application_id=application_id,
                stage="triage",
                idempotency_key="browser-v2-stage-baseline",
                source_snapshot_json="{}",
                source_fingerprint_sha256="fingerprint",
                proposal_json="{}",
                proposal_sha256="proposal",
            )
        )
        session.commit()
    bind = session_factory.kw.get("bind")
    if bind is not None:
        bind.dispose()

    with pytest.raises(RuntimeError, match="opportunity_fit_session_count"):
        _assert_real_ai_browser_no_cross_domain_writes(data_dir, application_id, baseline)


def test_real_ai_browser_harness_isolated_and_uses_base_url():
    harness = Path(__file__).parents[1] / "scripts" / "pilot-real-ai-browser-harness.ps1"
    source = harness.read_text(encoding="utf-8")
    assert "OFFERPILOT_DATA" in source
    assert "Copy-Item" in source
    assert "Get-NetTCPConnection" in source
    assert "Get-TreeIds" in source
    assert "http://127.0.0.1:$port" in source
    assert "/api/application-events" in source
    assert "optionally generate an AI note preview" in source
    assert "top-level 面试" in source
    assert "准备面试" in source
    assert "面试准备建议 drawer" in source
    assert "Do not substitute the application-detail 材料包 action" in source
    assert "_capture_real_ai_browser_domain_baseline" in source
    assert "_assert_real_ai_browser_no_cross_domain_writes" in source
    assert "PILOT_BROWSER_HARNESS_BASELINE_JSON" in source
    assert "interview-preparation boundary assertion" in source
    assert source.index("_assert_real_ai_browser_no_cross_domain_writes") < source.index(
        "_cleanup_real_ai_browser_records"
    )
    assert "$baseUrl/applications/$applicationId" not in source
    assert "_cleanup_real_ai_browser_records" in source
    assert "if ($LASTEXITCODE -ne 0)" in source
    assert source.count("if ($LASTEXITCODE -ne 0)") >= 2


def test_real_ai_material_proposal_smoke_rejects_renamed_snapshot_leak():
    class Response:
        status_code = 201

        def json(self) -> dict[str, object]:
            return {
                "id": 8,
                "status": "draft",
                "changes": [],
                "source": {"frozen_resume_payload": {"raw_text": "secret"}},
            }

    class Client:
        def post(self, path: str, json: dict[str, object]) -> Response:
            if path.endswith("/job-description/versions"):
                return Response()
            if path == "/api/resumes":
                return Response()
            if path.endswith("/material-kit/generate"):
                return Response()
            if path.endswith("/material-revision-proposals"):
                return Response()
            raise AssertionError(path)

        def delete(self, path: str) -> Response:
            response = Response()
            response.status_code = 200
            return response

    with pytest.raises(RuntimeError, match="leaked frozen source data"):
        _run_real_ai_material_proposal_smoke(Client(), [], 7)


def test_real_ai_opportunity_fit_smoke_requires_verified_triage_without_snapshot_leak():
    class Response:
        def __init__(self, payload: dict[str, object] | None = None, status_code: int = 201) -> None:
            self.status_code = status_code
            self._payload = payload or {}

        def json(self) -> dict[str, object]:
            return self._payload

    class Client:
        def __init__(self) -> None:
            self.seen_payloads: list[dict[str, object]] = []

        def _snapshot(self) -> dict[str, object]:
            return build_source_snapshot(
                application_id=7,
                company_name="Smoke Company",
                position_name="Smoke Position",
                resume_id=41,
                resume_title="AI Opportunity Fit Smoke Resume",
                resume_content={"raw_text": "Built API services and led migration.", "skills": ["Python"]},
                jd_text="Build reliable API quality workflows.",
                jd_source_label="Smoke pasted JD",
                candidate_assertions=["I led the migration."],
            )

        def _stage(self, stage: str, status: str, stage_id: int, parent: int | None = None) -> dict[str, object]:
            snapshot = self._snapshot()
            proposal = {
                "schema_version": 2,
                "stage": stage,
                "source": {
                    "kind": "opportunity_fit",
                    "contract_version": "opportunity_fit.v2",
                    "snapshot_version": "1",
                },
                "summary": {
                    "text": "Built API services support the role.",
                    "rationale": "The resume records API service work.",
                    "evidence_refs": [
                        {"source": "resume", "path": "/raw_text", "excerpt": "Built API services"}
                    ],
                },
                "conditions": [
                    {
                        "id": "api",
                        "text": "Confirm the scope of API service work.",
                        "rationale": "The resume contains API service work.",
                        "evidence_refs": [
                            {"source": "resume", "path": "/raw_text", "excerpt": "Built API services"}
                        ],
                    }
                ],
                "risks": [
                    {
                        "id": "quality",
                        "text": "Quality workflow scope needs confirmation.",
                        "rationale": "The JD asks for reliable API quality workflows.",
                        "evidence_refs": [
                            {
                                "source": "jd",
                                "path": "/jd_text",
                                "excerpt": "Build reliable API quality workflows.",
                            }
                        ],
                    }
                ],
                "questions": [],
                "next_steps": [],
            }
            return {
                "id": stage_id,
                "review_id": 8,
                "stage_id": stage_id,
                "application_id": 7,
                "resume_id": 41,
                "jd_version_id": 13,
                "stage": stage,
                "schema_version": 2,
                "stage_status": status,
                "parent_triage_stage_id": parent,
                "idempotency_key": f"smoke-{stage}-{stage_id}",
                "source_fingerprint_sha256": sha256_text(canonical_json(snapshot)),
                "proposal_sha256": sha256_text(canonical_json(proposal)),
                "created_at": "2026-07-28T00:00:00Z",
                "proposal": proposal,
                **({"confirmation_token": "smoke-confirmation-token"} if stage == "triage" and status == "ready" else {}),
            }

        def post(self, path: str, json: dict[str, object] | None = None) -> Response:
            if path.endswith("/job-description/versions"):
                return Response({"id": 13}, status_code=201)
            if path == "/api/resumes":
                return Response({"id": 41})
            if path.endswith("opportunity-fit-reviews"):
                assert json is not None
                self.seen_payloads.append(json)
                return Response(self._stage("triage", "ready", 8))
            if path.endswith("/triage/8/confirm"):
                return Response(self._stage("triage", "confirmed", 8), status_code=200)
            if path.endswith("deep-review"):
                assert json is not None
                self.seen_payloads.append(json)
                return Response(self._stage("deep_review", "ready", 9, parent=8))
            raise AssertionError(path)

        def get(self, path: str) -> Response:
            if path == "/api/applications/7":
                return Response({"company_name": "Smoke Company", "position_name": "Smoke Position"}, status_code=200)
            if path == "/api/applications/7/job-description":
                return Response({"current": None}, status_code=200)
            if "schema_version=2" in path:
                return Response(
                    {"schema_version": 2, "stages": [{"stage": "triage"}, {"stage": "deep_review"}]},
                    status_code=200,
                )
            raise AssertionError(path)

    steps: list[SmokeStep] = []
    client = Client()
    _run_real_ai_opportunity_fit_smoke(client, steps, 7)
    assert client.seen_payloads[0]["schema_version"] == 2
    assert client.seen_payloads[1]["schema_version"] == 2
    assert [step.name for step in steps] == [
        "http_opportunity_fit_review",
        "http_opportunity_fit_deep_review",
    ]


def test_cli_verify_local_runs_http_smoke(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    marker = data_dir / "user-marker.txt"
    marker.write_text("must remain unchanged", encoding="utf-8")
    (data_dir / "config.json").write_text('{"model":"local-test"}\n', encoding="utf-8")
    before = {
        path.relative_to(data_dir).as_posix(): path.read_bytes()
        for path in data_dir.rglob("*")
        if path.is_file()
    }
    monkeypatch.setenv("OFFERPILOT_DATA", str(data_dir))
    runner = CliRunner()

    result = runner.invoke(app, ["verify", "--profile", "local", "--static-dir", str(_static_dir(tmp_path))])

    assert result.exit_code == 0
    assert "Verify local passed" in result.output
    assert "http_unconfigured_chat" in result.output
    assert "http_resume_crud" in result.output
    assert "http_application_event_crud" in result.output
    assert "http_health" in result.output
    assert "http_confirm_action" in result.output
    after = {
        path.relative_to(data_dir).as_posix(): path.read_bytes()
        for path in data_dir.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_real_ai_interview_review_smoke_allows_verified_evidence_excerpt():
    class Response:
        status_code = 201

        def __init__(self, payload: dict[str, object], status_code: int = 201) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class Client:
        def __init__(self) -> None:
            self.event_calls = 0
            self.note_calls = 0
            self.proposal_calls = 0

        def post(self, path: str, json: dict[str, object]) -> Response:
            if path == "/api/application-events":
                assert json["event_type"] == "interview"
                self.event_calls += 1
                return Response(
                    {
                        "id": 30 + self.event_calls,
                        "application_id": 7,
                        "event_type": "interview",
                    }
                )
            if path == "/api/applications/7/notes":
                self.note_calls += 1
                assert json["application_event_id"] == 30 + self.note_calls
                return Response(
                    {"id": 31 + self.note_calls, "application_event_id": 30 + self.note_calls}
                )
            if path.startswith("/api/notes/") and path.endswith("/interview-review-proposals"):
                assert set(json) == {"idempotency_key"}
                self.proposal_calls += 1
                excerpts = [
                    "SMOKE_PRIVATE_INTERVIEW_QUESTION: explain the migration rollback plan",
                    "SMOKE_PRIVATE_INTERVIEW_REFLECTION: I omitted the failure mode initially.",
                    "SMOKE_PRIVATE_INTERVIEW_DIFFICULTY: prioritizing the first diagnostic step",
                ]
                paths = ["/questions", "/self_reflection", "/difficulty_points"]
                return Response(
                    {
                        "id": 33,
                        "note_id": 32,
                        "application_event_id": 31,
                        "source_status": "current",
                        "proposal": {
                            "summary": {
                                "text": "本次复盘记录不足以形成有依据的表现判断，请先补充待澄清问题。",
                                "evidence_refs": [
                                        {
                                            "source": "interview_note",
                                            "path": paths[self.proposal_calls - 1],
                                            "excerpt": excerpts[self.proposal_calls - 1],
                                        }
                                ],
                            },
                            "observations": [],
                            "clarifications": [],
                            "practice_focuses": [],
                            "next_questions": [],
                        },
                        "proposal_hash": "hash",
                        "source_fingerprint": "fingerprint",
                        "created_at": "2026-07-22T00:00:00Z",
                    }
                )
            raise AssertionError(path)

    steps: list[SmokeStep] = []
    _run_real_ai_interview_review_smoke(Client(), steps, 7)

    assert [step.name for step in steps] == ["http_interview_review_proposal"]


def test_real_ai_interview_knowledge_capture_smoke_requires_confirmed_history():
    class Response:
        status_code = 201

        def __init__(self, payload: dict[str, object], status_code: int = 201) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class Client:
        def post(self, path: str, json: dict[str, object]) -> Response:
            if path == "/api/application-events":
                return Response({"id": 31})
            if path == "/api/applications/7/notes":
                return Response({"id": 32})
            if path == "/api/notes/32/knowledge-capture/preview":
                assert json["mode"] == "ai"
                return Response(
                    {
                        "attempt_key": "real-ai-interview-knowledge",
                        "note_fingerprint": "fingerprint",
                        "preview": {"title": "safe", "blocks": []},
                    },
                    status_code=200,
                )
            if path == "/api/notes/32/knowledge-capture/confirm":
                return Response({"version_id": 12, "source_id": 13, "content": {}})
            raise AssertionError(path)

        def get(self, path: str) -> Response:
            if path == "/api/knowledge/notes":
                return Response({"items": [{"id": 12}]}, status_code=200)
            raise AssertionError(path)

    steps: list[SmokeStep] = []
    _run_real_ai_interview_knowledge_capture_smoke(Client(), steps, 7)
    assert [step.name for step in steps] == ["http_interview_knowledge_capture"]
