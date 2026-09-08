from __future__ import annotations

import json
import math
import re
import statistics
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import (
    Application,
    ApplicationEvent,
    InterviewPracticeCase,
    MockInterviewAttempt,
    MockInterviewTurn,
    VoiceCoachingSnapshot,
)
from offerpilot.repositories.json_contract import canonical_json, sha256_text


class VoiceCoachingNotFound(Exception):
    pass


class VoiceCoachingConflict(ValueError):
    pass


class VoiceCoachingValidationError(ValueError):
    pass


_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_FOCUS_KINDS = {"long_pause_control", "filler_reduction", "pace_consistency"}


class VoiceCoachingRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def create_or_replay(
        self,
        *,
        application_id: int,
        event_id: int,
        attempt_id: int,
        turn_no: int,
        idempotency_key: str,
        total_duration_ms: int,
        voiced_duration_ms: int,
        pause_count: int,
        longest_pause_ms: int,
        speech_rate_cpm: int | None,
        filler_occurrences: list[dict[str, Any]],
        reflection_text: str,
        focus_kind: str | None,
        origin_snapshot_id: int | None,
    ) -> tuple[dict[str, Any], bool]:
        if not isinstance(idempotency_key, str) or _IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None:
            raise VoiceCoachingValidationError("voice coaching idempotency key is invalid")
        validated = _validate_measurements(
            total_duration_ms=total_duration_ms,
            voiced_duration_ms=voiced_duration_ms,
            pause_count=pause_count,
            longest_pause_ms=longest_pause_ms,
            speech_rate_cpm=speech_rate_cpm,
            filler_occurrences=filler_occurrences,
            reflection_text=reflection_text,
            focus_kind=focus_kind,
        )

        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            attempt, turn = _owned_turn(
                session,
                application_id=application_id,
                event_id=event_id,
                attempt_id=attempt_id,
                turn_no=turn_no,
            )
            answer = turn.answer_text.strip()
            if turn.turn_status != "answered" or not answer:
                raise VoiceCoachingValidationError("voice coaching turn must be answered")
            fillers = _validate_fillers(validated["filler_occurrences"], answer)
            if origin_snapshot_id is not None:
                _bounded_int("origin_snapshot_id", origin_snapshot_id, minimum=1)
                origin = session.get(VoiceCoachingSnapshot, origin_snapshot_id)
                if origin is None or not _snapshot_source_available(session, origin):
                    raise VoiceCoachingValidationError("voice coaching origin snapshot is unavailable")

            answer_sha256 = sha256_text(answer)
            fingerprint_payload = {
                "application_id": application_id,
                "event_id": event_id,
                "attempt_id": attempt.id,
                "turn_id": turn.id,
                "turn_no": turn.turn_no,
                "answer_sha256": answer_sha256,
                **validated,
                "filler_occurrences": fillers,
                "origin_snapshot_id": origin_snapshot_id,
            }
            request_fingerprint = sha256_text(canonical_json(fingerprint_payload))

            replay = session.scalar(
                select(VoiceCoachingSnapshot).where(
                    VoiceCoachingSnapshot.idempotency_key == idempotency_key
                )
            )
            if replay is not None:
                if replay.request_fingerprint_sha256 != request_fingerprint:
                    raise VoiceCoachingConflict("voice coaching idempotency input changed")
                return _snapshot_json(session, replay), False

            existing_turn = session.scalar(
                select(VoiceCoachingSnapshot).where(VoiceCoachingSnapshot.turn_id == turn.id)
            )
            if existing_turn is not None:
                raise VoiceCoachingConflict("voice coaching snapshot exists for turn")

            snapshot = VoiceCoachingSnapshot(
                attempt_id=attempt.id,
                turn_id=turn.id,
                application_id=application_id,
                event_id=event_id,
                idempotency_key=idempotency_key,
                request_fingerprint_sha256=request_fingerprint,
                question_text_snapshot=turn.question_text,
                confirmed_answer_text_snapshot=answer,
                answer_sha256=answer_sha256,
                measurement_source="local_browser_measurement",
                total_duration_ms=validated["total_duration_ms"],
                voiced_duration_ms=validated["voiced_duration_ms"],
                pause_count=validated["pause_count"],
                longest_pause_ms=validated["longest_pause_ms"],
                speech_rate_cpm=validated["speech_rate_cpm"],
                filler_occurrences_json=canonical_json(fillers),
                reflection_text=validated["reflection_text"],
                focus_kind=validated["focus_kind"],
                origin_snapshot_id=origin_snapshot_id,
            )
            session.add(snapshot)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                replay = session.scalar(
                    select(VoiceCoachingSnapshot).where(
                        VoiceCoachingSnapshot.idempotency_key == idempotency_key
                    )
                )
                if replay is not None and replay.request_fingerprint_sha256 == request_fingerprint:
                    return _snapshot_json(session, replay), False
                if replay is not None:
                    raise VoiceCoachingConflict("voice coaching idempotency input changed") from exc
                raise VoiceCoachingConflict("voice coaching snapshot exists for turn") from exc
            session.refresh(snapshot)
            return _snapshot_json(session, snapshot), True

    def create_or_replay_quick(
        self,
        *,
        practice_case_id: int,
        attempt_id: int,
        turn_no: int,
        idempotency_key: str,
        total_duration_ms: int,
        voiced_duration_ms: int,
        pause_count: int,
        longest_pause_ms: int,
        speech_rate_cpm: int | None,
        filler_occurrences: list[dict[str, Any]],
        reflection_text: str,
        focus_kind: str | None,
        origin_snapshot_id: int | None,
    ) -> tuple[dict[str, Any], bool]:
        if not isinstance(idempotency_key, str) or _IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None:
            raise VoiceCoachingValidationError("voice coaching idempotency key is invalid")
        validated = _validate_measurements(
            total_duration_ms=total_duration_ms,
            voiced_duration_ms=voiced_duration_ms,
            pause_count=pause_count,
            longest_pause_ms=longest_pause_ms,
            speech_rate_cpm=speech_rate_cpm,
            filler_occurrences=filler_occurrences,
            reflection_text=reflection_text,
            focus_kind=focus_kind,
        )
        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            attempt, turn = _owned_quick_turn(
                session,
                practice_case_id=practice_case_id,
                attempt_id=attempt_id,
                turn_no=turn_no,
            )
            answer = turn.answer_text.strip()
            if turn.turn_status != "answered" or not answer:
                raise VoiceCoachingValidationError("voice coaching turn must be answered")
            fillers = _validate_fillers(validated["filler_occurrences"], answer)
            if origin_snapshot_id is not None:
                _bounded_int("origin_snapshot_id", origin_snapshot_id, minimum=1)
                origin = session.get(VoiceCoachingSnapshot, origin_snapshot_id)
                if origin is None or not _snapshot_source_available(session, origin):
                    raise VoiceCoachingValidationError("voice coaching origin snapshot is unavailable")
            answer_sha256 = sha256_text(answer)
            request_fingerprint = sha256_text(
                canonical_json(
                    {
                        "context_kind": "quick_practice",
                        "practice_case_id": practice_case_id,
                        "attempt_id": attempt.id,
                        "turn_id": turn.id,
                        "turn_no": turn.turn_no,
                        "answer_sha256": answer_sha256,
                        **validated,
                        "filler_occurrences": fillers,
                        "origin_snapshot_id": origin_snapshot_id,
                    }
                )
            )
            replay = session.scalar(
                select(VoiceCoachingSnapshot).where(VoiceCoachingSnapshot.idempotency_key == idempotency_key)
            )
            if replay is not None:
                if replay.request_fingerprint_sha256 != request_fingerprint:
                    raise VoiceCoachingConflict("voice coaching idempotency input changed")
                return _snapshot_json(session, replay), False
            existing_turn = session.scalar(
                select(VoiceCoachingSnapshot).where(VoiceCoachingSnapshot.turn_id == turn.id)
            )
            if existing_turn is not None:
                raise VoiceCoachingConflict("voice coaching snapshot exists for turn")
            snapshot = VoiceCoachingSnapshot(
                attempt_id=attempt.id,
                turn_id=turn.id,
                context_kind="quick_practice",
                application_id=None,
                event_id=None,
                practice_case_id=practice_case_id,
                idempotency_key=idempotency_key,
                request_fingerprint_sha256=request_fingerprint,
                question_text_snapshot=turn.question_text,
                confirmed_answer_text_snapshot=answer,
                answer_sha256=answer_sha256,
                measurement_source="local_browser_measurement",
                total_duration_ms=validated["total_duration_ms"],
                voiced_duration_ms=validated["voiced_duration_ms"],
                pause_count=validated["pause_count"],
                longest_pause_ms=validated["longest_pause_ms"],
                speech_rate_cpm=validated["speech_rate_cpm"],
                filler_occurrences_json=canonical_json(fillers),
                reflection_text=validated["reflection_text"],
                focus_kind=validated["focus_kind"],
                origin_snapshot_id=origin_snapshot_id,
            )
            session.add(snapshot)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                replay = session.scalar(
                    select(VoiceCoachingSnapshot).where(VoiceCoachingSnapshot.idempotency_key == idempotency_key)
                )
                if replay is not None and replay.request_fingerprint_sha256 == request_fingerprint:
                    return _snapshot_json(session, replay), False
                raise VoiceCoachingConflict("voice coaching snapshot exists for turn") from exc
            session.refresh(snapshot)
            return _snapshot_json(session, snapshot), True

    def get_for_turn(
        self, *, application_id: int, event_id: int, attempt_id: int, turn_no: int
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            _attempt, turn = _owned_turn(
                session,
                application_id=application_id,
                event_id=event_id,
                attempt_id=attempt_id,
                turn_no=turn_no,
            )
            snapshot = session.scalar(
                select(VoiceCoachingSnapshot).where(VoiceCoachingSnapshot.turn_id == turn.id)
            )
            return _snapshot_json(session, snapshot) if snapshot is not None else None

    def get_for_quick_turn(
        self, *, practice_case_id: int, attempt_id: int, turn_no: int
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            _attempt, turn = _owned_quick_turn(
                session,
                practice_case_id=practice_case_id,
                attempt_id=attempt_id,
                turn_no=turn_no,
            )
            snapshot = session.scalar(
                select(VoiceCoachingSnapshot).where(VoiceCoachingSnapshot.turn_id == turn.id)
            )
            return _snapshot_json(session, snapshot) if snapshot is not None else None

    def list_snapshots(
        self, *, limit: int, before_id: int | None
    ) -> list[dict[str, Any]]:
        bounded_limit = _bounded_int("limit", limit, minimum=1, maximum=100)
        if before_id is not None:
            _bounded_int("before_id", before_id, minimum=1)
        with self._session_factory() as session:
            statement = select(VoiceCoachingSnapshot)
            if before_id is not None:
                statement = statement.where(VoiceCoachingSnapshot.id < before_id)
            rows = list(
                session.scalars(
                    statement.order_by(VoiceCoachingSnapshot.id.desc()).limit(bounded_limit)
                )
            )
            return [_snapshot_json(session, row) for row in rows]

    def delete_snapshot(self, snapshot_id: int) -> None:
        _bounded_int("snapshot_id", snapshot_id, minimum=1)
        with self._session_factory() as session:
            session.execute(
                delete(VoiceCoachingSnapshot).where(VoiceCoachingSnapshot.id == snapshot_id)
            )
            session.commit()

    def trends(self) -> dict[str, Any]:
        rows = self.list_snapshots(limit=30, before_id=None)
        return build_voice_coaching_trends(rows)


def build_voice_coaching_trends(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = rows[:30]
    current = ordered[:5]
    previous = ordered[5:10]
    metrics = {
        "total_duration_ms": _metric_window(current, previous, "total_duration_ms"),
        "longest_pause_ms": _metric_window(current, previous, "longest_pause_ms"),
        "speech_rate_cpm": _metric_window(current, previous, "speech_rate_cpm"),
        "filler_per_minute": _filler_metric(current, previous),
    }
    recommendation = _recommendation(ordered[:3])
    return {
        "snapshot_count": len(ordered),
        "window_size": min(5, len(ordered)),
        "metrics": metrics,
        "recommendation": recommendation,
    }


def _validate_measurements(**values: Any) -> dict[str, Any]:
    total = _bounded_int("total_duration_ms", values["total_duration_ms"], minimum=1, maximum=299_000)
    voiced = _bounded_int("voiced_duration_ms", values["voiced_duration_ms"], minimum=0, maximum=total)
    pause_count = _bounded_int("pause_count", values["pause_count"], minimum=0, maximum=300)
    longest = _bounded_int("longest_pause_ms", values["longest_pause_ms"], minimum=0, maximum=total)
    rate = values["speech_rate_cpm"]
    if rate is not None:
        rate = _bounded_int("speech_rate_cpm", rate, minimum=1, maximum=1_000)
    fillers = values["filler_occurrences"]
    if not isinstance(fillers, list) or len(fillers) > 20:
        raise VoiceCoachingValidationError("voice coaching filler occurrences are invalid")
    reflection = values["reflection_text"]
    if not isinstance(reflection, str) or len(list(reflection)) > 1_000:
        raise VoiceCoachingValidationError("voice coaching reflection is invalid")
    reflection = reflection.strip()
    focus_kind = values["focus_kind"]
    if focus_kind is not None and focus_kind not in _FOCUS_KINDS:
        raise VoiceCoachingValidationError("voice coaching focus kind is invalid")
    return {
        "total_duration_ms": total,
        "voiced_duration_ms": voiced,
        "pause_count": pause_count,
        "longest_pause_ms": longest,
        "speech_rate_cpm": rate,
        "filler_occurrences": fillers,
        "reflection_text": reflection,
        "focus_kind": focus_kind,
    }


def _validate_fillers(items: list[dict[str, Any]], answer: str) -> list[dict[str, Any]]:
    answer_points = list(answer)
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"text", "count", "transcript_offsets"}:
            raise VoiceCoachingValidationError("voice coaching filler item is invalid")
        filler = item["text"]
        if not isinstance(filler, str) or not (1 <= len(list(filler)) <= 20):
            raise VoiceCoachingValidationError("voice coaching filler text is invalid")
        count = _bounded_int("filler count", item["count"], minimum=1, maximum=100)
        offsets = item["transcript_offsets"]
        if not isinstance(offsets, list) or len(offsets) != count or len(offsets) > 100:
            raise VoiceCoachingValidationError("voice coaching filler offsets are invalid")
        normalized = [_bounded_int("filler offset", offset, minimum=0) for offset in offsets]
        if normalized != sorted(set(normalized)):
            raise VoiceCoachingValidationError("voice coaching filler offsets are invalid")
        filler_points = list(filler)
        if any(answer_points[offset:offset + len(filler_points)] != filler_points for offset in normalized):
            raise VoiceCoachingValidationError("voice coaching filler offset does not match answer")
        result.append({"text": filler, "count": count, "transcript_offsets": normalized})
    return result


def _bounded_int(name: str, value: Any, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not math.isfinite(value):
        raise VoiceCoachingValidationError(f"voice coaching {name} is invalid")
    if value < minimum or (maximum is not None and value > maximum):
        raise VoiceCoachingValidationError(f"voice coaching {name} is invalid")
    return value


def _owned_turn(
    session: Session,
    *,
    application_id: int,
    event_id: int,
    attempt_id: int,
    turn_no: int,
) -> tuple[MockInterviewAttempt, MockInterviewTurn]:
    application = session.get(Application, application_id)
    event = session.get(ApplicationEvent, event_id)
    attempt = session.get(MockInterviewAttempt, attempt_id)
    if (
        application is None
        or application.deleted_at is not None
        or event is None
        or event.application_id != application_id
        or event.event_type != "interview"
        or attempt is None
        or attempt.application_id != application_id
        or attempt.event_id != event_id
    ):
        raise VoiceCoachingNotFound()
    turn = session.scalar(
        select(MockInterviewTurn).where(
            MockInterviewTurn.attempt_id == attempt_id,
            MockInterviewTurn.turn_no == turn_no,
        )
    )
    if turn is None:
        raise VoiceCoachingNotFound()
    return attempt, turn


def _owned_quick_turn(
    session: Session,
    *,
    practice_case_id: int,
    attempt_id: int,
    turn_no: int,
) -> tuple[MockInterviewAttempt, MockInterviewTurn]:
    case = session.get(InterviewPracticeCase, practice_case_id)
    attempt = session.get(MockInterviewAttempt, attempt_id)
    if (
        case is None
        or attempt is None
        or attempt.context_kind != "quick_practice"
        or attempt.practice_case_id != practice_case_id
    ):
        raise VoiceCoachingNotFound()
    turn = session.scalar(
        select(MockInterviewTurn).where(
            MockInterviewTurn.attempt_id == attempt_id,
            MockInterviewTurn.turn_no == turn_no,
        )
    )
    if turn is None:
        raise VoiceCoachingNotFound()
    return attempt, turn


def _snapshot_source_available(session: Session, row: VoiceCoachingSnapshot) -> bool:
    if row.context_kind == "quick_practice":
        case = session.get(InterviewPracticeCase, row.practice_case_id)
        attempt = session.get(MockInterviewAttempt, row.attempt_id)
        return bool(
            case is not None
            and attempt is not None
            and attempt.context_kind == "quick_practice"
            and attempt.practice_case_id == row.practice_case_id
        )
    application = session.get(Application, row.application_id)
    event = session.get(ApplicationEvent, row.event_id)
    return bool(
        application is not None
        and application.deleted_at is None
        and event is not None
        and event.application_id == row.application_id
        and event.event_type == "interview"
    )


def _snapshot_json(session: Session, row: VoiceCoachingSnapshot) -> dict[str, Any]:
    application = session.get(Application, row.application_id) if row.application_id is not None else None
    source_available = _snapshot_source_available(session, row)
    try:
        fillers = json.loads(row.filler_occurrences_json)
    except (TypeError, ValueError):
        fillers = []
    return {
        "id": row.id,
        "attempt_id": row.attempt_id,
        "turn_id": row.turn_id,
        "context_kind": row.context_kind,
        "application_id": row.application_id,
        "event_id": row.event_id,
        "practice_case_id": row.practice_case_id,
        "question_text": row.question_text_snapshot,
        "confirmed_answer_text": row.confirmed_answer_text_snapshot,
        "answer_sha256": row.answer_sha256,
        "measurement_source": row.measurement_source,
        "total_duration_ms": row.total_duration_ms,
        "voiced_duration_ms": row.voiced_duration_ms,
        "pause_count": row.pause_count,
        "longest_pause_ms": row.longest_pause_ms,
        "speech_rate_cpm": row.speech_rate_cpm,
        "filler_occurrences": fillers if isinstance(fillers, list) else [],
        "reflection_text": row.reflection_text,
        "focus_kind": row.focus_kind,
        "origin_snapshot_id": row.origin_snapshot_id,
        "created_at": row.created_at.isoformat() if row.created_at is not None else None,
        "source_available": source_available,
        "company_name": application.company_name if application is not None else "",
        "position_name": application.position_name if application is not None else "",
    }


def _median(values: list[int]) -> int | float | None:
    if not values:
        return None
    value = statistics.median(values)
    return int(value) if float(value).is_integer() else value


def _metric_window(
    current: list[dict[str, Any]], previous: list[dict[str, Any]], field: str
) -> dict[str, Any]:
    current_rows = [row for row in current if isinstance(row.get(field), int)]
    previous_rows = [row for row in previous if isinstance(row.get(field), int)]
    current_median = _median([int(row[field]) for row in current_rows])
    previous_median = _median([int(row[field]) for row in previous_rows])
    delta = (
        current_median - previous_median
        if current_median is not None and previous_median is not None
        else None
    )
    return {
        "current_median": current_median,
        "previous_median": previous_median,
        "delta": delta,
        "source_snapshot_ids": [row["id"] for row in current_rows],
        "previous_source_snapshot_ids": [row["id"] for row in previous_rows],
    }


def _filler_rate(rows: list[dict[str, Any]]) -> float | None:
    total_ms = sum(int(row["total_duration_ms"]) for row in rows)
    if total_ms <= 0:
        return None
    count = sum(
        int(item.get("count", 0))
        for row in rows
        for item in row.get("filler_occurrences", [])
        if isinstance(item, dict) and isinstance(item.get("count"), int)
    )
    return round(count / total_ms * 60_000, 2)


def _filler_metric(current: list[dict[str, Any]], previous: list[dict[str, Any]]) -> dict[str, Any]:
    current_rate = _filler_rate(current)
    previous_rate = _filler_rate(previous)
    return {
        "current_median": current_rate,
        "previous_median": previous_rate,
        "delta": (
            round(current_rate - previous_rate, 2)
            if current_rate is not None and previous_rate is not None
            else None
        ),
        "source_snapshot_ids": [row["id"] for row in current],
        "previous_source_snapshot_ids": [row["id"] for row in previous],
    }


def _recommendation(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(rows) < 2:
        return None
    long_pause = [row for row in rows if row["longest_pause_ms"] >= 2_500]
    if len(long_pause) >= 2:
        return _recommendation_json("long_pause_control", long_pause, "连续长停顿仍较明显")
    filler_rate = _filler_rate(rows)
    if filler_rate is not None and filler_rate >= 3:
        return _recommendation_json("filler_reduction", rows, "口头禅出现频率较高")
    rates = [row for row in rows if isinstance(row.get("speech_rate_cpm"), int)]
    if len(rates) == len(rows):
        values = [int(row["speech_rate_cpm"]) for row in rates]
        if min(values) > 0 and (max(values) - min(values)) / min(values) >= 0.4:
            return _recommendation_json("pace_consistency", rates, "最近回答语速波动较大")
    return None


def _recommendation_json(focus_kind: str, rows: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    source = next((row for row in rows if row.get("source_available")), rows[0])
    labels = {
        "long_pause_control": "减少长停顿",
        "filler_reduction": "减少口头禅",
        "pace_consistency": "稳定表达节奏",
    }
    return {
        "focus_kind": focus_kind,
        "title": labels[focus_kind],
        "reason": reason,
        "source_snapshot_ids": [row["id"] for row in rows],
        "source_snapshot_id": source["id"],
        "application_id": source["application_id"],
        "event_id": source["event_id"],
        "question_text": source["question_text"],
        "source_available": bool(source.get("source_available")),
    }
