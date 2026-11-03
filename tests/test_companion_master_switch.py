"""Offline regressions for the companion master switch, not model style evals."""
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from offerpilot.ai.types import Assistant
from offerpilot.api import create_app
from offerpilot.companion.contracts import CompanionPolicy, CompanionSettingsCommand
from offerpilot.companion.persona_repository import PersonaRepository
from offerpilot.companion.repository import CompanionRepository
from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import Application, PilotTurnRecord
from tests._companion import enable_companion
from tests.test_companion_context import event
from tests.test_managed_runtime_api import _PendingModel, _wait_for_status
from tests.test_persona_runtime import profile, update
from tests.test_pilot_turn_api import CountingModel

ENDPOINTS = ['/api/chat', '/api/chat/stream', '/api/pilot/runtime/v1/turns']


def set_enabled(repository, enabled):
    snapshot = repository.read()
    return repository.update_settings(CompanionSettingsCommand(
        identity=snapshot['identity'], expected_revision=snapshot['revision'],
        command_id=uuid4(), settings=CompanionPolicy(enabled=enabled)), now=1001)


def submit(client, endpoint, **fields):
    response = client.post(endpoint, json={
        'request_id': str(uuid4()), 'message': '你的小日常怎么样了？', **fields,
    })
    assert response.status_code in (200, 202), response.text
    if endpoint.endswith('/turns'):
        terminal = _wait_for_status(client, response.json()['turn_id'], terminal={'completed', 'failed'})
        assert terminal['state'] == 'completed', terminal
    elif endpoint.endswith('/stream'):
        assert 'text/event-stream' in response.headers['content-type']
    return response


def assert_ordinary(messages):
    assert messages, 'The fake provider must have been called'
    assert not profile(messages)
    content = '\n'.join(message.content or '' for message in messages)
    for forbidden in ('"source": "companion_life"', 'companion-v1.6', '被好好照顾着、无忧无虑', '呆萌'):
        assert forbidden not in content
    system = '\n'.join(message.content for message in messages if message.role == 'system')
    assert 'OfferPilot' in system
    assert '不要暴露隐藏推理' in system
    assert '不编造' in system


def assert_binding(sessions, original, enabled):
    with sessions() as session:
        turns = session.scalars(select(PilotTurnRecord)).all()
        assert turns
        versions = json.loads(turns[-1].source_versions_json)
        assert versions['companion_enabled_v1'] == ('true' if enabled else 'false')
        assert json.loads(versions['companion_persona_v1']) == {
            key: original[key] for key in ('profile_id', 'revision', 'content_hash')
        }
        return versions


@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('custom', [False, True])
def test_default_off_is_ordinary_pilot_even_with_saved_persona(tmp_path, endpoint, custom):
    model = CountingModel()
    app = create_app(data_dir=tmp_path, chat_model=model)
    sessions = session_factory_for_data_dir(tmp_path)
    persona = PersonaRepository(sessions)
    original = update(persona, '保存但未启用的名字') if custom else persona.read()
    assert CompanionRepository(sessions).read()['settings']['enabled'] is False
    with TestClient(app) as client:
        submit(client, endpoint)
    assert model.calls == 1
    assert_ordinary(model.messages)
    assert_binding(sessions, original, False)


@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('custom', [False, True])
def test_disable_removes_all_companion_sources_without_resetting_persona(tmp_path, endpoint, custom):
    model = CountingModel()
    app = create_app(data_dir=tmp_path, chat_model=model)
    sessions = session_factory_for_data_dir(tmp_path)
    companion = CompanionRepository(sessions)
    persona = PersonaRepository(sessions)
    original = update(persona, '关闭前名字') if custom else persona.read()
    saved, history = persona.read(), persona.revisions()
    enable_companion(companion)
    event(companion, 'app_open')
    set_enabled(companion, False)
    with TestClient(app) as client:
        submit(client, endpoint)
    assert_ordinary(model.messages)
    assert_binding(sessions, original, False)
    assert persona.read() == saved
    assert persona.revisions() == history


@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('admitted_enabled', [False, True])
def test_admission_switch_cannot_restore_or_leak_companion_packages(tmp_path, monkeypatch, endpoint, admitted_enabled):
    from offerpilot.pilot_runtime.service import PilotRuntime
    model = CountingModel()
    app = create_app(data_dir=tmp_path, chat_model=model)
    sessions = session_factory_for_data_dir(tmp_path)
    companion = CompanionRepository(sessions)
    original = update(PersonaRepository(sessions), '不应泄漏的名字')
    enable_companion(companion)
    event(companion, 'app_open')
    set_enabled(companion, admitted_enabled)
    original_ports = PilotRuntime.with_start_ports
    switched = False

    def switch_after_admission(self, *args, **kwargs):
        nonlocal switched
        if not switched:
            switched = True
            set_enabled(companion, not admitted_enabled)
        return original_ports(self, *args, **kwargs)

    monkeypatch.setattr(PilotRuntime, 'with_start_ports', switch_after_admission)
    with TestClient(app) as client:
        submit(client, endpoint)
    assert switched
    assert model.calls == 1
    assert_ordinary(model.messages)
    assert_binding(sessions, original, admitted_enabled)


def test_restart_stays_off_and_enabling_restores_original_persona(tmp_path):
    model = CountingModel()
    sessions = session_factory_for_data_dir(tmp_path)
    companion = CompanionRepository(sessions)
    persona = PersonaRepository(sessions)
    original = update(persona, '重新出现的名字', additional_style_notes='自然简短')
    saved, history = persona.read(), persona.revisions()
    enable_companion(companion)
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        submit(client, '/api/chat')
        assert profile(model.messages)[0]['expression_data']['display_name'] == '重新出现的名字'
        set_enabled(companion, False)
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        assert companion.read()['settings']['enabled'] is False
        submit(client, '/api/chat')
        assert_ordinary(model.messages)
        assert_binding(sessions, original, False)
        enable_companion(companion)
        submit(client, '/api/chat')
        assert profile(model.messages)[0]['expression_data']['display_name'] == '重新出现的名字'
        assert_binding(sessions, original, True)
    assert persona.read() == saved
    assert persona.revisions() == history


def test_disable_then_restart_confirmation_retains_hitl_and_binding(tmp_path):
    class ResumingModel(_PendingModel):
        def __init__(self):
            super().__init__()
            self.resumed_messages = []

        def complete(self, messages, tools):
            if not self.calls:
                return super().complete(messages, tools)
            self.calls += 1
            self.resumed_messages = list(messages)
            return Assistant(content='已完成。')

    model = ResumingModel()
    sessions = session_factory_for_data_dir(tmp_path)
    companion = CompanionRepository(sessions)
    original = update(PersonaRepository(sessions), '待确认前名字')
    enable_companion(companion)
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        application = client.post('/api/applications', json={'company_name': 'Example', 'position_name': 'Dev'}).json()
        response = client.post('/api/pilot/runtime/v1/turns', json={
            'request_id': str(uuid4()), 'message': '修改投递状态',
            'context_type': 'application', 'context_ref': str(application['id']),
        })
        assert response.status_code == 202, response.text
        submitted = response.json()
        state = _wait_for_status(client, submitted['turn_id'], terminal={'waiting_confirmation', 'failed'})
        assert state['state'] == 'waiting_confirmation', state
        pending = state['terminal']['response']['pending_action']
        versions = assert_binding(sessions, original, True)
        set_enabled(companion, False)
    with sessions() as session:
        assert session.get(Application, application['id']).status == 'applied'
    confirmation = {'request_id': str(uuid4()), 'conversation_id': submitted['conversation_id'],
                    'confirmation_token': pending['confirmation_token'], 'approved': True}
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        state = client.get(f"/api/pilot/runtime/v1/turns/{submitted['turn_id']}").json()
        assert state['state'] == 'waiting_confirmation'
        assert model.calls == 1
        endpoint = f"/api/pilot/runtime/v1/turns/{submitted['turn_id']}/confirm"
        confirmed = client.post(endpoint, json=confirmation)
        assert confirmed.status_code == 202, confirmed.text
        state = _wait_for_status(client, submitted['turn_id'], terminal={'completed', 'failed'})
        assert state['state'] == 'completed', state
        assert client.post(endpoint, json=confirmation).json()['replayed'] is True
    assert model.calls == 2
    assert_ordinary(model.resumed_messages)
    assert assert_binding(sessions, original, True) == versions
    with sessions() as session:
        assert session.get(Application, application['id']).status == 'interview'
