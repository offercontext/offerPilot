import pytest

from offerpilot.evaluation.contracts import EvidenceBundle, RuleResult
from offerpilot.evaluation.graders import grade_trial
from offerpilot.evaluation.reporting import aggregate
from tests.evaluation.test_contracts import manifest, plan


def test_failure_survives_independent_grader_exception() -> None:
    def broken(_: EvidenceBundle) -> RuleResult:
        raise RuntimeError("lost tail")

    p = plan(["wrong_object", "tail"])
    result = grade_trial(p, EvidenceBundle(), {
        "wrong_object": lambda _: RuleResult(rule_id="wrong_object", status="fail",
                                              reason_code="wrong_object", evidence_refs=["dml:1"]),
        "tail": broken,
    }, execution_status="eval_error")
    assert result.verdict == "fail"
    assert result.known_failures == ["wrong_object"]
    assert result.unknowns == ["tail"]
    assert aggregate(manifest(p), [result])["exit_code"] == 1


def test_frozen_denominator_and_required_rule() -> None:
    p = plan()
    summary = aggregate(manifest(p), [])
    assert summary["planned"] == 1 and summary["counts"]["incomplete"] == 1
    assert summary["trials"][0]["execution_status"] == "not_run"
    result = grade_trial(p, EvidenceBundle(), {})
    assert result.verdict == "incomplete"
    assert summary["release_eligible"] is False
    with pytest.raises(ValueError, match="duplicate"):
        aggregate(manifest(p), [result, result])


def test_required_rule_cannot_be_downgraded() -> None:
    p = plan(["r"])
    r = grade_trial(p, EvidenceBundle(), {
        "r": lambda _: RuleResult(rule_id="r", required=False, status="not_applicable",
                                   reason_code="disabled")})
    assert r.verdict == "incomplete"
    forged = r.model_copy(update={"rules": [RuleResult(rule_id="r", required=False,
                    status="not_applicable", reason_code="disabled")], "verdict": "pass", "unknowns": []})
    with pytest.raises(ValueError, match="downgraded"):
        aggregate(manifest(p), [forged])


@pytest.mark.parametrize("bad", [{"termination_source": []}, {"termination_events": True}, {"consumption": []}])
def test_bad_result_metadata_cannot_swallow_independent_failure(bad: dict) -> None:
    p = plan(["r"])
    result = grade_trial(p, EvidenceBundle(facts=bad), {"r": lambda _: RuleResult(
        rule_id="r", status="fail", reason_code="actual_business_failure")})
    assert result.verdict == "fail" and result.execution_status == "eval_error"
    assert aggregate(manifest(p), [result])["exit_code"] == 1


def test_recovery_receipt_cannot_claim_another_conversation() -> None:
    from offerpilot.evaluation.graders.p0 import saved_receipt
    operation = {"id": "original", "conversation_id": 1, "status": "committed",
                 "terminal_payload_sha256": "digest", "confirmation_strategy_fingerprint": "strategy"}
    messages = [{"role": "tool", "content": "effect"}, {"role": "assistant", "content": "saved"}]
    responses = [{"message": "saved", "operation_id": "original", "conversation_id": 2,
                  "write_status": "committed", "replayed": True}] * 2
    evidence = EvidenceBundle(milestones={"persisted": {"operation": operation, "messages": messages}},
        facts={"operation": operation, "messages": messages, "replays": responses})
    assert saved_receipt(evidence).status == "fail"
