"""Exact failed fifth batch cannot become free money or a successful review."""
from __future__ import annotations

import base64
import json
import zipfile

import pytest

from scripts.companion_ai_validation import continuation as first
from scripts.companion_ai_validation import shadow_continuation as old
from scripts.companion_ai_validation import shadow_quote_continuation as new


def members():
    with zipfile.ZipFile(new.EVIDENCE_PATH) as z:
        return {name: z.read(name) for name in z.namelist()}


def encode(value):
    return json.dumps(value, ensure_ascii=False).encode() + b"\n"


def test_real_failed_archive_replayed_without_repair():
    p = new.verified_predecessor()
    assert (p.request_count, p.settled_micro_cny, p.reserved_micro_cny) == (49, 414284, 0)
    state = new._verify_members(members())
    assert state["halted"] is True
    assert state["batch_request_count"] == 1
    candidate = new.lineage_fields()
    assert candidate["correction_ordinal"] == 5
    assert candidate["price_valid_until"] == 1793577600
    assert new.BUDGET == 20_000_000 and new.RESERVATION == 3_000_000
    assert len(new.ALL_HISTORICAL_IDENTITIES) == 10
    assert new.PREDECESSOR_SOURCE_SHA in new.ALL_HISTORICAL_IDENTITIES
    new.validate_lineage(candidate)
    with pytest.raises(first.ContinuationError):
        new.validate_lineage(old.lineage_fields())


@pytest.mark.parametrize("index,key,value", [
    (0, "prior_settled_micro_cny", 0), (0, "prior_request_count", 0),
    (1, "reserved_micro_cny", 0), (1, "body_sha256", "0" * 64),
    (2, "cost_micro_cny", 0), (2, "prompt_tokens", 588),
    (2, "completion_tokens", 146), (2, "reserved_micro_cny", 3_000_000),
    (3, "event", "stage_complete"), (3, "halted", False),
    (3, "batch_request_count", 6), (3, "lifetime_request_count", 48),
    (3, "reserved_micro_cny", 3_000_000), (3, "reserved_micro_cny", False),
    (3, "price_valid_until", new.PRICE_VALID_UNTIL),
])
def test_accounting_mutation_rejected(index, key, value):
    data = members()
    rows = [json.loads(line) for line in data["budget.jsonl"].splitlines()]
    rows[index][key] = value
    data["budget.jsonl"] = b"".join(encode(row) for row in rows)
    with pytest.raises(first.ContinuationError):
        new._verify_members(data)


@pytest.mark.parametrize("mutation", ["pass", "attempt", "stage", "request", "response", "extra"])
def test_summary_and_evidence_cross_checks(mutation):
    data = members()
    summary = json.loads(data["summary.json"])
    if mutation == "pass":
        summary["matrix"]["passed"] = True
    elif mutation == "attempt":
        summary["matrix"]["cases"][1]["status"] = "passed"
    elif mutation == "stage":
        summary["budget"]["stages_complete"] = 1
    elif mutation == "request":
        row = json.loads(data["requests.jsonl"])
        row["raw_body"] += " "
        data["requests.jsonl"] = encode(row)
    elif mutation == "response":
        row = json.loads(data["responses.jsonl"])
        envelope = json.loads(base64.b64decode(row["raw_base64"]))
        envelope["usage"]["completion_tokens"] += 1
        row["raw_base64"] = base64.b64encode(encode(envelope)).decode()
        data["responses.jsonl"] = encode(row)
    else:
        data["requests.jsonl"] *= 2
    data["summary.json"] = encode(summary)
    with pytest.raises(first.ContinuationError):
        new._verify_members(data)


def test_missing_any_earlier_chain_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(first, "ORIGINAL_EVIDENCE_PATH", tmp_path / "absent.zip")
    with pytest.raises(first.ContinuationError):
        new.verified_predecessor()


def test_missing_fifth_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(new, "EVIDENCE_PATH", tmp_path / "absent.zip")
    with pytest.raises(first.ContinuationError):
        new.verified_predecessor()


def test_zip_corruption_and_extra_member_rejected():
    raw = bytearray(new.EVIDENCE_PATH.read_bytes())
    raw[-1] ^= 1
    with pytest.raises(first.ContinuationError):
        new._verify_archive(bytes(raw))
    data = members()
    data["unknown.json"] = b"{}"
    with pytest.raises(first.ContinuationError):
        new._verify_members(data)


def test_original_candidate_is_unchanged():
    assert old.CORRECTION_ORDINAL == 4
    assert old.verified_predecessor().request_count == 48
    assert first.verified_predecessor().request_count == 36
