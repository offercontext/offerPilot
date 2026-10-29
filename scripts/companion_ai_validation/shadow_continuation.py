"""Fixed six-review candidate lineage; four immutable historical archives.

Offline verification only. The candidate deadline is not execution authority.
The fourth archive genuinely lacks requests.jsonl; no payload is reconstructed.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.companion_ai_validation import continuation as old

ADMISSION_SCHEMA = old.ADMISSION_SCHEMA
LINEAGE_KEYS = old.LINEAGE_KEYS
CAMPAIGN_ID = old.CAMPAIGN_ID
CORRECTION_ORDINAL = 4
BUDGET = old.BUDGET
RESERVATION = old.RESERVATION
PRIOR_SETTLED = 411_934
PRIOR_REQUESTS = 48
PRICE_VALID_UNTIL = int(datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp())
OPERATIONS = ("s01", "s02", "s03", "s04", "s05", "s06")
OPERATIONS_SHA256 = hashlib.sha256(json.dumps(
    OPERATIONS, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
).encode()).hexdigest()
PREDECESSOR_SOURCE_SHA = "ca4248638a34de8300502d08b08c2e8a66875cb0"
# Identity metadata inherited from the verified historical run checkpoint.
PREDECESSOR_HELPER_SHA = "0987c91300c668c0b4947d30875ee3ff78e92d2c"
PREDECESSOR_RUN_ID = 40184557896
PREDECESSOR_HELPER_RUN_ID = 40177612429
PREDECESSOR_TREE_SHA = "096b232a194d4cf1815f975918e24c8308aa69d5"
PREDECESSOR_ARTIFACT_ID = 12596006678
ARCHIVE_SHA256 = "34dfbd00993f40d4a3a49ff7b1ebe910a3d8b6ecd4d1467f268da838d9d6610a"
ARCHIVE_SIZE = 18964
LEDGER_SHA256 = "0e1aa9e2f8ede4fd65199011b299f459a69bc1b1116e8d367fae19c2471b852c"
MEMBERS = {
    "summary.json": (45002, "1a58180fbf369026c88b3df3fa7c42e1f6aeec40e0b1c047a0c7e995a29991bb"),
    "responses.jsonl": (33778, "aa0ff2c51e5e33bcd51fbb90b97ff411a21cfe0327669a82b94457eb0838e6a0"),
    "budget.jsonl": (45947, LEDGER_SHA256),
}
EVIDENCE_PATH = Path(__file__).with_name("evidence") / "predecessor-fourth-12596006678.zip"
ContinuationError = old.ContinuationError
ALL_HISTORICAL_IDENTITIES = frozenset((
    old.ORIGINAL_SOURCE_SHA, old.ORIGINAL_HELPER_SHA,
    old.SECOND_SOURCE_SHA, old.SECOND_HELPER_SHA,
    old.PREDECESSOR_SOURCE_SHA, old.PREDECESSOR_HELPER_SHA,
    PREDECESSOR_SOURCE_SHA, PREDECESSOR_HELPER_SHA,
))


def _state(count: int, settled: int, reserved: int, *, closed: bool = False) -> dict[str, Any]:
    return {
        **old.lineage_fields(), "schema": 2, "source_sha": PREDECESSOR_SOURCE_SHA,
        "request_count": count, "settled_micro_cny": settled,
        "reserved_micro_cny": reserved, "halted": closed,
        "batch_request_count": count, "lifetime_request_count": old.PRIOR_REQUESTS + count,
        "batch_settled_micro_cny": settled - old.PRIOR_SETTLED,
        "batch_reserved_micro_cny": reserved, "lifetime_settled_micro_cny": settled,
        "lifetime_reserved_micro_cny": reserved, "lifetime_liability_micro_cny": settled + reserved,
        "lifetime_remaining_micro_cny": BUDGET - settled - reserved,
    }


def _replay_ledger(raw: bytes) -> dict[str, Any]:
    rows = old._lines(raw)
    state_keys = set(_state(0, old.PRIOR_SETTLED, 0))
    old._demand(len(rows) == 2 + 3 * len(old.OPERATIONS))
    old._demand(set(rows[0]) == state_keys | {"event"}
                and old._exact(rows[0], {**_state(0, old.PRIOR_SETTLED, 0), "event": "created"}))
    settled = old.PRIOR_SETTLED
    for index, operation in enumerate(old.OPERATIONS):
        reserve, settlement, complete = rows[1 + index * 3:4 + index * 3]
        old._demand(set(reserve) == state_keys | {"event", "operation", "body_sha256"}
                    and old._exact(reserve, {**_state(index + 1, settled, RESERVATION),
                                            "event": "reserved", "operation": operation})
                    and isinstance(reserve["body_sha256"], str)
                    and bool(old._HASH.fullmatch(reserve["body_sha256"]))
                    and settled + RESERVATION <= BUDGET)
        old._demand(set(settlement) == state_keys | {
            "event", "operation", "cost_micro_cny", "prompt_tokens", "completion_tokens"})
        prompt, completion, cost = (settlement.get(key) for key in
                                    ("prompt_tokens", "completion_tokens", "cost_micro_cny"))
        old._demand(type(prompt) is int and 0 <= prompt <= 1_048_576
                    and type(completion) is int and 0 <= completion <= 512
                    and prompt + completion <= 1_048_576
                    and type(cost) is int and cost == prompt * 2 + completion * 8
                    and 0 <= cost <= RESERVATION)
        assert isinstance(cost, int)
        settled += cost
        old._demand(old._exact(settlement, {**_state(index + 1, settled, 0),
                                           "event": "settled", "operation": operation}))
        old._demand(set(complete) == state_keys | {"event", "operation", "calls"}
                    and old._exact(complete, {**_state(index + 1, settled, 0),
                                              "event": "stage_complete", "operation": operation,
                                              "calls": 1}))
    old._demand(settled == PRIOR_SETTLED and old.PRIOR_REQUESTS + len(old.OPERATIONS) == PRIOR_REQUESTS)
    old._demand(set(rows[-1]) == state_keys | {"event"}
                and old._exact(rows[-1], {**_state(len(old.OPERATIONS), settled, 0, closed=True),
                                         "event": "closed"}))
    return _state(len(old.OPERATIONS), settled, 0)


def _verify_archive(raw: bytes) -> dict[str, Any]:
    members = old._archive_members(raw, size=ARCHIVE_SIZE, digest=ARCHIVE_SHA256, expected=MEMBERS)
    state = _replay_ledger(members["budget.jsonl"])
    summary = old._json(members["summary.json"])
    responses = old._lines(members["responses.jsonl"])
    old._demand(set(summary) == {"schema", "matrix", "budget"}
                and type(summary["schema"]) is int and summary["schema"] == 2)
    expected_budget = {**state, "stages_complete": len(old.OPERATIONS),
                       "accounting_basis": "conservative_usage_not_provider_invoice"}
    budget, matrix = summary["budget"], summary["matrix"]
    old._demand(isinstance(budget, dict) and set(budget) == set(expected_budget)
                and old._exact(budget, expected_budget) and isinstance(matrix, dict)
                and old._exact(matrix, {"passed": True, "pass_scope": "mechanism_only",
                    "schema_version": 1, "synthetic_only": True, "transport": "uvicorn:/api/chat",
                    "ordinary_count": len(old.OPERATIONS), "tone_review_required": True})
                and len(responses) == len(old.OPERATIONS) and matrix.get("responses") == responses)
    for operation, response in zip(old.OPERATIONS, responses, strict=True):
        old._demand(old._exact(response, {"operation": operation, "ordinary_turn_read_only": True,
                                         "source_verified": True})
                    and isinstance(response.get("assistant_text"), str)
                    and 0 < len(response["assistant_text"].encode()) <= 8192)
    return state


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
    state = _verify_archive(old._read_evidence(EVIDENCE_PATH, ARCHIVE_SIZE))
    old._demand(old._exact(state, {
        "prior_settled_micro_cny": previous.settled_micro_cny,
        "prior_reserved_micro_cny": previous.reserved_micro_cny,
        "prior_request_count": previous.request_count,
    }))
    return Predecessor(settled_micro_cny=state["lifetime_settled_micro_cny"],
                       reserved_micro_cny=state["lifetime_reserved_micro_cny"],
                       request_count=state["lifetime_request_count"])


def lineage_fields() -> dict[str, Any]:
    previous = verified_predecessor()
    # Unknown historical identities must never produce executable admission.
    old._demand(previous.run_id > 0 and len(previous.helper_sha) == 40,
                "PREDECESSOR_IDENTITY_UNAVAILABLE")
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
    old._demand(isinstance(record, Mapping) and old._exact(record, lineage_fields()),
                "CONTINUATION_LINEAGE_MISMATCH")
