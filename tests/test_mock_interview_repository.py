import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from offerpilot.db import init_database
from offerpilot.models import (
    Application,
    ApplicationEvent,
    ApplicationJDVersion,
    InterviewPreparationProposal,
    MockInterviewAttempt,
    MockInterviewTurn,
    Resume,
)
from offerpilot.repositories.mock_interviews import (
    MockInterviewIdempotencyConflict,
    MockInterviewRepository,
    MockInterviewSourceChanged,
    MockInterviewTurnIdempotencyConflict,
    provider_mock_interview_snapshot,
)


def _seed(factory):
    with factory() as session:
        application = Application(company_name="Acme", position_name="Engineer", status="interview")
        session.add(application)
        session.flush()
        event = ApplicationEvent(
            application_id=application.id,
            event_type="interview",
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
            status="todo",
        )
        second_event = ApplicationEvent(
            application_id=application.id,
            event_type="interview",
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=2),
            status="todo",
        )
        resume = Resume(title="Resume", parsed_data="Python", content_json=json.dumps({"skills": ["Python"]}))
        other_resume = Resume(title="Other", parsed_data="Go", content_json=json.dumps({"skills": ["Go"]}))
        session.add_all([event, second_event, resume, other_resume])
        session.commit()
        return application.id, event.id, second_event.id, resume.id, other_resume.id


def _start_ready(repo, *args, **kwargs):
    result = repo.create_or_replay_start(*args, **kwargs)
    if result.question_claim is not None:
        revision, token, transcript_fingerprint = result.question_claim
        repo.complete_question(
            result.attempt.id,
            1,
            revision,
            token,
            transcript_fingerprint,
            "Question grounded in the JD.",
        )
    return result


def test_start_requires_visible_scheduled_interview_and_selected_resume(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)

    result = _start_ready(repo,
        app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    )
    attempt = result.attempt

    assert attempt.application_id == app_id
    assert attempt.event_id == event_id
    assert attempt.resume_id == resume_id


def test_start_rejects_empty_jd_without_provider_call(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)

    with pytest.raises(ValueError, match="jd_text"):
        repo.create_or_replay_start(app_id, event_id, resume_id, "  ", None, "attempt-1", "question-1")


def test_answer_updates_transcript_not_source_fingerprint(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    result = _start_ready(repo,
        app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    )
    attempt, turn = result.attempt, result.turn
    source_fingerprint = attempt.source_fingerprint

    updated = repo.submit_answer(attempt.id, 1, "My answer", "answer-1")

    assert updated.source_fingerprint == source_fingerprint
    assert updated.transcript_fingerprint != attempt.transcript_fingerprint
    assert updated.current_turn_no == 1
    assert turn.question_idempotency_key == "question-1"


def test_jd_update_marks_source_changed_without_interrupting_frozen_attempt(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    with factory() as session:
        version = ApplicationJDVersion(
            application_id=app_id,
            version_number=1,
            jd_text="JD v1",
            content_sha256="version-one",
            source_kind="ui",
            idempotency_key="jd-version-one",
            request_fingerprint_sha256="request-one",
        )
        session.add(version)
        session.commit()
        version_id = version.id
    repo = MockInterviewRepository(factory)
    result = _start_ready(
        repo,
        app_id,
        event_id,
        resume_id,
        "JD v1",
        None,
        "attempt-1",
        "question-1",
        jd_version_id=version_id,
    )
    with factory() as session:
        session.add(
            ApplicationJDVersion(
                application_id=app_id,
                version_number=2,
                jd_text="JD v2",
                content_sha256="version-two",
                source_kind="ui",
                idempotency_key="jd-version-two",
                request_fingerprint_sha256="request-two",
            )
        )
        session.commit()

    assert repo.source_status(result.attempt.id) == "source_changed"
    updated = repo.submit_answer(result.attempt.id, 1, "继续使用冻结的 JD 回答。", "answer-1")
    assert updated.id == result.attempt.id


def test_legacy_attempt_without_jd_version_is_not_marked_current(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    result = _start_ready(
        repo,
        app_id,
        event_id,
        resume_id,
        "Legacy JD",
        None,
        "attempt-legacy",
        "question-legacy",
    )

    with factory() as session:
        session.add(
            ApplicationJDVersion(
                application_id=app_id,
                version_number=1,
                jd_text="Current JD",
                content_sha256="current-version",
                source_kind="ui",
                idempotency_key="jd-current-version",
                request_fingerprint_sha256="request-current-version",
            )
        )
        session.commit()

    assert repo.source_status(result.attempt.id) == "source_changed"


def test_same_attempt_key_same_input_replays_existing_attempt(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    first_result = _start_ready(repo,
        app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    )
    first, first_turn = first_result.attempt, first_result.turn

    replay_result = _start_ready(repo,
        app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    )
    replay, replay_turn = replay_result.attempt, replay_result.turn

    assert replay.id == first.id
    assert replay_turn.id == first_turn.id


def test_same_attempt_key_different_input_returns_idempotency_conflict(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, other_resume_id = _seed(factory)
    repo = MockInterviewRepository(factory)
    repo.create_or_replay_start(app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1")

    with pytest.raises(MockInterviewIdempotencyConflict):
        repo.create_or_replay_start(app_id, event_id, other_resume_id, "JD text", None, "attempt-1", "question-2")


def test_same_turn_key_different_answer_returns_turn_idempotency_conflict(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    attempt = _start_ready(repo,
        app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    ).attempt
    repo.submit_answer(attempt.id, 1, "first", "answer-1")

    with pytest.raises(MockInterviewTurnIdempotencyConflict):
        repo.submit_answer(attempt.id, 1, "second", "answer-1")


def test_editing_submitted_answer_requires_new_attempt(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    attempt = _start_ready(repo,
        app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    ).attempt
    repo.submit_answer(attempt.id, 1, "first", "answer-1")

    with pytest.raises(MockInterviewTurnIdempotencyConflict):
        repo.submit_answer(attempt.id, 1, "edited", "answer-2")


def test_initial_question_key_is_persisted_on_first_turn(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)

    result = _start_ready(repo,
        app_id, event_id, resume_id, "JD text", None, "attempt-1", "initial-question-1"
    )
    turn = result.turn

    assert turn.question_idempotency_key == "initial-question-1"


def test_mock_interview_snapshot_keeps_frozen_jd_identity(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    with factory() as session:
        version = ApplicationJDVersion(
            application_id=app_id,
            version_number=4,
            jd_text="JD text",
            content_sha256="mock-jd-content-sha256",
            source_kind="ui",
            idempotency_key="mock-jd-version-0001",
            request_fingerprint_sha256="mock-jd-request-sha256",
        )
        session.add(version)
        session.commit()
        version_id = version.id

    result = _start_ready(
        MockInterviewRepository(factory),
        app_id,
        event_id,
        resume_id,
        "JD text",
        None,
        "mock-snapshot-key-01",
        "mock-question-key-01",
        jd_version_id=version_id,
    )

    snapshot = json.loads(result.attempt.input_snapshot_json)
    assert snapshot["jd"] == {
        "text": "JD text",
        "version_id": version_id,
        "version_number": 4,
        "content_sha256": "mock-jd-content-sha256",
    }
    assert snapshot["jd_version_id"] == version_id


def test_question_source_snapshot_keeps_only_minimal_question_evidence(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    result = repo.create_or_replay_start(
        app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    )
    with factory() as session:
        turn = session.scalar(select(MockInterviewTurn).where(MockInterviewTurn.attempt_id == result.attempt.id))
        assert turn is not None
        snapshot = json.loads(turn.question_source_snapshot_json)
        assert set(snapshot) == {"jd", "resume", "selected_preparation"}


def test_provider_snapshot_excludes_preparation_database_identity_and_full_proposal():
    attempt = MockInterviewAttempt(
        input_snapshot_json=json.dumps({
            "jd": {"text": "JD"},
            "resume": {"content_json": {"raw_text": "Resume"}},
            "selected_preparation": [{
                "proposal_id": 17,
                "source_fingerprint": "source-hash",
                "proposal_hash": "proposal-hash",
                "items": [{"id": "direction-1", "text": "Prepare", "evidence_refs": []}],
                "evidence": [],
            }],
            "proposal": {"all_sections": ["must not be sent"]},
        })
    )

    snapshot = provider_mock_interview_snapshot(attempt)

    assert snapshot["selected_preparation"] == [{
        "items": [{"text": "Prepare", "evidence_refs": []}],
        "evidence": [],
    }]
    assert "proposal" not in snapshot
    assert "proposal_id" not in json.dumps(snapshot)
    assert "source-hash" not in json.dumps(snapshot)


def test_preparation_selection_freezes_only_selected_items(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    proposal_body = {
        "preparation_directions": [{
            "id": "direction-1", "text": "Prepare Python examples", "evidence_refs": [
                {"source": "jd", "path": "/jd/text", "excerpt": "Python"}
            ],
        }],
        "story_prompts": [{"id": "story-1", "text": "Unused story", "evidence_refs": []}],
        "review_points": [],
        "interviewer_questions": [],
        "items_to_clarify": [],
    }
    with factory() as session:
        event = session.get(ApplicationEvent, event_id)
        resume = session.get(Resume, resume_id)
        assert event is not None and resume is not None
        proposal = InterviewPreparationProposal(
            application_id=app_id,
            application_event_id=event_id,
            resume_id=resume_id,
            idempotency_key="preparation-1",
            attempt_status="ready",
            proposal_status="normal",
            input_snapshot_json=json.dumps({
                "event": {
                    "id": event_id,
                    "event_type": event.event_type,
                    "subtype": event.subtype,
                    "round": event.round,
                    "scheduled_at": event.scheduled_at.isoformat(),
                    "duration_minutes": event.duration_minutes,
                    "status": event.status,
                },
                "resume": {"id": resume_id, "content_json": json.loads(resume.content_json)},
                "knowledge_evidence": [],
            }),
            source_fingerprint="source-1",
            proposal_json=json.dumps(proposal_body),
            proposal_hash="proposal-1",
        )
        session.add(proposal)
        session.commit()
        proposal_id = proposal.id

    repo = MockInterviewRepository(factory)
    result = repo.create_or_replay_start(
        app_id,
        event_id,
        resume_id,
        "JD Python",
        proposal_id,
        "attempt-preparation-1",
        "question-preparation-1",
        {"proposal_id": proposal_id, "item_ids": ["direction-1"]},
    )
    provider_snapshot = provider_mock_interview_snapshot(result.attempt)
    stored = json.loads(result.attempt.input_snapshot_json)

    assert [item["id"] for item in stored["selected_preparation"][0]["items"]] == ["direction-1"]
    assert provider_snapshot["selected_preparation"][0]["items"] == [{
        "text": "Prepare Python examples",
        "evidence_refs": [{"source": "jd", "path": "/jd/text", "excerpt": "Python"}],
    }]
    assert "story-1" not in json.dumps(provider_snapshot)
    assert str(proposal_id) not in json.dumps(provider_snapshot)


def test_same_initial_question_key_replays_first_turn(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    first = _start_ready(repo,
        app_id, event_id, resume_id, "JD text", None, "attempt-1", "initial-question-1"
    ).attempt

    replay_result = _start_ready(repo,
        app_id, event_id, resume_id, "JD text", None, "attempt-1", "initial-question-1"
    )
    replay, turn = replay_result.attempt, replay_result.turn

    assert replay.id == first.id
    assert turn.question_idempotency_key == "initial-question-1"


def test_different_initial_question_key_returns_turn_idempotency_conflict(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    repo.create_or_replay_start(
        app_id, event_id, resume_id, "JD text", None, "attempt-1", "initial-question-1"
    )

    with pytest.raises(MockInterviewTurnIdempotencyConflict):
        repo.create_or_replay_start(
            app_id, event_id, resume_id, "JD text", None, "attempt-1", "initial-question-2"
        )


def test_concurrent_first_start_creates_one_attempt_and_one_provider_owner(tmp_path):
    path = tmp_path / "data.db"
    factory = init_database(path)
    app_id, event_id, _, resume_id, _ = _seed(factory)
    barrier = threading.Barrier(2)
    results = []

    def start() -> None:
        repo = MockInterviewRepository(init_database(path))
        barrier.wait()
        results.append(
        _start_ready(repo,
                app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
            )
        )

    threads = [threading.Thread(target=start) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2
    with factory() as session:
        assert session.query(MockInterviewAttempt).count() == 1
        assert session.scalar(select(MockInterviewAttempt).where(MockInterviewAttempt.id == results[0].attempt.id)).id == results[1].attempt.id


def test_expired_question_lease_has_one_cas_takeover(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    attempt = _start_ready(repo,
        app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    ).attempt
    with factory() as session:
        stored = session.get(MockInterviewAttempt, attempt.id)
        assert stored is not None
        stored.provider_lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    first = repo.claim_question(attempt.id, 1, "question-1")
    second = repo.claim_question(attempt.id, 1, "question-1")

    assert first is not None
    assert first.replay_turn is not None
    assert second is not None
    assert second.replay_turn is not None


def test_question_claim_freezes_transcript_under_write_lock(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    attempt = _start_ready(
        repo, app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    ).attempt
    repo.submit_answer(attempt.id, 1, "first answer", "answer-1")

    claim = repo.claim_question(attempt.id, 2, "question-2")

    assert claim is not None
    assert list(claim.turns) == [{
        "turn_no": 1,
        "question": "Question grounded in the JD.",
        "answer": "first answer",
    }]


def test_stale_question_completion_returns_conflict_not_attempt(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    attempt = _start_ready(
        repo, app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    ).attempt
    repo.submit_answer(attempt.id, 1, "first answer", "answer-1")
    claim = repo.claim_question(attempt.id, 2, "question-2")
    assert claim is not None

    with factory() as session:
        stored = session.get(MockInterviewAttempt, attempt.id)
        assert stored is not None
        stored.generation_revision += 1
        session.commit()

    assert repo.complete_question(
        attempt.id,
        2,
        claim.revision,
        claim.provider_call_token,
        claim.transcript_fingerprint,
        "stale question",
    ) is None


def test_expired_second_question_lease_can_be_claimed_once(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    attempt = _start_ready(
        repo, app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    ).attempt
    repo.submit_answer(attempt.id, 1, "first answer", "answer-1")
    first = repo.claim_question(attempt.id, 2, "question-2")
    assert first is not None
    with factory() as session:
        stored = session.get(MockInterviewAttempt, attempt.id)
        assert stored is not None
        stored.provider_lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    owners = [repo.claim_question(attempt.id, 2, "question-2"), repo.claim_question(attempt.id, 2, "question-2")]
    assert sum(owner is not None for owner in owners) == 1


def test_expired_second_question_dual_connections_have_one_owner(tmp_path):
    path = tmp_path / "data.db"
    factory = init_database(path)
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    attempt = _start_ready(
        repo, app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    ).attempt
    repo.submit_answer(attempt.id, 1, "first answer", "answer-1")
    first = repo.claim_question(attempt.id, 2, "question-2")
    assert first is not None
    with factory() as session:
        stored = session.get(MockInterviewAttempt, attempt.id)
        assert stored is not None
        stored.provider_lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    barrier = threading.Barrier(2)
    owners = []

    def claim() -> None:
        barrier.wait()
        owners.append(MockInterviewRepository(init_database(path)).claim_question(attempt.id, 2, "question-2"))

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(owner is not None for owner in owners) == 1


def test_completed_question_replay_does_not_reclaim_provider_lease(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    attempt = _start_ready(
        repo, app_id, event_id, resume_id, "JD text", None, "attempt-replay", "question-1"
    ).attempt

    replay = repo.claim_question(attempt.id, 1, "question-1")

    assert replay is not None
    assert replay.replay_turn is not None
    assert replay.replay_turn.question_text == "Question grounded in the JD."


def test_question_retry_freezes_only_completed_previous_turns(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    attempt = _start_ready(
        repo, app_id, event_id, resume_id, "JD text", None, "attempt-freeze", "question-1"
    ).attempt
    repo.submit_answer(attempt.id, 1, "first answer", "answer-1")
    first_claim = repo.claim_question(attempt.id, 2, "question-2")
    assert first_claim is not None
    with factory() as session:
        stored = session.get(MockInterviewAttempt, attempt.id)
        assert stored is not None
        stored.provider_lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    retry = repo.claim_question(attempt.id, 2, "question-2")

    assert retry is not None
    assert list(retry.turns) == [{
        "turn_no": 1,
        "question": "Question grounded in the JD.",
        "answer": "first answer",
    }]


def test_next_question_claim_and_completion_persist_a_second_turn(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    attempt = _start_ready(repo,
        app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    ).attempt
    repo.submit_answer(attempt.id, 1, "My answer", "answer-1")

    owner = repo.claim_question(attempt.id, 2, "question-2")
    assert owner is not None
    revision, token, transcript_fingerprint = owner
    completed = repo.complete_question(
        attempt.id, 2, revision, token, transcript_fingerprint, "Tell me about the tradeoff."
    )

    assert completed is not None
    with factory() as session:
        turns = list(
            session.query(__import__("offerpilot.models", fromlist=["MockInterviewTurn"]).MockInterviewTurn)
            .filter_by(attempt_id=attempt.id)
            .order_by(__import__("offerpilot.models", fromlist=["MockInterviewTurn"]).MockInterviewTurn.turn_no)
        )
        assert [(turn.turn_no, turn.question_idempotency_key, turn.question_text) for turn in turns] == [
            (1, "question-1", turns[0].question_text),
            (2, "question-2", "Tell me about the tradeoff."),
        ]


def test_feedback_claim_uses_lease_and_cas_completion(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    attempt = _start_ready(repo,
        app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    ).attempt
    repo.submit_answer(attempt.id, 1, "My answer", "answer-1")

    owner = repo.claim_feedback(attempt.id, "feedback-1")
    assert owner is not None
    revision, token, transcript_fingerprint = owner
    assert repo.claim_feedback(attempt.id, "feedback-1") is None
    proposal, created = repo.complete_feedback(
        attempt.id,
        "feedback-1",
        revision,
        token,
        transcript_fingerprint,
        {"schema_version": "mock-interview-feedback-v1", "proposal_status": "safe_empty", "strengths": [], "practice_points": [], "follow_up_questions": [], "next_practice_steps": []},
        "safe_empty",
    )
    assert created is True
    replay, replay_created = repo.get_feedback(attempt.id, "feedback-1")
    assert replay is not None
    assert replay.id == proposal.id
    assert replay_created is False


def test_feedback_claim_returns_the_transcript_frozen_under_its_write_lock(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    attempt = _start_ready(
        repo, app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    ).attempt
    repo.submit_answer(attempt.id, 1, "first answer", "answer-1")

    claim = repo.claim_feedback(attempt.id, "feedback-1")

    assert claim is not None
    assert list(claim.turns) == [{"turn_no": 1, "question": "Question grounded in the JD.", "answer": "first answer"}]


def test_feedback_completion_rechecks_sources_after_provider_returns(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    attempt = _start_ready(
        repo, app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    ).attempt
    repo.submit_answer(attempt.id, 1, "first answer", "answer-1")
    claim = repo.claim_feedback(attempt.id, "feedback-1")
    assert claim is not None

    with factory() as session:
        event = session.get(ApplicationEvent, event_id)
        assert event is not None
        event.status = "cancelled"
        session.commit()

    with pytest.raises(MockInterviewSourceChanged):
        repo.complete_feedback(
            attempt.id,
            "feedback-1",
            claim.revision,
            claim.provider_call_token,
            claim.transcript_fingerprint,
            {"schema_version": "mock-interview-feedback-v1", "proposal_status": "safe_empty", "strengths": [], "practice_points": [], "follow_up_questions": [], "next_practice_steps": []},
            "safe_empty",
        )


def test_provider_and_contract_failure_recheck_sources_before_persisting_status(tmp_path):
    factory = init_database(tmp_path / "data.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    repo = MockInterviewRepository(factory)
    attempt = repo.create_or_replay_start(
        app_id, event_id, resume_id, "JD text", None, "attempt-1", "question-1"
    ).attempt
    with factory() as session:
        event = session.get(ApplicationEvent, event_id)
        assert event is not None
        event.status = "cancelled"
        session.commit()

    with pytest.raises(MockInterviewSourceChanged):
        repo.mark_provider_unknown(attempt.id, 1, attempt.provider_call_token, "question")
    with factory() as session:
        stored = session.get(MockInterviewAttempt, attempt.id)
        assert stored is not None
        assert stored.attempt_status == "source_conflict"


def test_feedback_contract_failure_does_not_create_proposal(tmp_path):
    from offerpilot.ai.mock_interview import MockInterviewUnverifiableError, generate_feedback
    from offerpilot.ai.types import Assistant

    class InvalidModel:
        supports_json_schema = False

        def complete(self, *_args, **_kwargs):
            return Assistant(content="{\"unexpected\":true}")

    with pytest.raises(MockInterviewUnverifiableError):
        generate_feedback(
            InvalidModel(),
            {"jd": {"text": "JD"}, "resume": {"content_json": {}}},
            [{"turn_no": 1, "question": "Q", "answer": "A"}],
        )
