from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from offerpilot.ai.types import Assistant
from offerpilot.api import create_app
from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import MockInterviewFeedbackProposal
from offerpilot.repositories.json_contract import canonical_json, sha256_text


class _MockInterviewModel:
    supports_json_schema = False

    def complete(self, messages, tools, **kwargs):
        return Assistant(content='{"question":"Describe the Python service tradeoff.","evidence_ids":["ev_001"]}')


def _setup(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path, chat_model=_MockInterviewModel()))
    application = client.post(
        "/api/applications",
        json={"company_name": "Acme", "position_name": "Engineer", "status": "interview"},
    ).json()
    event = client.post(
        "/api/application-events",
        json={
            "application_id": application["id"],
            "event_type": "interview",
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "duration_minutes": 30,
        },
    ).json()
    resume = client.post("/api/resumes", json={"title": "Resume", "text": "Python"}).json()
    jd_version = client.post(
        f"/api/applications/{application['id']}/job-description/versions",
        json={
            "jd_text": "需要 Python",
            "source_url": None,
            "expected_current_version_id": None,
            "idempotency_key": "mock-review-jd-1",
        },
    ).json()
    started = client.post(
        f"/api/applications/{application['id']}/events/{event['id']}/mock-interview/attempts",
        json={
            "resume_id": resume["id"],
            "jd_version_id": jd_version["id"],
            "attempt_idempotency_key": "attempt-1",
            "initial_question_idempotency_key": "question-1",
        },
    ).json()
    answered = client.post(
        f"/api/applications/{application['id']}/events/{event['id']}/mock-interview/attempts/{started['attempt_id']}/turns",
        json={"turn_no": 1, "answer_text": "我做过 Python 服务", "turn_idempotency_key": "answer-1"},
    )
    assert answered.status_code == 200
    factory = session_factory_for_data_dir(tmp_path)
    with factory() as session:
        proposal_json = {
            "schema_version": "mock-interview-feedback-v1",
            "proposal_status": "normal",
            "strengths": [
                {
                    "id": "strength-1",
                    "text": "回答包含 Python 服务经历",
                    "evidence_refs": [
                        {"source": "turn", "path": "/turns/1/answer", "excerpt": "Python 服务"}
                    ],
                }
            ],
            "practice_points": [],
            "follow_up_questions": [],
            "next_practice_steps": [],
        }
        attempt_id = started["attempt_id"]
        attempt = session.get(__import__("offerpilot.models", fromlist=["MockInterviewAttempt"]).MockInterviewAttempt, attempt_id)
        assert attempt is not None
        encoded = canonical_json(proposal_json)
        proposal = MockInterviewFeedbackProposal(
            attempt_id=attempt_id,
            idempotency_key="feedback-1",
            input_snapshot_json=attempt.input_snapshot_json,
            source_fingerprint=attempt.source_fingerprint,
            transcript_fingerprint=attempt.transcript_fingerprint,
            proposal_json=encoded,
            proposal_hash=sha256_text(encoded),
            proposal_status="normal",
        )
        session.add(proposal)
        session.commit()
        proposal_id = proposal.id
    return client, application["id"], event["id"], started["attempt_id"], proposal_id


def test_confirm_selected_feedback_creates_one_independent_review_draft(tmp_path):
    client, app_id, event_id, attempt_id, proposal_id = _setup(tmp_path)
    path = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts/{attempt_id}/review-drafts"
    response = client.post(
        path,
        json={
            "proposal_id": proposal_id,
            "confirmation_idempotency_key": "confirm-1",
            "selected_blocks": [
                {
                    "id": "strength-1",
                    "text": "我的编辑说明",
                    "evidence_refs": [
                        {"source": "turn", "path": "/turns/1/answer", "excerpt": "Python 服务"}
                    ],
                }
            ],
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "confirmed"

    replay = client.post(
        path,
        json={
            "proposal_id": proposal_id,
            "confirmation_idempotency_key": "confirm-1",
            "selected_blocks": [
                {
                    "id": "strength-1",
                    "text": "我的编辑说明",
                    "evidence_refs": [
                        {"source": "turn", "path": "/turns/1/answer", "excerpt": "Python 服务"}
                    ],
                }
            ],
        },
    )
    assert replay.status_code == 200
    assert replay.json()["draft_id"] == response.json()["draft_id"]


def test_confirm_requires_explicit_confirmation_and_valid_selected_blocks(tmp_path):
    client, app_id, event_id, attempt_id, proposal_id = _setup(tmp_path)
    path = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts/{attempt_id}/review-drafts"
    missing = client.post(path, json={"proposal_id": proposal_id, "selected_blocks": []})
    invalid = client.post(
        path,
        json={
            "proposal_id": proposal_id,
            "confirmation_idempotency_key": "confirm-2",
            "selected_blocks": [{"id": "not-in-proposal", "text": "x", "evidence_refs": []}],
        },
    )
    assert missing.status_code == 422
    assert invalid.status_code == 422


def test_confirm_rejects_proposal_from_another_attempt_context(tmp_path):
    client, app_id, event_id, attempt_id, proposal_id = _setup(tmp_path)
    other_app = client.post(
        "/api/applications",
        json={"company_name": "Other", "position_name": "Engineer", "status": "interview"},
    ).json()
    other_event = client.post(
        "/api/application-events",
        json={
            "application_id": other_app["id"],
            "event_type": "interview",
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "duration_minutes": 30,
        },
    ).json()
    path = f"/api/applications/{other_app['id']}/events/{other_event['id']}/mock-interview/attempts/{attempt_id}/review-drafts"
    response = client.post(
        path,
        json={
            "proposal_id": proposal_id,
            "confirmation_idempotency_key": "cross-context-confirm",
            "selected_blocks": [],
        },
    )
    assert response.status_code in {404, 422}
