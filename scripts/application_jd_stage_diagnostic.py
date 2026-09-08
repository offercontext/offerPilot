from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


_TRIAGE_ARRAY_FIELDS = ("conditions", "risks", "questions", "next_steps")
_EVIDENCE_SOURCES = ("jd", "resume", "user_assertion")
_SAFE_HASH = re.compile(r"^[0-9a-f]{64}$")
_SAFE_REQUEST_ID_HASH = re.compile(r"^[0-9a-f]{12,64}$")
_SAFE_CATEGORY = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def slice_audit_records(
    records: list[dict[str, Any]], start_index: int
) -> tuple[list[dict[str, Any]], int]:
    start = max(0, min(start_index, len(records)))
    return records[start:], len(records)


def _string_leaf_count(value: Any) -> int:
    if isinstance(value, str):
        return 1
    if isinstance(value, dict):
        return sum(_string_leaf_count(child) for child in value.values())
    if isinstance(value, list):
        return sum(_string_leaf_count(child) for child in value)
    return 0


def _assertion_count(snapshot: dict[str, Any]) -> int:
    assertions = snapshot.get("candidate_assertions")
    if not isinstance(assertions, list):
        assertions = snapshot.get("user_assertions")
    if not isinstance(assertions, list):
        return 0
    return sum(
        isinstance(item, str)
        or (isinstance(item, dict) and isinstance(item.get("text"), str))
        for item in assertions
    )


def _evidence_counts(snapshot: dict[str, Any]) -> dict[str, int]:
    counts = {source: 0 for source in _EVIDENCE_SOURCES}
    jd = snapshot.get("jd")
    if isinstance(jd, dict) and isinstance(jd.get("text"), str) and jd["text"].strip():
        counts["jd"] += 1
    material_kit = snapshot.get("material_kit")
    if (
        isinstance(material_kit, dict)
        and isinstance(material_kit.get("jd_snapshot"), str)
        and material_kit["jd_snapshot"].strip()
    ):
        counts["jd"] += 1
    if isinstance(snapshot.get("jd_text"), str) and snapshot["jd_text"].strip():
        counts["jd"] += 1

    resume = snapshot.get("resume")
    if isinstance(resume, dict):
        content = resume.get("content_json")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (TypeError, ValueError):
                content = None
        if isinstance(content, (dict, list)):
            counts["resume"] += _string_leaf_count(content)

    assertions = _assertion_count(snapshot)
    counts["user_assertion"] += assertions

    bundle = snapshot.get("latest_evidence_bundle")
    if isinstance(bundle, dict):
        evidence_items = bundle.get("evidence")
        if not isinstance(evidence_items, list):
            evidence_items = bundle.get("entries")
        if isinstance(evidence_items, list):
            for item in evidence_items:
                if isinstance(item, dict) and item.get("source") in counts:
                    counts[str(item["source"])] += 1
    return counts


def _proposal_array_lengths(stage: str, proposal: dict[str, Any]) -> dict[str, int]:
    if stage == "triage":
        lengths = {
            field: len(proposal.get(field))
            for field in _TRIAGE_ARRAY_FIELDS
            if isinstance(proposal.get(field), list)
        }
        summary = proposal.get("summary")
        if isinstance(summary, dict) and isinstance(summary.get("evidence_refs"), list):
            lengths["summary.evidence_refs"] = len(summary["evidence_refs"])
        return lengths
    return {
        key: len(value)
        for key, value in proposal.items()
        if isinstance(key, str) and isinstance(value, list)
    }


def _proposal_status(stage: str, proposal: dict[str, Any]) -> str:
    if not proposal:
        return "unavailable"
    declared = proposal.get("proposal_status")
    if isinstance(declared, str) and declared:
        return declared
    lengths = _proposal_array_lengths(stage, proposal)
    if stage == "triage" and lengths:
        expected = set(_TRIAGE_ARRAY_FIELDS) | {"summary.evidence_refs"}
        if expected.issubset(lengths) and all(lengths[key] == 0 for key in expected):
            return "safe_empty"
    return "non_empty" if any(lengths.values()) else "empty"


def _browser_stage_a_observation(records: list[dict[str, Any]]) -> dict[str, Any]:
    def matching(kind: str, method: str, pattern: str) -> list[dict[str, Any]]:
        return [
            record
            for record in records
            if record.get("kind") == kind
            and record.get("method") == method
            and isinstance(record.get("url"), str)
            and re.search(pattern, str(record["url"]))
        ]

    pilot_chat = matching("browser_request", "POST", r"/api/chat(?:/stream)?$")
    confirmation = matching(
        "browser_request", "POST", r"/api/chat/confirm(?:/stream)?$"
    )
    confirmation_responses = [
        record
        for record in matching(
            "browser_response", "POST", r"/api/chat/confirm(?:/stream)?$"
        )
        if record.get("response_status") in {200, 201}
    ]
    history = [
        record
        for record in matching(
            "browser_response",
            "GET",
            r"/job-description/versions(?:\?.*)?$",
        )
        if record.get("response_status") == 200
    ]
    details = [
        record
        for record in matching(
            "browser_response",
            "GET",
            r"/job-description/versions/\d+$",
        )
        if record.get("response_status") == 200
    ]
    history_sources = [
        source
        for record in history
        for source in (
            record.get("response_source_kinds")
            if isinstance(record.get("response_source_kinds"), list)
            else []
        )
    ]
    source_kinds = sorted(
        {
            str(source)
            for source in history_sources
            if source in {"ui", "pilot"}
        }
        | {
            str(record.get("response_source_kind"))
            for record in details
            if record.get("response_source_kind") in {"ui", "pilot"}
        }
    )
    pilot_version_ids = [
        int(record["response_jd_version_id"])
        for record in details
        if record.get("response_source_kind") == "pilot"
        and isinstance(record.get("response_jd_version_id"), int)
    ]
    pilot_observed = bool(pilot_chat) and "pilot" in source_kinds
    confirmation_observed = bool(confirmation_responses) and bool(confirmation)
    return {
        "pilot_observed": pilot_observed,
        "confirmation_observed": confirmation_observed,
        "jd_version_id": max(pilot_version_ids) if pilot_version_ids else None,
        "source_kinds": source_kinds,
        "ready": pilot_observed
        and confirmation_observed
        and bool(pilot_version_ids),
    }


def _provider_model(records: list[dict[str, Any]]) -> dict[str, str] | None:
    candidates = {
        (
            str(record.get("provider_type")),
            str(record.get("provider_id")),
            str(record.get("model")),
        )
        for record in records
        if record.get("provider_type") and record.get("provider_id") and record.get("model")
    }
    if len(candidates) != 1:
        return None
    provider_type, provider_id, model = next(iter(candidates))
    return {"provider_type": provider_type, "provider_id": provider_id, "model": model}


def _provider_result(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not records:
        return None
    record = records[-1]
    return {
        "status": record.get("status") if record.get("status") in {"success", "error"} else None,
        "elapsed_ms": record.get("elapsed_ms"),
        "failure_category": record.get("failure_category")
        if isinstance(record.get("failure_category"), str)
        and _SAFE_CATEGORY.fullmatch(record["failure_category"])
        else None,
        "provider_request_id_hash": record.get("provider_request_id_hash", "")
        if isinstance(record.get("provider_request_id_hash"), str)
        and _SAFE_REQUEST_ID_HASH.fullmatch(record["provider_request_id_hash"])
        else "",
    }


def build_stage_record(
    *,
    stage: str,
    snapshot: dict[str, Any] | None,
    proposal: dict[str, Any] | None,
    result_status: str | None,
    provider_records: list[dict[str, Any]],
    provider_results: list[dict[str, Any]],
    browser_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if stage == "jd_pilot":
        observation = _browser_stage_a_observation(browser_records or [])
        return {
            "stage": stage,
            "operation": stage,
            "result_status": "ready" if observation["ready"] else "not_observed",
            "input_fingerprints": [],
            "schema_fingerprints": [],
            "user_assertion_count": 0,
            "evidence_counts": {source: 0 for source in _EVIDENCE_SOURCES},
            "path_types": [],
            "proposal_status": "not_applicable",
            "array_lengths": {},
            "failure_category": None,
            "failure_categories": [],
            "provider_model": None,
            "provider_result": None,
            "provider_request_count": 0,
            **observation,
        }
    safe_snapshot = snapshot if isinstance(snapshot, dict) else {}
    safe_proposal = proposal if isinstance(proposal, dict) else {}
    counts = _evidence_counts(safe_snapshot)
    failures = sorted(
        {
            str(record.get("failure_category"))
            for record in provider_results
            if isinstance(record.get("failure_category"), str)
            and record.get("failure_category")
            and _SAFE_CATEGORY.fullmatch(record["failure_category"])
        }
    )
    return {
        "stage": stage,
        "operation": stage,
        "result_status": result_status or "not_observed",
        "input_fingerprints": sorted(
            {
                str(record["input_fingerprint_sha256"])
                for record in provider_records
                if isinstance(record.get("input_fingerprint_sha256"), str)
                and _SAFE_HASH.fullmatch(record["input_fingerprint_sha256"])
            }
        ),
        "schema_fingerprints": sorted(
            {
                str(record["schema_fingerprint_sha256"])
                for record in provider_records
                if isinstance(record.get("schema_fingerprint_sha256"), str)
                and _SAFE_HASH.fullmatch(record["schema_fingerprint_sha256"])
            }
        ),
        "user_assertion_count": _assertion_count(safe_snapshot),
        "evidence_counts": counts,
        "path_types": [source for source in _EVIDENCE_SOURCES if counts[source] > 0],
        "proposal_status": _proposal_status(stage, safe_proposal),
        "array_lengths": _proposal_array_lengths(stage, safe_proposal),
        "failure_category": failures[-1] if failures else None,
        "failure_categories": failures,
        "provider_model": _provider_model(provider_records),
        "provider_result": _provider_result(provider_results),
        "provider_request_count": len(provider_records),
    }


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _parse_object(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _db_stage_payload(
    db_path: Path,
    stage: str,
    application_id: int,
    jd_version_id: int | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    with sqlite3.connect(db_path) as db:
        if stage == "triage":
            row = db.execute(
                "SELECT source_snapshot_json, proposal_json, status "
                "FROM opportunity_fit_review_stages "
                "WHERE application_id = ? AND stage = 'triage' "
                "AND (? IS NULL OR jd_version_id = ?) "
                "ORDER BY id DESC LIMIT 1",
                (application_id, jd_version_id, jd_version_id),
            ).fetchone()
            if row is None:
                return None, None, None
            return _parse_object(row[0]), _parse_object(row[1]), str(row[2])

        if stage == "interview_preparation":
            row = db.execute(
                "SELECT input_snapshot_json, proposal_json, attempt_status "
                "FROM interview_preparation_proposals "
                "WHERE application_id = ? AND (? IS NULL OR jd_version_id = ?) "
                "ORDER BY id DESC LIMIT 1",
                (application_id, jd_version_id, jd_version_id),
            ).fetchone()
            if row is None:
                return None, None, None
            return _parse_object(row[0]), _parse_object(row[1]), str(row[2])

        if stage == "material_kit":
            row = db.execute(
                "SELECT resume_id, jd_snapshot, content_json, status "
                "FROM application_material_kits "
                "WHERE application_id = ? AND (? IS NULL OR jd_version_id = ?) "
                "ORDER BY id DESC LIMIT 1",
                (application_id, jd_version_id, jd_version_id),
            ).fetchone()
            if row is None:
                return None, None, None
            resume_row = db.execute(
                "SELECT content_json FROM resumes WHERE id = ?", (int(row[0]),)
            ).fetchone()
            resume_content = _parse_object(resume_row[0]) if resume_row else None
            snapshot = {
                "jd": {"text": row[1]} if isinstance(row[1], str) else {},
                "resume": {"content_json": resume_content or {}},
                "user_assertions": [],
            }
            return snapshot, _parse_object(row[2]), str(row[3])

    return None, None, None


def collect_stage_diagnostic(
    *,
    stage: str,
    db_path: Path,
    application_id: int,
    jd_version_id: int | None,
    provider_audit_path: Path,
    operation_audit_path: Path,
    provider_start_index: int = 0,
    operation_start_index: int = 0,
    browser_audit_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    provider_records, provider_end = slice_audit_records(
        _jsonl(provider_audit_path), provider_start_index
    )
    operation_records, operation_end = slice_audit_records(
        _jsonl(operation_audit_path), operation_start_index
    )
    provider_records = [
        record for record in provider_records if record.get("kind") == "provider_request_metadata"
    ]
    provider_results = [
        record
        for record in operation_records
        if record.get("kind") == "provider_request_result"
    ]
    snapshot, proposal, result_status = _db_stage_payload(
        db_path, stage, application_id, jd_version_id
    )
    record = build_stage_record(
        stage=stage,
        snapshot=snapshot,
        proposal=proposal,
        result_status=result_status,
        provider_records=provider_records,
        provider_results=provider_results,
        browser_records=_jsonl(browser_audit_path) if browser_audit_path else None,
    )
    record["audit_window"] = {
        "provider_start_index": provider_start_index,
        "provider_end_index": provider_end,
        "operation_start_index": operation_start_index,
        "operation_end_index": operation_end,
    }
    return record, {
        "provider_end_index": provider_end,
        "operation_end_index": operation_end,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a redacted Application JD stage diagnostic.")
    parser.add_argument("--stage", required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--application-id", type=int, required=True)
    parser.add_argument("--jd-version-id", type=int)
    parser.add_argument("--provider-audit", type=Path, required=True)
    parser.add_argument("--operation-audit", type=Path, required=True)
    parser.add_argument("--provider-start-index", type=int, default=0)
    parser.add_argument("--operation-start-index", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-audit", type=Path)
    args = parser.parse_args()
    record, offsets = collect_stage_diagnostic(
        stage=args.stage,
        db_path=args.db,
        application_id=args.application_id,
        jd_version_id=args.jd_version_id,
        provider_audit_path=args.provider_audit,
        operation_audit_path=args.operation_audit,
        provider_start_index=args.provider_start_index,
        operation_start_index=args.operation_start_index,
        browser_audit_path=args.browser_audit,
    )
    record["audit_offsets"] = offsets
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n")
    print(json.dumps({**record, "audit_offsets": offsets}, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
