"""One fixed fourth batch, linked to three immutable synthetic predecessor archives.

Standard library only. No network, credential, caller-selected balance, URL or
predecessor. Every public verification re-reads all three fixed, hash-pinned archives.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import stat
from typing import Any
import zipfile

ADMISSION_SCHEMA = 2
CAMPAIGN_ID = "one-shot-20261021"
CORRECTION_ORDINAL = 3
# Original schema-1 evidence is immutable; these identities remain explicit for
# preflight's three-run history check, not caller-selected continuation inputs.
ORIGINAL_RUN_ID = 39736858377
ORIGINAL_HELPER_RUN_ID = 39699222167
ORIGINAL_ARTIFACT_ID = 12408325852
ORIGINAL_SOURCE_SHA = "6329657d841b1bc4d8e3a3cdb7618df92c5a140a"
ORIGINAL_HELPER_SHA = "3f139ada435b2398e52b65162208a8cbb818b8ef"
ORIGINAL_TREE_SHA = "5ba642bbdd2a8d714764a43a448752cd92b2b689"
ORIGINAL_ARCHIVE_SHA256 = "86bbef7eee6db958f70f59695f99e15179c458a4f0def363b1742b7bfb246cf7"
ORIGINAL_LEDGER_SHA256 = "8cc6ff556286f3b158925d007e5c44f24c58a04d8a85702911cd39a83e405074"
ORIGINAL_ARCHIVE_SIZE = 17499
ORIGINAL_MEMBERS = {
    "budget.jsonl": (10791, ORIGINAL_LEDGER_SHA256),
    "summary.json": (51043, "3efaaff13bb040c4337acf7a4dac4b80025062c6ecf50aaf3669b206810c6995"),
    "responses.jsonl": (41048, "2c8b58af851fde4eac35503e52d65960039d2a15d21e081cc9f1346c9ea10d93"),
}
ORIGINAL_EVIDENCE_PATH = Path(__file__).with_name("evidence") / "predecessor-39736858377.zip"
ORIGINAL_SETTLED = 103_154
ORIGINAL_REQUESTS = 12
SECOND_RUN_ID = 39746585328
SECOND_HELPER_RUN_ID = 39744681282
SECOND_ARTIFACT_ID = 12412545645
SECOND_SOURCE_SHA = "e6cbb73e02863d79332855fadfe75f0e699312ae"
SECOND_HELPER_SHA = "64bcd7ab737650415b1d987b1e56681aca6f9fcf"
SECOND_TREE_SHA = "e595f61eafc7c3cdd4011c19016892f3a93db15e"
SECOND_ARCHIVE_SHA256 = "483e889eb6788f827857fde681ca4e42f627328af537b5113ecbce109ed53dab"
SECOND_LEDGER_SHA256 = "f005b4dc89bc2ac46d752a310399bd39c7104da757992ab3a8e0ab1357f8756c"
SECOND_ARCHIVE_SIZE = 18554
SECOND_MEMBERS = {
    "budget.jsonl": (45917, SECOND_LEDGER_SHA256),
    "summary.json": (52171, "ca01395f3fbdfda1a7d7e4b52cc85f34d549c3f9193aba45cddf7271bef300f5"),
    "responses.jsonl": (41194, "0ca8a036d4706514ea3c1f95e04e00dff3831ed41a76024883baabba2cb42c88"),
}
SECOND_EVIDENCE_PATH = Path(__file__).with_name("evidence") / "predecessor-39746585328.zip"
PREDECESSOR_RUN_ID = 40060633208
PREDECESSOR_HELPER_RUN_ID = 40059926016
PREDECESSOR_ARTIFACT_ID = 12547790764
PREDECESSOR_SOURCE_SHA = "5b4d7a75ff40a3f8f7cbee9b3e6300bfa6896a10"
PREDECESSOR_HELPER_SHA = "744fb0f6a716cce3e75e14eb8bf9dfe585aff4c2"
PREDECESSOR_TREE_SHA = "08a19a7f84868590b8c9a49d288356afac134d11"
ARCHIVE_SHA256 = "e12dc5ebe12183bf5f673cf941100b0be0eb6902f2fa90abf45d881bb613917d"
LEDGER_SHA256 = "db3e83e8543468fffd01931ade74bd137a24f0af691e048054a4296c8b98632b"
ARCHIVE_SIZE = 17747
MEMBERS = {
    "budget.jsonl": (45917, LEDGER_SHA256),
    "summary.json": (51532, "a5d4db3379b91755dbba4ef812e248f3ca47da2d7e74e168bdbb76a9348d18f0"),
    "responses.jsonl": (40555, "774f62166a735dcd6b7ad61f1f3405db0873f7e9e541d45605afb13a8fa6dc88"),
}
EVIDENCE_PATH = Path(__file__).with_name("evidence") / "predecessor-40060633208.zip"
PRIOR_SETTLED = 315_652
PRIOR_REQUESTS = 36
BUDGET = 20_000_000
RESERVATION = 3_000_000
SECOND_SETTLED = 208_516
SECOND_REQUESTS = 24
# Historical evidence keeps its original deadline; a new candidate deadline is
# not retroactive authorization and cannot rewrite the accepted historical run.
HISTORICAL_PRICE_VALID_UNTIL = int(datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp())
PRICE_VALID_UNTIL = int(datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp())
OPERATIONS = (
    "shared_wait_after_absence", "actual_color_choice", "serious_mixed_work",
    "work_finish_continuation", "same_type_ambiguity", "deleted_source_memory",
    "greeting_without_page", "greeting_with_page", "third_party_experience",
    "future_invitation", "explicit_work_format", "unseen_ordinary_greeting",
)
OPERATIONS_SHA256 = hashlib.sha256(json.dumps(
    OPERATIONS, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
).encode()).hexdigest()
# Historical plans stay fixed even if the current candidate changes its matrix.
HISTORICAL_OPERATIONS = (
    "independent_play", "shared_wait_after_absence", "actual_color_choice",
    "unpressured_leisure", "paused_gap", "serious_mixed_work",
    "work_finish_continuation", "boat_specific_memory", "cloud_specific_memory",
    "same_type_ambiguity", "deleted_source_memory", "withdrawal_and_boundaries",
)
HISTORICAL_OPERATIONS_SHA256 = hashlib.sha256(json.dumps(
    HISTORICAL_OPERATIONS, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
).encode()).hexdigest()
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_STATE_KEYS = frozenset(("schema", "source_sha", "request_count", "settled_micro_cny",
                         "reserved_micro_cny", "budget_micro_cny", "halted"))
LINEAGE_KEYS = frozenset((
    "campaign_id", "correction_ordinal", "predecessor_run_id", "predecessor_artifact_id",
    "predecessor_source_sha", "predecessor_helper_sha", "predecessor_archive_sha256",
    "predecessor_ledger_sha256", "prior_settled_micro_cny", "prior_reserved_micro_cny",
    "prior_request_count", "budget_micro_cny", "operations_sha256", "price_valid_until",
))


class ContinuationError(RuntimeError):
    """Only fixed safe error codes, never artifact contents or filesystem paths."""


def _demand(condition: bool, code: str = "PREDECESSOR_EVIDENCE_INVALID") -> None:
    if not condition:
        raise ContinuationError(code)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _demand(key not in result)
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise ContinuationError("PREDECESSOR_EVIDENCE_INVALID")


def _json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ContinuationError("PREDECESSOR_EVIDENCE_INVALID") from None
    _demand(isinstance(value, dict))
    assert isinstance(value, dict)
    return value


def _lines(raw: bytes) -> list[dict[str, Any]]:
    _demand(bool(raw) and raw.endswith(b"\n") and b"\r" not in raw)
    lines = raw.splitlines()
    _demand(bool(lines) and len(lines) <= 38 and all(0 < len(line) <= 32768 for line in lines))
    return [_json(line) for line in lines]


def _exact(value: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    return all(key in value and type(value[key]) is type(item) and value[key] == item
               for key, item in expected.items())


def _state(count: int, settled: int, reserved: int, *, closed: bool = False) -> dict[str, Any]:
    return {"schema": 1, "source_sha": ORIGINAL_SOURCE_SHA, "request_count": count,
            "settled_micro_cny": settled, "reserved_micro_cny": reserved,
            "budget_micro_cny": BUDGET, "halted": closed}


def _replay_ledger(raw: bytes) -> dict[str, Any]:
    """Replay a fixed complete predecessor; no unknowns can become free money."""
    rows = _lines(raw)
    _demand(len(rows) == 2 + 3 * ORIGINAL_REQUESTS)
    _demand(set(rows[0]) == _STATE_KEYS | {"event"}
            and _exact(rows[0], {**_state(0, 0, 0), "event": "created"}))
    settled = 0
    for index, operation in enumerate(HISTORICAL_OPERATIONS):
        reserve, settlement, complete = rows[1 + index * 3:4 + index * 3]
        _demand(set(reserve) == _STATE_KEYS | {"event", "operation", "body_sha256"}
                and _exact(reserve, {**_state(index + 1, settled, RESERVATION),
                                    "event": "reserved", "operation": operation})
                and isinstance(reserve["body_sha256"], str)
                and bool(_HASH.fullmatch(reserve["body_sha256"]))
                and settled + RESERVATION <= BUDGET)
        _demand(set(settlement) == _STATE_KEYS | {
            "event", "operation", "cost_micro_cny", "prompt_tokens", "completion_tokens"})
        prompt, completion, cost = (settlement.get(key) for key in
                                    ("prompt_tokens", "completion_tokens", "cost_micro_cny"))
        _demand(type(prompt) is int and 0 <= prompt <= 1_048_576
                and type(completion) is int and 0 <= completion <= 512
                and prompt + completion <= 1_048_576
                and type(cost) is int and cost == prompt * 2 + completion * 8
                and 0 <= cost <= RESERVATION)
        assert isinstance(cost, int)
        settled += cost
        _demand(_exact(settlement, {**_state(index + 1, settled, 0),
                                    "event": "settled", "operation": operation}))
        _demand(set(complete) == _STATE_KEYS | {"event", "operation", "calls"}
                and _exact(complete, {**_state(index + 1, settled, 0),
                                      "event": "stage_complete", "operation": operation, "calls": 1}))
    _demand(settled == ORIGINAL_SETTLED)
    # Broker.close intentionally marks closed.halted=true; a halted event or an
    # unclosed/uncertain reservation is never accepted by the exact replay above.
    _demand(set(rows[-1]) == _STATE_KEYS | {"event"}
            and _exact(rows[-1], {**_state(ORIGINAL_REQUESTS, settled, 0, closed=True), "event": "closed"}))
    return _state(ORIGINAL_REQUESTS, settled, 0)


def _historical_lineage() -> dict[str, Any]:
    """Exactly ordinal 1's recorded lineage, never today's candidate lineage."""
    return {
        "campaign_id": CAMPAIGN_ID, "correction_ordinal": 1,
        "predecessor_run_id": ORIGINAL_RUN_ID, "predecessor_artifact_id": ORIGINAL_ARTIFACT_ID,
        "predecessor_source_sha": ORIGINAL_SOURCE_SHA, "predecessor_helper_sha": ORIGINAL_HELPER_SHA,
        "predecessor_archive_sha256": ORIGINAL_ARCHIVE_SHA256,
        "predecessor_ledger_sha256": ORIGINAL_LEDGER_SHA256,
        "prior_settled_micro_cny": ORIGINAL_SETTLED, "prior_reserved_micro_cny": 0,
        "prior_request_count": ORIGINAL_REQUESTS, "budget_micro_cny": BUDGET,
        "operations_sha256": HISTORICAL_OPERATIONS_SHA256, "price_valid_until": HISTORICAL_PRICE_VALID_UNTIL,
    }


def _third_historical_lineage() -> dict[str, Any]:
    """Exactly ordinal 2's immutable lineage; no candidate repricing."""
    return {
        "campaign_id": CAMPAIGN_ID, "correction_ordinal": 2,
        "predecessor_run_id": SECOND_RUN_ID, "predecessor_artifact_id": SECOND_ARTIFACT_ID,
        "predecessor_source_sha": SECOND_SOURCE_SHA, "predecessor_helper_sha": SECOND_HELPER_SHA,
        "predecessor_archive_sha256": SECOND_ARCHIVE_SHA256,
        "predecessor_ledger_sha256": SECOND_LEDGER_SHA256,
        "prior_settled_micro_cny": SECOND_SETTLED, "prior_reserved_micro_cny": 0,
        "prior_request_count": SECOND_REQUESTS, "budget_micro_cny": BUDGET,
        "operations_sha256": HISTORICAL_OPERATIONS_SHA256,
        "price_valid_until": int(datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp()),
    }


def _correction_state(count: int, settled: int, reserved: int, *,
                      closed: bool = False, third: bool = False) -> dict[str, Any]:
    prior_requests = SECOND_REQUESTS if third else ORIGINAL_REQUESTS
    prior_settled = SECOND_SETTLED if third else ORIGINAL_SETTLED
    return {
        **(_third_historical_lineage() if third else _historical_lineage()), "schema": 2,
        "source_sha": PREDECESSOR_SOURCE_SHA if third else SECOND_SOURCE_SHA,
        "request_count": count, "settled_micro_cny": settled,
        "reserved_micro_cny": reserved, "halted": closed,
        "batch_request_count": count, "lifetime_request_count": prior_requests + count,
        "batch_settled_micro_cny": settled - prior_settled,
        "batch_reserved_micro_cny": reserved, "lifetime_settled_micro_cny": settled,
        "lifetime_reserved_micro_cny": reserved, "lifetime_liability_micro_cny": settled + reserved,
        "lifetime_remaining_micro_cny": BUDGET - settled - reserved,
    }


def _replay_correction_ledger(raw: bytes, *, third: bool = False) -> dict[str, Any]:
    """Replay the fixed ordinal 1 or 2 ledger from its exact cumulative opening.

    Historical batch deltas never replace lifetime liability. This private
    selector accepts only the two compiled historical schemas, not caller
    balances, new ordinals, repricing or arbitrary continuation evidence.
    """
    prior_settled = SECOND_SETTLED if third else ORIGINAL_SETTLED
    prior_requests = SECOND_REQUESTS if third else ORIGINAL_REQUESTS
    expected_settled = PRIOR_SETTLED if third else SECOND_SETTLED
    expected_requests = PRIOR_REQUESTS if third else SECOND_REQUESTS

    def state(count: int, settled: int, reserved: int, *, closed: bool = False) -> dict[str, Any]:
        return _correction_state(count, settled, reserved, closed=closed, third=third)

    rows = _lines(raw)
    state_keys = set(state(0, prior_settled, 0))
    _demand(len(rows) == 2 + 3 * len(HISTORICAL_OPERATIONS))
    _demand(set(rows[0]) == state_keys | {"event"}
            and _exact(rows[0], {**state(0, prior_settled, 0), "event": "created"}))
    settled = prior_settled
    for index, operation in enumerate(HISTORICAL_OPERATIONS):
        reserve, settlement, complete = rows[1 + index * 3:4 + index * 3]
        _demand(set(reserve) == state_keys | {"event", "operation", "body_sha256"}
                and _exact(reserve, {**state(index + 1, settled, RESERVATION),
                                    "event": "reserved", "operation": operation})
                and isinstance(reserve["body_sha256"], str)
                and bool(_HASH.fullmatch(reserve["body_sha256"]))
                and settled + RESERVATION <= BUDGET)
        _demand(set(settlement) == state_keys | {
            "event", "operation", "cost_micro_cny", "prompt_tokens", "completion_tokens"})
        prompt, completion, cost = (settlement.get(key) for key in
                                    ("prompt_tokens", "completion_tokens", "cost_micro_cny"))
        _demand(type(prompt) is int and 0 <= prompt <= 1_048_576
                and type(completion) is int and 0 <= completion <= 512
                and prompt + completion <= 1_048_576
                and type(cost) is int and cost == prompt * 2 + completion * 8
                and 0 <= cost <= RESERVATION)
        assert isinstance(cost, int)
        settled += cost
        _demand(_exact(settlement, {**state(index + 1, settled, 0),
                                    "event": "settled", "operation": operation}))
        _demand(set(complete) == state_keys | {"event", "operation", "calls"}
                and _exact(complete, {**state(index + 1, settled, 0),
                                      "event": "stage_complete", "operation": operation, "calls": 1}))
    _demand(settled == expected_settled and prior_requests + len(HISTORICAL_OPERATIONS) == expected_requests)
    # Only the final, normally closed row may set halted=true. Unknown usage,
    # extra attempts, incomplete stages and outstanding reservations all block.
    _demand(set(rows[-1]) == state_keys | {"event"}
            and _exact(rows[-1], {**state(len(HISTORICAL_OPERATIONS), settled, 0, closed=True),
                                  "event": "closed"}))
    return state(len(HISTORICAL_OPERATIONS), settled, 0)


def _archive_members(raw: bytes, *, size: int, digest: str,
                     expected: Mapping[str, tuple[int, str]]) -> dict[str, bytes]:
    _demand(len(raw) == size and hashlib.sha256(raw).hexdigest() == digest,
            "PREDECESSOR_ARCHIVE_MISMATCH")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            _demand(len(infos) == len(expected) and {i.filename for i in infos} == set(expected))
            members: dict[str, bytes] = {}
            for info in infos:
                member_size, member_digest = expected[info.filename]
                mode = info.external_attr >> 16
                _demand(not info.is_dir() and stat.S_ISREG(mode)
                        and not info.flag_bits & 1 and info.compress_type == zipfile.ZIP_DEFLATED
                        and info.file_size == member_size and 0 < info.compress_size <= size)
                with archive.open(info) as stream:
                    data = stream.read(member_size + 1)
                _demand(len(data) == member_size and hashlib.sha256(data).hexdigest() == member_digest,
                        "PREDECESSOR_MEMBER_MISMATCH")
                members[info.filename] = data
    except (OSError, ValueError, EOFError, RuntimeError, zipfile.BadZipFile) as exc:
        if isinstance(exc, ContinuationError):
            raise
        raise ContinuationError("PREDECESSOR_EVIDENCE_INVALID") from None
    return members


def _verify_dialogue(members: Mapping[str, bytes], state: Mapping[str, Any], *, schema: int) -> None:
    summary, responses = _json(members["summary.json"]), _lines(members["responses.jsonl"])
    _demand(set(summary) == {"schema", "matrix", "budget"}
            and type(summary["schema"]) is int and summary["schema"] == schema)
    budget, matrix = summary["budget"], summary["matrix"]
    expected_budget = {**state, "stages_complete": len(HISTORICAL_OPERATIONS)}
    if schema == 2:
        expected_budget["accounting_basis"] = "conservative_usage_not_provider_invoice"
    _demand(isinstance(budget, dict) and set(budget) == set(expected_budget)
            and _exact(budget, expected_budget)
            and isinstance(matrix, dict)
            and _exact(matrix, {"passed": True, "pass_scope": "mechanism_only", "schema_version": 1,
                                "synthetic_only": True, "ordinary_count": len(HISTORICAL_OPERATIONS),
                                "tone_review_required": True, "transport": "uvicorn:/api/chat"})
            and len(responses) == len(HISTORICAL_OPERATIONS) and matrix.get("responses") == responses)
    for operation, response in zip(HISTORICAL_OPERATIONS, responses, strict=True):
        _demand(_exact(response, {"operation": operation, "ordinary_turn_read_only": True,
                                  "source_verified": True})
                and isinstance(response.get("assistant_text"), str)
                and 0 < len(response["assistant_text"].encode()) <= 8192)


def _verify_original_archive(raw: bytes) -> dict[str, Any]:
    members = _archive_members(raw, size=ORIGINAL_ARCHIVE_SIZE, digest=ORIGINAL_ARCHIVE_SHA256,
                               expected=ORIGINAL_MEMBERS)
    state = _replay_ledger(members["budget.jsonl"])
    _verify_dialogue(members, state, schema=1)
    return state


def _verify_second_archive(raw: bytes) -> dict[str, Any]:
    members = _archive_members(raw, size=SECOND_ARCHIVE_SIZE, digest=SECOND_ARCHIVE_SHA256, expected=SECOND_MEMBERS)
    state = _replay_correction_ledger(members["budget.jsonl"])
    _verify_dialogue(members, state, schema=2)
    return state


def _verify_archive(raw: bytes) -> dict[str, Any]:
    members = _archive_members(raw, size=ARCHIVE_SIZE, digest=ARCHIVE_SHA256, expected=MEMBERS)
    state = _replay_correction_ledger(members["budget.jsonl"], third=True)
    _verify_dialogue(members, state, schema=2)
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


def _read_evidence(path: Path, size: int) -> bytes:
    try:
        _demand(path.is_file() and not path.is_symlink(), "PREDECESSOR_EVIDENCE_UNAVAILABLE")
        with path.open("rb") as stream:
            return stream.read(size + 1)
    except OSError:
        raise ContinuationError("PREDECESSOR_EVIDENCE_UNAVAILABLE") from None


def verified_predecessor() -> Predecessor:
    """No caller path/balance/ordinal; verify all three pinned archives on every call."""
    original = _verify_original_archive(_read_evidence(ORIGINAL_EVIDENCE_PATH, ORIGINAL_ARCHIVE_SIZE))
    correction = _verify_second_archive(_read_evidence(SECOND_EVIDENCE_PATH, SECOND_ARCHIVE_SIZE))
    _demand(_exact(correction, {
        "prior_settled_micro_cny": original["settled_micro_cny"],
        "prior_reserved_micro_cny": original["reserved_micro_cny"],
        "prior_request_count": original["request_count"],
    }))
    third = _verify_archive(_read_evidence(EVIDENCE_PATH, ARCHIVE_SIZE))
    _demand(_exact(third, {
        "prior_settled_micro_cny": correction["lifetime_settled_micro_cny"],
        "prior_reserved_micro_cny": correction["lifetime_reserved_micro_cny"],
        "prior_request_count": correction["lifetime_request_count"],
    }))
    return Predecessor(settled_micro_cny=third["lifetime_settled_micro_cny"],
                       reserved_micro_cny=third["lifetime_reserved_micro_cny"],
                       request_count=third["lifetime_request_count"])


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
    """Validate lineage within an admission/journal, not arbitrary starting money."""
    _demand(isinstance(record, Mapping) and _exact(record, lineage_fields()), "CONTINUATION_LINEAGE_MISMATCH")
