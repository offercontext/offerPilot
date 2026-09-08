from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Generic, Literal, TypeVar

from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session, sessionmaker
from offerpilot.repositories.session_binding import finish_repository_write, repository_session

from offerpilot.models import (
    Application,
    ApplicationEvent,
    ApplicationJDVersion,
    ApplicationMaterialKit,
    ApplicationOutcome,
    ApplicationSubmissionSnapshot,
    Resume,
)


_KEY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
STAGES = frozenset({"applied", "screening", "written_test", "interview", "offer", "closed"})
RESULTS = frozenset({"advanced", "rejected", "withdrawn", "no_response", "offer_received", "other"})
FEEDBACK_TAGS = frozenset(
    {"technical_depth", "communication", "system_design", "domain_experience", "leadership", "collaboration", "other"}
)
SourceState = Literal["current", "changed", "missing"]
T = TypeVar("T")


class ApplicationOutcomeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ApplicationOutcomeValidationError(ApplicationOutcomeError):
    pass


class ApplicationOutcomeConflict(ApplicationOutcomeError):
    pass


class ApplicationOutcomeNotFound(ApplicationOutcomeError):
    pass


@dataclass(frozen=True)
class SubmissionSnapshotCreate:
    application_id: int
    resume_id: int
    jd_version_id: int
    material_kit_id: int | None
    submitted_at: datetime
    note: str
    source_kind: Literal["ui", "pilot"]
    idempotency_key: str


@dataclass(frozen=True)
class OutcomeCreate:
    application_id: int
    submission_snapshot_id: int
    application_event_id: int | None
    stage: str
    result: str
    feedback_text: str
    reflection_text: str
    next_action_text: str
    feedback_tags: tuple[str, ...]
    occurred_at: datetime
    source_kind: Literal["ui", "pilot"]
    idempotency_key: str


@dataclass(frozen=True)
class WriteResult(Generic[T]):
    value: T
    replayed: bool


@dataclass(frozen=True)
class SubmissionSnapshotView:
    value: ApplicationSubmissionSnapshot
    source_states: dict[str, SourceState]
    resume_title: str
    jd_version_number: int


class ApplicationOutcomesRepository:
    def __init__(self, session_factory: sessionmaker[Session], session: Session | None = None):
        self._session_factory = session_factory
        self._session = session

    def bind(self, session: Session) -> "ApplicationOutcomesRepository":
        return ApplicationOutcomesRepository(self._session_factory, session)

    def create_snapshot(
        self, data: SubmissionSnapshotCreate
    ) -> WriteResult[ApplicationSubmissionSnapshot]:
        _validate_snapshot_input(data)
        fingerprint = _hash_json(
            {
                "application_id": data.application_id,
                "resume_id": data.resume_id,
                "jd_version_id": data.jd_version_id,
                "material_kit_id": data.material_kit_id,
                "submitted_at": _utc_iso(data.submitted_at),
                "note": data.note,
                "source_kind": data.source_kind,
            }
        )
        with repository_session(self._session_factory, self._session) as session:
            if self._session is None:
                session.execute(text("BEGIN IMMEDIATE"))
            existing = session.scalar(
                select(ApplicationSubmissionSnapshot).where(
                    ApplicationSubmissionSnapshot.application_id == data.application_id,
                    ApplicationSubmissionSnapshot.idempotency_key == data.idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise ApplicationOutcomeConflict(
                        "application_archive_idempotency_conflict",
                        "snapshot idempotency input changed",
                    )
                return WriteResult(existing, True)

            application = session.get(Application, data.application_id)
            if application is None or application.deleted_at is not None:
                raise ApplicationOutcomeNotFound("application_not_found", "application not found")
            resume = session.get(Resume, data.resume_id)
            if resume is None or resume.deleted_at is not None:
                raise ApplicationOutcomeNotFound("resume_not_found", "resume not found")
            jd = session.get(ApplicationJDVersion, data.jd_version_id)
            if jd is None or jd.application_id != data.application_id:
                raise ApplicationOutcomeConflict(
                    "application_archive_source_conflict", "JD version does not belong to application"
                )
            kit = None
            if data.material_kit_id is not None:
                kit = session.get(ApplicationMaterialKit, data.material_kit_id)
                if kit is None or kit.application_id != data.application_id:
                    raise ApplicationOutcomeConflict(
                        "application_archive_source_conflict",
                        "material kit does not belong to application",
                    )

            resume_json = _canonical_json(_json_value(resume.content_json))
            material_json = _canonical_json(_json_value(kit.content_json)) if kit is not None else None
            snapshot = ApplicationSubmissionSnapshot(
                application_id=data.application_id,
                resume_id=data.resume_id,
                jd_version_id=data.jd_version_id,
                material_kit_id=data.material_kit_id,
                resume_snapshot_json=resume_json,
                resume_snapshot_hash=_hash_text(resume_json),
                jd_snapshot=jd.jd_text,
                jd_snapshot_hash=_hash_text(jd.jd_text),
                material_snapshot_json=material_json,
                material_snapshot_hash=_hash_text(material_json) if material_json is not None else None,
                note=data.note,
                source_kind=data.source_kind,
                idempotency_key=data.idempotency_key,
                request_fingerprint_sha256=fingerprint,
                submitted_at=_as_naive_utc(data.submitted_at),
            )
            session.add(snapshot)
            finish_repository_write(session, self._session)
            session.refresh(snapshot)
            return WriteResult(snapshot, False)

    def list_snapshots(self, application_id: int) -> list[SubmissionSnapshotView]:
        with self._session_factory() as session:
            snapshots = list(
                session.scalars(
                    select(ApplicationSubmissionSnapshot)
                    .where(ApplicationSubmissionSnapshot.application_id == application_id)
                    .order_by(desc(ApplicationSubmissionSnapshot.submitted_at), desc(ApplicationSubmissionSnapshot.id))
                )
            )
            return [self._snapshot_view(session, item) for item in snapshots]

    def get_snapshot(self, application_id: int, snapshot_id: int) -> SubmissionSnapshotView | None:
        with self._session_factory() as session:
            snapshot = session.get(ApplicationSubmissionSnapshot, snapshot_id)
            if snapshot is None or snapshot.application_id != application_id:
                return None
            return self._snapshot_view(session, snapshot)

    def create_outcome(self, data: OutcomeCreate) -> WriteResult[ApplicationOutcome]:
        normalized_tags = _validate_outcome_input(data)
        fingerprint = _hash_json(
            {
                "application_id": data.application_id,
                "submission_snapshot_id": data.submission_snapshot_id,
                "application_event_id": data.application_event_id,
                "stage": data.stage,
                "result": data.result,
                "feedback_text": data.feedback_text,
                "reflection_text": data.reflection_text,
                "next_action_text": data.next_action_text,
                "feedback_tags": normalized_tags,
                "occurred_at": _utc_iso(data.occurred_at),
                "source_kind": data.source_kind,
            }
        )
        with repository_session(self._session_factory, self._session) as session:
            if self._session is None:
                session.execute(text("BEGIN IMMEDIATE"))
            existing = session.scalar(
                select(ApplicationOutcome).where(
                    ApplicationOutcome.application_id == data.application_id,
                    ApplicationOutcome.idempotency_key == data.idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise ApplicationOutcomeConflict(
                        "application_outcome_idempotency_conflict",
                        "outcome idempotency input changed",
                    )
                return WriteResult(existing, True)

            application = session.get(Application, data.application_id)
            if application is None or application.deleted_at is not None:
                raise ApplicationOutcomeNotFound("application_not_found", "application not found")
            snapshot = session.get(ApplicationSubmissionSnapshot, data.submission_snapshot_id)
            if snapshot is None or snapshot.application_id != data.application_id:
                raise ApplicationOutcomeConflict(
                    "application_outcome_source_conflict", "snapshot does not belong to application"
                )
            if data.application_event_id is not None:
                event = session.get(ApplicationEvent, data.application_event_id)
                if event is None or event.application_id != data.application_id:
                    raise ApplicationOutcomeConflict(
                        "application_outcome_source_conflict", "event does not belong to application"
                    )

            outcome = ApplicationOutcome(
                application_id=data.application_id,
                submission_snapshot_id=data.submission_snapshot_id,
                application_event_id=data.application_event_id,
                stage=data.stage,
                result=data.result,
                feedback_text=data.feedback_text,
                reflection_text=data.reflection_text,
                next_action_text=data.next_action_text,
                feedback_tags_json=_canonical_json(normalized_tags),
                source_kind=data.source_kind,
                idempotency_key=data.idempotency_key,
                request_fingerprint_sha256=fingerprint,
                occurred_at=_as_naive_utc(data.occurred_at),
            )
            session.add(outcome)
            finish_repository_write(session, self._session)
            session.refresh(outcome)
            return WriteResult(outcome, False)

    def list_outcomes(self, application_id: int) -> list[ApplicationOutcome]:
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(ApplicationOutcome)
                    .where(ApplicationOutcome.application_id == application_id)
                    .order_by(desc(ApplicationOutcome.occurred_at), desc(ApplicationOutcome.id))
                )
            )

    def summary(self, application_id: int) -> dict[str, object]:
        outcomes = self.list_outcomes(application_id)
        stages = Counter(item.stage for item in outcomes)
        results = Counter(item.result for item in outcomes)
        tags: Counter[str] = Counter()
        for item in outcomes:
            tags.update(_json_string_list(item.feedback_tags_json))
        return {
            "total": len(outcomes),
            "stage_counts": dict(sorted(stages.items())),
            "result_counts": dict(sorted(results.items())),
            "feedback_tag_counts": dict(sorted(tags.items())),
            "next_actions_pending": sum(bool(item.next_action_text.strip()) for item in outcomes),
        }

    def _snapshot_view(
        self, session: Session, snapshot: ApplicationSubmissionSnapshot
    ) -> SubmissionSnapshotView:
        resume = session.get(Resume, snapshot.resume_id)
        jd = session.get(ApplicationJDVersion, snapshot.jd_version_id)
        current_jd_id = session.scalar(
            select(ApplicationJDVersion.id)
            .where(ApplicationJDVersion.application_id == snapshot.application_id)
            .order_by(desc(ApplicationJDVersion.version_number))
            .limit(1)
        )
        kit = session.get(ApplicationMaterialKit, snapshot.material_kit_id) if snapshot.material_kit_id else None

        resume_state: SourceState = "missing"
        if resume is not None and resume.deleted_at is None:
            current_resume_json = _canonical_json(_json_value(resume.content_json))
            resume_state = "current" if _hash_text(current_resume_json) == snapshot.resume_snapshot_hash else "changed"
        jd_state: SourceState = "missing" if jd is None else ("current" if current_jd_id == snapshot.jd_version_id else "changed")
        material_state: SourceState = "current"
        if snapshot.material_kit_id is not None:
            if kit is None:
                material_state = "missing"
            else:
                current_material = _canonical_json(_json_value(kit.content_json))
                material_state = "current" if _hash_text(current_material) == snapshot.material_snapshot_hash else "changed"
        return SubmissionSnapshotView(
            value=snapshot,
            source_states={"resume": resume_state, "jd": jd_state, "material": material_state},
            resume_title=(resume.title or resume.name) if resume is not None else "",
            jd_version_number=jd.version_number if jd is not None else 0,
        )


def _validate_snapshot_input(data: SubmissionSnapshotCreate) -> None:
    for name, value in (("application_id", data.application_id), ("resume_id", data.resume_id), ("jd_version_id", data.jd_version_id)):
        if type(value) is not int or value <= 0:
            raise ApplicationOutcomeValidationError("application_archive_invalid_request", f"{name} must be positive")
    if data.material_kit_id is not None and (type(data.material_kit_id) is not int or data.material_kit_id <= 0):
        raise ApplicationOutcomeValidationError("application_archive_invalid_request", "material_kit_id must be positive")
    _validate_key(data.idempotency_key)
    _validate_time(data.submitted_at)
    _validate_text(data.note, "note", 4_000)
    if data.source_kind not in {"ui", "pilot"}:
        raise ApplicationOutcomeValidationError("application_archive_invalid_request", "source_kind is invalid")


def _validate_outcome_input(data: OutcomeCreate) -> list[str]:
    if data.stage not in STAGES:
        raise ApplicationOutcomeValidationError("application_outcome_invalid_request", "stage is invalid")
    if data.result not in RESULTS:
        raise ApplicationOutcomeValidationError("application_outcome_invalid_request", "result is invalid")
    for name, value in (("application_id", data.application_id), ("submission_snapshot_id", data.submission_snapshot_id)):
        if type(value) is not int or value <= 0:
            raise ApplicationOutcomeValidationError("application_outcome_invalid_request", f"{name} must be positive")
    if data.application_event_id is not None and (type(data.application_event_id) is not int or data.application_event_id <= 0):
        raise ApplicationOutcomeValidationError("application_outcome_invalid_request", "application_event_id must be positive")
    _validate_key(data.idempotency_key)
    _validate_time(data.occurred_at)
    _validate_text(data.feedback_text, "feedback_text", 20_000)
    _validate_text(data.reflection_text, "reflection_text", 20_000)
    _validate_text(data.next_action_text, "next_action_text", 10_000)
    if data.source_kind not in {"ui", "pilot"}:
        raise ApplicationOutcomeValidationError("application_outcome_invalid_request", "source_kind is invalid")
    normalized = sorted(set(data.feedback_tags))
    if len(normalized) > 8 or any(tag not in FEEDBACK_TAGS for tag in normalized):
        raise ApplicationOutcomeValidationError("application_outcome_invalid_request", "feedback_tags are invalid")
    return normalized


def _validate_key(value: str) -> None:
    if not isinstance(value, str) or _KEY.fullmatch(value) is None:
        raise ApplicationOutcomeValidationError("application_outcome_invalid_request", "idempotency_key is invalid")


def _validate_time(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ApplicationOutcomeValidationError("application_outcome_invalid_request", "datetime must include timezone")


def _validate_text(value: str, field: str, limit: int) -> None:
    if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
        raise ApplicationOutcomeValidationError("application_outcome_invalid_request", f"{field} is invalid")


def _as_naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_value(value: str) -> object:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ApplicationOutcomeValidationError(
            "application_archive_invalid_request", "source JSON is invalid"
        ) from exc


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash_json(value: object) -> str:
    return _hash_text(_canonical_json(value))


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_string_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []
