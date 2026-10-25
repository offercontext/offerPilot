"""Bounded read-only recall across life steps, with live actual-source checks."""
from copy import deepcopy
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from offerpilot.companion.context import (
    COMPANION_CONTEXT_BYTE_CAP, companion_context_message, read_companion_context,
)
from offerpilot.companion.contracts import CompanionResetCommand
from offerpilot.companion.models import CompanionChatEvidence
from offerpilot.context_projector.contracts import FrozenMessage, canonical_json
from offerpilot.models import ChatMessage
from offerpilot.repositories.chat import ChatRepository
from tests.test_companion_chat import command, life as life, run


def read(life, query='我们约好一起选什么颜色？', conversation_id=None):
    with life[0]() as session:
        return read_companion_context(session.connection().connection.driver_connection,
            query, context_type='workspace', conversation_id=conversation_id)


def choose(life, value='蓝色'):
    kind = '名字' if value.startswith('叫') else '颜色'
    with run(life, f'等我回来一起选{kind}' if kind == '颜色' else '等我回来一起起个名字') as (first, execute):
        execute()
    with run(life, value, first.conversation_id) as (answer, execute):
        execute()
    return answer


def next_activity(life):
    repo = life[1]
    now = repo.read()['state']['activity']['updated_at']
    for delta in (300, 600, 900, 2700):
        repo.apply_event(command(repo, 'app_open'), now=now + delta)


def test_real_choice_remains_sourced_after_current_activity_changes(life):
    answer = choose(life)
    before = read(life)
    original_id = before['virtual_activity']['id']
    assert before['recent_shared_choice']['activity_relation'] == 'current_activity'
    next_activity(life)
    snapshot = life[1].read()
    after = read(life)
    choice = after['recent_shared_choice']
    assert after['virtual_activity']['id'] != original_id
    assert after['actual_user_participation'] is None
    assert choice['activity_id'] == original_id
    assert choice['activity_relation'] == 'prior_activity'
    assert choice['value'] == '蓝色'
    assert choice['scope'] == 'shared_step_only'
    assert choice['source_message_id'] == answer.user_message_id
    assert choice['source_conversation_id'] == answer.conversation_id
    assert choice['source_turn_id'] == answer.turn_id
    assert 'title' not in choice and 'preference' not in json.dumps(after)
    assert companion_context_message(after) is not None
    assert life[1].read()['revision'] == snapshot['revision']  # Reading never progresses life.


def test_deleted_past_choice_and_reset_have_no_recall(life):
    answer = choose(life)
    next_activity(life)
    ChatRepository(life[0]).delete_conversation(answer.conversation_id)
    assert 'recent_shared_choice' not in read(life)
    snapshot = life[1].read()
    life[1].reset(CompanionResetCommand(identity=snapshot['identity'], expected_revision=snapshot['revision'],
        command_id=uuid4(), confirmed=True))
    assert 'recent_shared_choice' not in read(life)


@pytest.mark.parametrize('field', ['identity', 'activity_id', 'conversation_id', 'turn_id', 'message_role'])
def test_mismatched_source_chain_is_not_an_actual_choice(life, field):
    answer = choose(life)
    with run(life, '你好') as (other, _):
        pass
    with life[0]() as session:
        evidence = session.scalar(select(CompanionChatEvidence).where(CompanionChatEvidence.action == 'answer'))
        if field == 'message_role':
            session.get(ChatMessage, answer.user_message_id).role = 'assistant'
        else:
            setattr(evidence, field, {
                'identity': str(uuid4()), 'activity_id': str(uuid4()),
                'conversation_id': other.conversation_id, 'turn_id': other.turn_id,
            }[field])
        session.commit()
    context = read(life)
    assert 'recent_shared_choice' not in context
    actual = context['actual_user_participation']
    assert actual is None


def test_vague_promise_reference_requires_recent_actual_same_conversation_step(life):
    assert read(life, '我们的约定呢？') is None
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    assert read(life, '我们的约定呢？', first.conversation_id)['actual_user_participation']['status'] == 'promised'
    with run(life, '你好') as (other, _):
        pass
    assert read(life, '我们的约定呢？', other.conversation_id) is None
    with run(life, '先帮我改简历', first.conversation_id):
        pass
    assert read(life, '我们的约定呢？', first.conversation_id) is None
    assert read(life, '我们的约定呢？先帮我改简历', first.conversation_id) is None
    assert read(life)['actual_user_participation']['status'] == 'promised'


def test_admitted_promise_question_can_read_but_cannot_rearm_short_reply(life):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    with run(life, '我们的约定呢？', first.conversation_id):
        context = read(life, '我们的约定呢？', first.conversation_id)
        assert context['actual_user_participation']['promise'] == '等你一起选颜色'
    with run(life, '蓝色', first.conversation_id) as (answer, _):
        assert not life[2].matches(answer.user_message_id, '蓝色', answer.conversation_id)
    assert life[1].read()['state']['activity']['status'] == 'waiting_shared'


def test_optional_recall_yields_to_current_state_without_mutating_source(life, monkeypatch):
    choose(life)
    data = read(life)
    from offerpilot.companion import context

    before = deepcopy(data)
    full = companion_context_message(data)
    without_history = {key: value for key, value in data.items() if key != 'recent_shared_choice'}
    current = companion_context_message(without_history)
    size = len(canonical_json(FrozenMessage.freeze(current).canonical_value())) + 1
    monkeypatch.setattr(context, 'COMPANION_CONTEXT_BYTE_CAP', size)
    message = companion_context_message(data)
    assert message is not None
    assert message.content == current.content
    assert message.surface_revision != current.surface_revision
    assert message.surface_revision == full.surface_revision
    assert '这次活动完成了选颜色这一步' in message.content
    assert data == before
    assert len(canonical_json(FrozenMessage.freeze(message).canonical_value())) < COMPANION_CONTEXT_BYTE_CAP


def test_maximum_actual_name_keeps_current_promise_envelope_bounded(life):
    choose(life, '叫' + '棉' * 24 + '吧')
    data = read(life)
    assert data['recent_shared_choice']['value'] == '棉' * 24
    message = companion_context_message(data)
    assert message is not None
    assert '棉' * 24 in message.content
    assert len(canonical_json(FrozenMessage.freeze(message).canonical_value())) < COMPANION_CONTEXT_BYTE_CAP


def test_real_source_loader_recalls_without_consuming_a_vague_color_answer(tmp_path):
    from fastapi.testclient import TestClient
    from offerpilot.ai.types import Assistant
    from offerpilot.api import create_app
    from offerpilot.companion.contracts import CompanionEventCommand

    class RecordingModel:
        def __init__(self):
            self.requests = []

        def complete(self, messages, tools, response_format=None):
            self.requests.append(messages)
            return Assistant(content='我会按保存的来源回答。')

    model = RecordingModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        repo = client.app.state.companion_repository
        snapshot = repo.read()
        repo.apply_event(CompanionEventCommand(identity=snapshot['identity'], expected_revision=snapshot['revision'],
            command_id=uuid4(), event='app_open'))
        joined = client.post('/api/chat', json={'request_id': str(uuid4()), 'message': '等我回来一起选颜色'}).json()
        conversation_id = joined['conversation_id']
        assert model.requests == []
        asked = client.post('/api/chat', json={'request_id': str(uuid4()), 'conversation_id': conversation_id,
            'message': '我们的约定呢？'})
        assert asked.status_code == 200, asked.text
        envelopes = [message for message in model.requests[-1]
            if '以下是带来源的角色日常参考数据' in (message.content or '')]
        assert len(envelopes) == 1
        assert '已在应用内约好一起选颜色' in envelopes[0].content
        before = repo.read()['revision']
        ambiguous = client.post('/api/chat', json={'request_id': str(uuid4()), 'conversation_id': conversation_id,
            'message': '蓝色'})
        assert ambiguous.status_code == 200, ambiguous.text
        assert len(model.requests) == 2
        assert repo.read()['revision'] == before
        mixed = client.post('/api/chat', json={'request_id': str(uuid4()), 'conversation_id': conversation_id,
            'message': '我们约好一起选什么颜色？先帮我整理简历'})
        assert mixed.status_code == 200, mixed.text
        assert all('以下是带来源的角色日常参考数据' not in (message.content or '')
                   for message in model.requests[-1])
        assert repo.read()['state']['activity']['status'] == 'waiting_shared'


def test_delete_source_redacts_dependent_resume_receipt_but_never_reexecutes(life):
    from offerpilot.companion.models import CompanionCommandReceipt
    with run(life, '等我回来一起选颜色') as (source, execute):
        execute()
    with run(life, '暂停你的折纸活动') as (other, execute):
        execute()
    with run(life, '继续折纸吧', other.conversation_id) as (resumed, execute):
        assert '等你一起选颜色' in execute()
        ChatRepository(life[0]).delete_conversation(source.conversation_id)
        revision = life[1].read()['revision']
        assert execute() == '这条历史日常操作的来源已变化，请查看当前状态。'
        assert life[1].read()['revision'] == revision
    with life[0]() as session:
        evidence = session.scalar(select(CompanionChatEvidence).where(CompanionChatEvidence.message_id == resumed.user_message_id))
        receipt = session.get(CompanionCommandReceipt, evidence.command_id)
        assert json.loads(receipt.response_json)['redacted'] is True
        assert '选颜色' not in receipt.response_json
    assert life[1].read()['state']['activity']['promise'] is None
    assert life[1].read()['state']['activity']['status'] == 'paused'
    assert read(life)['actual_user_participation'] is None


def test_delete_dependent_resume_conversation_preserves_original_wait_source(life):
    with run(life, '等我回来一起选颜色') as (source, execute):
        execute()
    with run(life, '暂停你的折纸活动') as (other, execute):
        execute()
    with run(life, '继续折纸吧', other.conversation_id) as (_, execute):
        execute()
    ChatRepository(life[0]).delete_conversation(other.conversation_id)
    activity = life[1].read()['state']['activity']
    assert activity['status'] == 'waiting_shared'
    assert activity['promise'] == '等你一起选颜色'
    assert activity['shared_step']['conversation_id'] == source.conversation_id
    assert read(life)['actual_user_participation']['status'] == 'promised'
    with run(life, '蓝色', source.conversation_id) as (_, execute):
        assert '选蓝色' in execute()


def test_control_confirmation_of_chat_wait_is_not_a_chat_reply(life):
    with run(life, '等我回来一起选颜色') as (_, execute):
        execute()
    life[1].apply_event(command(life[1], 'complete_shared'))
    context = read(life)
    actual = context['actual_user_participation']
    assert actual['status'] == 'completed'
    assert actual['source'] == 'companion_participation:user_command'
    assert actual['source_event_id']
    assert 'user_reply' not in actual
    assert 'recent_shared_choice' not in context
