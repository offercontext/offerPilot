"""Offline fourth-archive chain: reject mutations, unknowns and lineage resets."""
from __future__ import annotations

import hashlib
import json
import zipfile

import pytest

from scripts.companion_ai_validation import continuation as old
from scripts.companion_ai_validation import shadow_continuation as shadow


def ledger() -> list[dict]:
    with zipfile.ZipFile(shadow.EVIDENCE_PATH) as archive:
        return [json.loads(line) for line in archive.read("budget.jsonl").splitlines()]


def raw(rows: list[dict]) -> bytes:
    return b"\n".join(json.dumps(row, separators=(",", ":")).encode() for row in rows) + b"\n"


def test_four_archives_replay_and_exact_balance():
    previous = shadow.verified_predecessor()
    assert (previous.request_count, previous.settled_micro_cny, previous.reserved_micro_cny) == (
        48, 411934, 0)
    assert previous.run_id == 40184557896
    assert hashlib.sha256(shadow.EVIDENCE_PATH.read_bytes()).hexdigest() == shadow.ARCHIVE_SHA256
    with zipfile.ZipFile(shadow.EVIDENCE_PATH) as archive:
        assert set(archive.namelist()) == {"budget.jsonl", "summary.json", "responses.jsonl"}


def test_candidate_lineage_and_old_path_preserved():
    candidate = shadow.lineage_fields()
    assert candidate["correction_ordinal"] == 4
    assert candidate["prior_request_count"] == 48
    assert candidate["price_valid_until"] > old.PRICE_VALID_UNTIL
    assert candidate["budget_micro_cny"] == old.BUDGET == 20_000_000
    assert shadow.RESERVATION == old.RESERVATION == 3_000_000
    assert shadow.OPERATIONS == ("s01", "s02", "s03", "s04", "s05", "s06")
    assert old.lineage_fields()["prior_request_count"] == 36
    assert old.CORRECTION_ORDINAL == 3
    shadow.validate_lineage(candidate)
    with pytest.raises(old.ContinuationError):
        shadow.validate_lineage(old.lineage_fields())


@pytest.mark.parametrize("index,key,value", [
    (0, "prior_request_count", 0),
    (0, "prior_settled_micro_cny", 0),
    (1, "reserved_micro_cny", 0),
    (1, "body_sha256", "not-a-hash"),
    (2, "cost_micro_cny", 0),
    (2, "completion_tokens", True),
    (2, "completion_tokens", 513),
    (2, "settled_micro_cny", 0),
    (3, "calls", 2),
    (-1, "reserved_micro_cny", 3_000_000),
    (-1, "lifetime_request_count", 0),
    (-1, "halted", False),
    (-1, "event", "halted"),
])
def test_replay_rejects_accounting_mutations(index, key, value):
    rows = ledger()
    rows[index][key] = value
    with pytest.raises(old.ContinuationError):
        shadow._replay_ledger(raw(rows))


def test_archive_corruption_fails():
    data = bytearray(shadow.EVIDENCE_PATH.read_bytes())
    data[-1] ^= 1
    with pytest.raises(old.ContinuationError):
        shadow._verify_archive(bytes(data))


def test_missing_fourth_archive_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(shadow, "EVIDENCE_PATH", tmp_path / "absent.zip")
    with pytest.raises(old.ContinuationError):
        shadow.verified_predecessor()


def test_missing_prior_archive_still_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(old, "ORIGINAL_EVIDENCE_PATH", tmp_path / "absent.zip")
    with pytest.raises(old.ContinuationError):
        shadow.verified_predecessor()


def test_all_expired_run_identities_are_forbidden():
    assert len(shadow.ALL_HISTORICAL_IDENTITIES) == 8
    assert shadow.PREDECESSOR_HELPER_SHA in shadow.ALL_HISTORICAL_IDENTITIES
    assert shadow.PREDECESSOR_SOURCE_SHA in shadow.ALL_HISTORICAL_IDENTITIES
