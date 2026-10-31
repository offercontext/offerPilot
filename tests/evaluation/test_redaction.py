from offerpilot.evaluation.contracts import EvidenceBundle, RuleResult
from offerpilot.evaluation.graders import grade_trial
from offerpilot.evaluation.reporting import aggregate
from tests.evaluation.test_contracts import plan, manifest


def test_public_summary_excludes_private_data() -> None:
    p = plan(["r"])
    evidence = EvidenceBundle(facts={"confirmation_token": "private-token", "conversation": "private-chat"})
    result = grade_trial(p, evidence, {"r": lambda _: RuleResult(rule_id="r", status="fail", reason_code="bad")})
    report = str(aggregate(manifest(p), [result]))
    assert "private-token" not in report and "private-chat" not in report
