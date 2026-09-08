import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from offerpilot.db import init_database
from offerpilot.models import Application, ApplicationEvent, InterviewPracticeCase, Resume
from offerpilot.repositories.interview_practice_cases import (
    InterviewPracticeCaseIdempotencyConflict,
    InterviewPracticeCaseRepository,
    InterviewPracticeCaseValidationError,
)
from offerpilot.repositories.mock_interviews import MockInterviewRepository


def _seed_resume(factory):
    with factory() as session:
        resume = Resume(
            title="筱哲简历",
            parsed_data="Python FastAPI",
            content_json=json.dumps({"summary": "后端工程师", "skills": ["Python", "SQL"]}),
        )
        application = Application(company_name="澄明科技", position_name="后端工程师", status="interview")
        session.add_all([resume, application])
        session.flush()
        event = ApplicationEvent(
            application_id=application.id,
            event_type="interview",
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
            status="todo",
        )
        session.add(event)
        session.commit()
        return resume.id, application.id, event.id


def test_case_freezes_user_confirmed_input_and_replays_by_key(tmp_path):
    factory = init_database(tmp_path / "data.db")
    resume_id, _, _ = _seed_resume(factory)
    repository = InterviewPracticeCaseRepository(factory)

    created, was_created = repository.create_or_replay(
        idempotency_key="quick-case-筱哲-001",
        position_name="后端工程师",
        jd_text="负责 Python 服务与数据平台建设。",
        resume_id=resume_id,
    )
    replay, replayed = repository.create_or_replay(
        idempotency_key="quick-case-筱哲-001",
        position_name="后端工程师",
        jd_text="负责 Python 服务与数据平台建设。",
        resume_id=resume_id,
    )

    assert was_created is True
    assert replayed is False
    assert replay.id == created.id
    assert replay.jd_text_snapshot == "负责 Python 服务与数据平台建设。"
    assert replay.resume_content_snapshot_json == json.dumps(
        {"summary": "后端工程师", "skills": ["Python", "SQL"]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_case_same_key_with_changed_input_is_a_conflict(tmp_path):
    factory = init_database(tmp_path / "data.db")
    resume_id, _, _ = _seed_resume(factory)
    repository = InterviewPracticeCaseRepository(factory)
    repository.create_or_replay(
        idempotency_key="quick-case-筱哲-002",
        position_name="后端工程师",
        jd_text="JD one",
        resume_id=resume_id,
    )

    with pytest.raises(InterviewPracticeCaseIdempotencyConflict):
        repository.create_or_replay(
            idempotency_key="quick-case-筱哲-002",
            position_name="后端工程师",
            jd_text="JD two",
            resume_id=resume_id,
        )


def test_case_rejects_blank_or_overlong_position_and_jd(tmp_path):
    factory = init_database(tmp_path / "data.db")
    resume_id, _, _ = _seed_resume(factory)
    repository = InterviewPracticeCaseRepository(factory)

    with pytest.raises(InterviewPracticeCaseValidationError):
        repository.create_or_replay(
            idempotency_key="quick-case-筱哲-003",
            position_name="  ",
            jd_text="JD",
            resume_id=resume_id,
        )
    with pytest.raises(InterviewPracticeCaseValidationError):
        repository.create_or_replay(
            idempotency_key="quick-case-筱哲-004",
            position_name="后端工程师",
            jd_text="x" * 100_001,
            resume_id=resume_id,
        )


def test_case_archive_does_not_delete_frozen_history(tmp_path):
    factory = init_database(tmp_path / "data.db")
    resume_id, _, _ = _seed_resume(factory)
    repository = InterviewPracticeCaseRepository(factory)
    case, _ = repository.create_or_replay(
        idempotency_key="quick-case-筱哲-005",
        position_name="后端工程师",
        jd_text="JD",
        resume_id=resume_id,
    )

    archived = repository.archive(case.id)
    loaded = repository.get(case.id)

    assert archived.status == "archived"
    assert loaded is not None
    assert loaded.jd_text_snapshot == "JD"


def test_attempt_context_is_explicit_on_fresh_schema(tmp_path):
    factory = init_database(tmp_path / "data.db")
    with factory() as session:
        columns = {row[1] for row in session.execute(text("PRAGMA table_info(mock_interview_attempts)"))}
        assert {"context_kind", "practice_case_id"}.issubset(columns)
        assert {row[1] for row in session.execute(text("PRAGMA table_info(voice_coaching_snapshots)"))} >= {
            "context_kind",
            "practice_case_id",
        }
        assert session.query(InterviewPracticeCase).count() == 0


def test_quick_practice_attempt_uses_case_context_without_application_or_event(tmp_path):
    factory = init_database(tmp_path / "data.db")
    resume_id, application_id, event_id = _seed_resume(factory)
    cases = InterviewPracticeCaseRepository(factory)
    case, _ = cases.create_or_replay(
        idempotency_key="quick-case-筱哲-006",
        position_name="后端工程师",
        jd_text="负责 Python 服务。",
        resume_id=resume_id,
    )
    repository = MockInterviewRepository(factory)

    result = repository.create_or_replay_quick_start(
        practice_case_id=case.id,
        attempt_idempotency_key="quick-attempt-001",
        initial_question_idempotency_key="quick-question-001",
    )

    assert result.attempt.context_kind == "quick_practice"
    assert result.attempt.practice_case_id == case.id
    assert result.attempt.application_id is None
    assert result.attempt.event_id is None
    assert result.attempt.resume_id == resume_id
    assert application_id != result.attempt.application_id
    assert event_id != result.attempt.event_id


def test_quick_practice_attempt_namespace_includes_case_context(tmp_path):
    factory = init_database(tmp_path / "data.db")
    resume_id, _, _ = _seed_resume(factory)
    cases = InterviewPracticeCaseRepository(factory)
    first_case, _ = cases.create_or_replay(
        idempotency_key="quick-case-筱哲-007",
        position_name="后端工程师",
        jd_text="JD one",
        resume_id=resume_id,
    )
    second_case, _ = cases.create_or_replay(
        idempotency_key="quick-case-筱哲-008",
        position_name="数据工程师",
        jd_text="JD two",
        resume_id=resume_id,
    )
    repository = MockInterviewRepository(factory)
    first = repository.create_or_replay_quick_start(
        practice_case_id=first_case.id,
        attempt_idempotency_key="quick-attempt-002",
        initial_question_idempotency_key="quick-question-002",
    )

    second = repository.create_or_replay_quick_start(
        practice_case_id=second_case.id,
        attempt_idempotency_key="quick-attempt-002",
        initial_question_idempotency_key="quick-question-003",
    )
    assert second.attempt.id != first.attempt.id
    assert second.attempt.practice_case_id == second_case.id
