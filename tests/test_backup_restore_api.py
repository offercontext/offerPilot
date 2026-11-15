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


def test_cold_restore_knowledge_bundle_through_real_api(tmp_path: Path):
    """Actual upload/export/confirm, process exit, install and API byte reads."""
    seed = r'''
import base64, io, json, sys, time
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient
from offerpilot.api import create_app
root=Path(sys.argv[1])
main=b'# Synthetic bundle\n\n![diagram](pic.png)\n'
buffer=io.BytesIO()
Image.new('RGB',(12,12),(12,34,56)).save(buffer,format='PNG')
picture=buffer.getvalue()
with TestClient(create_app(data_dir=root/'source')) as client:
    response=client.post('/api/knowledge/sources',files=[('file',('source.md',main)),('files',('pic.png',picture,'image/png'))])
    assert response.status_code==202,response.text
    source_id=response.json()['source']['id']
    for _ in range(250):
        state=client.get(f'/api/knowledge/sources/{source_id}').json()
        if state['extraction_status']=='extracted': break
        assert state['extraction_status']!='failed',state
        time.sleep(.02)
    else: raise AssertionError('extraction did not complete')
    assets=client.get(f'/api/knowledge/sources/{source_id}/assets').json()['items']
    asset_id=assets[0]['id']
    evidence=client.get(f'/api/knowledge/sources/{source_id}/evidence').json()['items']
    assert any(item.get('asset_id')==asset_id for item in evidence)
    exported=client.get('/api/backups/export')
    assert exported.status_code==200,exported.text
with TestClient(create_app(data_dir=root/'target')) as client:
    (root/'target/original-marker').write_text('retained')
    preview=client.post('/api/backups/restore/preview',content=exported.content)
    assert preview.status_code==200,preview.text
    record=preview.json()
    confirmed=client.post('/api/backups/restore/confirm',json={'id':record['id'],'sha256':record['sha256'],'confirmation':'REPLACE WORKSPACE'})
    assert confirmed.status_code==200,confirmed.text
    assert confirmed.json()['state']=='queued'
(root/'expected.json').write_text(json.dumps({'source':source_id,'asset':asset_id,'main':base64.b64encode(main).decode(),'image':base64.b64encode(picture).decode()}))
'''
    read = r'''
import base64,json,sys
from pathlib import Path
from fastapi.testclient import TestClient
from offerpilot.api import create_app
from offerpilot.backup_restore import read_record,operation_paths
root=Path(sys.argv[1]); expected=json.loads((root/'expected.json').read_text())
with TestClient(create_app(data_dir=root/'target')) as client:
    assert client.get('/api/backups/restore').json()['state']=='completed'
    sid=expected['source']; aid=expected['asset']
    source=client.get(f'/api/knowledge/sources/{sid}')
    assert source.status_code==200 and source.json()['id']==sid
    assets=client.get(f'/api/knowledge/sources/{sid}/assets').json()['items']
    assert len(assets)==1 and assets[0]['id']==aid
    evidence=client.get(f'/api/knowledge/sources/{sid}/evidence').json()['items']
    assert any(item.get('asset_id')==aid for item in evidence)
    for suffix,key in [('content','main'),(f'assets/{aid}/content','image')]:
        response=client.get(f'/api/knowledge/sources/{sid}/{suffix}')
        assert response.status_code==200,response.text
        assert response.content==base64.b64decode(expected[key])
    assert client.get(f'/api/knowledge/sources/{sid+100}/assets/{aid}/content').status_code==404
'''
    for code in (seed, read):
        result = subprocess.run([sys.executable, '-c', code, str(tmp_path)],
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        if code == seed:
            (tmp_path / 'source').rename(tmp_path / 'source-unavailable')
    assert not (tmp_path / 'target/original-marker').exists()
    from offerpilot.backup_restore import operation_paths, read_record
    _, original, _ = operation_paths(tmp_path / 'target', read_record(tmp_path / 'target'))
    assert (original / 'original-marker').read_text() == 'retained'
