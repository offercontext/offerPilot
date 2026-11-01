"""Five-call retest after nine immutable batches; never resume their allowances.

The ninth passed mechanism checks only. Its original answers (including the
three independently identified semantic failures) remain byte-for-byte intact.
Recorded ledger usage supports a conservative estimate, not a provider invoice
or an independent reconstruction of the missing upstream response envelopes.
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
from scripts.companion_ai_validation import experience_continuation as old

ADMISSION_SCHEMA = old.ADMISSION_SCHEMA
LINEAGE_KEYS = old.LINEAGE_KEYS
CAMPAIGN_ID = old.CAMPAIGN_ID
CORRECTION_ORDINAL = 9
BUDGET = old.BUDGET
RESERVATION = old.RESERVATION
PRIOR_SETTLED = 558_124
PRIOR_REQUESTS = 73
PRICE_VALID_UNTIL = old.PRICE_VALID_UNTIL
OUTPUT_TOKENS = 512
OPERATIONS = ("x01", "x02", "x03", "x09", "x11")
OPERATIONS_SHA256 = hashlib.sha256(json.dumps(OPERATIONS, separators=(",", ":")).encode()).hexdigest()
PREDECESSOR_SOURCE_SHA = "94d183473dbef58176b36fd65e4496b83e287f2f"
PREDECESSOR_HELPER_SHA = "0803bb2fc814bcc51906bd9eaae018f45f3ebd00"
PREDECESSOR_RUN_ID = 41278459727
PREDECESSOR_HELPER_RUN_ID = 41276222134
PREDECESSOR_TREE_SHA = "4e96c2f29f5b68bbc5b0f1b5fa6cce9d34c32313"
PREDECESSOR_ARTIFACT_ID = 13072896758
ARCHIVE_SHA256 = "ba29cedaa8f293bc745a2224ca660363b2fe06de293d4e17fc8cc0a8787eacfd"
ARCHIVE_SIZE = 27937
LEDGER_SHA256 = "08bd6f28909f4844dbe9a322803517a17a3afeaa6a4026e409766a30fde35bd9"
MEMBERS = {
    "summary.json": (45857, "9a1c1bcc687887c07c78dda828bd93f67bcf4c4535a91264fa485eae00d31ae3"),
    "budget.jsonl": (45310, LEDGER_SHA256),
    "responses.jsonl": (32074, "9621d8739c5a9ca83b363600574a86c51dbc193a56a9c2d2080776ed1e35db88"),
    "requests.jsonl": (209869, "ec1a7842b7bd1c7a5ff681824c25708609af7646adb84ef6cf0e95a08240cf76"),
}
EVIDENCE_PATH = (Path(__file__).resolve().parents[2] / "experiments/companion_experience"
                 / "fixtures/ninth-paid-original-artifact.zip")
ContinuationError = archive.ContinuationError
ALL_HISTORICAL_IDENTITIES = old.ALL_HISTORICAL_IDENTITIES | frozenset((
    PREDECESSOR_SOURCE_SHA, PREDECESSOR_HELPER_SHA,
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
        _equal((len(requests), len(responses), len(ledger)), (12, 12, 38))
        summary = gate.decode(members["summary.json"])
        _equal(sorted(summary), ["budget", "matrix", "product_candidate", "schema"])
        _equal(summary["schema"], ADMISSION_SCHEMA)
        # This is the immutable old candidate, not the newly repaired product.
        _equal(summary["product_candidate"], {
            "source_sha": "6d5ff92ac67fb01aad1752f2b307c79a706e943d",
            "tree_sha": "3de6eca96b06ee2fa031c4c5e9e9c1cc60c1af72",
            "objects": {"src": "19c4461c814db3cc32e098e7c889656b271fe15c",
                        "pyproject.toml": "b7b8e27f31e1a402bc7e5330f0b13b3c9bc04dfe",
                        "uv.lock": "eb7803ff8bf95818a70ad597a9e41d22187896c7",
                        "web": "176cb3683a6db77390985ff436e61e98042f5532"},
            "web_executed": False,
        })
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
        expected_rows.append({**_state(lineage, 12, settled, 0, closed=True), "event": "closed"})
        _equal(ledger, expected_rows)
        # Original summary was captured before finally/close; do not rewrite it.
        _equal(summary["budget"], {**_state(lineage, 12, settled, 0), "stages_complete": 12,
                                   "accounting_basis": "conservative_usage_not_provider_invoice"})
        matrix = summary["matrix"]
        groups = matrix["groups"]
        _equal([group["group"] for group in groups], ["a", "b", "c", "d", "e"])
        _equal([row for group in groups for row in group["responses"]], responses)
        _equal([len(group["controls"]) for group in groups], [0, 0, 3, 0, 4])
        for group in groups:
            _equal(group["passed"], True)
            archive._demand(group["assertions"] and all(v is True for v in group["assertions"].values()))
            for control in group["controls"]:
                _equal(control["runtime_budget"],
                       {"model_calls": 0, "title_model_calls": 0, "tool_calls": 0})
        _equal(matrix, {
            "schema_version": 1, "passed": True, "pass_scope": "mechanism_only",
            "synthetic_only": True, "web_executed": False,
            "transport": "uvicorn:/api/pilot/runtime/v1/turns", "ordinary_count": 12,
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
           (61, 458256, 0))
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
