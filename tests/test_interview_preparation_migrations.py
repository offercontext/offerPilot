import sqlite3

from sqlalchemy import text

from offerpilot.db import init_database


def test_fresh_database_creates_interview_preparation_schema_and_0012(tmp_path):
    factory = init_database(tmp_path / "data.db")
    with factory() as session:
        tables = set(
            session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).scalars()
        )
        columns = {
            row[1]
            for row in session.execute(
                text("PRAGMA table_info(interview_preparation_proposals)")
            )
        }
        versions = set(
            session.execute(text("SELECT version FROM schema_migrations")).scalars()
        )
    factory.kw["bind"].dispose()

    assert "interview_preparation_proposals" in tables
    assert {
        "id",
        "application_id",
        "application_event_id",
        "resume_id",
        "idempotency_key",
        "attempt_status",
        "proposal_status",
        "generation_revision",
        "provider_call_token",
        "provider_lease_until",
        "invalidation_reason",
        "input_snapshot_json",
        "source_fingerprint",
        "proposal_json",
        "proposal_hash",
        "created_at",
    } <= columns
    assert "0012_interview_preparation_proposals" in versions


def test_existing_database_upgrade_is_idempotent_and_preserves_data(tmp_path):
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE applications ("
            "id INTEGER PRIMARY KEY, company_name TEXT NOT NULL, "
            "position_name TEXT NOT NULL, deleted_at DATETIME)"
        )
        connection.execute(
            "INSERT INTO applications(id, company_name, position_name) "
            "VALUES (1, 'Legacy Co', 'Legacy Role')"
        )
        connection.commit()
    finally:
        connection.close()

    first = init_database(db_path)
    first.kw["bind"].dispose()
    second = init_database(db_path)
    with second() as session:
        application = session.execute(
            text("SELECT company_name, position_name FROM applications WHERE id=1")
        ).one()
        migration_count = session.execute(
            text(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE version='0012_interview_preparation_proposals'"
            )
        ).scalar_one()
    second.kw["bind"].dispose()

    assert application == ("Legacy Co", "Legacy Role")
    assert migration_count == 1


def test_attempt_identifiers_are_not_foreign_keys(tmp_path):
    factory = init_database(tmp_path / "data.db")
    with factory() as session:
        foreign_keys = {
            row[3]
            for row in session.execute(
                text("PRAGMA foreign_key_list(interview_preparation_proposals)")
            )
        }
        indexes = {
            row[1]
            for row in session.execute(
                text("PRAGMA index_list(interview_preparation_proposals)")
            )
        }
    factory.kw["bind"].dispose()

    assert foreign_keys == set()
    assert any(name.startswith("sqlite_autoindex_") for name in indexes)
