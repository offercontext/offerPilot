"""Additive initialization preserves pre-companion business records."""
import json
import sqlite3
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from offerpilot.companion.contracts import CompanionEventCommand
from offerpilot.companion.models import CompanionEvent, CompanionState
from offerpilot.companion.persona import PERSONA_VERSION
from offerpilot.companion.repository import CompanionConflict, CompanionRepository, MIN_STEP_SECONDS
from offerpilot.db import init_database
from offerpilot.models import Application

TABLES = (
    'companion_state', 'companion_events', 'companion_participation',
    'companion_command_receipts', 'companion_presence', 'companion_usage_session',
)


def test_old_database_upgrade_is_additive_and_restart_preserves_identity(tmp_path):
    path = tmp_path / 'data.db'
    sessions = init_database(path)
    with sessions() as session:
        session.add(Application(company_name='Keep company', position_name='Keep job'))
        session.commit()
    sessions.kw['bind'].dispose()
    # Model the previous schema, which had no companion tables or migration.
    with sqlite3.connect(path) as connection:
        for table in TABLES:
            connection.execute(f'DROP TABLE {table}')
        connection.execute("DELETE FROM schema_migrations WHERE version='0034_companion_life'")
    upgraded = init_database(path)
    first = CompanionRepository(upgraded).read()
    with upgraded() as session:
        row = session.scalar(select(Application))
        assert row.company_name == 'Keep company'
        assert row.position_name == 'Keep job'
    upgraded.kw['bind'].dispose()
    restarted = init_database(path)
    assert CompanionRepository(restarted).read()['identity'] == first['identity']
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT 1 FROM schema_migrations WHERE version='0034_companion_life'").fetchone()
        for table in TABLES:
            assert connection.execute(f'PRAGMA foreign_key_list({table})').fetchall() == []


def test_v11_adds_user_chat_provenance_without_replacing_v1_state(tmp_path):
    path = tmp_path / 'old-v1.db'
    sessions = init_database(path)
    repo = CompanionRepository(sessions)
    first = repo.read()
    started = repo.apply_event(CompanionEventCommand(identity=first['identity'], expected_revision=first['revision'],
                                                   command_id=uuid4(), event='app_open'))
    sessions.kw['bind'].dispose()
    with sqlite3.connect(path) as connection:
        connection.execute('DROP TABLE companion_chat_evidence')
        connection.execute('ALTER TABLE companion_participation DROP COLUMN origin')
        connection.execute("UPDATE companion_state SET persona_version='companion-v1'")
        connection.execute("DELETE FROM schema_migrations WHERE version='0035_companion_chat'")
    upgraded = init_database(path)
    restored = CompanionRepository(upgraded).read()
    assert restored['identity'] == first['identity']
    assert restored['state'] == started['state']
    assert restored['revision'] == started['revision'] + 1
    assert restored['persona_version'] == PERSONA_VERSION
    with upgraded() as session:
        assert session.get(CompanionState, 1).identity == first['identity']
    assert CompanionRepository(upgraded).read()['revision'] == restored['revision']


@pytest.mark.parametrize('previous_persona', ['companion-v1', 'companion-v1.1', PERSONA_VERSION])
@pytest.mark.parametrize('pending_wish', [False, True])
def test_old_activity_wish_backfill_is_conservative_preserves_pending_and_fences_once(
    tmp_path, previous_persona, pending_wish,
):
    sessions = init_database(tmp_path / 'old-wishes.db')
    repo = CompanionRepository(sessions)
    initial = repo.read(1000)
    started = repo.apply_event(CompanionEventCommand(identity=initial['identity'],
        expected_revision=initial['revision'], command_id=uuid4(), event='app_open'), 1000)
    old_life = started['state']
    old_life['activity'].pop('wish_offered')
    if pending_wish:
        old_life['wish'] = {'id': str(uuid4()), 'activity_id': old_life['activity']['id'],
                           'text': '一起给纸小船起个名字'}
    with sessions() as session:
        state = session.get(CompanionState, 1)
        state.life_json = json.dumps(old_life, ensure_ascii=False)
        state.persona_version = previous_persona
        session.commit()
    stale = CompanionEventCommand(identity=started['identity'], expected_revision=started['revision'],
                                  command_id=uuid4(), event='chat_end')
    restored_repo = CompanionRepository(sessions)
    restored = restored_repo.read(1000)
    assert restored['identity'] == started['identity']
    assert restored['persona_version'] == PERSONA_VERSION
    assert restored['revision'] == started['revision'] + 1
    assert restored['recent_events'] == started['recent_events']
    assert restored['state'] == {**old_life, 'activity': {**old_life['activity'], 'wish_offered': True}}
    assert restored['state']['wish'] == old_life['wish']
    assert CompanionRepository(sessions).read(1000) == restored
    with pytest.raises(CompanionConflict, match='revision_conflict'):
        restored_repo.apply_event(stale, 1000 + MIN_STEP_SECONDS)
    for day in (1, 2):
        snapshot = restored_repo.read(1000)
        current = restored_repo.apply_event(CompanionEventCommand(identity=snapshot['identity'],
            expected_revision=snapshot['revision'], command_id=uuid4(), event='chat_end'), 1000 + day * 86400)
        assert current['state']['wish'] == old_life['wish']
        assert current['state']['activity']['wish_offered']
        assert current['state']['activity']['step_index'] == day
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(CompanionEvent)) == 3
        assert session.get(CompanionState, 1).sequence == 1


def test_v13_binding_upgrade_only_backfills_authoritative_current_entity(tmp_path):
    path = tmp_path / 'old-v12.db'
    sessions = init_database(path)
    repo = CompanionRepository(sessions)
    first = repo.read(1000)
    started = repo.apply_event(CompanionEventCommand(identity=first['identity'],
        expected_revision=first['revision'], command_id=uuid4(), event='app_open'), 1000)
    current_id = started['state']['activity']['id']
    historical_id = str(uuid4())
    other_identity = str(uuid4())
    with sessions() as session:
        for identity, activity_id, message in (
            (first['identity'], historical_id, '虚拟小船的旧故事，不可据文字回填'),
            (other_identity, current_id, '另一个身份即使活动 ID 相同也不可混入'),
        ):
            session.add(CompanionEvent(id=str(uuid4()), identity=identity, revision=0,
                category='virtual', event_type='activity_started', activity_id=activity_id,
                template_id=None, text=message, source='deterministic_template', occurred_at=500))
        session.commit()
    sessions.kw['bind'].dispose()
    with sqlite3.connect(path) as connection:
        connection.execute('ALTER TABLE companion_events DROP COLUMN template_id')
        connection.execute("UPDATE companion_state SET persona_version='companion-v1.2'")
        connection.execute("DELETE FROM schema_migrations WHERE version='0036_companion_activity_binding'")
    upgraded = init_database(path)
    restored = CompanionRepository(upgraded).read(1000)
    assert restored['identity'] == started['identity']
    assert restored['state'] == started['state']
    assert restored['revision'] == started['revision'] + 1
    with upgraded() as session:
        events = session.scalars(select(CompanionEvent)).all()
        assert all(event.template_id == ('paper_boat' if event.identity == first['identity']
            and event.activity_id == current_id else None) for event in events)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT 1 FROM schema_migrations WHERE version='0036_companion_activity_binding'").fetchone()
    assert CompanionRepository(upgraded).read(1000) == restored


def test_current_binding_backfill_does_not_overwrite_conflicting_existing_metadata(tmp_path):
    sessions = init_database(tmp_path / 'conflicting-binding.db')
    repo = CompanionRepository(sessions)
    initial = repo.read(1000)
    repo.apply_event(CompanionEventCommand(identity=initial['identity'], expected_revision=initial['revision'],
        command_id=uuid4(), event='app_open'), 1000)
    with sessions() as session:
        event = session.scalar(select(CompanionEvent))
        event.template_id = 'cloud_catalog'
        session.commit()
    before = repo.read(1000)
    assert CompanionRepository(sessions).read(1000) == before
    with sessions() as session:
        assert session.scalar(select(CompanionEvent)).template_id == 'cloud_catalog'


@pytest.mark.parametrize('previous_persona', ['companion-v1.2', 'companion-v1.3', 'companion-v1.4', 'companion-v1.5'])
def test_expression_upgrade_preserves_waiting_life_and_fences_old_window_once(
    tmp_path, previous_persona,
):
    sessions = init_database(tmp_path / 'persona-upgrade.db')
    repo = CompanionRepository(sessions)
    for action in ('app_open', 'join'):
        before = repo.read(1000)
        repo.apply_event(CompanionEventCommand(identity=before['identity'],
            expected_revision=before['revision'], command_id=uuid4(), event=action,
            activity_id=before['state']['activity']['id'] if before['state']['activity'] else None), 1000)
    before = repo.read(1000)
    with sessions() as session:
        session.get(CompanionState, 1).persona_version = previous_persona
        session.commit()
    restored_repo = CompanionRepository(sessions)
    restored = restored_repo.read(1000)
    assert restored['persona_version'] == PERSONA_VERSION
    assert restored['identity'] == before['identity']
    assert restored['revision'] == before['revision'] + 1
    assert restored['state'] == before['state']
    assert restored['state']['activity']['status'] == 'waiting_shared'
    assert restored['shared_participation'] == before['shared_participation']
    assert restored['recent_events'] == before['recent_events']
    assert CompanionRepository(sessions).read(1000) == restored
    with pytest.raises(CompanionConflict, match='revision_conflict'):
        restored_repo.apply_event(CompanionEventCommand(identity=before['identity'],
            expected_revision=before['revision'], command_id=uuid4(), event='pause',
            activity_id=before['state']['activity']['id']), 1001)
