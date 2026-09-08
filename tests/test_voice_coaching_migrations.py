from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from offerpilot.db import init_database
from offerpilot.models import MockInterviewAttempt, MockInterviewTurn, VoiceCoachingSnapshot


def test_voice_coaching_schema_is_additive_and_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "voice-coaching.db"

    first = init_database(db_path)
    first.kw["bind"].dispose()
    second = init_database(db_path)
    with second() as session:
        tables = {
            row[0]
            for row in session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        migrations = set(
            session.execute(text("SELECT version FROM schema_migrations")).scalars()
        )
        migration_count = session.execute(
            text(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE version='0022_voice_coaching_snapshots'"
            )
        ).scalar_one()
        indexes = {
            row[1]
            for row in session.execute(text("PRAGMA index_list(voice_coaching_snapshots)"))
        }
        foreign_keys = {
            (row[3], row[2], row[6])
            for row in session.execute(text("PRAGMA foreign_key_list(voice_coaching_snapshots)"))
        }
    second.kw["bind"].dispose()

    assert "voice_coaching_snapshots" in tables
    assert {
        "0018_application_jd_versions",
        "0019_interview_story_library",
        "0020_application_outcome_feedback",
        "0021_adaptive_interview_practice",
        "0022_voice_coaching_snapshots",
    } <= migrations
    assert migration_count == 1
    assert {
        "idx_voice_coaching_snapshots_created",
        "idx_voice_coaching_snapshots_application_event",
        "idx_voice_coaching_snapshots_attempt",
    } <= indexes
    assert {
        ("attempt_id", "mock_interview_attempts", "CASCADE"),
        ("turn_id", "mock_interview_turns", "CASCADE"),
        ("origin_snapshot_id", "voice_coaching_snapshots", "SET NULL"),
    } <= foreign_keys


def test_voice_coaching_schema_rejects_duplicate_turn_and_global_key(tmp_path: Path) -> None:
    factory = init_database(tmp_path / "voice-coaching-unique.db")
    with factory() as session:
        attempt = MockInterviewAttempt(
            application_id=1,
            event_id=2,
            resume_id=3,
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
            question_text="请介绍一次排障经历",
            answer_text="我定位并修复了连接池耗尽。",
            answer_sha256="answer",
            turn_status="answered",
        )
        session.add(turn)
        session.flush()
        session.add(_snapshot(attempt.id, turn.id, "save-key"))
        session.commit()

        session.add(_snapshot(attempt.id, turn.id, "other-key"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        other_turn = MockInterviewTurn(
            attempt_id=attempt.id,
            turn_no=2,
            question_idempotency_key="question-key-2",
            turn_idempotency_key="turn-key-2",
            question_text="请介绍另一次经历",
            answer_text="我完成了复盘。",
            answer_sha256="answer-2",
            turn_status="answered",
        )
        session.add(other_turn)
        session.flush()
        session.add(_snapshot(attempt.id, other_turn.id, "save-key"))
        with pytest.raises(IntegrityError):
            session.commit()

    factory.kw["bind"].dispose()


def _snapshot(attempt_id: int, turn_id: int, key: str) -> VoiceCoachingSnapshot:
    return VoiceCoachingSnapshot(
        attempt_id=attempt_id,
        turn_id=turn_id,
        application_id=1,
        event_id=2,
        idempotency_key=key,
        request_fingerprint_sha256=f"fingerprint-{key}",
        question_text_snapshot="请介绍一次排障经历",
        confirmed_answer_text_snapshot="我定位并修复了连接池耗尽。",
        answer_sha256="answer",
        measurement_source="local_browser_measurement",
        total_duration_ms=10_000,
        voiced_duration_ms=7_000,
        pause_count=1,
        longest_pause_ms=1_000,
        speech_rate_cpm=120,
        filler_occurrences_json="[]",
        reflection_text="",
        focus_kind=None,
        origin_snapshot_id=None,
    )


def test_voice_coaching_model_declares_immutable_columns() -> None:
    columns = {column.name: column for column in inspect(VoiceCoachingSnapshot).columns}

    assert columns["question_text_snapshot"].nullable is False
    assert columns["confirmed_answer_text_snapshot"].nullable is False
    assert columns["measurement_source"].nullable is False
    assert "updated_at" not in columns
