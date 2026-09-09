from uuid import uuid4
import json
import pytest

from fastapi.testclient import TestClient

from offerpilot.ai.types import Assistant
from offerpilot.api import create_app
from offerpilot.pilot_control import PilotControlRepository
from tests.test_action_presentation import _Model as ActionModel


class CountingModel:
    def __init__(self):
        self.calls = 0
        self.messages = []

    def complete(self, messages, tools):
        self.calls += 1
        self.messages = list(messages)
        return Assistant(content='已收到。')


def test_lost_response_retry_recovers_original_turn_without_running_again(tmp_path):
    model = CountingModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        payload = {'request_id': str(uuid4()), 'message': '独立任务', 'conversation_id': 0}
        first = client.post('/api/chat', json=payload)
        assert first.status_code == 200
        assert 'turn_id' in first.json()
        retried = client.post('/api/chat', json=payload)
        assert retried.status_code == 200
        assert retried.json()['type'] == 'turn_recovered'
        assert retried.json()['turn_id'] == first.json()['turn_id']
        assert retried.json()['conversation_id'] == first.json()['conversation_id']
        default_retry = client.post('/api/chat', json={**payload, 'mode': 'general', 'context_type': 'workspace', 'context_ref': ''})
        assert default_retry.status_code == 200
        assert default_retry.json()['turn_id'] == first.json()['turn_id']
        assert client.get(f"/api/chat/requests/{payload['request_id']}").json()['turn_id'] == first.json()['turn_id']
        assert model.calls == 1
        messages = client.get(f"/api/chat/conversations/{first.json()['conversation_id']}").json()
        assert sum(message['role'] == 'user' for message in messages) == 1
        turn = client.get(f"/api/chat/turns/{first.json()['turn_id']}").json()
        assert turn['message_ids'] == [message['id'] for message in messages]
        assert sum(message.role == 'user' and message.content == '独立任务' for message in model.messages) == 1
        assert client.post('/api/chat', json={**payload, 'message': '修改后的请求'}).status_code == 409
        assert model.calls == 1


def test_unknown_conversation_is_not_a_deleted_request(tmp_path):
    model = CountingModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        response = client.post('/api/chat', json={'request_id': str(uuid4()), 'message': '不存在', 'conversation_id': 999})
        assert response.status_code == 404
        assert model.calls == 0


def test_timeline_recovers_persisted_messages_without_provider_or_journal(tmp_path):
    model = CountingModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        result = client.post('/api/chat', json={'request_id': str(uuid4()), 'message': '持久时间线'}).json()
        endpoint = f"/api/chat/conversations/{result['conversation_id']}/timeline"
        snapshot = client.get(endpoint)
        assert snapshot.status_code == 200
        body = snapshot.json()
        assert body['mode'] == 'snapshot'
        assert {item['turn_id'] for item in body['items']} == {result['turn_id']}
        assert [item['payload']['content'] for item in body['items'] if item['item_type'].endswith('_message')] == ['持久时间线', '已收到。']
        delta = client.get(endpoint, params={'cursor': body['cursor']}).json()
        assert delta['items'] == []
        assert model.calls == 1


def test_restart_and_deleted_conversation_never_restart_old_submission(tmp_path):
    model = CountingModel()
    payload = {'request_id': str(uuid4()), 'message': '进程重启恢复', 'conversation_id': 0}
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        first = client.post('/api/chat', json=payload).json()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        recovered = client.post('/api/chat/stream', json=payload)
        assert recovered.status_code == 200
        assert recovered.json()['type'] == 'turn_recovered'
        assert recovered.json()['turn_id'] == first['turn_id']
        read = client.get(f"/api/chat/turns/{first['turn_id']}")
        assert read.status_code == 200
        assert read.json()['state'] == 'completed'
        client.delete(f"/api/chat/conversations/{first['conversation_id']}")
        assert client.post('/api/chat', json=payload).status_code == 410
        assert client.get(f"/api/chat/turns/{first['turn_id']}").status_code == 404
        assert model.calls == 1


@pytest.mark.parametrize('endpoint,method', [('/api/chat', 'with_start_ports'), ('/api/chat/stream', 'with_start_ports'), ('/api/chat/stream', 'prepare_stream')])
def test_post_admission_initialization_failure_has_identity_and_never_reexecutes(tmp_path, monkeypatch, endpoint, method):
    model = CountingModel()
    app = create_app(data_dir=tmp_path, chat_model=model)

    def unavailable(*args, **kwargs):
        raise RuntimeError('private exception must not reach client')

    monkeypatch.setattr(type(app.state.pilot_runtime), method, unavailable)
    with TestClient(app) as client:
        payload = {'request_id': str(uuid4()), 'message': '初始化失败'}
        failed = client.post(endpoint, json=payload)
        assert failed.status_code in {500, 503}
        assert 'private exception' not in failed.text
        turn_id = failed.json()['turn_id']
        turn = client.get(f'/api/chat/turns/{turn_id}').json()
        assert turn['state'] in {'failed', 'interrupted'}
        retry = client.post(endpoint, json=payload)
        assert retry.status_code == 200
        assert retry.json()['turn_id'] == turn_id
        assert model.calls == 0


def test_failed_terminal_write_retries_the_known_result_not_interrupted(tmp_path, monkeypatch):
    original = PilotControlRepository.finish
    attempted = []

    def finish(self, lease, state):
        attempted.append(state)
        if len(attempted) == 1:
            raise RuntimeError('temporary display write failure')
        original(self, lease, state)

    monkeypatch.setattr(PilotControlRepository, 'finish', finish)
    with TestClient(create_app(data_dir=tmp_path, chat_model=CountingModel())) as client:
        result = client.post('/api/chat', json={'request_id': str(uuid4()), 'message': '完整回答'}).json()
        assert attempted == ['completed', 'completed']
        assert client.get(f"/api/chat/turns/{result['turn_id']}").json()['state'] == 'completed'


def test_stream_envelopes_have_durable_identity_and_read_does_not_execute(tmp_path):
    model = CountingModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        key = str(uuid4())
        response = client.post('/api/chat/stream', json={'request_id': key, 'message': '流式回复'})
        assert response.status_code == 200
        turn_id = response.headers['X-Pilot-Turn-Id']
        frames = [json.loads(line[5:].strip()) for line in response.text.splitlines() if line.startswith('data:')]
        assert frames
        assert all(frame['turn_id'] == turn_id and frame['request_id'] == key for frame in frames)
        read = client.get(f'/api/chat/requests/{key}').json()
        assert read['turn_id'] == turn_id
        assert read['state'] == 'completed'
        assert model.calls == 1


def test_durable_action_keeps_identity_through_confirmation_and_undo(tmp_path):
    model = ActionModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        client.post('/api/applications', json={'company_name': '持久动作', 'position_name': '工程师', 'status': 'applied'})
        proposed = client.post('/api/chat', json={'request_id': str(uuid4()), 'message': '修改状态'}).json()
        operation_id = proposed['pending_action']['operation_id']
        endpoint = f"/api/chat/conversations/{proposed['conversation_id']}/timeline"
        first = client.get(endpoint).json()
        initial = next(item for item in first['items'] if item['item_type'] == 'action')
        assert initial['turn_id'] == proposed['turn_id']
        assert initial['payload']['action']['decision'] == 'undecided'
        confirmed = client.post('/api/chat/confirm', json={
            'conversation_id': proposed['conversation_id'], 'operation_id': operation_id,
            'confirmation_token': proposed['pending_action']['confirmation_token'],
            'approved': True, 'edited_args': {'status': 'offer'},
        })
        assert confirmed.status_code == 200
        changed = client.get(endpoint, params={'cursor': first['cursor']}).json()
        receipt = next(item for item in changed['items'] if item['item_type'] == 'action')
        assert receipt['item_id'] == initial['item_id']
        assert receipt['ordinal'] == initial['ordinal']
        assert receipt['revision'] > initial['revision']
        assert receipt['payload']['action']['execution'] == 'committed'
        read = client.get(f"/api/chat/turns/{proposed['turn_id']}").json()
        assert operation_id in read['operation_ids']
        assert len(read['message_ids']) > 1
        assert client.post('/api/chat/undo-last-write', json={'conversation_id': proposed['conversation_id'], 'parent_operation_id': operation_id}).status_code == 200
        after = client.get(endpoint, params={'cursor': changed['cursor']}).json()
        undone = next(item for item in after['items'] if item['item_type'] == 'action')
        assert undone['item_id'] == initial['item_id']
        assert undone['revision'] > receipt['revision']
        assert undone['payload']['action']['undo'] == 'undone'
        assert model.calls == 1
