from datetime import datetime, timedelta, timezone
from uuid import uuid4
import json

import pytest

from offerpilot.evaluation import core_review as review
from offerpilot.evaluation.core_governance import Adjudication
from offerpilot.evaluation.registry import file_hash
from tests.evaluation.test_core_governance import real_manifest, rows
from tests.evaluation.test_core_governance import manifest as manifest


@pytest.mark.parametrize("count,negative", [(0, False), (19, True), (20, False), (20, True)])
def test_judge_requires_twenty_unique_bound_human_positive_and_negative_records(
    tmp_path, count, negative
):
    # Synthetic label fixtures exercise the gate; they are never exported as human evidence.
    path = tmp_path / "synthetic-label.txt"
    path.write_text("Synthetic unit-test label; no actual human attestation.", encoding="utf-8")
    now = datetime.now(timezone.utc)
    labels = []
    evidence = {}
    for i in range(count):
        key = (str(uuid4()), "candidate", "E09.memory_override", "real", 1)
        evidence[key] = "1" * 64
        labels.append(
            Adjudication(
                adjudication_id=str(uuid4()),
                run_id=key[0],
                trial_key=key,
                evidence_hash=evidence[key],
                rule_id="quality.supported",
                rubric="quality-supported-v1",
                reviewer="synthetic human fixture",
                role="human",
                decision="fail" if negative and i == 0 else "pass",
                reason="Synthetic fixture",
                created_at=now,
                expires_at=now + timedelta(days=1),
                record_ref=str(path),
                record_hash=file_hash(path),
                dimensions=dict(relevance=2, grounding=2, actionability=2, interaction_burden=2),
            )
        )
        record = labels[-1]
        ref = tmp_path / f"structured-label-{i}.json"
        fields = {
            "adjudication_id",
            "run_id",
            "trial_key",
            "evidence_hash",
            "rubric",
            "reviewer",
            "decision",
            "reason",
            "dimensions",
            "created_at",
            "expires_at",
        }
        content = {k: v for k, v in record.model_dump(mode="json").items() if k in fields}
        content.update(schema_version=1, kind="human-rubric-record")
        ref.write_text(json.dumps(content), encoding="utf-8")
        labels[-1] = record.model_copy(update=dict(record_ref=str(ref), record_hash=file_hash(ref)))
    result = review.judge_eligibility(labels, evidence)
    assert result["eligible_for_calibration"] == (count == 20 and negative)
    assert result["network_requests"] == 0 and not result["judge_release_enabled"]
    if labels:
        assert not review.judge_eligibility([labels[0]] * 20, evidence)["eligible_for_calibration"]
        assert not review.judge_eligibility(labels, {})["eligible_for_calibration"]
        assert not review.judge_eligibility(
            [labels[0].model_copy(update=dict(record_ref=str(path), record_hash=file_hash(path)))]
            + labels[1:],
            evidence,
        )["eligible_for_calibration"]
        assert not review.judge_eligibility(
            [labels[0].model_copy(update=dict(run_id=str(uuid4())))] + labels[1:], evidence
        )["eligible_for_calibration"]
        (tmp_path / "structured-label-0.json").write_text("Changed reference", encoding="utf-8")
        assert not review.judge_eligibility(labels, evidence)["eligible_for_calibration"]


def test_review_pack_blinds_identity_preserves_audit_and_raw_safety_failures(
    manifest, tmp_path, monkeypatch
):
    m = real_manifest(tmp_path)
    actual = [r for r in rows(m) if any(rule.rule_id == "quality.supported" for rule in r.rules)]
    monkeypatch.setattr(review, "read_run", lambda _: (m, actual))
    monkeypatch.setattr(
        review,
        "load",
        lambda *_: dict(
            errors={},
            facts=dict(answer="two synthetic sentences"),
            http=[dict(body=dict(message="Explicit synthetic user request"))],
            input_observations=[],
        ),
    )
    out = tmp_path / "pack"
    private_audit = tmp_path / "maintainer/audit.json"
    summary = review.blinded_pack([tmp_path / "run"], out, private_audit)
    pack = json.loads((out / "review-pack.json").read_bytes())
    audit = json.loads(private_audit.read_bytes())
    assert not (out / "audit-map.json").exists()
    assert summary["human_labels"] == 0
    assert all("trial_key" not in s and "variant_id" not in s for s in pack["samples"])
    assert all(s["user_requests"] for s in pack["samples"])
    assert {s["sample_id"] for s in pack["samples"]} == {s["sample_id"] for s in audit["samples"]}
    assert audit["pack_hash"] == file_hash(out / "review-pack.json")
    assert all(r.verdict == "pass" for r in actual)
    with pytest.raises(ValueError):
        review.blinded_pack([tmp_path / "run"], out, private_audit)
    with pytest.raises(ValueError):
        review.blinded_pack(
            [tmp_path / "run"], tmp_path / "new-pack", tmp_path / "new-pack/audit.json"
        )
