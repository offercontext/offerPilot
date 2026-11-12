from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from offerpilot.backup_archive import FORMAT, MANIFEST, stage_archive, workspace_files
from offerpilot.db import init_database


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    sessions = init_database(root / "data.db")
    sessions.kw["bind"].dispose()
    return root


def package(root: Path, *, manifest: bool = True) -> Path:
    archive = root.parent / "backup.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as stream:
        for name in workspace_files(root):
            stream.write(root / name, name)
        if manifest:
            stream.writestr(MANIFEST, json.dumps({"format": FORMAT, "files": workspace_files(root)}))
    return archive


def test_current_and_legacy_roundtrip(workspace: Path) -> None:
    for legacy in (False, True):
        destination = workspace.parent / str(legacy)
        result = stage_archive(package(workspace, manifest=not legacy), destination, workspace / "data.db")
        assert result["legacy"] is legacy
        assert result["counts"]["applications"] == 0
        assert (destination / "data.db").read_bytes() == (workspace / "data.db").read_bytes()


def test_manifest_mismatch_leaves_source_intact(workspace: Path) -> None:
    archive = package(workspace)
    before = workspace_files(workspace)
    with ZipFile(archive, "a") as stream:
        stream.writestr("extra", "unexpected")
    stage = workspace.parent / "stage"
    with pytest.raises(ValueError, match="manifest"):
        stage_archive(archive, stage, workspace / "data.db")
    assert not stage.exists()
    assert workspace_files(workspace) == before


def test_changed_schema_rejected_without_migration(workspace: Path) -> None:
    archive = package(workspace)
    with closing(sqlite3.connect(workspace / "data.db")) as db:
        db.execute("CREATE TABLE new_table (id INTEGER)")
        db.commit()
    with pytest.raises(ValueError, match="schema"):
        stage_archive(archive, workspace.parent / "stage", workspace / "data.db")


def test_sidecar_rejected(workspace: Path) -> None:
    (workspace / "data.db-wal").write_bytes(b"incomplete")
    with pytest.raises(ValueError, match="sidecars"):
        stage_archive(package(workspace), workspace.parent / "stage", workspace / "data.db")


def test_resume_cannot_reference_configuration(workspace: Path) -> None:
    with closing(sqlite3.connect(workspace / "data.db")) as db:
        db.execute("INSERT INTO resumes (name,file_path) VALUES ('synthetic','config.json')")
        db.commit()
    (workspace / "config.json").write_text("{}")
    with pytest.raises(ValueError, match="attachment_scope"):
        stage_archive(package(workspace), workspace.parent / "stage", workspace / "data.db")


def test_image_digest_and_missing_bytes(workspace: Path) -> None:
    image_id = "img_" + "a" * 32
    content = b"synthetic attachment"
    directory = workspace / "chat-images"
    directory.mkdir()
    path = directory / (image_id + ".image")
    path.write_bytes(content)
    with closing(sqlite3.connect(workspace / "data.db")) as db:
        db.execute("INSERT INTO chat_attachments (id,sha256,mime_type,size_bytes,width,height,"
                   "normalization_version,context_type,context_ref,bound,state,created_at) "
                   "VALUES (?,?,'image/png',?,1,1,'v1','workspace','',0,'ready',1)",
                   (image_id, hashlib.sha256(content).hexdigest(), len(content)))
        db.commit()
    stage_archive(package(workspace), workspace.parent / "good", workspace / "data.db")
    path.write_bytes(b"altered")
    with pytest.raises(ValueError, match="attachment_mismatch"):
        stage_archive(package(workspace), workspace.parent / "bad", workspace / "data.db")
    path.unlink()
    with pytest.raises(ValueError, match="attachment_missing"):
        stage_archive(package(workspace), workspace.parent / "missing", workspace / "data.db")


@pytest.mark.parametrize("manifest", ["[]", "null", "broken"])
def test_invalid_manifest_is_controlled(workspace: Path, manifest: str) -> None:
    archive = package(workspace, manifest=False)
    with ZipFile(archive, "a") as stream:
        stream.writestr(MANIFEST, manifest)
    with pytest.raises(ValueError, match="manifest"):
        stage_archive(archive, workspace.parent / "stage", workspace / "data.db")


def test_source_resume_path_is_checked(workspace: Path) -> None:
    with closing(sqlite3.connect(workspace / "data.db")) as db:
        db.execute("INSERT INTO resumes (name,file_path,source_file_path) VALUES ('test','','../secret')")
        db.commit()
    with pytest.raises(ValueError, match="unsafe_archive_path"):
        stage_archive(package(workspace), workspace.parent / "stage", workspace / "data.db")


def test_manifest_has_separate_limit(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("offerpilot.backup_archive.MAX_MANIFEST", 20)
    with pytest.raises(ValueError, match="manifest_limit"):
        stage_archive(package(workspace), workspace.parent / "stage", workspace / "data.db")
