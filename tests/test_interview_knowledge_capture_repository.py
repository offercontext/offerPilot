from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event, Lock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from offerpilot.db import init_database
from offerpilot.models import (
    Application,
    ApplicationEvent,
    InterviewKnowledgeCaptureAttempt,
    InterviewNote,
)
from offerpilot.repositories.interview_knowledge_capture import (
    CaptureAttemptConflict,
    InterviewKnowledgeCaptureRepository,
)


def _setup(tmp_path):
    factory = init_database(tmp_path / "data.db")
    with factory() as session:
        application = Application(company_name="Acme", position_name="Backend")
        session.add(application)
        session.flush()
        event = ApplicationEvent(
            application_id=application.id,
            event_type="interview",
            scheduled_at=datetime(2026, 7, 20, 10, tzinfo=timezone.utc),
            duration_minutes=45,
        )
        note = InterviewNote(
            application_id=application.id,
            application_event_id=event.id if event.id else None,
            company="Acme",
            position="Backend",
            questions="设计一个缓存",
            self_reflection="我解释了淘汰策略",
            difficulty_points="说明一致性",
            mood="平稳",
        )
        session.add(event)
        session.flush()
        note.application_event_id = event.id
        session.add(note)
        session.commit()
        return factory, note.id


def _selected() -> list[dict[str, object]]:
    return [
        {"fragment_id": "client-1", "path": "/questions", "start": 0, "end": 6, "text": "设计一个缓存"},
        {"fragment_id": "client-2", "path": "/self_reflection", "start": 0, "end": 8, "text": "我解释了淘汰策略"},
    ]


def test_same_key_can_switch_from_direct_to_ai_without_new_key(tmp_path) -> None:
    factory, note_id = _setup(tmp_path)
    repository = InterviewKnowledgeCaptureRepository(factory)
    direct = repository.prepare_preview(note_id, "attempt-1", "direct", _selected())
    ai = repository.claim_ai_preview(note_id, "attempt-1", direct.fragments)
    assert direct.attempt_key == ai.attempt_key == "attempt-1"
    assert ai.preview_status == "ai_generating"


def test_concurrent_ai_claim_allows_one_provider_call_across_two_sqlite_sessions(tmp_path) -> None:
    factory, note_id = _setup(tmp_path)
    engine_a = create_engine(factory.kw["bind"].url, connect_args={"check_same_thread": False})
    engine_b = create_engine(factory.kw["bind"].url, connect_args={"check_same_thread": False})
    factory_a = sessionmaker(bind=engine_a, expire_on_commit=False)
    factory_b = sessionmaker(bind=engine_b, expire_on_commit=False)
    repository_a = InterviewKnowledgeCaptureRepository(factory_a)
    repository_b = InterviewKnowledgeCaptureRepository(factory_b)
    selected = repository_a.prepare_preview(note_id, "attempt-1", "direct", _selected()).fragments
    started = Event()
    release = Event()
    calls = 0
    calls_lock = Lock()

    def invoke(repository):
        nonlocal calls
        claim = repository.claim_ai_preview(note_id, "attempt-1", selected)
        if not claim.should_call_provider:
            return claim
        with calls_lock:
            calls += 1
        started.set()
        release.wait(timeout=5)
        repository.complete_ai_preview(
            note_id,
            "attempt-1",
            claim.preview_revision,
            claim.provider_call_token,
            {"title": "", "blocks": []},
        )
        return claim

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(invoke, repository_a)
        assert started.wait(timeout=5)
        second_future = pool.submit(invoke, repository_b)
        second = second_future.result(timeout=5)
        release.set()
        first = first_future.result(timeout=5)

    assert calls == 1
    assert second.preview_status == "ai_generating"
    assert first.should_call_provider is True
    engine_a.dispose()
    engine_b.dispose()


def test_stale_provider_result_fails_revision_token_cas(tmp_path) -> None:
    factory, note_id = _setup(tmp_path)
    repository = InterviewKnowledgeCaptureRepository(factory)
    selected = repository.prepare_preview(note_id, "attempt-1", "direct", _selected()).fragments
    claim = repository.claim_ai_preview(note_id, "attempt-1", selected)
    repository.mark_provider_unknown(
        note_id, "attempt-1", claim.preview_revision, claim.provider_call_token
    )
    assert not repository.complete_ai_preview(
        note_id,
        "attempt-1",
        claim.preview_revision,
        claim.provider_call_token,
        {"title": "", "blocks": []},
    )


def test_same_key_with_different_input_is_rejected_and_delete_is_idempotent(tmp_path) -> None:
    factory, note_id = _setup(tmp_path)
    repository = InterviewKnowledgeCaptureRepository(factory)
    repository.prepare_preview(note_id, "attempt-1", "direct", _selected())
    changed = [
        {
            "fragment_id": "other",
            "path": "/difficulty_points",
            "start": 0,
            "end": 5,
            "text": "说明一致性",
        }
    ]
    try:
        repository.prepare_preview(note_id, "attempt-1", "direct", changed)
    except CaptureAttemptConflict:
        pass
    else:
        raise AssertionError("expected attempt conflict")
    assert repository.discard_unconfirmed_attempt(note_id, "attempt-1") is True
    assert repository.discard_unconfirmed_attempt(note_id, "attempt-1") is False
    with factory() as session:
        assert session.scalar(
            select(InterviewKnowledgeCaptureAttempt).where(
                InterviewKnowledgeCaptureAttempt.note_id == note_id
            )
        ) is None
