"""Deterministic checks of the image schema transaction, without provider I/O."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from threading import Barrier, Lock

import pytest
from sqlalchemy import event

from offerpilot import db as database


class ImageMigrationFinished(Exception):
    """Stop after the real image transaction, before unrelated migrations."""


def prepare(path: Path, *, existing: bool, keep_trigger: bool = False) -> str:
    factory = database.init_database(path)
    factory.kw["bind"].dispose()
    expected = '[{"id":"preserved-image-reference"}]' if existing else "[]"
    with sqlite3.connect(path) as connection:
        if not keep_trigger:
            connection.execute("DROP TRIGGER chat_attachment_immutable")
        connection.execute("DELETE FROM schema_migrations WHERE version='0034_chat_image_messages'")
        connection.execute("INSERT INTO conversations(id,title) VALUES(1,'synthetic')")
        connection.execute(
            "INSERT INTO chat_messages(id,conversation_id,role,content,image_refs_json) VALUES(1,1,'user','keep',?)",
            (expected,),
        )
        if not existing:
            connection.execute("ALTER TABLE chat_messages DROP COLUMN image_refs_json")
    return expected


@pytest.mark.parametrize("existing,keep_trigger", [(False, False), (True, False), (False, True)])
def test_image_schema_serializes_inspection_and_addition(tmp_path, monkeypatch, existing, keep_trigger):
    path = tmp_path / "image-schema.db"
    expected = prepare(path, existing=existing, keep_trigger=keep_trigger)
    begin_barrier = Barrier(2)
    observations = []
    unlocked_observations = []
    engines = []
    mutex = Lock()
    create_engine = database.create_engine
    record_migration = database._record_migration

    def instrumented_engine(*args, **kwargs):
        engine = create_engine(*args, **kwargs)
        engines.append(engine)

        @event.listens_for(engine, "before_cursor_execute")
        def before(connection, cursor, statement, parameters, context, executemany):
            if statement == "BEGIN IMMEDIATE" and not connection.info.get("image_lock_started"):
                # Both real connections attempt the write lock together. No
                # sleep, changed timeout, or serialized executor masks a race.
                begin_barrier.wait(timeout=10)
            if statement == "PRAGMA table_info(chat_messages)":
                columns = connection.connection.driver_connection.execute(statement).fetchall()
                with mutex:
                    target = observations if connection.info.get("image_lock_started") else unlocked_observations
                    target.append(any(row[1] == "image_refs_json" for row in columns))

        @event.listens_for(engine, "after_cursor_execute")
        def after(connection, cursor, statement, parameters, context, executemany):
            if statement == "BEGIN IMMEDIATE":
                connection.info["image_lock_started"] = True

        return engine

    def stop_after_image_commit(engine, version, description):
        if version == "0034_chat_image_messages":
            # The init frame still owns image_columns here. A partly consumed
            # PRAGMA cursor can block subsequent DDL even after commit.
            with engine.begin() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                connection.exec_driver_sql("CREATE TABLE image_schema_cursor_probe (id INTEGER)")
                connection.exec_driver_sql("DROP TABLE image_schema_cursor_probe")
            record_migration(engine, version, description)
            raise ImageMigrationFinished
        return record_migration(engine, version, description)

    monkeypatch.setattr(database, "create_engine", instrumented_engine)
    monkeypatch.setattr(database, "_record_migration", stop_after_image_commit)

    def initialize():
        with pytest.raises(ImageMigrationFinished):
            database.init_database(path)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(initialize) for _ in range(2)]
            for future in futures:
                future.result(timeout=20)
        assert unlocked_observations == [existing, existing]
        assert observations == ([True, True] if existing else [False, True])
        monkeypatch.setattr(database, "create_engine", create_engine)
        monkeypatch.setattr(database, "_record_migration", record_migration)
        factory = database.init_database(path)
        factory.kw["bind"].dispose()
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT content,image_refs_json FROM chat_messages WHERE id=1"
            ).fetchone() == ("keep", expected)
            assert connection.execute(
                "SELECT count(*) FROM schema_migrations WHERE version='0034_chat_image_messages'"
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name='chat_attachment_immutable'"
            ).fetchone() == (1,)
    finally:
        for engine in engines:
            engine.dispose()


def test_image_schema_rolls_back_column_when_trigger_creation_fails(tmp_path, monkeypatch):
    path = tmp_path / "rollback.db"
    prepare(path, existing=False)
    create_engine = database.create_engine
    engines = []

    def failing_engine(*args, **kwargs):
        engine = create_engine(*args, **kwargs)
        engines.append(engine)

        @event.listens_for(engine, "before_cursor_execute")
        def fail(connection, cursor, statement, parameters, context, executemany):
            if "CREATE TRIGGER IF NOT EXISTS chat_attachment_immutable" in statement:
                raise RuntimeError("synthetic image trigger failure")

        return engine

    monkeypatch.setattr(database, "create_engine", failing_engine)
    try:
        with pytest.raises(RuntimeError, match="synthetic image trigger failure"):
            database.init_database(path)
        with sqlite3.connect(path) as connection:
            assert "image_refs_json" not in {
                row[1] for row in connection.execute("PRAGMA table_info(chat_messages)")
            }
            assert connection.execute(
                "SELECT content FROM chat_messages WHERE id=1"
            ).fetchone() == ("keep",)
            assert connection.execute(
                "SELECT count(*) FROM schema_migrations WHERE version='0034_chat_image_messages'"
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name='chat_attachment_immutable'"
            ).fetchone() == (0,)
    finally:
        for engine in engines:
            engine.dispose()


def test_complete_image_schema_is_read_only_while_writer_holds_lock(tmp_path):
    path = tmp_path / "complete.db"
    expected = prepare(path, existing=True)
    factory = database.init_database(path)
    factory.kw["bind"].dispose()
    # No timer or retry allowance: a competing writer owns the lock throughout
    # the call, and any accidental SQLite write-lock acquisition fails at once.
    engine = database.create_engine(f"sqlite:///{path}", connect_args={"timeout": 0})
    statements = []

    @event.listens_for(engine, "before_cursor_execute")
    def only_reads(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
        assert statement.lstrip().upper().startswith(("SELECT", "PRAGMA"))

    writer = sqlite3.connect(path)
    try:
        writer.execute("BEGIN IMMEDIATE")
        database._ensure_chat_image_schema(engine)
        assert writer.in_transaction
        assert len(statements) == 2
        assert writer.execute(
            "SELECT content,image_refs_json FROM chat_messages WHERE id=1"
        ).fetchone() == ("keep", expected)
    finally:
        writer.rollback()
        writer.close()
        event.remove(engine, "before_cursor_execute", only_reads)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE image_fast_path_cursor_probe (id INTEGER)")
            connection.exec_driver_sql("DROP TABLE image_fast_path_cursor_probe")
    finally:
        engine.dispose()
