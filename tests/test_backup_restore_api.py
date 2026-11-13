from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from offerpilot.api import create_app
from offerpilot.backup_restore import status
from offerpilot.config import Config, save_config
from tests.test_backup_install import queued

__all__ = ["queued"]


def test_api_preview_confirm_cancel(tmp_path: Path):
    client = TestClient(create_app(data_dir=tmp_path))
    archive = client.get('/api/backups/export')
    assert archive.status_code == 200, archive.text
    preview = client.post('/api/backups/restore/preview', content=archive.content)
    assert preview.status_code == 200, preview.text
    record = preview.json()
    assert record['state'] == 'validated' and record['legacy'] is False
    assert 'files' not in record
    payload = {key: record[key] for key in ('id', 'sha256')}
    payload['confirmation'] = 'wrong'
    assert client.post('/api/backups/restore/confirm', json=payload).status_code == 409
    payload['confirmation'] = 'REPLACE WORKSPACE'
    for _ in range(2):
        assert client.post('/api/backups/restore/confirm', json=payload).json()['state'] == 'queued'
    assert client.get('/api/backups/restore').json()['state'] == 'queued'
    assert client.post('/api/backups/restore/cancel', json={'id': record['id']}).json()['state'] == 'cancelled'


def test_authentication_and_origin(tmp_path: Path):
    save_config(tmp_path, Config(auth_enabled=True, auth_token='synthetic-token'))
    client = TestClient(create_app(data_dir=tmp_path))
    assert client.get('/api/backups/restore').status_code == 401
    for action in ('preview', 'confirm', 'cancel'):
        assert client.post('/api/backups/restore/' + action, json={}).status_code == 401
    client.headers['X-OfferPilot-Token'] = 'synthetic-token'
    for action in ('preview', 'confirm', 'cancel'):
        assert client.post('/api/backups/restore/' + action, json={},
                           headers={'Origin': 'https://untrusted.invalid'}).status_code == 403
    for action in ('confirm', 'cancel'):
        assert client.post('/api/backups/restore/' + action, content='x' * 4097).status_code == 409


def test_invalid_upload_preserves_live_database(tmp_path: Path):
    client = TestClient(create_app(data_dir=tmp_path))
    before = (tmp_path / 'data.db').read_bytes()
    assert client.post('/api/backups/restore/preview', content=b'not zip').status_code == 400
    assert (tmp_path / 'data.db').read_bytes() == before
    assert client.get('/api/backups/restore').json()['state'] == 'none'


def test_actual_cold_app_startup_completes_restore(queued):
    root, _ = queued
    code = ("from pathlib import Path; from fastapi.testclient import TestClient; "
            "from offerpilot.api import create_app; "
            f"root=Path({str(root)!r})\n"
            "with TestClient(create_app(data_dir=root)) as client:\n"
            " assert client.get('/api/health').status_code==200\n"
            " assert client.get('/api/backups/restore').json()['state']=='completed'\n")
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    assert status(root)['state'] == 'completed'
    assert (root / '中文.txt').read_text(encoding='utf-8') == 'backup'


def test_actual_startup_failure_rolls_back_on_next_process(queued):
    from offerpilot.backup_archive import workspace_files
    from tests.test_backup_install import cold

    root, _ = queued
    before = workspace_files(root)
    code = f'''
from pathlib import Path
from fastapi.testclient import TestClient
from offerpilot.api import create_app
app=create_app(data_dir=Path({str(root)!r}))
def fail():
    raise RuntimeError('synthetic startup failure')
app.router.on_startup.insert(0, fail)
with TestClient(app):
    pass
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=45)
    assert result.returncode != 0
    assert 'synthetic startup failure' in result.stderr
    assert status(root)['state'] == 'installed'
    assert cold(root).returncode == 0
    assert status(root)['state'] == 'rolled_back'
    assert workspace_files(root) == before
