"""Quote-v2 continuation of five immutable batches, including a settled failure.

The fifth batch failed its first reference span, never completed a stage and
never sent the other five requests. This verifier preserves that failure and
its original bytes; a settled failure is not a successful semantic result.
"""
from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.companion_ai_validation import continuation as archive
from scripts.companion_ai_validation import shadow_continuation as old

ADMISSION_SCHEMA = old.ADMISSION_SCHEMA
LINEAGE_KEYS = old.LINEAGE_KEYS
CAMPAIGN_ID = old.CAMPAIGN_ID
CORRECTION_ORDINAL = 5
BUDGET = old.BUDGET
RESERVATION = old.RESERVATION
PRIOR_SETTLED = 414_284
PRIOR_REQUESTS = 49
PRICE_VALID_UNTIL = int(datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp())
HISTORICAL_PRICE_VALID_UNTIL = 1793404800
OPERATIONS = old.OPERATIONS
OPERATIONS_SHA256 = old.OPERATIONS_SHA256
PREDECESSOR_SOURCE_SHA = "2298348ce0e884a08a7cd7199b42425884310ebd"
PREDECESSOR_HELPER_SHA = "abf7b99d1a8cbab10c09d43f7cb842bf3e5d200b"
PREDECESSOR_RUN_ID = 40812959831
PREDECESSOR_HELPER_RUN_ID = 40811062570
PREDECESSOR_TREE_SHA = "373c5fd342a112577ca30484b8ff55510bc9f9f4"
PREDECESSOR_ARTIFACT_ID = 12871274468
ARCHIVE_SHA256 = "4044acb685abf79c20b63ed8c505e71e10691f58e799684b78c70bb9831ea6db"
ARCHIVE_SIZE = 5215
LEDGER_SHA256 = "c04db996ef834da0d8a750bd9f7d99f12957769600abfe275ba1466dc0a65140"
MEMBERS = {
    "summary.json": (2377, "0658c74cc5b81357a76852fe6b3475faab6b761ecaeffc13c27bc50b250d5a9e"),
    "responses.jsonl": (1494, "d1db1d82692322273af08f2321831ce371508d5f8621034439917ddcf65e7234"),
    "budget.jsonl": (4659, LEDGER_SHA256),
    "requests.jsonl": (2954, "830b5da065b94704d1fdf6c874b1e89594111f33606d4482ea50c4da95dbecd7"),
}
EVIDENCE_PATH = (Path(__file__).resolve().parents[2] / "experiments/shadow_quote_v2/fixtures"
                 / "paid-original-artifact.zip")
REQUEST_HASHES = {
    "s01": "c41fcc6094892a4fc0531ac99e43bcd5845cb79c51424f90a87af1e10e4100e0",
    "s02": "8a1cb62a6b0eeb71800a7301ebf38ba95f6a63e3b085cb6cc489b29fb32f74ec",
    "s03": "bdf3610e090a9defa725570a9528b849139c49d6b1a436fdf25896452d0e78b2",
    "s04": "25d7c2475fd86d2f8175d637eb5e82085520c785a9f5a92104ccc02224d84f42",
    "s05": "df2660209f2e54138a27a467d44ac8d0826798c8963a47c663914955328b0ec8",
    "s06": "8dadb05cb62fd5e14f766c074cdca7ec63be641a49794ffb153bc67840cf02d4",
}
RESPONSE_SHA256 = "91678126eb9d71037bccfdbbad462ad5062e6401ba97768a04511a61d6f2075b"
ContinuationError = archive.ContinuationError
ALL_HISTORICAL_IDENTITIES = old.ALL_HISTORICAL_IDENTITIES | frozenset((
    PREDECESSOR_SOURCE_SHA, PREDECESSOR_HELPER_SHA,
))


def _equal(value: Any, expected: Any) -> None:
    """Exact keys and nested scalar types, including false versus zero."""
    archive._demand(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
                    == json.dumps(expected, sort_keys=True, ensure_ascii=False, allow_nan=False))


def _historical_lineage() -> dict[str, Any]:
    # The historical quote cannot be extended by changing this candidate's quote.
    return {**old.lineage_fields(), "price_valid_until": HISTORICAL_PRICE_VALID_UNTIL}


def _state(count: int, settled: int, reserved: int, *, lineage: Mapping[str, Any],
           closed: bool = False) -> dict[str, Any]:
    return {
        **lineage, "schema": 2, "source_sha": PREDECESSOR_SOURCE_SHA,
        "request_count": count, "settled_micro_cny": settled,
        "reserved_micro_cny": reserved, "halted": closed,
        "batch_request_count": count, "lifetime_request_count": old.PRIOR_REQUESTS + count,
        "batch_settled_micro_cny": settled - old.PRIOR_SETTLED,
        "batch_reserved_micro_cny": reserved, "lifetime_settled_micro_cny": settled,
        "lifetime_reserved_micro_cny": reserved, "lifetime_liability_micro_cny": settled + reserved,
        "lifetime_remaining_micro_cny": BUDGET - settled - reserved,
    }


def _replay_ledger(raw: bytes) -> dict[str, Any]:
    rows = archive._lines(raw)
    historical = _historical_lineage()
    _equal(rows, [
        {**_state(0, old.PRIOR_SETTLED, 0, lineage=historical), "event": "created"},
        {**_state(1, old.PRIOR_SETTLED, RESERVATION, lineage=historical), "event": "reserved", "operation": "s01",
         "body_sha256": REQUEST_HASHES["s01"]},
        {**_state(1, PRIOR_SETTLED, 0, lineage=historical), "event": "settled", "operation": "s01",
         "cost_micro_cny": 2350, "prompt_tokens": 587, "completion_tokens": 147},
        {**_state(1, PRIOR_SETTLED, 0, closed=True, lineage=historical), "event": "closed"},
    ])
    archive._demand(587 * 2 + 147 * 8 == PRIOR_SETTLED - old.PRIOR_SETTLED
                    and old.PRIOR_REQUESTS + 1 == PRIOR_REQUESTS
                    and old.PRIOR_SETTLED + RESERVATION <= BUDGET)
    return _state(1, PRIOR_SETTLED, 0, closed=True, lineage=historical)


def _verify_members(members: Mapping[str, bytes]) -> dict[str, Any]:
    """Replay relationships independently of ZIP/member hash checks."""
    archive._demand(set(members) == set(MEMBERS))
    state = _replay_ledger(members["budget.jsonl"])
    summary = archive._json(members["summary.json"])
    archive._demand(set(summary) == {"schema", "matrix", "budget", "experiment"})
    _equal(summary["schema"], 2)
    _equal(summary["budget"], {**state, "stages_complete": 0,
                               "accounting_basis": "conservative_usage_not_provider_invoice"})
    _equal(summary["matrix"], {
        "passed": False, "error_code": "shadow_reference_span",
        "pass_scope": "structure_only_not_semantic_correctness", "product_modified": False,
        "cases": [{"operation": "s01", "status": "failed", "error_code": "shadow_reference_span"}]
        + [{"operation": op, "status": "not_attempted"} for op in OPERATIONS[1:]],
    })
    _equal(summary["experiment"], {
        "cases_sha256": "501cd8ac4b49b6a963a6829be487ae0f3e46dd1f77086b1a2bdcfa2cca055b30",
        "requests_sha256": REQUEST_HASHES, "model": "deepseek-flash", "output_limit": 768,
        "price_valid_until": HISTORICAL_PRICE_VALID_UNTIL,
        "input_micro_cny_per_token": 2, "output_micro_cny_per_token": 8,
        "original_drafts_unchanged": True,
    })
    requests = archive._lines(members["requests.jsonl"])
    archive._demand(len(requests) == 1 and isinstance(requests[0].get("raw_body"), str))
    request = requests[0]
    _equal(request, {"operation": "s01", "ordinal": 1, "body_sha256": REQUEST_HASHES["s01"],
                     "raw_body": request["raw_body"]})
    archive._demand(hashlib.sha256(request["raw_body"].encode()).hexdigest() == REQUEST_HASHES["s01"])
    payload = archive._json(request["raw_body"].encode())
    sources = archive._json(payload["messages"][1]["content"].encode())
    responses = archive._lines(members["responses.jsonl"])
    archive._demand(len(responses) == 1 and isinstance(responses[0].get("raw_base64"), str))
    response = responses[0]
    _equal(response, {"operation": "s01", "status": 200, "raw_base64": response["raw_base64"],
                      "body_sha256": RESPONSE_SHA256})
    try:
        response_raw = base64.b64decode(response["raw_base64"], validate=True)
    except (ValueError, binascii.Error):
        raise ContinuationError("PREDECESSOR_EVIDENCE_INVALID") from None
    archive._demand(hashlib.sha256(response_raw).hexdigest() == RESPONSE_SHA256)
    envelope = archive._json(response_raw)
    _equal(envelope["model"], "deepseek-flash")
    _equal(envelope["usage"], {
        "prompt_tokens": 587, "completion_tokens": 147, "total_tokens": 734,
        "prompt_tokens_details": {"cached_tokens": 0},
        "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 587,
    })
    archive._demand(len(envelope["choices"]) == 1)
    choice = envelope["choices"][0]
    _equal(choice["finish_reason"], "stop")
    content = archive._json(choice["message"]["content"].encode())
    _equal(content["target"], sources["target"])
    archive._demand(len(content["evidence"]) == 1)
    # Preserve the actual failure: a nonempty verbatim quote with an empty span.
    _equal(content["evidence"][0], {"source_id": "user", "start": 0, "end": 0,
                                    "quote": sources["sources"][0]["text"]})
    return state


def _verify_archive(raw: bytes) -> dict[str, Any]:
    return _verify_members(archive._archive_members(
        raw, size=ARCHIVE_SIZE, digest=ARCHIVE_SHA256, expected=MEMBERS))


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


def verified_predecessor() -> Predecessor:
    previous = old.verified_predecessor()
    state = _verify_archive(archive._read_evidence(EVIDENCE_PATH, ARCHIVE_SIZE))
    _equal({key: state[key] for key in (
        "prior_settled_micro_cny", "prior_reserved_micro_cny", "prior_request_count")}, {
        "prior_settled_micro_cny": previous.settled_micro_cny,
        "prior_reserved_micro_cny": previous.reserved_micro_cny,
        "prior_request_count": previous.request_count,
    })
    return Predecessor(settled_micro_cny=state["lifetime_settled_micro_cny"],
                       reserved_micro_cny=state["lifetime_reserved_micro_cny"],
                       request_count=state["lifetime_request_count"])


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
