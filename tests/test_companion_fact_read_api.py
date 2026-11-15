"""Production HTTP composition across sync, stream and managed execution."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from offerpilot.api import create_app
from offerpilot.companion.models import CompanionFactReadReceipt
from offerpilot.companion.repository import CompanionRepository
from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import ChatMessage, PilotTurnMessage
from tests._companion import enable_companion
from tests.test_companion_context import event
from tests.test_managed_runtime_api import _wait_for_status
from tests.test_pilot_turn_api import CountingModel

ENDPOINTS = ['/api/chat', '/api/chat/stream', '/api/pilot/runtime/v1/turns']


@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('message', ['我们的小船叫什么？', '你的小日常怎么样了？',
    '正事先到这儿。你刚才说的小日常呢？',
    '你的小船折到哪了？', '我们的小船叫什么来着？？',
    '我们的小船叫什么呀？', '我们的小船叫什么！',
    '正事先到这儿\n你刚才说的小日常呢？'])
def test_fact_route_commits_before_delivery_and_never_calls_provider(tmp_path, endpoint, message):
    model = CountingModel()
    app = create_app(data_dir=tmp_path, chat_model=model)
    sessions = session_factory_for_data_dir(tmp_path)
    repo = CompanionRepository(sessions)
    enable_companion(repo)
    event(repo, 'app_open')
    before = repo.read()
    payload = {'request_id': str(uuid4()), 'message': message}
    with TestClient(app) as client:
        response = client.post(endpoint, json=payload)
        assert response.status_code in (200, 202), response.text
        if endpoint.endswith('/turns'):
            status = _wait_for_status(client, response.json()['turn_id'], terminal={'completed', 'failed'})
            assert status['state'] == 'completed', status
        with sessions() as session:
            receipts = session.scalars(select(CompanionFactReadReceipt)).all()
            assert len(receipts) == 1
            receipt = receipts[0]
            assistant = session.get(ChatMessage, receipt.assistant_message_id)
            assert assistant is not None
            assert session.get(PilotTurnMessage, assistant.id).turn_id == receipt.turn_id
            saved = assistant.content
        assert saved
        assert model.calls == 0
        repeated = client.post(endpoint, json=payload)
        assert repeated.status_code in (200, 202), repeated.text
        with sessions() as session:
            assert len(session.scalars(select(CompanionFactReadReceipt)).all()) == 1
            assert len(session.scalars(select(ChatMessage).where(ChatMessage.role == 'assistant')).all()) == 1
        if endpoint.endswith('/stream'):
            assert '"supports_delta":false' in response.text.replace(' ', '')
            assert 'event: delta' not in response.text
        assert model.calls == 0
    assert repo.read() == before


@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('message,extra', [
    ('你在做什么？', {}),
    ('帮我改简历。我们的小船叫什么？', {}),
    ('我想放空一会儿，聊聊你的小日常吧', {}),
    ('我们的小船叫什么？', {'page_context': {'view': 'board', 'label': '看板'}}),
    ('你的小船折到哪了？\n然后帮我改简历', {}),
    ('我们的小船叫\n什么？', {}),
])
def test_ordinary_or_mixed_context_stays_on_original_provider_route(tmp_path, endpoint, message, extra):
    model = CountingModel()
    app = create_app(data_dir=tmp_path, chat_model=model)
    sessions = session_factory_for_data_dir(tmp_path)
    repo = CompanionRepository(sessions)
    enable_companion(repo)
    event(repo, 'app_open')
    with TestClient(app) as client:
        response = client.post(endpoint, json={'request_id': str(uuid4()), 'message': message, **extra})
        assert response.status_code in (200, 202), response.text
        if endpoint.endswith('/turns'):
            status = _wait_for_status(client, response.json()['turn_id'], terminal={'completed', 'failed'})
            assert status['state'] == 'completed', status
    assert model.calls == 1
    with sessions() as session:
        assert session.scalars(select(CompanionFactReadReceipt)).all() == []


@pytest.mark.parametrize('endpoint', ['/api/chat/stream', '/api/pilot/runtime/v1/turns'])
@pytest.mark.parametrize('invalidate', [False, True])
def test_transport_event_observes_atomic_commit_or_no_assistant(tmp_path, monkeypatch, endpoint, invalidate):
    import offerpilot.pilot_runtime.service as runtime_service
    from offerpilot.pilot_runtime.contracts import AssistantMessageEvent, CompletedEvent, MessageOutcome
    from offerpilot.pilot_runtime.managed_execution import RuntimeExecutionManager
    from offerpilot.companion.fact_read import CompanionFactReadService
    from offerpilot.companion.models import CompanionState
    model = CountingModel()
    app = create_app(data_dir=tmp_path, chat_model=model)
    sessions = session_factory_for_data_dir(tmp_path)
    repo = CompanionRepository(sessions)
    enable_companion(repo)
    event(repo, 'app_open')
    observed = []
    emit = runtime_service.emit_runtime_event
    def inspect_event(sink, item):
        if isinstance(item, AssistantMessageEvent):
            with sessions() as session:
                receipt = session.scalar(select(CompanionFactReadReceipt))
                observed.append(receipt is not None and
                    session.get(ChatMessage, receipt.assistant_message_id).content == item.message and
                    session.get(PilotTurnMessage, receipt.assistant_message_id).turn_id == receipt.turn_id)
        return emit(sink, item)
    monkeypatch.setattr(runtime_service, 'emit_runtime_event', inspect_event)
    managed_emit = RuntimeExecutionManager._emit_event_locked
    def inspect_managed(manager, handle, item, *, force):
        if isinstance(item, CompletedEvent) and isinstance(item.response, MessageOutcome):
            with sessions() as session:
                receipt = session.scalar(select(CompanionFactReadReceipt))
                observed.append(receipt is not None and
                    session.get(ChatMessage, receipt.assistant_message_id).content == item.response.message and
                    session.get(PilotTurnMessage, receipt.assistant_message_id).turn_id == receipt.turn_id)
        return managed_emit(manager, handle, item, force=force)
    monkeypatch.setattr(RuntimeExecutionManager, '_emit_event_locked', inspect_managed)
    if invalidate:
        execute = CompanionFactReadService.execute
        def change_then_execute(service, *args, **kwargs):
            with sessions() as session:
                state = session.get(CompanionState, 1)
                state.revision += 1
                session.commit()
            return execute(service, *args, **kwargs)
        monkeypatch.setattr(CompanionFactReadService, 'execute', change_then_execute)
    with TestClient(app) as client:
        response = client.post(endpoint, json={'request_id': str(uuid4()), 'message': '我们的小船叫什么？'})
        if endpoint.endswith('/turns'):
            assert response.status_code == 202, response.text
            status = _wait_for_status(client, response.json()['turn_id'], terminal={'completed', 'failed'})
            assert status['state'] == ('failed' if invalidate else 'completed'), status
    assert observed == [] if invalidate else observed and all(observed)
    assert model.calls == 0
    with sessions() as session:
        assert len(session.scalars(select(CompanionFactReadReceipt)).all()) == (0 if invalidate else 1)


@pytest.mark.parametrize('endpoint', ENDPOINTS)
def test_frozen_fact_disabled_before_execution_reports_mode_without_persona_leak(tmp_path, monkeypatch, endpoint):
    from offerpilot.pilot_runtime.service import PilotRuntime
    from offerpilot.companion.persona_repository import PersonaRepository
    from tests.test_persona_runtime import update
    from tests.test_companion_master_switch import set_enabled
    model = CountingModel()
    app = create_app(data_dir=tmp_path, chat_model=model)
    sessions = session_factory_for_data_dir(tmp_path)
    repo = CompanionRepository(sessions)
    persona = PersonaRepository(sessions)
    update(persona, '不应泄漏的名字')
    original_persona = persona.read()
    enable_companion(repo)
    event(repo, 'app_open')
    original = PilotRuntime.with_start_ports
    switched = []
    def disable_after_admission(runtime, *args, **kwargs):
        if not switched:
            switched.append(True)
            set_enabled(repo, False)
        return original(runtime, *args, **kwargs)
    monkeypatch.setattr(PilotRuntime, 'with_start_ports', disable_after_admission)
    with TestClient(app) as client:
        response = client.post(endpoint, json={'request_id': str(uuid4()), 'message': '我们的小船叫什么？'})
        if endpoint.endswith('/turns'):
            assert response.status_code == 202, response.text
            status = _wait_for_status(client, response.json()['turn_id'], terminal={'completed', 'failed'})
            assert status['state'] == 'completed', status
        elif endpoint.endswith('/stream'):
            assert '陪伴已关闭，当前已恢复普通 Pilot。' in response.text
        else:
            assert response.status_code == 200, response.text
    assert switched and model.calls == 0 and not model.messages
    assert persona.read() == original_persona
    with sessions() as session:
        assert len(session.scalars(select(CompanionFactReadReceipt)).all()) == 1
        assistants = session.scalars(select(ChatMessage).where(ChatMessage.role == 'assistant')).all()
        assert len(assistants) == 1 and assistants[0].content == '陪伴已关闭，当前已恢复普通 Pilot。'


@pytest.mark.parametrize('endpoint', ENDPOINTS)
def test_fact_shaped_turn_admitted_off_stays_plain_after_enable(tmp_path, monkeypatch, endpoint):
    from offerpilot.pilot_runtime.service import PilotRuntime
    from offerpilot.companion.persona_repository import PersonaRepository
    from tests.test_persona_runtime import update
    from tests.test_companion_master_switch import set_enabled, assert_ordinary
    model = CountingModel()
    app = create_app(data_dir=tmp_path, chat_model=model)
    sessions = session_factory_for_data_dir(tmp_path)
    repo = CompanionRepository(sessions)
    update(PersonaRepository(sessions), '不可提前恢复的人设')
    assert not repo.read()['settings']['enabled']
    original = PilotRuntime.with_start_ports
    switched = []
    def enable_after_admission(runtime, *args, **kwargs):
        if not switched:
            switched.append(True)
            set_enabled(repo, True)
        return original(runtime, *args, **kwargs)
    monkeypatch.setattr(PilotRuntime, 'with_start_ports', enable_after_admission)
    with TestClient(app) as client:
        response = client.post(endpoint, json={'request_id': str(uuid4()), 'message': '我们的小船叫什么？'})
        assert response.status_code in (200, 202), response.text
        if endpoint.endswith('/turns'):
            status = _wait_for_status(client, response.json()['turn_id'], terminal={'completed', 'failed'})
            assert status['state'] == 'completed', status
    assert switched and model.calls == 1
    assert_ordinary(model.messages)
    with sessions() as session:
        assert session.scalars(select(CompanionFactReadReceipt)).all() == []
