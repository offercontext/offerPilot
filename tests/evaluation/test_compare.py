from offerpilot.evaluation.contracts import EvidenceBundle, RuleResult
from offerpilot.evaluation.graders import grade_trial
from offerpilot.evaluation.compare import compare
from tests.evaluation.test_contracts import plan, manifest
from uuid import uuid4
from offerpilot.evaluation.registry import fault_detected


def test_persistent_failure_missing_and_incomparable() -> None:
    p = plan(["r"])
    m = manifest(p)
    m = m.model_copy(update={"plan_kind": "p0"})
    runtime = {"lock_hash": "f" * 64, "target_runtime": {"python": "synthetic", "sqlite": "synthetic", "dependencies": {}}}
    m = m.model_copy(update={"variants": {"candidate": runtime}})
    pb = p.model_copy(update={"variant_id": "baseline"})
    mb = m.model_copy(update={"variants": {"baseline": runtime}, "planned_trials": [pb]})
    failed = grade_trial(p, EvidenceBundle(), {"r": lambda _: RuleResult(rule_id="r", status="fail", reason_code="bad")})
    baseline_failed = failed.model_copy(update={"variant_id": "baseline"})
    report = compare(mb, [baseline_failed], m, [failed])
    assert report["exit_code"] == 1 and report["groups"][0]["classification"] == "persistent_failure"
    report = compare(mb, [baseline_failed], m, [])
    assert report["exit_code"] == 1 and report["groups"][0]["classification"] == "incomplete"
    changed = m.model_copy(update={"grader_version": "other"})
    report = compare(m, [], changed, [])
    assert report["exit_code"] == 2 and not report["groups"][0]["comparable"]


def test_same_single_run_is_not_ab_but_independent_single_runs_are_supported() -> None:
    p = plan(["r"])
    m = manifest(p)
    m = m.model_copy(update={"plan_kind": "p0"})
    m = m.model_copy(update={"variants": {"candidate": {"lock_hash": "f" * 64,
        "target_runtime": {"python": "synthetic", "sqlite": "synthetic", "dependencies": {}}}}})
    passed = grade_trial(p, EvidenceBundle(), {"r": lambda _: RuleResult(rule_id="r", status="pass", reason_code="ok")})
    assert compare(m, [passed], m, [passed])["exit_code"] == 2
    other_id = uuid4()
    other = m.model_copy(update={"run_id": other_id,
                                  "planned_trials": [p.model_copy(update={"run_id": other_id})]})
    result = passed.model_copy(update={"run_id": other_id})
    assert compare(m, [passed], other, [result])["exit_code"] == 0


def test_unreached_fault_or_unrelated_failure_is_not_canary_detection() -> None:
    assert not fault_detected("wrong_target", {"faulted": False, "known_failures": ["business.no_unexpected_delta"]})
    assert not fault_detected("wrong_target", {"faulted": True, "known_failures": ["execution.expected_outcome"]})
    assert fault_detected("wrong_target", {"faulted": True, "known_failures": ["business.no_unexpected_delta"]})
