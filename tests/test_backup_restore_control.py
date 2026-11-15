from __future__ import annotations

import json
from pathlib import Path

import pytest

from offerpilot import backup_restore as restore
from offerpilot.backup_archive import workspace_files
from offerpilot.backup_export import export_archive
from offerpilot.db import init_database


@pytest.fixture
def prepared(tmp_path: Path):
    root = tmp_path / "live"
    sessions = init_database(root / "data.db")
    sessions.kw["bind"].dispose()
    (root / "中文资料.txt").write_text("synthetic", encoding="utf-8")
    archive = tmp_path / "backup.zip"
    archive.write_bytes(export_archive(root, '{}'))
    return root, restore.prepare(root, archive), archive


def test_confirmation_cancel_and_repetition_preserve_live(prepared) -> None:
    root, record, _ = prepared
    before = workspace_files(root)
    assert "files" not in record
    with pytest.raises(ValueError, match="confirmation"):
        restore.confirm(root, record["id"], record["sha256"], "yes")
    for _ in range(2):
        assert restore.confirm(root, record["id"], record["sha256"], "REPLACE WORKSPACE")["state"] == "queued"
    for _ in range(2):
        assert restore.cancel(root, record["id"])["state"] == "cancelled"
    with pytest.raises(ValueError, match="confirmable"):
        restore.confirm(root, record["id"], record["sha256"], "REPLACE WORKSPACE")
    assert workspace_files(root) == before


def test_only_one_pending_preview(prepared) -> None:
    root, record, archive = prepared
    with pytest.raises(ValueError, match="already_pending"):
        restore.prepare(root, archive)
    assert restore.status(root)["id"] == record["id"]


def test_alias_and_utf8_receipt(prepared) -> None:
    from tests.conftest import symlink_or_skip

    root, record, _ = prepared
    alias = root.parent / "alias"
    symlink_or_skip(alias, root, target_is_directory=True)
    assert restore.status(alias)["id"] == record["id"]
    receipt = restore.read_record(alias)
    assert "中文资料.txt" in receipt["files"]
    raw = (restore.store_path(root) / "state.json").read_bytes()
    assert "中文资料.txt".encode() in raw


@pytest.mark.parametrize("field,value", [("state", []), ("state", "unknown"), ("id", "../bad"), ("sha256", "bad"), ("files", {"../bad": "a" * 64})])
def test_invalid_receipt_fails_closed(prepared, field, value) -> None:
    root, _, _ = prepared
    before = workspace_files(root)
    record = restore.read_record(root)
    record[field] = value
    (restore.store_path(root) / "state.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError):
        restore.status(root)
    assert workspace_files(root) == before


def test_linked_candidate_rejected_before_operation(prepared) -> None:
    from tests.conftest import symlink_or_skip

    root, record, _ = prepared
    candidate, _, _ = restore.operation_paths(root, record)
    outside = root.parent / "outside"
    candidate.rename(outside)
    before = workspace_files(outside)
    symlink_or_skip(candidate, outside, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe_restore_directory"):
        restore.operation_paths(root, record)
    assert workspace_files(outside) == before


@pytest.mark.parametrize("field", ["id", "sha256"])
def test_confirmation_identity_cannot_change(prepared, field) -> None:
    root, record, _ = prepared
    wrong = dict(record)
    wrong[field] = "0" * (32 if field == "id" else 64)
    with pytest.raises(ValueError, match="confirmation_mismatch"):
        restore.confirm(root, wrong["id"], wrong["sha256"], "REPLACE WORKSPACE")
    assert restore.status(root)["state"] == "validated"


def test_receipt_link_is_rejected(prepared) -> None:
    from tests.conftest import symlink_or_skip

    root, _, _ = prepared
    receipt = restore.store_path(root) / "state.json"
    external = root.parent / "receipt-original.json"
    receipt.rename(external)
    before = external.read_bytes()
    symlink_or_skip(receipt, external)
    with pytest.raises(ValueError, match="invalid_restore_receipt"):
        restore.status(root)
    assert external.read_bytes() == before
