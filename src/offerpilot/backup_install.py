"""Install queued restores only at cold service startup under exclusive ownership."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from offerpilot.backup_archive import validate_database, workspace_files
from offerpilot.backup_restore import (
    operation_paths, read_record, require_directory, store_path, sync_directory, write_record,
)
from offerpilot.workspace_lock import (
    acquire_workspace, exclusive_control, has_lease, retain_shared,
)


_installed_here: set[Path] = set()

def _reset_authority(root: Path, candidate: Path) -> None:
    from offerpilot.config import Config

    path = root / "config.json"
    if path.is_symlink():
        raise ValueError("unsafe_restore_configuration")
    config = Config.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else Config()
    config.chat_auto_approve_writes = False
    config.confirmation_secret = secrets.token_urlsafe(32)
    (candidate / "config.json").write_text(config.model_dump_json(indent=2), encoding="utf-8")
    (candidate / "config.json").chmod(0o600)
    journal = "agent-journal-key.json"
    if (root / journal).is_symlink():
        raise ValueError("unsafe_restore_configuration")
    (candidate / journal).unlink(missing_ok=True)
    if (root / journal).exists():
        shutil.copyfile(root / journal, candidate / journal)
        (candidate / journal).chmod(0o600)
    with closing(sqlite3.connect(candidate / "data.db")) as db:
        db.execute("PRAGMA foreign_keys=ON")
        for row_id, payload in db.execute("SELECT id,settings_json FROM proactive_settings").fetchall():
            settings = json.loads(payload)
            settings.update(enabled=False, drafts_enabled=False)
            db.execute("UPDATE proactive_settings SET settings_json=?,revision=revision+1 WHERE id=?",
                       (json.dumps(settings), row_id))
        db.execute("UPDATE proactive_jobs SET state='cancelled',owner='',lease_until=0 "
                   "WHERE state IN ('pending','leased','running')")
        db.execute("UPDATE external_agent_connections SET active=0")
        db.execute("UPDATE job_mail_connections SET status='disconnected',ai_enabled=0,"
                   "credential_ref=NULL,next_run_at=NULL")
        db.execute("UPDATE knowledge_jobs SET canceled=1,status='canceled' "
                   "WHERE status IN ('pending','running')")
        db.commit()


def _sync_parents(root: Path) -> None:
    sync_directory(root.parent)
    sync_directory(store_path(root))


def _rollback(root: Path, record: dict[str, Any]) -> None:
    candidate, original, failed = operation_paths(root, record)
    if original.exists():
        if root.exists():
            require_directory(root)
            if failed.exists():
                raise ValueError("restore_rollback_conflict")
            os.replace(root, failed)
            _sync_parents(root)
        os.replace(original, root)
        _sync_parents(root)
    elif not root.exists() or (not candidate.exists() and not failed.exists()):
        raise ValueError("restore_original_missing")
    else:
        require_directory(root)
    record["state"] = "rolled_back"
    write_record(root, record)


def _install(root: Path, record: dict[str, Any]) -> None:
    candidate, original, failed = operation_paths(root, record)
    try:
        require_directory(root)
        if original.exists() or failed.exists():
            raise ValueError("restore_directory_conflict")
        if workspace_files(candidate) != record["files"]:
            raise ValueError("restore_stage_changed")
        validate_database(candidate, root / "data.db")
        _reset_authority(root, candidate)
        for path in candidate.rglob("*"):
            if path.is_file():
                # Windows _commit requires a writable descriptor; never truncate staged bytes.
                with path.open("r+b") as stream:
                    os.fsync(stream.fileno())
        for path in sorted((p for p in candidate.rglob("*") if p.is_dir()), reverse=True):
            sync_directory(path)
        sync_directory(candidate)
        record["state"] = "switching"
        write_record(root, record)
        os.replace(root, original)
        _sync_parents(root)
        os.replace(candidate, root)
        _sync_parents(root)
        record["state"] = "installed"
        write_record(root, record)
    except BaseException:
        if record["state"] in {"switching", "installed"}:
            _rollback(root, record)
        else:
            record["state"] = "failed"
            write_record(root, record)
        raise


def ensure_workspace_lease(root: Path, *, apply_restore: bool = False) -> None:
    root = root.resolve()
    if has_lease(root):
        return
    root.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_control(root):
        record = read_record(root)
        pending = record["state"] in {"queued", "switching", "installed"}
        if pending and not apply_restore:
            raise ValueError("restore_requires_service_restart")
        fd = acquire_workspace(root, exclusive=pending)
        try:
            if record["state"] in {"switching", "installed"}:
                _rollback(root, record)
            elif record["state"] == "queued":
                _install(root, record)
                _installed_here.add(root)
            retain_shared(root, fd)
        except BaseException:
            os.close(fd)
            raise


def complete_startup(root: Path) -> None:
    root = root.resolve()
    with exclusive_control(root):
        record = read_record(root)
        if record["state"] == "installed":
            if root not in _installed_here or not has_lease(root):
                raise ValueError("restore_startup_owner_required")
            record["state"] = "completed"
            write_record(root, record)
