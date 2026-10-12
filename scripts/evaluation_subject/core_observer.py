"""Bounded, stdlib-only committed-DML observer. Never imports target code."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

PROFILES = {
    "business.v1": {"applications": ["id"], "application_events": ["id"]},
    "context_sources.v1": {
        "applications": ["id"],
        "application_events": ["id"],
        "confirmed_memories": ["id"],
        "confirmed_memory_versions": ["id"],
        "confirmed_memory_mutations": ["id"],
        "knowledge_sources": ["id"],
        "knowledge_extraction_snapshots": ["id"],
        "knowledge_evidence": ["id"],
        "knowledge_notes": ["id"],
        "knowledge_note_versions": ["id"],
        "knowledge_note_evidence": ["note_version_id", "block_id", "evidence_id"],
        "knowledge_captured_source_metadata": ["source_id"],
        "context_contributor_settings": ["id"],
    },
}
VERSION = "core-observer-v1"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(
        path.resolve(strict=True).as_uri() + "?mode=ro" if readonly else str(path),
        uri=readonly,
        timeout=3,
    )
    conn.row_factory = sqlite3.Row
    return conn


def quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def record(alias: str, columns: list[str]) -> str:
    pairs = []
    for c in columns:
        expr = f"{alias}.{quote(c)}"
        pairs += [
            "'" + c.replace("'", "''") + "'",
            f"CASE WHEN typeof({expr})='blob' THEN json_object('$blob_hex',hex({expr})) ELSE {expr} END",
        ]
    return "json_object(" + ",".join(pairs) + ")"


def schema(conn: sqlite3.Connection, tables: dict[str, list[str]]) -> dict[str, Any]:
    return {t: [tuple(r) for r in conn.execute(f"PRAGMA table_info({quote(t)})")] for t in tables}


def triggers(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE '_eval_core_%' ORDER BY name"
        )
    ]


def install(path: Path, profile: str) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError("unregistered observation profile")
    tables = PROFILES[profile]
    with connect(path) as conn:
        before = schema(conn, tables)
        if any(not cols for cols in before.values()):
            raise ValueError("profile table missing")
        for t, cols in before.items():
            actual_pk = [r[1] for r in sorted(cols, key=lambda r: r[5]) if r[5]]
            if actual_pk != tables[t]:
                raise ValueError("registered primary key differs: " + t)
        conn.execute(
            "CREATE TABLE _eval_core_meta(profile TEXT,version TEXT,schema_hash TEXT,trigger_hash TEXT,epoch INTEGER)"
        )
        conn.execute(
            "CREATE TABLE _eval_core_log(seq INTEGER PRIMARY KEY AUTOINCREMENT,epoch INTEGER,kind TEXT,table_name TEXT,operation TEXT,old_key TEXT,new_key TEXT,old_json TEXT,new_json TEXT,marker_json TEXT)"
        )
        for t, cols in before.items():
            names = [r[1] for r in cols]
            for op in ("INSERT", "UPDATE", "DELETE"):
                old = record("OLD", names) if op != "INSERT" else "NULL"
                new = record("NEW", names) if op != "DELETE" else "NULL"
                old_key = record("OLD", tables[t]) if op != "INSERT" else "NULL"
                new_key = record("NEW", tables[t]) if op != "DELETE" else "NULL"
                conn.execute(
                    f"CREATE TRIGGER {quote('_eval_core_' + t + '_' + op)} AFTER {op} ON {quote(t)} BEGIN INSERT INTO _eval_core_log(epoch,kind,table_name,operation,old_key,new_key,old_json,new_json) VALUES ((SELECT epoch FROM _eval_core_meta),'dml','{t}','{op}',{old_key},{new_key},{old},{new}); END"
                )
        conn.execute(
            "INSERT INTO _eval_core_meta VALUES(?,?,?,?,1)",
            (profile, VERSION, digest(before), digest(triggers(conn))),
        )
    marker(path, "observation_started", {})
    coverage: dict[str, Any] = read(path)["coverage"]
    return coverage


def marker(path: Path, name: str, data: dict[str, Any]) -> int:
    with connect(path) as conn:
        c = conn.execute(
            "INSERT INTO _eval_core_log(epoch,kind,marker_json) VALUES((SELECT epoch FROM _eval_core_meta),'marker',?)",
            (json.dumps({"name": name, **data}, sort_keys=True),),
        )
        assert c.lastrowid is not None
        return c.lastrowid


def next_epoch(path: Path) -> int:
    with connect(path) as conn:
        conn.execute("UPDATE _eval_core_meta SET epoch=epoch+1")
        epoch = int(conn.execute("SELECT epoch FROM _eval_core_meta").fetchone()[0])
    marker(path, "epoch_started", {"epoch": epoch})
    return epoch


def read(path: Path) -> dict[str, Any]:
    with connect(path, True) as conn:
        conn.execute("BEGIN")
        meta = dict(conn.execute("SELECT * FROM _eval_core_meta").fetchone())
        if meta["profile"] not in PROFILES or meta["version"] != VERSION:
            raise ValueError("observer profile/version unrecognized")
        tables = PROFILES[meta["profile"]]
        rows = [dict(r) for r in conn.execute("SELECT * FROM _eval_core_log ORDER BY seq")]
        allocated = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='_eval_core_log'"
        ).fetchone()
        gaps = []
        if digest(schema(conn, tables)) != meta["schema_hash"]:
            gaps.append("schema_changed")
        if digest(triggers(conn)) != meta["trigger_hash"]:
            gaps.append("trigger_changed")
        if [r["seq"] for r in rows] != list(range(1, int(allocated[0] if allocated else 0) + 1)):
            gaps.append("sequence_gap")
        markers = [
            {"seq": r["seq"], "epoch": r["epoch"], **json.loads(r["marker_json"])}
            for r in rows
            if r["kind"] == "marker"
        ]
        epochs = {
            r["epoch"] for r in markers if r["name"] in {"observation_started", "epoch_started"}
        }
        if epochs != set(range(1, meta["epoch"] + 1)):
            gaps.append("epoch_gap")
        deltas = [
            {
                "evidence_id": f"dml:{r['seq']}",
                "seq": r["seq"],
                "epoch": r["epoch"],
                "table": r["table_name"],
                "operation": r["operation"],
                **{
                    k: json.loads(r[k]) if r[k] else None
                    for k in ("old_key", "new_key", "old_json", "new_json")
                },
            }
            for r in rows
            if r["kind"] == "dml"
        ]
        snapshots = {
            t: [
                {
                    k: {"$blob_hex": v.hex().upper()} if isinstance(v, bytes) else v
                    for k, v in dict(r).items()
                }
                for r in conn.execute(
                    f"SELECT * FROM {quote(t)} ORDER BY {','.join(quote(k) for k in keys)}"
                )
            ]
            for t, keys in tables.items()
        }
        return {
            "snapshot": snapshots,
            "deltas": deltas,
            "markers": markers,
            "high_water": rows[-1]["seq"] if rows else 0,
            "coverage": {**meta, "tables": tables, "gaps": gaps, "epochs": sorted(epochs)},
        }


def backup(source: Path, target: Path) -> None:
    if target.exists():
        raise ValueError("sealed backup cannot be overwritten")
    with connect(source) as a, sqlite3.connect(target) as b:
        a.backup(b)
