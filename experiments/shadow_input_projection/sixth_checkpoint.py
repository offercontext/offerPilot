"""Read-only terminal checkpoint for a consumed, failed sixth batch.

No admission, reservation writer, new quote window, transport or resume API.
verify_archive is pure over bytes; verify_checkpoint additionally revalidates
all five predecessor archives from the existing offline verifier.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import stat
from typing import Any, Mapping, cast
import zipfile

ARCHIVE_SHA256 = "37e36d28a4b45585a48367923edb19d46e4c6ce338e1bc627bd1a816fbb71968"
ARCHIVE_SIZE = 7219
SOURCE_SHA = "69a0ffe08ef1e11e2ea6e7854ca608746a2ffe80"
HELPER_SHA = "32120713444f242ffc2c70f63334292a0db6187b"
RUN_ID = 41002088459
HELPER_RUN_ID = 40998254898
TREE_SHA = "6d2c2d84bb5588d5d0e6e35f0ee8b91472ecddc2"
ARTIFACT_ID = 12954297124
MEMBERS = {
    "summary.json": (3771, "c96edab7a1db62f332edc9d4cb750670be4244d92d297755d01ce2b4690a7e16"),
    "responses.jsonl": (3886, "7a83fcd7c2916b0e20d9a5715117a1b4888bb840577429f639ffdbfd1ef7b342"),
    "budget.jsonl": (11829, "90f82c2e69a70a87fa51a0c35d733c8a29d6ae41b0f1793de20dff3dd5dca16d"),
    "requests.jsonl": (7737, "9ae9562600019b45ad5321bd0c5b6f7a29a015e8e1dd074076104fe9132970d8"),
}
RESPONSE_HASHES = (
    "c0a0d8d5f091421759960f5a4702c41a67f6b9c63326d50dcf36698a707e831f",
    "2c26a3e932d4a2498723b5da30f0a08639a95f0af5f9bfc22daa4ba203e737e1",
    "a5fc84c043b25a5c23f8550b02a2ca6bf38382ed49a3cf21d3ea4aa91c177db0",
)
USAGES = ((477, 127, 1970), (477, 80, 1594), (474, 74, 1540))
HISTORICAL_LINEAGE: dict[str, Any] = {'campaign_id': 'one-shot-20261021',
 'correction_ordinal': 5,
 'predecessor_run_id': 40812959831,
 'predecessor_artifact_id': 12871274468,
 'predecessor_source_sha': '2298348ce0e884a08a7cd7199b42425884310ebd',
 'predecessor_helper_sha': 'abf7b99d1a8cbab10c09d43f7cb842bf3e5d200b',
 'predecessor_archive_sha256': '4044acb685abf79c20b63ed8c505e71e10691f58e799684b78c70bb9831ea6db',
 'predecessor_ledger_sha256': 'c04db996ef834da0d8a750bd9f7d99f12957769600abfe275ba1466dc0a65140',
 'prior_settled_micro_cny': 414284,
 'prior_reserved_micro_cny': 0,
 'prior_request_count': 49,
 'budget_micro_cny': 20000000,
 'operations_sha256': '6bf34b3b6bd897e24ddd422363190360886248a5cb9b8d660600161aa65a8bae',
 'price_valid_until': 1793577600}
HISTORICAL_EXPERIMENT: dict[str, Any] = {'protocol': 'shadow-quote-v2',
 'cases_sha256': '501cd8ac4b49b6a963a6829be487ae0f3e46dd1f77086b1a2bdcfa2cca055b30',
 'requests_sha256': {'s01': '31b7ce6e7fa388d001582a4f0ba11cda6bf8ed6938563805a0cb5689ddafb805',
                     's02': '7f733d8d64e0772b105971c6eea4acb5b9e7eed08bf5ce5baf34be8874bdbbc5',
                     's03': '2e20e7b0b3c8dc5f046d41674324a34ea65ce1fdbab56d570a1159f5a44d62c3',
                     's04': '42e16b7f501eb5ac694bf1a5bb7fc43b81122267a9cac72a474b2d37958263d4',
                     's05': '5b62e3586a7acb99005e59efedb63ab3c5e0d32f11b4162aa3c8ddba4c53fd00',
                     's06': 'bf99222eed215372faa9f4a36bb0d5a1a4cdb5737a260a5b8500b91a69af70a4'},
 'model': 'deepseek-flash',
 'output_limit': 768,
 'price_valid_until': 1793577600,
 'input_micro_cny_per_token': 2,
 'output_micro_cny_per_token': 8,
 'original_drafts_unchanged': True}


class CheckpointError(ValueError):
    """Constant safe diagnostic; never return raw payloads or credentials."""


def demand(condition: bool) -> None:
    if not condition:
        raise CheckpointError("sixth_checkpoint_invalid")


def equal(actual: Any, expected: Any) -> None:
    demand(json.dumps(actual, sort_keys=True, allow_nan=False)
           == json.dumps(expected, sort_keys=True, allow_nan=False))


def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        demand(key not in result)
        result[key] = value
    return result


def reject_constant(_: str) -> None:
    raise CheckpointError("sixth_checkpoint_invalid")


def decode(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)
    demand(isinstance(value, dict))
    return cast(dict[str, Any], value)


def lines(raw: bytes) -> list[dict[str, Any]]:
    demand(raw.endswith(b"\n") and b"\r" not in raw)
    return [decode(line) for line in raw.splitlines()]


@dataclass(frozen=True)
class Checkpoint:
    status: str = "consumed_failed"
    resumable: bool = False
    authorizes_execution: bool = False
    semantic_quality_proven: bool = False
    source_sha: str = SOURCE_SHA
    helper_sha: str = HELPER_SHA
    run_id: int = RUN_ID
    helper_run_id: int = HELPER_RUN_ID
    tree_sha: str = TREE_SHA
    artifact_id: int = ARTIFACT_ID
    archive_sha256: str = ARCHIVE_SHA256
    actual_calls: int = 3
    lifetime_calls: int = 52
    batch_micro_cny: int = 5104
    settled_micro_cny: int = 419388
    reserved_micro_cny: int = 0
    stages_complete: int = 2
    error_code: str = "shadow_quote_quote_not_unique"


def state(count: int, settled: int, reserved: int, *, closed: bool = False) -> dict[str, Any]:
    return {
        **HISTORICAL_LINEAGE, "schema": 2, "source_sha": SOURCE_SHA,
        "request_count": count, "settled_micro_cny": settled,
        "reserved_micro_cny": reserved, "halted": closed,
        "batch_request_count": count, "lifetime_request_count": 49 + count,
        "batch_settled_micro_cny": settled - 414284,
        "batch_reserved_micro_cny": reserved, "lifetime_settled_micro_cny": settled,
        "lifetime_reserved_micro_cny": reserved, "lifetime_liability_micro_cny": settled + reserved,
        "lifetime_remaining_micro_cny": 20_000_000 - settled - reserved,
    }


def replay_members(members: Mapping[str, bytes]) -> Checkpoint:
    """Pure exact-row replay plus request/response/summary relationships."""
    demand(set(members) == set(MEMBERS))
    ledger = lines(members["budget.jsonl"])
    requests, responses = lines(members["requests.jsonl"]), lines(members["responses.jsonl"])
    demand(len(requests) == len(responses) == 3)
    expected_rows = [{**state(0, 414284, 0), "event": "created"}]
    cases = []
    settled = 414284
    for index, (request, response, usage) in enumerate(zip(requests, responses, USAGES, strict=True)):
        op = f"s{index + 1:02d}"
        request_hash = HISTORICAL_EXPERIMENT["requests_sha256"][op]
        equal(request, {"operation": op, "ordinal": index + 1, "body_sha256": request_hash,
                        "raw_body": request["raw_body"]})
        body = request["raw_body"].encode()
        demand(hashlib.sha256(body).hexdigest() == request_hash)
        payload = decode(body)
        data = decode(payload["messages"][1]["content"].encode())
        equal(response, {"operation": op, "status": 200, "raw_base64": response["raw_base64"],
                         "body_sha256": RESPONSE_HASHES[index]})
        reply = base64.b64decode(response["raw_base64"], validate=True)
        demand(hashlib.sha256(reply).hexdigest() == RESPONSE_HASHES[index])
        envelope = decode(reply)
        prompt, completion, cost = usage
        equal(envelope["usage"], {
            "prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion, "prompt_tokens_details": {"cached_tokens": 0},
            "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": prompt,
        })
        equal(envelope["model"], "deepseek-flash")
        demand(len(envelope["choices"]) == 1)
        choice = envelope["choices"][0]
        equal(choice["finish_reason"], "stop")
        review = decode(choice["message"]["content"].encode())
        equal(review["protocol"], "shadow-quote-v2")
        equal(review["target_id"], op)
        located = []
        occurrences = []
        for evidence in review["evidence"]:
            demand(set(evidence) == {"source_id", "quote"})
            source = next(item["text"] for item in data["sources"]
                          if item["id"] == evidence["source_id"])
            quote = evidence["quote"]
            demand(isinstance(quote, str) and bool(quote))
            matches = [i for i in range(len(source)) if source.startswith(quote, i)]
            occurrences.append(len(matches))
            if len(matches) == 1:
                located.append({"source_id": evidence["source_id"], "start": matches[0],
                                "end": matches[0] + len(quote), "quote": quote})
        if index < 2:
            demand(bool(occurrences) and all(count == 1 for count in occurrences))
            cases.append({"operation": op, "status": "structure_valid_semantics_ungraded",
                          "review": {**review, "evidence": located, "target": data["target"],
                                     "input_sha256": hashlib.sha256(
                                         payload["messages"][1]["content"].encode()).hexdigest(),
                                     "scope": "structure_only_semantics_ungraded"}})
        else:
            demand(any(count > 1 for count in occurrences))
            cases.append({"operation": op, "status": "failed",
                          "error_code": "shadow_quote_quote_not_unique"})
        demand(prompt * 2 + completion * 8 == cost and settled + 3_000_000 <= 20_000_000)
        expected_rows.append({**state(index + 1, settled, 3_000_000), "event": "reserved",
                              "operation": op, "body_sha256": request_hash})
        settled += cost
        expected_rows.append({**state(index + 1, settled, 0), "event": "settled",
                              "operation": op, "cost_micro_cny": cost,
                              "prompt_tokens": prompt, "completion_tokens": completion})
        if index < 2:
            expected_rows.append({**state(index + 1, settled, 0), "event": "stage_complete",
                                  "operation": op, "calls": 1})
    demand(settled == 419388)
    expected_rows.append({**state(3, settled, 0, closed=True), "event": "closed"})
    equal(ledger, expected_rows)
    cases += [{"operation": f"s{i:02d}", "status": "not_attempted"} for i in (4, 5, 6)]
    equal(decode(members["summary.json"]), {
        "schema": 2,
        "matrix": {"passed": False, "error_code": "shadow_quote_quote_not_unique",
                   "pass_scope": "structure_only_not_semantic_correctness",
                   "product_modified": False, "cases": cases},
        "budget": {**state(3, settled, 0, closed=True), "stages_complete": 2,
                   "accounting_basis": "conservative_usage_not_provider_invoice"},
        "experiment": HISTORICAL_EXPERIMENT,
    })
    return Checkpoint()


def verify_archive(raw: bytes) -> Checkpoint:
    """Pure verifier; archive identity fixed, never selected by a caller."""
    try:
        demand(type(raw) is bytes and len(raw) == ARCHIVE_SIZE
               and hashlib.sha256(raw).hexdigest() == ARCHIVE_SHA256)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos = z.infolist()
            demand(len(infos) == 4 and {i.filename for i in infos} == set(MEMBERS))
            members = {}
            for info in infos:
                size, digest = MEMBERS[info.filename]
                demand(stat.S_ISREG(info.external_attr >> 16) and not info.is_dir()
                       and not info.flag_bits & 1 and info.file_size == size
                       and info.compress_type == zipfile.ZIP_DEFLATED)
                with z.open(info) as stream:
                    value = stream.read(size + 1)
                demand(len(value) == size and hashlib.sha256(value).hexdigest() == digest)
                members[info.filename] = value
        return replay_members(members)
    except (ValueError, TypeError, KeyError, IndexError, StopIteration, OSError,
            zipfile.BadZipFile, RuntimeError):
        raise CheckpointError("sixth_checkpoint_invalid") from None


def verify_checkpoint() -> Checkpoint:
    """Reverify all six archived batches; return a non-resumable terminal record."""
    from scripts.companion_ai_validation import shadow_quote_continuation as previous

    verified = previous.verified_predecessor()
    equal((verified.request_count, verified.settled_micro_cny, verified.reserved_micro_cny),
          (49, 414284, 0))
    equal(previous.lineage_fields(), HISTORICAL_LINEAGE)
    path = Path(__file__).resolve().parent / "fixtures/paid-original-artifact.zip"
    demand(path.is_file() and not path.is_symlink())
    with path.open("rb") as stream:
        return verify_archive(stream.read(ARCHIVE_SIZE + 1))
