from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import Application, ApplicationEvent, Conversation, InterviewNote, InterviewPreparationProposal
from .contracts import ProactivePolicy, ProactivePolicyUpdate
from .models import ProactiveJob, ProactiveSettings

ACTIVE = ("pending", "leased", "running")
DAY = 86400
MAX_QUEUE = 100
MAX_RETAINED_JOBS = 2000
REASONS = {"deadline": "投递截止时间临近", "interview_preparation": "面试临近，可提前准备",
           "stale_application": "投递已有七天未更新", "review": "面试结束，可记录复盘",
           "interview_draft": "已开启面试准备草稿"}


class ProactiveConflict(ValueError):
    pass


def timestamp(value: datetime) -> float:
    return value.replace(tzinfo=timezone.utc).timestamp() if value.tzinfo is None else value.timestamp()


def quiet(policy: ProactivePolicy, now: float) -> bool:
    hour = datetime.fromtimestamp(now, ZoneInfo(policy.timezone)).hour
    start, end = policy.quiet_start_hour, policy.quiet_end_hour
    return start <= hour < end if start < end else (hour >= start or hour < end) if start > end else False


def _policy(session: Session) -> tuple[int, ProactivePolicy]:
    row = session.get(ProactiveSettings, 1)
    return (row.revision, ProactivePolicy.model_validate_json(row.settings_json)) if row else (0, ProactivePolicy())


def _allowed(policy: ProactivePolicy, kind: str, application_id: int) -> bool:
    return policy.enabled and application_id in policy.application_ids and (
        policy.drafts_enabled if kind == "interview_draft" else policy.reminders_enabled)


def _source(session: Session, application_id: int, event_id: int | None, kind: str) -> tuple[str, dict[str, Any]] | None:
    app = session.get(Application, application_id)
    if app is None or app.deleted_at is not None or app.closed_at is not None or app.status == "closed":
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
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256(raw.encode()).hexdigest(), value


def _view(row: ProactiveJob) -> dict[str, Any]:
    return {key: getattr(row, key) for key in ("id", "kind", "application_id", "event_id", "state", "reason",
        "due_at", "created_at", "published_at", "result_text", "turn_id", "execution_generation", "error_code")}


class ProactiveRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def settings(self) -> dict[str, Any]:
        with self.sessions() as session:
            revision, policy = _policy(session)
            return {"revision": revision, "settings": policy.model_dump()}

    def update_settings(self, command: ProactivePolicyUpdate) -> dict[str, Any]:
        if not command.confirmed:
            raise ProactiveConflict("explicit_confirmation_required")
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            revision, _ = _policy(session)
            if revision != command.expected_revision:
                raise ProactiveConflict("settings_revision_conflict")
            for app_id in command.settings.application_ids:
                app = session.get(Application, app_id)
                if app is None or app.deleted_at is not None:
                    raise ProactiveConflict("application_unavailable")
            row = session.get(ProactiveSettings, 1)
            if row is None:
                row = ProactiveSettings(id=1)
                session.add(row)
            row.revision, row.settings_json = revision + 1, command.settings.model_dump_json()
            for job in session.scalars(select(ProactiveJob).where(ProactiveJob.state.in_(ACTIVE))).all():
                if not _allowed(command.settings, job.kind, job.application_id):
                    job.state, job.error_code = "cancelled", "scope_disabled"
                    job.result_text = ""
            session.commit()
            return {"revision": revision + 1, "settings": command.settings.model_dump()}

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            # A removed source must also disappear from stored draft display.
            rows = session.scalars(select(ProactiveJob).order_by(ProactiveJob.created_at.desc()).limit(100)).all()
            values = []
            for row in rows:
                value = _view(row)
                source = _source(session, row.application_id, row.event_id, row.kind)
                if source is None or source[0] != row.source_digest:
                    value.update(result_text="", state="cancelled", error_code="source_changed")
                values.append(value)
            return values

    def reconcile_source_changes(self) -> int:
        """Persist cancellation before Runtime is asked to stop stale work."""
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            changed = 0
            for row in session.scalars(select(ProactiveJob).where(ProactiveJob.state.in_(ACTIVE))).all():
                source = _source(session, row.application_id, row.event_id, row.kind)
                if source is not None and source[0] == row.source_digest:
                    continue
                row.state, row.result_text, row.error_code = "cancelled", "", "source_changed"
                changed += 1
            session.commit()
            return changed

    def cancel(self, job_id: str) -> None:
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(ProactiveJob, job_id)
            if row is None:
                raise ProactiveConflict("job_not_found")
            if row.state == "cancelled":
                return
            if row.state not in ACTIVE:
                raise ProactiveConflict("job_already_terminal")
            row.state, row.result_text, row.error_code = "cancelled", "", "user_cancelled"
            session.commit()

    def discover(self, now: float) -> int:
        """Bounded startup catch-up: at most 100 queued jobs and 100 sources."""
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            _, policy = _policy(session)
            if not policy.enabled or not policy.application_ids:
                return 0
            session.execute(delete(ProactiveJob).where(ProactiveJob.state.in_(("succeeded", "failed", "cancelled")),
                ProactiveJob.created_at < now - 7 * DAY))
            retained = session.scalar(select(func.count()).select_from(ProactiveJob)) or 0
            count = session.scalar(select(func.count()).select_from(ProactiveJob).where(ProactiveJob.state.in_(ACTIVE))) or 0
            added = 0
            candidates: list[tuple[int, int | None, str, float, float]] = []
            window_start = datetime.fromtimestamp(now - DAY, timezone.utc)
            window_end = datetime.fromtimestamp(now + DAY, timezone.utc)
            events = session.scalars(select(ApplicationEvent).where(ApplicationEvent.application_id.in_(policy.application_ids),
                ApplicationEvent.event_type.in_(("interview", "deadline")),
                ApplicationEvent.scheduled_at.is_not(None),
                or_(
                    and_(ApplicationEvent.scheduled_at >= window_start, ApplicationEvent.scheduled_at <= window_end),
                    and_(ApplicationEvent.remind_at >= window_start, ApplicationEvent.remind_at <= window_end),
                ))
                .order_by(func.coalesce(ApplicationEvent.remind_at, ApplicationEvent.scheduled_at), ApplicationEvent.id)
                .limit(100)).all()
            for event in events:
                when = timestamp(event.scheduled_at)  # type: ignore[arg-type]
                if event.event_type == "deadline":
                    candidates.append((event.application_id, event.id, "deadline", timestamp(event.remind_at) if event.remind_at else when - DAY, when))
                elif event.status == "done":
                    candidates.append((event.application_id, event.id, "review", when, when + DAY))
                else:
                    due = timestamp(event.remind_at) if event.remind_at else when - DAY
                    candidates.extend((event.application_id, event.id, kind, due, when) for kind in ("interview_preparation", "interview_draft"))
            for app in session.scalars(select(Application).where(Application.id.in_(policy.application_ids)).limit(100)):
                due = timestamp(app.updated_at) + 7 * DAY
                # One rolling weekly bucket; no replay of a months-long backlog.
                if due <= now:
                    due += int((now - due) // (7 * DAY)) * 7 * DAY
                candidates.append((app.id, None, "stale_application", due, due + DAY))
            for app_id, event_id, kind, due, expires in candidates:
                if count + added >= MAX_QUEUE or retained + added >= MAX_RETAINED_JOBS:
                    break
                if not _allowed(policy, kind, app_id) or not due <= now < expires or now - due > DAY:
                    continue
                source = _source(session, app_id, event_id, kind)
                if source is None:
                    continue
                identity = sha256(f"{app_id}:{event_id}:{kind}:{source[0]}:{int(due)}".encode()).hexdigest()
                if session.scalar(select(ProactiveJob.id).where(ProactiveJob.idempotency_key == identity)):
                    continue
                # Rate limit by subject independent of source revision.
                if session.scalar(select(ProactiveJob.id).where(ProactiveJob.application_id == app_id,
                    ProactiveJob.event_id == event_id, ProactiveJob.kind == kind,
                    ProactiveJob.created_at > now - DAY).limit(1)):
                    continue
                session.add(ProactiveJob(id=str(uuid4()), idempotency_key=identity, kind=kind,
                    application_id=app_id, event_id=event_id, source_digest=source[0],
                    reason=REASONS[kind], due_at=due, expires_at=expires, created_at=now))
                session.flush()
                added += 1
            session.commit()
            return added

    def claim(self, owner: str, now: float) -> str | None:
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            _, policy = _policy(session)
            for row in session.scalars(select(ProactiveJob).where(ProactiveJob.state.in_(("leased", "running")),
                ProactiveJob.lease_until <= now).limit(MAX_QUEUE)):
                if row.model_started_at is not None:
                    row.state, row.error_code = "result_unknown", "runtime_result_requires_reconciliation"
                elif row.attempt >= 3:
                    row.state, row.error_code = "failed", "attempt_limit"
                else:
                    row.state, row.next_attempt_at = "pending", now + 30
            rows = session.scalars(select(ProactiveJob).where(ProactiveJob.state == "pending", ProactiveJob.due_at <= now,
                ProactiveJob.next_attempt_at <= now).order_by(ProactiveJob.due_at).limit(MAX_QUEUE)).all()
            selected = None
            for row in rows:
                source = _source(session, row.application_id, row.event_id, row.kind)
                if not _allowed(policy, row.kind, row.application_id) or now >= row.expires_at or source is None or source[0] != row.source_digest:
                    row.state, row.error_code = "cancelled", "source_or_scope_changed"
                    continue
                if quiet(policy, now):
                    continue
                if selected is None:
                    row.state, row.owner, row.lease_until = "leased", owner, now + 120
                    row.attempt += 1
                    row.execution_generation += 1
                    row.turn_id = str(uuid4())
                    selected = row.id
            session.commit()
            return selected

    def begin(self, job_id: str, owner: str, now: float) -> dict[str, Any] | None:
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(ProactiveJob, job_id)
            _, policy = _policy(session)
            if row is None or row.state != "leased" or row.owner != owner or row.lease_until <= now:
                return None
            source = _source(session, row.application_id, row.event_id, row.kind)
            if not _allowed(policy, row.kind, row.application_id) or source is None or source[0] != row.source_digest or now >= row.expires_at:
                row.state, row.error_code = "cancelled", "source_or_scope_changed"
                session.commit()
                return None
            draft = row.kind == "interview_draft"
            charged = ProactiveJob.model_started_at if draft else ProactiveJob.published_at
            recent = session.scalar(select(func.count()).select_from(ProactiveJob).where(charged > now - DAY,
                ProactiveJob.kind == "interview_draft" if draft else ProactiveJob.kind != "interview_draft")) or 0
            if recent >= (policy.max_drafts_per_day if draft else policy.max_reminders_per_day) or quiet(policy, now):
                row.state, row.next_attempt_at = "pending", now + 3600
                session.commit()
                return None
            row.state = "running"
            if draft:
                # Charge before any dispatch. Uncertain admission cannot re-spend.
                row.model_started_at = now
                row.lease_until = min(row.lease_until, now + 60)
                conversation = Conversation(
                    title="Haru 主动准备草稿",
                    context_type="application",
                    context_ref=str(row.application_id),
                )
                session.add(conversation)
                session.flush()
                row.conversation_id = conversation.id
            result = {**_view(row), "source": source[1], "conversation_id": row.conversation_id, "lease_until": row.lease_until}
            session.commit()
            return result

    def publish(self, job_id: str, owner: str, turn_id: str, generation: int, content: str, now: float) -> bool:
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(ProactiveJob, job_id)
            if row is None or row.state != "running" or row.owner != owner or row.turn_id != turn_id or row.execution_generation != generation or row.lease_until <= now:
                return False
            _, policy = _policy(session)
            source = _source(session, row.application_id, row.event_id, row.kind)
            if not _allowed(policy, row.kind, row.application_id) or source is None or source[0] != row.source_digest or now >= row.expires_at:
                row.state, row.error_code = "cancelled", "source_or_scope_changed"
                session.commit()
                return False
            if quiet(policy, now):
                row.state, row.error_code = "cancelled", "quiet_hours_started"
                session.commit()
                return False
            if row.kind != "interview_draft":
                published = session.scalar(select(func.count()).select_from(ProactiveJob).where(
                    ProactiveJob.published_at > now - DAY, ProactiveJob.kind != "interview_draft")) or 0
                if published >= policy.max_reminders_per_day:
                    row.state, row.error_code = "cancelled", "daily_reminder_budget"
                    session.commit()
                    return False
            if not content.strip() or len(content.encode()) > 8192:
                row.state, row.error_code = "failed", "invalid_draft_output"
                session.commit()
                return False
            row.state, row.result_text, row.published_at = "succeeded", content, now
            session.commit()
            return True

    def dispatch_valid(self, job_id: str, owner: str, turn_id: str, generation: int, now: float) -> bool:
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(ProactiveJob, job_id)
            if row is None or row.state != "running" or row.owner != owner or row.turn_id != turn_id or row.execution_generation != generation or row.lease_until <= now:
                return False
            _, policy = _policy(session)
            source = _source(session, row.application_id, row.event_id, row.kind)
            valid = _allowed(policy, row.kind, row.application_id) and source is not None and source[0] == row.source_digest and now < row.expires_at and not quiet(policy, now)
            if not valid:
                row.state, row.error_code = "cancelled", "source_or_scope_changed"
                session.commit()
            return valid

    def fail(self, job_id: str, owner: str, *, unknown: bool) -> None:
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(ProactiveJob, job_id)
            if row is not None and row.owner == owner and row.state in ACTIVE:
                row.state = "result_unknown" if unknown else "failed"
                row.error_code = "runtime_result_requires_reconciliation" if unknown else "generation_failed"
            session.commit()
