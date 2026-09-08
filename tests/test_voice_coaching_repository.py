from __future__ import annotations

import math
from pathlib import Path

import pytest

from offerpilot.db import init_database
from offerpilot.models import (
    Application,
    ApplicationEvent,
    MockInterviewAttempt,
    MockInterviewTurn,
)
from offerpilot.repositories.voice_coaching import (
    VoiceCoachingConflict,
    VoiceCoachingNotFound,
    VoiceCoachingRepository,
    VoiceCoachingValidationError,
)


def _context(tmp_path: Path, *, answer: str = "我先定位指标，然后修复连接池。"):
    factory = init_database(tmp_path / "voice-coaching.db")
    with factory() as session:
        application = Application(company_name="云栖智能", position_name="后端工程师")
        session.add(application)
        session.flush()
        event = ApplicationEvent(
            application_id=application.id,
            event_type="interview",
            subtype="technical",
            round=1,
            status="done",
        )
        session.add(event)
        session.flush()
        attempt = MockInterviewAttempt(
            application_id=application.id,
            event_id=event.id,
            resume_id=9,
            idempotency_key="attempt-key",
            input_snapshot_json="{}",
            source_fingerprint="source",
            attempt_status="active",
            transcript_fingerprint="",
        )
        session.add(attempt)
        session.flush()
        turn = MockInterviewTurn(
            attempt_id=attempt.id,
            turn_no=1,
            question_idempotency_key="question-key",
            turn_idempotency_key="turn-key",
            question_text="请介绍一次线上故障排查。",
            answer_text=answer,
            answer_sha256="stored-answer-sha",
            turn_status="answered",
        )
        session.add(turn)
        session.commit()
        ids = application.id, event.id, attempt.id, turn.id
    return factory, ids


def _create(repository: VoiceCoachingRepository, ids: tuple[int, int, int, int], **overrides):
    application_id, event_id, attempt_id, _turn_id = ids
    payload = {
        "application_id": application_id,
        "event_id": event_id,
        "attempt_id": attempt_id,
        "turn_no": 1,
        "idempotency_key": "voice-save-key-0001",
        "total_duration_ms": 72_000,
        "voiced_duration_ms": 25_000,
        "pause_count": 1,
        "longest_pause_ms": 3_000,
        "speech_rate_cpm": 118,
        "filler_occurrences": [],
        "reflection_text": "结果部分还可以更简洁",
        "focus_kind": None,
        "origin_snapshot_id": None,
    }
    payload.update(overrides)
    return repository.create_or_replay(**payload)


def test_create_freezes_server_turn_and_replays_same_key(tmp_path: Path) -> None:
    factory, ids = _context(tmp_path)
    repository = VoiceCoachingRepository(factory)

    created, was_created = _create(repository, ids)
    replay, replay_created = _create(repository, ids)

    assert was_created is True
    assert replay_created is False
    assert replay["id"] == created["id"]
    assert created["question_text"] == "请介绍一次线上故障排查。"
    assert created["confirmed_answer_text"] == "我先定位指标，然后修复连接池。"
    assert created["measurement_source"] == "local_browser_measurement"
    assert created["application_id"] == ids[0]
    assert created["event_id"] == ids[1]
    factory.kw["bind"].dispose()


def test_create_rejects_changed_key_input_and_second_key_for_turn(tmp_path: Path) -> None:
    factory, ids = _context(tmp_path)
    repository = VoiceCoachingRepository(factory)
    _create(repository, ids)

    with pytest.raises(VoiceCoachingConflict, match="idempotency"):
        _create(repository, ids, longest_pause_ms=4_000)
    with pytest.raises(VoiceCoachingConflict, match="snapshot exists"):
        _create(repository, ids, idempotency_key="voice-save-key-0002")
    factory.kw["bind"].dispose()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("idempotency_key", "short"),
        ("total_duration_ms", 0),
        ("total_duration_ms", 299_001),
        ("total_duration_ms", math.inf),
        ("voiced_duration_ms", -1),
        ("voiced_duration_ms", 80_000),
        ("pause_count", 301),
        ("longest_pause_ms", 72_001),
        ("speech_rate_cpm", 0),
        ("speech_rate_cpm", 1_001),
        ("reflection_text", "复" * 1_001),
        ("focus_kind", "personality_score"),
    ],
)
def test_create_rejects_invalid_measurements(tmp_path: Path, field: str, value: object) -> None:
    factory, ids = _context(tmp_path)
    repository = VoiceCoachingRepository(factory)

    with pytest.raises(VoiceCoachingValidationError):
        _create(repository, ids, **{field: value})
    factory.kw["bind"].dispose()


def test_create_validates_filler_offsets_by_unicode_code_point(tmp_path: Path) -> None:
    factory, ids = _context(tmp_path, answer="然后我定位问题，然后完成修复。")
    repository = VoiceCoachingRepository(factory)
    valid = [{"text": "然后", "count": 2, "transcript_offsets": [0, 8]}]

    created, _ = _create(repository, ids, filler_occurrences=valid)
    assert created["filler_occurrences"] == valid

    factory_2, ids_2 = _context(tmp_path / "mismatch", answer="然后我定位问题，然后完成修复。")
    with pytest.raises(VoiceCoachingValidationError, match="offset"):
        _create(
            VoiceCoachingRepository(factory_2),
            ids_2,
            filler_occurrences=[{"text": "然后", "count": 2, "transcript_offsets": [0, 7]}],
        )
    factory.kw["bind"].dispose()
    factory_2.kw["bind"].dispose()


def test_create_rejects_unanswered_and_cross_resource_turns(tmp_path: Path) -> None:
    factory, ids = _context(tmp_path)
    repository = VoiceCoachingRepository(factory)
    with factory() as session:
        turn = session.get(MockInterviewTurn, ids[3])
        assert turn is not None
        turn.turn_status = "awaiting_answer"
        session.commit()

    with pytest.raises(VoiceCoachingValidationError, match="answered"):
        _create(repository, ids)
    with pytest.raises(VoiceCoachingNotFound):
        _create(repository, (ids[0] + 1, ids[1], ids[2], ids[3]))
    factory.kw["bind"].dispose()


def test_list_cursor_source_status_and_idempotent_delete(tmp_path: Path) -> None:
    factory, ids = _context(tmp_path)
    repository = VoiceCoachingRepository(factory)
    created, _ = _create(repository, ids)

    listed = repository.list_snapshots(limit=50, before_id=None)
    assert [item["id"] for item in listed] == [created["id"]]
    assert listed[0]["source_available"] is True
    assert listed[0]["company_name"] == "云栖智能"
    assert repository.list_snapshots(limit=50, before_id=created["id"]) == []

    repository.delete_snapshot(created["id"])
    repository.delete_snapshot(created["id"])
    assert repository.list_snapshots(limit=50, before_id=None) == []
    with factory() as session:
        assert session.get(MockInterviewTurn, ids[3]) is not None
    factory.kw["bind"].dispose()


def test_get_for_turn_uses_full_ownership_chain(tmp_path: Path) -> None:
    factory, ids = _context(tmp_path)
    repository = VoiceCoachingRepository(factory)
    created, _ = _create(repository, ids)

    assert repository.get_for_turn(
        application_id=ids[0], event_id=ids[1], attempt_id=ids[2], turn_no=1
    )["id"] == created["id"]
    with pytest.raises(VoiceCoachingNotFound):
        repository.get_for_turn(
            application_id=ids[0], event_id=ids[1] + 1, attempt_id=ids[2], turn_no=1
        )
    factory.kw["bind"].dispose()


def test_create_rejects_an_origin_whose_application_or_event_is_no_longer_visible(
    tmp_path: Path,
) -> None:
    factory, ids = _context(tmp_path)
    repository = VoiceCoachingRepository(factory)
    origin, _ = _create(repository, ids)
    with factory() as session:
        application = Application(company_name="星河科技", position_name="平台工程师")
        session.add(application)
        session.flush()
        event = ApplicationEvent(
            application_id=application.id,
            event_type="interview",
            subtype="technical",
            status="done",
        )
        session.add(event)
        session.flush()
        attempt = MockInterviewAttempt(
            application_id=application.id,
            event_id=event.id,
            resume_id=10,
            idempotency_key="target-attempt-key",
            input_snapshot_json="{}",
            source_fingerprint="target-source",
            attempt_status="active",
            transcript_fingerprint="",
        )
        session.add(attempt)
        session.flush()
        turn = MockInterviewTurn(
            attempt_id=attempt.id,
            turn_no=1,
            question_idempotency_key="target-question-key",
            turn_idempotency_key="target-turn-key",
            question_text="请介绍一次容量治理。",
            answer_text="我先确认容量基线，再完成分片扩容。",
            answer_sha256="target-answer-sha",
            turn_status="answered",
        )
        session.add(turn)
        session.commit()
        ids_2 = application.id, event.id, attempt.id, turn.id

    with factory() as session:
        application = session.get(Application, ids[0])
        assert application is not None
        application.deleted_at = application.created_at
        session.commit()

    with pytest.raises(VoiceCoachingValidationError, match="origin snapshot"):
        _create(repository, ids_2, origin_snapshot_id=origin["id"])

    with factory() as session:
        application = session.get(Application, ids[0])
        assert application is not None
        application.deleted_at = None
        event = session.get(ApplicationEvent, ids[1])
        assert event is not None
        session.delete(event)
        session.commit()

    with pytest.raises(VoiceCoachingValidationError, match="origin snapshot"):
        _create(repository, ids_2, origin_snapshot_id=origin["id"], idempotency_key="voice-save-key-0002")

    factory.kw["bind"].dispose()


def test_trends_use_stable_windows_and_long_pause_priority(tmp_path: Path) -> None:
    factory, ids = _context(tmp_path)
    repository = VoiceCoachingRepository(factory)
    created, _ = _create(repository, ids, longest_pause_ms=3_200)

    trend = repository.trends()

    assert trend["snapshot_count"] == 1
    assert trend["metrics"]["longest_pause_ms"]["current_median"] == 3_200
    assert trend["metrics"]["longest_pause_ms"]["source_snapshot_ids"] == [created["id"]]
    assert trend["recommendation"] is None
    factory.kw["bind"].dispose()


def test_trends_select_long_pause_then_filler_then_pace(tmp_path: Path) -> None:
    factory, ids = _context(tmp_path)
    repository = VoiceCoachingRepository(factory)
    first, _ = _create(repository, ids, longest_pause_ms=3_100)
    with factory() as session:
        first_turn = session.get(MockInterviewTurn, ids[3])
        assert first_turn is not None
        for turn_no, longest, rate, filler in (
            (2, 3_000, 100, []),
            (3, 1_000, 160, [{"text": "然后", "count": 2, "transcript_offsets": [0, 8]}]),
        ):
            turn = MockInterviewTurn(
                attempt_id=ids[2],
                turn_no=turn_no,
                question_idempotency_key=f"question-key-{turn_no}",
                turn_idempotency_key=f"turn-key-{turn_no}",
                question_text=f"问题 {turn_no}",
                answer_text="然后我定位问题，然后完成修复。",
                answer_sha256=f"answer-{turn_no}",
                turn_status="answered",
            )
            session.add(turn)
            session.flush()
            session.commit()
            _create(
                repository,
                ids,
                turn_no=turn_no,
                idempotency_key=f"voice-save-key-000{turn_no}",
                longest_pause_ms=longest,
                speech_rate_cpm=rate,
                filler_occurrences=filler,
            )

    trend = repository.trends()
    assert trend["recommendation"]["focus_kind"] == "long_pause_control"
    assert first["id"] in trend["recommendation"]["source_snapshot_ids"]
    factory.kw["bind"].dispose()
