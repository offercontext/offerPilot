from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from offerpilot.db import init_database, journal_session_factory_for_data_dir
from offerpilot.models import AgentContextSnapshot, AgentEvent, AgentRun, ChatMessage, Conversation


def _migration_versions(engine: object) -> set[str]:
    with engine.connect() as connection:  # type: ignore[attr-defined]
        return {
            str(row[0])
            for row in connection.execute(text("SELECT version FROM schema_migrations"))
        }


def test_fresh_database_has_durable_journal_schema(tmp_path: Path) -> None:
    session_factory = init_database(tmp_path / "data.db")
    engine = session_factory.kw["bind"]
    inspector = inspect(engine)

    assert {"agent_runs", "agent_events", "agent_context_snapshots"} <= set(
        inspector.get_table_names()
    )
    assert "0024_durable_execution_journal" in _migration_versions(engine)
    assert {column["name"] for column in inspector.get_columns("agent_runs")} >= {
        "initial_context_type",
        "initial_context_entity_id",
        "initial_context_ref_fingerprint",
        "fingerprint_key_id",
        "last_seq",
        "recording_status",
    }
    assert {column["name"] for column in inspector.get_columns("agent_context_snapshots")} >= {
        "manifest_schema_version",
        "fingerprint_key_id",
        "logical_input_fingerprint",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("agent_context_snapshots")
    } >= {
        "uq_agent_context_run_snapshot",
        "uq_agent_context_run_model_call",
    }


def test_migration_0024_is_recorded_once_from_0023_database(tmp_path: Path) -> None:
    db_path = tmp_path / "data.db"
    bootstrap = create_engine(f"sqlite:///{db_path}")
    with bootstrap.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE schema_migrations ("
                "version TEXT PRIMARY KEY, description TEXT NOT NULL, "
                "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO schema_migrations(version, description) "
                "VALUES ('0023_immersive_interview_studio', 'existing schema')"
            )
        )
    bootstrap.dispose()

    first = init_database(db_path)
    first.kw["bind"].dispose()
    second = init_database(db_path)
    engine = second.kw["bind"]

    with engine.connect() as connection:
        count = connection.execute(
            text(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE version = '0024_durable_execution_journal'"
            )
        ).scalar_one()
    assert count == 1


def test_deleting_input_message_sets_null_but_deleting_conversation_cascades(
    tmp_path: Path,
) -> None:
    session_factory = init_database(tmp_path / "data.db")
    with session_factory() as session:
        conversation = Conversation(title="journal-test")
        session.add(conversation)
        session.flush()
        message = ChatMessage(conversation_id=conversation.id, role="user", content="private")
        session.add(message)
        session.flush()
        run = AgentRun(
            id="11111111-1111-4111-8111-111111111111",
            conversation_id=conversation.id,
            input_message_id=message.id,
            origin_kind="user_message",
            initial_context_type="workspace",
            fingerprint_key_id="22222222-2222-4222-8222-222222222222",
            initial_transport_mode="sync",
            initial_route_kind="model",
            status="running",
        )
        session.add(run)
        session.flush()
        session.add_all(
            [
                AgentEvent(
                    id="33333333-3333-4333-8333-333333333333",
                    run_id=run.id,
                    seq=1,
                    dedupe_key="run.started",
                    event_type="run.started",
                    execution_segment_id="44444444-4444-4444-8444-444444444444",
                    payload_json='{"facts":{},"telemetry":{}}',
                    payload_digest="a" * 64,
                    fact_digest="b" * 64,
                ),
                AgentContextSnapshot(
                    id="55555555-5555-4555-8555-555555555555",
                    run_id=run.id,
                    execution_segment_id="44444444-4444-4444-8444-444444444444",
                    snapshot_key="initial:44444444-4444-4444-8444-444444444444",
                    manifest_schema_version=1,
                    snapshot_kind="initial",
                    manifest_json="{}",
                    manifest_digest="c" * 64,
                    canonicalizer_version="1",
                    logical_input_fingerprint="d" * 64,
                    fingerprint_key_id="22222222-2222-4222-8222-222222222222",
                ),
            ]
        )
        session.commit()
        conversation_id = conversation.id
        message_id = message.id
        run_id = run.id

    with session_factory() as session:
        session.delete(session.get(ChatMessage, message_id))  # type: ignore[arg-type]
        session.commit()
        session.expire_all()
        assert session.get(AgentRun, run_id).input_message_id is None  # type: ignore[union-attr]
        session.delete(session.get(Conversation, conversation_id))  # type: ignore[arg-type]
        session.commit()
        assert session.get(AgentRun, run_id) is None
        assert session.query(AgentEvent).filter_by(run_id=run_id).count() == 0
        assert session.query(AgentContextSnapshot).filter_by(run_id=run_id).count() == 0


def test_journal_session_factory_uses_existing_schema_and_foreign_keys(tmp_path: Path) -> None:
    init_database(tmp_path / "data.db")
    journal_factory = journal_session_factory_for_data_dir(tmp_path)

    with journal_factory() as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert session.execute(text("SELECT COUNT(*) FROM agent_runs")).scalar_one() == 0


def test_database_rejects_malformed_uuid_strings(tmp_path: Path) -> None:
    session_factory = init_database(tmp_path / "data.db")
    with session_factory() as session:
        conversation = Conversation(title="uuid-check")
        session.add(conversation)
        session.flush()
        session.add(
            AgentRun(
                id="x" * 36,
                conversation_id=conversation.id,
                origin_kind="system",
                initial_context_type="workspace",
                fingerprint_key_id="y" * 36,
                initial_transport_mode="sync",
                initial_route_kind="deterministic",
                status="running",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize("model", [AgentEvent, AgentContextSnapshot])
def test_database_rejects_malformed_model_call_uuid(tmp_path: Path, model: type[object]) -> None:
    session_factory = init_database(tmp_path / "data.db")
    with session_factory() as session:
        conversation = Conversation(title="model-call-uuid-check")
        session.add(conversation)
        session.flush()
        run = AgentRun(
            id="11111111-1111-4111-8111-111111111111",
            conversation_id=conversation.id,
            origin_kind="system",
            initial_context_type="workspace",
            fingerprint_key_id="22222222-2222-4222-8222-222222222222",
            initial_transport_mode="sync",
            initial_route_kind="deterministic",
            status="running",
        )
        session.add(run)
        session.flush()
        if model is AgentEvent:
            row = AgentEvent(
                id="33333333-3333-4333-8333-333333333333",
                run_id=run.id,
                seq=1,
                dedupe_key="model.requested:bad",
                event_type="model.requested",
                execution_segment_id="44444444-4444-4444-8444-444444444444",
                model_call_id="bad",
                payload_json='{"facts":{},"telemetry":{}}',
                payload_digest="a" * 64,
                fact_digest="b" * 64,
            )
        else:
            row = AgentContextSnapshot(
                id="55555555-5555-4555-8555-555555555555",
                run_id=run.id,
                execution_segment_id="44444444-4444-4444-8444-444444444444",
                snapshot_key="model-input:test",
                snapshot_kind="model_input",
                model_call_id="bad",
                manifest_json="{}",
                manifest_digest="c" * 64,
                canonicalizer_version="1",
                logical_input_fingerprint="d" * 64,
                fingerprint_key_id="22222222-2222-4222-8222-222222222222",
            )
        session.add(row)
        with pytest.raises(IntegrityError):
            session.commit()
