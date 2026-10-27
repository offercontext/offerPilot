"""Production bridge tests use paired fake gates, keys and transport only."""
import base64
from dataclasses import replace
import inspect
import json
from pathlib import Path
import re
from types import SimpleNamespace
import zipfile

import pytest
import yaml

from scripts.companion_ai_controller import admission as a, policy as p, production as prod
from scripts.companion_ai_controller.execute import _Clock, _FakeUpstream
from scripts.companion_ai_validation import broker

TEMPLATE = Path('scripts/companion_ai_controller/templates/companion-ai-controller.yml')


def paid_record():
    now = p.DEADLINE - 3600
    return a.ProductionAdmission('production_verified', 'paid', 'a' * 64, 'b' * 64, 900001,
                                'c' * 40, p.SOURCE_SHA, p.REQUEST_HASHES, p.BATCH_KEY,
                                48, 411934, 0, now, now + 120, p.DEADLINE)


def test_unconfigured_blocks_before_context_secret_transport_and_network(tmp_path, monkeypatch):
    monkeypatch.delenv(prod.REGISTRATION_VARIABLE, raising=False)
    monkeypatch.setattr(prod, '_read_key', lambda: pytest.fail('secret read'))
    monkeypatch.setattr(prod, 'runtime_context', lambda: pytest.fail('unconfigured context'))
    monkeypatch.setattr(broker, 'HTTPSUpstream', lambda *a, **k: pytest.fail('real transport'))
    with pytest.raises(p.Blocked, match='PRODUCTION_UNCONFIGURED'):
        prod.send_current(tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_public_execution_interface_has_no_injectable_authority():
    assert list(inspect.signature(prod.send_current).parameters) == ['evidence_dir']
    assert list(inspect.signature(prod.verify_current).parameters) == ['phase']
    assert list(inspect.signature(prod.load_registration).parameters) == []


def test_handoff_is_bounded_strict_before_data_not_an_authorization():
    before = replace(paid_record(), phase='before', expires_at=p.DEADLINE)
    assert prod.decode_handoff(prod.encode_handoff(before)) == before
    for malformed in ('!', 'a' * 40000, base64.b64encode(b'{"phase":"before","phase":"paid"}').decode()):
        with pytest.raises(Exception):
            prod.decode_handoff(malformed)
    with pytest.raises(p.Blocked):
        prod.decode_handoff(prod.encode_handoff(paid_record()))


def test_self_pinned_registration_forbidden(monkeypatch):
    monkeypatch.setenv(prod.REGISTRATION_VARIABLE, json.dumps({'controller_sha': 'a' * 40}))
    with pytest.raises(p.Blocked, match='SELF_PINNED_CONTROLLER_FORBIDDEN'):
        prod.load_registration()


def test_runtime_maps_fixed_owner_login_and_does_not_invent_triggering_id(monkeypatch):
    values = {'CONTROLLER_CANDIDATE_SHA': p.SOURCE_SHA, 'CONTROLLER_CI_RUN_ID': str(prod.H_RUN),
              'CONTROLLER_MANIFEST_SHA256': p.MANIFEST_SHA256,
              'GITHUB_REPOSITORY_ID': str(p.REPOSITORY_ID), 'GITHUB_REF': p.CONTROL_REF,
              'GITHUB_SHA': 'a' * 40, 'GITHUB_WORKFLOW_SHA': 'a' * 40, 'GITHUB_RUN_ID': '900001',
              'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
              'GITHUB_ACTOR_ID': str(p.OWNER_ID), 'GITHUB_TRIGGERING_ACTOR': 'XiaoZheBrother'}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    result = prod.runtime_context()
    assert result.triggering_actor_id == p.OWNER_ID
    monkeypatch.setenv('GITHUB_TRIGGERING_ACTOR', 'not-owner')
    with pytest.raises(p.Blocked, match='TRIGGERING_ACTOR_INVALID'):
        prod.runtime_context()


@pytest.fixture
def fake_bridge(tmp_path, monkeypatch):
    """Transport-unit fixture: explicitly replace every authority/key/network boundary."""
    events = []
    monkeypatch.setattr(broker, 'time', _Clock)
    window = broker.RunWindow(shadow_review=True)
    monkeypatch.setattr(prod, 'verify_current', lambda phase: (
        paid_record(), SimpleNamespace(run_id=900001), window))
    monkeypatch.setattr(prod, '_read_key', lambda: events.append('key') or 'unit-test-key-only')
    monkeypatch.setattr(prod, 'clean_environment', lambda: events.append('clean'))
    fake = _FakeUpstream(None)

    def constructor(key, *, window):
        assert key == 'unit-test-key-only'
        events.append('fake_transport')
        return fake

    monkeypatch.setattr(broker, 'HTTPSUpstream', constructor)
    monkeypatch.setenv('RUNNER_TEMP', str(tmp_path))
    return events, fake


def test_fixed_fake_bridge_runs_actual_six_broker_calls_and_verified_zip(tmp_path, fake_bridge):
    events, fake = fake_bridge
    out = tmp_path / 'out'
    assert prod.send_current(out)
    assert events == ['key', 'clean', 'fake_transport'] and fake.calls == 6
    with zipfile.ZipFile(out / 'evidence.zip') as archive:
        assert set(archive.namelist()) == set(prod.EVIDENCE_FILES) | {'manifest.json'}
        assert len(archive.read('requests.jsonl').splitlines()) == 6
        assert json.loads(archive.read('summary.json'))['budget']['settled_micro_cny'] == 411934 + 1680
        assert b'unit-test-key-only' not in b''.join(archive.read(n) for n in archive.namelist())
    with pytest.raises(p.Blocked, match='EXECUTION_ALREADY_CLAIMED'):
        prod.send_current(tmp_path / 'second')
    assert fake.calls == 6 and events.count('key') == 1


def test_expired_paid_record_blocks_secret_after_claim(tmp_path, fake_bridge, monkeypatch):
    events, fake = fake_bridge
    monkeypatch.setattr(prod, 'verify_current', lambda phase: (
        replace(paid_record(), expires_at=p.DEADLINE - 4000), SimpleNamespace(run_id=900001),
        broker.RunWindow(shadow_review=True)))
    with pytest.raises(p.Blocked, match='PAID_ADMISSION_EXPIRED'):
        prod.send_current(tmp_path / 'out')
    assert not events and fake.calls == 0


def test_unknown_transport_keeps_reservation_and_does_not_retry(tmp_path, fake_bridge):
    _, fake = fake_bridge
    fake.failure = 'transport_unknown'
    out = tmp_path / 'out'
    assert prod.send_current(out) is False
    assert fake.calls == 1
    with zipfile.ZipFile(out / 'evidence.zip') as archive:
        report = json.loads(archive.read('summary.json'))
        assert report['budget']['reserved_micro_cny'] == 3000000
        assert report['budget']['halted'] is True


def test_credential_echo_never_becomes_public_archive(tmp_path, fake_bridge, monkeypatch):
    monkeypatch.setattr(broker, 'HTTPSUpstream', lambda *a, **k: lambda raw: (500, b'unit-test-key-only'))
    with pytest.raises(p.Blocked, match='CREDENTIAL_ECHO_QUARANTINED'):
        prod.send_current(tmp_path / 'out')
    assert not (tmp_path / 'out' / 'evidence.zip').exists()
    assert all(b'unit-test-key-only' not in path.read_bytes() for path in (tmp_path / 'out').iterdir())


def test_failed_zip_verification_does_not_publish(tmp_path):
    folder = tmp_path / 'out'
    folder.mkdir()
    (folder / 'summary.json').write_text('{}')
    with pytest.raises(p.Blocked, match='EVIDENCE_INCOMPLETE'):
        prod._publish_archive(folder, paid_record())
    assert not (folder / 'evidence.zip').exists()


def test_inert_template_fixed_protection_and_no_candidate_execution():
    assert TEMPLATE.parent.name == 'templates'
    assert TEMPLATE.parts[0] == 'scripts' and '.github' not in TEMPLATE.parts
    raw = TEMPLATE.read_text()
    data = yaml.safe_load(raw)
    assert data['permissions'] == {'contents': 'read'}
    assert data['concurrency'] == {'group': p.CONCURRENCY, 'cancel-in-progress': False}
    trigger = data.get('on', data.get(True))
    assert set(trigger) == {'workflow_dispatch'}
    assert set(trigger['workflow_dispatch']['inputs']) == {'candidate_sha', 'ci_run_id', 'manifest_sha256'}
    jobs = data['jobs']
    assert set(jobs) == {'offline', 'preflight', 'paid'}
    assert [jobs[k]['name'] for k in ('offline', 'preflight', 'paid')] == [a.OFFLINE_JOB, a.PREFLIGHT_JOB, a.PAID_JOB]
    assert jobs['paid']['timeout-minutes'] == 20
    assert jobs['paid']['needs'] == ['offline', 'preflight']
    assert 'environment' not in jobs['offline'] and 'environment' not in jobs['preflight']
    guard = jobs['paid']['if']
    for fragment in (p.CONTROL_REF, 'CONTROLLER_SHA', 'REGISTRATION', 'run_attempt == 1'):
        assert fragment in guard
    for job in jobs.values():
        assert 'secrets.' not in str(job.get('env', {}))
        for step in job['steps']:
            if 'uses' in step:
                assert re.fullmatch(r'[^@]+@[a-f0-9]{40}', step['uses'])
            if 'run' in step:
                assert '${{ inputs.' not in step['run']
                assert 'continue-on-error' not in step
    steps = jobs['paid']['steps']
    checks = [i for i, s in enumerate(steps) if s.get('run', '').endswith('production paid')]
    sends = [i for i, s in enumerate(steps) if 'production send ' in s.get('run', '')]
    assert len(checks) == len(sends) == 1 and checks[0] < sends[0]
    assert sum('secrets.' in str(s) for s in steps) == 1
    assert list(steps[sends[0]]['env']) == [prod.KEY_NAME]
    assert not any('uv ' in s.get('run', '') or 'pip ' in s.get('run', '') for s in steps)
    upload = next(s for s in steps if s.get('uses', '').startswith('actions/upload-artifact@'))
    assert upload['if'] == 'always()' and upload['with']['path'].endswith('/evidence.zip')
    assert '*' not in upload['with']['path'] and '.jsonl' not in upload['with']['path']


def test_paid_freshness_does_not_expand_to_execution_window():
    record = paid_record()
    with pytest.raises(p.Blocked, match='PAID_ADMISSION_EXPIRED'):
        prod._fresh(record, record.checked_at + 121)
    with pytest.raises(p.Blocked, match='PAID_ADMISSION_EXPIRED'):
        prod._fresh(replace(record, expires_at=record.checked_at + 1200), record.checked_at)


@pytest.mark.parametrize('mutation', [None, 'approval', 'binding'])
def test_full_metadata_gate_to_fake_provider_has_no_caller_admission_bypass(tmp_path, monkeypatch, mutation):
    from scripts.companion_ai_controller.tests.test_admission import make_fixture, approve_fixture
    from scripts.companion_ai_controller.tests.test_read_only import response

    reg, ctx, data = make_fixture()
    before = a.verify_before(reg, ctx, reader=data.__getitem__, now=_Clock.time())
    paid_data = approve_fixture(reg, ctx, data)
    if mutation == 'approval':
        paid_data[f'/actions/runs/{ctx.run_id}/approvals'] = response([])
    if mutation == 'binding':
        before = replace(before, binding_digest='0' * 64)
    reads = []

    class FakePublicReader:
        def __init__(self, *, registered_paths):
            self.registered_paths = registered_paths

        def __call__(self, path):
            assert path in self.registered_paths or a.ro._allowed(path)
            reads.append(path)
            return paid_data[path]

    monkeypatch.setattr(a.ro, 'PublicReader', FakePublicReader)
    monkeypatch.setattr(prod, 'load_registration', lambda: reg)
    monkeypatch.setattr(prod, 'runtime_context', lambda: ctx)
    monkeypatch.setattr(prod, '_checkout', lambda sha: None)
    monkeypatch.setattr(broker, 'time', _Clock)
    monkeypatch.setenv('CONTROLLER_BEFORE', prod.encode_handoff(before))
    monkeypatch.setenv('RUNNER_TEMP', str(tmp_path))
    keys = []
    monkeypatch.setattr(prod, '_read_key', lambda: keys.append('fake') or 'unit-provider-only')
    monkeypatch.setattr(prod, 'clean_environment', lambda: None)
    fake = _FakeUpstream(None)
    monkeypatch.setattr(broker, 'HTTPSUpstream', lambda *args, **kwargs: fake)
    if mutation:
        with pytest.raises(p.Blocked):
            prod.send_current(tmp_path / 'out')
        assert not keys and fake.calls == 0
    else:
        assert prod.send_current(tmp_path / 'out')
        assert keys == ['fake'] and fake.calls == 6
    assert f'/actions/runs/{ctx.run_id}/approvals' in reads


def test_explicit_metadata_token_is_removed_before_provider_loader(tmp_path, monkeypatch):
    from scripts.companion_ai_controller.tests.test_admission import make_fixture, approve_fixture

    reg, ctx, data = make_fixture()
    reg = replace(reg, reader_mode='github_actions_read')
    before = a.verify_before(reg, ctx, reader=data.__getitem__, now=_Clock.time())
    paid_data = approve_fixture(reg, ctx, data)
    metadata_values = []

    def build_reader(registration, context, *, metadata_token=None):
        metadata_values.append(metadata_token)
        return paid_data.__getitem__

    monkeypatch.setattr(a, 'build_reader', build_reader)
    monkeypatch.setattr(prod, 'load_registration', lambda: reg)
    monkeypatch.setattr(prod, 'runtime_context', lambda: ctx)
    monkeypatch.setattr(prod, '_checkout', lambda sha: None)
    monkeypatch.setattr(broker, 'time', _Clock)
    monkeypatch.setenv('CONTROLLER_BEFORE', prod.encode_handoff(before))
    monkeypatch.setenv('RUNNER_TEMP', str(tmp_path))
    monkeypatch.setenv(prod.METADATA_TOKEN_NAME, 'unit-metadata-only')

    def key():
        assert prod.METADATA_TOKEN_NAME not in prod.os.environ
        return 'unit-provider-only'

    monkeypatch.setattr(prod, '_read_key', key)
    monkeypatch.setattr(prod, 'clean_environment', lambda: None)
    fake = _FakeUpstream(None)

    def transport(key, *, window):
        assert key == 'unit-provider-only'
        return fake

    monkeypatch.setattr(broker, 'HTTPSUpstream', transport)
    assert prod.send_current(tmp_path / 'out')
    assert metadata_values == ['unit-metadata-only']
    with zipfile.ZipFile(tmp_path / 'out' / 'evidence.zip') as archive:
        assert b'unit-metadata-only' not in b''.join(archive.read(n) for n in archive.namelist())


def test_actions_read_profile_diff_is_only_explicit_permission_and_three_scoped_tokens():
    from copy import deepcopy
    base = yaml.safe_load(TEMPLATE.read_text())
    alternate_path = TEMPLATE.with_name('companion-ai-controller-actions-read.yml')
    alternate = yaml.safe_load(alternate_path.read_text())
    normalized = deepcopy(alternate)
    assert normalized['permissions'].pop('actions') == 'read'
    tokens = 0
    for job in normalized['jobs'].values():
        for step in job['steps']:
            if prod.METADATA_TOKEN_NAME in step.get('env', {}):
                assert step['env'].pop(prod.METADATA_TOKEN_NAME) == '${{ github.token }}'
                assert 'scripts.companion_ai_controller.production ' in step['run']
                tokens += 1
                if not step['env']:
                    step.pop('env')
    assert tokens == 3 and normalized == base
    assert '.github' not in alternate_path.parts


def test_production_import_needs_only_standard_library():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-S', '-c',
        'import scripts.companion_ai_controller.admission; import scripts.companion_ai_controller.production'],
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_inert_platform_review_template_never_calls_paid_or_send():
    path = TEMPLATE.with_name('companion-ai-controller-review-dry-run.yml')
    raw = path.read_text()
    data = yaml.safe_load(raw)
    assert path.parts[0] == 'scripts'
    assert data['permissions'] == {'contents': 'read'}
    assert set(data.get('on', data.get(True))) == {'workflow_dispatch'}
    assert set(data['jobs']) == {'platform_review'}
    job = data['jobs']['platform_review']
    assert job['name'] == 'Controller platform-only review'
    assert job['environment'] == 'offerpilot-companion-ai-controller'
    assert p.CONTROL_REF in job['if'] and 'CONTROLLER_SHA' in job['if']
    assert 'secrets.' not in raw and 'github.token' not in raw
    assert prod.METADATA_TOKEN_NAME not in raw and prod.KEY_NAME not in raw
    commands = '\n'.join(step.get('run', '') for step in job['steps'])
    assert 'production control-probe' in commands
    assert 'production send' not in commands and 'production paid' not in commands
    assert 'uv ' not in commands and 'pip ' not in commands


def test_unconfigured_platform_probe_cannot_claim_or_read_keys(monkeypatch):
    monkeypatch.delenv(prod.REGISTRATION_VARIABLE, raising=False)
    monkeypatch.setattr(prod, '_read_key', lambda: pytest.fail('provider key'))
    monkeypatch.setattr(prod, '_claim', lambda _: pytest.fail('paid attempt'))
    with pytest.raises(p.Blocked, match='PRODUCTION_UNCONFIGURED'):
        prod.probe_current()
