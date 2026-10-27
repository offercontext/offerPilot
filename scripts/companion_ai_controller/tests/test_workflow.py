"""The development workflow has no protected job or secret-bearing path."""
import re

import yaml

from scripts.companion_ai_controller.tests.workflow_assets import historical_workflow

WORKFLOW = historical_workflow('development')


def test_workflow_has_only_fixed_fake_push_job_and_read_token():
    raw = WORKFLOW.read_text()
    data = yaml.safe_load(raw)
    trigger = data.get('on', data.get(True))
    assert set(trigger) == {'push'}
    assert trigger['push']['branches'] == ['feat/20261027-fixed-ai-controller']
    assert data['permissions'] == {'contents': 'read'}
    assert data['concurrency'] == {
        'group': 'offerpilot-companion-ai-20261021-one-shot', 'cancel-in-progress': False}
    assert set(data['jobs']) == {'rehearsal'}
    assert 'environment:' not in raw and 'secrets.' not in raw
    assert 'workflow_dispatch' not in raw and 'pull_request_target' not in raw
    for step in data['jobs']['rehearsal']['steps']:
        if 'uses' in step:
            assert re.fullmatch(r'[^@]+@[0-9a-f]{40}', step['uses'])
        if step.get('uses', '').startswith('actions/checkout@'):
            assert step['with']['persist-credentials'] is False
    commands = '\n'.join(s.get('run', '') for s in data['jobs']['rehearsal']['steps'])
    assert 'scripts.companion_ai_controller.test_gate' in commands
    assert 'scripts.companion_ai_controller.rehearsal --output-dir' in commands
    assert '--live' not in commands


def test_workflow_explicit_upload_allowlist_contains_original_requests():
    data = yaml.safe_load(WORKFLOW.read_text())
    upload = next(s for s in data['jobs']['rehearsal']['steps']
                  if s.get('uses', '').startswith('actions/upload-artifact@'))
    assert upload['if'] == 'always()'
    paths = upload['with']['path'].splitlines()
    assert len(paths) == 5 and all('*' not in p for p in paths)
    assert 'artifacts/companion-controller-public-platform.json' in paths
    assert 'artifacts/companion-controller-rehearsal/rehearsal.zip' in paths
    assert not any(path.endswith('.jsonl') for path in paths)
    assert 'artifacts/companion-controller-offline.json' in paths
