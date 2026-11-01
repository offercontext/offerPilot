"""Read-only lineage rejection tests; never contact provider or GitHub."""
import hashlib
import io
import json
import zipfile

import pytest

from scripts.companion_ai_validation import continuation as archive
from scripts.companion_ai_validation import experience_continuation as lineage


def test_eight_archives_replayed_with_exact_opening_balance(monkeypatch):
    reads = set()
    original = archive._read_evidence

    def captured(path, size):
        reads.add(path)
        return original(path, size)

    monkeypatch.setattr(archive, "_read_evidence", captured)
    predecessor = lineage.verified_predecessor()
    assert (predecessor.request_count, predecessor.settled_micro_cny,
            predecessor.reserved_micro_cny) == (61, 458256, 0)
    assert len(reads) == 8
    assert lineage.CORRECTION_ORDINAL == 8
    assert lineage.PRICE_VALID_UNTIL == 1793664000
    assert lineage.OPERATIONS == tuple(f"x{i:02d}" for i in range(1, 13))
    assert lineage.OPERATIONS_SHA256 != lineage.old.OPERATIONS_SHA256


@pytest.mark.parametrize("generation", range(8))
def test_damage_to_any_predecessor_blocks_new_batch(monkeypatch, generation):
    original = archive._read_evidence
    seen = []

    def corrupted(path, size):
        if path not in seen:
            seen.append(path)
        raw = original(path, size)
        if seen.index(path) == generation:
            return raw[:-1] + bytes([raw[-1] ^ 1])
        return raw

    monkeypatch.setattr(archive, "_read_evidence", corrupted)
    with pytest.raises(archive.ContinuationError):
        lineage.verified_predecessor()


@pytest.mark.parametrize("mutation", ["balance", "count", "halt", "reserved", "calls", "duplicate"])
def test_eighth_ledger_replay_not_merely_archive_hash(monkeypatch, mutation):
    with zipfile.ZipFile(lineage.EVIDENCE_PATH) as z:
        infos = z.infolist()
        members = {info.filename: z.read(info) for info in infos}
    rows = [json.loads(row) for row in members["budget.jsonl"].splitlines()]
    if mutation == "balance":
        rows[2]["settled_micro_cny"] += 1
    elif mutation == "count":
        rows[2]["lifetime_request_count"] -= 1
    elif mutation == "halt":
        rows[2]["halted"] = True
    elif mutation == "reserved":
        rows[-1]["reserved_micro_cny"] = 3000000
    elif mutation == "calls":
        rows[3]["calls"] = 2
    else:
        rows.insert(3, dict(rows[2]))
    members["budget.jsonl"] = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        for info in infos:
            z.writestr(info, members[info.filename])
    raw = output.getvalue()
    # Bypass only the byte pins to exercise independent semantic ledger replay.
    monkeypatch.setattr(lineage, "ARCHIVE_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(lineage, "ARCHIVE_SIZE", len(raw))
    monkeypatch.setattr(lineage, "MEMBERS", {
        name: (len(value), hashlib.sha256(value).hexdigest()) for name, value in members.items()
    })
    with pytest.raises(archive.ContinuationError):
        lineage._verify_archive(raw, lineage.old.lineage_fields())


def test_lineage_rejects_old_opening_balance_and_bool_counts():
    valid = lineage.lineage_fields()
    for key, value in (("prior_request_count", 58), ("prior_settled_micro_cny", 429280),
                       ("prior_reserved_micro_cny", 3000000), ("prior_request_count", True)):
        with pytest.raises(archive.ContinuationError):
            lineage.validate_lineage({**valid, key: value})


def test_preserves_failed_third_case_and_two_closed_stages():
    with zipfile.ZipFile(lineage.EVIDENCE_PATH) as z:
        summary = json.loads(z.read("summary.json"))
        rows = [json.loads(row) for row in z.read("budget.jsonl").splitlines()]
    assert summary["matrix"]["passed"] is False
    assert summary["matrix"]["error_code"] == "semantic_disagreement"
    assert summary["budget"]["stages_complete"] == 2
    assert [row["operation"] for row in rows if row["event"] == "stage_complete"] == ["t01", "t02"]
    assert rows[-1]["event"] == "closed" and rows[-1]["halted"] is True
    assert rows[-1]["reserved_micro_cny"] == 0
    assert lineage.verified_predecessor().request_count == 61


@pytest.mark.parametrize("mutation", ["passed", "semantic_quality", "not_attempted", "stage_complete"])
def test_eighth_semantic_failure_cannot_be_laundered(monkeypatch, mutation):
    with zipfile.ZipFile(lineage.EVIDENCE_PATH) as z:
        infos = z.infolist()
        members = {info.filename: z.read(info) for info in infos}
    summary = json.loads(members["summary.json"])
    if mutation == "passed":
        summary["matrix"]["passed"] = True
    elif mutation == "semantic_quality":
        summary["experiment"]["semantic_quality_established"] = True
    elif mutation == "not_attempted":
        summary["experiment"]["not_attempted"] = 0
    else:
        summary["budget"]["stages_complete"] = 3
    members["summary.json"] = json.dumps(summary).encode() + b"\n"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        for info in infos:
            z.writestr(info, members[info.filename])
    raw = output.getvalue()
    monkeypatch.setattr(lineage, "ARCHIVE_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(lineage, "ARCHIVE_SIZE", len(raw))
    monkeypatch.setattr(lineage, "MEMBERS", {
        name: (len(value), hashlib.sha256(value).hexdigest()) for name, value in members.items()
    })
    with pytest.raises(archive.ContinuationError):
        lineage._verify_archive(raw, lineage.old.lineage_fields())
