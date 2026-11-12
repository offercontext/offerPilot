"""Bounded staging and validation, without opening an uploaded DB for migrations."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from offerpilot.backup_paths import archive_parts, validate_archive_index

MAX_COMPRESSED = 256 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024
MAX_MANIFEST = 8 * 1024 * 1024
MANIFEST = "offerpilot-backup.json"
FORMAT = "offerpilot-workspace-v1"


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def workspace_files(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("unsafe_workspace_file")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = file_digest(path)
        elif not path.is_dir():
            raise ValueError("unsafe_workspace_file")
    return result


def stage_archive(archive_path: Path, destination: Path, reference_db: Path) -> dict[str, Any]:
    """Create a new private stage; discard only that stage on any validation failure."""
    if archive_path.stat().st_size > MAX_COMPRESSED:
        raise ValueError("archive_size_limit")
    destination.mkdir(mode=0o700)
    try:
        with ZipFile(archive_path) as archive:
            entries = archive.infolist()
            validate_archive_index(entries)
            expanded = 0
            for entry in entries:
                if entry.is_dir():
                    continue
                if entry.filename == MANIFEST and entry.file_size > MAX_MANIFEST:
                    raise ValueError("archive_manifest_limit")
                expanded += entry.file_size
                if expanded > MAX_EXPANDED or entry.file_size > max(1, entry.compress_size) * 1000:
                    raise ValueError("archive_size_limit")
            actual_total = 0
            for entry in entries:
                path = destination.joinpath(*archive_parts(entry.filename, directory=entry.is_dir()))
                if entry.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, path.open("xb") as target:
                    path.chmod(0o600)
                    actual_size = 0
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        actual_size += len(block)
                        actual_total += len(block)
                        if actual_total > MAX_EXPANDED or actual_size > entry.file_size:
                            raise ValueError("archive_size_limit")
                        target.write(block)
                    if actual_size != entry.file_size:
                        raise ValueError("archive_size_mismatch")
        files = workspace_files(destination)
        legacy = MANIFEST not in files
        if not legacy:
            try:
                manifest = json.loads((destination / MANIFEST).read_text(encoding="utf-8"))
            except (ValueError, UnicodeError) as exc:
                raise ValueError("archive_manifest_invalid") from exc
            expected = {name: value for name, value in files.items() if name != MANIFEST}
            if (not isinstance(manifest, dict) or manifest.get("format") != FORMAT
                    or manifest.get("files") != expected):
                raise ValueError("archive_manifest_mismatch")
        counts = validate_database(destination, reference_db)
        return {"files": files, "legacy": legacy, "counts": counts,
                "sha256": file_digest(archive_path)}
    except BaseException:
        shutil.rmtree(destination)
        raise


def _open_readonly(path: Path, *, immutable: bool = True) -> sqlite3.Connection:
    if not path.is_file() or path.is_symlink():
        raise ValueError("archive_database_missing")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro" + ("&immutable=1" if immutable else ""),
                         uri=True)
    db.execute("PRAGMA trusted_schema=OFF")
    return db


def _schema(db: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                      "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()


def _attachment(root: Path, name: str, prefix: tuple[str, ...], *,
                digest: str | None = None, size: int | None = None) -> None:
    parts = archive_parts(name)
    if parts[:len(prefix)] != prefix or len(parts) <= len(prefix):
        raise ValueError("archive_attachment_scope")
    path = root.joinpath(*parts)
    if not path.is_file() or path.is_symlink():
        raise ValueError("archive_attachment_missing")
    if (size is not None and path.stat().st_size != size) or (
        digest is not None and file_digest(path) != digest
    ):
        raise ValueError("archive_attachment_mismatch")


def validate_database(root: Path, reference_db: Path) -> dict[str, int]:
    """Check exact current schema, relational integrity and owned attachment bytes."""
    if any((root / ("data.db" + suffix)).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("archive_database_sidecars")
    with closing(_open_readonly(root / "data.db")) as db, closing(
        _open_readonly(reference_db, immutable=False)
    ) as reference:
        if _schema(db) != _schema(reference):
            raise ValueError("archive_schema_mismatch")
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)] or db.execute(
            "PRAGMA foreign_key_check"
        ).fetchone() is not None:
            raise ValueError("archive_database_integrity")
        for (name,) in db.execute("SELECT file_path FROM resumes WHERE file_path != '' "
                                 "UNION SELECT source_file_path FROM resumes WHERE source_file_path != ''"):
            _attachment(root, name, ("resumes",))
        for source_id, name in db.execute("SELECT id,main_relative_path FROM knowledge_sources"):
            _attachment(root, name, ("knowledge", "sources", str(source_id)))
        for source_id, name, size, digest in db.execute(
            "SELECT source_id,relative_path,bytes,sha256 FROM knowledge_source_assets"
        ):
            _attachment(root, name, ("knowledge", "sources", str(source_id), "assets"),
                        digest=digest, size=size)
        for image_id, size, digest in db.execute("SELECT id,size_bytes,sha256 FROM chat_attachments"):
            if re.fullmatch(r"img_[0-9a-f]{32}", image_id) is None:
                raise ValueError("archive_attachment_scope")
            _attachment(root, f"chat-images/{image_id}.image", ("chat-images",), digest=digest, size=size)
        key_ids = {row[0] for row in db.execute("SELECT DISTINCT fingerprint_key_id FROM write_operations")}
        key_path = root / "write-operation-ledger.key"
        if key_ids or key_path.exists():
            from offerpilot.ai.write_operations import _read_ledger_key
            key = _read_ledger_key(key_path)
            if key_ids - {key.key_id}:
                raise ValueError("archive_ledger_key_mismatch")
        return {table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in ("applications", "resumes", "conversations", "knowledge_sources")}
