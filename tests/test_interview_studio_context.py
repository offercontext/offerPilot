import json

from offerpilot.db import init_database
from offerpilot.models import Resume
from offerpilot.repositories.interview_practice_cases import InterviewPracticeCaseRepository
from offerpilot.repositories.mock_interviews import MockInterviewRepository
from offerpilot.repositories.voice_coaching import VoiceCoachingRepository


def test_quick_practice_voice_snapshot_uses_case_context(tmp_path):
    factory = init_database(tmp_path / "data.db")
    with factory() as session:
        resume = Resume(title="筱哲简历", parsed_data="Python", content_json=json.dumps({"skills": ["Python"]}))
        session.add(resume)
        session.commit()
        resume_id = resume.id

    case, _ = InterviewPracticeCaseRepository(factory).create_or_replay(
        idempotency_key="voice-case-001",
        position_name="后端工程师",
        jd_text="JD",
        resume_id=resume_id,
    )
    mock = MockInterviewRepository(factory)
    started = mock.create_or_replay_quick_start(
        practice_case_id=case.id,
        attempt_idempotency_key="voice-attempt-001",
        initial_question_idempotency_key="voice-question-001",
    )
    assert started.question_claim is not None
    revision, token, transcript = started.question_claim
    mock.complete_question(started.attempt.id, 1, revision, token, transcript, "请介绍一次排障经历。")
    mock.submit_answer(started.attempt.id, 1, "我定位了慢查询并补充索引。", "voice-answer-001")

    snapshot, created = VoiceCoachingRepository(factory).create_or_replay_quick(
        practice_case_id=case.id,
        attempt_id=started.attempt.id,
        turn_no=1,
        idempotency_key="voice-snapshot-001",
        total_duration_ms=10_000,
        voiced_duration_ms=8_000,
        pause_count=1,
        longest_pause_ms=800,
        speech_rate_cpm=260,
        filler_occurrences=[],
        reflection_text="回答简洁。",
        focus_kind="pace_consistency",
        origin_snapshot_id=None,
    )

    assert created is True
    assert snapshot["context_kind"] == "quick_practice"
    assert snapshot["practice_case_id"] == case.id
    assert snapshot["application_id"] is None
    assert snapshot["event_id"] is None
