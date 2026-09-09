"""Independent current Note and Evidence recall, using one bounded read snapshot."""
from __future__ import annotations

from hashlib import sha256
import json
import re
import sqlite3
from types import SimpleNamespace
from typing import Any

from offerpilot.context_projector.contracts import ProjectionError
from offerpilot.context_projector.loader import fetch_rows
from offerpilot.knowledge.interview_capture import note_fingerprint
from offerpilot.knowledge.search import parse_query


def _capture_current(connection: sqlite3.Connection, source_id: int) -> bool:
    metadata = connection.execute("SELECT origin_note_id, note_fingerprint FROM knowledge_captured_source_metadata WHERE source_id=?", (source_id,)).fetchone()
    if metadata is None:
        return True
    cursor = connection.execute("""SELECT n.* FROM interview_notes n LEFT JOIN applications a ON a.id=n.application_id
        WHERE n.id=? AND (n.application_id IS NULL OR (a.id IS NOT NULL AND a.deleted_at IS NULL))
        AND length(CAST(n.company || n.position || n.round || n.date || n.questions || n.self_reflection || n.difficulty_points || n.mood AS BLOB))<=65536""", (metadata[0],))
    row = cursor.fetchone()
    if row is None:
        return False
    note = SimpleNamespace(**dict(zip((column[0] for column in cursor.description), row, strict=True)))
    return bool(note_fingerprint(note) == metadata[1])


def _evidence(connection: sqlite3.Connection, evidence_id: str, *, source_id: int | None = None) -> dict[str, object] | None:
    row = connection.execute("""SELECT e.id, e.source_id, e.snapshot_id, e.canonical_excerpt, e.content_hash,
            substr(x.canonical_text, e.char_start+1, e.char_end-e.char_start), x.digest
        FROM knowledge_evidence e JOIN knowledge_sources s ON s.id=e.source_id
        JOIN knowledge_extraction_snapshots x ON x.id=e.snapshot_id AND x.source_id=s.id
        WHERE e.id=? AND s.lifecycle='active' AND s.deleted_at IS NULL AND s.archived_at IS NULL
          AND s.active_snapshot_id=e.snapshot_id AND e.char_start>=0 AND e.char_end>e.char_start
          AND e.char_end-e.char_start<=16000
          AND length(e.canonical_excerpt)<=16000""", (evidence_id,)).fetchone()
    if row is None or (source_id is not None and row[1] != source_id):
        return None
    if row[3] != row[5] or sha256(row[3].encode()).hexdigest() != row[4]:
        raise ProjectionError("knowledge_evidence_integrity_failed")
    if not _capture_current(connection, row[1]):
        return None
    return {"kind": "evidence", "evidence_id": row[0], "source_id": row[1], "snapshot_id": row[2],
            "excerpt": row[3], "content_hash": row[4], "snapshot_digest": row[6]}


def recall_knowledge(connection: sqlite3.Connection, query: str) -> list[dict[str, object]]:
    # Keep the whole bounded question: a polite Chinese preamble must not use
    # up the term budget before the actual subject. Bigrams also retain short
    # names when the source inserts another word (蓝鲸协议 / 蓝鲸幂等协议).
    bounded_query = query[:500]
    short_terms = [run[index:index + 2]
        for run in re.findall(r"[㐀-鿿豈-﫿぀-ヿ가-힯]{2,}", bounded_query)
        for index in range(len(run) - 1)]
    terms = tuple(dict.fromkeys(term.lower() for term in (*parse_query(bounded_query).terms, *short_terms)))
    if not terms:
        return []
    # One bound JSON parameter avoids a query-depth/parameter explosion. Rank
    # both lanes before their independent caps so old weak hits cannot hide a
    # later relevant source; source integrity is still checked below.
    query_terms = json.dumps(terms, ensure_ascii=False)
    notes = fetch_rows(connection.execute("""WITH terms AS MATERIALIZED (SELECT value AS term FROM json_each(?)),
        active_notes AS MATERIALIZED (SELECT n.id AS note_id, n.title, v.id AS version_id, v.source_id,
            v.content_json, v.content_hash, lower(n.title || ' ' || v.content_json) AS search_text
            FROM knowledge_notes n JOIN knowledge_note_versions v
            ON v.id=n.current_version_id AND v.note_id=n.id
            JOIN knowledge_sources s ON s.id=v.source_id
            WHERE n.archived_at IS NULL AND s.lifecycle='active'
            AND s.deleted_at IS NULL AND s.archived_at IS NULL AND length(v.content_json)<=65536),
        matches AS MATERIALIZED (SELECT n.note_id, t.term FROM active_notes n JOIN terms t
            ON instr(n.search_text, t.term)>0),
        document_frequency AS (SELECT term, count(*) AS frequency FROM matches GROUP BY term),
        scored AS (SELECT m.note_id, sum(1.0 / df.frequency) AS relevance
            FROM matches m JOIN document_frequency df ON df.term=m.term
            GROUP BY m.note_id)
        SELECT n.note_id, n.title, n.version_id, n.source_id, n.content_json, n.content_hash
        FROM active_notes n JOIN scored ON scored.note_id=n.note_id
        ORDER BY scored.relevance DESC, n.note_id LIMIT 24""", (query_terms,)), max_rows=24)
    note_items: list[dict[str, object]] = []
    for row in notes:
        note_id, title, version_id, source_id, raw, content_hash = row
        if sha256(str(raw).encode()).hexdigest() != content_hash:
            raise ProjectionError("knowledge_note_integrity_failed")
        if not _capture_current(connection, int(str(source_id))):
            continue
        content: Any = json.loads(str(raw))
        if not isinstance(content, dict) or not isinstance(content.get("blocks"), list):
            raise ProjectionError("knowledge_note_integrity_failed")
        links = fetch_rows(connection.execute("SELECT block_id, evidence_id FROM knowledge_note_evidence WHERE note_version_id=? ORDER BY block_id,evidence_id LIMIT 161", (version_id,)), max_rows=160)
        evidence: list[dict[str, object]] = []
        evidence_by_block: dict[str, list[str]] = {}
        valid = True
        block_ids = {block.get("block_id") for block in content["blocks"] if isinstance(block, dict)}
        for block_id, evidence_id in links:
            if block_id not in block_ids:
                raise ProjectionError("knowledge_note_reference_invalid")
            item = _evidence(connection, str(evidence_id), source_id=int(str(source_id)))
            if item is None:
                valid = False
                break
            evidence_by_block.setdefault(str(block_id), []).append(str(item["excerpt"] ))
            if str(evidence_id) not in {str(item["evidence_id"]) for item in evidence}:
                evidence.append(item)
        if not valid or not evidence:
            continue
        for block in content["blocks"]:
            if not isinstance(block, dict) or not isinstance(block.get("evidence_refs"), list):
                raise ProjectionError("knowledge_note_reference_invalid")
            refs = block["evidence_refs"]
            if not refs or any(not isinstance(ref, dict) for ref in refs) or sorted(ref.get("excerpt", "") for ref in refs) != sorted(evidence_by_block.get(str(block.get("block_id")), [])):
                raise ProjectionError("knowledge_note_reference_invalid")
        note_items.append({"kind": "confirmed_note", "note_id": note_id, "version_id": version_id,
            "source_id": source_id, "content_hash": content_hash, "title": title, "content": content,
            "evidence": evidence})
    candidates = fetch_rows(connection.execute("""WITH terms AS MATERIALIZED (SELECT value AS term FROM json_each(?)),
        active_evidence AS MATERIALIZED (SELECT e.id, e.source_id, e.ordinal,
                lower(e.search_text) AS search_text
            FROM knowledge_evidence e JOIN knowledge_sources s ON s.id=e.source_id
            WHERE s.lifecycle='active' AND s.deleted_at IS NULL AND s.archived_at IS NULL
            AND s.active_snapshot_id=e.snapshot_id),
        matches AS MATERIALIZED (SELECT e.id, t.term FROM active_evidence e JOIN terms t
            ON instr(e.search_text, t.term)>0),
        document_frequency AS (SELECT term, count(*) AS frequency FROM matches GROUP BY term),
        scored AS (SELECT m.id, sum(1.0 / df.frequency) AS relevance
            FROM matches m JOIN document_frequency df ON df.term=m.term
            GROUP BY m.id)
        SELECT e.id FROM active_evidence e JOIN scored ON scored.id=e.id
        ORDER BY scored.relevance DESC, e.source_id, e.ordinal, e.id LIMIT 24""", (query_terms,)), max_rows=24)
    evidence_items = []
    for (evidence_id,) in candidates:
        item = _evidence(connection, str(evidence_id))
        if item is not None:
            evidence_items.append(item)
    # Alternate lanes so an abundance of Notes cannot hide independent Evidence.
    merged = []
    for index in range(max(len(note_items), len(evidence_items))):
        if index < len(note_items):
            merged.append(note_items[index])
        if index < len(evidence_items):
            merged.append(evidence_items[index])
    return merged
