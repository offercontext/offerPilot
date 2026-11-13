from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from offerpilot.backup_archive import stage_archive, workspace_files
from offerpilot.backup_export import export_archive
from offerpilot.db import init_database


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    sessions = init_database(root / "data.db")
    sessions.kw["bind"].dispose()
    return root


def test_export_restore_export_roundtrip(source: Path) -> None:
    (source / "agent-journal-key.json").write_text('"synthetic-secret"')
    (source / ".agent-journal-key.json.test.tmp").write_text('"synthetic-secret"')
    (source / "config.json").write_text('{"api_key":"synthetic-secret"}')
    before = workspace_files(source)
    first = source.parent / "first.zip"
    first.write_bytes(export_archive(source, '{"api_key":""}'))
    with ZipFile(BytesIO(first.read_bytes())) as archive:
        assert all("agent-journal-key.json" not in name for name in archive.namelist())
        assert json.loads(archive.read("config.json"))["api_key"] == ""
    restored = source.parent / "restored"
    stage_archive(first, restored, source / "data.db")
    second = source.parent / "second.zip"
    second.write_bytes(export_archive(restored, '{}'))
    result = stage_archive(second, source.parent / "again", source / "data.db")
    assert result["legacy"] is False
    assert workspace_files(source) == before


@pytest.mark.parametrize("limit", ["MAX_COMPRESSED", "MAX_EXPANDED", "MAX_MANIFEST"])
def test_export_rejects_limits_without_source_changes(source: Path, monkeypatch: pytest.MonkeyPatch,
                                                      limit: str) -> None:
    before = workspace_files(source)
    monkeypatch.setattr("offerpilot.backup_export." + limit, 1)
    with pytest.raises(ValueError, match="limit"):
        export_archive(source, '{}')
    assert workspace_files(source) == before


def test_final_archive_obeys_import_limits(source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = workspace_files(source)
    monkeypatch.setattr("offerpilot.backup_archive.MAX_COMPRESSED", 1)
    with pytest.raises(ValueError, match="archive_size_limit"):
        export_archive(source, '{}')
    assert workspace_files(source) == before


def test_nested_link_is_rejected(source: Path) -> None:
    from tests.conftest import symlink_or_skip

    other = source.parent / "outside"
    other.mkdir()
    (other / "secret").write_text("unchanged")
    symlink_or_skip(source / "linked", other, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe_workspace_file"):
        export_archive(source, '{}')
    assert (other / "secret").read_text() == "unchanged"


def test_missing_database_is_not_created(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="archive_database_missing"):
        export_archive(tmp_path, '{}')
    assert list(tmp_path.iterdir()) == []


def test_unreadable_directory_cannot_be_silently_omitted(source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    blocked = source / "blocked"
    blocked.mkdir()
    (blocked / "unreferenced-file").write_text("must not be omitted")
    before = workspace_files(source)
    original = os.scandir

    def deny(path):
        if not isinstance(path, int) and Path(path) == blocked:
            raise PermissionError("synthetic unreadable directory")
        return original(path)

    with monkeypatch.context() as patch:
        patch.setattr(os, "scandir", deny)
        with pytest.raises(PermissionError, match="synthetic"):
            export_archive(source, '{}')
    assert workspace_files(source) == before
