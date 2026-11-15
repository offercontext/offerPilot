"""Durable restore preparation and explicit confirmation; installation is separate."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any
from uuid import uuid4

from offerpilot.backup_archive import stage_archive
from offerpilot.backup_paths import archive_parts
from offerpilot.workspace_lock import exclusive_control

ACTIVE = {"validated", "queued", "switching", "installed"}
STATES = ACTIVE | {"none", "completed", "cancelled", "failed", "rolled_back"}
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[0-9a-f]{32}")


def sync_directory(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def require_directory(path: Path) -> None:
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
        raise ValueError("unsafe_restore_directory")


def store_path(root: Path) -> Path:
    root = root.resolve()
    path = root.parent / f".{root.name}.restores"
    if path.is_symlink():
        raise ValueError("unsafe_restore_store")
    path.mkdir(mode=0o700, exist_ok=True)
    require_directory(path)
    return path


def read_record(root: Path) -> dict[str, Any]:
    path = store_path(root) / "state.json"
    if not path.exists() and not path.is_symlink():
        return {"state": "none"}
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 64 * 1024 * 1024
            or getattr(metadata, "st_file_attributes", 0) & 0x400):
        raise ValueError("invalid_restore_receipt")
    record = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(record, dict) or not isinstance(record.get("state"), str)
            or record["state"] not in STATES):
        raise ValueError("invalid_restore_receipt")
    if record["state"] == "none":
        raise ValueError("invalid_restore_receipt")
    if not isinstance(record.get("id"), str) or _ID.fullmatch(record["id"]) is None:
        raise ValueError("invalid_restore_receipt")
    if not isinstance(record.get("sha256"), str) or _DIGEST.fullmatch(record["sha256"]) is None:
        raise ValueError("invalid_restore_receipt")
    files = record.get("files")
    if not isinstance(files, dict) or len(files) > 20_000:
        raise ValueError("invalid_restore_receipt")
    for name, digest in files.items():
        archive_parts(name)
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise ValueError("invalid_restore_receipt")
    return record


def write_record(root: Path, record: dict[str, Any]) -> None:
    store = store_path(root)
    temporary = store / (uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        temporary.chmod(0o600)
        json.dump(record, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, store / "state.json")
    sync_directory(store)


def operation_paths(root: Path, record: dict[str, Any]) -> tuple[Path, Path, Path]:
    operation_id = record["id"]
    if not isinstance(operation_id, str) or _ID.fullmatch(operation_id) is None:
        raise ValueError("invalid_restore_receipt")
    store = store_path(root)
    paths = (store / (operation_id + "-candidate"), store / (operation_id + "-original"),
             store / (operation_id + "-failed"))
    for path in paths:
        if path.exists() or path.is_symlink():
            require_directory(path)
    return paths


def public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "files"}


def status(root: Path) -> dict[str, Any]:
    root = root.resolve()
    with exclusive_control(root):
        return public_record(read_record(root))


def prepare(root: Path, archive: Path) -> dict[str, Any]:
    root = root.resolve()
    with exclusive_control(root):
        if read_record(root)["state"] in ACTIVE:
            raise ValueError("restore_already_pending")
        record: dict[str, Any] = {"id": uuid4().hex, "state": "validated"}
        candidate, _, _ = operation_paths(root, record)
        record.update(stage_archive(archive, candidate, root / "data.db"))
        write_record(root, record)
        return public_record(record)


def confirm(root: Path, operation_id: str, digest: str, confirmation: str) -> dict[str, Any]:
    root = root.resolve()
    with exclusive_control(root):
        record = read_record(root)
        if (record.get("id") != operation_id or record.get("sha256") != digest
                or confirmation != "REPLACE WORKSPACE"):
            raise ValueError("restore_confirmation_mismatch")
        if record["state"] == "queued":
            return public_record(record)
        if record["state"] != "validated":
            raise ValueError("restore_not_confirmable")
        record["state"] = "queued"
        write_record(root, record)
        return public_record(record)


def cancel(root: Path, operation_id: str) -> dict[str, Any]:
    root = root.resolve()
    with exclusive_control(root):
        record = read_record(root)
        if record.get("id") != operation_id:
            raise ValueError("restore_operation_mismatch")
        if record["state"] == "cancelled":
            return public_record(record)
        if record["state"] not in {"validated", "queued"}:
            raise ValueError("restore_not_cancellable")
        record["state"] = "cancelled"
        write_record(root, record)
        return public_record(record)
