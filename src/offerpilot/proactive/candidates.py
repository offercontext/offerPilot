"""Read-only reminder facts shared by today's cards and the opt-in job queue.

Eligibility and source revisions live here. Display windows are not permission to
send: the queue still owns scope, budgets, quiet hours, leases and publication.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
from hashlib import sha256
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from offerpilot.models import (
    Application, ApplicationEvent, InterviewNote, InterviewPreparationProposal, Offer,
)

DAY = 86400
STALE_STATUSES = ("pending", "applied", "written_test")
DISPLAY_ONLY_EVENT_TYPES = ("written_test", "offer_step", "custom")


def timestamp(value: datetime) -> float:
    return value.replace(tzinfo=timezone.utc).timestamp() if value.tzinfo is None else value.timestamp()


def revision(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256(raw.encode()).hexdigest()


def available(app: Application | None) -> bool:
    return app is not None and app.deleted_at is None and app.closed_at is None and app.status != "closed"


def subject(application_id: int, event_id: int | None, kind: str) -> dict[str, Any]:
    reason = "interview_preparation" if kind == "interview_draft" else kind
    subject_type, subject_id = ("application_event", event_id) if event_id is not None else ("application", application_id)
    return {"candidate_id": f"{subject_type}:{subject_id}:{reason}", "subject_type": subject_type,
            "subject_id": subject_id, "reason_code": reason}


def reminder_source(session: Session, application_id: int, event_id: int | None, kind: str,
                    *, draft_timezone: str = "Asia/Shanghai") -> tuple[str, dict[str, Any]] | None:
    app = session.get(Application, application_id)
    if not available(app) or app is None:
        return None
    if kind == "stale_application" and (event_id is not None or app.status not in STALE_STATUSES):
        return None
    if kind not in {"stale_application", "deadline", "review", "interview_preparation", "interview_draft"}:
        return None
    if kind != "stale_application" and event_id is None:
        return None
    value: dict[str, Any] = {"application_id": app.id, "company": app.company_name[:200],
        "position": app.position_name[:200], "status": app.status, "updated_at": app.updated_at.isoformat()}
    if event_id is not None:
        event = session.get(ApplicationEvent, event_id)
        if event is None or event.application_id != app.id or event.scheduled_at is None:
            return None
        if kind == "review":
            if event.event_type != "interview" or event.status != "done":
                return None
            if session.scalar(select(InterviewNote.id).where(InterviewNote.application_event_id == event.id).limit(1)):
                return None
        elif event.status != "todo" or event.event_type != ("deadline" if kind == "deadline" else "interview"):
            return None
        if kind in {"interview_preparation", "interview_draft"} and session.scalar(select(InterviewPreparationProposal.id).where(
            InterviewPreparationProposal.application_event_id == event.id,
            InterviewPreparationProposal.attempt_status == "ready",
            InterviewPreparationProposal.proposal_status == "normal").limit(1)):
            return None
        value.update(event_id=event.id, event_type=event.event_type, subtype=event.subtype,
                     scheduled_at=event.scheduled_at.isoformat(), status=event.status, round=event.round,
                     remind_at=event.remind_at.isoformat() if event.remind_at else None)
        if kind == "interview_draft":
            scheduled = datetime.fromtimestamp(timestamp(event.scheduled_at), timezone.utc)
            value.update(scheduled_at=scheduled.isoformat(), timezone=draft_timezone,
                scheduled_at_local=scheduled.astimezone(ZoneInfo(draft_timezone)).isoformat())
    return revision(value), value


@dataclass(frozen=True)
class ReminderCandidate:
    id: str
    subject_type: str
    subject_id: int
    reason_code: str
    source_revision: str
    application_id: int | None
    event_id: int | None
    offer_id: int | None
    company: str
    position: str
    due_at: float
    display_from: float
    display_until: float | None
    delivery_due_at: float | None
    delivery_expires_at: float | None
    updated_at: float | None = None
    deadline_label: str | None = None
    event_type: str | None = None

    def visible(self, now: float) -> bool:
        return self.display_from <= now and (self.display_until is None or now < self.display_until)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def source_candidate(session: Session, application_id: int, event_id: int | None, kind: str,
                     now: float) -> ReminderCandidate | None:
    source = reminder_source(session, application_id, event_id, kind)
    if source is None:
        return None
    digest, value = source
    identity = subject(application_id, event_id, kind)
    updated = timestamp(datetime.fromisoformat(value["updated_at"]))
    if kind == "stale_application":
        due = updated + 7 * DAY
        # The display is continuously stale from day seven; delivery retains a
        # single weekly catch-up bucket, never a replay of the backlog.
        delivery = due + max(0, int((now - due) // (7 * DAY))) * 7 * DAY
        display_from, display_until, expires = due, None, delivery + DAY
    else:
        due = timestamp(datetime.fromisoformat(value["scheduled_at"]))
        if kind == "review":
            display_from, display_until, delivery, expires = due, due + DAY, due, due + DAY
        else:
            display_from, display_until = due - 3 * DAY, due
            delivery = timestamp(datetime.fromisoformat(value["remind_at"])) if value["remind_at"] else due - DAY
            expires = due
    return ReminderCandidate(id=identity["candidate_id"], subject_type=identity["subject_type"],
        subject_id=identity["subject_id"], reason_code=identity["reason_code"], source_revision=digest,
        application_id=application_id, event_id=event_id, offer_id=None,
        company=value["company"], position=value["position"], due_at=due,
        display_from=display_from, display_until=display_until, delivery_due_at=delivery,
        delivery_expires_at=expires, updated_at=updated, event_type=value.get("event_type"))


def upcoming_event_candidate(session: Session, event: ApplicationEvent) -> ReminderCandidate | None:
    """Preserve ordinary calendar cards without extending queue source eligibility."""
    if event.event_type not in DISPLAY_ONLY_EVENT_TYPES or event.status != "todo" or event.scheduled_at is None:
        return None
    app = session.get(Application, event.application_id)
    if not available(app) or app is None:
        return None
    due, updated = timestamp(event.scheduled_at), timestamp(app.updated_at)
    digest = revision({"application_id": app.id, "application_status": app.status,
        "updated_at": updated, "company": app.company_name[:200], "position": app.position_name[:200],
        "event_id": event.id, "event_type": event.event_type, "status": event.status,
        "subtype": event.subtype, "round": event.round, "due_at": due,
        "remind_at": timestamp(event.remind_at) if event.remind_at else None})
    return ReminderCandidate(id=f"application_event:{event.id}:event_upcoming", subject_type="application_event",
        subject_id=event.id, reason_code="event_upcoming", source_revision=digest,
        application_id=app.id, event_id=event.id, offer_id=None,
        company=app.company_name[:200], position=app.position_name[:200], due_at=due,
        display_from=due - 3 * DAY, display_until=due,
        delivery_due_at=None, delivery_expires_at=None, updated_at=updated, event_type=event.event_type)


_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:?\d{2})?\Z")


def offer_candidate(session: Session, offer: Offer, zone: ZoneInfo) -> ReminderCandidate | None:
    if offer.status not in {"pending", "negotiating"} or not offer.deadline:
        return None
    if offer.application_id is not None and not available(session.get(Application, offer.application_id)):
        return None
    try:
        if _DATE_ONLY.fullmatch(offer.deadline):
            parsed = datetime.combine(datetime.fromisoformat(offer.deadline).date(), time.max, zone)
        elif _DATE_TIME.fullmatch(offer.deadline):
            parsed = datetime.fromisoformat(offer.deadline.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=zone)
        else:
            return None
    except ValueError:
        return None
    due = timestamp(parsed)
    digest = revision({"offer_id": offer.id, "application_id": offer.application_id,
        "company": offer.company_name, "position": offer.position_name, "status": offer.status,
        "updated_at": offer.updated_at.isoformat(), "deadline": offer.deadline, "due_at": due})
    return ReminderCandidate(id=f"offer:{offer.id}:offer_response_deadline", subject_type="offer",
        subject_id=offer.id, reason_code="offer_response_deadline", source_revision=digest,
        application_id=offer.application_id, event_id=None, offer_id=offer.id,
        company=offer.company_name, position=offer.position_name, due_at=due,
        display_from=due - 7 * DAY, display_until=due, delivery_due_at=None, delivery_expires_at=None,
        deadline_label=parsed.strftime("%Y-%m-%d"))


def list_display_candidates(session: Session, now: float, timezone_name: str) -> list[dict[str, Any]]:
    """No opt-in needed to read facts. Never creates jobs or spends a budget."""
    zone = ZoneInfo(timezone_name)
    candidates: list[ReminderCandidate | None] = []
    for event in session.scalars(select(ApplicationEvent).where(
        ApplicationEvent.event_type.in_(("interview", "deadline", *DISPLAY_ONLY_EVENT_TYPES)),
        ApplicationEvent.scheduled_at >= datetime.fromtimestamp(now - DAY, timezone.utc),
        ApplicationEvent.scheduled_at <= datetime.fromtimestamp(now + 3 * DAY, timezone.utc))
        .order_by(ApplicationEvent.id)):
        if event.event_type in DISPLAY_ONLY_EVENT_TYPES:
            candidates.append(upcoming_event_candidate(session, event))
            continue
        kind = "deadline" if event.event_type == "deadline" else "review" if event.status == "done" else "interview_preparation"
        candidates.append(source_candidate(session, event.application_id, event.id, kind, now))
    for app in session.scalars(select(Application).where(Application.status.in_(STALE_STATUSES),
        Application.updated_at <= datetime.fromtimestamp(now - 7 * DAY, timezone.utc))):
        candidates.append(source_candidate(session, app.id, None, "stale_application", now))
    for offer in session.scalars(select(Offer).where(Offer.status.in_(("pending", "negotiating")))):
        candidates.append(offer_candidate(session, offer, zone))
    return [item.to_dict() for item in candidates if item is not None and item.visible(now)]
