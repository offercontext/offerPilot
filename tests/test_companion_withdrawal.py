"""Withdrawal releases one promised step, not history or the whole activity."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from offerpilot.api import create_app
from offerpilot.companion.chat import SOURCE_KEY, freeze_chat_command, parse_chat_intent
from offerpilot.companion.contracts import CompanionPolicy, CompanionSettingsCommand
from offerpilot.companion.models import (
    CompanionChatEvidence, CompanionCommandReceipt, CompanionEvent,
    CompanionParticipation, CompanionState,
)
from offerpilot.companion.repository import CompanionConflict, MIN_STEP_SECONDS, companion_source
from offerpilot.models import Conversation
from offerpilot.pilot_control import TurnExecutionFenced
from offerpilot.repositories.chat import ChatRepository
from tests.test_companion_chat import admit, command, life as life, run
from tests.test_pilot_turn_api import CountingModel


@pytest.mark.parametrize('message', [
    '这步你自己做吧', '这一步你自己继续吧', 'Haru，这步你自己做吧',
    '我这次不参加了，你自己继续吧', '我这次不参加了，你自己继续折小船吧',
    '我这次不参加了，你自己继续做纸星星吧',
])
def test_withdrawal_grammar_accepts_only_explicit_bounded_sentences(message):
    intent = parse_chat_intent(message, shared_withdrawable=True)
    assert intent and intent.action == 'withdraw_shared'


@pytest.mark.parametrize('message', [
    '不要', '不想', '我不想参加了', '不要这步你自己做吧', '不想让你自己继续折小船',
    '不是这步你自己做吧', '没有说这步你自己做吧', '如果这步你自己做吧',
    '假设我这次不参加了，你自己继续折小船吧', '他说这步你自己做吧',
    '“这步你自己做吧”', '你说这步你自己做吧', '这步你自己做吧？',
    '能不能这步你自己做吧', '这步你自己做吧，好吗', '这步你自己做吧，然后帮我改简历',
    '我这次不参加了，你自己继续折小船吧，之后创建投递',
    '先 review PR，这步你自己做吧', '这步你自己做吧，然后告诉我天气',
    '这步你自己做吧\n帮我写代码', '我这次不参加了',
])
def test_negated_reported_quoted_hypothetical_question_and_mixed_turns_stay_chat(message):
    assert parse_chat_intent(message, shared_withdrawable=True) is None


def test_unbound_deictic_withdrawal_is_not_an_instruction():
    assert parse_chat_intent('这步你自己做吧') is None
    assert parse_chat_intent('我这次不参加了，你自己继续吧') is None


def test_chat_withdrawal_is_sourced_idempotent_and_never_completes_the_step(life):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    before = life[1].read()
    part_id = before['shared_participation'][0]['id']
    with run(life, '这步你自己做吧', first.conversation_id) as (admission, execute):
        message = execute()
        after = life[1].read()
        assert execute() == message
        assert life[1].read()['revision'] == after['revision']
    activity = after['state']['activity']
    assert activity['status'] == 'active'
    for key in ('id', 'template_id', 'step_index', 'step_title'):
        assert activity[key] == before['state']['activity'][key]
    assert activity['participants'] == ['companion']
    assert activity['promise'] is None and 'shared_step' not in activity
    assert activity['wish_offered'] is True and after['state']['wish'] is None
    assert after['shared_participation'][0]['status'] == 'abandoned'
    assert after['shared_participation'][0]['completed_at'] is None
    assert '不会记成一起完成' in message
    with life[0]() as session:
        evidence = session.scalar(select(CompanionChatEvidence).where(CompanionChatEvidence.action == 'withdraw_shared'))
        assert evidence.participation_id == part_id
        assert evidence.message_id == admission.user_message_id
        assert evidence.conversation_id == first.conversation_id
        event = session.get(CompanionEvent, evidence.event_id)
        assert (event.event_type, event.category, event.source) == ('shared_withdrawn', 'actual', 'user_chat')
        assert companion_source(session) is None  # Withdrawal is not a proactive candidate.
        assert session.scalar(select(func.count()).select_from(CompanionParticipation)) == 1


@pytest.mark.parametrize('paused', [False, True])
def test_control_withdrawal_retains_delay_pause_and_never_reinvites(life, paused):
    repo = life[1]
    joined = repo.apply_event(command(repo, 'join'))
    if paused:
        repo.apply_event(command(repo, 'pause'))
    now = joined['state']['activity']['updated_at'] + 100000
    withdrawal = command(repo, 'withdraw_shared')
    after = repo.apply_event(withdrawal, now=now)
    assert repo.apply_event(withdrawal, now=now + 999)['receipt'] == after['receipt']
    activity = after['state']['activity']
    assert activity['status'] == ('paused' if paused else 'active')
    assert activity['step_index'] == joined['state']['activity']['step_index']
    assert activity['updated_at'] == now
    assert activity['participants'] == ['companion']
    assert after['recent_events'][0]['event_type'] == 'shared_withdrawn'
    assert after['recent_events'][0]['source'] == 'user_command'
    assert repo.read(now=now + 999999)['state'] == after['state']
    assert not repo.apply_event(command(repo, 'app_open'), now=now + MIN_STEP_SECONDS - 1)['changed']
    if paused:
        assert activity['paused_from'] == 'active'
        assert not repo.apply_event(command(repo, 'app_open'), now=now + 999999)['changed']
        repo.apply_event(command(repo, 'resume'), now=now)
    progressed = repo.apply_event(command(repo, 'app_open'), now=now + MIN_STEP_SECONDS)
    assert progressed['state']['activity']['step_index'] == activity['step_index'] + 1
    assert progressed['state']['wish'] is None
    assert progressed['state']['activity']['wish_offered'] is True


def test_paused_chat_wait_withdraws_without_resuming(life):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    with run(life, '暂停你的折纸活动', first.conversation_id) as (_, execute):
        execute()
    with run(life, '这步你自己做吧', first.conversation_id) as (_, execute):
        assert '仍停在这里' in execute()
    activity = life[1].read()['state']['activity']
    assert activity['status'] == 'paused' and activity['paused_from'] == 'active'
    assert activity['promise'] is None


def test_old_completed_choice_is_retained_when_next_shared_step_is_withdrawn(life):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    with run(life, '蓝色', first.conversation_id) as (_, execute):
        execute()
    completed = life[1].read()['shared_participation'][0]
    with run(life, '等我回来一起起个名字', first.conversation_id) as (_, execute):
        execute()
    with run(life, '我这次不参加了，你自己继续折小船吧', first.conversation_id) as (_, execute):
        execute()
    parts = life[1].read()['shared_participation']
    assert next(part for part in parts if part['id'] == completed['id']) == completed
    assert sorted(part['status'] for part in parts) == ['abandoned', 'completed']
    with life[0]() as session:
        answer = session.scalar(select(CompanionChatEvidence).where(CompanionChatEvidence.action == 'answer'))
        assert answer.value == '蓝色'


@pytest.mark.parametrize('intervening', ['帮我改简历', '给我讲个笑话'])
def test_unrelated_conversation_frame_closes_deictic_withdrawal(life, intervening):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    with run(life, intervening, first.conversation_id):
        pass
    before = life[1].read()
    with run(life, '这步你自己做吧', first.conversation_id) as (admission, _):
        assert not life[2].matches(admission.user_message_id, '这步你自己做吧', first.conversation_id)
    assert life[1].read()['state'] == before['state']
    # Naming the actual object explicitly is not a stale deictic reference.
    with run(life, '我这次不参加了，你自己继续折小船吧', first.conversation_id) as (_, execute):
        execute()
    assert life[1].read()['state']['activity']['status'] == 'active'


def test_explicit_wrong_object_and_missing_live_shared_target_do_not_write(life):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    wrong = '我这次不参加了，你自己继续做纸星星吧'
    with run(life, wrong, first.conversation_id) as (admission, _):
        assert not life[2].matches(admission.user_message_id, wrong, first.conversation_id)
    life[1].apply_event(command(life[1], 'withdraw_shared'))
    explicit = '我这次不参加了，你自己继续折小船吧'
    with run(life, explicit, first.conversation_id) as (admission, _):
        assert not life[2].matches(admission.user_message_id, explicit, first.conversation_id)


@pytest.mark.parametrize('message', ['这步你自己做吧', '我这次不参加了，你自己继续折小船吧'])
def test_cross_conversation_withdrawal_is_not_source_ownership(life, message):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    before = life[1].read()
    with run(life, message) as (other, _):
        assert other.conversation_id != first.conversation_id
        assert not life[2].matches(other.user_message_id, message, other.conversation_id)
    ChatRepository(life[0]).delete_conversation(other.conversation_id)
    after = life[1].read()
    assert after['state'] == before['state']
    assert after['shared_participation'] == before['shared_participation']


@pytest.mark.parametrize('delete_original', [False, True])
def test_deleting_either_source_conversation_keeps_the_other_source(life, delete_original):
    with run(life, '等我回来一起选颜色') as (original, execute):
        execute()
    with run(life, '蓝色', original.conversation_id) as (_, execute):
        execute()
    completed = life[1].read()['shared_participation'][0]
    with run(life, '等我回来一起起个名字') as (current, execute):
        execute()
    with run(life, '这步你自己做吧', current.conversation_id) as (_, execute):
        execute()
    parts = life[1].read()['shared_participation']
    withdrawn = next(part for part in parts if part['status'] == 'abandoned')
    deleted = original.conversation_id if delete_original else current.conversation_id
    kept = current.conversation_id if delete_original else original.conversation_id
    ChatRepository(life[0]).delete_conversation(deleted)
    after = life[1].read()
    assert after['shared_participation'] == [withdrawn if delete_original else completed]
    assert after['state']['activity']['status'] == 'active'
    assert after['state']['activity']['promise'] is None
    assert after['state']['wish'] is None and after['state']['activity']['wish_offered'] is True
    with life[0]() as session:
        evidence = session.scalars(select(CompanionChatEvidence)).all()
        assert evidence and all(item.conversation_id == kept for item in evidence)
        assert all(item.participation_id == (withdrawn if delete_original else completed)['id'] for item in evidence)


@pytest.mark.parametrize('change,error', [('revision', 'revision_conflict'), ('activity', 'activity_identity_conflict'), ('participation', 'shared_step_conflict')])
def test_frozen_withdrawal_never_retargets_after_state_changes(life, change, error):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    message = '这步你自己做吧'
    admission, lease = admit(life, message, first.conversation_id)
    with life[0]() as session:
        row = session.get(CompanionState, 1)
        data = json.loads(row.life_json)
        if change == 'revision':
            row.revision += 1
        elif change == 'activity':
            data['activity']['id'] = str(uuid4())
        else:
            data['activity']['shared_step']['participation_id'] = str(uuid4())
        row.life_json = json.dumps(data)
        session.commit()
    with life[4].execution_scope(lease), pytest.raises(CompanionConflict, match=error):
        life[2].execute(admission.user_message_id, message, first.conversation_id)
    assert life[1].read()['shared_participation'][0]['status'] == 'promised'
    life[4].finish(lease, 'completed')


def test_withdrawal_requires_owner_and_commit_fence_rolls_back(life, monkeypatch):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    before = life[1].read()
    admission, lease = admit(life, '这步你自己做吧', first.conversation_id)
    with pytest.raises(CompanionConflict, match='owner_missing'):
        life[2].execute(admission.user_message_id, '这步你自己做吧', first.conversation_id)
    original = life[2]._apply
    with life[4].execution_scope(lease) as scope:
        def revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            scope.revoked.set()
            return result
        monkeypatch.setattr(life[2], '_apply', revoke)
        with pytest.raises(TurnExecutionFenced):
            life[2].execute(admission.user_message_id, '这步你自己做吧', first.conversation_id)
    assert life[1].read()['state'] == before['state']
    assert life[1].read()['revision'] == before['revision']
    with life[0]() as session:
        assert not session.scalar(select(CompanionChatEvidence).where(CompanionChatEvidence.action == 'withdraw_shared'))


def test_control_wins_a_chat_withdrawal_race_once_without_double_abandonment(life):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    admission, lease = admit(life, '这步你自己做吧', first.conversation_id)
    life[1].apply_event(command(life[1], 'withdraw_shared'))
    with life[4].execution_scope(lease), pytest.raises(CompanionConflict, match='revision_conflict'):
        life[2].execute(admission.user_message_id, '这步你自己做吧', first.conversation_id)
    with life[0]() as session:
        assert session.scalar(select(func.count()).select_from(CompanionEvent).where(CompanionEvent.event_type == 'shared_withdrawn')) == 1
        assert not session.scalar(select(CompanionChatEvidence).where(CompanionChatEvidence.action == 'withdraw_shared'))
    assert life[1].read()['shared_participation'][0]['status'] == 'abandoned'
    life[4].finish(lease, 'completed')


@pytest.mark.parametrize('blocked', ['disabled', 'application', 'mock_interview', 'pending', 'attachments', 'pilot_action'])
def test_withdrawal_admission_respects_disabled_work_and_pending_guards(life, blocked):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    request = SimpleNamespace(message='我这次不参加了，你自己继续折小船吧', attachments=(), pilot_action=None)
    with life[0]() as session:
        conversation = session.get(Conversation, first.conversation_id)
        if blocked == 'disabled':
            row = session.get(CompanionState, 1)
            row.settings_json = CompanionPolicy(enabled=False).model_dump_json()
        elif blocked == 'application':
            conversation.context_type = 'application'
        elif blocked == 'mock_interview':
            conversation.mode = 'mock_interview'
        elif blocked == 'pending':
            conversation.pending_tool_call_id = 'existing-work-owner'
        elif blocked == 'attachments':
            request.attachments = ('work.pdf',)
        else:
            request.pilot_action = {'kind': 'work'}
        assert freeze_chat_command(session, conversation, request) == {}


def test_proactivity_off_still_allows_explicit_withdrawal_without_proactive_source(life):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    repo = life[1]
    before = repo.read()
    repo.update_settings(CompanionSettingsCommand(identity=before['identity'], expected_revision=before['revision'],
        command_id=uuid4(), settings=CompanionPolicy(proactivity='off')))
    with run(life, '这步你自己做吧', first.conversation_id) as (_, execute):
        execute()
    assert repo.read()['settings']['proactivity'] == 'off'
    assert repo.read()['state']['activity']['status'] == 'active'


@pytest.mark.parametrize('endpoint', ['/api/chat', '/api/chat/stream'])
def test_withdrawal_uses_real_chat_transport_and_preserves_mixed_work_message(tmp_path, endpoint):
    model = CountingModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        repo = client.app.state.companion_repository
        repo.apply_event(command_for_start(repo))
        joined = client.post('/api/chat', json={'request_id': str(uuid4()), 'message': '等我回来一起选颜色'}).json()
        conversation_id = joined['conversation_id']
        mixed = '这步你自己做吧，然后帮我改简历'
        response = client.post('/api/chat', json={'request_id': str(uuid4()), 'conversation_id': conversation_id, 'message': mixed})
        assert response.status_code == 200 and model.calls == 1
        assert any(item.role == 'user' and item.content == mixed for item in model.messages)
        assert repo.read()['state']['activity']['status'] == 'waiting_shared'
        explicit = '我这次不参加了，你自己继续折小船吧'
        payload = {'request_id': str(uuid4()), 'conversation_id': conversation_id, 'message': explicit}
        response = client.post(endpoint, json=payload)
        assert response.status_code == 200, response.text
        assert '剩下的我自己慢慢做' in response.text
        assert model.calls == 1
        assert repo.read()['shared_participation'][0]['status'] == 'abandoned'


def command_for_start(repo):
    from offerpilot.companion.contracts import CompanionEventCommand
    before = repo.read()
    return CompanionEventCommand(identity=before['identity'], expected_revision=before['revision'], command_id=uuid4(), event='app_open')


def test_withdrawal_freezes_source_participation_without_other_conversation_ownership(life):
    from offerpilot.models import PilotTurnRecord
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    before = life[1].read()
    admission, lease = admit(life, '这步你自己做吧', first.conversation_id)
    with life[0]() as session:
        turn = session.get(PilotTurnRecord, admission.turn_id)
        frozen = json.loads(json.loads(turn.source_versions_json)[SOURCE_KEY])
        assert frozen['identity'] == before['identity']
        assert frozen['revision'] == before['revision']
        assert frozen['activity_id'] == before['state']['activity']['id']
        assert frozen['participation_id'] == before['shared_participation'][0]['id']
        assert frozen['shared_withdrawable'] is True
    life[4].finish(lease, 'completed')


def test_source_deletion_removes_withdrawal_receipt_and_does_not_restore_wait(life):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    with run(life, '这步你自己做吧', first.conversation_id) as (_, execute):
        execute()
    ChatRepository(life[0]).delete_conversation(first.conversation_id)
    view = life[1].read()
    assert view['state']['activity']['status'] == 'active'
    assert view['state']['activity']['promise'] is None and view['state']['wish'] is None
    assert view['shared_participation'] == []
    with life[0]() as session:
        assert not session.scalar(select(CompanionChatEvidence))
        assert not session.scalar(select(CompanionCommandReceipt).where(CompanionCommandReceipt.response_json.contains('不会记成一起完成')))
