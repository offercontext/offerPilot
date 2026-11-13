from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from offerpilot import backup_restore as restore
from offerpilot.backup_archive import workspace_files
from offerpilot.backup_export import export_archive
from offerpilot.workspace_lock import acquire_workspace


@pytest.fixture
def queued(tmp_path: Path):
    root = tmp_path / "live"
    subprocess.run([sys.executable, "-c", "from pathlib import Path; "
                    "from offerpilot.db import init_database; "
                    f"s=init_database(Path({str(root / 'data.db')!r})); s.kw['bind'].dispose()"],
                   check=True, timeout=20)
    (root / "中文.txt").write_text("backup", encoding="utf-8")
    archive = tmp_path / "backup.zip"
    archive.write_bytes(export_archive(root, '{}'))
    (root / "中文.txt").write_text("original", encoding="utf-8")
    record = restore.prepare(root, archive)
    restore.confirm(root, record["id"], record["sha256"], "REPLACE WORKSPACE")
    return root, record


def cold(root: Path, *, complete=True, apply=True):
    code = ("from pathlib import Path; from offerpilot.backup_install import "
            "ensure_workspace_lease,complete_startup; "
            f"root=Path({str(root)!r}); ensure_workspace_lease(root,apply_restore={apply}); "
            + ("complete_startup(root)" if complete else ""))
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)


def test_success_retains_original_and_does_not_repeat(queued):
    root, record = queued
    before = workspace_files(root)
    result = cold(root)
    assert result.returncode == 0, result.stderr
    assert (root / "中文.txt").read_text(encoding="utf-8") == "backup"
    assert restore.status(root)["state"] == "completed"
    _, original, _ = restore.operation_paths(root, record)
    assert workspace_files(original) == before
    (root / "中文.txt").write_text("new work", encoding="utf-8")
    assert cold(root).returncode == 0
    assert (root / "中文.txt").read_text(encoding="utf-8") == "new work"


def test_unfinished_startup_rolls_back(queued):
    root, _ = queued
    before = workspace_files(root)
    result = cold(root, complete=False)
    assert result.returncode == 0, result.stderr
    assert restore.status(root)["state"] == "installed"
    assert cold(root).returncode == 0
    assert restore.status(root)["state"] == "rolled_back"
    assert workspace_files(root) == before


def test_ordinary_process_and_live_owner_block_install(queued):
    root, _ = queued
    before = workspace_files(root)
    assert cold(root, apply=False).returncode != 0
    fd = acquire_workspace(root)
    try:
        assert cold(root).returncode != 0
        assert restore.status(root)["state"] == "queued"
        assert workspace_files(root) == before
    finally:
        os.close(fd)
    assert cold(root).returncode == 0


def test_tampering_fails_before_switch(queued):
    root, record = queued
    before = workspace_files(root)
    candidate, _, _ = restore.operation_paths(root, record)
    (candidate / "中文.txt").write_text("changed", encoding="utf-8")
    assert cold(root).returncode != 0
    assert restore.status(root)["state"] == "failed"
    assert workspace_files(root) == before


@pytest.mark.parametrize("after", ["original", "candidate"])
def test_process_death_between_renames_rolls_back(queued, after):
    root, _ = queued
    before = workspace_files(root)
    code = f'''
import os
from pathlib import Path
from offerpilot import backup_install as install
replace = os.replace
def crash(source, target):
    replace(source, target)
    if ({after!r} == 'original' and str(target).endswith('-original')) or (
        {after!r} == 'candidate' and str(source).endswith('-candidate')):
        os._exit(91)
install.os.replace = crash
install.ensure_workspace_lease(Path({str(root)!r}), apply_restore=True)
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=20)
    assert result.returncode == 91, result.stderr
    assert cold(root).returncode == 0
    assert restore.status(root)["state"] == "rolled_back"
    assert workspace_files(root) == before


def test_restored_authorities_are_disabled_and_local_secrets_retained(tmp_path: Path):
    import base64
    import json
    import sqlite3
    from contextlib import closing
    from offerpilot.config import Config

    root = tmp_path / "live"
    seed = f'''
from pathlib import Path
from datetime import datetime, timezone
from offerpilot.db import init_database
from offerpilot.models import KnowledgeJob
from offerpilot.proactive.models import ProactiveSettings, ProactiveJob
from offerpilot.job_mail.models import JobMailConnection
from offerpilot.external_agents.models import ExternalAgentConnection
sessions=init_database(Path({str(root / 'data.db')!r}))
with sessions() as db:
    db.add(ProactiveSettings(id=1,revision=4,settings_json='{{"enabled":true,"drafts_enabled":true}}'))
    db.add(ProactiveJob(id='job',idempotency_key='key',kind='draft',application_id=1,source_digest='digest',state='running',reason='synthetic',due_at=1,expires_at=2,created_at=1,owner='old-owner',lease_until=999))
    db.add(JobMailConnection(id='mail',scope_id='scope',status='connected',ai_enabled=True,credential_ref='synthetic-vault-reference',next_run_at=datetime.now(timezone.utc)))
    db.add(ExternalAgentConnection(id='external',name='synthetic',application_ids_json='[]',workspace=True,allow_scheduling=True,active=True,created_at=datetime.now(timezone.utc)))
    db.add(KnowledgeJob(kind='brief',queue='llm',status='pending'))
    db.commit()
sessions.kw['bind'].dispose()
'''
    subprocess.run([sys.executable, "-c", seed], check=True, timeout=20)
    archive = tmp_path / "backup.zip"
    archive.write_bytes(export_archive(root, '{}'))
    config = Config(auth_enabled=True, auth_token="synthetic-local-login", api_key="synthetic-local-key",
                    chat_auto_approve_writes=True, confirmation_secret="synthetic-old-confirmation")
    (root / "config.json").write_text(config.model_dump_json(), encoding="utf-8")
    journal = {"schema_version": 1, "key_id": "11111111-1111-4111-8111-111111111111",
               "secret": base64.urlsafe_b64encode(b"j" * 32).decode().rstrip("=")}
    journal_bytes = json.dumps(journal).encode()
    (root / "agent-journal-key.json").write_bytes(journal_bytes)
    record = restore.prepare(root, archive)
    restore.confirm(root, record["id"], record["sha256"], "REPLACE WORKSPACE")
    result = cold(root)
    assert result.returncode == 0, result.stderr
    restored = json.loads((root / "config.json").read_text(encoding="utf-8"))
    assert restored["auth_token"] == config.auth_token
    assert restored["api_key"] == config.api_key
    assert restored["chat_auto_approve_writes"] is False
    assert restored["confirmation_secret"] != config.confirmation_secret
    assert (root / "agent-journal-key.json").read_bytes() == journal_bytes
    with closing(sqlite3.connect(root / "data.db")) as db:
        settings, revision = db.execute("SELECT settings_json,revision FROM proactive_settings").fetchone()
        assert json.loads(settings) == {"enabled": False, "drafts_enabled": False}
        assert revision == 5
        assert db.execute("SELECT state,owner,lease_until FROM proactive_jobs").fetchone() == ('cancelled', '', 0)
        assert db.execute("SELECT active FROM external_agent_connections").fetchone() == (0,)
        assert db.execute("SELECT status,ai_enabled,credential_ref,next_run_at FROM job_mail_connections").fetchone() == ('disconnected', 0, None, None)
        assert db.execute("SELECT status,canceled FROM knowledge_jobs").fetchone() == ('canceled', 1)


@pytest.mark.parametrize("after", ["failed", "restored"])
def test_process_death_during_rollback_is_recoverable(queued, after):
    root, _ = queued
    before = workspace_files(root)
    assert cold(root, complete=False).returncode == 0
    code = f'''
import os
from pathlib import Path
from offerpilot import backup_install as install
replace = os.replace
def crash(source, target):
    replace(source, target)
    if ({after!r} == 'failed' and str(target).endswith('-failed')) or (
        {after!r} == 'restored' and str(source).endswith('-original')):
        os._exit(92)
install.os.replace = crash
install.ensure_workspace_lease(Path({str(root)!r}), apply_restore=True)
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=20)
    assert result.returncode == 92, result.stderr
    assert cold(root).returncode == 0
    assert restore.status(root)["state"] == "rolled_back"
    assert workspace_files(root) == before
