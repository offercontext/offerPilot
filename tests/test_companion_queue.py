"""Companion delivery uses the existing durable publication boundary."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from offerpilot.companion.contracts import CompanionEventCommand, CompanionPresenceCommand
from offerpilot.companion.repository import CompanionRepository
from offerpilot.companion.models import CompanionPresence
from offerpilot.db import init_database
from offerpilot.proactive.models import ProactiveJob
from offerpilot.proactive.repository import ProactiveRepository

NOW = datetime(2026, 10, 21, 4, tzinfo=timezone.utc).timestamp()


def event(repo, name, now=NOW, **extra):
    view = repo.read(now)
    return repo.apply_event(CompanionEventCommand(
        identity=view['identity'], expected_revision=view['revision'], command_id=uuid4(),
        event=name, **extra,
    ), now)


def presence(repo, client, now=NOW, **flags):
    view = repo.read(now)
    with repo.sessions() as session:
        row = session.get(CompanionPresence, client)
        sequence = row.last_sequence + 1 if row else 0
    return repo.presence(CompanionPresenceCommand(
        identity=view['identity'], client_id=client, sequence=sequence, **flags,
    ), now)


@pytest.fixture
def life(tmp_path):
    sessions = init_database(tmp_path / 'life.db')
    repo = CompanionRepository(sessions)
    client = str(uuid4())
    view = event(repo, 'app_open')
    presence(repo, client)
    return sessions, repo, ProactiveRepository(sessions), client, view


def test_two_windows_deliver_once_and_response_retry_never_replays(life):
    sessions, repo, queue, client, view = life
    client2 = str(uuid4())
    presence(repo, client2)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda cid: queue.take_companion_bid(
            view['identity'], view['revision'], cid, NOW,
        ), [client, client2]))
    assert sum(item is not None for item in results) == 1
    assert queue.take_companion_bid(view['identity'], view['revision'], client, NOW + 1) is None
    assert repo.read(NOW)['revision'] == view['revision']
    assert queue.list_jobs() == []  # No virtual story in the job inbox.
    with sessions() as session:
        rows = session.scalars(select(ProactiveJob)).all()
        assert len(rows) == 1 and rows[0].state == 'succeeded'


def test_scheduler_cannot_dispatch_life_and_state_does_not_depend_on_delivery(life):
    _, repo, queue, client, view = life
    assert queue.discover_companion(NOW) == 1
    assert queue.claim('background', NOW) is None
    assert queue.discover_companion(NOW) == 0
    assert repo.read(NOW)['state'] == view['state']
    job_id = queue.claim('worker', NOW, companion_only=True)
    job = queue.begin(job_id, 'worker', NOW)
    assert job is not None
    queue.fail(job_id, 'worker', unknown=False)
    assert queue.take_companion_bid(view['identity'], view['revision'], client, NOW + 1) is None
    assert repo.read(NOW)['state'] == view['state']


@pytest.mark.parametrize('change', ['pause', 'typing', 'quiet', 'failure', 'work_running', 'awaiting_approval', 'mock_interview'])
def test_publish_rechecks_source_and_suppression(life, change):
    sessions, repo, queue, client, view = life
    queue.discover_companion(NOW)
    job_id = queue.claim('worker', NOW, companion_only=True)
    job = queue.begin(job_id, 'worker', NOW)
    assert job is not None
    if change == 'pause':
        event(repo, 'pause', NOW + 1, activity_id=view['state']['activity']['id'])
    else:
        presence(repo, client, NOW + 1, **{change: True})
    assert not queue.publish(job_id, 'worker', job['turn_id'], job['execution_generation'], 'unsafe model text', NOW + 1, delivery_client_id=client)
    with sessions() as session:
        row = session.get(ProactiveJob, job_id)
        assert row.result_text == '' and row.published_at is None


def test_deterministic_source_text_only_and_stale_request_is_silent(life):
    sessions, repo, queue, client, view = life
    assert queue.take_companion_bid(view['identity'], view['revision'] - 1, client, NOW) is None
    assert queue.take_companion_bid(view['identity'], view['revision'], str(uuid4()), NOW) is None
    queue.discover_companion(NOW)
    job_id = queue.claim('worker', NOW, companion_only=True)
    job = queue.begin(job_id, 'worker', NOW)
    assert queue.publish(job_id, 'worker', job['turn_id'], job['execution_generation'], 'I watched a real video', NOW, delivery_client_id=client)
    with sessions() as session:
        assert session.get(ProactiveJob, job_id).result_text == job['source']['text']
    assert not queue.publish(job_id, 'worker', job['turn_id'], job['execution_generation'], 'again', NOW, delivery_client_id=client)


def test_inactive_or_expired_candidate_never_delivers(life):
    _, repo, queue, client, view = life
    presence(repo, client, NOW + 1, visible=False)
    assert queue.take_companion_bid(view['identity'], view['revision'], client, NOW + 1) is None
    presence(repo, client, NOW + 3601)
    assert queue.take_companion_bid(view['identity'], view['revision'], client, NOW + 3601) is None
    assert repo.read(NOW + 3601)['revision'] == view['revision']


def test_missing_usage_fails_closed_instead_of_publishing_without_session_guard(life):
    from offerpilot.companion.models import CompanionUsageSession

    sessions, _, queue, client, _ = life
    queue.discover_companion(NOW)
    job_id = queue.claim('worker', NOW, companion_only=True)
    job = queue.begin(job_id, 'worker', NOW)
    with sessions() as session:
        session.delete(session.get(CompanionUsageSession, 1))
        session.commit()
    assert not queue.publish(job_id, 'worker', job['turn_id'], job['execution_generation'],
                             job['source']['text'], NOW, delivery_client_id=client)


def add_work_event(sessions, *, due_at=NOW + 3600):
    from offerpilot.models import Application, ApplicationEvent

    with sessions() as session:
        application = Application(company_name='Acme', position_name='Engineer',
            updated_at=datetime.fromtimestamp(NOW, timezone.utc))
        session.add(application)
        session.flush()
        work_event = ApplicationEvent(application_id=application.id, event_type='interview',
            status='todo', scheduled_at=datetime.fromtimestamp(due_at, timezone.utc))
        session.add(work_event)
        session.commit()
        return application.id, work_event.id


def enable_work_reminders(queue, application_ids, *, enabled=True):
    from offerpilot.proactive.contracts import ProactivePolicy, ProactivePolicyUpdate

    queue.update_settings(ProactivePolicyUpdate(
        expected_revision=queue.settings()['revision'], confirmed=True,
        settings=ProactivePolicy(enabled=enabled, reminders_enabled=True,
            application_ids=application_ids, max_reminders_per_day=1,
            quiet_start_hour=0, quiet_end_hour=0),
    ))


def test_candidate_reads_and_life_bid_routes_remain_separate(life, monkeypatch):
    from unittest.mock import Mock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from offerpilot.proactive.api import register_proactive_routes

    sessions, repo, queue, client_id, view = life
    application_id, event_id = add_work_event(sessions)
    monkeypatch.setattr('offerpilot.proactive.api.time.time', lambda: NOW)
    monkeypatch.setattr('offerpilot.proactive.repository.time', lambda: NOW)
    app = FastAPI()
    register_proactive_routes(app, sessions, Mock(), Mock())
    with TestClient(app) as client:
        candidates = client.get('/api/reminders/candidates?timezone=UTC')
        assert candidates.status_code == 200
        assert [item['id'] for item in candidates.json()['items']] == [
            f'application_event:{event_id}:interview_preparation',
        ]
        assert candidates.json()['items'][0]['application_id'] == application_id
        assert not queue.settings()['settings']['enabled']
        with sessions() as session:
            assert session.scalar(select(ProactiveJob.id)) is None
        delivered = client.post('/api/companion/bid', json={
            'identity': view['identity'], 'expected_revision': view['revision'],
            'client_id': client_id,
        })
        assert delivered.status_code == 200
        item = delivered.json()['item']
        assert item['kind'] == 'companion_life'
        assert item['source_identity'] == view['identity']
        assert item['source_revision'] == view['revision']
        assert not {'candidate_id', 'subject_type', 'subject_id', 'reason_code'} & item.keys()
        assert client.get('/api/proactive/jobs').json()['items'] == []
        assert client.get('/api/reminders/candidates?timezone=UTC').json() == candidates.json()
    assert repo.read(NOW)['revision'] == view['revision']


def test_life_delivery_does_not_spend_work_reminder_budget(life):
    sessions, _, queue, client, view = life
    app_id, _ = add_work_event(sessions)
    second_app_id, _ = add_work_event(sessions)
    enable_work_reminders(queue, [app_id, second_app_id])
    assert queue.take_companion_bid(view['identity'], view['revision'], client, NOW) is not None
    assert queue.discover(NOW) == 2
    first_id = queue.claim('work-one', NOW)
    first = queue.begin(first_id, 'work-one', NOW)
    second_id = queue.claim('work-two', NOW)
    second = queue.begin(second_id, 'work-two', NOW)
    assert first is not None and second is not None
    assert first['subject_type'] == second['subject_type'] == 'application_event'
    assert queue.publish(first_id, 'work-one', first['turn_id'], first['execution_generation'],
        'Current work reminder', NOW + 1)
    assert not queue.publish(second_id, 'work-two', second['turn_id'], second['execution_generation'],
        'Over-budget work reminder', NOW + 1)
    jobs = queue.list_jobs()
    assert len(jobs) == 2 and all(job['kind'] != 'companion_life' for job in jobs)
    with sessions() as session:
        assert session.get(ProactiveJob, second_id).error_code == 'daily_reminder_budget'


def test_work_scope_disable_does_not_cancel_pending_life(life):
    sessions, repo, queue, client, view = life
    app_id, _ = add_work_event(sessions)
    enable_work_reminders(queue, [app_id])
    assert queue.discover(NOW) == 1
    assert queue.discover_companion(NOW) == 1
    enable_work_reminders(queue, [app_id], enabled=False)
    assert queue.list_jobs()[0]['state'] == 'cancelled'
    assert queue.take_companion_bid(view['identity'], view['revision'], client, NOW) is not None
    assert repo.read(NOW)['revision'] == view['revision']


def test_current_candidate_expiry_fences_work_without_invalidating_life(life):
    sessions, repo, queue, client, view = life
    app_id, event_id = add_work_event(sessions, due_at=NOW + 1)
    enable_work_reminders(queue, [app_id])
    assert queue.reminder_candidates(NOW)[0]['event_id'] == event_id
    assert queue.discover(NOW) == 1
    job_id = queue.claim('work', NOW)
    job = queue.begin(job_id, 'work', NOW)
    assert job is not None
    assert queue.reminder_candidates(NOW + 1) == []
    assert not queue.publish(job_id, 'work', job['turn_id'], job['execution_generation'],
        'Expired work reminder', NOW + 1)
    assert queue.take_companion_bid(view['identity'], view['revision'], client, NOW + 1) is not None
    assert repo.read(NOW + 1)['revision'] == view['revision']
