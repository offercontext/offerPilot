from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from scripts.evaluation_subject import core_observer as observer


@pytest.fixture
def database(tmp_path: Path):
    db = tmp_path / "data.db"
    with sqlite3.connect(db) as c:
        for table, keys in observer.PROFILES["context_sources.v1"].items():
            columns = ",".join(observer.quote(k) + " TEXT" for k in keys)
            c.execute(
                f"CREATE TABLE {observer.quote(table)}({columns}, payload TEXT, PRIMARY KEY({','.join(observer.quote(k) for k in keys)}))"
            )
    observer.install(db, "context_sources.v1")
    return db


@pytest.mark.parametrize("table", list(observer.PROFILES["context_sources.v1"]))
def test_each_registered_table_commit_rollback_compensate(database, table):
    keys = observer.PROFILES["context_sources.v1"][table]
    columns = ",".join(observer.quote(k) for k in keys)
    values = ["original"] * len(keys)
    with sqlite3.connect(database) as c:
        c.execute(
            f"INSERT INTO {observer.quote(table)}({columns},payload) VALUES({','.join('?' for _ in keys)},?)",
            [*values, "a"],
        )
    with sqlite3.connect(database) as c:
        c.execute(f'UPDATE {observer.quote(table)} SET payload="rollback"')
        c.rollback()
    with sqlite3.connect(database) as c:
        c.execute(f'UPDATE {observer.quote(table)} SET payload="bad"')
    with sqlite3.connect(database) as c:
        c.execute(f'UPDATE {observer.quote(table)} SET payload="a"')
    with sqlite3.connect(database) as c:
        c.execute(f'UPDATE {observer.quote(table)} SET {observer.quote(keys[0])}="new"')
    with sqlite3.connect(database) as c:
        c.execute(f"DELETE FROM {observer.quote(table)}")
    data = observer.read(database)
    assert data["coverage"]["gaps"] == []
    assert [r["operation"] for r in data["deltas"]] == [
        "INSERT",
        "UPDATE",
        "UPDATE",
        "UPDATE",
        "DELETE",
    ]
    assert data["deltas"][-2]["old_key"] != data["deltas"][-2]["new_key"]
    assert data["snapshot"][table] == []


@pytest.mark.parametrize("table", list(observer.PROFILES["context_sources.v1"]))
def test_every_table_commit_survives_process_crash_and_uncommitted_write_does_not(database, table):
    keys = observer.PROFILES["context_sources.v1"][table]
    columns = ",".join(observer.quote(k) for k in keys)
    query = f"INSERT INTO {observer.quote(table)}({columns},payload) VALUES({','.join('?' for _ in keys)},?)"
    program = """import os,sqlite3,sys,json
db,query,keys=sys.argv[1:]
c=sqlite3.connect(db)
c.execute(query, ['committed']*int(keys)+[sqlite3.Binary(b'bytes')])
c.commit()
c.execute(query, ['uncommitted']*int(keys)+['lost'])
os._exit(17)
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(database), query, str(len(keys))], timeout=15
    )
    assert result.returncode == 17
    data = observer.read(database)
    assert len(data["deltas"]) == 1
    assert len(data["snapshot"][table]) == 1
    assert data["snapshot"][table][0]["payload"] == {"$blob_hex": "6279746573".upper()}
    assert data["coverage"]["gaps"] == []


def test_profile_is_closed_and_tampering_is_gap(database):
    with pytest.raises(ValueError):
        observer.install(database, "arbitrary")
    with sqlite3.connect(database) as c:
        c.execute("DROP TRIGGER _eval_core_applications_UPDATE")
    assert "trigger_changed" in observer.read(database)["coverage"]["gaps"]
