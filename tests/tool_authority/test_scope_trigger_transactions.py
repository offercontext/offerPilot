"""Real SQLite transaction boundaries for the repeatable 0028 trigger installer."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Event
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError

import offerpilot.db as database
from tests.tool_authority.test_migration_0028 import _legacy_operation, _legacy_schema_engine

TRIGGERS = (
    "trg_conversations_scope_insert", "trg_conversations_scope_update",
    "trg_conversations_scope_revision_unchanged", "trg_conversations_mode_insert",
    "trg_conversations_mode_update", "trg_write_operation_scope_insert",
    "trg_write_operation_scope_fingerprint_immutable", "trg_write_operation_scope_identity_immutable",
    "trg_write_operation_scope_conversation_immutable", "trg_write_operation_scope_status",
)
LEGACY_ID = "00000000-0000-4000-8000-000000000001"
BOUND_ID = "00000000-0000-4000-8000-000000000002"


def _database(path: Path, *, migrated: bool = True) -> None:
    legacy = _legacy_schema_engine()
    try:
        with legacy.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO conversations(id,title,mode,context_type,context_ref) "
                "VALUES(1,'synthetic','general','workspace','')"
            )
            _legacy_operation(conn, operation_id=LEGACY_ID)
        if migrated:
            database._ensure_scoped_tool_authority_schema(legacy)
            with legacy.begin() as conn:
                conn.exec_driver_sql("UPDATE conversations SET context_ref='seeded',scope_revision=1 WHERE id=1")
                _legacy_operation(conn, operation_id=BOUND_ID,
                                  authorization_scope_fingerprint="hmac-sha256:" + "c" * 64)
        raw = legacy.raw_connection()
        try:
            with sqlite3.connect(path) as destination:
                raw.driver_connection.backup(destination)
        finally:
            raw.close()
    finally:
        legacy.dispose()


def _assert_busy(error: BaseException) -> None:
    code = getattr(error, "sqlite_errorcode", None)
    if code is None:  # Python 3.10 does not expose SQLite error codes.
        assert str(error) == "database is locked"
    else:
        assert code == 5  # SQLITE_BUSY, not SQLITE_LOCKED or an unrelated failure.


def _schema(path: Path) -> list[tuple[str, str]]:
    with sqlite3.connect(path) as conn:
        return [(str(name), str(sql)) for name, sql in conn.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
        )]


def _rows(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    with sqlite3.connect(path) as conn:
        return {table: conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in ("conversations", "write_operations", "schema_migrations")}


def _assert_guards(path: Path) -> None:
    assert {name for name, _ in _schema(path)} == set(TRIGGERS)
    with sqlite3.connect(path) as conn:
        next_revision = conn.execute("SELECT scope_revision + 1 FROM conversations WHERE id=1").fetchone()[0]
        for statement, reason in (
            ("UPDATE conversations SET context_ref='changed' WHERE id=1", "scope revision"),
            (f"UPDATE conversations SET scope_revision={next_revision} WHERE id=1", "without scope mutation"),
            (f"UPDATE conversations SET mode=' bad',scope_revision={next_revision} WHERE id=1", "invalid conversation mode"),
            (f"UPDATE write_operations SET status='committed' WHERE id='{LEGACY_ID}'", "unbound typed operation"),
            (f"UPDATE write_operations SET tool_name='create_offer' WHERE id='{LEGACY_ID}'", "authority identity"),
            (f"UPDATE write_operations SET authorization_scope_fingerprint=NULL WHERE id='{BOUND_ID}'", "fingerprint is immutable"),
        ):
            with pytest.raises(sqlite3.IntegrityError, match=reason):
                conn.execute(statement)
            conn.rollback()


def _hold_first_drop(engine: Engine) -> tuple[Event, Event, list[bool]]:
    dropped = Event()
    release = Event()
    driver_transactions: list[bool] = []

    def after(conn: Connection, _cursor: Any, statement: str, _params: Any,
              _context: Any, _many: bool) -> None:
        if statement == "DROP TRIGGER IF EXISTS trg_conversations_scope_insert":
            driver_transactions.append(conn.connection.driver_connection.in_transaction)
            dropped.set()
            assert release.wait(45), "test must release the paused migration"

    event.listen(engine, "after_cursor_execute", after)
    return dropped, release, driver_transactions


def test_trigger_replacement_is_atomic_and_serializes_independent_engine_threads(tmp_path: Path) -> None:
    path = tmp_path / "scope-race.db"
    _database(path)
    before_schema, before_rows = _schema(path), _rows(path)
    first = create_engine(f"sqlite:///{path}", connect_args={"timeout": 10})
    second = create_engine(f"sqlite:///{path}", connect_args={"timeout": 10})
    dropped, release, driver_transactions = _hold_first_drop(first)
    second_attempted = Event()
    order: list[str] = []

    def second_before(_conn: Connection, _cursor: Any, statement: str, _params: Any,
                      _context: Any, _many: bool) -> None:
        if statement.startswith("UPDATE conversations SET mode"):
            second_attempted.set()
        if statement == "DROP TRIGGER IF EXISTS trg_conversations_scope_insert":
            order.append("second-drop")

    def first_commit(_conn: Connection) -> None:
        if dropped.is_set():
            order.append("first-trigger-commit")

    event.listen(second, "before_cursor_execute", second_before)
    event.listen(first, "commit", first_commit)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            one = executor.submit(database._ensure_scoped_tool_authority_schema, first)
            try:
                assert dropped.wait(10)
                two = executor.submit(database._ensure_scoped_tool_authority_schema, second)
                assert second_attempted.wait(10)
                # Neither an ORM transaction flag nor a sleep proves atomic DDL.
                assert driver_transactions == [True]
                assert _schema(path) == before_schema, "readers must retain the entire old guard set"
                with sqlite3.connect(path, timeout=0) as contender:
                    with pytest.raises(sqlite3.OperationalError) as error:
                        contender.execute("BEGIN IMMEDIATE")
                    _assert_busy(error.value)
            finally:
                release.set()
            one.result(timeout=15)
            two.result(timeout=15)
        assert order.index("first-trigger-commit") < order.index("second-drop")
        assert _schema(path) == before_schema
        assert _rows(path) == before_rows
        _assert_guards(path)
    finally:
        release.set()
        first.dispose()
        second.dispose()


_PROCESS_OBSERVER = """
import json, sqlite3, sys
with sqlite3.connect(sys.argv[1], timeout=0) as conn:
    schema = conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall()
    blocked = False
    try:
        conn.execute('BEGIN IMMEDIATE')
    except sqlite3.OperationalError as error:
        code = getattr(error, 'sqlite_errorcode', None)
        if (code is not None and code != 5) or (code is None and str(error) != 'database is locked'): raise
        blocked = True
    finally:
        conn.rollback()
print(json.dumps({'schema': schema, 'writerBlocked': blocked}))
"""



_PROCESS_MIGRATION = """
import sys
sys.path.insert(0, sys.argv[2])
from sqlalchemy import create_engine, event
from offerpilot.db import _ensure_scoped_tool_authority_schema
engine = create_engine('sqlite:///' + sys.argv[1], connect_args={'timeout': 10})
def before(conn, cursor, statement, params, context, many):
    if statement.startswith('UPDATE conversations SET mode'):
        print('WRITE_ATTEMPT', flush=True)
event.listen(engine, 'before_cursor_execute', before)
try:
    _ensure_scoped_tool_authority_schema(engine)
    print('COMPLETE', flush=True)
finally:
    engine.dispose()
"""


def _process_handshake(
    executor: ThreadPoolExecutor, process: subprocess.Popen[str], *, timeout: float,
) -> str:
    assert process.stdout is not None
    reader = executor.submit(process.stdout.readline)
    try:
        return reader.result(timeout=timeout)
    except BaseException:
        # A timed-out Future does not cancel readline. Close its writer before
        # executor.__exit__ waits, otherwise outer process cleanup is unreachable.
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        reader.result(timeout=5)
        raise


def test_unresponsive_child_handshake_reaps_process_before_waiting_for_reader() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises(TimeoutError):
                _process_handshake(executor, process, timeout=0.05)
            assert process.poll() is not None
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


def test_trigger_write_lock_and_complete_old_schema_are_visible_across_processes(tmp_path: Path) -> None:
    path = tmp_path / "scope-process.db"
    _database(path)
    before_schema, before_rows = _schema(path), _rows(path)
    engine = create_engine(f"sqlite:///{path}")
    dropped, release, driver_transactions = _hold_first_drop(engine)
    process = None
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(database._ensure_scoped_tool_authority_schema, engine)
            try:
                assert dropped.wait(10)
                observation = subprocess.run(
                    [sys.executable, "-c", _PROCESS_OBSERVER, str(path)],
                    check=True, capture_output=True, text=True, timeout=10,
                )
                evidence = json.loads(observation.stdout)
                assert driver_transactions == [True]
                assert evidence["writerBlocked"] is True
                assert evidence["schema"] == [list(row) for row in before_schema]
                process = subprocess.Popen(
                    [sys.executable, "-c", _PROCESS_MIGRATION, str(path),
                     str(Path(database.__file__).resolve().parents[1])],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                assert _process_handshake(executor, process, timeout=20).strip() == "WRITE_ATTEMPT"
            finally:
                release.set()
            future.result(timeout=15)
            assert process is not None
            output, errors = process.communicate(timeout=15)
            assert process.returncode == 0, errors
            assert output.strip() == "COMPLETE"
        assert _schema(path) == before_schema
        assert _rows(path) == before_rows
        _assert_guards(path)
    finally:
        release.set()
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        engine.dispose()


@pytest.mark.parametrize("ddl_step", range(20))
def test_each_trigger_ddl_failure_rolls_back_the_complete_previous_guard_set(
    tmp_path: Path, ddl_step: int,
) -> None:
    path = tmp_path / "scope-rollback.db"
    _database(path)
    # A harmless old definition makes rollback distinguishable even when the
    # injected failure follows the last CREATE. Its authority checks are intact.
    with sqlite3.connect(path) as conn:
        original = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (TRIGGERS[0],)).fetchone()[0]
        conn.execute(f"DROP TRIGGER {TRIGGERS[0]}")
        conn.execute(original.replace("BEGIN", "BEGIN SELECT 1;", 1))
    before_schema, before_rows = _schema(path), _rows(path)
    engine = create_engine(f"sqlite:///{path}")
    observed: list[str] = []

    def fail_after_ddl(_conn: Connection, _cursor: Any, statement: str, _params: Any,
                       _context: Any, _many: bool) -> None:
        if statement.lstrip().startswith(("DROP TRIGGER", "CREATE TRIGGER")):
            observed.append(statement)
            if len(observed) - 1 == ddl_step:
                raise RuntimeError("injected scope-trigger DDL failure")

    event.listen(engine, "after_cursor_execute", fail_after_ddl)
    try:
        with pytest.raises(RuntimeError, match="injected scope-trigger DDL failure"):
            database._ensure_scoped_tool_authority_schema(engine)
        assert len(observed) == ddl_step + 1
        assert _schema(path) == before_schema
        assert _rows(path) == before_rows
        _assert_guards(path)
        event.remove(engine, "after_cursor_execute", fail_after_ddl)
        database._ensure_scoped_tool_authority_schema(engine)
        migrated = _schema(path)
        assert migrated != before_schema, "reentry must refresh an older guard definition despite the marker"
        database._ensure_scoped_tool_authority_schema(engine)
        assert _schema(path) == migrated
        assert _rows(path) == before_rows
        _assert_guards(path)
    finally:
        engine.dispose()


@pytest.mark.parametrize("failure_step", [7, 17])
def test_first_install_trigger_failure_keeps_additive_columns_without_marker_then_retries(
    tmp_path: Path, failure_step: int,
) -> None:
    path = tmp_path / "first-attempt.db"
    _database(path, migrated=False)
    engine = create_engine(f"sqlite:///{path}")
    observed = 0

    def fail_after_ddl(_conn: Connection, _cursor: Any, statement: str, _params: Any,
                      _context: Any, _many: bool) -> None:
        nonlocal observed
        if statement.lstrip().startswith(("DROP TRIGGER", "CREATE TRIGGER")):
            observed += 1
            if observed == failure_step:
                raise RuntimeError("first trigger install failed")

    event.listen(engine, "after_cursor_execute", fail_after_ddl)
    try:
        with pytest.raises(RuntimeError, match="first trigger install failed"):
            database._ensure_scoped_tool_authority_schema(engine)
        assert _schema(path) == []
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT * FROM schema_migrations").fetchall() == []
            assert conn.execute("SELECT title,mode,context_type,context_ref,scope_revision FROM conversations").fetchall() == [
                ("synthetic", "general", "workspace", "", 0)
            ]
            assert conn.execute("SELECT id,status,authorization_scope_fingerprint FROM write_operations").fetchall() == [
                (LEGACY_ID, "proposed", None)
            ]
        event.remove(engine, "after_cursor_execute", fail_after_ddl)
        database._ensure_scoped_tool_authority_schema(engine)
        with engine.begin() as conn:
            _legacy_operation(conn, operation_id=BOUND_ID,
                              authorization_scope_fingerprint="hmac-sha256:" + "c" * 64)
        before_schema, before_rows = _schema(path), _rows(path)
        database._ensure_scoped_tool_authority_schema(engine)
        assert _schema(path) == before_schema
        assert _rows(path) == before_rows
        assert len(before_rows["schema_migrations"]) == 1
        _assert_guards(path)
    finally:
        engine.dispose()


def test_missing_trigger_is_repaired_despite_existing_marker(tmp_path: Path) -> None:
    path = tmp_path / "missing-trigger.db"
    _database(path)
    before_schema, before_rows = _schema(path), _rows(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER trg_write_operation_scope_status")
    engine = create_engine(f"sqlite:///{path}")
    try:
        database._ensure_scoped_tool_authority_schema(engine)
        assert _schema(path) == before_schema
        assert _rows(path) == before_rows
        _assert_guards(path)
    finally:
        engine.dispose()


def test_busy_at_trigger_transaction_begin_fails_without_ddl_then_retries(tmp_path: Path) -> None:
    path = tmp_path / "scope-busy.db"
    _database(path)
    before_schema, before_rows = _schema(path), _rows(path)
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 0.05})
    holder = sqlite3.connect(path, timeout=0)
    attempted = False
    ddl: list[str] = []

    def acquire_competing_lock(_conn: Connection, _cursor: Any, statement: str, _params: Any,
                               _context: Any, _many: bool) -> None:
        nonlocal attempted
        if statement == "BEGIN IMMEDIATE":
            attempted = True
            holder.execute("BEGIN IMMEDIATE")
        if statement.lstrip().startswith(("DROP TRIGGER", "CREATE TRIGGER")):
            ddl.append(statement)

    event.listen(engine, "before_cursor_execute", acquire_competing_lock)
    try:
        with pytest.raises(OperationalError) as failure:
            database._ensure_scoped_tool_authority_schema(engine)
        assert attempted is True
        _assert_busy(failure.value.orig)
        assert ddl == []
        holder.rollback()
        assert _schema(path) == before_schema
        assert _rows(path) == before_rows
        event.remove(engine, "before_cursor_execute", acquire_competing_lock)
        database._ensure_scoped_tool_authority_schema(engine)
        _assert_guards(path)
    finally:
        holder.close()
        engine.dispose()


def test_marker_write_failure_leaves_complete_guards_and_retry_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "marker-retry.db"
    _database(path, migrated=False)
    engine = create_engine(f"sqlite:///{path}")
    record = database._record_migration

    def fail_record(_engine: Engine, version: str, _description: str) -> None:
        assert version == "0028_scoped_tool_authority"
        raise RuntimeError("marker write failed")

    monkeypatch.setattr(database, "_record_migration", fail_record)
    try:
        with pytest.raises(RuntimeError, match="marker write failed"):
            database._ensure_scoped_tool_authority_schema(engine)
        assert {name for name, _ in _schema(path)} == set(TRIGGERS)
        assert _rows(path)["schema_migrations"] == []
        monkeypatch.setattr(database, "_record_migration", record)
        database._ensure_scoped_tool_authority_schema(engine)
        assert len(_rows(path)["schema_migrations"]) == 1
        with engine.begin() as conn:
            _legacy_operation(conn, operation_id=BOUND_ID,
                              authorization_scope_fingerprint="hmac-sha256:" + "c" * 64)
        _assert_guards(path)
    finally:
        engine.dispose()
