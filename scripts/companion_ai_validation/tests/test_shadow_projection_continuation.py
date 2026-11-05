"""Offline-only terminal archive tamper tests; nothing can resume this run."""
from dataclasses import FrozenInstanceError
import json
import zipfile

import pytest

from experiments.shadow_input_projection import sixth_checkpoint as c
from scripts.companion_ai_validation import shadow_projection_continuation as projection

ARCHIVE = projection.EVIDENCE_PATH


def members():
    with zipfile.ZipFile(ARCHIVE) as z:
        return {name: z.read(name) for name in z.namelist()}


def encode(value):
    return json.dumps(value, ensure_ascii=False).encode() + b"\n"


def test_six_batches_real_checkpoint_is_consumed_not_authority():
    result = c.verify_checkpoint()
    assert result == c.verify_archive(ARCHIVE.read_bytes())
    assert (result.actual_calls, result.lifetime_calls, result.settled_micro_cny,
            result.reserved_micro_cny, result.batch_micro_cny) == (3, 52, 419388, 0, 5104)
    assert not result.resumable and not result.authorizes_execution
    assert not result.semantic_quality_proven
    assert result.status == "consumed_failed"
    assert result.stages_complete == 2
    assert result.artifact_id == 12954297124
    with pytest.raises(FrozenInstanceError):
        result.resumable = True
    assert not hasattr(c, "main") and not hasattr(c, "lineage_fields")


@pytest.mark.parametrize("index,key,value", [
    (0, "source_sha", "0" * 40), (0, "predecessor_helper_sha", "0" * 40),
    (0, "predecessor_source_sha", "0" * 40), (0, "predecessor_run_id", 1),
    (0, "predecessor_artifact_id", 1), (0, "predecessor_archive_sha256", "0" * 64),
    (0, "prior_request_count", 0), (0, "prior_settled_micro_cny", 0),
    (1, "reserved_micro_cny", 0), (1, "body_sha256", "0" * 64),
    (2, "prompt_tokens", 0), (2, "cost_micro_cny", 0),
    (3, "calls", 2), (5, "completion_tokens", 81),
    (8, "reserved_micro_cny", 3_000_000), (8, "event", "unknown"),
    (9, "event", "stage_complete"), (9, "halted", False),
    (9, "lifetime_request_count", 51), (9, "lifetime_settled_micro_cny", 0),
    (9, "lifetime_reserved_micro_cny", 3_000_000), (9, "reserved_micro_cny", False),
    (9, "price_valid_until", 9999999999), (9, "unknown_micro_cny", 0),
])
def test_exact_ledger_rejects_every_mutation(index, key, value):
    data = members()
    rows = [json.loads(line) for line in data["budget.jsonl"].splitlines()]
    rows[index][key] = value
    data["budget.jsonl"] = b"".join(encode(row) for row in rows)
    with pytest.raises(c.CheckpointError):
        c.replay_members(data)


@pytest.mark.parametrize("mutation", ["pass", "retry", "stage3", "request", "response",
                                        "http", "duplicate", "missing", "unclosed"])
def test_payload_summary_and_terminal_failures(mutation):
    data = members()
    summary = json.loads(data["summary.json"])
    if mutation == "pass":
        summary["matrix"]["passed"] = True
    elif mutation == "retry":
        summary["matrix"]["cases"][3]["status"] = "passed"
    elif mutation == "stage3":
        summary["budget"]["stages_complete"] = 3
    elif mutation == "missing":
        del data["requests.jsonl"]
    elif mutation == "unclosed":
        data["budget.jsonl"] = b"\n".join(data["budget.jsonl"].splitlines()[:-1]) + b"\n"
    else:
        key = "requests.jsonl" if mutation in ("request", "duplicate") else "responses.jsonl"
        rows = [json.loads(line) for line in data[key].splitlines()]
        if mutation == "request":
            rows[0]["raw_body"] += " "
        elif mutation == "response":
            rows[2]["body_sha256"] = "0" * 64
        elif mutation == "http":
            rows[2]["status"] = 500
        else:
            rows.append(rows[0])
        data[key] = b"".join(encode(row) for row in rows)
    data["summary.json"] = encode(summary)
    with pytest.raises(c.CheckpointError):
        c.replay_members(data)


def test_zip_identity_and_missing_member_rejected():
    raw = bytearray(ARCHIVE.read_bytes())
    raw[-1] ^= 1
    with pytest.raises(c.CheckpointError):
        c.verify_archive(bytes(raw))
    data = members()
    data["extra.txt"] = b"unexpected"
    with pytest.raises(c.CheckpointError):
        c.replay_members(data)


def test_old_history_failure_is_not_erased(monkeypatch, tmp_path):
    from scripts.companion_ai_validation import continuation as first
    monkeypatch.setattr(first, "ORIGINAL_EVIDENCE_PATH", tmp_path / "missing.zip")
    with pytest.raises(first.ContinuationError):
        c.verify_checkpoint()


def test_fifth_failed_history_is_still_mandatory(monkeypatch, tmp_path):
    from scripts.companion_ai_validation import shadow_quote_continuation as fifth
    monkeypatch.setattr(fifth, "EVIDENCE_PATH", tmp_path / "missing.zip")
    with pytest.raises(fifth.ContinuationError):
        c.verify_checkpoint()


def test_projection_inherits_exact_six_batch_terminal_and_new_identity():
    previous = projection.verified_predecessor()
    assert (previous.request_count, previous.settled_micro_cny,
            previous.reserved_micro_cny) == (52, 419388, 0)
    lineage = projection.lineage_fields()
    assert lineage["correction_ordinal"] == 6
    assert lineage["price_valid_until"] == 1793577600
    assert lineage["budget_micro_cny"] == 20_000_000
    assert projection.RESERVATION == 3_000_000
    assert len(projection.OPERATIONS) == 6
    assert len(projection.ALL_HISTORICAL_IDENTITIES) == 12
    assert c.SOURCE_SHA in projection.ALL_HISTORICAL_IDENTITIES
    assert c.HELPER_SHA in projection.ALL_HISTORICAL_IDENTITIES
    projection.validate_lineage(lineage)


def test_no_old_admission_is_a_new_admission():
    from scripts.companion_ai_validation import shadow_quote_continuation as fifth
    from scripts.companion_ai_validation import shadow_continuation as fourth
    from scripts.companion_ai_validation import continuation as third
    for module in (third, fourth, fifth):
        with pytest.raises(projection.ContinuationError):
            projection.validate_lineage(module.lineage_fields())


def test_missing_sixth_blocks_projection(monkeypatch, tmp_path):
    monkeypatch.setattr(projection, "EVIDENCE_PATH", tmp_path / "missing.zip")
    with pytest.raises(projection.ContinuationError):
        projection.verified_predecessor()


def test_corrupt_sixth_blocks_projection(monkeypatch, tmp_path):
    path = tmp_path / "corrupt.zip"
    raw = bytearray(ARCHIVE.read_bytes())
    raw[-1] ^= 1
    path.write_bytes(raw)
    monkeypatch.setattr(projection, "EVIDENCE_PATH", path)
    with pytest.raises(projection.ContinuationError):
        projection.lineage_fields()


def test_original_fifth_module_stays_on_its_original_batch():
    from scripts.companion_ai_validation import shadow_quote_continuation as fifth
    assert fifth.CORRECTION_ORDINAL == 5
    assert fifth.verified_predecessor().request_count == 49
