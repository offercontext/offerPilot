"""Deterministic, bounded virtual life. LLM output never enters these writes."""
from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
import time
from typing import Any, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import Conversation, MockInterviewAttempt, PilotExecution
from offerpilot.proactive.contracts import ProactivePolicy
from offerpilot.proactive.models import ProactiveJob, ProactiveSettings
from .contracts import (
    CompanionCommand, CompanionEventCommand, CompanionPolicy, CompanionPresenceCommand,
    CompanionResetCommand, CompanionSettingsCommand,
)
from .models import (
    CompanionChatEvidence, CompanionCommandReceipt, CompanionEvent, CompanionParticipation, CompanionPresence,
    CompanionState, CompanionUsageSession,
)

from .persona import PERSONA_VERSION
from .persona_repository import read_active_persona_spec
MIN_STEP_SECONDS = 300
IDLE_COOLDOWN_SECONDS = 1800
PRESENCE_TTL_SECONDS = 60
SESSION_INACTIVITY_SECONDS = 1800
SOURCE_TTL_SECONDS = 3600
MAX_RECENT_EVENTS = 12
BLOCKER_FLAGS = ("typing", "work_running", "awaiting_approval", "mock_interview", "quiet", "failure")
WAKE_EVENTS = frozenset(("app_open", "chat_end", "work_completed", "user_interaction"))
NOTIFIABLE_EVENTS = frozenset(("activity_started", "activity_progressed", "activity_completed"))
TEMPLATES: tuple[dict[str, Any], ...] = (
    {"id": "paper_boat", "title": "折一只纸小船", "steps": ["挑一张想象中的彩纸", "折好船身", "给小船画一面旗"],
     "wish_at_step": 1, "wish": "一起给纸小船起个名字", "promise": "等你一起给纸小船起名字"},
    {"id": "cloud_catalog", "title": "收集云朵的奇怪名字", "steps": ["想象一朵软乎乎的云", "画下云朵的轮廓", "为云朵编一个小名字"],
     "wish_at_step": None, "wish": "一起给一朵云起个怪名字", "promise": "等你一起给云朵起名字"},
    {"id": "paper_stars", "title": "做一串纸星星", "steps": ["挑几张想象中的星星纸", "折出一颗胖星星", "把星星串成小挂饰"],
     "wish_at_step": 2, "wish": "一起挑一颗幸运星", "promise": "等你一起挑一颗幸运星"},
)


class CompanionConflict(ValueError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _empty_life() -> dict[str, Any]:
    # These are authored interests of a fictional companion, never user preferences.
    return {"interests": ["paper_crafts", "odd_ideas"], "activity": None, "wish": None}


def companion_policy(session: Session) -> tuple[int, CompanionPolicy]:
    row = session.get(CompanionState, 1)
    return (row.revision, CompanionPolicy.model_validate_json(row.settings_json)) if row else (0, CompanionPolicy())


def companion_presence(session: Session, now: float) -> dict[str, Any]:
    """Read all live clients conservatively, without advancing life or sessions."""
    state = session.get(CompanionState, 1)
    usage = session.get(CompanionUsageSession, 1)
    clients = session.scalars(select(CompanionPresence).where(
        CompanionPresence.identity == (state.identity if state else ""),
        CompanionPresence.seen_at > now - PRESENCE_TTL_SECONDS,
    )).all()
    flags = [json.loads(client.flags_json) for client in clients]
    active = any(item.get("visible", False) for item in flags)
    blockers = [key for key in BLOCKER_FLAGS if any(item.get(key, False) for item in flags)]
    if usage and usage.session_quiet and "quiet" not in blockers:
        blockers.append("quiet")
    if not active:
        blockers.insert(0, "offline")
    return {"session_id": usage.session_id if usage else "", "active": active,
            "blocked": bool(blockers), "blockers": blockers,
            "unanswered": bool(usage and usage.unanswered),
            "session_quiet": bool(usage and usage.session_quiet)}


def companion_client_active(session: Session, client_id: str, now: float) -> bool:
    state = session.get(CompanionState, 1)
    client = session.get(CompanionPresence, client_id)
    return bool(state and client and client.identity == state.identity
                and client.seen_at > now - PRESENCE_TTL_SECONDS
                and json.loads(client.flags_json).get("visible", False))


def _backend_busy(session: Session) -> bool:
    # Read product truth too: a stale/incorrect browser flag never overrides
    # pending confirmation, a running owner, or an unfinished mock interview.
    if session.scalar(select(PilotExecution.turn_id).where(
            PilotExecution.state.in_(("running", "waiting_confirmation", "result_unknown"))).limit(1)):
        return True
    if session.scalar(select(Conversation.id).where(
            Conversation.pending_tool_name != "").limit(1)):
        return True
    return session.scalar(select(MockInterviewAttempt.id).where(
        MockInterviewAttempt.cancelled_at.is_(None),
        MockInterviewAttempt.attempt_status.in_(("generating_question", "awaiting_answer", "generating_feedback")),
    ).limit(1)) is not None


def _event_view(row: CompanionEvent) -> dict[str, Any]:
    return {key: getattr(row, key) for key in (
        "id", "revision", "category", "event_type", "activity_id", "text", "source", "occurred_at",
    )}


def companion_snapshot(session: Session, now: float) -> dict[str, Any]:
    """Pure bounded read used by API and sourced context projection."""
    row = session.get(CompanionState, 1)
    if row is None:
        return {"identity": "", "revision": 0, "persona_version": PERSONA_VERSION,
                "settings": CompanionPolicy().model_dump(), "state": _empty_life(),
                "recent_events": [], "shared_participation": [],
                "presence": companion_presence(session, now)}
    events = session.scalars(select(CompanionEvent).where(CompanionEvent.identity == row.identity)
        .order_by(CompanionEvent.revision.desc()).limit(MAX_RECENT_EVENTS)).all()
    participation = session.scalars(select(CompanionParticipation).where(
        CompanionParticipation.identity == row.identity)
        .order_by(CompanionParticipation.created_at.desc()).limit(MAX_RECENT_EVENTS)).all()
    return {"identity": row.identity, "revision": row.revision, "persona_version": row.persona_version,
            "settings": CompanionPolicy.model_validate_json(row.settings_json).model_dump(),
            "state": json.loads(row.life_json), "recent_events": [_event_view(event) for event in events],
            "shared_participation": [{key: getattr(part, key) for key in (
                "id", "activity_id", "status", "promise", "created_at", "completed_at",
            )} for part in participation], "presence": companion_presence(session, now)}


def companion_source(session: Session) -> tuple[str, dict[str, Any]] | None:
    """Latest committed candidate only. Never invent catch-up or call a model."""
    row = session.get(CompanionState, 1)
    if row is None or row.latest_event_id is None:
        return None
    policy = CompanionPolicy.model_validate_json(row.settings_json)
    life = json.loads(row.life_json)
    activity = life.get("activity")
    event = session.get(CompanionEvent, row.latest_event_id)
    if (not policy.enabled or not activity or activity["status"] in ("paused", "waiting_shared", "abandoned")
            or event is None or event.identity != row.identity or event.event_type not in NOTIFIABLE_EVENTS):
        return None
    value = {"identity": row.identity, "revision": row.revision, "persona_version": row.persona_version,
             "event_id": event.id, "event_revision": event.revision, "event_type": event.event_type,
             "category": event.category, "source": event.source, "text": event.text,
             "occurred_at": event.occurred_at, "expires_at": event.occurred_at + SOURCE_TTL_SECONDS,
             "activity": activity, "wish": life.get("wish"), "settings": policy.model_dump()}
    return sha256(_json(value).encode()).hexdigest(), value


def companion_publish_guard(session: Session, now: float) -> bool:
    """Must be checked in the SAME write transaction as queue publication."""
    _, policy = companion_policy(session)
    if not policy.enabled or policy.proactivity == "off":
        return False
    source = companion_source(session)
    if source is None or now >= source[1]["expires_at"]:
        return False
    presence = companion_presence(session, now)
    if presence["blocked"] or presence["unanswered"] or _backend_busy(session):
        return False
    config = session.get(ProactiveSettings, 1)
    timezone_name = (ProactivePolicy.model_validate_json(config.settings_json).timezone
                     if config else "Asia/Shanghai")
    local = datetime.fromtimestamp(now, ZoneInfo(timezone_name))
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    day_end = (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
    published = (ProactiveJob.kind == "companion_life", ProactiveJob.published_at.is_not(None))
    limit = 1 if policy.proactivity == "less" else 2
    for start, end in ((day_start, day_end), (now - 86400, now + 1)):
        count = session.scalar(select(func.count()).select_from(ProactiveJob).where(
            *published, ProactiveJob.published_at >= start, ProactiveJob.published_at < end)) or 0
        if count >= limit:
            return False
    latest = session.scalar(select(func.max(ProactiveJob.published_at)).where(*published))
    return latest is None or now - latest >= 3600


def mark_companion_published(session: Session, now: float, event_id: str) -> bool:
    """Mark unanswered-bid guard after caller's guard, before its publication commit."""
    source = companion_source(session)
    usage = session.get(CompanionUsageSession, 1)
    if source is None or source[1]["event_id"] != event_id or usage is None:
        return False
    usage.unanswered = True
    return True


class CompanionRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions
        self.initialize()

    def initialize(self) -> None:
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            if session.get(CompanionState, 1) is None:
                session.add(CompanionState(id=1, identity=str(uuid4()), revision=0,
                    persona_version=PERSONA_VERSION, settings_json=CompanionPolicy().model_dump_json(),
                    life_json=_json(_empty_life()), latest_event_id=None, sequence=0, updated_at=0))
            state = session.get(CompanionState, 1)
            if state is not None:
                changed = state.persona_version != PERSONA_VERSION
                state.persona_version = PERSONA_VERSION
                life = json.loads(state.life_json)
                activity = life.get("activity")
                if activity is not None and "wish_offered" not in activity:
                    # Earlier versions offered on creation. Missing history is
                    # not permission to re-offer a declined or accepted wish.
                    # Preserve any still-pending wish exactly as it was.
                    activity["wish_offered"] = True
                    state.life_json = _json(life)
                    changed = True
                if activity is not None and any(
                    template["id"] == activity.get("template_id") for template in TEMPLATES
                ):
                    # Only the authoritative current entity can recover its
                    # old structured binding. Never guess historical types
                    # from event prose, nor overwrite conflicting metadata.
                    for event in session.scalars(select(CompanionEvent).where(
                        CompanionEvent.identity == state.identity,
                        CompanionEvent.activity_id == activity["id"],
                        CompanionEvent.template_id.is_(None),
                    )):
                        event.template_id = activity["template_id"]
                        changed = True
                if changed:
                    # One startup normalization, one fence, including persona
                    # upgrades. A second initializer must remain a pure no-op.
                    state.revision += 1
            if session.get(CompanionUsageSession, 1) is None:
                session.add(CompanionUsageSession(id=1, session_id=str(uuid4()), last_seen_at=0, unanswered=False, session_quiet=False))
            session.commit()

    def read(self, now: float | None = None) -> dict[str, Any]:
        with self.sessions() as session:
            return companion_snapshot(session, time.time() if now is None else now)

    def presence(self, command: CompanionPresenceCommand, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            state = self._state(session)
            if str(command.identity) != state.identity:
                raise CompanionConflict("identity_conflict")
            # TTL controls visibility, not ordering. Retain high-water marks
            # until reset so delayed packets cannot resurrect expired clients.
            row = session.get(CompanionPresence, str(command.client_id))
            flags_json = _json(command.model_dump(exclude={"identity", "client_id", "sequence"}))
            if row is not None:
                if command.sequence < row.last_sequence:
                    return companion_presence(session, now)
                if command.sequence == row.last_sequence:
                    if flags_json != row.flags_json:
                        raise CompanionConflict("presence_sequence_conflict")
                    return companion_presence(session, now)
            else:
                row = CompanionPresence(client_id=str(command.client_id))
                session.add(row)
            row.identity, row.seen_at = state.identity, now
            row.last_sequence, row.flags_json = command.sequence, flags_json
            usage = session.get(CompanionUsageSession, 1)
            assert usage is not None
            if command.visible:
                if now - usage.last_seen_at >= SESSION_INACTIVITY_SECONDS:
                    usage.session_id, usage.unanswered = str(uuid4()), False
                    usage.session_quiet = False
                usage.last_seen_at = now
            session.flush()
            result = companion_presence(session, now)
            session.commit()
            return result

    @staticmethod
    def _state(session: Session) -> CompanionState:
        row = session.get(CompanionState, 1)
        assert row is not None
        return row

    @staticmethod
    def _check(session: Session, row: CompanionState, command: CompanionCommand,
               kind: str) -> tuple[str, dict[str, Any] | None]:
        digest = sha256((kind + ":" + _json(command.model_dump(mode="json"))).encode()).hexdigest()
        receipt = session.get(CompanionCommandReceipt, str(command.command_id))
        if receipt is not None:
            if (receipt.identity != str(command.identity) or receipt.request_digest != digest
                    or receipt.resulting_identity != row.identity):
                raise CompanionConflict("command_id_conflict")
            return digest, cast(dict[str, Any], json.loads(receipt.response_json))
        if str(command.identity) != row.identity:
            raise CompanionConflict("identity_conflict")
        if command.expected_revision != row.revision:
            raise CompanionConflict("revision_conflict")
        return digest, None

    @staticmethod
    def _finish(session: Session, row: CompanionState, command: CompanionCommand,
                digest: str, changed: bool, now: float) -> dict[str, Any]:
        session.flush()
        result = companion_snapshot(session, now)
        result.update(receipt={"command_id": str(command.command_id), "revision": row.revision}, changed=changed)
        session.add(CompanionCommandReceipt(command_id=str(command.command_id), identity=str(command.identity),
            resulting_identity=row.identity, request_digest=digest, response_json=_json(result)))
        session.commit()
        return result

    @staticmethod
    def _event(session: Session, row: CompanionState, life: dict[str, Any], now: float,
               event_type: str, message: str, *, actual: bool = False) -> None:
        activity = life.get("activity")
        if actual and activity is not None and "user" in activity["participants"]:
            # Both card commands and sourced chat participation use this write
            # path. Keep a content-free latch even if chat evidence is erased.
            activity["wish_offered"] = True
        row.revision += 1
        row.updated_at = now
        row.life_json = _json(life)
        event_id = str(uuid4())
        session.add(CompanionEvent(id=event_id, identity=row.identity, revision=row.revision,
            category="actual" if actual else "virtual", event_type=event_type,
            activity_id=life["activity"]["id"] if life.get("activity") else None, text=message,
            template_id=activity.get("template_id") if activity else None,
            source="user_command" if actual else "deterministic_template", occurred_at=now))
        row.latest_event_id = event_id

    def apply_event(self, command: CompanionEventCommand, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = self._state(session)
            digest, receipt = self._check(session, row, command, "event")
            if receipt is not None:
                return receipt
            life = json.loads(row.life_json)
            policy = CompanionPolicy.model_validate_json(row.settings_json)
            if command.event in ("quiet_session", "resume_session"):
                usage = session.get(CompanionUsageSession, 1)
                assert usage is not None
                desired_quiet = command.event == "quiet_session"
                changed = usage.session_quiet != desired_quiet
                usage.session_quiet = desired_quiet
                # An explicit session control is real current use, including
                # before the first heartbeat. It never acknowledges a prior bid.
                usage.last_seen_at = max(usage.last_seen_at, now)
                if changed:
                    row.revision += 1
                    row.updated_at = now
                return self._finish(session, row, command, digest, changed, now)
            if command.event in ("user_interaction", "join", "accept_wish", "complete_shared"):
                usage = session.get(CompanionUsageSession, 1)
                if usage is not None:
                    usage.unanswered = False
            if command.event in WAKE_EVENTS:
                changed = policy.enabled and self._advance(session, row, life, now)
            else:
                if not policy.enabled:
                    raise CompanionConflict("companion_disabled")
                changed = self._interact(session, row, life, command, now)
            return self._finish(session, row, command, digest, changed, now)

    def _advance(self, session: Session, row: CompanionState, life: dict[str, Any], now: float) -> bool:
        activity = life.get("activity")
        if activity is not None:
            if activity["status"] in ("paused", "waiting_shared"):
                return False
            minimum = (IDLE_COOLDOWN_SECONDS if activity["status"] in ("completed", "abandoned")
                       else MIN_STEP_SECONDS)
            if now - activity["updated_at"] < minimum:
                return False
        if activity is None or activity["status"] in ("completed", "abandoned"):
            # Expression interests do not grant capabilities. This whitelist is
            # consulted only for a new activity; existing activities and shared
            # promises continue against the complete registry below.
            allowed = read_active_persona_spec(session).enabled_activity_templates
            candidates = tuple(item for item in TEMPLATES if item["id"] in allowed)
            if not candidates:
                return False
            template = candidates[row.sequence % len(candidates)]
            row.sequence += 1
            activity = {"id": str(uuid4()), "template_id": template["id"], "title": template["title"],
                "status": "active", "step_index": 0, "step_count": len(template["steps"]),
                "step_title": template["steps"][0], "started_at": now, "updated_at": now,
                "participants": ["companion"], "promise": None, "wish_offered": False}
            life["activity"] = activity
            life["wish"] = None
            self._event(session, row, life, now, "activity_started", ("在自己的小世界里，开始" if row.sequence % 2 else "在想象的小角落里，打算") + template["title"])
            return True
        template = next(item for item in TEMPLATES if item["id"] == activity["template_id"])
        activity["updated_at"] = now
        if activity["step_index"] + 1 >= activity["step_count"]:
            activity["status"] = "completed"
            life["wish"] = None
            self._event(session, row, life, now, "activity_completed", "在自己的小世界里，完成了" + activity["title"])
        else:
            activity["step_index"] += 1
            activity["step_title"] = template["steps"][activity["step_index"]]
            if (activity["step_index"] == template["wish_at_step"]
                    and not activity.get("wish_offered", True)
                    and "user" not in activity["participants"] and life.get("wish") is None):
                # The invitation belongs to this actual progress event, not a
                # timer, heartbeat, read or separate queue-triggered advance.
                activity["wish_offered"] = True
                life["wish"] = {"id": str(uuid4()), "activity_id": activity["id"], "text": template["wish"]}
            self._event(session, row, life, now, "activity_progressed", ("在自己的小世界里，正在" if row.sequence % 2 else "在想象的小角落里，试着") + activity["step_title"])
        return True

    def _interact(self, session: Session, row: CompanionState, life: dict[str, Any],
                  command: CompanionEventCommand, now: float) -> bool:
        activity = life.get("activity")
        if activity is None or str(command.activity_id) != activity["id"]:
            raise CompanionConflict("activity_identity_conflict")
        event = command.event
        if event in ("accept_wish", "decline_wish"):
            wish = life.get("wish")
            if not wish or str(command.wish_id) != wish["id"]:
                raise CompanionConflict("wish_identity_conflict")
            life["wish"] = None
            activity["wish_offered"] = True
            if event == "decline_wish":
                self._event(session, row, life, now, "wish_declined", "用户略过了这个小心愿", actual=True)
                return True
        status = activity["status"]
        if event == "pause":
            if status not in ("active", "waiting_shared"):
                raise CompanionConflict("activity_not_pausable")
            activity["paused_from"] = status
            activity["status"] = "paused"
            kind, message = "activity_paused", "用户暂停了这件小事"
        elif event == "resume":
            if status != "paused":
                raise CompanionConflict("activity_not_paused")
            activity["status"] = activity.pop("paused_from", "active")
            kind, message = "activity_resumed", "用户恢复了这件小事"
        elif event == "abandon":
            if status not in ("active", "waiting_shared", "paused"):
                raise CompanionConflict("activity_already_terminal")
            activity["status"], activity["promise"] = "abandoned", None
            activity.pop("shared_step", None)
            activity.pop("paused_from", None)
            life["wish"] = None
            for abandoned_part in session.scalars(select(CompanionParticipation).where(
                    CompanionParticipation.identity == row.identity,
                    CompanionParticipation.activity_id == activity["id"], CompanionParticipation.status == "promised")):
                abandoned_part.status = "abandoned"
            kind, message = "activity_abandoned", "用户结束了这件小事"
        elif event in ("join", "accept_wish"):
            if status != "active" or "user" in activity["participants"]:
                raise CompanionConflict("activity_not_joinable")
            template = next(item for item in TEMPLATES if item["id"] == activity["template_id"])
            activity["status"], activity["promise"] = "waiting_shared", template["promise"]
            activity["participants"] = ["companion", "user"]
            life["wish"] = None
            session.add(CompanionParticipation(id=str(uuid4()), identity=row.identity,
                activity_id=activity["id"], status="promised", promise=template["promise"],
                created_at=now, completed_at=None))
            kind, message = "shared_joined", "用户明确加入了这件小事；共同的一步尚未完成"
        elif event == "withdraw_shared":
            if status != "waiting_shared" and not (status == "paused" and activity.get("paused_from") == "waiting_shared"):
                raise CompanionConflict("activity_not_waiting_shared")
            shared = activity.get("shared_step")
            parts = session.scalars(select(CompanionParticipation).where(
                CompanionParticipation.identity == row.identity,
                CompanionParticipation.activity_id == activity["id"],
                CompanionParticipation.status == "promised")).all()
            if len(parts) != 1 or (shared and shared.get("participation_id") != parts[0].id):
                raise CompanionConflict("participation_missing")
            parts[0].status = "abandoned"
            activity["promise"] = None
            activity["participants"] = ["companion"]
            activity.pop("shared_step", None)
            if status == "paused":
                activity["paused_from"] = "active"
            else:
                activity["status"] = "active"
            life["wish"] = None
            activity["wish_offered"] = True
            activity["updated_at"] = now
            # This only releases the user's pending step. It neither advances
            # the companion's work nor rewrites prior completed participation.
            # Time spent waiting was not independent work: keep the next wake
            # behind the normal step delay, including after a long absence.
            self._event(session, row, life, now, "shared_withdrawn",
                        "用户退出了当前约定的一步，未完成这次共同参与；活动本身保留", actual=True)
            return True
        elif event == "complete_shared":
            if status != "waiting_shared":
                raise CompanionConflict("activity_not_waiting_shared")
            part = session.scalar(select(CompanionParticipation).where(
                CompanionParticipation.identity == row.identity,
                CompanionParticipation.activity_id == activity["id"], CompanionParticipation.status == "promised"))
            if part is None:
                raise CompanionConflict("participation_missing")
            part.status, part.completed_at = "completed", now
            activity["status"], activity["promise"] = "active", None
            activity.pop("shared_step", None)
            kind, message = "shared_completed", "用户明确确认完成了约好的一步"
        else:
            raise CompanionConflict("unsupported_event")
        activity["updated_at"] = now
        self._event(session, row, life, now, kind, message, actual=True)
        return True

    def update_settings(self, command: CompanionSettingsCommand, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = self._state(session)
            digest, receipt = self._check(session, row, command, "settings")
            if receipt is not None:
                return receipt
            changed = CompanionPolicy.model_validate_json(row.settings_json) != command.settings
            if changed:
                row.revision += 1
                row.updated_at = now
                row.settings_json = command.settings.model_dump_json()
            return self._finish(session, row, command, digest, changed, now)

    def reset(self, command: CompanionResetCommand, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        if not command.confirmed:
            raise CompanionConflict("explicit_confirmation_required")
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = self._state(session)
            digest, receipt = self._check(session, row, command, "reset")
            if receipt is not None:
                return receipt
            for model in (CompanionChatEvidence, CompanionEvent, CompanionParticipation, CompanionPresence, CompanionCommandReceipt):
                session.execute(delete(model))
            # Erase old virtual prose from the shared queue, retaining only the
            # publication timestamps/identities needed to prevent budget bypass.
            for job in session.scalars(select(ProactiveJob).where(ProactiveJob.kind == "companion_life")):
                job.result_text = ""
                if job.state in ("pending", "leased", "running"):
                    job.state, job.error_code = "cancelled", "companion_reset"
            row.identity, row.revision = str(uuid4()), row.revision + 1
            row.life_json, row.latest_event_id, row.sequence = _json(_empty_life()), None, 0
            row.updated_at = now
            # Retain preferences and the content-free unanswered-bid guard. Clearing memories
            # cannot reset daily queue publication counts or unanswered suppression.
            return self._finish(session, row, command, digest, True, now)
