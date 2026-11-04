"""Four-call switch experience after ten immutable paid batches.

The predecessor passed mechanism checks, not semantic acceptance. Retain its
original answers and full settled/unknown liability. Recorded usage is a
conservative estimate, not a provider invoice or missing response envelope.
"""
from __future__ import annotations

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
from scripts.companion_ai_validation import experience_retest_continuation as old

ADMISSION_SCHEMA = old.ADMISSION_SCHEMA
LINEAGE_KEYS = old.LINEAGE_KEYS
CAMPAIGN_ID = old.CAMPAIGN_ID
CORRECTION_ORDINAL = 11
BUDGET = old.BUDGET
RESERVATION = old.RESERVATION
PRIOR_SETTLED = 598_118
PRIOR_REQUESTS = 78
PRICE_VALID_UNTIL = 1793836800  # 2026-11-05 00:00 UTC; owner renewed 2026-11-04.
OUTPUT_TOKENS = 512
OPERATIONS = ("a_default_off", "b_enabled_chat", "c_disabled_work", "d_disabled_identity")
OPERATIONS_SHA256 = hashlib.sha256(json.dumps(OPERATIONS, separators=(",", ":")).encode()).hexdigest()
PREDECESSOR_SOURCE_SHA = "899fd91659a4a57986d1eba63f7cc0c051e38862"
PREDECESSOR_HELPER_SHA = "d75d48a836d22ca83ddc0ce90092ba156390faca"
PREDECESSOR_RUN_ID = 41770764462
PREDECESSOR_HELPER_RUN_ID = 41767085952
PREDECESSOR_TREE_SHA = "8b55c016d34a9fab7169d8e10151e674505dfab3"
PREDECESSOR_ARTIFACT_ID = 13297045215
ARCHIVE_SHA256 = "d9c4b19e88601956755d0bc96eda6001bd5564d0f01fa0c37101af61362993a8"
ARCHIVE_SIZE = 17674
LEDGER_SHA256 = "65554a15f34e2e5ef00fd8fed61f8c8f873d55c7e9c175908f2545381d13d3b8"
MEMBERS = {'summary.json': (23057, 'd5b19aaef393d4aea92097fb349fb594b35e8aafc412e3280b653298ceac17cc'), 'responses.jsonl': (17151, 'e57985e4589945c9537ed56ab6a72c68d222627f3fa7f3b38f639c07a4ab5322'), 'budget.jsonl': (20184, '65554a15f34e2e5ef00fd8fed61f8c8f873d55c7e9c175908f2545381d13d3b8'), 'requests.jsonl': (82282, '2cc0e1e61dc69b1f7658d1cf127ba201367a890fb387f0b0609cabda2bdf50ae')}
EVIDENCE_PATH = (Path(__file__).resolve().parents[2] / "experiments/companion_experience"
                 / "fixtures/tenth-paid-original-artifact.zip")
ContinuationError = archive.ContinuationError
ALL_HISTORICAL_IDENTITIES = old.ALL_HISTORICAL_IDENTITIES | frozenset((
    PREDECESSOR_SOURCE_SHA, PREDECESSOR_HELPER_SHA,
    "ca73a48211c0106daf5ac8d017c7cab7f0e03c35",
    "d6ed094caaf5d18a74abee5810f9fee0734bb508",
))
_equal = old._equal


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


def _state(lineage: dict[str, Any], count: int, settled: int, reserved: int,
           *, closed: bool = False) -> dict[str, Any]:
    return {
        **lineage, "schema": ADMISSION_SCHEMA, "source_sha": PREDECESSOR_SOURCE_SHA,
        "request_count": count, "settled_micro_cny": settled,
        "reserved_micro_cny": reserved, "halted": closed,
        "batch_request_count": count, "lifetime_request_count": old.PRIOR_REQUESTS + count,
        "batch_settled_micro_cny": settled - old.PRIOR_SETTLED,
        "batch_reserved_micro_cny": reserved,
        "lifetime_settled_micro_cny": settled, "lifetime_reserved_micro_cny": reserved,
        "lifetime_liability_micro_cny": settled + reserved,
        "lifetime_remaining_micro_cny": BUDGET - settled - reserved,
    }


def _verify_archive(raw: bytes, lineage: dict[str, Any]) -> Predecessor:
    from scripts.companion_ai_validation import broker as gate

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
            value = members[name]
            archive._demand(value.endswith(b"\n") and b"\r" not in value)
            return [gate.decode(row) for row in value.splitlines()]

        requests, responses, ledger = (lines(name) for name in
                                      ("requests.jsonl", "responses.jsonl", "budget.jsonl"))
        _equal((len(requests), len(responses), len(ledger)), (5, 5, 17))
        summary = gate.decode(members["summary.json"])
        _equal(sorted(summary), ["budget", "matrix", "product_candidate", "schema"])
        _equal(summary["schema"], ADMISSION_SCHEMA)
        # This is the immutable old candidate, not the newly repaired product.
        _equal(summary["product_candidate"], {'source_sha': '42a54a48a90e3cc7738d3c6029e11aebe46d276b', 'tree_sha': '57e67e539fb0c7798ab9031cafc0fc73ddfdd75b', 'objects': {'src': '4df7375c334aa1fe68395320b355bbe43eaa2bab', 'pyproject.toml': 'dfa900978a00026b0c7a222bcb6ba7ce62201121', 'uv.lock': 'eb7803ff8bf95818a70ad597a9e41d22187896c7', 'web': 'aa1d7428642416642524f304f5a2bd99012cd0fd'}, 'web_executed': False})
        settled = old.PRIOR_SETTLED
        expected_rows = [{**_state(lineage, 0, settled, 0), "event": "created"}]
        for ordinal, (operation, request, response) in enumerate(
                zip(old.OPERATIONS, requests, responses, strict=True), 1):
            body = request["raw_body"].encode()
            _equal(request, {"operation": operation, "ordinal": ordinal,
                             "body_sha256": hashlib.sha256(body).hexdigest(),
                             "raw_body": body.decode()})
            decoded = gate.decode(body)
            _equal((decoded["model"], decoded["max_tokens"], decoded["thinking"],
                    decoded["stream"], decoded["tool_choice"]),
                   (gate.MODEL, 512, {"type": "disabled"}, False, "auto"))
            _equal(response["operation"], operation)
            _equal(decoded["messages"][-1], {"role": "user", "content": response["user_input"]})
            _equal((response["ordinary_turn_read_only"], response["source_verified"]), (True, True))
            _equal(response["provenance"]["runtime_budget"],
                   {"model_calls": 1, "title_model_calls": 0, "tool_calls": 0})
            _equal((response["provenance"]["transport"], response["provenance"]["web_executed"]),
                   ("runtime_http_backend", False))
            _equal(response["snapshot"], response["provenance"]["snapshot_before"])
            archive._demand(isinstance(response["assistant_text"], str)
                            and bool(response["assistant_text"].strip()))
            # Only recorded broker counters survive in this artifact, not raw
            # provider envelopes. Recompute their conservative estimate exactly.
            recorded = ledger[ordinal * 3 - 1]
            prompt = gate._integer(recorded["prompt_tokens"], gate.CONTEXT_TOKENS)
            completion = gate._integer(recorded["completion_tokens"], OUTPUT_TOKENS)
            cost = prompt * 2 + completion * 8
            archive._demand(cost <= RESERVATION and settled + RESERVATION <= BUDGET)
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
        expected_rows.append({**_state(lineage, 5, settled, 0, closed=True), "event": "closed"})
        _equal(ledger, expected_rows)
        # Original summary was captured before finally/close; do not rewrite it.
        _equal(summary["budget"], {**_state(lineage, 5, settled, 0), "stages_complete": 5,
                                   "accounting_basis": "conservative_usage_not_provider_invoice"})
        matrix = summary["matrix"]
        groups = matrix["groups"]
        _equal([group["group"] for group in groups], ["a", "d", "e"])
        _equal([row for group in groups for row in group["responses"]], responses)
        _equal([len(group["controls"]) for group in groups], [0, 0, 2])
        for group in groups:
            _equal(group["passed"], True)
            archive._demand(group["assertions"] and all(v is True for v in group["assertions"].values()))
            for control in group["controls"]:
                _equal(control["runtime_budget"],
                       {"model_calls": 0, "title_model_calls": 0, "tool_calls": 0})
        _equal(matrix, {
            "schema_version": 1, "passed": True, "pass_scope": "mechanism_only",
            "synthetic_only": True, "web_executed": False,
            "transport": "uvicorn:/api/pilot/runtime/v1/turns", "ordinary_count": 5,
            "tone_review_required": True, "semantic_review": "post_run_independent_review_required",
            "context_observations": [{"operation": row["operation"],
                "actual_context_present": row["expected_context"] is not None,
                "fixture_reference_present": row["provenance"]["fixture_reference_not_projected"] is not None}
                for row in responses], "groups": groups,
        })
        return Predecessor()
    except (ValueError, TypeError, KeyError, IndexError, StopIteration, OSError,
            zipfile.BadZipFile, RuntimeError):
        raise ContinuationError("PREDECESSOR_EVIDENCE_INVALID") from None


def verified_predecessor() -> Predecessor:
    previous = old.verified_predecessor()
    _equal((previous.request_count, previous.settled_micro_cny, previous.reserved_micro_cny),
           (73, 558124, 0))
    return _verify_archive(archive._read_evidence(EVIDENCE_PATH, ARCHIVE_SIZE), old.lineage_fields())


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
