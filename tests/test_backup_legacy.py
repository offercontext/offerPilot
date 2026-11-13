from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from offerpilot.backup_archive import stage_archive, workspace_files
from offerpilot.db import init_database


def test_actual_historical_export_is_rejected_without_changing_current_workspace(tmp_path: Path) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/backups/legacy-d0cd4068.json").read_text(encoding="utf-8"))
    assert fixture["source_commit"] == "d0cd40688f9ec4802c760d7ae8e868516ca3ba9f"
    content = base64.b64decode(fixture["archive_base64"], validate=True)
    assert hashlib.sha256(content).hexdigest() == fixture["archive_sha256"]
    archive = tmp_path / "legacy.zip"
    archive.write_bytes(content)
    current = tmp_path / "current"
    sessions = init_database(current / "data.db")
    sessions.kw["bind"].dispose()
    before = workspace_files(current)
    candidate = tmp_path / "candidate"
    with pytest.raises(ValueError, match="archive_schema_mismatch"):
        stage_archive(archive, candidate, current / "data.db")
    assert not candidate.exists()
    assert workspace_files(current) == before
    assert archive.read_bytes() == content
