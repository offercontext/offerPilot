"""Fresh installations must not start virtual life before an explicit opt-in."""
from uuid import uuid4

import pytest

from offerpilot.companion.contracts import CompanionEventCommand, CompanionPolicy, CompanionSettingsCommand
from offerpilot.companion.repository import CompanionRepository, companion_policy, companion_snapshot
from offerpilot.db import init_database


def test_fresh_state_and_missing_row_are_disabled_and_wakes_do_nothing(tmp_path):
    sessions = init_database(tmp_path / 'data.db')
    assert CompanionPolicy().enabled is False
    with sessions() as session:
        assert companion_policy(session)[1].enabled is False
        assert companion_snapshot(session, 100)['settings']['enabled'] is False
    repo = CompanionRepository(sessions)
    before = repo.read(100)
    assert before['settings']['enabled'] is False
    for kind in ('app_open', 'chat_end', 'work_completed', 'user_interaction'):
        after = repo.apply_event(CompanionEventCommand(
            identity=before['identity'], expected_revision=before['revision'],
            command_id=uuid4(), event=kind), 200)
        assert after['changed'] is False
        assert after['state']['activity'] is None
        assert after['recent_events'] == []
    assert CompanionRepository(sessions).read(200)['settings']['enabled'] is False


@pytest.mark.parametrize('enabled', [False, True])
def test_saved_choice_survives_restart_and_reset(tmp_path, enabled):
    from offerpilot.companion.contracts import CompanionResetCommand

    sessions = init_database(tmp_path / 'data.db')
    repo = CompanionRepository(sessions)
    before = repo.read(100)
    saved = repo.update_settings(CompanionSettingsCommand(
        identity=before['identity'], expected_revision=before['revision'], command_id=uuid4(),
        settings=CompanionPolicy(enabled=enabled, proactivity='less', hide_status=True)), 100)
    restarted = CompanionRepository(sessions)
    assert restarted.read(100)['settings'] == saved['settings']
    reset = restarted.reset(CompanionResetCommand(
        identity=saved['identity'], expected_revision=saved['revision'], command_id=uuid4(), confirmed=True), 200)
    assert reset['settings'] == saved['settings']


@pytest.mark.parametrize('enabled', [False, True])
def test_legacy_persisted_settings_are_not_reinterpreted(tmp_path, enabled):
    """Old automatic True is indistinguishable from opt-in, so preserve it."""
    import json
    from offerpilot.companion.models import CompanionState

    path = tmp_path / 'data.db'
    sessions = init_database(path)
    CompanionRepository(sessions)
    legacy = json.dumps({'enabled': enabled, 'proactivity': 'default', 'hide_status': False})
    with sessions() as session:
        row = session.get(CompanionState, 1)
        row.settings_json = legacy
        session.commit()
    sessions.kw['bind'].dispose()
    restarted = init_database(path)
    assert CompanionRepository(restarted).read()['settings']['enabled'] is enabled
    with restarted() as session:
        assert session.get(CompanionState, 1).settings_json == legacy


def test_user_can_enable_then_disable_without_losing_activity(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from offerpilot.companion.api import register_companion_routes
    from offerpilot.companion.context import read_companion_context
    from offerpilot.companion.repository import companion_source, companion_publish_guard
    import sqlite3

    path = tmp_path / 'data.db'
    sessions = init_database(path)
    repo = CompanionRepository(sessions)
    app = FastAPI()
    register_companion_routes(app, sessions)
    with TestClient(app) as client:
        initial = client.get('/api/companion').json()
        assert initial['settings']['enabled'] is False
        def toggle(view, enabled):
            response = client.put('/api/companion/settings', json={
                'identity': view['identity'], 'expected_revision': view['revision'],
                'command_id': str(uuid4()), 'settings': {**view['settings'], 'enabled': enabled},
            })
            assert response.status_code == 200, response.text
            return response.json()
        enabled = toggle(initial, True)
        started = repo.apply_event(CompanionEventCommand(
            identity=enabled['identity'], expected_revision=enabled['revision'],
            command_id=uuid4(), event='app_open'), 100)
        assert started['state']['activity'] is not None
        disabled = toggle(started, False)
        assert disabled['state'] == started['state']
        with sessions() as session:
            assert companion_source(session) is None
            assert companion_publish_guard(session, 101) is False
        with sqlite3.connect(path) as connection:
            assert read_companion_context(connection, '你的小日常怎么样了？', context_type='workspace') is None
        assert toggle(disabled, True)['state'] == started['state']


def test_fresh_application_http_wakes_require_explicit_opt_in(tmp_path):
    """Exercise real application startup and routes, not only the repository."""
    from fastapi.testclient import TestClient
    from offerpilot.api import create_app

    with TestClient(create_app(data_dir=tmp_path)) as client:
        initial_response = client.get('/api/companion')
        assert initial_response.status_code == 200
        initial = initial_response.json()
        assert initial['settings']['enabled'] is False
        for event in ('app_open', 'chat_end', 'work_completed', 'user_interaction'):
            response = client.post('/api/companion/events', json={
                'identity': initial['identity'], 'expected_revision': initial['revision'],
                'command_id': str(uuid4()), 'event': event,
            })
            assert response.status_code == 200, response.text
            after = response.json()
            assert after['changed'] is False
            assert after['revision'] == initial['revision']
            assert after['state']['activity'] is None
            assert after['recent_events'] == []
        final = client.get('/api/companion').json()
        assert final['settings']['enabled'] is False
        assert final['state'] == initial['state']
