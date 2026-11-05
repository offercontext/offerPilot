"""Offline physical provider-boundary tests for revoking companion expression."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from offerpilot.ai.agent_loop import NewTurnSeed
from offerpilot.ai.types import Assistant, Message, ToolCall
from offerpilot.api import create_app
from offerpilot.companion.repository import CompanionRepository
from offerpilot.config import AIProviderProfile
from offerpilot.context_projector.gateway import (
    AgentProviderGatewaySession, FrozenProviderExecutionChain, SingleCandidateAgentTransport,
)
from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import Application
from offerpilot.pilot_runtime.composition import _AgentDriver
from offerpilot.pilot_runtime.errors import RuntimeCancelled
from tests._companion import enable_companion
from tests.agent_loop.helpers import ToolDefinition
from tests.agent_loop.test_runner import invocation
from tests.test_companion_master_switch import set_enabled
from tests.test_managed_runtime_api import _PendingModel, _wait_for_status


def seed(companion):
    return NewTurnSeed((
        Message(role='system', content='你是 OfferPilot。', surface_contributor='static_policy',
                surface_revision='companion-expression' if companion else 'pilot-plain'),
        Message(role='user', content='列出投递', surface_contributor='current_request'),
    ))


@pytest.mark.parametrize('companion', [True, False])
def test_switch_off_after_read_request_blocks_next_provider_only_for_companion(companion):
    enabled = True
    calls = []
    reads = []

    class Model:
        def complete(self, messages, tools):
            nonlocal enabled
            calls.append(list(messages))
            if len(calls) == 1:
                enabled = False
                return Assistant(tool_calls=[ToolCall('read-1', 'list_applications', '{}')])
            return Assistant(content='完成')

    def read(_args):
        reads.append(True)
        return '[]'

    value = invocation(Model(), (ToolDefinition('list_applications', executor=read),), seed=seed(companion))
    driver = _AgentDriver(companion_enabled=lambda: enabled)
    if companion:
        with pytest.raises(RuntimeCancelled):
            driver.execute(value)
        assert len(calls) == 1
    else:
        assert driver.execute(value).reply == '完成'
        assert len(calls) == 2
    assert reads == [True], 'Read execution is not a companion expression side effect'


@pytest.mark.parametrize('companion', [True, False])
def test_switch_off_after_failed_candidate_blocks_fallback_only_for_companion(companion):
    enabled = True
    attempts = []
    chain = FrozenProviderExecutionChain.freeze([
        AIProviderProfile(id='primary', api_key='fake', base_url='https://primary.test/v1'),
        AIProviderProfile(id='fallback', api_key='fake', base_url='https://fallback.test/v1'),
    ])

    def complete(*args):
        raise AssertionError('Agent Loop must use the deferred stream boundary')

    def stream(candidate, messages, tools, emit):
        nonlocal enabled
        attempts.append(candidate.provider_id)
        if candidate.provider_id == 'primary':
            enabled = False
            raise ConnectionError('offline primary failure')
        return Assistant(content='备用完成')

    transport = SingleCandidateAgentTransport(complete, stream)

    class Model:
        def new_agent_provider_session(self):
            return AgentProviderGatewaySession(chain, transport)

        def complete_agent_surface(self, *args, **kwargs):
            raise AssertionError('gateway owns completion')

        def stream_agent_surface(self, *args, **kwargs):
            raise AssertionError('gateway owns streaming')

    value = invocation(Model(), (), seed=seed(companion))
    driver = _AgentDriver(companion_enabled=lambda: enabled)
    if companion:
        with pytest.raises(RuntimeCancelled):
            driver.execute(value)
        assert attempts == ['primary']
    else:
        assert driver.execute(value).reply == '备用完成'
        assert attempts == ['primary', 'fallback']


def test_switch_off_after_approved_write_keeps_commit_but_blocks_companion_continuation(tmp_path, monkeypatch):
    """Switch at the provider callback after a real authorized write commits."""
    sessions = session_factory_for_data_dir(tmp_path)
    companion = CompanionRepository(sessions)
    enable_companion(companion)
    model = _PendingModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        application = client.post('/api/applications', json={'company_name': 'Example', 'position_name': 'Dev'}).json()
        submitted_response = client.post('/api/pilot/runtime/v1/turns', json={
            'request_id': str(uuid4()), 'message': '修改投递状态',
            'context_type': 'application', 'context_ref': str(application['id']),
        })
        assert submitted_response.status_code == 202, submitted_response.text
        submitted = submitted_response.json()
        state = _wait_for_status(client, submitted['turn_id'], terminal={'waiting_confirmation', 'failed'})
        assert state['state'] == 'waiting_confirmation', state
        pending = state['terminal']['response']['pending_action']
    with sessions() as session:
        assert session.get(Application, application['id']).status == 'applied'
    old_init = _AgentDriver.__init__
    switched = []

    def wrapped_init(self, optional_loader=None, companion_enabled=None):
        def switch_after_commit():
            with sessions() as session:
                status = session.get(Application, application['id']).status
            if status == 'interview' and not switched:
                switched.append(status)
                set_enabled(companion, False)
            return companion_enabled()
        old_init(self, optional_loader, switch_after_commit)

    monkeypatch.setattr(_AgentDriver, '__init__', wrapped_init)
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        endpoint = f"/api/pilot/runtime/v1/turns/{submitted['turn_id']}/confirm"
        response = client.post(endpoint, json={
            'request_id': str(uuid4()), 'conversation_id': submitted['conversation_id'],
            'confirmation_token': pending['confirmation_token'], 'approved': True,
        })
        assert response.status_code == 202, response.text
        state = _wait_for_status(client, submitted['turn_id'], terminal={'completed', 'interrupted', 'failed'})
        assert state['state'] == 'interrupted', state
    assert switched == ['interview']
    assert model.calls == 1, 'No physical provider continuation after companion revocation'
    assert companion.read()['settings']['enabled'] is False
    with sessions() as session:
        assert session.get(Application, application['id']).status == 'interview'
