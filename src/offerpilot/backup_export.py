"""Consistent SQLite snapshots whose final ZIP passes the restore preflight limits."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from offerpilot.backup_archive import (
    FORMAT, MANIFEST, MAX_COMPRESSED, MAX_EXPANDED, MAX_MANIFEST,
    stage_archive, validate_database, workspace_files,
)
from offerpilot.backup_paths import archive_parts

_EXCLUDED = {MANIFEST, "data.db", "data.db-wal", "data.db-shm", "data.db-journal",
             "agent-journal-key.json", "config.json"}


def export_archive(root: Path, redacted_config: str) -> bytes:
    """Do not mutate live data; reject archives that cannot pass bounded import."""
    root = root.resolve()
    database = root / "data.db"
    if not database.is_file() or database.is_symlink():
        raise ValueError("archive_database_missing")
    with tempfile.TemporaryDirectory(prefix="offerpilot-export-") as directory:
        stage = Path(directory) / "workspace"
        stage.mkdir(mode=0o700)
        # Block DB commits, using a separate reader for the SQLite backup API.
        with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=1)) as guard:
            guard.execute("BEGIN IMMEDIATE")
            with closing(sqlite3.connect((root / "data.db").as_uri() + "?mode=ro", uri=True)) as source, closing(
                sqlite3.connect(stage / "data.db")
            ) as target:
                source.backup(target)
            total = (stage / "data.db").stat().st_size
            for name in workspace_files(root):
                if name in _EXCLUDED or name.startswith(".agent-journal-key.json."):
                    continue
                archive_parts(name)
                path = root / name
                destination = stage / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with path.open("rb") as source_file, destination.open("xb") as target_file:
                    for block in iter(lambda: source_file.read(1024 * 1024), b""):
                        total += len(block)
                        if total > MAX_EXPANDED:
                            raise ValueError("archive_size_limit")
                        target_file.write(block)
            guard.rollback()
        config_bytes = redacted_config.encode("utf-8")
        if total + len(config_bytes) > MAX_EXPANDED:
            raise ValueError("archive_size_limit")
        (stage / "config.json").write_bytes(config_bytes)
        validate_database(stage, root / "data.db")
        files = workspace_files(stage)
        manifest = json.dumps({"format": FORMAT, "files": files}, ensure_ascii=False).encode("utf-8")
        if len(manifest) > MAX_MANIFEST:
            raise ValueError("archive_manifest_limit")
        if total + len(config_bytes) + len(manifest) > MAX_EXPANDED:
            raise ValueError("archive_size_limit")
        archive = Path(directory) / "backup.zip"
        with ZipFile(archive, "w", ZIP_DEFLATED) as stream:
            for name in files:
                stream.write(stage / name, name)
            stream.writestr(MANIFEST, manifest)
        if archive.stat().st_size > MAX_COMPRESSED:
            raise ValueError("archive_size_limit")
        # Validate serialized names: Windows ZipInfo.from_file retains native separators
        # in orig_filename in memory, while the actual ZIP stores canonical slashes.
        # File writes are outside SQLite transactions; validate the actual frozen ZIP.
        stage_archive(archive, Path(directory) / "verification", stage / "data.db")
        return archive.read_bytes()
