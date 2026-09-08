import sqlite3

from offerpilot.db import init_database
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.resumes import ResumeCreate, ResumesRepository


def _table_columns(db_path, table):
    with sqlite3.connect(db_path) as connection:
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _table_info(db_path, table):
    with sqlite3.connect(db_path) as connection:
        return list(connection.execute(f"PRAGMA table_info({table})"))


def _foreign_keys(db_path, table):
    with sqlite3.connect(db_path) as connection:
        return list(connection.execute(f"PRAGMA foreign_key_list({table})"))


def _migration_count(db_path, version):
    with sqlite3.connect(db_path) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?", (version,)
        ).fetchone()[0]


def test_application_jd_versions_migration_adds_table_and_identity_columns(tmp_path):
    db_path = tmp_path / "data.db"

    init_database(db_path)

    assert _table_columns(db_path, "application_jd_versions") == {
        "id",
        "application_id",
        "version_number",
        "jd_text",
        "content_sha256",
        "source_url",
        "source_kind",
        "idempotency_key",
        "request_fingerprint_sha256",
        "created_at",
    }
    for table in (
        "jd_analyses",
        "resume_matches",
        "application_material_kits",
        "material_revision_proposals",
        "opportunity_fit_reviews",
        "opportunity_fit_review_sessions",
        "opportunity_fit_review_stages",
        "interview_preparation_proposals",
        "mock_interview_attempts",
    ):
        assert "jd_version_id" in _table_columns(db_path, table)
        assert not any(foreign_key[3] == "jd_version_id" for foreign_key in _foreign_keys(db_path, table))

    assert _migration_count(db_path, "0018_application_jd_versions") == 1
    assert any(row[1] == "jd_version_id" and row[2].upper() == "INTEGER" and row[3] == 0 for row in _table_info(db_path, "jd_analyses"))
    application_foreign_keys = _foreign_keys(db_path, "application_jd_versions")
    assert any(
        foreign_key[2] == "applications"
        and foreign_key[3] == "application_id"
        and foreign_key[6].upper() == "CASCADE"
        for foreign_key in application_foreign_keys
    )


def test_application_jd_versions_migration_is_idempotent_and_preserves_existing_rows(tmp_path):
    db_path = tmp_path / "data.db"
    session_factory = init_database(db_path)
    application = ApplicationsRepository(session_factory).create(
        ApplicationCreate(company_name="星云数据", position_name="后端工程师")
    )

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO application_jd_versions "
            "(application_id, version_number, jd_text, content_sha256, source_url, source_kind, "
            "idempotency_key, request_fingerprint_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (application.id, 1, "JD 原文", "hash-v1", None, "ui", "jd-key-00000001", "request-hash"),
        )
        before = connection.execute(
            "SELECT application_id, version_number, jd_text, content_sha256, source_url, source_kind, "
            "idempotency_key, request_fingerprint_sha256 FROM application_jd_versions"
        ).fetchall()

    init_database(db_path)

    with sqlite3.connect(db_path) as connection:
        after = connection.execute(
            "SELECT application_id, version_number, jd_text, content_sha256, source_url, source_kind, "
            "idempotency_key, request_fingerprint_sha256 FROM application_jd_versions"
        ).fetchall()
    assert after == before
    assert _migration_count(db_path, "0018_application_jd_versions") == 1


def test_application_delete_keeps_history_identity_and_cascades_only_jd_versions(tmp_path):
    db_path = tmp_path / "data.db"
    session_factory = init_database(db_path)
    applications = ApplicationsRepository(session_factory)
    resumes = ResumesRepository(session_factory)
    application = applications.create(
        ApplicationCreate(company_name="星云数据", position_name="后端工程师")
    )
    resume = resumes.create(ResumeCreate(name="筱哲简历", parsed_data="resume"))

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO application_jd_versions "
            "(application_id, version_number, jd_text, content_sha256, source_kind, idempotency_key, "
            "request_fingerprint_sha256) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (application.id, 1, "冻结 JD", "hash-v1", "ui", "jd-key-00000002", "request-hash-2"),
        )
        jd_version_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.execute(
            "INSERT INTO jd_analyses (application_id, jd_source, jd_text, result, jd_version_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (application.id, "application_jd", "冻结 JD", '{"summary":"原样"}', jd_version_id),
        )
        connection.execute(
            "INSERT INTO resume_matches (resume_id, application_id, jd_text, result, jd_version_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (resume.id, application.id, "冻结 JD", '{"match":true}', jd_version_id),
        )
        connection.execute("DELETE FROM applications WHERE id = ?", (application.id,))
        assert connection.execute(
            "SELECT COUNT(*) FROM application_jd_versions WHERE id = ?", (jd_version_id,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT application_id, jd_version_id, jd_text, result FROM jd_analyses"
        ).fetchone() == (None, jd_version_id, "冻结 JD", '{"summary":"原样"}')
        assert connection.execute(
            "SELECT application_id, jd_version_id, jd_text, result FROM resume_matches"
        ).fetchone() == (None, jd_version_id, "冻结 JD", '{"match":true}')
