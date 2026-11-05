from __future__ import annotations

# 业务地图：docs/architecture/code-map/workbench-automation.md（主动队列状态与发布条件）。

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from time import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import Application, ApplicationEvent, Conversation
from .candidates import DAY, list_display_candidates, reminder_source, source_candidate, subject
from .contracts import ProactivePolicy, ProactivePolicyUpdate
from .models import ProactiveJob, ProactiveSettings
from offerpilot.companion.repository import (
    companion_client_active, companion_policy,
    companion_publish_guard, companion_source, mark_companion_published,
)

COMPANION_KIND = "companion_life"

ACTIVE = ("pending", "leased", "running")
MAX_QUEUE = 100
MAX_RETAINED_JOBS = 2000
REASONS = {"deadline": "投递截止时间临近", "interview_preparation": "面试临近，可提前准备",
           "stale_application": "投递已有七天未更新", "review": "面试结束，可记录复盘",
           "interview_draft": "已开启面试准备草稿"}


class ProactiveConflict(ValueError):
    pass


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
    if kind == COMPANION_KIND:
        return companion_source(session)
    _, policy = _policy(session)
    return reminder_source(session, application_id, event_id, kind, draft_timezone=policy.timezone)


def _job_allowed(session: Session, policy: ProactivePolicy, row: ProactiveJob) -> bool:
    if row.kind == COMPANION_KIND:
        _, settings = companion_policy(session)
        return bool(settings.enabled and settings.proactivity != "off")
    return _allowed(policy, row.kind, row.application_id)


def _view(row: ProactiveJob) -> dict[str, Any]:
    value = {key: getattr(row, key) for key in ("id", "kind", "application_id", "event_id", "state", "reason",
        "due_at", "created_at", "published_at", "result_text", "turn_id", "execution_generation", "error_code")}
    # The companion's application_id=0 is an isolation sentinel, not a reminder subject.
    if row.kind != COMPANION_KIND:
        value.update(subject(row.application_id, row.event_id, row.kind), source_revision=row.source_digest)
    return value


class ProactiveRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def reminder_candidates(self, now: float, timezone_name: str = "UTC") -> list[dict[str, Any]]:
        with self.sessions() as session:
            return list_display_candidates(session, now, timezone_name)

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
                if job.kind != COMPANION_KIND and not _allowed(command.settings, job.kind, job.application_id):
                    job.state, job.error_code = "cancelled", "scope_disabled"
                    job.result_text = ""
            session.commit()
            return {"revision": revision + 1, "settings": command.settings.model_dump()}

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            # A removed source must also disappear from stored draft display.
            rows = session.scalars(select(ProactiveJob).where(ProactiveJob.kind != COMPANION_KIND).order_by(ProactiveJob.created_at.desc()).limit(100)).all()
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
            sources: list[tuple[int, int | None, str]] = [(event.application_id, event.id,
                "deadline" if event.event_type == "deadline" else "review" if event.status == "done" else "interview_preparation")
                for event in events]
            sources.extend((app.id, None, "stale_application") for app in
                session.scalars(select(Application).where(Application.id.in_(policy.application_ids)).limit(100)))
            for app_id, event_id, kind in sources:
                candidate = source_candidate(session, app_id, event_id, kind, now)
                if candidate is None or candidate.delivery_due_at is None or candidate.delivery_expires_at is None:
                    continue
                kinds = (kind, "interview_draft") if kind == "interview_preparation" else (kind,)
                candidates.extend((app_id, event_id, job_kind, candidate.delivery_due_at, candidate.delivery_expires_at)
                                  for job_kind in kinds)
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

    def claim(self, owner: str, now: float, *, companion_only: bool = False) -> str | None:
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
                ProactiveJob.next_attempt_at <= now,
                ProactiveJob.kind == COMPANION_KIND if companion_only else ProactiveJob.kind != COMPANION_KIND).order_by(ProactiveJob.due_at).limit(MAX_QUEUE)).all()
            selected = None
            for row in rows:
                source = _source(session, row.application_id, row.event_id, row.kind)
                if not _job_allowed(session, policy, row) or now >= row.expires_at or source is None or source[0] != row.source_digest:
                    row.state, row.error_code = "cancelled", "source_or_scope_changed"
                    continue
                if quiet(policy, now) or (row.kind == COMPANION_KIND and not companion_publish_guard(session, now)):
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
            if not _job_allowed(session, policy, row) or source is None or source[0] != row.source_digest or now >= row.expires_at:
                row.state, row.error_code = "cancelled", "source_or_scope_changed"
                session.commit()
                return None
            if row.kind == COMPANION_KIND:
                if quiet(policy, now) or not companion_publish_guard(session, now):
                    row.state, row.next_attempt_at = "pending", now + 30
                    session.commit()
                    return None
                row.state = "running"
                result = {**_view(row), "source": source[1], "lease_until": row.lease_until}
                session.commit()
                return result
            draft = row.kind == "interview_draft"
            charged = ProactiveJob.model_started_at if draft else ProactiveJob.published_at
            recent = session.scalar(select(func.count()).select_from(ProactiveJob).where(charged > now - DAY,
                ProactiveJob.kind == "interview_draft" if draft else ProactiveJob.kind.not_in(("interview_draft", COMPANION_KIND)))) or 0
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
            source_value = source[1]
            if draft:
                # The observation time is not a source version: it is frozen at
                # dispatch and must not invalidate the job on every clock tick.
                source_value = {**source_value,
                    "current_time": datetime.fromtimestamp(now, ZoneInfo(policy.timezone)).isoformat()}
            result = {**_view(row), "source": source_value, "conversation_id": row.conversation_id, "lease_until": row.lease_until}
            session.commit()
            return result

    def publish(self, job_id: str, owner: str, turn_id: str, generation: int, content: str, now: float, *, delivery_client_id: str | None = None) -> bool:
        # 模型完成后仍需核对本代所有权、租约、范围、来源、期限和静默时段。
        # 这里发布队列结果文本，不向外部发送，也不创建正式面试成果。
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(ProactiveJob, job_id)
            if row is None or row.state != "running" or row.owner != owner or row.turn_id != turn_id or row.execution_generation != generation or row.lease_until <= now:
                return False
            _, policy = _policy(session)
            source = _source(session, row.application_id, row.event_id, row.kind)
            if not _job_allowed(session, policy, row) or source is None or source[0] != row.source_digest or now >= row.expires_at:
                row.state, row.error_code = "cancelled", "source_or_scope_changed"
                session.commit()
                return False
            if quiet(policy, now):
                row.state, row.error_code = "cancelled", "quiet_hours_started"
                session.commit()
                return False
            if row.kind == COMPANION_KIND:
                if (delivery_client_id is None
                        or not companion_client_active(session, delivery_client_id, now)
                        or not companion_publish_guard(session, now)):
                    row.state, row.error_code = "cancelled", "companion_delivery_suppressed"
                    session.commit()
                    return False
                # Only the deterministic persisted event may be delivered, never
                # model free text. Budget/session state is committed with delivery.
                content = source[1]["text"]
                if not mark_companion_published(session, now, source[1]["event_id"]):
                    row.state, row.error_code = "cancelled", "companion_session_unavailable"
                    session.commit()
                    return False
            elif row.kind != "interview_draft":
                published = session.scalar(select(func.count()).select_from(ProactiveJob).where(
                    ProactiveJob.published_at > now - DAY, ProactiveJob.kind.not_in(("interview_draft", COMPANION_KIND)))) or 0
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
            valid = _job_allowed(session, policy, row) and source is not None and source[0] == row.source_digest and now < row.expires_at and not quiet(policy, now)
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

    def discover_companion(self, now: float) -> int:
        """Queue one current event, independently of life state advancement.

        Companion jobs share the durable queue and publication fences but are
        only dispatched on a visible client's request, never by the scheduler.
        application_id=0 denotes the isolated workspace companion, not a job.
        """
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            source = companion_source(session)
            if source is None or not companion_publish_guard(session, now):
                return 0
            digest, value = source
            _, policy = _policy(session)
            if quiet(policy, now) or now >= value["expires_at"]:
                return 0
            # Keep the existing bounded retention policy even when work-related
            # proactivity is disabled; publication accounting needs only 24 hours.
            session.execute(delete(ProactiveJob).where(
                ProactiveJob.kind == COMPANION_KIND,
                ProactiveJob.state.in_(("succeeded", "failed", "cancelled")),
                ProactiveJob.created_at < now - 7 * DAY,
            ))
            # Clean stale candidates without changing the independent life state.
            for row in session.scalars(select(ProactiveJob).where(
                ProactiveJob.kind == COMPANION_KIND, ProactiveJob.state.in_(ACTIVE),
            )):
                if row.source_digest != digest or row.expires_at <= now:
                    row.state, row.error_code = "cancelled", "source_changed"
            count = session.scalar(select(func.count()).select_from(ProactiveJob).where(
                ProactiveJob.state.in_(ACTIVE))) or 0
            retained = session.scalar(select(func.count()).select_from(ProactiveJob)) or 0
            identity = sha256(f"companion:{value['identity']}:{value['event_id']}:{digest}".encode()).hexdigest()
            exists = session.scalar(select(ProactiveJob.id).where(ProactiveJob.idempotency_key == identity))
            if exists or count >= MAX_QUEUE or retained >= MAX_RETAINED_JOBS:
                session.commit()
                return 0
            session.add(ProactiveJob(
                id=str(uuid4()), idempotency_key=identity, kind=COMPANION_KIND,
                application_id=0, event_id=None, source_digest=digest,
                reason="虚拟日常", due_at=now, expires_at=value["expires_at"], created_at=now,
            ))
            session.commit()
            return 1

    def take_companion_bid(self, identity: str, revision: int, client_id: str, now: float | None = None) -> dict[str, Any] | None:
        """At-most-once visible-client delivery through the normal queue sink.

        Lost responses are not replayed; silence is preferable to duplicate bids.
        Each stage rechecks state, quiet hours, presence and publication budgets.
        """
        def clock() -> float:
            return now if now is not None else time()

        stamp = clock()
        with self.sessions() as session:
            source = companion_source(session)
            if source is None or source[1]["identity"] != identity or source[1]["revision"] != revision:
                return None
            if not companion_client_active(session, client_id, stamp):
                return None
        self.discover_companion(clock())
        owner = str(uuid4())
        job_id = self.claim(owner, clock(), companion_only=True)
        if job_id is None:
            return None
        job = self.begin(job_id, owner, clock())
        if job is None:
            return None
        source_value = job["source"]
        # A concurrent transition must not deliver a newer event to an older UI.
        if source_value["identity"] != identity or source_value["revision"] != revision:
            self.cancel(job_id)
            return None
        if not self.publish(job_id, owner, job["turn_id"], job["execution_generation"], source_value["text"], clock(), delivery_client_id=client_id):
            return None
        return {**job, "source": None, "state": "succeeded", "published_at": clock(),
                "result_text": source_value["text"], "source_revision": revision,
                "source_identity": identity, "source_event_type": source_value["event_type"]}
