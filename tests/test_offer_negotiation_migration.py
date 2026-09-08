from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from offerpilot.db import init_database


def _create_pre_negotiation_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO schema_migrations(version, description)
            VALUES ('0016_event_bound_mock_interview', 'legacy current schema');
            CREATE TABLE offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER,
                company_name TEXT NOT NULL,
                position_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                base_monthly INTEGER NOT NULL DEFAULT 0,
                months_per_year INTEGER NOT NULL DEFAULT 12,
                signing_bonus INTEGER NOT NULL DEFAULT 0,
                equity TEXT NOT NULL DEFAULT '',
                perks TEXT NOT NULL DEFAULT '',
                deadline TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                assessment TEXT NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO offers(
                company_name, position_name, status, base_monthly, months_per_year,
                signing_bonus, equity, perks, deadline, notes, assessment
            ) VALUES (
                'Nebula Data', 'Backend Engineer', 'pending', 28000, 14,
                50000, 'equity pending', 'meal subsidy', '2026-09-01',
                'original note', 'needs review'
            );
            """
        )
        conn.commit()


def _table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }


def test_fresh_database_creates_offer_negotiation_tables_and_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "data.db"

    init_database(db_path)

    assert {
        "offer_comparison_dimensions",
        "offer_comparison_values",
        "offer_negotiation_proposals",
        "offer_negotiation_briefs",
    } <= _table_names(db_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = '0017_offer_comparison_negotiation'"
        ).fetchone() == (1,)
        indexes = list(conn.execute("PRAGMA index_list(offer_comparison_values)"))
        assert any(row[2] == 1 for row in indexes)


def test_real_pre_feature_database_upgrade_is_idempotent_and_preserves_offer_bytes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data.db"
    _create_pre_negotiation_db(db_path)

    init_database(db_path)
    with sqlite3.connect(db_path) as conn:
        before = conn.execute(
            "SELECT company_name, position_name, status, base_monthly, months_per_year, "
            "signing_bonus, equity, perks, deadline, notes, assessment FROM offers WHERE id = 1"
        ).fetchone()
        migration_count = conn.execute(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '0017_offer_comparison_negotiation'"
        ).fetchone()[0]

    init_database(db_path)
    with sqlite3.connect(db_path) as conn:
        after = conn.execute(
            "SELECT company_name, position_name, status, base_monthly, months_per_year, "
            "signing_bonus, equity, perks, deadline, notes, assessment FROM offers WHERE id = 1"
        ).fetchone()
        assert after == before
        assert migration_count == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '0017_offer_comparison_negotiation'"
        ).fetchone()[0] == 1


def test_current_value_unique_constraint_and_history_survive_offer_delete(tmp_path: Path) -> None:
    db_path = tmp_path / "data.db"
    init_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO offers(company_name, position_name) VALUES ('Company', 'Role')")
        offer_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        dimension_id = conn.execute(
            "INSERT INTO offer_comparison_dimensions(label) VALUES ('Commute') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO offer_comparison_values(offer_id, dimension_id, value_text) "
            "VALUES (?, ?, '35 minutes')",
            (offer_id, dimension_id),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO offer_comparison_values(offer_id, dimension_id, value_text) "
                "VALUES (?, ?, 'duplicate')",
                (offer_id, dimension_id),
            )

        conn.execute(
            "INSERT INTO offer_negotiation_proposals("
            "offer_id, idempotency_key, attempt_status, source_fingerprint, "
            "input_snapshot_json, source_states_json, revision"
            ") VALUES (?, 'A1', 'ready', 'fp', '{}', '{}', 1)",
            (offer_id,),
        )
        proposal_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO offer_negotiation_briefs("
            "proposal_id, offer_id, selected_blocks_json, edited_content_json"
            ") VALUES (?, ?, '[]', '{}')",
            (proposal_id, offer_id),
        )
        brief_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("DELETE FROM offers WHERE id = ?", (offer_id,))
        assert conn.execute(
            "SELECT offer_id FROM offer_negotiation_briefs WHERE id = ?",
            (brief_id,),
        ).fetchone() == (offer_id,)
        conn.commit()
