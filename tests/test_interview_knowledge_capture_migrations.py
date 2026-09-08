from __future__ import annotations

import sqlite3

from sqlalchemy import text

from offerpilot.db import init_database


def test_fresh_database_creates_capture_schema_and_records_0011(tmp_path) -> None:
    factory = init_database(tmp_path / "fresh.db")
    with factory() as session:
        tables = {
            row[0]
            for row in session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        migrations = set(
            session.execute(text("SELECT version FROM schema_migrations")).scalars()
        )
    assert {
        "interview_knowledge_capture_attempts",
        "knowledge_captured_source_metadata",
        "knowledge_notes",
        "knowledge_note_versions",
        "knowledge_note_evidence",
    } <= tables
    assert "0011_confirmed_interview_knowledge_capture" in migrations


def test_existing_database_missing_event_column_is_upgraded_after_tables_exist(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY,
                company_name TEXT NOT NULL,
                position_name TEXT NOT NULL,
                deleted_at DATETIME
            );
            CREATE TABLE interview_notes (
                id INTEGER PRIMARY KEY,
                application_id INTEGER,
                company TEXT NOT NULL,
                position TEXT NOT NULL,
                round TEXT DEFAULT '',
                date TEXT DEFAULT '',
                questions TEXT DEFAULT '',
                self_reflection TEXT DEFAULT '',
                difficulty_points TEXT DEFAULT '',
                mood TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
    init_database(path)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(interview_notes)")}
        migrations = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations")
        }
    assert "application_event_id" in columns
    assert "0011_confirmed_interview_knowledge_capture" in migrations
