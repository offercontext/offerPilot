from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.application_strategy.models import ApplicationStrategyChange, ApplicationStrategyReceipt
from offerpilot.models import (
    Application, ApplicationJDVersion, ApplicationOutcome, ApplicationSubmissionSnapshot, Resume,
)
from offerpilot.repositories.application_strategy_evidence import (
    StrategyEvidenceUnavailable,
    list_strategy_target_outcomes,
    resolve_strategy_origin,
    resolve_strategy_target,
)

_KEY = re.compile(r"[A-Za-z0-9_-]{16,128}")
_MAX_EVIDENCE_ID = 2**63 - 1


class StrategyConflict(ValueError):
    """A prior command, stage revision or exact outcome no longer matches."""


@dataclass(frozen=True)
class ProposeChange:
    proposal: str


@dataclass(frozen=True)
class ConfirmApplied:
    expected_revision: int
    target_application_id: int
    target_snapshot_id: int
    applied_detail: str


@dataclass(frozen=True)
class DecideChange:
    expected_revision: int
    target_outcome_id: int
    decision: Literal["keep", "revise", "drop"]


StrategyCommand = ProposeChange | ConfirmApplied | DecideChange


@dataclass(frozen=True)
class StrategyArchiveLabel:
    id: int
    application_id: int
    submitted_at: datetime
    company_name: str
    position_name: str
    resume_title: str
    jd_version_number: int


@dataclass(frozen=True)
class StrategyState:
    origin: ApplicationOutcome
    change: ApplicationStrategyChange | None
    target: StrategyArchiveLabel | None
    comparable_outcomes: tuple[ApplicationOutcome, ...]


@dataclass(frozen=True)
class StrategyWriteResult:
    state: StrategyState
    replayed: bool


class ApplicationStrategyRepository:
    """Three user-confirmed commands; keep domain evidence read-only."""

    def __init__(
        self, factory: sessionmaker[Session], *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._factory = factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def get(self, origin_application_id: int, origin_outcome_id: int) -> StrategyState:
        _validate_id(origin_application_id)
        _validate_id(origin_outcome_id)
        with self._factory() as session:
            # SQLite legacy transaction control does not start a snapshot on SELECT.
            session.execute(text("BEGIN"))
            now = self._clock()
            origin = resolve_strategy_origin(
                session, application_id=origin_application_id, outcome_id=origin_outcome_id, now=now
            )
            return self._state(session, origin, session.get(ApplicationStrategyChange, origin.id), now)

    def list_targets(
        self, origin_application_id: int, origin_outcome_id: int, target_application_id: int
    ) -> tuple[StrategyArchiveLabel, ...]:
        for value in (origin_application_id, origin_outcome_id, target_application_id):
            _validate_id(value)
        with self._factory() as session:
            session.execute(text("BEGIN"))
            now = self._clock()
            origin = resolve_strategy_origin(
                session, application_id=origin_application_id, outcome_id=origin_outcome_id, now=now
            )
            change = session.get(ApplicationStrategyChange, origin.id)
            if change is None or change.revision != 0:
                raise StrategyConflict("Propose a change before choosing its applied submission")
            if target_application_id == origin.application_id:
                return ()
            now_utc = now.astimezone(timezone.utc).replace(tzinfo=None)
            return tuple(
                _archive_label(row) for row in session.execute(
                    _archive_label_query()
                    .where(
                        Application.id == target_application_id,
                        Application.deleted_at.is_(None),
                        ApplicationSubmissionSnapshot.submitted_at > origin.occurred_at,
                        ApplicationSubmissionSnapshot.submitted_at <= now_utc,
                    )
                    .order_by(
                        ApplicationSubmissionSnapshot.submitted_at.desc(),
                        ApplicationSubmissionSnapshot.id.desc(),
                    )
                )
            )

    def execute(
        self,
        origin_application_id: int,
        origin_outcome_id: int,
        *,
        request_key: str,
        command: StrategyCommand,
    ) -> StrategyWriteResult:
        _validate_id(origin_application_id)
        _validate_id(origin_outcome_id)
        if not _KEY.fullmatch(request_key):
            raise ValueError("Invalid application strategy request key")
        _validate_command(command)
        kind = {
            ProposeChange: "propose.v1", ConfirmApplied: "confirm_applied.v1", DecideChange: "decide.v1",
        }[type(command)]
        fingerprint = _digest({"kind": kind, "input": asdict(command)})
        # Persist only the hash of the opaque key. No command arguments or response snapshot.
        key_digest = hashlib.sha256(request_key.encode("ascii")).hexdigest()

        with self._factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            now = self._clock()
            origin = resolve_strategy_origin(
                session, application_id=origin_application_id, outcome_id=origin_outcome_id, now=now
            )
            change = session.get(ApplicationStrategyChange, origin.id)
            receipt = session.get(ApplicationStrategyReceipt, (origin.id, key_digest))
            if receipt is not None:
                if receipt.fingerprint != fingerprint:
                    raise StrategyConflict("Request key was used with different input")
                return StrategyWriteResult(self._state(session, origin, change, now), True)

            if isinstance(command, ProposeChange):
                if change is not None:
                    raise StrategyConflict("A change already exists for this outcome")
                change = ApplicationStrategyChange(
                    origin_outcome_id=origin.id, proposal=command.proposal.strip(), revision=0
                )
                session.add(change)
                session.flush()
            elif change is None or change.revision != command.expected_revision:
                raise StrategyConflict("Application strategy revision changed")
            elif isinstance(command, ConfirmApplied):
                if change.revision != 0:
                    raise StrategyConflict("The applied submission is already confirmed")
                target = resolve_strategy_target(
                    session, origin=origin, application_id=command.target_application_id,
                    snapshot_id=command.target_snapshot_id, now=now,
                )
                change.target_snapshot_id = target.id
                change.applied_detail = command.applied_detail.strip()
                change.revision = 1
            elif isinstance(command, DecideChange):
                if change.revision != 1:
                    raise StrategyConflict("Confirm an applied submission before deciding")
                state = self._state(session, origin, change, now)
                if not any(item.id == command.target_outcome_id for item in state.comparable_outcomes):
                    raise StrategyConflict("Choose an exact comparable screening outcome")
                change.target_outcome_id = command.target_outcome_id
                change.decision = command.decision
                change.revision = 2

            session.add(ApplicationStrategyReceipt(
                origin_outcome_id=origin.id, request_key=key_digest, fingerprint=fingerprint
            ))
            state = self._state(session, origin, change, now)
            session.commit()
            return StrategyWriteResult(state, False)

    @staticmethod
    def _state(
        session: Session,
        origin: ApplicationOutcome,
        change: ApplicationStrategyChange | None,
        now: datetime,
    ) -> StrategyState:
        if change is None or change.target_snapshot_id is None:
            return StrategyState(origin, change, None, ())
        stored = session.get(ApplicationSubmissionSnapshot, change.target_snapshot_id)
        if stored is None:
            raise StrategyEvidenceUnavailable("Target submission snapshot is unavailable")
        target = resolve_strategy_target(
            session, origin=origin, application_id=stored.application_id, snapshot_id=stored.id, now=now
        )
        outcomes = tuple(list_strategy_target_outcomes(session, target=target, now=now))
        if change.target_outcome_id is not None:
            outcomes = tuple(item for item in outcomes if item.id == change.target_outcome_id)
            if not outcomes:
                raise StrategyEvidenceUnavailable("Decided screening outcome is unavailable")
        label = session.execute(_archive_label_query().where(ApplicationSubmissionSnapshot.id == target.id)).one()
        return StrategyState(origin, change, _archive_label(label), outcomes)


def _archive_label_query():
    # Only labels are read here; never load resume, JD, or material bodies to populate a selector.
    return (
        select(
            ApplicationSubmissionSnapshot.id,
            ApplicationSubmissionSnapshot.application_id,
            ApplicationSubmissionSnapshot.submitted_at,
            Application.company_name,
            Application.position_name,
            Resume.title,
            Resume.name,
            ApplicationJDVersion.version_number,
        )
        .join(Application, Application.id == ApplicationSubmissionSnapshot.application_id)
        .join(Resume, Resume.id == ApplicationSubmissionSnapshot.resume_id)
        .join(ApplicationJDVersion, ApplicationJDVersion.id == ApplicationSubmissionSnapshot.jd_version_id)
    )


def _archive_label(row) -> StrategyArchiveLabel:
    archive_id, app_id, submitted, company, position, title, name, jd_version = row
    return StrategyArchiveLabel(archive_id, app_id, submitted, company, position, title or name or "", jd_version)


def _digest(values: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_command(command: StrategyCommand) -> None:
    if isinstance(command, ProposeChange):
        _validate_detail(command.proposal)
        return
    if type(command.expected_revision) is not int or not 0 <= command.expected_revision <= 2:
        raise ValueError("Invalid application strategy revision")
    if isinstance(command, ConfirmApplied):
        _validate_detail(command.applied_detail)
        _validate_id(command.target_application_id)
        _validate_id(command.target_snapshot_id)
    elif isinstance(command, DecideChange):
        _validate_id(command.target_outcome_id)
        if command.decision not in {"keep", "revise", "drop"}:
            raise ValueError("Invalid application strategy decision")


def _validate_detail(value: str) -> None:
    if not value.strip() or len(value.encode("utf-8")) > 2000:
        raise ValueError("Application strategy detail must be 1–2000 UTF-8 bytes")


def _validate_id(value: int) -> None:
    if type(value) is not int or not 1 <= value <= _MAX_EVIDENCE_ID:
        raise ValueError("Invalid application strategy evidence ID")
