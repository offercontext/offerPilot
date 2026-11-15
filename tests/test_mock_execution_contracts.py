"""One provider/claim/commit matrix for quick practice and event-bound interviews."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from itertools import count
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from offerpilot.ai.types import Assistant
from offerpilot.api import create_app
from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import (
    Application,
    ApplicationEvent,
    InterviewPracticeCase,
    MockInterviewAttempt,
)
from offerpilot.repositories.application_jd_versions import ApplicationJDService
from offerpilot.repositories.interview_practice_cases import InterviewPracticeCaseRepository
from offerpilot.repositories.mock_interviews import MockInterviewRepository
from offerpilot.repositories.resumes import ResumeCreate, ResumesRepository
from offerpilot.reliability.trace import read_mock_interview_traces


class MatrixModel:
    supports_json_schema = False

    def __init__(self):
        self.calls = 0
        self.error = None
        self.invalid = False
        self.on_complete = None

    def complete(self, messages, tools, **kwargs):
        self.calls += 1
        if self.on_complete is not None:
            self.on_complete()
        if self.error is not None:
            raise self.error
        if self.invalid:
            return Assistant(content="NOT JSON private-provider-output")
        payload = json.loads(messages[-1].content)
        if any("mock-interview-feedback-v1" in message.content for message in messages):
            return Assistant(
                content=json.dumps(
                    {
                        "schema_version": "mock-interview-feedback-v1",
                        "proposal_status": "safe_empty",
                        "strengths": [],
                        "practice_points": [],
                        "follow_up_questions": [],
                        "next_practice_steps": [],
                    }
                )
            )
        turn_evidence = [item for item in payload["evidence_catalog"] if item["source"] == "turn"]
        return Assistant(
            content=json.dumps(
                {
                    "question": "如何验证刚才的方案？"
                    if turn_evidence
                    else "请介绍你的 Python 经验。",
                    "evidence_ids": [turn_evidence[-1]["id"], "ev_001"]
                    if turn_evidence
                    else ["ev_001"],
                }
            )
        )


@pytest.fixture(scope="module")
def mock_environment(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("mock-execution-contract")
    factory = session_factory_for_data_dir(data_dir)
    model = MatrixModel()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("offerpilot.api.session_factory_for_data_dir", lambda _: factory)
        client = TestClient(create_app(data_dir=data_dir, chat_model=model))
    yield data_dir, factory, model, client, count(1)
    client.close()
    factory.kw["bind"].dispose()


@pytest.fixture(params=["quick_practice", "application_event"])
def scenario(request, mock_environment):
    data_dir, factory, model, client, sequence = mock_environment
    number = next(sequence)
    model.__init__()
    resume = ResumesRepository(factory).create(ResumeCreate(name="Resume", parsed_data="Python"))
    with factory() as session:
        application = Application(company_name="Acme", position_name="Backend", status="interview")
        session.add(application)
        session.flush()
        event = ApplicationEvent(
            application_id=application.id,
            event_type="interview",
            status="todo",
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
            duration_minutes=30,
        )
        session.add(event)
        session.commit()
    versions = ApplicationJDService(factory)
    jd = versions.create_version(
        application.id,
        jd_text="private-jd-python",
        source_url=None,
        source_kind="ui",
        expected_current_version_id=None,
        idempotency_key=f"mock-matrix-jd-{number:04d}",
    ).version
    case, _ = InterviewPracticeCaseRepository(factory).create_or_replay(
        idempotency_key=f"mock-matrix-case-{number}",
        position_name="Backend",
        jd_text="private-jd-python",
        resume_id=resume.id,
    )

    class Scenario:
        kind = request.param
        attempt_id = None
        stage = "start"
        attempt_key = f"mock-matrix-attempt-{number}"
        question_key = f"mock-matrix-question-{number}"
        feedback_key = f"mock-matrix-feedback-{number}"
        base = (
            f"/api/interview-practice-cases/{case.id}/mock-interview/attempts"
            if kind == "quick_practice"
            else f"/api/applications/{application.id}/events/{event.id}/mock-interview/attempts"
        )

        def start(self, *, new_key=False):
            payload = {
                "attempt_idempotency_key": self.attempt_key + ("-new" if new_key else ""),
                "initial_question_idempotency_key": self.question_key,
            }
            if self.kind == "application_event":
                payload.update({"resume_id": resume.id, "jd_version_id": jd.id})
            response = client.post(self.base, json=payload)
            if response.status_code in (200, 201):
                self.attempt_id = response.json()["attempt_id"]
            return response

        def answer(self):
            response = client.post(
                f"{self.base}/{self.attempt_id}/turns",
                json={
                    "turn_no": 1,
                    "answer_text": "private-answer-python",
                    "turn_idempotency_key": f"mock-matrix-answer-{number}",
                },
            )
            assert response.status_code in (200, 201), response.text

        def prepare(self, stage):
            self.stage = stage
            if stage != "start":
                response = self.start()
                assert response.status_code == 201, response.text
                self.answer()
            model.calls = 0

        def invoke(self):
            if self.stage == "start":
                return self.start()
            if self.stage == "next":
                return client.post(
                    f"{self.base}/{self.attempt_id}/turns/2/question",
                    json={
                        "question_idempotency_key": self.question_key + "-2",
                    },
                )
            return client.post(
                f"{self.base}/{self.attempt_id}/finish",
                json={
                    "feedback_idempotency_key": self.feedback_key,
                },
            )

        def attempt(self):
            with factory() as session:
                return session.scalar(
                    select(MockInterviewAttempt).where(
                        MockInterviewAttempt.idempotency_key == self.attempt_key,
                    )
                )

        def replace_owner(self):
            with factory() as session:
                attempt = session.get(MockInterviewAttempt, self.attempt().id)
                attempt.generation_revision += 1
                attempt.provider_call_token = "new-owner-token"
                session.commit()

        def invalidate_source(self):
            with factory() as session:
                if self.kind == "quick_practice":
                    # Frozen quick sources must fail closed if their persisted bytes change.
                    session.get(
                        InterviewPracticeCase, case.id
                    ).jd_text_snapshot = "corrupt-snapshot"
                else:
                    session.get(ApplicationEvent, event.id).status = "cancelled"
                session.commit()

        def evolve_source(self):
            if self.kind == "quick_practice":
                InterviewPracticeCaseRepository(factory).archive(case.id)
            else:
                versions.create_version(
                    application.id,
                    jd_text="new JD",
                    source_url=None,
                    source_kind="ui",
                    expected_current_version_id=jd.id,
                    idempotency_key=f"mock-matrix-new-jd-{number}",
                )

        def traces(self):
            return [
                item
                for item in read_mock_interview_traces(data_dir)
                if item["attempt_id"] == self.attempt().id
            ]

    result = Scenario()
    result.model = model
    result.repository = MockInterviewRepository(factory)
    return result


@pytest.mark.parametrize("stage", ["start", "next", "feedback"])
def test_success_and_same_key_replay_share_one_provider_call(scenario, stage):
    scenario.prepare(stage)
    result = scenario.invoke()
    assert result.status_code == 201, result.text
    trace = scenario.traces()[-1]
    assert trace["scenario_id"] == f"mock_interview:{scenario.kind}:" + (
        "feedback" if stage == "feedback" else "question"
    )
    assert trace["operation_id"] == result.json()["operation_id"]
    replay = scenario.invoke()
    assert replay.status_code == 200, replay.text
    assert scenario.model.calls == 1
    if stage != "feedback":
        assert replay.json()["turn"] == result.json()["turn"]
    else:
        assert replay.json()["proposal_id"] == result.json()["proposal_id"]
    assert len(scenario.traces()) == (1 if stage == "start" else 2)


@pytest.mark.parametrize("stage", ["start", "next", "feedback"])
def test_provider_unknown_never_retries_automatically_or_leaks_text(scenario, stage):
    scenario.prepare(stage)
    scenario.model.error = RuntimeError("sk-provider-secret private-provider-output")
    response = scenario.invoke()
    assert response.status_code == 502
    expected = (
        "mock_interview_provider_error"
        if scenario.kind == "application_event"
        else "mock_interview_feedback_result_unknown"
        if stage == "feedback"
        else "mock_interview_question_result_unknown"
    )
    assert response.json()["error_code"] == expected
    assert scenario.attempt().attempt_status == "provider_unknown"
    scenario.invoke()
    assert scenario.model.calls == 1
    trace = scenario.traces()[-1]
    assert trace["response_error_code"] == expected
    output = json.dumps([response.json(), trace])
    for private in (
        "sk-provider-secret",
        "private-provider-output",
        "private-jd",
        "private-answer",
        scenario.question_key,
        scenario.feedback_key,
    ):
        assert private not in output


@pytest.mark.parametrize("stage", ["start", "next", "feedback"])
def test_contract_failure_has_one_repair_and_stays_terminal(scenario, stage):
    scenario.prepare(stage)
    scenario.model.invalid = True
    response = scenario.invoke()
    assert response.status_code == 502
    assert response.json()["error_code"] == "mock_interview_unverifiable"
    assert scenario.attempt().attempt_status == "contract_failed"
    assert scenario.model.calls == 2
    replay = scenario.invoke()
    assert replay.status_code == 502
    assert replay.json()["error_code"] == "mock_interview_unverifiable"
    assert scenario.model.calls == 2
    assert scenario.traces()[-1]["repair_count"] == 1
    assert "private-provider-output" not in response.text


@pytest.mark.parametrize("stage", ["start", "next", "feedback"])
def test_late_owner_cannot_commit_or_overwrite_current_owner(scenario, stage):
    scenario.prepare(stage)
    scenario.model.on_complete = scenario.replace_owner
    response = scenario.invoke()
    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == "mock_interview_transcript_conflict"
    assert scenario.attempt().provider_call_token == "new-owner-token"
    assert scenario.model.calls == 1
    if stage == "feedback":
        assert (
            scenario.repository.get_feedback(scenario.attempt().id, scenario.feedback_key)[0]
            is None
        )
    else:
        turn = scenario.repository.get_turn(scenario.attempt().id, 1 if stage == "start" else 2)
        assert not turn.question_text


@pytest.mark.parametrize("stage", ["start", "next", "feedback"])
def test_source_withdrawal_during_generation_fails_closed(scenario, stage):
    scenario.prepare(stage)
    scenario.model.on_complete = scenario.invalidate_source
    response = scenario.invoke()
    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == "mock_interview_source_conflict"
    assert scenario.attempt().attempt_status == "source_conflict"
    assert scenario.model.calls == 1
    assert scenario.traces()[-1]["response_error_code"] == "mock_interview_source_conflict"


@pytest.mark.parametrize("stage", ["start", "next", "feedback"])
def test_concurrent_same_key_has_one_claim_winner(scenario, stage):
    scenario.prepare(stage)
    entered, release = Event(), Event()

    def block_provider():
        entered.set()
        assert release.wait(10)

    scenario.model.on_complete = block_provider
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(scenario.invoke)
        try:
            assert entered.wait(10)
            response = scenario.invoke()
            assert response.status_code == 202, response.text
            assert scenario.model.calls == 1
        finally:
            release.set()
        assert future.result(timeout=10).status_code == 201
    assert scenario.model.calls == 1


def test_new_jd_or_archived_case_keeps_existing_frozen_attempt(scenario):
    scenario.prepare("next")
    scenario.evolve_source()
    response = scenario.invoke()
    assert response.status_code == 201, response.text
    # A new start cannot reuse the old JD/current case, but the existing snapshot
    # remains available instead of rewriting or deleting historical content.
    new_start = scenario.start(new_key=True)
    assert new_start.status_code in (404, 409), new_start.text
    assert scenario.model.calls == 1


@pytest.mark.parametrize("stage", ["start", "next", "feedback"])
def test_missing_model_keeps_context_specific_unknown_result(scenario, stage, monkeypatch):
    from fastapi.responses import JSONResponse

    scenario.prepare(stage)
    # Exercise the HTTP adapter's real JSONResponse -> unavailable-model mapping.
    monkeypatch.setattr(
        "offerpilot.api._chat_model",
        lambda *args: JSONResponse({"error": "AI is not configured"}, status_code=503),
    )
    response = scenario.invoke()
    assert response.status_code == 502, response.text
    expected = (
        "mock_interview_provider_error"
        if scenario.kind == "application_event"
        else "mock_interview_feedback_result_unknown"
        if stage == "feedback"
        else "mock_interview_question_result_unknown"
    )
    assert response.json()["error_code"] == expected
    assert scenario.attempt().attempt_status == "provider_unknown"
    assert scenario.model.calls == 0
    trace = scenario.traces()[-1]
    assert trace["provider"] == "none"
    assert trace["response_error_code"] == expected


def test_feedback_without_answers_is_rejected_before_model_loading(scenario, monkeypatch):
    assert scenario.start().status_code == 201
    scenario.stage = "feedback"

    def must_not_load(*args):
        raise AssertionError("feedback without answers must not load the model")

    monkeypatch.setattr("offerpilot.api._chat_model", must_not_load)
    response = scenario.invoke()
    assert response.status_code == 422, response.text
    assert response.json()["error_code"] == "mock_interview_answer_required"
    assert scenario.attempt().attempt_status == "awaiting_answer"


def test_feedback_helper_without_answer_evidence_remains_safe_empty_without_model():
    from offerpilot.ai.mock_interview import SAFE_EMPTY_FEEDBACK, generate_feedback

    proposal, diagnostic = generate_feedback(None, {}, [])
    assert proposal == SAFE_EMPTY_FEEDBACK
    assert diagnostic["failure_category"] == "no_answer_evidence"


@pytest.mark.parametrize("stage", ["start", "next", "feedback"])
@pytest.mark.parametrize("failure", ["provider", "contract"])
@pytest.mark.parametrize("change", ["owner", "source"])
def test_failure_finalization_cannot_overwrite_new_owner_or_withdrawn_source(
    scenario,
    stage,
    failure,
    change,
):
    scenario.prepare(stage)
    scenario.model.on_complete = (
        scenario.replace_owner if change == "owner" else scenario.invalidate_source
    )
    if failure == "provider":
        scenario.model.error = RuntimeError("private-provider-error")
    else:
        scenario.model.invalid = True
    response = scenario.invoke()
    expected = (
        "mock_interview_transcript_conflict"
        if change == "owner"
        else "mock_interview_source_conflict"
    )
    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == expected
    assert scenario.model.calls == (1 if failure == "provider" else 2)
    attempt = scenario.attempt()
    if change == "owner":
        assert attempt.provider_call_token == "new-owner-token"
        assert attempt.attempt_status == (
            "generating_feedback" if stage == "feedback" else "generating_question"
        )
    else:
        assert attempt.attempt_status == "source_conflict"
    trace = scenario.traces()[-1]
    assert trace["operation_id"] == response.json()["operation_id"]
    assert trace["response_error_code"] == expected
    assert trace["provider_outcome"] == (
        "provider_error" if failure == "provider" else "unverifiable"
    )
