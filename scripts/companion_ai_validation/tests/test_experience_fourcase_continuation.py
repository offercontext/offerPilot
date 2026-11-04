"""Read-only ninth-artifact replay and inherited-budget tamper rejection."""
import hashlib
import io
import json
import zipfile

import pytest

from scripts.companion_ai_validation import continuation as archive
from scripts.companion_ai_validation import experience_fourcase_continuation as lineage


def test_nine_archives_replayed_and_budget_not_reset(monkeypatch):
    reads = set()
    original = archive._read_evidence

    def captured(path, size):
        reads.add(path)
        return original(path, size)

    monkeypatch.setattr(archive, "_read_evidence", captured)
    previous = lineage.verified_predecessor()
    assert (previous.request_count, previous.settled_micro_cny, previous.reserved_micro_cny) == (78, 598118, 0)
    assert len(reads) == 10
    assert lineage.OPERATIONS == ("a_default_off", "b_enabled_chat", "c_disabled_work", "d_disabled_identity")
    assert lineage.CORRECTION_ORDINAL == 11
    assert lineage.BUDGET == 20000000 and lineage.RESERVATION == 3000000
    assert lineage.PRICE_VALID_UNTIL == 1793836800
    assert lineage.PREDECESSOR_SOURCE_SHA in lineage.ALL_HISTORICAL_IDENTITIES
    assert lineage.OPERATIONS_SHA256 != lineage.old.OPERATIONS_SHA256


@pytest.mark.parametrize("generation", range(10))
def test_any_immutable_archive_corruption_blocks_retest(monkeypatch, generation):
    original = archive._read_evidence
    seen = []

    def corrupted(path, size):
        if path not in seen:
            seen.append(path)
        raw = original(path, size)
        return raw[:-1] + bytes([raw[-1] ^ 1]) if seen.index(path) == generation else raw

    monkeypatch.setattr(archive, "_read_evidence", corrupted)
    with pytest.raises(archive.ContinuationError):
        lineage.verified_predecessor()


@pytest.mark.parametrize("mutation", [
    "balance", "count", "halt", "reserved", "calls", "duplicate", "bool_usage",
    "cost", "limit", "thinking", "semantic_claim", "source", "summary_closed",
    "response_write", "control_call", "extra_request",
])
def test_ninth_replay_checks_not_just_byte_hash(monkeypatch, mutation):
    with zipfile.ZipFile(lineage.EVIDENCE_PATH) as z:
        infos = z.infolist()
        members = {info.filename: z.read(info) for info in infos}
    rows = [json.loads(row) for row in members["budget.jsonl"].splitlines()]
    requests = [json.loads(row) for row in members["requests.jsonl"].splitlines()]
    responses = [json.loads(row) for row in members["responses.jsonl"].splitlines()]
    summary = json.loads(members["summary.json"])
    if mutation == "balance":
        rows[2]["settled_micro_cny"] += 1
    elif mutation == "count":
        rows[-1]["lifetime_request_count"] -= 1
    elif mutation == "halt":
        rows[-1]["halted"] = False
    elif mutation == "reserved":
        rows[-1]["reserved_micro_cny"] = 3000000
    elif mutation == "calls":
        rows[3]["calls"] = 2
    elif mutation == "duplicate":
        rows.insert(3, dict(rows[2]))
    elif mutation == "bool_usage":
        rows[2]["completion_tokens"] = True
    elif mutation == "cost":
        rows[2]["cost_micro_cny"] += 1
    elif mutation in {"limit", "thinking"}:
        body = json.loads(requests[0]["raw_body"])
        body["max_tokens" if mutation == "limit" else "thinking"] = (
            768 if mutation == "limit" else {"type": "enabled"})
        requests[0]["raw_body"] = json.dumps(body)
        requests[0]["body_sha256"] = hashlib.sha256(requests[0]["raw_body"].encode()).hexdigest()
    elif mutation == "semantic_claim":
        summary["matrix"]["pass_scope"] = "semantic_correctness"
    elif mutation == "source":
        summary["product_candidate"]["source_sha"] = "0" * 40
    elif mutation == "summary_closed":
        summary["budget"]["halted"] = True
    elif mutation == "response_write":
        responses[0]["ordinary_turn_read_only"] = False
    elif mutation == "control_call":
        summary["matrix"]["groups"][2]["controls"][0]["runtime_budget"]["model_calls"] = 1
    else:
        requests.append(requests[0])
    for name, data in (("budget.jsonl", rows), ("requests.jsonl", requests), ("responses.jsonl", responses)):
        members[name] = b"".join(json.dumps(row).encode() + b"\n" for row in data)
    members["summary.json"] = json.dumps(summary).encode() + b"\n"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        for info in infos:
            z.writestr(info, members[info.filename])
    raw = output.getvalue()
    # Deliberately bypass outer hashes to exercise the independent replay checks.
    monkeypatch.setattr(lineage, "ARCHIVE_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(lineage, "ARCHIVE_SIZE", len(raw))
    monkeypatch.setattr(lineage, "MEMBERS", {
        name: (len(value), hashlib.sha256(value).hexdigest()) for name, value in members.items()
    })
    with pytest.raises(archive.ContinuationError):
        lineage._verify_archive(raw, lineage.old.lineage_fields())



def test_lineage_rejects_reset_budget_and_bool_count():
    valid = lineage.lineage_fields()
    for key, value in (("prior_request_count", 73), ("prior_settled_micro_cny", 558124),
                       ("prior_reserved_micro_cny", 3000000), ("prior_request_count", True)):
        with pytest.raises(archive.ContinuationError):
            lineage.validate_lineage({**valid, key: value})
