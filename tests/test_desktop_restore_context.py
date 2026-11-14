"""Synthetic cold-process checks: desktop identity follows restore authority."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("rollback", [False, True])
def test_restore_context_cold_start_and_rollback(tmp_path: Path, rollback: bool):
    def run(code: str) -> dict:
        result = subprocess.run([sys.executable, "-c", code, str(tmp_path)],
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout.strip().splitlines()[-1])

    initial = run('''
import json,sys
from pathlib import Path
from fastapi.testclient import TestClient
from offerpilot.api import create_app
from offerpilot.config import Config,save_config
root=Path(sys.argv[1])/'data'
save_config(root,Config(auth_enabled=True,auth_token='synthetic-current',confirmation_secret='synthetic-confirmation'))
with TestClient(create_app(data_dir=root)) as c:
 assert c.get('/api/reminders/desktop-context').status_code==401
 c.headers['X-OfferPilot-Token']='synthetic-current'
 before=c.get('/api/reminders/desktop-context').json()['workspace_id']
 assert len(before)==64 and before!='synthetic-confirmation'
 archive=c.get('/api/backups/export');assert archive.status_code==200
 record=c.post('/api/backups/restore/preview',content=archive.content).json()
 assert c.get('/api/reminders/desktop-context').json()['workspace_id']==before
 assert c.post('/api/backups/restore/cancel',json={'id':record['id']}).status_code==200
 assert c.get('/api/reminders/desktop-context').json()['workspace_id']==before
 record=c.post('/api/backups/restore/preview',content=archive.content).json()
 assert c.post('/api/backups/restore/confirm',json={**{k:record[k] for k in ('id','sha256')},'confirmation':'REPLACE WORKSPACE'}).status_code==200
 print(json.dumps({'id':before}))
''')
    if rollback:
        # Stop after the durable installation phase, before startup completion.
        run('''
import json,sys
from pathlib import Path
from offerpilot.backup_install import ensure_workspace_lease
ensure_workspace_lease(Path(sys.argv[1])/'data',apply_restore=True)
print(json.dumps({'installed':True}))
''')
    read = '''
import json,sys
from pathlib import Path
from fastapi.testclient import TestClient
from offerpilot.api import create_app
with TestClient(create_app(data_dir=Path(sys.argv[1])/'data')) as c:
 assert c.get('/api/reminders/desktop-context').status_code==401
 c.headers['X-OfferPilot-Token']='synthetic-current'
 print(json.dumps({'id':c.get('/api/reminders/desktop-context').json()['workspace_id'],
                   'state':c.get('/api/backups/restore').json()['state']}))
'''
    after = run(read)
    assert after['state'] == ('rolled_back' if rollback else 'completed')
    assert (initial['id'] == after['id']) is rollback
    assert run(read) == after


def test_context_requires_current_desktop_transport_token(tmp_path: Path):
    from fastapi.testclient import TestClient

    from offerpilot.config import Config, save_config
    from offerpilot.desktop import TOKEN_HEADER, DesktopSecurity, create_desktop_app

    data = tmp_path / 'data'
    static = tmp_path / 'web'
    static.mkdir()
    (static / 'index.html').write_text('<html>synthetic</html>')
    save_config(data, Config(auth_enabled=True, auth_token='web-only'))
    token = 'a1' * 32
    app = create_desktop_app(data_dir=data, static_dir=static,
                             security=DesktopSecurity(token=token, port=48271))
    with TestClient(app, base_url='http://127.0.0.1:48271') as client:
        path = '/api/reminders/desktop-context'
        for headers in ({}, {TOKEN_HEADER: 'b2' * 32}, {'X-OfferPilot-Token': 'web-only'}):
            assert client.get(path, headers=headers).status_code == 401
        response = client.get(path, headers={TOKEN_HEADER: token})
        assert response.status_code == 200
        identity = response.json()['workspace_id']
        assert len(identity) == 64 and identity != token
        assert client.get(path, headers={TOKEN_HEADER: identity}).status_code == 401
