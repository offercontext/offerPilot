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
