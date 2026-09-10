from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import text
import pytest

from offerpilot.api import create_app
from offerpilot.repositories.application_jd_versions import ApplicationJDService
from offerpilot.db import init_database
from offerpilot.repositories.application_creation import ApplicationCreationService
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository


def payload(**changes):
    return {
        "company_name": "示例公司", "position_name": "工程师", "status": "pending",
        "idempotency_key": "create-request-00000001",
        "initial_jd": {"jd_text": "  15–25K·14薪\n200元/天 ✨\n", "source_url": "https://source.invalid/a"},
        **changes,
    }


def test_atomic_creation_restart_replay_and_conflict(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    first = client.post('/api/applications', json=payload())
    assert first.status_code == 201
    result = first.json()
    assert result['jd_version_id'] > 0
    restarted = TestClient(create_app(data_dir=tmp_path))
    replay = restarted.post('/api/applications', json=payload())
    assert replay.status_code == 200
    assert replay.json() == result
    assert restarted.post('/api/applications', json=payload(notes='changed')).status_code == 409
    current = restarted.get(f"/api/applications/{result['id']}/job-description").json()['current']
    assert current['jd_text'] == payload()['initial_jd']['jd_text']
    assert current['created_at'].endswith('Z')
    assert current['source_url'] != result['job_url']
    client.delete(f"/api/applications/{result['id']}")
    assert restarted.post('/api/applications', json=payload()).json() == result
    assert restarted.get('/api/applications').json() == []


def test_jd_failure_rolls_back_everything(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError('injected JD failure')
    monkeypatch.setattr(ApplicationJDService, 'create_version', fail)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post('/api/applications', json=payload()).status_code == 500
    with app.state.db_engine.connect() as conn:
        for table in ('applications', 'application_jd_versions', 'application_creation_receipts'):
            assert conn.scalar(text(f'SELECT count(*) FROM {table}')) == 0


def test_concurrent_same_key_creates_once(tmp_path):
    app = create_app(data_dir=tmp_path)
    def submit(_):
        return TestClient(app).post('/api/applications', json=payload())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert sorted(r.status_code for r in results) == [200, 201]
    assert results[0].json() == results[1].json()
    with app.state.db_engine.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM applications')) == 1
        assert conn.scalar(text('SELECT count(*) FROM application_jd_versions')) == 1


def test_duplicate_rules_and_workspace_identity(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    first = client.post('/api/applications', json=payload(initial_jd=None, company_name='ＡＣＭＥ', position_name='Dev Engineer')).json()
    response = client.get('/api/applications/duplicates', params={'company_name': 'acme', 'position_name': 'dev'})
    assert response.status_code == 200
    assert [r['id'] for r in response.json()['items']] == [first['id']]
    assert client.get('/api/applications/duplicates', params={'company_name': 'a', 'position_name': 'dev'}).json()['items'] == []
    scope = client.get('/api/applications/creation-context').json()
    assert TestClient(create_app(data_dir=tmp_path)).get('/api/applications/creation-context').json() == scope
    assert TestClient(create_app(data_dir=tmp_path / 'other')).get('/api/applications/creation-context').json() != scope
    assert client.post('/api/applications', json=payload(expected_scope_id='other')).status_code == 409


def test_whitespace_and_empty_jd_validation(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    assert client.post('/api/applications', json=payload(company_name='  ')).status_code == 400
    assert client.post('/api/applications', json=payload(initial_jd={'jd_text': '  '})).status_code == 422
    assert client.post('/api/applications', json=payload(idempotency_key=None)).status_code == 422
    result = client.post('/api/applications', json=payload(initial_jd=None)).json()
    assert result['jd_version_id'] is None
    assert client.get(f"/api/applications/{result['id']}/job-description").json() == {'current': None}


def test_fingerprint_defaults_key_order_and_raw_text(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    original = payload()
    original.pop('status')
    created = client.post('/api/applications', json=original)
    equivalent = dict(reversed(list(original.items())))
    equivalent.update(status='applied', notes='', closed_reason='', job_url='')
    equivalent['initial_jd'] = {'source_url': ' https://source.invalid/a ', 'jd_text': original['initial_jd']['jd_text']}
    assert client.post('/api/applications', json=equivalent).json() == created.json()
    equivalent['initial_jd']['jd_text'] = equivalent['initial_jd']['jd_text'].strip()
    assert client.post('/api/applications', json=equivalent).status_code == 409


def test_duplicate_ranking_limit_query_and_soft_delete(tmp_path):
    sessions = init_database(tmp_path / 'db.sqlite')
    apps = ApplicationsRepository(sessions)
    service = ApplicationCreationService(sessions)
    url = 'https://job.invalid/?id=1&track=2'
    url_match = apps.create(ApplicationCreate(company_name='other', position_name='other', job_url=url))
    exact = apps.create(ApplicationCreate(company_name='ACME', position_name='Dev'))
    with sessions() as session:
        session.execute(text("UPDATE applications SET status='assessment' WHERE id=:id"), {'id': exact.id})
        session.commit()
    for _ in range(22):
        apps.create(ApplicationCreate(company_name='Ａｃｍｅ Corp', position_name='DEV Engineer'))
    result = service.duplicates('acme', 'dev', f' {url} ')
    assert result['has_more'] is True
    assert len(result['items']) == 20
    assert [r['id'] for r in result['items'][:2]] == [url_match.id, exact.id]
    assert result['items'][1]['status'] == 'written_test'
    assert service.duplicates('', '', 'https://job.invalid/?id=1')['items'] == []
    with sessions() as session:
        session.execute(text('UPDATE applications SET deleted_at=CURRENT_TIMESTAMP WHERE id=:id'), {'id': url_match.id})
        session.commit()
    assert service.duplicates('', '', url)['items'] == []


def test_additive_migration_and_receipt_unique_constraint(tmp_path):
    from sqlalchemy.exc import IntegrityError

    path = tmp_path / 'db.sqlite'
    sessions = init_database(path)
    existing = ApplicationsRepository(sessions).create(ApplicationCreate('existing', 'role'))
    with sessions.kw['bind'].begin() as connection:
        connection.execute(text('DROP TABLE application_creation_receipts'))
        connection.execute(text('DROP TABLE application_creation_workspace'))
        connection.execute(text("DELETE FROM schema_migrations WHERE version='0029_application_creation_receipts'"))
    sessions.kw['bind'].dispose()
    sessions = init_database(path)
    service = ApplicationCreationService(sessions)
    result, _ = service.create(ApplicationCreate('new', 'role'), None, 'request-key-00000001')
    assert ApplicationsRepository(sessions).get(existing.id).company_name == 'existing'
    with sessions.kw['bind'].begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text('''INSERT INTO application_creation_receipts
                (scope_id, operation, idempotency_key, request_fingerprint, application_id, result_json)
                SELECT scope_id, operation, idempotency_key, request_fingerprint, application_id, result_json
                FROM application_creation_receipts'''))
    sessions.kw['bind'].dispose()
    restarted = ApplicationCreationService(init_database(path))
    assert restarted.create(ApplicationCreate('new', 'role'), None, 'request-key-00000001') == (result, True)


def test_receipt_failure_rolls_back_application_and_jd(tmp_path):
    from sqlalchemy.exc import IntegrityError

    sessions = init_database(tmp_path / 'db.sqlite')
    with sessions.kw['bind'].begin() as connection:
        connection.execute(text('''CREATE TRIGGER fail_receipt BEFORE INSERT ON application_creation_receipts
            BEGIN SELECT RAISE(ABORT, 'injected receipt failure'); END'''))
    with pytest.raises(IntegrityError):
        ApplicationCreationService(sessions).create(
            ApplicationCreate('company', 'role'), payload()['initial_jd'], 'receipt-failure-00001',
        )
    with sessions.kw['bind'].connect() as connection:
        for table in ('applications', 'application_jd_versions', 'application_creation_receipts'):
            assert connection.scalar(text(f'SELECT count(*) FROM {table}')) == 0


def test_lock_contention_is_retryable_without_new_records(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from offerpilot.repositories.application_jd_versions import JDVersionError

    path = tmp_path / 'db.sqlite'
    sessions = init_database(path)
    competing_engine = create_engine(f'sqlite:///{path}', connect_args={'timeout': 0.01})
    competing = ApplicationCreationService(sessionmaker(competing_engine))
    with sessions() as owner:
        owner.execute(text('BEGIN IMMEDIATE'))
        with pytest.raises(JDVersionError) as caught:
            competing.create(ApplicationCreate('company', 'role'), None, 'locked-request-0001')
        assert caught.value.status_code == 503
        assert caught.value.code == 'application_creation_retryable'
        assert owner.scalar(text('SELECT count(*) FROM applications')) == 0
    assert competing.create(ApplicationCreate('company', 'role'), None, 'locked-request-0001')[1] is False
    competing_engine.dispose()


@pytest.mark.parametrize('job_url,source_url', [
    ('', None), ('https://job.invalid/a', None), ('', 'https://source.invalid/b'),
])
def test_sources_remain_independent(tmp_path, job_url, source_url):
    sessions = init_database(tmp_path / 'db.sqlite')
    result, _ = ApplicationCreationService(sessions).create(
        ApplicationCreate('company', 'role', job_url=job_url),
        {'jd_text': '  200元/天\n', 'source_url': source_url}, 'independent-source-01',
    )
    version = ApplicationJDService(sessions).get_current(result['id'])
    assert result['job_url'] == job_url
    assert version.source_url == source_url
    assert version.jd_text == '  200元/天\n'


def test_initial_jd_update_preserves_submission_and_rejects_stale_edit(tmp_path):
    from datetime import datetime, timezone
    from offerpilot.repositories.application_jd_versions import JDVersionConflictError
    from offerpilot.repositories.application_outcomes import ApplicationOutcomesRepository, SubmissionSnapshotCreate
    from offerpilot.repositories.resumes import ResumeCreate, ResumesRepository

    sessions = init_database(tmp_path / 'db.sqlite')
    service = ApplicationCreationService(sessions)
    application, _ = service.create(ApplicationCreate('company', 'role'), payload()['initial_jd'], 'creation-snapshot-01')
    jds = ApplicationJDService(sessions)
    first = jds.get_current(application['id'])
    resume = ResumesRepository(sessions).create(ResumeCreate(title='resume', content_json={'raw_text': '原简历'}))
    outcomes = ApplicationOutcomesRepository(sessions)
    frozen = outcomes.create_snapshot(SubmissionSnapshotCreate(
        application_id=application['id'], resume_id=resume.id, jd_version_id=first.id,
        material_kit_id=None, submitted_at=datetime.now(timezone.utc), note='',
        source_kind='ui', idempotency_key='submission-snapshot-01',
    )).value
    jds.create_version(application['id'], jd_text='新 JD', source_url=None, source_kind='ui',
                       expected_current_version_id=first.id, idempotency_key='updated-jd-version-01')
    assert jds.get_version(application['id'], first.id).jd_text == payload()['initial_jd']['jd_text']
    assert outcomes.list_snapshots(application['id'])[0].value.jd_snapshot == frozen.jd_snapshot == first.jd_text
    with pytest.raises(JDVersionConflictError):
        jds.create_version(application['id'], jd_text='过期编辑', source_url=None, source_kind='ui',
                           expected_current_version_id=first.id, idempotency_key='stale-jd-version-001')
    assert service.create(ApplicationCreate('company', 'role'), payload()['initial_jd'], 'creation-snapshot-01') == (application, True)
