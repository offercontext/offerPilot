"""V1 receipts/grants survive the CHECK/nullable-target migration atomically."""

import re
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import Index

from offerpilot.db import init_database
from offerpilot.external_agents.migration import ensure_business_schema
from offerpilot.external_agents.service import ExternalAgentService, create_connection
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository


def legacy_workspace(tmp_path):
    sessions = init_database(tmp_path / "data.db")
    application = ApplicationsRepository(sessions).create(ApplicationCreate("旧连接", "开发"))
    connection = create_connection(sessions, "v1", [application.id], allow_scheduling=True)
    service = ExternalAgentService(sessions, connection["connection_id"])
    pending = service.call(
        "propose_interview",
        {
            "idempotency_key": str(uuid4()),
            "application_id": application.id,
            "scheduled_at": "2026-11-01T15:00:00Z",
            "duration_minutes": 60,
        },
    )
    engine = sessions.kw["bind"]
    with engine.begin() as db:
        sql = db.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name='external_agent_requests'"
        ).scalar_one()
        old = sql.replace(
            "CREATE TABLE external_agent_requests", "CREATE TABLE external_agent_requests_legacy", 1
        )
        old, count = re.subn(
            r"action IN \([^)]*\)", "action IN ('create_interview','reschedule_interview')", old
        )
        assert count == 1
        old = old.replace("application_id INTEGER", "application_id INTEGER NOT NULL")
        db.exec_driver_sql(old)
        db.exec_driver_sql(
            "INSERT INTO external_agent_requests_legacy SELECT * FROM external_agent_requests"
        )
        db.exec_driver_sql("DROP TABLE external_agent_requests")
        db.exec_driver_sql(
            "ALTER TABLE external_agent_requests_legacy RENAME TO external_agent_requests"
        )
        db.exec_driver_sql("ALTER TABLE external_agent_connections DROP COLUMN capabilities_json")
        db.exec_driver_sql(
            "DELETE FROM schema_migrations WHERE version='0036_external_agent_business'"
        )
        before = list(db.exec_driver_sql("SELECT * FROM external_agent_requests ORDER BY id"))
    return sessions, service, pending, before


def test_v1_pending_request_exact_rows_and_digest_survive_repeated_upgrade(tmp_path):
    sessions, service, pending, before = legacy_workspace(tmp_path)
    engine = sessions.kw["bind"]
    ensure_business_schema(engine)
    ensure_business_schema(engine)
    with engine.connect() as db:
        assert (
            list(db.exec_driver_sql("SELECT * FROM external_agent_requests ORDER BY id")) == before
        )
        assert (
            db.exec_driver_sql(
                "SELECT capabilities_json FROM external_agent_connections"
            ).scalar_one()
            == "[]"
        )
        assert not list(db.exec_driver_sql("PRAGMA foreign_key_check"))
    assert service.call("get_request", {"request_id": pending["request_id"]}) == pending
    view = service.review(pending["request_id"])
    result = service.decide(view["request_id"], view["proposal_digest"], "approve")
    assert result["status"] == "committed"
    assert service.grant().get("capabilities", []) == []
    engine.dispose()


def test_failed_migration_rolls_back_table_drop_and_grant_column(tmp_path, monkeypatch):
    sessions, _, _, before = legacy_workspace(tmp_path)
    engine = sessions.kw["bind"]
    original = Index.create

    def fail_index(index, *args, **kwargs):
        if index.table.name == "external_agent_requests":
            raise RuntimeError("failure after table replacement")
        return original(index, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Index, "create", fail_index)
        with pytest.raises(RuntimeError, match="failure after table replacement"):
            ensure_business_schema(engine)
    with engine.connect() as db:
        assert (
            list(db.exec_driver_sql("SELECT * FROM external_agent_requests ORDER BY id")) == before
        )
        assert "capabilities_json" not in {
            row[1] for row in db.exec_driver_sql("PRAGMA table_info(external_agent_connections)")
        }
        assert (
            "create_application"
            not in db.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE name='external_agent_requests'"
            ).scalar_one()
        )
        assert (
            db.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE name='external_agent_requests_upgrade'"
            ).scalar_one()
            == 0
        )
    ensure_business_schema(engine)
    engine.dispose()


def test_concurrent_migrators_preserve_all_old_receipts(tmp_path):
    sessions, _, _, before = legacy_workspace(tmp_path)
    engine = sessions.kw["bind"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: ensure_business_schema(engine), range(2)))
    with engine.connect() as db:
        assert (
            list(db.exec_driver_sql("SELECT * FROM external_agent_requests ORDER BY id")) == before
        )
        assert not list(db.exec_driver_sql("PRAGMA foreign_key_check"))
    engine.dispose()
