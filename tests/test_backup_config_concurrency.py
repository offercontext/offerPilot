from __future__ import annotations

import json
import subprocess
import sys

from offerpilot import backup_restore as restore
from tests.test_backup_install import cold, queued

__all__ = ["queued"]


def test_config_read_write_process_blocks_install_until_it_exits(queued):
    root, old = queued
    restore.cancel(root, old['id'])
    record = restore.prepare(root, root.parent / 'backup.zip')
    code = f'''
from pathlib import Path
from offerpilot.config import load_config, save_config
root=Path({str(root)!r})
config=load_config(root)
config.confirmation_secret='synthetic-old-confirmation'
print('read',flush=True)
input()
save_config(root,config)
'''
    process = subprocess.Popen([sys.executable, '-u', '-c', code], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == 'read'
        restore.confirm(root, record['id'], record['sha256'], 'REPLACE WORKSPACE')
        assert cold(root).returncode != 0
        assert restore.status(root)['state'] == 'queued'
        _, errors = process.communicate('\n', timeout=20)
        assert process.returncode == 0, errors
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    assert cold(root).returncode == 0
    config = json.loads((root / 'config.json').read_text(encoding='utf-8'))
    assert config['confirmation_secret'] != 'synthetic-old-confirmation'


def test_new_config_reader_cannot_enter_queued_workspace(queued):
    root, _ = queued
    result = subprocess.run([sys.executable, '-c', 'from pathlib import Path; '
                             'from offerpilot.config import load_config; '
                             f'load_config(Path({str(root)!r}))'], capture_output=True, timeout=20)
    assert result.returncode != 0
    assert restore.status(root)['state'] == 'queued'
