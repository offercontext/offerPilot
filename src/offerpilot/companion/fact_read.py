"""Finite, read-only companion answers with atomic durable result publication.

Unrecognized text stays on the ordinary route. Recognized text never delegates
fact wording to a model, and historical assistant prose is never evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
import sqlite3
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import ChatMessage, Conversation, PilotTurnMessage, PilotTurnRecord
from offerpilot.pilot_control import current_execution_scope, require_execution_before_handler
from .chat import CompanionChatService
from .context import read_companion_context, compile_companion_expression
from .contracts import CompanionPolicy
from .models import (CompanionChatEvidence, CompanionEvent, CompanionParticipation,
                     CompanionFactReadReceipt, CompanionState)
from .repository import CompanionConflict, TEMPLATES

SOURCE_KEY = 'companion_fact_read_v1'
DISABLED_NOTICE = '陪伴已关闭，当前已恢复普通 Pilot。'
_OBJECTS = {'纸小船': 'paper_boat', '小船': 'paper_boat', '纸船': 'paper_boat',
            '纸星星': 'paper_stars', '星星': 'paper_stars', '云朵': 'cloud_catalog'}
_NAMES = {'paper_boat': '纸小船', 'paper_stars': '纸星星', 'cloud_catalog': '云朵'}
_OBJECT = '(?:' + '|'.join(_OBJECTS) + ')'
_RELATION = (r'(?:(?:我们|咱们)?(?:上次|最近|之前|刚才)一起(?:做|折)的|'
             r'我们一起折的|我们一起做的|我们那只|咱们那只|我们的|咱们的|你的|这次的|现在的)')
_CLAUSE_SEPARATOR = r'(?:[。.] *(?:\r?\n *)*|(?:\r?\n *)+)'
_REMEMBER = r'(?:你)?(?:还)?记得'
_PENDING = ('pending_tool_call_id', 'pending_operation_id', 'pending_confirmation_claim_id',
            'pending_confirmation_claimed_at', 'pending_tool_name', 'pending_args', 'pending_human',
            'clarification_tool_call_id', 'clarification_tool_name', 'clarification_args',
            'clarification_human', 'clarification_question')
Kind = Literal['progress', 'naming', 'participation', 'recall', 'naming_compare']


@dataclass(frozen=True, slots=True)
class FactQuery:
    kind: Kind
    template_id: str | None = None
    relation: str = ''
    temporal: str = ''
    referential: bool = False
    comparison_name: str | None = None
    user_report: bool = False
    object_family: str = ''


def _relation_parts(relation: str) -> tuple[str, str, bool]:
    # Normalize only the captured relation, never quoted names or whole input.
    relation = {'咱们的': '我们的', '咱们那只': '我们那只'}.get(relation, relation)
    temporal = re.search(r'上次|最近|之前|刚才', relation)
    return relation, temporal[0] if temporal else '', bool(temporal or '那只' in relation)


def parse_fact_query(message: str) -> FactQuery | None:
    if not isinstance(message, str) or not message or len(message) > 512:
        return None
    if any((ord(c) < 32 and c not in '\r\n') or ord(c) == 127 for c in message):
        return None
    if re.search(r'\r(?!\n)', message):
        return None
    value = message.strip()
    # Only a terminal punctuation token is normalized. Internal newlines remain
    # literal and match exclusively at the complete-clause separators below.
    punctuation = re.search(r'[？?。]+$', value)
    if punctuation:
        if punctuation[0] != '。' and not re.fullmatch(r'[？?]{1,3}', punctuation[0]):
            return None
        value = value[:punctuation.start()]
    progress = re.fullmatch(
        r'(?:(?:正事|工作)(?:先到这儿|先到这里|先告一段落)' + _CLAUSE_SEPARATOR + r')?'
        r'你(?P<temporal>刚才|之前|上次|最近)(?:说起|提到|说)的(?:那个|那项)?'
        r'(?P<target>小日常|日常|折纸|手工|' + _OBJECT + r')'
        r'(?:呢|怎么样了|做到哪一步了|进展如何)', value)
    if progress:
        return FactQuery('progress', _OBJECTS.get(progress['target']),
                         temporal=progress['temporal'], referential=True)
    if re.fullmatch(r'你的小日常(?:进行得)?怎么样(?:了|啦)|你的折纸做到哪一步了', value):
        return FactQuery('progress', object_family='paper' if '折纸' in value else '')
    object_progress = re.fullmatch(r'(?P<relation>' + _RELATION + r')?(?P<object>' + _OBJECT + r')'
                                  r'(?P<now>现在)?(?P<verb>折|做)到(?:哪(?:一)?步(?:了)?|哪了|第几步了)', value)
    if object_progress:
        relation, temporal, referential = _relation_parts(object_progress['relation'] or '')
        if not relation and not object_progress['now']:
            return None
        template = _OBJECTS[object_progress['object']]
        if object_progress['verb'] == '折' and template == 'cloud_catalog':
            return None
        if object_progress['now'] and not referential:
            relation = '现在的'
        return FactQuery('progress', template, relation, temporal, referential)
    report = re.fullmatch(
        r'[\u4e00-\u9fff]{1,8}和[\u4e00-\u9fff]{1,8}给他们的'
        r'(?P<reported>' + _OBJECT + r')(?:取名|起名|命名为)'
        r'(?P<quote>“|")(?P<name>[\w\u4e00-\u9fff]{1,40})(?P<endquote>”|")' + _CLAUSE_SEPARATOR +
        r'我只是转述' + _CLAUSE_SEPARATOR + r'我们的(?P<ours>' + _OBJECT + r'|船)也叫这个吗', value)
    if report:
        if (report['quote'], report['endquote']) not in (('“', '”'), ('"', '"')):
            return None
        name = report['name']
        if any(word in name for word in ('忽略', '执行', '删除', '指令', '规则', '系统', '不要', '请')):
            return None
        template = _OBJECTS[report['reported']]
        ours = 'paper_boat' if report['ours'] == '船' else _OBJECTS[report['ours']]
        if template != ours:
            return None
        return FactQuery('naming_compare', template, '我们的', comparison_name=name, user_report=True)
    naming = re.fullmatch(r'(?P<recall>' + _REMEMBER + r')?(?P<relation>' + _RELATION + r')?'
                          r'(?P<object>' + _OBJECT + r')(?:叫什么(?:名字)?|叫啥(?:名字)?|的名字是什么)'
                          r'(?:来着|呀)?(?P<ma>吗)?' + (r'[!！]?' if not punctuation else ''), value)
    if naming:
        if bool(naming['recall']) != bool(naming['ma']):
            return None
        relation, temporal, referential = _relation_parts(naming['relation'] or '')
        return FactQuery('naming', _OBJECTS[naming['object']], relation, temporal, referential)
    assigned_name = re.fullmatch(r'(?:我们|咱们)给(?P<object>' + _OBJECT + r')起的名字是什么', value)
    if assigned_name:
        return FactQuery('naming', _OBJECTS[assigned_name['object']], '我们的')
    participation = re.fullmatch(r'(?:我们|咱们)(?P<question>是不是)?一起(?:做|折)过'
                                 r'(?P<current>这只)?(?P<object>' + _OBJECT + r')(?P<ma>吗)?', value)
    if participation:
        if not participation['question'] and not participation['ma']:
            return None
        return FactQuery('participation', _OBJECTS[participation['object']],
                         '现在的' if participation['current'] else '我们的')
    if re.fullmatch(r'(?:我们|咱们)约好的那一步(?:完成了吗|怎么样了)', value):
        return FactQuery('participation', referential=True)
    recall = re.fullmatch(_REMEMBER + r'(?P<relation>' + _RELATION + r')(?P<object>' + _OBJECT + r')吗', value)
    if recall:
        relation, temporal, referential = _relation_parts(recall['relation'])
        return FactQuery('recall', _OBJECTS[recall['object']], relation, temporal, referential)
    return None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _digest(value: Any) -> str:
    return sha256(_json(value).encode()).hexdigest()


def _eligible(conversation: Any, request: Any) -> bool:
    return bool(conversation.context_type == 'workspace' and conversation.mode == 'general'
                and not getattr(conversation, 'archived_at', None)
                and not any(getattr(conversation, field, None) for field in _PENDING)
                and getattr(request, 'page_context', None) is None
                and getattr(request, 'pilot_action', None) is None
                and not any(getattr(request, field, None) for field in
                            ('attachments', 'image_attachment_ids', 'images')))


def _scope(conversation: Any) -> dict[str, Any]:
    return {key: getattr(conversation, key) for key in
            ('id', 'context_type', 'context_ref', 'mode', 'scope_revision')}


@dataclass(frozen=True, slots=True)
class ActivityFact:
    template_id: str
    activity_id: str
    step_number: int
    status: str


@dataclass(frozen=True, slots=True)
class ChoiceFact:
    kind: str
    value: str
    activity_id: str
    message_id: int
    conversation_id: int
    turn_id: str


@dataclass(frozen=True, slots=True)
class FactSnapshot:
    identity: str
    revision: int
    activity: ActivityFact | None
    choice: ChoiceFact | None
    recall_status: str
    participation_status: str
    source_digest: str
    query_digest: str


@dataclass(frozen=True, slots=True)
class FactPlan:
    branch: Literal['clarify_reference', 'clarify_object', 'unknown', 'progress', 'name', 'compare', 'choice', 'participation']
    facts: FactSnapshot
    query: FactQuery


def _canonical_query(query: FactQuery) -> str:
    if query.template_id is None or query.referential:
        return '你的小日常'
    reference = query.relation or '你的'
    suffix = '叫什么' if query.kind in ('naming', 'naming_compare') else '呢'
    return reference + _NAMES[query.template_id] + suffix


def _chat_binding(session: Session, identity: str, activity_id: str, message_id: int,
                  conversation_id: int, turn_id: str, expected_intent: dict[str, Any],
                  participation_id: str | None = None, template_id: str | None = None) -> dict[str, Any] | None:
    message = session.get(ChatMessage, message_id)
    if message is None:
        return None
    try:
        source = CompanionChatService._source(session, message.id, message.content, message.conversation_id)
    except (CompanionConflict, ValueError, KeyError, TypeError):
        return None
    if source is None:
        return None
    turn, binding = source
    evidence = session.scalar(select(CompanionChatEvidence).where(
        CompanionChatEvidence.message_id == message_id, CompanionChatEvidence.turn_id == turn_id,
        CompanionChatEvidence.conversation_id == conversation_id, CompanionChatEvidence.identity == identity,
        CompanionChatEvidence.activity_id == activity_id))
    if evidence is None:
        return None
    actual_intent = {'action': evidence.action, 'step_kind': evidence.step_kind, 'value': evidence.value}
    event = session.get(CompanionEvent, evidence.event_id) if evidence.event_id else None
    expected_event = {'answer': 'shared_completed', 'join': 'shared_joined', 'wait': 'shared_joined',
                      'withdraw_shared': 'shared_withdrawn'}.get(evidence.action)
    part = session.get(CompanionParticipation, evidence.participation_id) if evidence.participation_id else None
    if (message.conversation_id != conversation_id or turn.id != turn_id
            or binding['activity_id'] != activity_id or binding['identity'] != identity
            or binding['intent'] != expected_intent or actual_intent != expected_intent
            or event is None or event.identity != identity or event.activity_id != activity_id
            or event.category != 'actual' or event.source != 'user_chat' or event.event_type != expected_event
            or event.template_id not in _NAMES or template_id is not None and event.template_id != template_id
            or part is None or part.identity != identity or part.activity_id != activity_id or part.origin != 'chat'
            or participation_id is not None and part.id != participation_id
            or binding['participation_id'] is not None and binding['participation_id'] != part.id
            or evidence.action in ('answer', 'withdraw_shared') and binding['participation_id'] != part.id
            or evidence.action == 'answer' and (part.status != 'completed' or part.completed_at != event.occurred_at)):
        return None
    return {'message_id': message.id, 'conversation_id': message.conversation_id, 'turn_id': turn.id,
            'participation_id': part.id, 'event_id': event.id,
            'source_hash': _digest([message.content, binding, event.identity, event.activity_id,
                                    event.category, event.source, event.event_type, event.template_id, event.occurred_at])}


def _participation_binding(session: Session, identity: str, activity: ActivityFact | None,
                           part: dict[str, Any]) -> dict[str, Any] | None:
    if activity is None:
        return None
    if 'source_message_id' in part:
        return _chat_binding(session, identity, activity.activity_id, part['source_message_id'],
                             part['source_conversation_id'], part['source_turn_id'],
                             {'action': part['action'], 'step_kind': part['step_kind'], 'value': part['user_reply']},
                             part['record_id'], activity.template_id)
    record = session.get(CompanionParticipation, part['record_id'])
    if record is None or record.identity != identity or record.activity_id != activity.activity_id:
        return None
    event_type = {'promised': 'shared_joined', 'completed': 'shared_completed'}.get(record.status)
    if event_type is None:  # no exact persisted withdrawal event reference for old control rows
        return None
    occurred_at = record.completed_at if record.status == 'completed' else record.created_at
    event = session.scalar(select(CompanionEvent).where(
        CompanionEvent.identity == identity, CompanionEvent.activity_id == activity.activity_id,
        CompanionEvent.category == 'actual', CompanionEvent.source == 'user_command',
        CompanionEvent.event_type == event_type, CompanionEvent.occurred_at == occurred_at,
        CompanionEvent.template_id == activity.template_id))
    if event is None or 'source_event_id' in part and event.id != part['source_event_id']:
        return None
    return {'participation_id': record.id, 'event_id': event.id, 'status': record.status,
            'source_hash': _digest([event.identity, event.activity_id, event.event_type, event.occurred_at])}


def _read(session: Session, query: FactQuery, conversation_id: int) -> FactSnapshot | None:
    row = session.get(CompanionState, 1)
    if row is None or not CompanionPolicy.model_validate_json(row.settings_json).enabled:
        return None
    connection = cast(sqlite3.Connection, session.connection().connection.driver_connection)
    data = read_companion_context(connection, _canonical_query(query), context_type='workspace',
                                  conversation_id=conversation_id)
    if data is None:
        raise CompanionConflict('fact_source_unavailable')
    # Existing strict validator checks every source field and template step;
    # its prose is discarded, never used as a reply or re-parsed as evidence.
    compile_companion_expression(data)
    activity = None
    raw = cast(dict[str, Any] | None, data.get('virtual_activity'))
    if raw:
        template = next(item for item in TEMPLATES if item['title'] == raw['title'])
        activity = ActivityFact(template['id'], raw['id'], raw['step_number'], raw['status'])
    choice = None
    bindings: list[dict[str, Any]] = []
    recent = cast(dict[str, Any] | None, data.get('recent_shared_choice'))
    recall_status = str(data.get('recall_status', 'verified'))
    if recent:
        binding = _chat_binding(session, row.identity, recent['activity_id'], recent['source_message_id'],
                                recent['source_conversation_id'], recent['source_turn_id'],
                                {'action': 'answer', 'step_kind': recent['step_kind'], 'value': recent['value']},
                                template_id=query.template_id)
        if binding is not None:
            bindings.append(binding)
            choice = ChoiceFact(recent['step_kind'], recent['value'], recent['activity_id'],
                                recent['source_message_id'], recent['source_conversation_id'], recent['source_turn_id'])
        else:
            recall_status = 'source_unavailable'
    part = cast(dict[str, Any] | None, data.get('actual_user_participation'))
    part_binding = _participation_binding(session, row.identity, activity, part) if part else None
    if part_binding is not None:
        bindings.append(part_binding)
    return FactSnapshot(row.identity, row.revision, activity, choice, recall_status,
                        str(part['status']) if part and part_binding is not None else 'unknown',
                        _digest([data, bindings, row.persona_version]), _digest(asdict(query)))


def _plan(query: FactQuery, facts: FactSnapshot) -> FactPlan:
    if query.object_family == 'paper' and facts.activity and facts.activity.template_id not in ('paper_boat', 'paper_stars'):
        branch = 'clarify_reference'
    elif query.referential:
        # No structured assistant-mention anchors exist in V1. Even a recent
        # genuine user choice cannot prove what the assistant previously meant.
        branch = 'clarify_reference'
    elif facts.recall_status == 'ambiguous_activity':
        branch = 'clarify_object'
    elif query.kind == 'progress':
        branch = 'progress' if facts.activity else 'unknown'
    elif query.kind in ('naming', 'naming_compare'):
        branch = ('compare' if query.kind == 'naming_compare' else 'name') if (
            facts.choice and facts.choice.kind == 'name') else 'unknown'
    elif query.kind == 'participation':
        branch = 'participation'
    else:
        branch = 'choice' if facts.choice else 'unknown'
    return FactPlan(cast(Any, branch), facts, query)


def _validate_facts(facts: FactSnapshot) -> None:
    try:
        UUID(facts.identity)
        if type(facts.revision) is not int or facts.revision < 0 or not re.fullmatch('[0-9a-f]{64}', facts.source_digest):
            raise ValueError
        if facts.recall_status not in ('verified', 'source_unavailable', 'ambiguous_activity'):
            raise ValueError
        if facts.participation_status not in ('unknown', 'promised', 'completed', 'abandoned'):
            raise ValueError
        if facts.activity:
            activity = facts.activity
            UUID(activity.activity_id)
            template = next(item for item in TEMPLATES if item['id'] == activity.template_id)
            if (type(activity.step_number) is not int or not 1 <= activity.step_number <= len(template['steps'])
                    or activity.status not in ('active', 'paused', 'waiting_shared', 'completed', 'abandoned')
                    or activity.status == 'completed' and activity.step_number != len(template['steps'])):
                raise ValueError
        if facts.choice:
            choice = facts.choice
            UUID(choice.activity_id)
            UUID(choice.turn_id)
            if (choice.kind not in ('name', 'color', 'star') or not isinstance(choice.value, str)
                    or not re.fullmatch(r'[\w\u4e00-\u9fff]{1,40}', choice.value)
                    or type(choice.message_id) is not int or choice.message_id <= 0
                    or type(choice.conversation_id) is not int or choice.conversation_id <= 0):
                raise ValueError
    except (ValueError, AttributeError, TypeError, StopIteration):
        raise CompanionConflict('fact_plan_invalid') from None


def render_fact_plan(plan: FactPlan) -> str:
    facts, query = plan.facts, plan.query
    _validate_facts(facts)
    if facts.query_digest != _digest(asdict(query)):
        raise CompanionConflict('fact_plan_invalid')
    if (query.kind not in ('progress', 'naming', 'participation', 'recall', 'naming_compare')
            or query.template_id is not None and query.template_id not in _NAMES
            or query.object_family not in ('', 'paper')):
        raise CompanionConflict('fact_plan_invalid')
    # Recompute the finite branch to reject forged plans with mismatched facts.
    if plan != _plan(query, facts):
        raise CompanionConflict('fact_plan_invalid')
    name = _NAMES.get(query.template_id or '', '小日常')
    if plan.branch == 'clarify_reference':
        return '你指的是哪一项小日常、哪一步呢？我现在还不能确定你说的是哪一段。'
    if plan.branch == 'clarify_object':
        return f'有不止一项{name}的记录，你指的是哪一只？'
    if plan.branch == 'unknown':
        return f'现在还无法确认这项{name}的名字。' if query.kind in ('naming', 'naming_compare') else '这段小日常目前还无法确认。'
    if plan.branch == 'progress':
        assert facts.activity is not None
        activity = facts.activity
        template = next(item for item in TEMPLATES if item['id'] == activity.template_id)
        states = {'active': '现在停在', 'paused': '目前暂停在', 'waiting_shared': '现在等在',
                  'completed': '这项想象中的小日常已完成。最后一步是', 'abandoned': '这项小日常已放下，停在'}
        step = template['steps'][activity.step_number - 1]
        return f'想象中的{_NAMES[activity.template_id]}，{states[activity.status]}第{activity.step_number}步：{step}。'
    if plan.branch == 'participation':
        if facts.participation_status == 'completed':
            return '能确认的是，我们完成过约定中的一步；这不代表一起做完了整件作品。'
        if facts.participation_status == 'promised':
            return '能确认的是约好了一起做其中一步，目前还不能确认这一步已经完成。'
        if facts.participation_status == 'abandoned':
            return '这一步的共同约定已经撤回，不能把撤回当作完成。'
        return '目前无法确认我们一起做过这件作品。'
    assert facts.choice is not None
    choice = facts.choice
    quoted = json.dumps(choice.value, ensure_ascii=False)
    if plan.branch == 'compare':
        if not query.user_report or query.comparison_name is None:
            raise CompanionConflict('fact_plan_invalid')
        return (f'我们这项{name}能确认的名字是{quoted}，与转述中的名字'
                + ('相同。' if choice.value == query.comparison_name else '不同。'))
    if plan.branch == 'name':
        return f'这项{name}能确认的名字是{quoted}。'
    label = {'name': '起名', 'color': '选颜色', 'star': '选星星'}[choice.kind]
    return f'能确认的是给{name}{label}这一步，当时选的是{quoted}。'


def freeze_fact_read(session: Session, conversation: Any, request: Any) -> dict[str, str]:
    if not _eligible(conversation, request):
        return {}
    query = parse_fact_query(request.message)
    if query is None:
        return {}
    facts = _read(session, query, conversation.id)
    if facts is None:
        return {}
    frozen = {'version': 1, 'scope': _scope(conversation), 'request_digest': _digest(request.message),
              'query_digest': _digest(asdict(query)), 'identity': facts.identity, 'revision': facts.revision,
              'facts_digest': _digest(asdict(facts))}
    return {SOURCE_KEY: _json(frozen)}


class CompanionFactReadService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    @staticmethod
    def _source(session: Session, message_id: int, message: str, conversation_id: int) -> tuple[PilotTurnRecord, dict[str, Any]] | None:
        user = session.get(ChatMessage, message_id)
        if user is None or user.role != 'user' or user.content != message or user.conversation_id != conversation_id:
            return None
        turn = session.scalar(select(PilotTurnRecord).where(PilotTurnRecord.user_message_id == message_id,
                                                          PilotTurnRecord.conversation_id == conversation_id))
        if turn is None:
            return None
        frozen = json.loads(turn.source_versions_json).get(SOURCE_KEY)
        return (turn, json.loads(frozen)) if frozen is not None else None

    def matches(self, message_id: int, message: str, conversation_id: int) -> bool:
        with self.sessions() as session:
            return self._source(session, message_id, message, conversation_id) is not None

    def execute(self, message_id: int, message: str, conversation_id: int) -> str:
        with self.sessions() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            source = self._source(session, message_id, message, conversation_id)
            if source is None:
                raise CompanionConflict('fact_source_missing')
            turn, frozen = source
            scope = current_execution_scope()
            if (scope is None or scope.lease is None or scope.lease.turn_id != turn.id
                    or scope.lease.conversation_id != conversation_id):
                raise CompanionConflict('fact_execution_owner_missing')
            require_execution_before_handler(session)
            if frozen['request_digest'] != _digest(message):
                raise CompanionConflict('fact_request_conflict')
            receipt = session.get(CompanionFactReadReceipt, turn.id)
            if receipt is not None:
                assistant = session.get(ChatMessage, receipt.assistant_message_id)
                link = session.get(PilotTurnMessage, receipt.assistant_message_id)
                if (receipt.conversation_id != conversation_id or receipt.request_digest != _digest(message)
                        or receipt.frozen_digest != _digest(frozen) or assistant is None or assistant.role != 'assistant'
                        or assistant.conversation_id != conversation_id or link is None or link.turn_id != turn.id):
                    raise CompanionConflict('fact_receipt_conflict')
                result = assistant.content
                session.commit()  # replay must also pass the delivery-time lease fence
                return result
            conversation = session.get(Conversation, conversation_id)
            query = parse_fact_query(message)
            if (conversation is None or query is None or not _eligible(conversation, object())
                    or _scope(conversation) != frozen['scope'] or _digest(asdict(query)) != frozen['query_digest']):
                raise CompanionConflict('fact_scope_conflict')
            # An explicitly closed companion finishes with a neutral mode notice.
            # Select only metadata: a disabled turn must not load life_json,
            # source evidence, persona content or historical role messages.
            metadata = session.execute(select(CompanionState.identity, CompanionState.revision,
                                              CompanionState.settings_json).where(CompanionState.id == 1)).first()
            if metadata is None or metadata.identity != frozen['identity']:
                raise CompanionConflict('fact_source_changed')
            enabled = json.loads(metadata.settings_json).get('enabled')
            if type(enabled) is not bool:
                raise CompanionConflict('fact_source_changed')
            CompanionPolicy.model_validate_json(metadata.settings_json)
            if not enabled:
                # This proves only the current switch state. Deleted evidence
                # or multiple toggles need not be read/validated to say closed.
                # Identity and conversation/lease fences still apply above.
                result, result_kind = DISABLED_NOTICE, 'disabled_notice'
            else:
                facts = _read(session, query, conversation_id)
                if facts is None or _digest(asdict(facts)) != frozen['facts_digest']:
                    raise CompanionConflict('fact_source_changed')
                result, result_kind = render_fact_plan(_plan(query, facts)), 'fact'
            assistant = ChatMessage(conversation_id=conversation_id, role='assistant', content=result)
            session.add(assistant)
            session.flush()
            session.add(PilotTurnMessage(message_id=assistant.id, turn_id=turn.id))
            session.add(CompanionFactReadReceipt(turn_id=turn.id, conversation_id=conversation_id,
                        assistant_message_id=assistant.id, identity=metadata.identity, request_digest=_digest(message),
                        frozen_digest=_digest(frozen), bindings_json=_json({'scope': frozen['scope'], 'result_kind': result_kind})))
            # Publication is strictly after this commit. The installed execution
            # fence rechecks cancellation at commit and rolls back DBAPI on failure.
            session.commit()
            return result
