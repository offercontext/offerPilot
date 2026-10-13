import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from offerpilot.db import init_database
from offerpilot.interview_voice.admission import (
    AnswerAcceptance, CONSENT_REVISION, InterviewAnswerIn, VoiceAdmissionError,
)
from offerpilot.interview_voice.repository import InterviewVoiceRepository, VoiceSessionCreate
from offerpilot.models import InterviewVoiceSession, MockInterviewTurn, Resume
from offerpilot.repositories.mock_interviews import MockInterviewRepository
from test_mock_interview_repository import _seed, _start_ready


def _setup(tmp_path):
    factory = init_database(tmp_path / "voice.db")
    app_id, event_id, _, resume_id, _ = _seed(factory)
    interviews = MockInterviewRepository(factory)
    attempt = _start_ready(
        interviews, app_id, event_id, resume_id, "JD text", None, "a", "q",
    ).attempt
    request = VoiceSessionCreate(
        attempt_id=attempt.id, turn_no=1, context_kind="application_event",
        application_id=app_id, event_id=event_id, request_key="voice-1",
        consent=True, consent_revision=CONSENT_REVISION,
    )
    return factory, interviews, InterviewVoiceRepository(factory, "test-secret"), request


def _acceptance(grant):
    return AnswerAcceptance(
        input_source="voice", acceptance_method="session_auto",
        voice_session_id=grant["session_id"], voice_session_epoch=grant["epoch"],
        voice_consent_revision=grant["consent_revision"], voice_session_token=grant["token"],
    )


def test_auto_answer_requires_live_scoped_consent_and_replay_survives_close(tmp_path):
    factory, interviews, voice, request = _setup(tmp_path)
    grant = voice.create(request)
    assert voice.create(request) == grant
    with pytest.raises(VoiceAdmissionError, match="停止实时"):
        interviews.submit_answer(request.attempt_id, 1, "old tab", "manual-key")
    acceptance = _acceptance(grant)
    interviews.submit_answer(request.attempt_id, 1, "我的回答", "answer-key", acceptance=acceptance)
    voice.close(grant["session_id"], grant["token"])
    interviews.submit_answer(request.attempt_id, 1, "我的回答", "answer-key", acceptance=acceptance)
    with factory() as db:
        turn = db.scalar(select(MockInterviewTurn).where(MockInterviewTurn.attempt_id == request.attempt_id))
        assert turn.acceptance_method == "session_auto"
        assert turn.input_source == "voice"
        assert turn.voice_consent_revision == CONSENT_REVISION
        assert db.scalar(select(InterviewVoiceSession)).state == "revoked"
    with pytest.raises(VoiceAdmissionError, match="接受方式"):
        interviews.submit_answer(request.attempt_id, 1, "我的回答", "answer-key")


@pytest.mark.parametrize("field,value", [
    ("voice_session_token", "x" * 64), ("voice_session_epoch", 99),
])
def test_forged_grants_cannot_save_answers(tmp_path, field, value):
    _, interviews, voice, request = _setup(tmp_path)
    grant = voice.create(request)
    acceptance = _acceptance(grant).model_copy(update={field: value})
    with pytest.raises(VoiceAdmissionError):
        interviews.submit_answer(request.attempt_id, 1, "forged", "key", acceptance=acceptance)
    assert interviews.get_turn(request.attempt_id, 1).answer_text == ""


def test_takeover_revokes_old_writer_and_old_heartbeat(tmp_path):
    _, interviews, voice, request = _setup(tmp_path)
    first = voice.create(request)
    another = request.model_copy(update={"request_key": "second"})
    with pytest.raises(VoiceAdmissionError, match="其他页面"):
        voice.create(another)
    second = voice.create(another.model_copy(update={"takeover": True}))
    assert second["epoch"] == first["epoch"] + 1
    for action in (
        lambda: voice.heartbeat(first["session_id"], first["token"]),
        lambda: interviews.submit_answer(request.attempt_id, 1, "late", "key", acceptance=_acceptance(first)),
    ):
        with pytest.raises(VoiceAdmissionError, match="接管"):
            action()
    interviews.submit_answer(request.attempt_id, 1, "current", "key", acceptance=_acceptance(second))


def test_source_change_revokes_session_instead_of_renewing_lease(tmp_path):
    factory, _, voice, request = _setup(tmp_path)
    grant = voice.create(request)
    with factory() as db:
        resume = db.scalar(select(Resume).where(Resume.title == "Resume"))
        resume.content_json = '{"skills": ["changed"]}'
        db.commit()
    with pytest.raises(VoiceAdmissionError, match="来源已变化"):
        voice.heartbeat(grant["session_id"], grant["token"])
    with factory() as db:
        assert db.get(InterviewVoiceSession, grant["session_id"]).state == "revoked"


def test_concurrent_tabs_only_one_owner_and_one_answer(tmp_path):
    factory, interviews, voice, request = _setup(tmp_path)

    def start(key):
        try:
            return voice.create(request.model_copy(update={"request_key": key}))
        except VoiceAdmissionError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, ["tab-1", "tab-2"]))
    grants = [item for item in results if item]
    assert len(grants) == 1
    acceptance = _acceptance(grants[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: interviews.submit_answer(
            request.attempt_id, 1, "same", "same-key", acceptance=acceptance,
        ), range(2)))
    with factory() as db:
        assert len(db.scalars(select(MockInterviewTurn).where(MockInterviewTurn.answer_text == "same")).all()) == 1


def test_expired_lease_is_not_revived_and_manual_fallback_is_allowed(tmp_path):
    factory, interviews, voice, request = _setup(tmp_path)
    grant = voice.create(request)
    with factory() as db:
        db.get(InterviewVoiceSession, grant["session_id"]).lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    with pytest.raises(VoiceAdmissionError):
        voice.heartbeat(grant["session_id"], grant["token"])
    with pytest.raises(VoiceAdmissionError):
        interviews.submit_answer(request.attempt_id, 1, "late", "key", acceptance=_acceptance(grant))
    interviews.submit_answer(request.attempt_id, 1, "manual", "key", acceptance=AnswerAcceptance(input_source="voice"))
    assert interviews.get_turn(request.attempt_id, 1).acceptance_method == "manual"


@pytest.mark.parametrize("consent", [False, 1, "true"])
def test_consent_cannot_be_inferred_from_truthy_input(tmp_path, consent):
    _, _, _, request = _setup(tmp_path)
    with pytest.raises(ValidationError):
        VoiceSessionCreate.model_validate({**request.model_dump(), "consent": consent})


def test_invalid_context_turn_and_cross_attempt_do_not_grant_access(tmp_path):
    _, _, voice, request = _setup(tmp_path)
    with pytest.raises(VoiceAdmissionError):
        voice.create(request.model_copy(update={"event_id": 999}))
    with pytest.raises(VoiceAdmissionError):
        voice.create(request.model_copy(update={"turn_no": 2}))


@pytest.mark.parametrize("extra", [
    {"acceptance_method": "session_auto"}, {"voice_session_id": "invented"},
    {"answer_text": "  "}, {"turn_no": True}, {"answer_source": "voice_auto"},
])
def test_answer_schema_rejects_implicit_auto_acceptance(extra):
    with pytest.raises(ValidationError):
        InterviewAnswerIn.model_validate({"turn_no": 1, "answer_text": "answer", "turn_idempotency_key": "key", **extra})


def test_additive_migration_preserves_historical_answers_without_invented_provenance(tmp_path):
    factory, interviews, _, request = _setup(tmp_path)
    interviews.submit_answer(request.attempt_id, 1, "history", "old-key")
    factory.kw["bind"].dispose()
    path = tmp_path / "voice.db"
    columns = ("input_source", "acceptance_method", "voice_session_id", "voice_session_epoch", "voice_consent_revision")
    with sqlite3.connect(path) as db:
        for column in columns:
            db.execute(f"ALTER TABLE mock_interview_turns DROP COLUMN {column}")
        db.execute("DROP TABLE interview_voice_sessions")
        db.execute("DELETE FROM schema_migrations WHERE version = '0033_interview_voice_sessions'")
    for _ in range(2):
        migrated = init_database(path)
        migrated.kw["bind"].dispose()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT answer_text, input_source, acceptance_method FROM mock_interview_turns").fetchone() == ("history", None, None)
        assert db.execute("SELECT count(*) FROM schema_migrations WHERE version = '0033_interview_voice_sessions'").fetchone()[0] == 1
