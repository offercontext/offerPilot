from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from offerpilot.evaluation.core_governance import (
    Adjudication,
    ReviewBundle,
    append_adjudication,
    reviewed_trials,
)
from offerpilot.evaluation.core_contracts import TrialResultV2
from offerpilot.evaluation.registry import file_hash
from tests.evaluation.test_core_governance import manifest as manifest
from tests.evaluation.test_core_governance import policy, rows, real_manifest


def fixture(manifest, tmp_path):
    manifest = real_manifest(tmp_path)
    result = next(
        r
        for r in rows(manifest)
        if r.variant_id == "candidate" and r.execution_item_id == "E09.memory_override"
    )
    data = result.model_dump(mode="json")
    data["rules"][-1]["status"] = "unknown"
    data["verdict"] = "incomplete"
    row = TrialResultV2.model_validate(data)
    path = tmp_path / "human-label.txt"
    path.write_text("Synthetic human label fixture; not an actual human review.", encoding="utf-8")
    now = datetime.now(timezone.utc)
    label = Adjudication(
        adjudication_id="synthetic-label",
        run_id=str(row.run_id),
        trial_key=row.identity(),
        evidence_hash=row.evidence_hash,
        rule_id="quality.supported",
        rubric="quality-supported-v1",
        reviewer="synthetic human",
        role="human",
        decision="pass",
        reason="synthetic rubric result",
        created_at=now,
        expires_at=now + timedelta(days=1),
        record_ref=str(path),
        record_hash=file_hash(path),
        dimensions=dict(relevance=3, grounding=3, actionability=3, interaction_burden=3),
    )
    return manifest, row, label, policy(manifest)


def test_adjudication_is_append_only_and_derives_without_editing_raw_trial(manifest, tmp_path):
    manifest, row, label, p = fixture(manifest, tmp_path)
    saved = append_adjudication(tmp_path / "labels", label)
    before = saved.read_bytes()
    with pytest.raises(FileExistsError):
        append_adjudication(tmp_path / "labels", label)
    derived = reviewed_trials(manifest, [row], p, ReviewBundle(adjudications=[label]))
    assert row.verdict == "incomplete" and row.rules[-1].status == "unknown"
    assert derived[0].verdict == "pass"
    assert saved.read_bytes() == before


@pytest.mark.parametrize("mutation", ["stale_evidence", "conflict", "abstain", "missing_ref"])
def test_invalid_or_conflicting_human_records_remain_unknown(manifest, tmp_path, mutation):
    manifest, row, label, p = fixture(manifest, tmp_path)
    records = [label]
    if mutation == "stale_evidence":
        records = [label.model_copy(update={"evidence_hash": "0" * 64})]
    elif mutation == "conflict":
        records.append(
            label.model_copy(update={"adjudication_id": str(uuid4()), "decision": "fail"})
        )
    elif mutation == "abstain":
        records = [label.model_copy(update={"decision": "abstain"})]
    else:
        records = [label.model_copy(update={"record_ref": str(tmp_path / "absent")})]
    assert (
        reviewed_trials(manifest, [row], p, ReviewBundle(adjudications=records))[0].verdict
        == "incomplete"
    )
