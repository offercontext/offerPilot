from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from offerpilot.backup_archive import stage_archive, validate_database
from offerpilot.backup_export import export_archive
from offerpilot.db import init_database


def _initialize(root: Path) -> None:
    factory = init_database(root / 'data.db')
    factory.kw['bind'].dispose()


def _raw_schema(root: Path):
    with sqlite3.connect(root / 'data.db') as db:
        return db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()


def test_fresh_and_reopened_database_have_identical_raw_schema(tmp_path: Path):
    _initialize(tmp_path)
    before = _raw_schema(tmp_path)
    _initialize(tmp_path)
    assert _raw_schema(tmp_path) == before


@pytest.mark.parametrize('trigger', ['trg_write_operation_compensation_insert',
                                    'trg_write_operation_compensation_update'])
def test_only_exact_known_legacy_trigger_sql_is_compatible(tmp_path: Path, trigger: str):
    reference, legacy = tmp_path / 'reference', tmp_path / 'legacy'
    _initialize(reference)
    _initialize(legacy)
    fixture = json.loads((Path(__file__).parent / 'fixtures/backups/legacy-compensation-layout-5e99.json').read_text())
    assert fixture['source_commit'] == '5e99b33836995640e9c8b510fb921addad8bceb8'
    with sqlite3.connect(legacy / 'data.db') as db:
        for name, sql in fixture['triggers'].items():
            db.execute(f'DROP TRIGGER {name}')
            db.execute(sql)
    assert _raw_schema(legacy) != _raw_schema(reference)
    validate_database(legacy, reference / 'data.db')
    archive = tmp_path / 'legacy-layout.zip'
    archive.write_bytes(export_archive(legacy, '{}'))
    stage_archive(archive, tmp_path / 'candidate', reference / 'data.db')
    for old, new in [("parent.status = 'committed'", "parent.status = 'failed'"),
                     ("undo:create_offer", "undo:other"), ('SELECT 1', 'SELECT 2'), ('parent.status =', 'parent.status  =')]:
        with sqlite3.connect(legacy / 'data.db') as db:
            db.execute(f'DROP TRIGGER {trigger}')
            sql = fixture['triggers'][trigger]
            assert old in sql
            db.execute(sql.replace(old, new))
        with pytest.raises(ValueError, match='archive_schema_mismatch'):
            validate_database(legacy, reference / 'data.db')


def test_same_archive_can_restore_twice_through_real_cold_api(tmp_path: Path):
    seed = '''
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from offerpilot.api import create_app
p=Path(sys.argv[1])
with TestClient(create_app(data_dir=p/'data')) as c:
 b=c.get('/api/backups/export');assert b.status_code==200
 (p/'backup.zip').write_bytes(b.content)
 r=c.post('/api/backups/restore/preview',content=b.content);assert r.status_code==200,r.text
 d=r.json();r=c.post('/api/backups/restore/confirm',json={**{k:d[k] for k in ('id','sha256')},'confirmation':'REPLACE WORKSPACE'});assert r.status_code==200,r.text
'''
    restore = '''
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from offerpilot.api import create_app
p=Path(sys.argv[1])
with TestClient(create_app(data_dir=p/'data')) as c:
 assert c.get('/api/backups/restore').json()['state']=='completed'
 r=c.post('/api/backups/restore/preview',content=(p/'backup.zip').read_bytes());assert r.status_code==200,r.text
 d=r.json()
 if sys.argv[2]=='last':
  r=c.post('/api/backups/restore/cancel',json={'id':d['id']})
 else:
  r=c.post('/api/backups/restore/confirm',json={**{k:d[k] for k in ('id','sha256')},'confirmation':'REPLACE WORKSPACE'})
 assert r.status_code==200,r.text
'''
    for code, stage in [(seed, 'seed'), (restore, 'again'), (restore, 'last')]:
        result = subprocess.run([sys.executable, '-c', code, str(tmp_path), stage],
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
