"""Upgrade/restart proofs for P0 without changing historical operation bytes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import offerpilot.db as database
from tests.test_review_to_readiness_migration_0029 import _create_fixed_pre_0029_database


STRATEGY_COLUMNS = (
    "confirmation_strategy_version",
    "confirmation_strategy_fields_json",
    "confirmation_strategy_fingerprint",
)
MIGRATION = "0031_edited_confirmation_receipt"


def _initialize(path: Path) -> None:
    factory = database.init_database(path)
    factory.kw["bind"].dispose()


def _snapshot(path: Path) -> tuple[list[str], list[tuple[object, ...]]]:
    with sqlite3.connect(path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(write_operations)")]
        values = connection.execute("SELECT * FROM write_operations ORDER BY id").fetchall()
    return columns, values


def _assert_complete(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(write_operations)")}
        assert set(STRATEGY_COLUMNS) <= columns
        assert connection.execute(
            "SELECT count(*) FROM schema_migrations WHERE version=?", (MIGRATION,)
        ).fetchone() == (1,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def _assert_history_preserved(
    path: Path, columns: list[str], values: list[tuple[object, ...]]
) -> None:
    with sqlite3.connect(path) as connection:
        selected = ",".join(f'"{column}"' for column in columns)
        assert connection.execute(
            f"SELECT {selected} FROM write_operations ORDER BY id"
        ).fetchall() == values
        strategy_values = ",".join(STRATEGY_COLUMNS)
        assert all(
            row == (None, None, None)
            for row in connection.execute(f"SELECT {strategy_values} FROM write_operations")
        )


def test_new_database_records_receipt_migration_and_reopening_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    _initialize(path)
    _assert_complete(path)
    _initialize(path)
    _assert_complete(path)


def test_legacy_terminal_operations_preserve_all_prior_columns_and_remain_immutable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    _create_fixed_pre_0029_database(path)
    columns, values = _snapshot(path)
    assert values, "the historical fixture must contain real terminal rows"
    _initialize(path)
    _assert_complete(path)
    _assert_history_preserved(path, columns, values)
    with sqlite3.connect(path) as connection:
        terminal_id = connection.execute(
            "SELECT id FROM write_operations WHERE status='committed' LIMIT 1"
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE write_operations SET confirmation_strategy_version=? WHERE id=?",
                ("edited_confirmation_receipt_v1", terminal_id),
            )


@pytest.mark.parametrize("last_column", STRATEGY_COLUMNS)
def test_interrupted_column_upgrade_restarts_without_rewriting_legacy_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, last_column: str
) -> None:
    path = tmp_path / "interrupted.db"
    _create_fixed_pre_0029_database(path)
    columns, values = _snapshot(path)
    original = database._ensure_column

    def interrupt(engine, table: str, column: str, definition: str) -> bool:
        changed = original(engine, table, column, definition)
        if table == "write_operations" and column == last_column and changed:
            engine.dispose()
            raise OSError("injected stop after a receipt column was committed")
        return changed

    with monkeypatch.context() as scoped:
        scoped.setattr(database, "_ensure_column", interrupt)
        with pytest.raises(OSError, match="injected stop"):
            _initialize(path)
    _initialize(path)
    _assert_complete(path)
    _assert_history_preserved(path, columns, values)


def test_complete_columns_with_missing_marker_converge_on_restart(tmp_path: Path) -> None:
    path = tmp_path / "marker.db"
    _initialize(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version=?", (MIGRATION,))
    _initialize(path)
    _assert_complete(path)
