"""Public GET rehearsal stays credential-free and never becomes admission."""
import hashlib
import json
from pathlib import Path
import shlex
from urllib.error import URLError
from urllib.request import ProxyHandler

import pytest
import yaml

from scripts.companion_ai_controller import read_only as public

WORKFLOW = Path('.github/workflows/companion-ai-controller-dry-run.yml')
PINNED_ZIP = Path('scripts/companion_ai_controller/evidence/h-offline-12707161298.zip')
REPORT = 'artifacts/companion-controller-public-read.json'
COMMAND = (
    'uv run python -m scripts.companion_ai_controller.read_only '
    '--offline-artifact scripts/companion_ai_controller/evidence/h-offline-12707161298.zip '
    '--scan-legacy-history > artifacts/companion-controller-public-read.json'
)


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def test_public_step_is_fixed_after_fake_and_fail_closed():
    data = workflow()
    job = data['jobs']['rehearsal']
    steps = job['steps']
    public_step = next(s for s in steps if s.get('name') == 'Read fixed public source and legacy history')
    assert shlex.split(public_step['run']) == shlex.split(COMMAND)
    assert set(public_step) == {'name', 'run'}
    fake = next(s for s in steps if s.get('name') == 'Run isolated fake-only controller path')
    assert steps.index(public_step) == steps.index(fake) + 1
    assert job['timeout-minutes'] == 20
    assert data['permissions'] == {'contents': 'read'}
    assert set(job['env']) == {'LITELLM_LOCAL_MODEL_COST_MAP', 'PYTHONPATH'}
    assert all('continue-on-error' not in step for step in steps)
    assert 'environment' not in job
    assert 'secrets.' not in WORKFLOW.read_text()
    assert '--live' not in public_step['run'] and 'retry' not in public_step['run']
    assert 'token' not in public_step['run'].lower() and 'proxy' not in public_step['run'].lower()


def test_always_uploads_public_failure_report_and_final_fake_zip_only():
    steps = workflow()['jobs']['rehearsal']['steps']
    upload = next(s for s in steps if s.get('uses', '').startswith('actions/upload-artifact@'))
    assert upload['if'] == 'always()'
    assert upload['with']['path'].splitlines() == [
        'artifacts/companion-controller-offline.json',
        'artifacts/companion-controller-rehearsal-status.json',
        'artifacts/companion-controller-rehearsal/rehearsal.zip',
        REPORT,
    ]
    # requests.jsonl remains inside the fully labeled, hash-verified fake ZIP;
    # no raw intermediate ledger directory or uncontrolled path is uploaded.
    assert not any('*' in p or p.endswith('.jsonl') for p in upload['with']['path'].splitlines())


def test_committed_h_zip_is_exact_verified_original():
    assert not PINNED_ZIP.is_symlink()
    raw = PINNED_ZIP.read_bytes()
    assert len(raw) == public.ZIP_SIZE == 38414
    assert hashlib.sha256(raw).hexdigest() == public.ZIP_SHA256
    public._verify_aggregate(raw)


def test_cli_public_failure_preserves_constant_json_and_nonzero(monkeypatch, capsys):
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        raise public.Blocked('PUBLIC_GET_FAILED')

    monkeypatch.setattr(public, 'collect_source', fail)
    monkeypatch.setattr('sys.argv', ['read_only', '--scan-legacy-history'])
    assert public.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report == {'mode': 'read_only_evidence', 'live_allowed': False,
                      'source_evidence_verified': False, 'error': 'PUBLIC_GET_FAILED'}
    assert calls == [{'artifact_bytes': None, 'scan_history': True}]


def test_public_transport_ignores_proxy_and_token_environment_without_fallback(monkeypatch):
    handlers_seen = []
    requests = []

    class FailingOpener:
        def open(self, request, timeout):
            requests.append((request, timeout))
            raise URLError('PRIVATE_SYNTHETIC_FAILURE_MUST_NOT_LEAK')

    def opener(*handlers):
        handlers_seen.extend(handlers)
        return FailingOpener()

    for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY'):
        monkeypatch.setenv(key, 'PRIVATE_SYNTHETIC_SENTINEL')
    monkeypatch.setattr(public, 'build_opener', opener)
    reader = public.PublicReader()
    with pytest.raises(public.Blocked, match='^PUBLIC_GET_FAILED$'):
        reader('')
    assert len(requests) == 1
    request, timeout = requests[0]
    assert request.get_method() == 'GET' and request.full_url == public.ROOT
    assert timeout == 15
    assert all('authorization' != key.lower() for key in request.headers)
    assert 'PRIVATE_SYNTHETIC_SENTINEL' not in str(request.headers)
    proxies = [h for h in handlers_seen if isinstance(h, ProxyHandler)]
    assert len(proxies) == 1 and proxies[0].proxies == {}
