from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

import pytest

from offerpilot import backup_restore as restore
from offerpilot.application_strategy.repository import (
    ApplicationStrategyRepository,
    ConfirmApplied,
    DecideChange,
    ProposeChange,
)
from offerpilot.backup_archive import stage_archive, workspace_files
from offerpilot.backup_export import export_archive
from offerpilot.db import init_database
from offerpilot.repositories.application_jd_versions import ApplicationJDService
from offerpilot.repositories.application_outcomes import (
    ApplicationOutcomesRepository,
    OutcomeCreate,
    SubmissionSnapshotCreate,
)
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.resumes import ResumeCreate, ResumesRepository
from tests.test_backup_install import cold
from tests.test_memory_metadata_backup import pack

_ATTACHMENT = "resumes/synthetic-strategy.txt"
_ATTACHMENT_BYTES = b"synthetic original submission material"
_PRE_STRATEGY_SCHEMA = "e52bf32280ed62a9d4c74e7ed1890fd231b95ca1a79406579b16fc767bd419df"


def _seed(root: Path) -> tuple[int, int, int]:
    sessions = init_database(root / "data.db")
    attachments = root / _ATTACHMENT
    attachments.parent.mkdir()
    attachments.write_bytes(_ATTACHMENT_BYTES)
    resume = ResumesRepository(sessions).create(ResumeCreate(
        title="Synthetic strategy résumé", content_json={"raw_text": "old archived content"}, file_path=_ATTACHMENT,
    ))
    apps = ApplicationsRepository(sessions)
    repo = ApplicationOutcomesRepository(sessions)
    specs = ApplicationJDService(sessions)

    def make_evidence(name: str, date: int, result: str) -> tuple[int, int, int]:
        app = apps.create(ApplicationCreate(company_name=name, position_name="Synthetic role"))
        spec = specs.create_version(
            app.id, jd_text="original JD", source_url=None, source_kind="ui",
            expected_current_version_id=None, idempotency_key="strategy-backup-jd",
        ).version
        submitted = datetime(2025, 8, date, 8, tzinfo=timezone.utc)
        occurred = submitted.replace(hour=10)
        snap = repo.create_snapshot(SubmissionSnapshotCreate(
            app.id, resume.id, spec.id, None, submitted, "verified archive note", "ui", "strategy-backup-archive",
        )).value
        outcome = repo.create_outcome(OutcomeCreate(
            app.id, snap.id, None, "screening", result, "verified original feedback",
            "", "", (), occurred, "ui", "strategy-backup-outcome",
        )).value
        return app.id, snap.id, outcome.id

    a, _, origin = make_evidence("Synthetic Origin", 12, "rejected")
    b, snapshot, outcome = make_evidence("Synthetic Target", 13, "advanced")
    strategies = ApplicationStrategyRepository(sessions)
    strategies.execute(a, origin, request_key="backup-strategy-propose", command=ProposeChange("Synthetic change"))
    strategies.execute(a, origin, request_key="backup-strategy-confirm", command=ConfirmApplied(0, b, snapshot, "Used once"))
    strategies.execute(a, origin, request_key="backup-strategy-decide", command=DecideChange(1, outcome, "keep"))
    sessions.kw["bind"].dispose()
    return origin, snapshot, outcome


def _predecessor(archive: Path, tmp_path: Path, *, before_voice: bool = False) -> Path:
    extracted = tmp_path / "predecessor"
    with ZipFile(archive) as zip_file:
        zip_file.extractall(extracted)  # fixture exported by this test
    with closing(sqlite3.connect(extracted / "data.db")) as db:
        db.execute("DROP TABLE application_strategy_receipts")
        db.execute("DROP TABLE application_strategy_changes")
        db.execute("DELETE FROM schema_migrations WHERE version='0042_application_strategy_changes'")
        rows = db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        # Hash generated independently by actual preceding master 13227d3b.
        assert sha256(json.dumps(rows).encode()).hexdigest() == _PRE_STRATEGY_SCHEMA
        if before_voice:
            db.execute("DROP TABLE mock_interview_answer_acceptance")
            db.execute("DROP TABLE interview_voice_sessions")
            db.execute("DELETE FROM schema_migrations WHERE version='0041_interview_voice_sessions'")
        db.commit()
    result = tmp_path / "before-strategy.zip"
    pack(extracted, result)
    return result


@pytest.mark.parametrize("era", ["current", "pre_strategy", "pre_voice"])
def test_strategy_backup_preserves_archives_attachments_and_available_decisions(tmp_path: Path, era: str) -> None:
    source = tmp_path / "source"
    origin, snapshot, outcome = _seed(source)
    archive = tmp_path / "current.zip"
    archive.write_bytes(export_archive(source, '{}'))
    if era != "current":
        archive = _predecessor(archive, tmp_path, before_voice=era == "pre_voice")
    archive_before = sha256(archive.read_bytes()).hexdigest()
    target = tmp_path / "live"
    initialize = subprocess.run(
        [sys.executable, "-c", "from pathlib import Path; from offerpilot.db import init_database; import sys;"
         "init_database(Path(sys.argv[1])).kw['bind'].dispose()", str(target / "data.db")],
        capture_output=True, text=True, timeout=30,
    )
    assert initialize.returncode == 0, initialize.stderr
    (target / "workspace-marker.txt").write_text("original live workspace")
    live_before = workspace_files(target)
    prepared = restore.prepare(target, archive)
    assert prepared["schema_upgrade"] == {
        "current": None,
        "pre_strategy": "application_strategy_v1",
        "pre_voice": "interview_voice_v1+application_strategy_v1",
    }[era]
    assert workspace_files(target) == live_before
    restore.confirm(target, prepared["id"], prepared["sha256"], "REPLACE WORKSPACE")
    installed = cold(target)
    assert installed.returncode == 0, installed.stderr
    assert restore.status(target)["state"] == "completed"
    assert (target / _ATTACHMENT).read_bytes() == _ATTACHMENT_BYTES
    assert sha256(archive.read_bytes()).hexdigest() == archive_before
    with closing(sqlite3.connect(target / "data.db")) as db:
        assert db.execute(
            "SELECT note,jd_snapshot FROM application_submission_snapshots WHERE id=?", (snapshot,)
        ).fetchone() == ("verified archive note", "original JD")
        assert db.execute(
            "SELECT submission_snapshot_id,feedback_text FROM application_outcomes WHERE id=?", (outcome,)
        ).fetchone() == (snapshot, "verified original feedback")
        records = db.execute(
            "SELECT origin_outcome_id,target_snapshot_id,target_outcome_id,decision FROM application_strategy_changes"
        ).fetchall()
        assert records == ([(origin, snapshot, outcome, "keep")] if era == "current" else [])
        assert db.execute("SELECT COUNT(*) FROM application_strategy_receipts").fetchone()[0] == (3 if era == "current" else 0)
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("sql", [
    "DROP TABLE application_strategy_receipts",
    "DROP INDEX ix_application_strategy_changes_target_snapshot_id",
    "ALTER TABLE application_strategy_changes ADD COLUMN changed TEXT",
    "DROP TABLE interview_voice_sessions",
])
def test_strategy_backup_rejects_partial_and_altered_schema(tmp_path: Path, sql: str) -> None:
    live = tmp_path / "live"
    init_database(live / "data.db").kw["bind"].dispose()
    live_before = workspace_files(live)
    initial = tmp_path / "initial.zip"
    initial.write_bytes(export_archive(live, '{}'))
    extracted = tmp_path / "partial"
    with ZipFile(initial) as zipped:
        zipped.extractall(extracted)
    with closing(sqlite3.connect(extracted / "data.db")) as db:
        db.execute(sql)
        db.commit()
    bad = tmp_path / "partial.zip"
    pack(extracted, bad)
    original = bad.read_bytes()
    stage = tmp_path / "stage"
    with pytest.raises(ValueError, match="archive_schema_mismatch"):
        stage_archive(bad, stage, live / "data.db")
    assert not stage.exists()
    assert workspace_files(live) == live_before
    assert bad.read_bytes() == original
