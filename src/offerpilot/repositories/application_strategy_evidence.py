from __future__ import annotations

# A strategy compares two recorded submissions, not live application status.
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from offerpilot.models import Application, ApplicationOutcome, ApplicationSubmissionSnapshot

_SCREENING_RESULTS = ("advanced", "rejected")


class StrategyEvidenceUnavailable(ValueError):
    """The referenced evidence is missing, mismatched or outside the first strategy slice."""


def resolve_strategy_origin(
    session: Session,
    *,
    application_id: int,
    outcome_id: int,
    now: datetime,
) -> ApplicationOutcome:
    origin = session.scalar(
        select(ApplicationOutcome)
        .join(Application, Application.id == ApplicationOutcome.application_id)
        .join(
            ApplicationSubmissionSnapshot,
            ApplicationSubmissionSnapshot.id == ApplicationOutcome.submission_snapshot_id,
        )
        .where(
            Application.id == application_id,
            Application.deleted_at.is_(None),
            ApplicationSubmissionSnapshot.application_id == application_id,
            ApplicationOutcome.id == outcome_id,
            ApplicationOutcome.stage == "screening",
            ApplicationOutcome.result == "rejected",
            ApplicationOutcome.occurred_at >= ApplicationSubmissionSnapshot.submitted_at,
            ApplicationOutcome.occurred_at <= _utc_naive(now),
        )
    )
    if origin is None:
        raise StrategyEvidenceUnavailable("Origin screening outcome is unavailable")
    return origin


def resolve_strategy_target(
    session: Session,
    *,
    origin: ApplicationOutcome,
    application_id: int,
    snapshot_id: int,
    now: datetime,
) -> ApplicationSubmissionSnapshot:
    # Called in the same transaction as origin resolution, including when resuming a saved strategy.
    target = session.scalar(
        select(ApplicationSubmissionSnapshot)
        .join(Application, Application.id == ApplicationSubmissionSnapshot.application_id)
        .where(
            Application.id == application_id,
            Application.deleted_at.is_(None),
            Application.id != origin.application_id,
            ApplicationSubmissionSnapshot.id == snapshot_id,
            ApplicationSubmissionSnapshot.submitted_at > origin.occurred_at,
            ApplicationSubmissionSnapshot.submitted_at <= _utc_naive(now),
        )
    )
    if target is None:
        raise StrategyEvidenceUnavailable("Target submission snapshot is unavailable")
    return target


def list_strategy_target_outcomes(
    session: Session,
    *,
    target: ApplicationSubmissionSnapshot,
    now: datetime,
) -> list[ApplicationOutcome]:
    # Reuse the transaction-validated target. Return every eligible result; never guess
    # which of multiple records is the outcome the user wants to compare.
    return list(
        session.scalars(
            select(ApplicationOutcome)
            .where(
                ApplicationOutcome.application_id == target.application_id,
                ApplicationOutcome.submission_snapshot_id == target.id,
                ApplicationOutcome.stage == "screening",
                ApplicationOutcome.result.in_(_SCREENING_RESULTS),
                ApplicationOutcome.occurred_at >= target.submitted_at,
                ApplicationOutcome.occurred_at <= _utc_naive(now),
            )
            .order_by(ApplicationOutcome.occurred_at.desc(), ApplicationOutcome.id.desc())
        )
    )


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Strategy evidence requires an aware clock")
    return value.astimezone(timezone.utc).replace(tzinfo=None)
