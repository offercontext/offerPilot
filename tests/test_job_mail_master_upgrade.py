"""Real pinned-master schema -> mail branch -> repeated initialization and startup.

Requires the baseline Git object locally (CI checkout fetch-depth: 0). Never fetches,
skips, opens a user database, or derives a fake baseline by deleting current tables.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient

from _offline_socketpair import socketpair_only_connect
from offerpilot.api import create_app
from offerpilot.ai.write_operations import (
    LEDGER_KEY_FILENAME, load_or_create_ledger_key, payload_from_operation,
)
from offerpilot.db import init_database
from offerpilot.job_mail.transport import OSVault
from offerpilot.models import WriteOperation
from offerpilot.repositories.application_creation import ApplicationCreationService
from offerpilot.repositories.applications import ApplicationCreate

BASELINE = "5b9605bab343c4f95ede2c0c61ecd47bb7d923a1"
ROOT = Path(__file__).resolve().parents[1]
BUSINESS_TABLES = (
    "applications", "application_events", "application_jd_versions",
    "application_creation_workspace", "application_creation_receipts",
    "conversations", "chat_messages", "write_operations",
)
MAIL_TABLES = {
    "job_mail_connections", "job_mail_sync_runs", "job_mail_evidence",
    "job_mail_suggestions", "job_mail_previews", "job_mail_receipts",
    "job_mail_credential_operations", "job_mail_extraction_attempts",
}


def _seed_master(tmp_path: Path, data_dir: Path) -> None:
    try:
        archive = subprocess.run(
            ["git", "archive", "--format=zip", BASELINE, "src"], cwd=ROOT,
            check=True, capture_output=True, timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Cannot archive pinned master {BASELINE}; ensure checkout fetch-depth: 0 "
            "or fetch this commit before running the test (no automatic fetch or skip)"
        ) from exc
    baseline_dir = tmp_path / "baseline"
    with zipfile.ZipFile(io.BytesIO(archive.stdout)) as source:
        source.extractall(baseline_dir)
    # -I discards inherited PYTHONPATH/user-site. Explicitly prepend only archived
    # master src, and verify the import location before running the seed script.
    bootstrap = """
import pathlib, runpy, sys
source, seed, data = sys.argv[1:]
sys.path.insert(0, source)
def no_network(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo'}:
        raise AssertionError('Baseline seed attempted network access')
sys.addaudithook(no_network)
import offerpilot.db
assert pathlib.Path(offerpilot.db.__file__).resolve().is_relative_to(pathlib.Path(source))
sys.argv = [seed, data]
runpy.run_path(seed, run_name='__main__')
"""
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ}
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               LITELLM_LOCAL_MODEL_COST_MAP="True")
    try:
        subprocess.run(
            [sys.executable, "-I", "-c", bootstrap, str(baseline_dir / "src"),
             str(ROOT / "tests/fixtures/job_mail_master_seed.py"), str(data_dir)],
            cwd=baseline_dir, env=env, check=True, capture_output=True, text=True, timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Pinned master seed failed: {exc.stderr}") from exc


def _snapshot(path: Path) -> dict[str, tuple[list[str], list[tuple]]]:
    with sqlite3.connect(path) as connection:
        return {
            table: (
                [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')],
                connection.execute(f'SELECT * FROM "{table}" ORDER BY id').fetchall(),
            ) for table in BUSINESS_TABLES
        }


def _assert_preserved(path: Path, before: dict) -> None:
    with sqlite3.connect(path) as connection:
        for table, (columns, rows) in before.items():
            projection = ",".join(f'"{column}"' for column in columns)
            assert connection.execute(
                f'SELECT {projection} FROM "{table}" ORDER BY id'
            ).fetchall() == rows, table
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT count(*) FROM schema_migrations WHERE version='0033_job_mail_review'"
        ).fetchone() == (1,)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE write_operations SET visible_result='changed'")


def test_pinned_master_upgrade_preserves_business_receipts_and_safe_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "synthetic-workspace"
    _seed_master(tmp_path, data_dir)
    path = data_dir / "data.db"
    before = _snapshot(path)
    key_bytes = (data_dir / LEDGER_KEY_FILENAME).read_bytes()
    assert all(rows for _, rows in before.values()), "Every protected table must be populated"
    with sqlite3.connect(path) as connection:
        original_tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert not any(name.startswith("job_mail_") for name in original_tables)
        assert connection.execute(
            "SELECT count(*) FROM schema_migrations WHERE version='0033_job_mail_review'"
        ).fetchone() == (0,)
    attempted = []

    def forbidden(*args, **kwargs):
        attempted.append("network, credential store, or model")
        raise AssertionError("Upgrade must remain offline and credential-free")

    monkeypatch.setattr(socket.socket, "connect", socketpair_only_connect(forbidden))
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket.socket, "sendto", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(OSVault, "__init__", forbidden)
    import litellm
    monkeypatch.setattr(litellm, "completion", forbidden)
    monkeypatch.setattr(litellm, "acompletion", forbidden)

    for _ in range(2):
        sessions = init_database(path)
        sessions.kw["bind"].dispose()
        _assert_preserved(path, before)
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert original_tables <= tables
        assert MAIL_TABLES <= tables
        assert all(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone() == (0,)
                   for table in MAIL_TABLES)
        scope = before["application_creation_workspace"][1][0][1]
        # Omit settings so SQLite's actual defaults, rather than test values or
        # ORM defaults, define the newly introduced connection's safety posture.
        connection.execute("INSERT INTO job_mail_connections (id, scope_id) VALUES (?, ?)",
                           ("synthetic-default-connection", scope))
    for _ in range(2):
        with TestClient(create_app(data_dir=data_dir), base_url="http://127.0.0.1",
                        client=("127.0.0.1", 50001)) as client:
            response = client.get("/api/job-mail/status")
            assert response.status_code == 200
            status = response.json()
            assert status["connection"]["sync_mode"] == "manual"
            assert status["connection"]["ai_enabled"] is False
            assert status["connection"]["status"] == "disconnected"
            assert status["run"] is None
            assert status["capabilities"]["real_connection"] is False
            assert status["capabilities"]["ai_recognition"] is False
            assert status["recognition_mode"] == "offline_rules"
        _assert_preserved(path, before)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT sync_mode, ai_enabled, credential_ref, next_run_at FROM job_mail_connections"
        ).fetchone() == ("manual", 0, None, None)
        assert all(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone() == (0,)
                   for table in MAIL_TABLES - {"job_mail_connections"})
    sessions = init_database(path)
    try:
        result, replay = ApplicationCreationService(sessions).create(
            ApplicationCreate(company_name="Synthetic Upgrade Co", position_name="Engineer"),
            {"jd_text": "Synthetic existing JD, never fetched", "source_url": ""},
            "master-upgrade-creation-0001",
        )
        assert replay is True
        columns, rows = before["application_creation_receipts"]
        assert result == json.loads(rows[0][columns.index("result_json")])
        assert (data_dir / LEDGER_KEY_FILENAME).read_bytes() == key_bytes
        key = load_or_create_ledger_key(data_dir, sessions)
        with sessions() as session:
            operation = session.get(WriteOperation, "11111111-1111-4111-8111-111111111111")
            assert operation is not None
            assert payload_from_operation(operation, key=key).result_json == '{"rejected":true}'
    finally:
        sessions.kw["bind"].dispose()
    _assert_preserved(path, before)
    assert attempted == []


@pytest.mark.parametrize("stage", ["archive", "seed"])
def test_missing_baseline_or_seed_failure_fails_instead_of_skipping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    run = subprocess.run

    def fail_selected(command, **kwargs):
        if (command[0] == "git") == (stage == "archive"):
            raise subprocess.CalledProcessError(128, command, stderr="synthetic baseline failure")
        return run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_selected)
    expected = BASELINE if stage == "archive" else "Pinned master seed failed"
    with pytest.raises(RuntimeError, match=expected):
        _seed_master(tmp_path, tmp_path / "synthetic-workspace")
    assert not (tmp_path / "synthetic-workspace/data.db").exists()
