"""New product-experience batch after eight immutable paid predecessors.

The eighth stopped at a preregistered semantic disagreement after three calls.
Its failed outcome, two completed stages, and costs are preserved, never resumed.
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
from scripts.companion_ai_validation import shadow_single_target_continuation as old

ADMISSION_SCHEMA = old.ADMISSION_SCHEMA
LINEAGE_KEYS = old.LINEAGE_KEYS
CAMPAIGN_ID = old.CAMPAIGN_ID
CORRECTION_ORDINAL = 8
BUDGET = 20_000_000
RESERVATION = 3_000_000
PRIOR_SETTLED = 458_256
PRIOR_REQUESTS = 61
PRICE_VALID_UNTIL = 1793664000  # 2026-11-03 00:00 UTC, separately authorized.
OUTPUT_TOKENS = 512
OPERATIONS = tuple(f"x{i:02d}" for i in range(1, 13))
OPERATIONS_SHA256 = hashlib.sha256(json.dumps(OPERATIONS, separators=(",", ":")).encode()).hexdigest()
PREDECESSOR_SOURCE_SHA = "e3a2c23701d00e642d9fad24a34a928905615ee7"
PREDECESSOR_HELPER_SHA = "e3d8bbd32a471f0eb88681111e9c4fcd414d99b0"
PREDECESSOR_RUN_ID = 41183011714
PREDECESSOR_HELPER_RUN_ID = 41181177860
PREDECESSOR_TREE_SHA = "d2444ee06533b3cc51e3e966862e272f89c30cd4"
PREDECESSOR_ARTIFACT_ID = 13036483703
ARCHIVE_SHA256 = "28c2725466286564200a6634ecccc07154147ba93ed76688a242341295f2a696"
ARCHIVE_SIZE = 16965
LEDGER_SHA256 = "914222cabeb78a8f14ececa722c417224fb14fa30136d76e3b9be526cfde6522"
MEMBERS = {
    "summary.json": (11432, "5667eddec306359e06631498ba669075201b048abecb917a289e0fba781aad2d"),
    "budget.jsonl": (11840, LEDGER_SHA256),
    "responses.jsonl": (9786, "52e0ae9373e762e92add1a9fe2b66e3605c25a35bcb96907637c705cc41fa3b7"),
    "requests.jsonl": (44280, "efff17fd801d5e6e0940e28641699c0b7f232a146bd874d2e60266ac2069be3e"),
}
EVIDENCE_PATH = (Path(__file__).resolve().parents[2] / "experiments/companion_experience"
                 / "fixtures/eighth-paid-original-artifact.zip")
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
        "batch_request_count": count, "lifetime_request_count": 58 + count,
        "batch_settled_micro_cny": settled - 429280, "batch_reserved_micro_cny": reserved,
        "lifetime_settled_micro_cny": settled, "lifetime_reserved_micro_cny": reserved,
        "lifetime_liability_micro_cny": settled + reserved,
        "lifetime_remaining_micro_cny": BUDGET - settled - reserved,
    }


def _verify_archive(raw: bytes, lineage: dict[str, Any]) -> Predecessor:
    from scripts.companion_ai_validation import broker as gate
    from scripts.companion_ai_validation import shadow_single_target_driver as driver

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
        _equal((len(requests), len(responses)), (3, 3))
        summary = gate.decode(members["summary.json"])
        settled = 429280
        expected_rows = [{**_state(lineage, 0, settled, 0), "event": "created"}]
        states = []
        for ordinal, (case, request, response) in enumerate(
                zip(driver.cases()[:3], requests, responses, strict=True), 1):
            operation = case["operation"]
            body = driver.frozen_request(operation)
            _equal(request, {"operation": operation, "ordinal": ordinal,
                             "body_sha256": hashlib.sha256(body).hexdigest(),
                             "raw_body": body.decode()})
            reply = base64.b64decode(response["raw_base64"], validate=True)
            _equal(response, {"operation": operation, "status": 200,
                              "body_sha256": hashlib.sha256(reply).hexdigest(),
                              "raw_base64": response["raw_base64"]})
            envelope = gate.decode(reply)
            cost, prompt, completion = gate.usage_cost(envelope, shadow_single_target=True)
            review = driver.validate(gate.response_content(envelope), case)
            matched = driver.semantic_matches(operation, review["reviewer_claims"])
            _equal(matched, ordinal < 3)
            state = {"operation": operation, "status": "structure_valid_semantics_ungraded",
                     "review": review}
            if not matched:
                state.update(status="structure_valid_semantic_disagreement",
                             error_code="semantic_disagreement")
            states.append(state)
            archive._demand(settled + RESERVATION <= BUDGET)
            expected_rows.append({**_state(lineage, ordinal, settled, RESERVATION),
                                  "event": "reserved", "operation": operation,
                                  "body_sha256": request["body_sha256"]})
            settled += cost
            expected_rows.append({**_state(lineage, ordinal, settled, 0), "event": "settled",
                                  "operation": operation, "cost_micro_cny": cost,
                                  "prompt_tokens": prompt, "completion_tokens": completion})
            if matched:
                expected_rows.append({**_state(lineage, ordinal, settled, 0), "event": "stage_complete",
                                      "operation": operation, "calls": 1})
        _equal(settled, PRIOR_SETTLED)
        expected_rows.append({**_state(lineage, 3, settled, 0, closed=True), "event": "closed"})
        _equal(ledger, expected_rows)
        _equal(summary["schema"], ADMISSION_SCHEMA)
        _equal(summary["budget"], {**_state(lineage, 3, settled, 0, closed=True), "stages_complete": 2,
                                   "accounting_basis": "conservative_usage_not_provider_invoice"})
        states.extend({"operation": case["operation"], "status": "not_attempted"}
                      for case in driver.cases()[3:])
        _equal(summary["matrix"], {"passed": False, "error_code": "semantic_disagreement",
                                   "pass_scope": "structure_only_not_semantic_correctness",
                                   "product_modified": False, "cases": states})
        _equal(summary["experiment"], {
            "protocol": driver.VERSION,
            "semantic_preregistration_sha256": driver.PREREGISTRATION_SHA256,
            "planned": 6, "attempted": 3, "incomplete": True, "not_attempted": 3,
            "structurally_valid": 3, "semantic_review_required": True,
            "semantic_quality_established": False, "cases_sha256": driver.CASES_SHA256,
            "requests_sha256": driver.REQUEST_HASHES, "model": gate.MODEL,
            "output_limit": 4096, "price_valid_until": old.PRICE_VALID_UNTIL,
            "input_micro_cny_per_token": 2, "output_micro_cny_per_token": 8,
            "original_drafts_unchanged": True,
        })
        return Predecessor()
    except (ValueError, TypeError, KeyError, IndexError, StopIteration, OSError,
            zipfile.BadZipFile, RuntimeError):
        raise ContinuationError("PREDECESSOR_EVIDENCE_INVALID") from None


def verified_predecessor() -> Predecessor:
    # No cache: all seven prior archives, including failed paid attempts, are
    # re-read before the eighth archive. There is no caller-supplied balance.
    previous = old.verified_predecessor()
    _equal((previous.request_count, previous.settled_micro_cny, previous.reserved_micro_cny),
           (58, 429280, 0))
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
