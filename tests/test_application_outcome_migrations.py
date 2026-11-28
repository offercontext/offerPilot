from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from offerpilot.db import init_database


def test_application_outcome_schema_is_additive_and_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "outcomes.db"

    first = init_database(db_path)
    first.kw["bind"].dispose()
    second = init_database(db_path)
    with second() as session:
        tables = {
            row[0]
            for row in session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        migrations = set(
            session.execute(text("SELECT version FROM schema_migrations")).scalars()
        )
        snapshot_indexes = {
            row[1]
            for row in session.execute(
                text("PRAGMA index_list(application_submission_snapshots)")
            )
        }
        outcome_indexes = {
            row[1]
            for row in session.execute(text("PRAGMA index_list(application_outcomes)"))
        }
    second.kw["bind"].dispose()

    assert {
        "application_submission_snapshots",
        "application_outcomes",
        "application_strategy_changes",
        "application_strategy_receipts",
    } <= tables
    assert {
        "0018_application_jd_versions",
        "0019_interview_story_library",
        "0020_application_outcome_feedback",
        "0042_application_strategy_changes",
    } <= migrations
    assert "idx_application_submission_snapshots_app" in snapshot_indexes
    assert "idx_application_outcomes_app_occurred" in outcome_indexes



def test_application_strategy_tables_upgrade_legacy_workspace(tmp_path: Path) -> None:
    db_path = tmp_path / "strategy-upgrade.db"
    workspace = init_database(db_path)
    with workspace.begin() as session:
        session.execute(text("DROP TABLE application_strategy_receipts"))
        session.execute(text("DROP TABLE application_strategy_changes"))
        session.execute(text(
            "DELETE FROM schema_migrations WHERE version='0042_application_strategy_changes'"
        ))
        before = session.execute(text("SELECT count(*) FROM application_outcomes")).scalar_one()
    workspace.kw["bind"].dispose()

    upgraded = init_database(db_path)
    with upgraded() as session:
        assert session.execute(text("SELECT count(*) FROM application_outcomes")).scalar_one() == before
        assert session.execute(text("SELECT count(*) FROM application_strategy_changes")).scalar_one() == 0
        assert session.execute(text("SELECT count(*) FROM application_strategy_receipts")).scalar_one() == 0
        assert not session.execute(text("PRAGMA foreign_key_check")).all()
    upgraded.kw["bind"].dispose()
