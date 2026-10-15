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


@pytest.mark.parametrize("with_context", [False, True])
def test_review_pack_blinds_identity_preserves_audit_and_raw_safety_failures(
    manifest, tmp_path, monkeypatch, with_context
):
    m = real_manifest(tmp_path)
    actual = [r for r in rows(m) if any(rule.rule_id == "quality.supported" for rule in r.rules)]
    private = tmp_path / "private"
    actual = [r.model_copy(update={"private_directory": str(private)}) for r in actual]
    actual[0] = actual[0].model_copy(
        update={
            "verdict": "fail",
            "rules": [
                actual[0].rules[0].model_copy(update={"status": "fail"}),
                *actual[0].rules[1:],
            ],
        }
    )
    inputs = (
        captured_inputs(private, [[dict(role="system", content="position=测试开发")]])
        if with_context
        else dict(input_observations=[], files={})
    )
    monkeypatch.setattr(review, "read_run", lambda _: (m, actual))
    monkeypatch.setattr(
        review,
        "load",
        lambda *_: dict(
            errors={},
            facts=dict(answer="two synthetic sentences"),
            http=[dict(body=dict(message="Explicit synthetic user request"))],
            **inputs,
        ),
    )
    out = tmp_path / "pack"
    private_audit = tmp_path / "maintainer/audit.json"
    summary = review.blinded_pack([tmp_path / "run"], out, private_audit)
    pack = json.loads((out / "review-pack.json").read_bytes())
    audit = json.loads(private_audit.read_bytes())
    assert not (out / "audit-map.json").exists()
    assert summary["human_labels"] == 0
    assert pack["schema_version"] == audit["schema_version"] == 2
    assert all("trial_key" not in s and "variant_id" not in s for s in pack["samples"])
    assert all(s["user_requests"] for s in pack["samples"])
    if with_context:
        assert all(
            s["model_context"]["calls"][0]["messages"][0]["content"] == "position=测试开发"
            for s in pack["samples"]
        )
        assert all(s["model_input_bindings"] for s in audit["samples"])
    else:
        assert all(
            s["model_context"] == {"status": "not_captured", "calls": []} for s in pack["samples"]
        )
    assert {s["sample_id"] for s in pack["samples"]} == {s["sample_id"] for s in audit["samples"]}
    assert audit["pack_hash"] == file_hash(out / "review-pack.json")
    assert actual[0].verdict == "fail" and all(r.verdict == "pass" for r in actual[1:])
    assert sum(s["raw_verdict"] == "fail" for s in audit["samples"]) == 1
    assert all("raw_verdict" not in s and "known_fail_rules" not in s for s in pack["samples"])
    with pytest.raises(ValueError):
        review.blinded_pack([tmp_path / "run"], out, private_audit)
    with pytest.raises(ValueError):
        review.blinded_pack(
            [tmp_path / "run"], tmp_path / "new-pack", tmp_path / "new-pack/audit.json"
        )


def captured_inputs(private, messages):
    private.mkdir(exist_ok=True)
    evidence = dict(input_observations=[], files={})
    for index, value in enumerate(messages, 1):
        path = private / f"request-{index}.json"
        path.write_text(
            json.dumps(
                dict(
                    messages=value,
                    model="hidden-model",
                    headers={"Authorization": "hidden-credential"},
                )
            ),
            encoding="utf-8",
        )
        evidence["files"][path.name] = {"sha256": file_hash(path)}
        evidence["input_observations"].append(
            dict(
                category="final_input",
                request_file=path.name,
                payload_hash=file_hash(path),
                epoch=1,
                seq=index,
                identity={"model_call_id": f"private-call-{index}"},
            )
        )
    return evidence


def test_review_context_preserves_actual_job_and_history_without_provider_identity(tmp_path):
    messages = [
        dict(role="system", content="company=云岚数据; position=测试开发; jd_version_id=none"),
        dict(role="user", content="给出面试准备建议"),
    ]
    after = [
        *messages,
        dict(role="assistant", content="上一轮回答"),
        dict(role="user", content="撤回偏好后再次回答"),
    ]
    evidence = captured_inputs(tmp_path, [messages, after])
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    context, bindings = review.model_context(tmp_path, evidence)
    assert context == dict(
        status="captured", calls=[dict(number=1, messages=messages), dict(number=2, messages=after)]
    )
    public = json.dumps(context)
    assert not any(
        value in public
        for value in ("hidden-model", "hidden-credential", "private-call-", str(tmp_path))
    )
    assert [b["payload_hash"] for b in bindings] == [
        o["payload_hash"] for o in evidence["input_observations"]
    ]
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize(
    "damage", ["unbound", "changed", "wrong_payload", "escape", "bad_messages"]
)
def test_review_context_refuses_unbound_or_corrupt_wire_input(tmp_path, damage):
    evidence = captured_inputs(tmp_path, [[dict(role="user", content="actual request")]])
    observation = evidence["input_observations"][0]
    path = tmp_path / observation["request_file"]
    if damage == "unbound":
        evidence["files"].clear()
    elif damage == "changed":
        path.write_text('{"messages": []}', encoding="utf-8")
    elif damage == "wrong_payload":
        observation["payload_hash"] = "0" * 64
    elif damage == "escape":
        observation["request_file"] = "../request-1.json"
    else:
        path.write_text('{"messages": "not captured messages"}', encoding="utf-8")
        evidence["files"][path.name]["sha256"] = observation["payload_hash"] = file_hash(path)
    with pytest.raises(ValueError):
        review.model_context(tmp_path, evidence)


def test_review_context_does_not_invent_model_knowledge_from_database_fixture(tmp_path):
    context, bindings = review.model_context(
        tmp_path,
        dict(
            input_observations=[],
            files={},
            initial={"applications": [{"company_name": "not sent", "position_name": "not sent"}]},
        ),
    )
    assert context == dict(status="not_captured", calls=[])
    assert bindings == []
