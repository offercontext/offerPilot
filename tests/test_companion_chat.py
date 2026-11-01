"""Real-message bounded life commands: negative grammar, ownership and replay."""
from contextlib import contextmanager
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from offerpilot.api import create_app
from offerpilot.companion.chat import CompanionChatService, freeze_chat_command, parse_chat_intent
from offerpilot.companion.contracts import CompanionEventCommand, CompanionPolicy, CompanionResetCommand, CompanionSettingsCommand
from offerpilot.companion.models import CompanionChatEvidence, CompanionEvent, CompanionParticipation
from offerpilot.companion.repository import CompanionConflict, CompanionRepository
from offerpilot.db import init_database
from offerpilot.models import ChatMessage
from offerpilot.pilot_control import PilotControlRepository, TurnExecutionFenced
from offerpilot.pilot_timeline import PilotTimelineRepository
from offerpilot.repositories.chat import ChatRepository
from tests.test_pilot_turn_api import CountingModel


@pytest.mark.parametrize('message,action,kind', [
    ('等我回来一起选颜色', 'wait', 'color'),
    ('先别往下做，等我回来我们一起给小船选颜色吧', 'wait', 'color'),
    ('Haru，等我有空一起起个名字', 'wait', 'name'),
    ('我回来了，我们一起选颜色吧', 'join', 'color'),
    ('我想和你一起折纸', 'join', None),
    ('我们一起给小船取个名字吧', 'join', 'name'),
    ('暂停你的折纸活动', 'pause', None),
    ('小船先暂停一下吧', 'pause', None),
    ('继续折纸吧', 'resume', None),
    ('恢复我们刚才的小船活动', 'resume', None),
])
def test_explicit_whole_sentences(message, action, kind):
    intent = parse_chat_intent(message)
    assert intent and (intent.action, intent.step_kind) == (action, kind)


@pytest.mark.parametrize('message', [
    '继续', '好了', '暂停', '我们一起做完了', '我想一起做',
    '不要暂停你的折纸活动', '我不想暂停你的折纸活动', '如果暂停你的折纸活动会怎样',
    '他说等我回来一起选颜色', '“等我回来一起选颜色”',
    '请解释“暂停你的折纸活动”', '比如等我回来一起选颜色',
    '等我回来一起选颜色，然后帮我改简历', '暂停工作，等我回来一起选颜色',
    '先完成代码，再继续折纸吧', '我没有说等我回来一起选颜色',
    '等我回来一起选颜色？', '蓝色', '叫棉花糖号吧',
])
def test_ambiguous_negated_quoted_conditional_or_mixed_work_never_writes(message):
    assert parse_chat_intent(message) is None


@pytest.fixture
def life(tmp_path):
    sessions = init_database(tmp_path / 'life.db')
    repo = CompanionRepository(sessions)
    snapshot = repo.read()
    repo.apply_event(CompanionEventCommand(identity=snapshot['identity'], expected_revision=snapshot['revision'],
                    command_id=uuid4(), event='app_open'))
    return sessions, repo, CompanionChatService(sessions), PilotTimelineRepository(sessions), PilotControlRepository(sessions)


def command(repo, event, **kwargs):
    view = repo.read()
    return CompanionEventCommand(identity=view['identity'], expected_revision=view['revision'], command_id=uuid4(),
                                 event=event, activity_id=view['state']['activity']['id'], **kwargs)


def admit(life, message, conversation_id=0):
    sessions, repo, service, timeline, controls = life
    request = SimpleNamespace(message=message, attachments=(), pilot_action=None)
    leases = []
    admission = timeline.admit(str(uuid4()), {'message': message, 'conversation_id': conversation_id},
        freeze_sources=lambda session, conversation: freeze_chat_command(session, conversation, request),
        on_admitted=lambda session, turn: leases.append(controls.claim_start_in_session(session, turn.id)))
    return admission, leases[0]


@contextmanager
def run(life, message, conversation_id=0):
    admission, lease = admit(life, message, conversation_id)
    with life[4].execution_scope(lease):
        yield admission, lambda: life[2].execute(admission.user_message_id, message, admission.conversation_id)
    life[4].finish(lease, 'completed')


def test_wait_refresh_day_gap_then_real_reply_completes_only_shared_step(life):
    _, repo, service, _, _ = life
    with run(life, '等我回来一起选颜色') as (admission, execute):
        assert '停在这里' in execute()
    waiting = repo.read()
    assert waiting['state']['activity']['status'] == 'waiting_shared'
    assert waiting['state']['activity']['promise'] == '等你一起选颜色'
    for wake in ('app_open', 'chat_end', 'work_completed', 'user_interaction'):
        assert not repo.apply_event(command(repo, wake), now=9999999999)['changed']
    with run(life, '蓝色', admission.conversation_id) as (answer, execute):
        assert '选蓝色' in execute()
    result = repo.read()
    assert result['state']['activity']['status'] == 'active'
    assert result['state']['activity']['step_index'] == 0
    assert result['shared_participation'][0]['status'] == 'completed'
    with life[0]() as session:
        evidence = session.scalar(select(CompanionChatEvidence).where(CompanionChatEvidence.action == 'answer'))
        assert evidence.message_id == answer.user_message_id
        assert evidence.value == '蓝色'
        assert session.get(ChatMessage, evidence.message_id).role == 'user'
        assert session.scalar(select(func.count()).select_from(CompanionParticipation)) == 1


def test_exact_retry_and_new_message_do_not_double_apply(life):
    with run(life, '等我回来一起选颜色') as (_, execute):
        first = execute()
        revision = life[1].read()['revision']
        assert execute() == first
        assert life[1].read()['revision'] == revision
    with life[0]() as session:
        assert session.scalar(select(func.count()).select_from(CompanionChatEvidence)) == 1


def test_frozen_revision_cannot_retarget_after_other_window_pause(life):
    admission, lease = admit(life, '等我回来一起选颜色')
    life[1].apply_event(command(life[1], 'pause'))
    with life[4].execution_scope(lease), pytest.raises(CompanionConflict, match='revision_conflict'):
        life[2].execute(admission.user_message_id, '等我回来一起选颜色', admission.conversation_id)
    assert life[1].read()['state']['activity']['status'] == 'paused'


def test_execution_owner_required_and_revoked_owner_cannot_commit(life):
    admission, lease = admit(life, '等我回来一起选颜色')
    with pytest.raises(CompanionConflict, match='owner_missing'):
        life[2].execute(admission.user_message_id, '等我回来一起选颜色', admission.conversation_id)
    with life[4].execution_scope(lease) as scope:
        scope.revoked.set()
        with pytest.raises(TurnExecutionFenced):
            life[2].execute(admission.user_message_id, '等我回来一起选颜色', admission.conversation_id)
    assert life[1].read()['state']['activity']['status'] == 'active'


def test_only_same_conversation_actual_user_reply_can_complete(life):
    with run(life, '等我回来一起选颜色') as (_, execute):
        execute()
    admission, lease = admit(life, '蓝色')
    assert not life[2].matches(admission.user_message_id, '蓝色', admission.conversation_id)
    with life[0]() as session:
        source = session.get(ChatMessage, admission.user_message_id)
        source.role = 'assistant'
        session.commit()
    assert not life[2].matches(admission.user_message_id, '蓝色', admission.conversation_id)
    assert life[1].read()['state']['activity']['status'] == 'waiting_shared'


def test_paused_shared_step_resumes_to_wait_not_automatic_completion(life):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    with run(life, '暂停你的折纸活动', first.conversation_id) as (_, execute):
        execute()
    with run(life, '蓝色', first.conversation_id) as (admission, _):
        assert not life[2].matches(admission.user_message_id, '蓝色', admission.conversation_id)
    with run(life, '继续折纸吧', first.conversation_id) as (_, execute):
        execute()
    assert life[1].read()['state']['activity']['status'] == 'waiting_shared'


def test_delete_source_conversation_erases_derived_choice_and_wait(life):
    with run(life, '等我回来一起选颜色') as (admission, execute):
        execute()
    ChatRepository(life[0]).delete_conversation(admission.conversation_id)
    view = life[1].read()
    assert view['state']['activity']['status'] == 'paused'
    assert view['state']['activity']['promise'] is None
    assert view['shared_participation'] == []
    with life[0]() as session:
        assert session.scalar(select(func.count()).select_from(CompanionChatEvidence)) == 0
        assert session.scalar(select(func.count()).select_from(CompanionEvent).where(CompanionEvent.source == 'user_chat')) == 0


@pytest.mark.parametrize('endpoint', ['/api/chat', '/api/chat/stream'])
def test_real_transport_routes_to_same_command_without_model(tmp_path, endpoint):
    model = CountingModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        snapshot = client.get('/api/companion').json()
        started = client.post('/api/companion/events', json={'identity': snapshot['identity'],
            'expected_revision': snapshot['revision'], 'command_id': str(uuid4()), 'event': 'app_open'})
        assert started.status_code == 200
        payload = {'request_id': str(uuid4()), 'message': '等我回来一起选颜色'}
        response = client.post(endpoint, json=payload)
        assert response.status_code == 200, response.text
        assert '停在这里' in response.text
        assert client.get('/api/companion').json()['state']['activity']['status'] == 'waiting_shared'
        assert model.calls == 0
        replay = client.post(endpoint, json=payload)
        assert replay.status_code == 200
        assert replay.json()['type'] == 'turn_recovered'
        assert model.calls == 0


def test_stale_identity_after_reset_and_disabled_mode(life):
    admission, lease = admit(life, '等我回来一起选颜色')
    snapshot = life[1].read()
    life[1].reset(CompanionResetCommand(identity=snapshot['identity'], expected_revision=snapshot['revision'],
                                      command_id=uuid4(), confirmed=True))
    with life[4].execution_scope(lease), pytest.raises(CompanionConflict, match='identity_conflict'):
        life[2].execute(admission.user_message_id, '等我回来一起选颜色', admission.conversation_id)
    life[4].finish(lease, 'completed')
    snapshot = life[1].read()
    life[1].update_settings(CompanionSettingsCommand(identity=snapshot['identity'], expected_revision=snapshot['revision'],
        command_id=uuid4(), settings=CompanionPolicy(enabled=False)))
    admission, _ = admit(life, '等我回来一起选颜色')
    assert not life[2].matches(admission.user_message_id, '等我回来一起选颜色', admission.conversation_id)


def test_explicit_other_activity_is_not_retargeted(life):
    admission, _ = admit(life, '暂停纸星星')
    assert not life[2].matches(admission.user_message_id, '暂停纸星星', admission.conversation_id)
    assert life[1].read()['state']['activity']['template_id'] == 'paper_boat'


def test_source_context_recalls_actual_reply_not_user_preference(life):
    from offerpilot.companion.context import read_companion_context, companion_context_message
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    with run(life, '我选蓝色', first.conversation_id) as (answer, execute):
        execute()
    with life[0]().connection() as connection:
        dbapi = connection.connection.driver_connection
        context = read_companion_context(dbapi, '上次一起做的小船', context_type='workspace')
    actual = context['actual_user_participation']
    assert actual['source'] == 'companion_participation:user_chat'
    assert actual['source_message_id'] == answer.user_message_id
    assert actual['user_reply'] == '蓝色'
    assert actual['scope'] == 'shared_step_only'
    assert companion_context_message(context) is not None
    assert 'preference' not in json.dumps(context)


def test_detached_runtime_uses_same_bound_command(tmp_path):
    from tests.test_managed_runtime_api import _wait_for_status
    model = CountingModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        repo = client.app.state.companion_repository
        snapshot = repo.read()
        repo.apply_event(CompanionEventCommand(identity=snapshot['identity'], expected_revision=snapshot['revision'],
                        command_id=uuid4(), event='app_open'))
        response = client.post('/api/pilot/runtime/v1/turns', json={'request_id': str(uuid4()),
                                                               'message': '等我回来一起选颜色'})
        assert response.status_code == 202, response.text
        result = _wait_for_status(client, response.json()['turn_id'], terminal={'completed', 'failed'})
        assert result['state'] == 'completed', result
        assert repo.read()['state']['activity']['status'] == 'waiting_shared'
        assert model.calls == 0


def test_commit_fence_rolls_back_life_if_owner_cancelled_after_transition(life, monkeypatch):
    admission, lease = admit(life, '等我回来一起选颜色')
    before = life[1].read()
    original = life[2]._apply
    with life[4].execution_scope(lease) as scope:
        def cancel_after_transition(*args, **kwargs):
            result = original(*args, **kwargs)
            scope.revoked.set()
            return result
        monkeypatch.setattr(life[2], '_apply', cancel_after_transition)
        with pytest.raises(TurnExecutionFenced):
            life[2].execute(admission.user_message_id, '等我回来一起选颜色', admission.conversation_id)
    after = life[1].read()
    assert after['revision'] == before['revision']
    assert after['state'] == before['state']
    with life[0]() as session:
        assert session.scalar(select(func.count()).select_from(CompanionChatEvidence)) == 0


def test_concurrent_chat_windows_share_one_revision_fence(life):
    from concurrent.futures import ThreadPoolExecutor
    sources = [admit(life, '等我回来一起选颜色') for _ in range(2)]
    def execute(source):
        admission, lease = source
        with life[4].execution_scope(lease):
            try:
                life[2].execute(admission.user_message_id, '等我回来一起选颜色', admission.conversation_id)
                return 'saved'
            except CompanionConflict:
                return 'conflict'
    with ThreadPoolExecutor(2) as workers:
        assert sorted(workers.map(execute, sources)) == ['conflict', 'saved']
    with life[0]() as session:
        assert session.scalar(select(func.count()).select_from(CompanionParticipation)) == 1
        assert session.scalar(select(func.count()).select_from(CompanionChatEvidence)) == 1


def test_intervening_work_or_ordinary_turn_closes_contextless_color_reply_slot(life):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    with run(life, '简历的标题用什么颜色好', first.conversation_id):
        pass  # Ordinary/model turn, not a companion action.
    admission, lease = admit(life, '蓝色', first.conversation_id)
    assert not life[2].matches(admission.user_message_id, '蓝色', first.conversation_id)
    life[4].finish(lease, 'completed')
    assert life[1].read()['state']['activity']['status'] == 'waiting_shared'
    with run(life, '我回来了，我们一起选颜色吧', first.conversation_id) as (_, execute):
        execute()
    with run(life, '蓝色', first.conversation_id) as (_, execute):
        assert '选蓝色' in execute()


def test_deleting_completed_chat_participation_removes_participant_flag_and_allows_controls(life):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    with run(life, '蓝色', first.conversation_id) as (_, execute):
        execute()
    ChatRepository(life[0]).delete_conversation(first.conversation_id)
    view = life[1].read()
    assert view['shared_participation'] == []
    assert view['state']['activity']['participants'] == ['companion']
    assert life[1].apply_event(command(life[1], 'join'))['state']['activity']['status'] == 'waiting_shared'


def test_source_deletion_redacts_control_receipt_snapshots_without_reexecuting(life):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    original = command(life[1], 'app_open')
    captured = life[1].apply_event(original)
    assert captured['state']['activity']['status'] == 'waiting_shared'
    assert captured['shared_participation']
    ChatRepository(life[0]).delete_conversation(first.conversation_id)
    after = life[1].read()
    recovered = life[1].apply_event(original)
    assert recovered['state'] == after['state']
    assert recovered['shared_participation'] == []
    assert recovered['redacted'] is True
    assert recovered['changed'] is False
    assert life[1].read()['revision'] == after['revision']
    assert recovered['receipt']['command_id'] == str(original.command_id)


def test_life_commands_leave_existing_work_hitl_and_pending_owner_intact(tmp_path):
    from tests.test_action_presentation import _Model
    from offerpilot.ai.types import Assistant
    class WorkModel(_Model):
        def complete(self, messages, tools):
            if self.calls == 0:
                return super().complete(messages, tools)
            self.calls += 1
            return Assistant(content='投递状态已更新。')
    model = WorkModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        application = client.post('/api/applications', json={'company_name': 'Keep approval',
            'position_name': 'Engineer', 'status': 'applied'}).json()
        repo = client.app.state.companion_repository
        view = repo.read()
        repo.apply_event(CompanionEventCommand(identity=view['identity'], expected_revision=view['revision'],
                                             command_id=uuid4(), event='app_open'))
        joined = client.post('/api/chat', json={'request_id': str(uuid4()), 'message': '等我回来一起选颜色'}).json()
        assert model.calls == 0
        proposed = client.post('/api/chat', json={'request_id': str(uuid4()),
            'conversation_id': joined['conversation_id'], 'message': '修改投递状态'}).json()
        assert proposed['pending_action']['tool_name'] == 'update_application_status'
        assert client.get(f"/api/applications/{application['id']}").json()['status'] == 'applied'
        blocked = client.post('/api/chat', json={'request_id': str(uuid4()),
            'conversation_id': joined['conversation_id'], 'message': '暂停你的折纸活动'})
        assert blocked.status_code == 409
        assert model.calls == 1
        pending = proposed['pending_action']
        confirmed = client.post('/api/chat/confirm', json={'conversation_id': joined['conversation_id'],
            'operation_id': pending['operation_id'], 'confirmation_token': pending['confirmation_token'],
            'approved': True})
        assert confirmed.status_code == 200, confirmed.text
        assert client.get(f"/api/applications/{application['id']}").json()['status'] == 'interview'
        assert model.calls == 2
        assert repo.read()['state']['activity']['status'] == 'waiting_shared'
