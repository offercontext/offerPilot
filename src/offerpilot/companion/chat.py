"""Closed, user-authored life commands inside the existing Pilot turn owner.

No provider output, tool arguments or quoted conversation text enters this path.
Admission freezes a parsed command and its target; execution revalidates the real
user message, source, owner and identity/revision in one SQLite transaction.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
import time
from typing import Any
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import ChatMessage, Conversation, PilotTurnRecord
from offerpilot.pilot_control import current_execution_scope, require_execution_before_handler
from .context import _WORK_EN, _WORK_ZH
from .contracts import CompanionCommand, CompanionEventCommand, CompanionPolicy
from .models import (
    CompanionChatEvidence, CompanionCommandReceipt, CompanionEvent,
    CompanionParticipation, CompanionState, CompanionUsageSession,
)
from .repository import CompanionConflict, CompanionRepository, companion_snapshot, _json

SOURCE_KEY = "companion_chat_v1"
_STEP_PROMISES = {
    "color": "等你一起选颜色",
    "name": "等你一起起个名字",
    "star": "等你一起挑一颗幸运星",
}
_STEP_QUESTIONS = {
    "color": "想选什么颜色？",
    "name": "想给它起什么名字？",
    "star": "想选哪一颗星星？",
}
# Whole-utterance patterns, not substring triggers. Unknown/mixed requests go
# through ordinary chat without a life write. Generic 'resume/done' never writes.
_TARGET = r"(?:你的|我们(?:刚才)?(?:的)?|这(?:个|项)|那(?:个|项))?(?:折纸(?:活动)?|纸小船|小船(?:活动)?|纸星星|云朵(?:活动)?|手工(?:活动)?|小日常|日常活动|这件小事|共同活动)"
_END = r"(?:一下|一会儿)?(?:吧|啦|哦)?[。！!]?"
_PREFIX = r"(?:(?:Pilot|Haru|小助手)[，, ]*)?(?:请|麻烦你)?"
_STEP = r"(?:(?:给|为)(?:纸小船|小船|云朵|纸星星|它))?(?P<step>选(?:个)?颜色|挑(?:个)?颜色|起(?:个)?名字|取(?:个)?名字|挑一颗(?:幸运)?星(?:星)?)"
_COLORS = r"(?:红|蓝|黄|绿|紫|粉|白|黑|橙|青|金|银|天蓝|浅蓝|深蓝|淡紫|浅绿|米白|彩虹)(?:色)?"


@dataclass(frozen=True)
class ChatIntent:
    action: str
    step_kind: str | None = None
    value: str | None = None


def parse_chat_intent(message: str, *, shared_kind: str | None = None,
                      shared_withdrawable: bool = False) -> ChatIntent | None:
    """Conservative full sentences; only a bound shared prompt admits short replies."""
    if not message or len(message) > 160 or any(word in message for word in _WORK_ZH) or _WORK_EN.search(message):
        return None
    value = message.strip()
    # Reported/conditional/quoted language is never an instruction. A literal
    # name is accepted only after the explicit '叫/取名' introducer below.
    if re.search(r'[\n\r`<>“”"「」]|(?:如果|假如|要是|假设|他说|她说|别人说|你说|比如|例如|能不能解释|不要|不想|不打算|并不|没有)', value):
        return None
    # Withdrawal is a separate whole-utterance intent, never inferred from
    # ordinary dislike or negation. Deictic wording needs a live conversation
    # binding supplied at admission; an explicit object is checked there too.
    withdrawal = r'(?:我这次不参加了[，,]?)?'
    explicit = r'我这次不参加了[，,]?你自己(?:继续)?(?:折(?:纸小船|小船|纸)|做纸星星|收集云朵)'
    deictic = withdrawal + r'(?:这(?:一)?步你自己(?:做|继续)|你自己继续)'
    if (re.fullmatch(_PREFIX + explicit + _END, value, re.IGNORECASE)
            or shared_withdrawable and re.fullmatch(_PREFIX + deictic + _END, value, re.IGNORECASE)):
        return ChatIntent('withdraw_shared')
    wait = re.fullmatch(_PREFIX + r"(?:先别往下做[，,]?|先停在这里[，,]?)?等我(?:回来|回头|有空)?[，,]?(?:我们|咱们)?一起" + _STEP + _END, value, re.IGNORECASE)
    join = re.fullmatch(_PREFIX + r"(?:我回来了[，,]?)?(?:我们|咱们|我(?:也)?(?:想|要)?(?:和你)?)(?:来)?一起" + _STEP + _END, value, re.IGNORECASE)
    match = wait or join
    if match:
        step = match['step']
        kind = 'color' if '颜色' in step else 'name' if '名字' in step else 'star'
        return ChatIntent('wait' if wait else 'join', kind)
    for action, verb in (('pause', r'(?:暂停|停一下|先停下)'), ('resume', r'(?:恢复|继续)')):
        if (re.fullmatch(_PREFIX + r'(?:先)?' + verb + _TARGET + _END, value, re.IGNORECASE)
                or re.fullmatch(_PREFIX + _TARGET + r'(?:先)?' + verb + _END, value, re.IGNORECASE)):
            return ChatIntent(action)
    if re.fullmatch(_PREFIX + r'(?:我们|咱们|我(?:也)?(?:想|要)?(?:和你)?)(?:来)?一起(?:折纸|做手工|折小船|做纸星星)' + _END, value, re.IGNORECASE):
        return ChatIntent('join')
    if shared_kind == 'color':
        answer = re.fullmatch(r'(?:我(?:想)?选|就选|选|就|用)?(?P<value>' + _COLORS + r')' + _END, value)
        if answer:
            return ChatIntent('answer', 'color', answer['value'])
    if shared_kind == 'name':
        answer = re.fullmatch(r'(?:就)?(?:叫|取名叫|起名叫)(?P<value>[\w\u4e00-\u9fff]{1,24}?)(?:吧|好了)?[。！!]?', value)
        if answer:
            return ChatIntent('answer', 'name', answer['value'])
    if shared_kind == 'star':
        answer = re.fullmatch(r'(?:我选|就选|选)?(?P<value>第[一二三]颗|胖胖的那颗|最小的那颗)' + _END, value)
        if answer:
            return ChatIntent('answer', 'star', answer['value'])
    return None


def freeze_chat_command(session: Session, conversation: Any, request: Any) -> dict[str, str]:
    """Called in the existing turn admission transaction, before provider work."""
    if (conversation.context_type != 'workspace' or conversation.mode != 'general'
            or conversation.pending_tool_call_id or request.attachments
            or getattr(request, 'image_attachment_ids', ()) or request.pilot_action is not None):
        return {}
    row = session.get(CompanionState, 1)
    if row is None or not CompanionPolicy.model_validate_json(row.settings_json).enabled:
        return {}
    life = json.loads(row.life_json)
    activity = life.get('activity')
    if not activity or activity['status'] in ('completed', 'abandoned'):
        return {}
    # An explicit different object is not an alias for the current activity.
    for words, templates in ((('小船',), ('paper_boat',)), (('云朵',), ('cloud_catalog',)),
                             (('纸星星',), ('paper_stars',)), (('折纸', '手工'), ('paper_boat', 'paper_stars'))):
        if any(word in request.message for word in words) and activity['template_id'] not in templates:
            return {}
    shared = activity.get('shared_step')
    # A short reply is only an answer while this conversation is still on the
    # bound shared step. Intervening ordinary/work chat closes that reply slot.
    last_user_id = session.scalar(select(ChatMessage.id).where(
        ChatMessage.conversation_id == conversation.id, ChatMessage.role == 'user',
    ).order_by(ChatMessage.id.desc()).limit(1))
    previous = session.scalar(select(CompanionChatEvidence).where(
        CompanionChatEvidence.message_id == last_user_id,
        CompanionChatEvidence.identity == row.identity,
        CompanionChatEvidence.activity_id == activity['id'],
        CompanionChatEvidence.action.in_(('join', 'wait', 'resume')),
    )) if last_user_id is not None else None
    bound_kind = (shared['kind'] if shared and shared.get('conversation_id') == conversation.id
                  and activity['status'] == 'waiting_shared' and previous is not None else None)
    # For V1, chat withdrawal stays in the shared step's source conversation.
    # A different conversation is not allowed to acquire ownership of this
    # participation merely by naming it. The explicit card remains a fallback.
    withdrawable = bool(shared and shared.get('conversation_id') == conversation.id
                        and (activity['status'] == 'waiting_shared'
                             or activity['status'] == 'paused' and activity.get('paused_from') == 'waiting_shared'))
    withdrawal_previous = session.scalar(select(CompanionChatEvidence).where(
        CompanionChatEvidence.message_id == last_user_id,
        CompanionChatEvidence.identity == row.identity,
        CompanionChatEvidence.activity_id == activity['id'],
        CompanionChatEvidence.action.in_(('join', 'wait', 'pause', 'resume')),
    )) if withdrawable and last_user_id is not None else None
    shared_withdrawable = withdrawable and withdrawal_previous is not None
    intent = parse_chat_intent(request.message, shared_kind=bound_kind, shared_withdrawable=shared_withdrawable)
    if intent is None:
        return {}
    if intent.action == 'withdraw_shared' and not withdrawable:
        return {}
    command = {'identity': row.identity, 'revision': row.revision, 'activity_id': activity['id'],
               'participation_id': shared.get('participation_id') if shared else None,
               'bound_kind': bound_kind, 'shared_withdrawable': shared_withdrawable, 'intent': asdict(intent)}
    return {SOURCE_KEY: _json(command)}


class CompanionChatService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions
        self.repository = CompanionRepository(sessions)

    def matches(self, message_id: int | None, message: str, conversation_id: int) -> bool:
        if message_id is None:
            return False
        with self.sessions() as session:
            return self._source(session, message_id, message, conversation_id) is not None

    @staticmethod
    def _source(session: Session, message_id: int, message: str, conversation_id: int) -> tuple[PilotTurnRecord, dict[str, Any]] | None:
        source = session.get(ChatMessage, message_id)
        if source is None or source.role != 'user' or source.content != message or source.conversation_id != conversation_id:
            return None
        turn = session.scalar(select(PilotTurnRecord).where(PilotTurnRecord.user_message_id == message_id,
                                                          PilotTurnRecord.conversation_id == conversation_id))
        if turn is None:
            return None
        raw = json.loads(turn.source_versions_json).get(SOURCE_KEY)
        if raw is None:
            return None
        frozen = json.loads(raw)
        intent = parse_chat_intent(message, shared_kind=frozen['bound_kind'],
                                   shared_withdrawable=frozen.get('shared_withdrawable', False))
        if intent is None or asdict(intent) != frozen['intent']:
            raise CompanionConflict('chat_source_conflict')
        return turn, frozen

    def execute(self, message_id: int, message: str, conversation_id: int, *, now: float | None = None) -> str:
        now = time.time() if now is None else now
        with self.sessions() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            source = self._source(session, message_id, message, conversation_id)
            if source is None:
                raise CompanionConflict('chat_source_missing')
            turn, frozen = source
            # This capability exists only inside the already-admitted Pilot
            # execution owner. The engine's before-commit fence checks it again.
            scope = current_execution_scope()
            if (scope is None or scope.lease is None or scope.lease.turn_id != turn.id
                    or scope.lease.conversation_id != conversation_id):
                raise CompanionConflict('chat_execution_owner_missing')
            require_execution_before_handler(session)
            row = CompanionRepository._state(session)
            command = CompanionCommand(identity=UUID(frozen['identity']), expected_revision=frozen['revision'],
                command_id=uuid5(NAMESPACE_URL, 'offerpilot:companion-chat:' + turn.id))
            digest, receipt = CompanionRepository._check(session, row, command, 'chat:' + _json(frozen))
            if receipt is not None:
                return str(receipt['message'])
            conversation = session.get(Conversation, conversation_id)
            if (conversation is None or conversation.archived_at is not None or conversation.context_type != 'workspace'
                    or conversation.mode != 'general' or conversation.pending_tool_call_id):
                raise CompanionConflict('chat_scope_conflict')
            if not CompanionPolicy.model_validate_json(row.settings_json).enabled:
                raise CompanionConflict('companion_disabled')
            life = json.loads(row.life_json)
            activity = life.get('activity')
            if not activity or activity['id'] != frozen['activity_id']:
                raise CompanionConflict('activity_identity_conflict')
            intent = ChatIntent(**frozen['intent'])
            response, part_id = self._apply(session, row, life, intent, frozen, now, conversation_id)
            event_id = row.latest_event_id
            event = session.get(CompanionEvent, event_id)
            if event is not None:
                event.source = 'user_chat'
            session.add(CompanionChatEvidence(command_id=str(command.command_id), identity=row.identity,
                activity_id=activity['id'], participation_id=part_id, event_id=event_id,
                conversation_id=conversation_id, message_id=message_id, turn_id=turn.id,
                action=intent.action, step_kind=intent.step_kind, value=intent.value))
            session.add(CompanionCommandReceipt(command_id=str(command.command_id), identity=row.identity,
                resulting_identity=row.identity, request_digest=digest, response_json=_json({'message': response})))
            usage = session.get(CompanionUsageSession, 1)
            if usage is not None:
                usage.unanswered = False
            session.commit()
            return response

    def _apply(self, session: Session, row: CompanionState, life: dict[str, Any], intent: ChatIntent,
               frozen: dict[str, Any], now: float, conversation_id: int) -> tuple[str, str | None]:
        activity = life['activity']
        repo = self.repository
        if intent.action == 'withdraw_shared':
            shared = activity.get('shared_step')
            if (not shared or shared.get('conversation_id') != conversation_id
                    or shared.get('participation_id') != frozen['participation_id']):
                raise CompanionConflict('shared_step_conflict')
            part = session.get(CompanionParticipation, shared['participation_id'])
            if (part is None or part.origin != 'chat' or part.identity != row.identity
                    or part.activity_id != activity['id'] or part.status != 'promised'):
                raise CompanionConflict('participation_missing')
            command = CompanionEventCommand(identity=UUID(row.identity), expected_revision=row.revision,
                command_id=uuid4(), activity_id=UUID(activity['id']), event='withdraw_shared')
            repo._interact(session, row, life, command, now)
            response = ('好，这一步不再等你参与，也不会记成一起完成。'
                        + ('日常仍停在这里，等你恢复后我再自己慢慢做。' if activity['status'] == 'paused'
                           else '剩下的我自己慢慢做。'))
            return response, part.id
        if intent.action in ('pause', 'resume'):
            command = CompanionEventCommand(identity=UUID(row.identity), expected_revision=row.revision,
                command_id=uuid4(), activity_id=UUID(activity['id']), event='pause' if intent.action == 'pause' else 'resume')
            repo._interact(session, row, life, command, now)
            response = ('好，这件小事先停在这里，不会悄悄往下做。' if intent.action == 'pause' else
                        '继续啦。' + (activity['promise'] + '，不会替你完成。' if activity['status'] == 'waiting_shared' else '从原来的这一步慢慢来。'))
            return response, None
        if intent.action in ('join', 'wait'):
            if activity['status'] not in ('active', 'waiting_shared'):
                raise CompanionConflict('activity_not_joinable')
            kind = intent.step_kind or ('star' if activity['template_id'] == 'paper_stars' else 'name')
            promise = _STEP_PROMISES[kind]
            part = session.scalar(select(CompanionParticipation).where(CompanionParticipation.identity == row.identity,
                CompanionParticipation.activity_id == activity['id'], CompanionParticipation.status == 'promised'))
            previous_shared = activity.get('shared_step')
            if part is not None and (part.origin != 'chat' or not previous_shared
                                     or previous_shared.get('conversation_id') != conversation_id):
                part.status = 'abandoned'
                part = None
            if part is None:
                part = CompanionParticipation(id=str(uuid4()), identity=row.identity, activity_id=activity['id'],
                    status='promised', promise=promise, origin='chat', created_at=now, completed_at=None)
                session.add(part)
            else:
                part.promise = promise
            activity.update(status='waiting_shared', promise=promise, participants=['companion', 'user'], updated_at=now,
                shared_step={'kind': kind, 'participation_id': part.id, 'conversation_id': conversation_id})
            life['wish'] = None
            CompanionRepository._event(session, row, life, now, 'shared_joined', '用户明确约定了一起做的一步，尚未完成', actual=True)
            return (('好，' + promise + '。你回来前就停在这里，不着急。') if intent.action == 'wait'
                    else '一起呀。' + _STEP_QUESTIONS[kind]), part.id
        if intent.action == 'answer':
            shared = activity.get('shared_step')
            if (activity['status'] != 'waiting_shared' or not shared or shared['conversation_id'] != conversation_id
                    or shared['participation_id'] != frozen['participation_id'] or shared['kind'] != intent.step_kind):
                raise CompanionConflict('shared_step_conflict')
            part = session.get(CompanionParticipation, shared['participation_id'])
            if part is None or part.identity != row.identity or part.activity_id != activity['id'] or part.status != 'promised':
                raise CompanionConflict('participation_missing')
            part.status, part.completed_at = 'completed', now
            activity.update(status='active', promise=None, updated_at=now)
            activity.pop('shared_step', None)
            CompanionRepository._event(session, row, life, now, 'shared_completed', '用户实际回复并完成了约定的选择；不代表整项活动完成', actual=True)
            prefix = {'color': '那就选', 'name': '那就叫', 'star': '就挑'}[intent.step_kind or 'name']
            return prefix + str(intent.value) + '。这一小步是我们一起定的，剩下的我慢慢做。', part.id
        raise CompanionConflict('unsupported_chat_action')


def erase_chat_evidence(session: Session, conversation_id: int) -> None:
    """Deleting chat removes its derived life evidence, not just a dangling URL."""
    records = session.scalars(select(CompanionChatEvidence).where(CompanionChatEvidence.conversation_id == conversation_id)).all()
    if not records:
        return
    part_ids = {row.participation_id for row in records if row.participation_id}
    event_ids = {row.event_id for row in records if row.event_id}
    # A resume in another conversation can quote this promise in its receipt.
    # Its frozen participation is a dependency, not ownership: deleting that
    # other conversation must never delete the original participant's source.
    dependent_receipts = set()
    if part_ids:
        for evidence, turn in session.execute(select(CompanionChatEvidence, PilotTurnRecord).join(
            PilotTurnRecord, PilotTurnRecord.id == CompanionChatEvidence.turn_id,
        ).where(CompanionChatEvidence.action == 'resume',
                CompanionChatEvidence.conversation_id != conversation_id)):
            frozen = json.loads(json.loads(turn.source_versions_json).get(SOURCE_KEY, '{}'))
            if frozen.get('participation_id') in part_ids:
                dependent_receipts.add(evidence.command_id)
    command_ids = {row.command_id for row in records}
    session.execute(delete(CompanionChatEvidence).where(CompanionChatEvidence.conversation_id == conversation_id))
    session.execute(delete(CompanionCommandReceipt).where(CompanionCommandReceipt.command_id.in_(command_ids)))
    session.execute(delete(CompanionEvent).where(CompanionEvent.id.in_(event_ids)))
    session.execute(delete(CompanionParticipation).where(CompanionParticipation.id.in_(part_ids), CompanionParticipation.origin == 'chat'))
    state = session.get(CompanionState, 1)
    if state is not None:
        life = json.loads(state.life_json)
        activity = life.get('activity')
        shared = activity.get('shared_step') if activity else None
        if shared and shared.get('conversation_id') == conversation_id:
            activity.pop('shared_step', None)
            activity['promise'] = None
            activity['participants'] = ['companion']
            if activity['status'] == 'waiting_shared':
                activity['status'] = 'paused'
                activity['paused_from'] = 'active'
            elif activity.get('paused_from') == 'waiting_shared':
                activity['paused_from'] = 'active'
        if activity and not session.scalar(select(CompanionParticipation.id).where(
                CompanionParticipation.identity == state.identity,
                CompanionParticipation.activity_id == activity['id'],
                CompanionParticipation.status.in_(('promised', 'completed'))).limit(1)):
            activity['participants'] = ['companion']
        state.life_json = _json(life)
        state.revision += 1
        if state.latest_event_id in event_ids:
            state.latest_event_id = None

        # UI command receipts may embed a historical life snapshot. Retain
        # their identity/digest (never execute again), but privacy deletion must
        # also erase those copies of the removed shared evidence and promises.
        session.flush()
        sanitized = companion_snapshot(session, time.time())
        for receipt in session.scalars(select(CompanionCommandReceipt)):
            payload = json.loads(receipt.response_json)
            if 'state' in payload:
                receipt.response_json = _json({**sanitized, 'changed': False, 'redacted': True,
                    'receipt': {'command_id': receipt.command_id, 'revision': state.revision}})
            elif receipt.command_id in dependent_receipts:
                receipt.response_json = _json({'message': '这条历史日常操作的来源已变化，请查看当前状态。',
                                               'redacted': True})
