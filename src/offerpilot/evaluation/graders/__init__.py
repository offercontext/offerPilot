from __future__ import annotations

from collections.abc import Callable

from offerpilot.evaluation.contracts import EvidenceBundle, PlannedTrial, RuleResult, TrialResult

Rule = Callable[[EvidenceBundle], RuleResult]


def grade_trial(plan: PlannedTrial, evidence: EvidenceBundle,
                graders: dict[str, Rule], *, execution_status: str = "completed",
                faulted: bool = False) -> TrialResult:
    results: list[RuleResult] = []
    for rule_id in plan.required_rules:
        try:
            result = graders[rule_id](evidence)
            if result.rule_id != rule_id or not result.required or result.status == "not_applicable":
                raise ValueError("required rule contract changed")
        except Exception as exc:
            result = RuleResult(rule_id=rule_id, status="unknown",
                                reason_code=f"grader_error:{type(exc).__name__}")
        results.append(result)
    failures = [r.rule_id for r in results if r.status == "fail"]
    unknowns = [r.rule_id for r in results if r.status == "unknown"]
    verdict = "fail" if failures else (
        "incomplete" if unknowns or execution_status != "completed" else "pass"
    )
    return TrialResult.model_validate({
        **plan.model_dump(exclude={"spec_hash", "fixture_hash", "coverage_kind", "required_rules"}),
        "execution_status": execution_status, "verdict": verdict, "rules": results,
        "known_failures": failures, "unknowns": unknowns, "faulted": faulted,
        "termination_source": evidence.facts.get("termination_source", "normal"),
        "termination_events": evidence.facts.get("termination_events", []),
        "consumption": evidence.facts.get("consumption", {}),
        "evidence_refs": list(evidence.files),
    })
