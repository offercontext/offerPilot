import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from offerpilot.ai.types import Assistant
from offerpilot.api import create_app


class _MockInterviewModel:
    supports_json_schema = False

    def complete(self, messages, tools, **kwargs):
        if any("mock-interview-feedback-v1" in message.content for message in messages):
            return Assistant(
                content='{"schema_version":"mock-interview-feedback-v1","proposal_status":"safe_empty","strengths":[],"practice_points":[],"follow_up_questions":[],"next_practice_steps":[]}'
            )
        return Assistant(
            content='{"question":"请结合 JD 说明你会如何准备。","evidence_ids":["ev_001"]}'
        )


class _InvalidQuestionModel:
    supports_json_schema = False
    calls = 0

    def complete(self, messages, tools, **kwargs):
        self.calls += 1
        return Assistant(content='{"unexpected":"raw model output"}')


class _StructuralRepairQuestionModel:
    supports_json_schema = False

    def __init__(self, second_response):
        self.second_response = second_response
        self.calls = 0

    def complete(self, messages, tools, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return Assistant(content='{"question":"Q","evidence_ids":[null]}')
        if isinstance(self.second_response, Exception):
            raise self.second_response
        return Assistant(content=self.second_response)


class _OverLimitQuestionModel:
    supports_json_schema = False

    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools, **kwargs):
        self.calls += 1
        return Assistant(content=json.dumps({
            "question": "Q",
            "evidence_ids": ["ev_001"] * 5,
        }, ensure_ascii=False))


class _DuplicateFollowUpModel:
    supports_json_schema = False

    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools, **kwargs):
        self.calls += 1
        evidence_id = "ev_001" if self.calls == 1 else "ev_003"
        return Assistant(content=json.dumps({
            "question": "请介绍一次 Python 项目。",
            "evidence_ids": [evidence_id],
        }, ensure_ascii=False))


class _BlankFeedbackRepairModel:
    supports_json_schema = False

    def __init__(self, *, repair_succeeds: bool = True):
        self.feedback_calls = 0
        self.repair_succeeds = repair_succeeds

    def complete(self, messages, tools, **kwargs):
        if any("mock-interview-feedback-v1" in message.content for message in messages):
            self.feedback_calls += 1
            if self.feedback_calls == 1 or not self.repair_succeeds:
                return Assistant(content=json.dumps({
                    "schema_version": "mock-interview-feedback-v1",
                    "proposal_status": "normal",
                    "strengths": [{
                        "id": "strength-1",
                        "text": "",
                        "evidence_ids": ["ev_003"],
                    }],
                    "practice_points": [],
                    "follow_up_questions": [],
                    "next_practice_steps": [],
                }, ensure_ascii=False))
            return Assistant(content=json.dumps({
                "schema_version": "mock-interview-feedback-v1",
                "proposal_status": "safe_empty",
                "strengths": [],
                "practice_points": [],
                "follow_up_questions": [],
                "next_practice_steps": [],
            }, ensure_ascii=False))
        return Assistant(content=json.dumps({
            "question": "请结合 JD 说明你会如何准备。",
            "evidence_ids": ["ev_001"],
        }, ensure_ascii=False))


def _client(tmp_path, model=None):
    client = TestClient(create_app(data_dir=tmp_path, chat_model=model or _MockInterviewModel()))
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
    resume = client.post(
        "/api/resumes",
        json={"title": "Resume", "text": "Python engineer"},
    ).json()
    client.post(
        f"/api/applications/{application['id']}/job-description/versions",
        json={
            "jd_text": "需要 Python",
            "source_url": None,
            "expected_current_version_id": None,
            "idempotency_key": "mock-interview-jd-01",
        },
    )
    return client, application["id"], event["id"], resume["id"]


def test_start_requires_selected_resume_and_nonempty_jd(tmp_path):
    client, app_id, event_id, resume_id = _client(tmp_path)
    path = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"

    missing_resume = client.post(path, json={"jd_version_id": 1, "attempt_idempotency_key": "a", "initial_question_idempotency_key": "q"})
    empty_jd = client.post(path, json={"resume_id": resume_id, "jd_version_id": None, "attempt_idempotency_key": "b", "initial_question_idempotency_key": "q"})

    assert missing_resume.status_code == 422
    assert empty_jd.status_code == 422


def test_start_and_same_key_replay_are_idempotent(tmp_path):
    client, app_id, event_id, resume_id = _client(tmp_path)
    path = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    payload = {
        "resume_id": resume_id,
        "jd_version_id": 1,
        "attempt_idempotency_key": "attempt-1",
        "initial_question_idempotency_key": "question-1",
    }

    first = client.post(path, json=payload)
    replay = client.post(path, json=payload)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert first.json()["attempt_id"] == replay.json()["attempt_id"]
    assert first.json()["turn"]["question"]
    assert "请介绍一次" not in first.json()["turn"]["question"]


def test_existing_attempt_rejects_boolean_jd_version_id_before_replay(tmp_path):
    client, app_id, event_id, resume_id = _client(tmp_path)
    path = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    payload = {
        "resume_id": resume_id,
        "jd_version_id": 1,
        "attempt_idempotency_key": "attempt-bool-version",
        "initial_question_idempotency_key": "question-bool-version",
    }

    first = client.post(path, json=payload)
    assert first.status_code == 201

    replay_with_bool = client.post(path, json={**payload, "jd_version_id": True})

    assert replay_with_bool.status_code == 422
    assert replay_with_bool.json()["error_code"] == "application_jd_version_required"


def test_replay_of_frozen_attempt_survives_jd_update(tmp_path):
    client, app_id, event_id, resume_id = _client(tmp_path)
    path = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    payload = {
        "resume_id": resume_id,
        "jd_version_id": 1,
        "attempt_idempotency_key": "frozen-attempt-replay-01",
        "initial_question_idempotency_key": "frozen-question-replay-01",
    }
    first = client.post(path, json=payload)
    assert first.status_code in {200, 201, 202}
    updated = client.post(
        f"/api/applications/{app_id}/job-description/versions",
        json={
            "jd_text": "Updated Python interview JD",
            "source_url": None,
            "expected_current_version_id": 1,
            "idempotency_key": "frozen-attempt-replay-jd-01",
        },
    )
    assert updated.status_code == 201
    replay = client.post(path, json=payload)
    assert replay.status_code in {200, 202}
    assert replay.json()["attempt_id"] == first.json()["attempt_id"]


def test_contract_failure_logs_only_safe_category(tmp_path):
    client, app_id, event_id, resume_id = _client(tmp_path, _InvalidQuestionModel())
    path = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"

    response = client.post(
        path,
        json={
            "resume_id": resume_id,
            "jd_version_id": 1,
            "attempt_idempotency_key": "diagnostic-attempt",
            "initial_question_idempotency_key": "diagnostic-question",
        },
    )

    assert response.status_code == 502
    assert response.json()["error_code"] == "mock_interview_unverifiable"
    assert isinstance(response.json().get("attempt_id"), int)
    assert "raw model output" not in response.text
    log_text = (tmp_path / "logs" / "offerpilot.log").read_text(encoding="utf-8")
    assert "mock_interview_contract_failure" in log_text
    assert "unexpected_field" in log_text
    assert "raw model output" not in log_text
    assert "Reliability engineering interview JD" not in log_text


def test_structural_question_failure_is_repaired_once(tmp_path):
    model = _StructuralRepairQuestionModel(
        '{"question":"请结合 Python 经验回答。","evidence_ids":["ev_001"]}'
    )
    client, app_id, event_id, resume_id = _client(tmp_path, model)

    response = client.post(
        f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts",
        json={
            "resume_id": resume_id,
            "jd_version_id": 1,
            "attempt_idempotency_key": "repair-success",
            "initial_question_idempotency_key": "repair-question",
        },
    )

    assert response.status_code == 201
    assert model.calls == 2


def test_repair_provider_failure_preserves_original_key(tmp_path):
    model = _StructuralRepairQuestionModel(RuntimeError("provider raw secret"))
    client, app_id, event_id, resume_id = _client(tmp_path, model)
    path = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    payload = {
        "resume_id": resume_id,
        "jd_version_id": 1,
        "attempt_idempotency_key": "repair-provider-unknown",
        "initial_question_idempotency_key": "repair-provider-question",
    }

    first = client.post(path, json=payload)
    replay = client.post(path, json=payload)

    assert first.status_code == 502
    assert first.json()["error_code"] == "mock_interview_provider_error"
    assert isinstance(first.json().get("attempt_id"), int)
    assert "provider raw secret" not in first.text
    assert replay.status_code == 202
    assert replay.json()["attempt_status"] == "provider_unknown"
    assert model.calls == 2
    log_text = (tmp_path / "logs" / "offerpilot.log").read_text(encoding="utf-8")
    assert "provider raw secret" not in log_text
    assert "JD Python" not in log_text


def test_repeated_structural_failure_is_terminal_and_replay_skips_provider(tmp_path):
    model = _StructuralRepairQuestionModel(
        '{"question":"Q2","evidence_ids":[null]}'
    )
    client, app_id, event_id, resume_id = _client(tmp_path, model)
    path = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    payload = {
        "resume_id": resume_id,
        "jd_version_id": 1,
        "attempt_idempotency_key": "repair-terminal",
        "initial_question_idempotency_key": "repair-terminal-question",
    }

    first = client.post(path, json=payload)
    calls_after_failure = model.calls
    replay = client.post(path, json=payload)

    assert first.status_code == 502
    assert first.json()["error_code"] == "mock_interview_unverifiable"
    assert replay.status_code == 502
    assert replay.json()["error_code"] == "mock_interview_unverifiable"
    assert first.json()["attempt_id"] == replay.json()["attempt_id"]
    assert calls_after_failure == 2
    assert model.calls == calls_after_failure


def test_duplicate_follow_up_is_terminal_and_replay_skips_provider(tmp_path):
    model = _DuplicateFollowUpModel()
    client, app_id, event_id, resume_id = _client(tmp_path, model)
    base = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    started = client.post(base, json={
        "resume_id": resume_id,
        "jd_version_id": 1,
        "attempt_idempotency_key": "duplicate-follow-up-attempt",
        "initial_question_idempotency_key": "duplicate-follow-up-first-question",
    }).json()
    attempt_id = started["attempt_id"]
    answered = client.post(
        f"{base}/{attempt_id}/turns",
        json={"turn_no": 1, "answer_text": "我使用 Python 开发过服务。", "turn_idempotency_key": "duplicate-follow-up-answer"},
    )
    question_path = f"{base}/{attempt_id}/turns/2/question"
    payload = {"question_idempotency_key": "duplicate-follow-up-second-question"}

    first = client.post(question_path, json=payload)
    replay = client.post(question_path, json=payload)

    assert answered.status_code == 200
    assert first.status_code == 502
    assert first.json()["error_code"] == "mock_interview_unverifiable"
    assert replay.status_code == 502
    assert replay.json()["error_code"] == "mock_interview_unverifiable"
    assert model.calls == 2


def test_over_limit_failure_is_terminal_without_retry_or_replay_provider_call(tmp_path):
    model = _OverLimitQuestionModel()
    client, app_id, event_id, resume_id = _client(tmp_path, model)
    path = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    payload = {
        "resume_id": resume_id,
        "jd_version_id": 1,
        "attempt_idempotency_key": "over-limit-terminal",
        "initial_question_idempotency_key": "over-limit-question",
    }

    first = client.post(path, json=payload)
    replay = client.post(path, json=payload)

    assert first.status_code == 502
    assert first.json()["error_code"] == "mock_interview_unverifiable"
    assert replay.status_code == 502
    assert replay.json()["error_code"] == "mock_interview_unverifiable"
    assert first.json()["attempt_id"] == replay.json()["attempt_id"]
    assert model.calls == 1


def test_start_rejects_cross_application_event(tmp_path):
    client, app_id, event_id, resume_id = _client(tmp_path)
    second = client.post(
        "/api/applications",
        json={"company_name": "Other", "position_name": "Other", "status": "interview"},
    ).json()
    path = f"/api/applications/{second['id']}/events/{event_id}/mock-interview/attempts"

    response = client.post(
        path,
        json={
            "resume_id": resume_id,
            "jd_version_id": 1,
            "attempt_idempotency_key": "attempt-1",
            "initial_question_idempotency_key": "question-1",
        },
    )

    assert response.status_code == 409


def test_submit_answer_and_finish_persist_safe_empty_feedback(tmp_path):
    client, app_id, event_id, resume_id = _client(tmp_path)
    base = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    started = client.post(
        base,
        json={
            "resume_id": resume_id,
            "jd_version_id": 1,
            "attempt_idempotency_key": "attempt-1",
            "initial_question_idempotency_key": "question-1",
        },
    ).json()
    attempt_id = started["attempt_id"]

    answered = client.post(
        f"{base}/{attempt_id}/turns",
        json={"turn_no": 1, "answer_text": "我做过 Python 服务", "turn_idempotency_key": "answer-1"},
    )
    finished = client.post(
        f"{base}/{attempt_id}/finish",
        json={"feedback_idempotency_key": "feedback-1"},
    )

    assert answered.status_code == 200
    assert finished.status_code == 201
    assert finished.json()["proposal_status"] == "safe_empty"


def test_finish_repairs_blank_feedback_once_and_returns_verified_result(tmp_path):
    model = _BlankFeedbackRepairModel()
    client, app_id, event_id, resume_id = _client(tmp_path, model)
    base = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    started = client.post(
        base,
        json={
            "resume_id": resume_id,
            "jd_version_id": 1,
            "attempt_idempotency_key": "attempt-blank-feedback",
            "initial_question_idempotency_key": "question-blank-feedback",
        },
    ).json()
    attempt_id = started["attempt_id"]
    answered = client.post(
        f"{base}/{attempt_id}/turns",
        json={
            "turn_no": 1,
            "answer_text": "我做过 Python 服务",
            "turn_idempotency_key": "answer-blank-feedback",
        },
    )

    finished = client.post(
        f"{base}/{attempt_id}/finish",
        json={"feedback_idempotency_key": "feedback-blank-feedback"},
    )

    assert answered.status_code == 200
    assert finished.status_code == 201
    assert finished.json()["proposal_status"] == "safe_empty"
    assert model.feedback_calls == 2


def test_finish_rejects_repeated_blank_feedback_after_one_repair(tmp_path):
    model = _BlankFeedbackRepairModel(repair_succeeds=False)
    client, app_id, event_id, resume_id = _client(tmp_path, model)
    base = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    started = client.post(
        base,
        json={
            "resume_id": resume_id,
            "jd_version_id": 1,
            "attempt_idempotency_key": "attempt-repeated-blank-feedback",
            "initial_question_idempotency_key": "question-repeated-blank-feedback",
        },
    ).json()
    attempt_id = started["attempt_id"]
    answered = client.post(
        f"{base}/{attempt_id}/turns",
        json={
            "turn_no": 1,
            "answer_text": "我做过 Python 服务",
            "turn_idempotency_key": "answer-repeated-blank-feedback",
        },
    )

    finished = client.post(
        f"{base}/{attempt_id}/finish",
        json={"feedback_idempotency_key": "feedback-repeated-blank-feedback"},
    )

    assert answered.status_code == 200
    assert finished.status_code == 502
    assert finished.json()["error_code"] == "mock_interview_unverifiable"
    assert model.feedback_calls == 2


def test_contract_failure_is_terminal_for_same_attempt_key(tmp_path):
    model = _InvalidQuestionModel()
    client, app_id, event_id, resume_id = _client(tmp_path, model)
    path = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    payload = {
        "resume_id": resume_id,
        "jd_version_id": 1,
        "attempt_idempotency_key": "attempt-contract",
        "initial_question_idempotency_key": "question-contract",
    }

    first = client.post(path, json=payload)
    first_calls = model.calls
    second = client.post(path, json=payload)

    assert first.status_code == 502
    assert second.status_code == 502
    assert first.json()["error_code"] == "mock_interview_unverifiable"
    assert second.json()["error_code"] == "mock_interview_unverifiable"
    assert first.json()["attempt_id"] == second.json()["attempt_id"]
    assert first_calls == 2
    assert model.calls == first_calls


def test_delete_unconfirmed_attempt_is_idempotent(tmp_path):
    client, app_id, event_id, resume_id = _client(tmp_path)
    base = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    started = client.post(base, json={
        "resume_id": resume_id,
        "jd_version_id": 1,
        "attempt_idempotency_key": "attempt-delete",
        "initial_question_idempotency_key": "question-delete",
    })
    attempt_id = started.json()["attempt_id"]

    deleted = client.delete(f"{base}/{attempt_id}")
    replay_delete = client.delete(f"{base}/{attempt_id}")

    assert deleted.status_code == 200
    assert replay_delete.status_code == 200


def test_delete_feedback_proposal_without_confirmed_draft_removes_attempt(tmp_path):
    client, app_id, event_id, resume_id = _client(tmp_path)
    base = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    started = client.post(base, json={
        "resume_id": resume_id,
        "jd_version_id": 1,
        "attempt_idempotency_key": "attempt-proposal-delete",
        "initial_question_idempotency_key": "question-proposal-delete",
    }).json()
    attempt_id = started["attempt_id"]
    client.post(
        f"{base}/{attempt_id}/turns",
        json={"turn_no": 1, "answer_text": "My answer", "turn_idempotency_key": "answer-proposal-delete"},
    )
    finished = client.post(
        f"{base}/{attempt_id}/finish",
        json={"feedback_idempotency_key": "feedback-proposal-delete"},
    )
    assert finished.status_code == 201

    deleted = client.delete(f"{base}/{attempt_id}")

    assert deleted.status_code == 200
    history = client.get(base)
    assert history.status_code == 200
    assert history.json()["items"] == []


def test_ready_question_replay_with_different_key_is_conflict(tmp_path):
    client, app_id, event_id, resume_id = _client(tmp_path)
    base = f"/api/applications/{app_id}/events/{event_id}/mock-interview/attempts"
    started = client.post(base, json={
        "resume_id": resume_id,
        "jd_version_id": 1,
        "attempt_idempotency_key": "attempt-question-replay",
        "initial_question_idempotency_key": "question-original",
    }).json()
    response = client.post(
        f"{base}/{started['attempt_id']}/turns/1/question",
        json={"question_idempotency_key": "question-different"},
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "mock_interview_turn_idempotency_conflict"
