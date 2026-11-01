"""New single-target batch; replay all seven immutable predecessors, never resume.

The seventh completed structural review is not a semantic approval. Earlier
consumed failures and their cost remain in the transitive archive chain.
"""
from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import stat
from typing import Any
import zipfile

from scripts.companion_ai_validation import continuation as archive
from scripts.companion_ai_validation import shadow_projection_continuation as old

ADMISSION_SCHEMA = old.ADMISSION_SCHEMA
LINEAGE_KEYS = old.LINEAGE_KEYS
CAMPAIGN_ID = old.CAMPAIGN_ID
CORRECTION_ORDINAL = 7
BUDGET = 20_000_000
RESERVATION = 3_000_000
PRIOR_SETTLED = 429_280
PRIOR_REQUESTS = 58
PRICE_VALID_UNTIL = 1793577600  # 2026-11-02 00:00 UTC, no renewal by replay.
OPERATIONS = ("t01", "t02", "t03", "t04", "t05", "t06")
OPERATIONS_SHA256 = hashlib.sha256(json.dumps(OPERATIONS, separators=(",", ":")).encode()).hexdigest()
PREDECESSOR_SOURCE_SHA = "c8ee98e627ce3e75bc596199509bb9b543557de8"
PREDECESSOR_HELPER_SHA = "13d86116af9c1b41458e695c2bee86a837fb5d12"
PREDECESSOR_RUN_ID = 41032423741
PREDECESSOR_HELPER_RUN_ID = 41022003105
PREDECESSOR_TREE_SHA = "5834c3eab6b2fbd5a871b94129d4cb41810de931"
PREDECESSOR_ARTIFACT_ID = 12968888464
ARCHIVE_SHA256 = "870cda7360f9c473f79620655203d3942f94dcc450c09223534c276ed3e54221"
ARCHIVE_SIZE = 11476
LEDGER_SHA256 = "8d5c0b321758bb9fedc07d08b1fb611d421b2c3e52378b38c86b025861ee357a"
MEMBERS = {
    "summary.json": (16107, "80be54923819ad24f785d3121e5a0ce9190047e4ce2aa5fc8f647cbe18f590be"),
    "budget.jsonl": (23735, LEDGER_SHA256),
    "responses.jsonl": (7676, "40689d9b097b89044a13951006dc237e217c1f2ba532fc3f39d6c7d21affd1e5"),
    "requests.jsonl": (15264, "c2daed061d9a0fb183ea5e4e28bf48794af4c555dfab47040ab602f37e909715"),
}
EVIDENCE_PATH = (Path(__file__).resolve().parents[2] / "experiments/shadow_single_target"
                 / "fixtures/seventh-paid-original-artifact.zip")
ContinuationError = archive.ContinuationError
ALL_HISTORICAL_IDENTITIES = old.ALL_HISTORICAL_IDENTITIES | frozenset((
    PREDECESSOR_SOURCE_SHA, PREDECESSOR_HELPER_SHA,
))


@dataclass(frozen=True)
class Predecessor:
    settled_micro_cny: int = PRIOR_SETTLED
    reserved_micro_cny: int = 0
    request_count: int = PRIOR_REQUESTS
    source_sha: str = PREDECESSOR_SOURCE_SHA
    helper_sha: str = PREDECESSOR_HELPER_SHA
    run_id: int = PREDECESSOR_RUN_ID
    artifact_id: int = PREDECESSOR_ARTIFACT_ID
    archive_sha256: str = ARCHIVE_SHA256
    ledger_sha256: str = LEDGER_SHA256


def _equal(actual: Any, expected: Any) -> None:
    archive._demand(json.dumps(actual, sort_keys=True, allow_nan=False)
                    == json.dumps(expected, sort_keys=True, allow_nan=False),
                    "PREDECESSOR_EVIDENCE_INVALID")


def _state(lineage: dict[str, Any], count: int, settled: int, reserved: int,
           *, closed: bool = False) -> dict[str, Any]:
    return {
        **lineage, "schema": ADMISSION_SCHEMA, "source_sha": PREDECESSOR_SOURCE_SHA,
        "request_count": count, "settled_micro_cny": settled,
        "reserved_micro_cny": reserved, "halted": closed,
        "batch_request_count": count, "lifetime_request_count": 52 + count,
        "batch_settled_micro_cny": settled - 419388, "batch_reserved_micro_cny": reserved,
        "lifetime_settled_micro_cny": settled, "lifetime_reserved_micro_cny": reserved,
        "lifetime_liability_micro_cny": settled + reserved,
        "lifetime_remaining_micro_cny": BUDGET - settled - reserved,
    }


def _verify_archive(raw: bytes, lineage: dict[str, Any]) -> Predecessor:
    from scripts.companion_ai_validation import broker as gate
    from scripts.companion_ai_validation import shadow_projection_driver as driver

    try:
        _equal((len(raw), hashlib.sha256(raw).hexdigest()), (ARCHIVE_SIZE, ARCHIVE_SHA256))
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos = z.infolist()
            _equal((len(infos), sorted(i.filename for i in infos)), (4, sorted(MEMBERS)))
            members = {}
            for info in infos:
                size, digest = MEMBERS[info.filename]
                archive._demand(stat.S_ISREG(info.external_attr >> 16) and not info.is_dir()
                                and not info.flag_bits & 1 and info.file_size == size
                                and info.compress_type == zipfile.ZIP_DEFLATED)
                with z.open(info) as stream:
                    value = stream.read(size + 1)
                _equal((len(value), hashlib.sha256(value).hexdigest()), (size, digest))
                members[info.filename] = value
        def lines(name: str) -> list[dict[str, Any]]:
            raw_lines = members[name]
            archive._demand(raw_lines.endswith(b"\n") and b"\r" not in raw_lines)
            return [gate.decode(row) for row in raw_lines.splitlines()]
        requests, responses, ledger = (lines(name) for name in
                                      ("requests.jsonl", "responses.jsonl", "budget.jsonl"))
        _equal((len(requests), len(responses)), (6, 6))
        summary = gate.decode(members["summary.json"])
        settled = 419388
        expected_rows = [{**_state(lineage, 0, settled, 0), "event": "created"}]
        states = []
        for ordinal, (case, request, response) in enumerate(
                zip(driver.cases(), requests, responses, strict=True), 1):
            operation = case["id"]
            body = driver.frozen_request(operation)
            _equal(request, {"operation": operation, "ordinal": ordinal,
                             "body_sha256": hashlib.sha256(body).hexdigest(),
                             "raw_body": body.decode()})
            reply = base64.b64decode(response["raw_base64"], validate=True)
            _equal(response, {"operation": operation, "status": 200,
                              "body_sha256": hashlib.sha256(reply).hexdigest(),
                              "raw_base64": response["raw_base64"]})
            envelope = gate.decode(reply)
            cost, prompt, completion = gate.usage_cost(envelope, shadow_projection=True)
            review = driver.validate(gate.response_content(envelope), case)
            states.append({"operation": operation, "status": "structure_valid_semantics_ungraded",
                           "review": review})
            archive._demand(settled + RESERVATION <= BUDGET)
            expected_rows.append({**_state(lineage, ordinal, settled, RESERVATION),
                                  "event": "reserved", "operation": operation,
                                  "body_sha256": request["body_sha256"]})
            settled += cost
            expected_rows.append({**_state(lineage, ordinal, settled, 0), "event": "settled",
                                  "operation": operation, "cost_micro_cny": cost,
                                  "prompt_tokens": prompt, "completion_tokens": completion})
            expected_rows.append({**_state(lineage, ordinal, settled, 0), "event": "stage_complete",
                                  "operation": operation, "calls": 1})
        _equal(settled, PRIOR_SETTLED)
        expected_rows.append({**_state(lineage, 6, settled, 0, closed=True), "event": "closed"})
        _equal(ledger, expected_rows)
        _equal(summary["schema"], ADMISSION_SCHEMA)
        _equal(summary["budget"], {**_state(lineage, 6, settled, 0), "stages_complete": 6,
                                   "accounting_basis": "conservative_usage_not_provider_invoice"})
        _equal(summary["matrix"], {"passed": True, "error_code": "",
                                   "pass_scope": "structure_only_not_semantic_correctness",
                                   "product_modified": False, "cases": states})
        experiment = summary["experiment"]
        _equal([experiment[key] for key in ("planned", "attempted", "structurally_valid",
                                            "not_attempted", "incomplete", "semantic_quality_established")],
               [6, 6, 6, 0, False, False])
        _equal(experiment["projection_mappings"], driver.projection_mappings())
        _equal(experiment["requests_sha256"], driver.REQUEST_HASHES)
        return Predecessor()
    except (ValueError, TypeError, KeyError, IndexError, StopIteration, OSError,
            zipfile.BadZipFile, RuntimeError):
        raise ContinuationError("PREDECESSOR_EVIDENCE_INVALID") from None


def verified_predecessor() -> Predecessor:
    # No cache: all six prior archives, including failed paid attempts, are
    # re-read before the seventh archive. There is no caller-supplied balance.
    previous = old.verified_predecessor()
    _equal((previous.request_count, previous.settled_micro_cny, previous.reserved_micro_cny),
           (52, 419388, 0))
    lineage = old.lineage_fields()
    return _verify_archive(archive._read_evidence(EVIDENCE_PATH, ARCHIVE_SIZE), lineage)


def lineage_fields() -> dict[str, Any]:
    previous = verified_predecessor()
    return {
        "campaign_id": CAMPAIGN_ID, "correction_ordinal": CORRECTION_ORDINAL,
        "predecessor_run_id": previous.run_id, "predecessor_artifact_id": previous.artifact_id,
        "predecessor_source_sha": previous.source_sha, "predecessor_helper_sha": previous.helper_sha,
        "predecessor_archive_sha256": previous.archive_sha256,
        "predecessor_ledger_sha256": previous.ledger_sha256,
        "prior_settled_micro_cny": previous.settled_micro_cny,
        "prior_reserved_micro_cny": previous.reserved_micro_cny,
        "prior_request_count": previous.request_count, "budget_micro_cny": BUDGET,
        "operations_sha256": OPERATIONS_SHA256, "price_valid_until": PRICE_VALID_UNTIL,
    }


def validate_lineage(record: Mapping[str, Any]) -> None:
    archive._demand(isinstance(record, Mapping) and archive._exact(record, lineage_fields()),
                    "CONTINUATION_LINEAGE_MISMATCH")
