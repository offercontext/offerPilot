"""SQLite-only bridge code, also loaded by absolute path in historical subjects.

Ordinary DML only. This file must not import offerpilot or third-party packages.
Sequence numbers identify durable records, never transaction or commit counts.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

TABLES = ("applications", "application_events")
VERSION = "p0-v1"


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    path = path.resolve(strict=True)
    conn = sqlite3.connect(path.as_uri() + "?mode=ro" if readonly else str(path),
                           uri=readonly, timeout=2)
    conn.row_factory = sqlite3.Row
    return conn


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def schema(conn: sqlite3.Connection) -> dict[str, Any]:
    result = {}
    for table in TABLES:
        rows = conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
        if not rows or "id" not in [r[1] for r in rows]:
            raise ValueError(f"observer requires migrated table: {table}")
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                           (table,)).fetchone()[0]
        result[table] = {"columns": [list(r) for r in rows], "ddl": ddl}
    return result


def settings(conn: sqlite3.Connection) -> dict[str, Any]:
    return {name: conn.execute(f"PRAGMA {name}").fetchone()[0]
            for name in ("journal_mode", "synchronous", "foreign_keys", "recursive_triggers")}


def _json_record(prefix: str, columns: list[str]) -> str:
    args = []
    for column in columns:
        ref = f"{prefix}.{_quote(column)}"
        args.extend(["'" + column.replace("'", "''") + "'",
                     f"CASE WHEN typeof({ref})='blob' THEN "
                     f"json_object('$blob_hex',hex({ref})) ELSE {ref} END"])
    return "json_object(" + ",".join(args) + ")"


def _triggers(conn: sqlite3.Connection) -> list[list[Any]]:
    return [list(r) for r in conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE '_eval_%' "
        "ORDER BY name")]


def install(path: Path) -> dict[str, Any]:
    with connect(path) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='_eval_observer_meta'").fetchone():
            raise ValueError("observer already installed; never repair evaluation evidence")
        conn.execute("SELECT json_object('probe', NULL, 'number', 1)").fetchone()
        business = schema(conn)
        columns = {t: [r[1] for r in business[t]["columns"]] for t in TABLES}
        conn.executescript("""
            CREATE TABLE _eval_observer_meta (
                observer_version TEXT NOT NULL, coverage_hash TEXT NOT NULL,
                schema_hash TEXT NOT NULL, trigger_hash TEXT NOT NULL,
                active_epoch INTEGER NOT NULL, installed_at TEXT NOT NULL,
                columns_json TEXT NOT NULL, settings_json TEXT NOT NULL
            );
            CREATE TABLE _eval_observation_log (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL, epoch INTEGER NOT NULL, table_name TEXT,
                row_key TEXT, operation TEXT, old_json TEXT, new_json TEXT,
                marker_json TEXT
            );
        """)
        for table, cols in columns.items():
            for operation in ("INSERT", "UPDATE", "DELETE"):
                old = _json_record("OLD", cols) if operation != "INSERT" else "NULL"
                new = _json_record("NEW", cols) if operation != "DELETE" else "NULL"
                key = "OLD" if operation == "DELETE" else "NEW"
                conn.execute(f"""CREATE TRIGGER {_quote('_eval_' + table + '_' + operation)}
                    AFTER {operation} ON {_quote(table)} BEGIN
                    INSERT INTO _eval_observation_log
                    (kind,epoch,table_name,row_key,operation,old_json,new_json)
                    VALUES ('dml',(SELECT active_epoch FROM _eval_observer_meta),
                            '{table}',CAST({key}.id AS TEXT),'{operation}',{old},{new}); END""")
        schema_hash, trigger_hash = _hash(business), _hash(_triggers(conn))
        recorded_settings = settings(conn)
        conn.execute("INSERT INTO _eval_observer_meta VALUES (?,?,?,?,1,datetime('now'),?,?)",
                     (VERSION, _hash(columns), schema_hash, trigger_hash,
                      json.dumps(columns), json.dumps(recorded_settings)))
        conn.commit()
    start = marker(path, "observation_started", {})
    return {"observer_version": VERSION, "columns": columns,
            "schema_hash": schema_hash, "trigger_hash": trigger_hash,
            "sqlite_settings": recorded_settings, "epochs": [1], "start_seq": start}


def marker(path: Path, name: str, metadata: dict[str, Any]) -> int:
    with connect(path) as conn:
        cursor = conn.execute(
            "INSERT INTO _eval_observation_log(kind,epoch,marker_json) "
            "VALUES ('marker',(SELECT active_epoch FROM _eval_observer_meta),?)",
            (json.dumps({"name": name, **metadata}, sort_keys=True),))
        conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid


def next_epoch(path: Path) -> int:
    """Caller must have joined the previous process before invoking this."""
    with connect(path) as conn:
        conn.execute("UPDATE _eval_observer_meta SET active_epoch=active_epoch+1")
        epoch = int(conn.execute("SELECT active_epoch FROM _eval_observer_meta").fetchone()[0])
        conn.execute("INSERT INTO _eval_observation_log(kind,epoch,marker_json) VALUES ('marker',?,?)",
                     (epoch, json.dumps({"name": "epoch_started", "epoch": epoch})))
    return epoch


def read(path: Path) -> dict[str, Any]:
    with connect(path, readonly=True) as conn:
        conn.execute("BEGIN")
        gaps = []
        meta = dict(conn.execute("SELECT * FROM _eval_observer_meta").fetchone())
        if _hash(schema(conn)) != meta["schema_hash"]:
            gaps.append("business_schema_changed")
        if _hash(_triggers(conn)) != meta["trigger_hash"]:
            gaps.append("observer_triggers_changed")
        deltas, markers = [], []
        for row in conn.execute("SELECT * FROM _eval_observation_log ORDER BY seq"):
            if row["kind"] == "dml":
                deltas.append({"evidence_id": f"dml:{row['seq']}", "seq": row["seq"],
                               "epoch": row["epoch"], "table": row["table_name"],
                               "operation": row["operation"], "row_key": row["row_key"],
                               "old": json.loads(row["old_json"]) if row["old_json"] else None,
                               "new": json.loads(row["new_json"]) if row["new_json"] else None})
            else:
                markers.append({"seq": row["seq"], "epoch": row["epoch"],
                                **json.loads(row["marker_json"])})
        snapshots = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {_quote(t)} ORDER BY id")]
                     for t in TABLES}
        high = conn.execute("SELECT coalesce(max(seq),0) FROM _eval_observation_log").fetchone()[0]
        allocation = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='_eval_observation_log'").fetchone()
        sequences = sorted([r["seq"] for r in deltas] + [r["seq"] for r in markers])
        if sequences != list(range(1, (allocation[0] if allocation else 0) + 1)):
            gaps.append("observation_records_missing")
        epoch_markers = {m["epoch"] for m in markers if m["name"] in {"epoch_started", "observation_started"}}
        if epoch_markers != set(range(1, meta["active_epoch"] + 1)):
            gaps.append("epoch_marker_missing")
        return {"snapshot": snapshots, "deltas": deltas, "markers": markers,
                "high_water": high, "gaps": gaps, "meta": meta}


def backup(path: Path, destination: Path) -> None:
    if destination.exists() or path.resolve() == destination.resolve():
        raise ValueError("sealed database must be new")
    # Keep the original DB and its WAL/journal in the private directory. SQLite's
    # backup API recovers and includes committed WAL, unlike copying data.db.
    with connect(path) as source, sqlite3.connect(destination) as target:
        source.backup(target)
