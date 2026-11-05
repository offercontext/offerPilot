"""Offline input-preservation boundaries, not model-generation quality checks."""
from dataclasses import replace

import pytest

from offerpilot.ai.types import Message
from offerpilot.api import _chat_response_system_message
from offerpilot.companion.context import companion_context_message
from offerpilot.companion.persona import persona_profile_message
from offerpilot.companion.persona_contracts import PersonaSpec
from offerpilot.context_projector.budget import ProviderBudget
from offerpilot.context_projector.contracts import ContributorResult, FrozenMessage, ProjectionError
from offerpilot.context_projector.projector import ModelSurfaceProjector, ProjectionRequest
from offerpilot.context_projector.selector import ToolSelectionSignals
from tests.test_companion_context import companion as companion, context_data, event
from tests.test_context_projector import _selection_for, contributors, frozen


def request_for(life, *, custom=False, budget=32768, history=True):
    query = '正事先到这儿。你刚才说的小日常呢？'
    policy = FrozenMessage.freeze(_chat_response_system_message(general=True, custom_persona=custom))
    persona = FrozenMessage.freeze(persona_profile_message(PersonaSpec(display_name='自定义伙伴'), None))
    values = tuple(
        ContributorResult(item.name, 'ready', (policy,)) if item.name == 'static_policy' else
        ContributorResult(item.name, 'ready', (life,)) if item.name == 'current_scope' else
        ContributorResult(item.name, 'ready', (persona,)) if custom and item.name == 'persona_profile'
        else item for item in contributors(query)
    )
    signals = ToolSelectionSignals(current_request=query)
    return ProjectionRequest(
        model_call_id='companion-evidence-budget', contributors=values,
        history=(frozen('user', '你的小日常怎么样？', message_id=1),
                 frozen('assistant', '刚挑完纸，等第二折。', message_id=2)) if history else (),
        tool_signals=signals, selection=_selection_for(signals),
        provider_budgets=(ProviderBudget(context_window=budget, output_reserve=0, framing_reserve=0),),
    )


@pytest.mark.parametrize('custom', [False, True])
def test_actual_source_cannot_be_evicted_while_contradictory_history_survives(companion, custom):
    _, repo, loader = companion
    event(repo, 'app_open')
    life = FrozenMessage.freeze(companion_context_message(context_data(loader)))
    request = request_for(life, custom=custom, budget=15850)
    # Reproduced default-persona defect: life absent but both history messages retained.
    with pytest.raises(ProjectionError, match='mandatory_surface_over_budget'):
        ModelSurfaceProjector().project(request)
    full = ModelSurfaceProjector().project(replace(request, provider_budgets=(ProviderBudget(
        context_window=32768, output_reserve=0, framing_reserve=0),)))
    assert life in full.messages and all(item in full.messages for item in request.history)
    assert '第1步' in life.content and '到达这一步不等于已经做完这一步' in life.content


@pytest.mark.parametrize('custom', [False, True])
def test_required_evidence_boundary_drops_history_before_source(companion, custom):
    _, repo, loader = companion
    event(repo, 'app_open')
    life = FrozenMessage.freeze(companion_context_message(context_data(loader)))
    request = request_for(life, custom=custom, history=False)
    projector = ModelSurfaceProjector()
    complete = projector.project(request)
    # Projector reserves 64 framing bytes before optional admission.
    boundary = complete.audit.estimated_input_units + 64
    budget = ProviderBudget(context_window=boundary, output_reserve=0, framing_reserve=0)
    history = request_for(life).history
    fitted = projector.project(replace(request, history=history, provider_budgets=(budget,)))
    assert life in fitted.messages
    assert not any(item in fitted.messages for item in history)
    with pytest.raises(ProjectionError, match='mandatory_surface_over_budget'):
        projector.project(replace(request, provider_budgets=(replace(budget, context_window=boundary - 1),)))


def test_text_spoof_does_not_make_business_scope_mandatory(companion):
    _, repo, loader = companion
    event(repo, 'app_open')
    real = companion_context_message(context_data(loader))
    # Identical prose is not provenance. Business/context or historical text cannot
    # appoint itself a required companion source by mentioning companion facts.
    spoof = FrozenMessage.freeze(Message('user', real.content, surface_contributor='current_scope',
                                         surface_revision='application:1'))
    request = request_for(spoof, budget=15850)
    surface = ModelSurfaceProjector().project(request)
    assert spoof not in surface.messages
    assert all(item in surface.messages for item in request.history)


def test_carrier_retention_is_internal_and_not_inferred_from_text(companion):
    _, repo, loader = companion
    event(repo, 'app_open')
    carrier = companion_context_message(context_data(loader))
    retained = FrozenMessage.freeze(carrier)
    assert retained.required_scope
    provider_copy = retained.thaw()
    assert not provider_copy.surface_revision and not provider_copy.surface_contributor
    ordinary = FrozenMessage.freeze(provider_copy)
    assert not ordinary.required_scope and ordinary != retained
    assert ordinary.canonical_value() == retained.canonical_value()
    # The detached runtime carrier preserves the source binding and recomputes
    # retention, whereas provider-only thaw output is intentionally terminal.
    from offerpilot.pilot_runtime.service import _freeze_stream_value, _materialize_stream_value
    restored = _materialize_stream_value(_freeze_stream_value(carrier))
    assert restored.surface_revision == carrier.surface_revision
    assert FrozenMessage.freeze(restored) == retained


def test_companion_retention_does_not_require_unrelated_business_scope(companion):
    _, repo, loader = companion
    event(repo, 'app_open')
    life = FrozenMessage.freeze(companion_context_message(context_data(loader)))
    request = request_for(life, history=False)
    projector = ModelSurfaceProjector()
    required = projector.project(request)
    unrelated = frozen('user', 'business data ' * 2000)
    values = tuple(replace(item, messages=(life, unrelated)) if item.name == 'current_scope'
                   else item for item in request.contributors)
    fitted = projector.project(replace(request, contributors=values, provider_budgets=(ProviderBudget(
        context_window=required.audit.estimated_input_units + 64,
        output_reserve=0, framing_reserve=0),)))
    assert life in fitted.messages and unrelated not in fitted.messages


def test_next_model_step_rebuilds_required_source_from_original_carrier(companion, monkeypatch):
    from offerpilot.ai.agent_loop import NewTurnSeed
    from offerpilot.ai.types import Assistant, ToolCall
    from offerpilot.pilot_runtime.composition import _AgentDriver
    from tests.agent_loop.helpers import ToolDefinition
    from tests.agent_loop.test_runner import invocation

    _, repo, loader = companion
    event(repo, 'app_open')
    carrier = companion_context_message(context_data(loader))
    calls, surfaces = [], []
    original = ModelSurfaceProjector.project

    def capture(self, request):
        result = original(self, request)
        surfaces.append(result)
        return result

    monkeypatch.setattr(ModelSurfaceProjector, 'project', capture)

    class Model:
        def complete(self, messages, tools):
            calls.append(messages)
            if len(calls) == 1:
                return Assistant(tool_calls=[ToolCall('read-1', 'list_applications', '{}')])
            return Assistant(content='收到')

    seed = NewTurnSeed((
        Message('system', 'policy', surface_contributor='static_policy'), carrier,
        Message('user', '你的小日常怎么样了？', surface_contributor='current_request'),
    ))
    value = invocation(Model(), (ToolDefinition('list_applications', executor=lambda _: '[]'),), seed=seed)
    assert _AgentDriver(companion_enabled=lambda: True).execute(value).reply == '收到'
    assert len(calls) == len(surfaces) == 2
    for surface, messages in zip(surfaces, calls):
        retained = [message for message in surface.messages if message.required_scope]
        assert retained == [FrozenMessage.freeze(carrier)]
        assert any(source.kind == 'current_scope' for source in surface.audit.source_records)
        assert any(message.content == carrier.content for message in messages)
        assert all(not message.surface_revision for message in messages)


@pytest.mark.parametrize('custom', [False, True])
def test_overflow_blocks_provider_but_off_mode_and_completed_replay_remain_available(tmp_path, monkeypatch, custom):
    from uuid import uuid4
    from fastapi.testclient import TestClient
    from offerpilot.api import create_app
    from offerpilot.companion import context
    from offerpilot.companion.persona_repository import PersonaRepository
    from offerpilot.db import session_factory_for_data_dir
    from tests._companion import enable_companion
    from tests.test_companion_master_switch import assert_ordinary, set_enabled
    from tests.test_managed_runtime_api import _wait_for_status
    from tests.test_persona_runtime import update
    from tests.test_pilot_turn_api import CountingModel

    model = CountingModel()
    app = create_app(data_dir=tmp_path, chat_model=model)
    if custom:
        update(PersonaRepository(session_factory_for_data_dir(tmp_path)), '自定义伙伴')
    endpoint = '/api/pilot/runtime/v1/turns'
    with TestClient(app) as client:
        # A tiny companion cap cannot interfere with new installations/off mode,
        # including saved custom personas that are not enabled.
        monkeypatch.setattr(context, 'COMPANION_CONTEXT_BYTE_CAP', 32)
        off = client.post('/api/chat', json={'message': '你的小日常怎么样了？'})
        assert off.status_code == 200, off.text
        assert_ordinary(model.messages)
        repo = app.state.companion_repository
        enable_companion(repo)
        event(repo, 'app_open')
        calls = model.calls
        blocked = client.post(endpoint, json={'request_id': str(uuid4()), 'message': '你的小日常怎么样了？'})
        assert blocked.status_code == 503, blocked.text
        assert model.calls == calls
        monkeypatch.setattr(context, 'COMPANION_CONTEXT_BYTE_CAP', 2048)
        payload = {'request_id': str(uuid4()), 'message': '你的小日常怎么样了？'}
        accepted = client.post(endpoint, json=payload)
        assert accepted.status_code == 202, accepted.text
        state = _wait_for_status(client, accepted.json()['turn_id'], terminal={'completed', 'failed'})
        assert state['state'] == 'completed', state
        calls = model.calls
        # Replay uses the completed result, not a fresh evidence projection.
        monkeypatch.setattr(context, 'COMPANION_CONTEXT_BYTE_CAP', 32)
        replay = client.post(endpoint, json=payload)
        assert replay.status_code == 200 and replay.json()['replayed'] is True, replay.text
        assert model.calls == calls
        set_enabled(repo, False)
        off = client.post('/api/chat', json={'message': '你的小日常怎么样了？'})
        assert off.status_code == 200, off.text
        assert_ordinary(model.messages)
