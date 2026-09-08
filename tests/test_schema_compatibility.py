import json
import sqlite3

import pytest

from offerpilot.db import init_database


def test_chat_messages_have_conversation_foreign_key(tmp_path):
    db_path = tmp_path / "data.db"
    init_database(db_path)
    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        foreign_keys = conn.execute("PRAGMA foreign_key_list(chat_messages)").fetchall()

    assert any(row[2] == "conversations" and row[3] == "conversation_id" for row in foreign_keys)


def test_application_events_have_application_foreign_key_and_event_columns(tmp_path):
    db_path = tmp_path / "data.db"
    init_database(db_path)
    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        foreign_keys = conn.execute("PRAGMA foreign_key_list(application_events)").fetchall()
        event_columns = {row[1] for row in conn.execute("PRAGMA table_info(application_events)")}
        legacy_events = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
        ).fetchall()

    assert any(row[2] == "applications" and row[3] == "application_id" for row in foreign_keys)
    assert {"event_type", "subtype", "tags", "round", "scheduled_at", "remind_at"}.issubset(
        event_columns
    )
    assert legacy_events == []


def test_applications_have_v01_lifecycle_columns(tmp_path):
    db_path = tmp_path / "data.db"
    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(applications)")}

    assert {
        "closed_reason",
        "closed_at",
        "deleted_at",
        "first_pending_at",
        "first_applied_at",
        "first_written_test_at",
        "first_interview_at",
        "first_offer_at",
    }.issubset(columns)


def test_init_database_backfills_legacy_application_lifecycle_columns(tmp_path):
    db_path = tmp_path / "data.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL,
                position_name TEXT NOT NULL,
                job_url TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'applied',
                source TEXT NOT NULL DEFAULT 'cli',
                notes TEXT DEFAULT '',
                applied_at DATETIME NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO applications (
                company_name, position_name, status, applied_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "ByteDance",
                "Backend",
                "interview",
                "2026-07-01 09:00:00",
                "2026-07-01 09:00:00",
                "2026-07-03 10:00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO applications (
                company_name, position_name, status, applied_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "OpenAI",
                "Product",
                "closed",
                "2026-07-02 09:00:00",
                "2026-07-02 09:00:00",
                "2026-07-04 10:00:00",
            ),
        )

    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                status,
                first_interview_at,
                closed_at,
                first_applied_at,
                closed_reason,
                deleted_at
            FROM applications
            ORDER BY id
            """
        ).fetchall()
        versions = {
            row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

    assert rows[0] == ("interview", "2026-07-03 10:00:00", None, None, "", None)
    assert rows[1] == ("closed", None, "2026-07-04 10:00:00", None, "", None)
    assert "0005_application_lifecycle_columns" in versions


def test_interview_notes_application_foreign_key_sets_null(tmp_path):
    db_path = tmp_path / "data.db"
    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        foreign_keys = conn.execute("PRAGMA foreign_key_list(interview_notes)").fetchall()

    assert any(
        row[2] == "applications" and row[3] == "application_id" and row[6] == "SET NULL"
        for row in foreign_keys
    )


def test_knowledge_legacy_tables_are_gone_after_reset(tmp_path):
    db_path = tmp_path / "data.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations ("
            "version TEXT PRIMARY KEY, description TEXT NOT NULL, "
            "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        for table in _legacy_knowledge_tables():
            if "fts" in table:
                conn.execute(f"CREATE VIRTUAL TABLE {table} USING fts5(content)")
            else:
                conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")

    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        versions = {
            row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

    for table in _legacy_knowledge_tables():
        assert table not in tables, f"legacy knowledge table {table} still present"
    assert "knowledge_rewrite_reset" in versions


def test_knowledge_reset_is_idempotent(tmp_path):
    db_path = tmp_path / "data.db"
    init_database(db_path)
    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        versions = [
            row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        ]

    assert versions.count("knowledge_rewrite_reset") == 1


def test_knowledge_reset_preserves_other_modules(tmp_path):
    db_path = tmp_path / "data.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations ("
            "version TEXT PRIMARY KEY, description TEXT NOT NULL, "
            "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE TABLE applications (id INTEGER PRIMARY KEY, company_name TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO applications (company_name) VALUES ('OfferPilot')")
        conn.execute("CREATE TABLE knowledge_documents (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE knowledge_wiki_pages (id INTEGER PRIMARY KEY)")

    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT company_name FROM applications").fetchall()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }

    assert rows == [("OfferPilot",)]
    assert "knowledge_documents" not in tables
    assert "knowledge_wiki_pages" not in tables
    # KI-02 起新表 knowledge_sources 由本模块创建并维护，不再视为 legacy
    assert "knowledge_sources" in tables


def _legacy_knowledge_tables() -> tuple[str, ...]:
    # KI-02 起新表 knowledge_sources/origins/snapshots/evidence/evidence_fts/jobs 由本模块
    # 创建并维护，不再视为 legacy；只保留旧自动 Wiki 占位实现的表名作为破坏性重置对象。
    return (
        "knowledge_bases",
        "knowledge_documents",
        "knowledge_chunks",
        "knowledge_chunks_fts",
        "knowledge_wiki_pages",
        "knowledge_wiki_pages_fts",
        "knowledge_page_versions",
        "knowledge_index_entries",
        "knowledge_page_evidence",
        "knowledge_wikilinks",
        "knowledge_reviews",
        "knowledge_review_revisions",
        "knowledge_review_jobs",
        "knowledge_config_versions",
    )


def test_init_database_adds_current_chat_context_columns(tmp_path):
    db_path = tmp_path / "data.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE conversations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "title TEXT NOT NULL DEFAULT '新对话',"
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        conn.execute(
            "CREATE TABLE chat_messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "conversation_id INTEGER NOT NULL,"
            "role TEXT NOT NULL,"
            "content TEXT DEFAULT '',"
            "tool_calls TEXT DEFAULT '',"
            "tool_call_id TEXT DEFAULT '',"
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        )

    init_database(db_path)
    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        conversation_columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
        message_columns = {row[1] for row in conn.execute("PRAGMA table_info(chat_messages)")}
        message_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'chat_messages'"
        ).fetchone()[0]
        message_foreign_keys = conn.execute("PRAGMA foreign_key_list(chat_messages)").fetchall()
        message_indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(chat_messages)").fetchall()
        }
        operation_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'write_operations'"
        ).fetchone()[0]

    assert {"mode", "context_type", "context_ref", "pinned_at", "archived_at"}.issubset(
        conversation_columns
    )
    assert "offer_id" not in conversation_columns
    assert "provider_blocks" in message_columns
    assert "ck_chat_messages_delivery_group" in message_sql
    assert "ck_chat_messages_delivery_shape" in message_sql
    assert "idx_chat_messages_conv" in message_indexes
    assert any(
        row[2] == "write_operations" and row[3] == "operation_id" for row in message_foreign_keys
    )
    assert "length(parent_terminal_payload_sha256) = 71" in operation_sql


def test_write_operation_chat_rebuild_is_atomic_on_orphan_rows(tmp_path):
    db_path = tmp_path / "orphan.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE conversations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "title TEXT NOT NULL DEFAULT 'legacy',"
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        conn.execute(
            "CREATE TABLE chat_messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "conversation_id INTEGER NOT NULL,"
            "role TEXT NOT NULL,"
            "content TEXT DEFAULT '',"
            "tool_calls TEXT DEFAULT '',"
            "tool_call_id TEXT DEFAULT '',"
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        conn.execute(
            "INSERT INTO chat_messages(conversation_id, role, content) "
            "VALUES (999, 'user', 'orphan')"
        )

    for _attempt in range(2):
        with pytest.raises(RuntimeError, match="foreign key migration failed"):
            init_database(db_path)
        with sqlite3.connect(db_path) as conn:
            table_sql = conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'chat_messages'"
            ).fetchone()[0]
            versions = {
                row[0] for row in conn.execute("SELECT version FROM schema_migrations")
            }
        assert "ck_chat_messages_delivery_group" not in table_sql
        assert "0026_write_operation_ledger" not in versions


def test_write_operation_chat_rebuild_drops_stale_cross_table_trigger(tmp_path):
    db_path = tmp_path / "stale-trigger.db"
    init_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER trg_chat_message_operation_insert")
        conn.execute("DROP TRIGGER trg_chat_message_operation_update")
        conn.execute("DROP TRIGGER trg_chat_message_operation_bind")
        conn.execute("DROP TRIGGER trg_write_operation_chat_restrict")
        conn.execute("ALTER TABLE chat_messages RENAME TO chat_messages_current")
        conn.execute(
            "CREATE TABLE chat_messages ("
            "id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, role TEXT NOT NULL,"
            "content TEXT NOT NULL DEFAULT '', tool_calls TEXT NOT NULL DEFAULT '',"
            "tool_call_id TEXT NOT NULL DEFAULT '', provider_blocks TEXT NOT NULL DEFAULT '',"
            "operation_id TEXT, delivery_kind TEXT, delivery_ordinal INTEGER,"
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO chat_messages SELECT * FROM chat_messages_current"
        )
        conn.execute("DROP TABLE chat_messages_current")
        conn.execute(
            "CREATE TRIGGER trg_write_operation_chat_restrict "
            "BEFORE DELETE ON write_operations "
            "WHEN EXISTS (SELECT 1 FROM chat_messages WHERE operation_id = OLD.id) "
            "BEGIN SELECT RAISE(ABORT, 'operation delivery messages exist'); END"
        )

    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='chat_messages'"
        ).fetchone()[0]
        trigger = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name='trg_write_operation_chat_restrict'"
        ).fetchone()
    assert "ck_chat_messages_delivery_group" in table_sql
    assert trigger == ("trg_write_operation_chat_restrict",)


def test_init_database_creates_idempotent_schema_migration_log(tmp_path):
    db_path = tmp_path / "data.db"

    init_database(db_path)
    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(schema_migrations)")}
        rows = conn.execute(
            "SELECT version, description FROM schema_migrations ORDER BY version"
        ).fetchall()

    assert {"version", "description", "applied_at"}.issubset(columns)
    assert rows.count(("0001_base_schema", "Create current application tables")) == 1


def test_current_chat_context_migration_is_recorded(tmp_path):
    db_path = tmp_path / "data.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE conversations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "title TEXT NOT NULL DEFAULT 'legacy',"
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        conn.execute(
            "CREATE TABLE chat_messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "conversation_id INTEGER NOT NULL,"
            "role TEXT NOT NULL,"
            "content TEXT DEFAULT '',"
            "tool_calls TEXT DEFAULT '',"
            "tool_call_id TEXT DEFAULT '',"
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        )

    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        versions = {
            row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        conversation_columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}

    assert "0002_chat_state_columns" in versions
    assert {"context_type", "context_ref", "pinned_at", "archived_at"}.issubset(
        conversation_columns
    )
    assert "offer_id" not in conversation_columns


def test_init_database_backfills_legacy_resume_v01_columns_idempotently(tmp_path):
    db_path = tmp_path / "data.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO schema_migrations (version, description)
            VALUES ('0003_resume_content_columns', 'Add resume content columns')
            """
        )
        conn.execute(
            """
            CREATE TABLE resumes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT DEFAULT '',
                file_path TEXT DEFAULT '',
                parsed_data TEXT DEFAULT '',
                parse_status TEXT DEFAULT 'pending',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO resumes (name, file_path, parsed_data, parse_status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "Legacy Backend",
                "resumes/legacy_backend.pdf",
                "Legacy raw resume text",
                "text-ready",
                "2026-07-01 10:00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO resumes (name, file_path, parsed_data, parse_status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "Legacy Frontend",
                "resumes/legacy_frontend.pdf",
                "Frontend raw text",
                "text-ready",
                "2026-07-02 10:00:00",
            ),
        )

    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, title, source_file_path, content_json, is_master
            FROM resumes
            ORDER BY id
            """
        ).fetchall()
        versions = [
            row[0]
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]

    assert rows[0][1] == "Legacy Backend"
    assert rows[0][2] == "resumes/legacy_backend.pdf"
    assert json.loads(rows[0][3]) == {"raw_text": "Legacy raw resume text"}
    assert rows[0][4] == 1
    assert rows[1][1] == "Legacy Frontend"
    assert rows[1][2] == "resumes/legacy_frontend.pdf"
    assert json.loads(rows[1][3]) == {"raw_text": "Frontend raw text"}
    assert rows[1][4] == 0
    assert sum(row[4] for row in rows) == 1
    assert versions.count("0003_resume_content_columns") == 1
    assert versions.count("0004_resume_v01_columns") == 1

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE resumes
            SET title = ?, source_file_path = ?, content_json = ?
            WHERE id = 1
            """,
            (
                "User Edited Title",
                "resumes/user-edited.pdf",
                json.dumps(
                    {"career_intent": {"target_roles": ["Backend Engineer"]}},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ),
        )

    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        rows_after_second_init = conn.execute(
            """
            SELECT id, title, source_file_path, content_json, is_master
            FROM resumes
            ORDER BY id
            """
        ).fetchall()
        versions_after_second_init = [
            row[0]
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]

    assert rows_after_second_init[0] == (
        1,
        "User Edited Title",
        "resumes/user-edited.pdf",
        '{"career_intent":{"target_roles":["Backend Engineer"]}}',
        1,
    )
    assert rows_after_second_init[1] == rows[1]
    assert sum(row[4] for row in rows_after_second_init) == 1
    assert versions_after_second_init.count("0003_resume_content_columns") == 1
    assert versions_after_second_init.count("0004_resume_v01_columns") == 1


def test_init_database_collapses_multiple_active_resume_masters(tmp_path):
    db_path = tmp_path / "data.db"
    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO resumes (name, title, is_master, source, content_json)
            VALUES ('One', 'One', 1, 'manual', '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO resumes (name, title, is_master, source, content_json)
            VALUES ('Two', 'Two', 1, 'manual', '{}')
            """
        )

    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, is_master
            FROM resumes
            WHERE deleted_at IS NULL
            ORDER BY id
            """
        ).fetchall()

    assert rows == [(1, 1), (2, 0)]
