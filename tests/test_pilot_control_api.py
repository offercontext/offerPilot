from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from offerpilot.ai.types import Assistant
from offerpilot.api import create_app
from tests.test_action_presentation import _Model


class BarrierModel:
    def __init__(self):
        self.entered = Event()
        self.release = Event()
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        self.entered.set()
        assert self.release.wait(20)
        return Assistant(content='迟到的回复不得发布')


@pytest.mark.parametrize('endpoint', ['/api/chat', '/api/chat/stream'])
def test_another_page_can_stop_exact_execution_before_late_provider_result(tmp_path, endpoint):
    model = BarrierModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client, ThreadPoolExecutor() as pool:
        key = str(uuid4())
        pending = pool.submit(client.post, endpoint, json={'message': '持续运行', 'request_id': key})
        try:
            assert model.entered.wait(20)
            turn = client.get(f'/api/chat/requests/{key}').json()
            assert turn['execution_generation'] == 1
            current = client.get(f"/api/chat/conversations/{turn['conversation_id']}/execution").json()['execution']
            assert current['turn_id'] == turn['turn_id']
            command = {'command_id': str(uuid4()), 'expected_generation': 1}
            stopped = client.post(f"/api/chat/turns/{turn['turn_id']}/interrupt", json=command)
            assert stopped.status_code == 200
            assert stopped.json()['status'] == 'stopped'
            assert client.post(f"/api/chat/turns/{turn['turn_id']}/interrupt", json=command).json() == stopped.json()
        finally:
            model.release.set()
            pending.result(timeout=20)
        messages = client.get(f"/api/chat/conversations/{turn['conversation_id']}").json()
        assert [message['role'] for message in messages] == ['user']
        assert client.get(f"/api/chat/turns/{turn['turn_id']}").json()['state'] == 'stopped'
        assert model.calls == 1


def test_active_conversation_rejects_new_request_without_persisting_another_message(tmp_path):
    model = BarrierModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client, ThreadPoolExecutor() as pool:
        key = str(uuid4())
        first = pool.submit(client.post, '/api/chat', json={'message': '正在执行', 'request_id': key})
        try:
            assert model.entered.wait(20)
            turn = client.get(f'/api/chat/requests/{key}').json()
            second = client.post('/api/chat', json={'message': '冲突的新任务', 'conversation_id': turn['conversation_id']})
            assert second.status_code == 409
            assert second.json()['error_code'] == 'turn_execution_active'
        finally:
            model.release.set()
            first.result(timeout=20)
        messages = client.get(f"/api/chat/conversations/{turn['conversation_id']}").json()
        assert sum(message['role'] == 'user' for message in messages) == 1
        assert model.calls == 1


def test_confirmation_resumes_same_turn_but_terminal_replay_has_no_new_generation(tmp_path):
    model = _Model()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        client.post('/api/applications', json={'company_name': '控制验收', 'position_name': '工程师', 'status': 'applied'})
        proposed = client.post('/api/chat', json={'message': '更新投递'}).json()
        turn_id = proposed['turn_id']
        before = client.get(f'/api/chat/turns/{turn_id}').json()
        assert before['execution_generation'] == 1
        assert before['state'] == 'waiting_confirmation'
        no_worker = client.post(f'/api/chat/turns/{turn_id}/interrupt', json={
            'command_id': str(uuid4()), 'expected_generation': 1,
        })
        assert no_worker.json()['status'] == 'no_active_execution'
        action = proposed['pending_action']
        confirm = {'conversation_id': proposed['conversation_id'], 'operation_id': action['operation_id'],
                   'confirmation_token': action['confirmation_token'], 'approved': True, 'edited_args': {'status': 'offer'}}
        result = client.post('/api/chat/confirm', json=confirm)
        assert result.status_code == 200, result.text
        after = client.get(f'/api/chat/turns/{turn_id}').json()
        assert after['execution_generation'] == 2
        assert after['state'] == 'completed'
        assert client.post('/api/chat/confirm', json=confirm).status_code == 200
        assert client.get(f'/api/chat/turns/{turn_id}').json()['execution_generation'] == 2
        old = client.post(f'/api/chat/turns/{turn_id}/interrupt', json={
            'command_id': str(uuid4()), 'expected_generation': 1,
        })
        assert old.json()['status'] == 'generation_changed'
        assert client.get('/api/applications/1').json()['status'] == 'offer'
        assert client.post('/api/chat/undo-last-write', json={
            'conversation_id': proposed['conversation_id'], 'parent_operation_id': action['operation_id'],
        }).status_code == 200
        assert model.calls == 1


def test_durable_timeout_keeps_timeout_receipt_without_accepting_late_model_output(tmp_path, monkeypatch):
    import offerpilot.api as api
    monkeypatch.setattr(api, 'CHAT_AGENT_TIMEOUT_SECONDS', 0.05)
    model = BarrierModel()
    try:
        with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
            response = client.post('/api/chat', json={'message': '超时应有已保存说明'})
            assert response.status_code == 200
            turn_id = response.json()['turn_id']
            turn = client.get(f'/api/chat/turns/{turn_id}').json()
            messages = client.get(f"/api/chat/conversations/{turn['conversation_id']}").json()
            assert [message['role'] for message in messages] == ['user', 'assistant']
            assert '处理时间过长' in messages[-1]['content']
            assert turn['state'] == 'interrupted'
    finally:
        model.release.set()


def test_control_endpoints_require_existing_api_auth(tmp_path):
    from offerpilot.config import Config, save_config
    save_config(tmp_path, Config(auth_enabled=True, auth_token='control-api-test-token'))
    with TestClient(create_app(data_dir=tmp_path, chat_model=_Model())) as client:
        assert client.get('/api/chat/conversations/1/execution').status_code == 401
        assert client.post(f'/api/chat/turns/{uuid4()}/interrupt', json={
            'command_id': str(uuid4()), 'expected_generation': 1,
        }).status_code == 401


def test_interrupt_validation_has_no_model_or_tool_side_effect(tmp_path):
    model = _Model()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        for generation in [None, 0, True, '1']:
            assert client.post(f'/api/chat/turns/{uuid4()}/interrupt', json={
                'command_id': str(uuid4()), 'expected_generation': generation,
            }).status_code == 422
        assert client.post(f'/api/chat/turns/{uuid4()}/interrupt', json={
            'command_id': str(uuid4()), 'expected_generation': 1,
        }).status_code == 404
        assert model.calls == 0


def test_stop_after_approval_claim_prevents_handler_entry_and_preserves_pending(tmp_path, monkeypatch):
    from offerpilot.pilot_control import PilotControlRepository
    import offerpilot.ai.write_operations as writes
    claimed = Event()
    release = Event()
    original_claim = PilotControlRepository.claim_confirmation
    original_execute = writes.execute_prepared
    entries = []

    def claim(self, operation_id):
        lease = original_claim(self, operation_id)
        claimed.set()
        assert release.wait(20)
        return lease

    def execute(*args, **kwargs):
        entries.append(True)
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(PilotControlRepository, 'claim_confirmation', claim)
    monkeypatch.setattr(writes, 'execute_prepared', execute)
    with TestClient(create_app(data_dir=tmp_path, chat_model=_Model())) as client, ThreadPoolExecutor() as pool:
        client.post('/api/applications', json={'company_name': '审批停止', 'position_name': '工程师', 'status': 'applied'})
        proposed = client.post('/api/chat', json={'message': '更新状态'}).json()
        pending = proposed['pending_action']
        payload = {'conversation_id': proposed['conversation_id'], 'approved': True,
                   'confirmation_token': pending['confirmation_token'], 'operation_id': pending['operation_id'],
                   'edited_args': {'status': 'offer'}}
        approval = pool.submit(client.post, '/api/chat/confirm', json=payload)
        try:
            assert claimed.wait(20)
            stopped = client.post(f"/api/chat/turns/{proposed['turn_id']}/interrupt", json={
                'command_id': str(uuid4()), 'expected_generation': 2,
            })
            assert stopped.json()['status'] == 'stopped'
        finally:
            release.set()
            approval.result(timeout=20)
        assert not entries
        assert client.get('/api/applications/1').json()['status'] == 'applied'
        assert client.get(f"/api/chat/turns/{proposed['turn_id']}").json()['state'] == 'stopped'
        # A new explicit confirmation still uses the original Pending/token.
        assert client.post('/api/chat/confirm', json=payload).status_code == 200
        assert len(entries) == 1
        assert client.get('/api/applications/1').json()['status'] == 'offer'
        assert client.get(f"/api/chat/turns/{proposed['turn_id']}").json()['execution_generation'] == 3


def test_successful_title_is_allowed_but_private_owner_never_enters_public_identity(tmp_path):
    from offerpilot.db import session_factory_for_data_dir
    from offerpilot.models import PilotExecution
    from sqlalchemy import select

    class Model:
        def __init__(self):
            self.calls = []

        def complete(self, messages, tools):
            self.calls.append((messages, tools))
            return Assistant(content='已保存的回复')

    model, title = Model(), Model()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model, title_model=title)) as client:
        response = client.post('/api/chat', json={'message': '标题与身份验收'})
        assert response.status_code == 200
        body = response.json()
        execution = client.get(f"/api/chat/conversations/{body['conversation_id']}/execution").json()['execution']
        assert set(execution) == {'turn_id', 'conversation_id', 'execution_generation', 'state'}
        assert len(title.calls) == 1
        with session_factory_for_data_dir(tmp_path)() as session:
            row = session.scalar(select(PilotExecution).where(PilotExecution.turn_id == body['turn_id']))
            assert row.owner_token not in response.text + repr(model.calls) + repr(title.calls)
