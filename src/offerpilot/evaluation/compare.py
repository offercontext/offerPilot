from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from offerpilot.evaluation.contracts import RunManifest, TrialResult
from offerpilot.evaluation.reporting import aggregate
from offerpilot.evaluation.registry import fault_detected


def compare(manifest_a: RunManifest, results_a: list[TrialResult],
            manifest_b: RunManifest, results_b: list[TrialResult]) -> dict[str, Any]:
    a, b = aggregate(manifest_a, results_a), aggregate(manifest_b, results_b)
    independent_single = (manifest_a.run_id != manifest_b.run_id
                          and set(manifest_a.variants) == set(manifest_b.variants) == {"candidate"})
    def choose(summary: dict[str, Any], preferred: str) -> dict[tuple[Any, ...], dict[str, Any]]:
        variants = {r["variant_id"] for r in summary["trials"]}
        variant = preferred if preferred in variants else "candidate" if independent_single else None
        return {(r["execution_item_id"], r["mode"], r["repeat_index"]): r
                for r in summary["trials"] if r["variant_id"] == variant}
    left, right = choose(a, "baseline"), choose(b, "candidate")
    bindings_a = {(p.execution_item_id, p.mode, p.repeat_index): p for p in manifest_a.planned_trials}
    bindings_b = {(p.execution_item_id, p.mode, p.repeat_index): p for p in manifest_b.planned_trials}
    rows = []
    global_equal = all(getattr(manifest_a, k) == getattr(manifest_b, k) for k in (
        "suite_hash", "bridge_version", "observer_version", "grader_version", "evaluator_hash", "allowed_variables", "timezone"))
    def runtime(summary: dict[str, Any], manifest: RunManifest, preferred: str) -> dict[str, Any]:
        variant = preferred if preferred in manifest.variants else "candidate" if independent_single else None
        details = manifest.variants.get(variant, {}) if variant else {}
        return {k: details[k] for k in ("lock_hash", "target_runtime") if k in details}
    left_runtime = runtime(a, manifest_a, "baseline")
    right_runtime = runtime(b, manifest_b, "candidate")
    global_equal = (global_equal and set(left_runtime) == {"lock_hash", "target_runtime"}
                    and left_runtime == right_runtime
                    and {k: v for k, v in manifest_a.environment.items() if k != "executable"}
                    == {k: v for k, v in manifest_b.environment.items() if k != "executable"})
    for key in sorted(set(left) | set(right)):
        x, y = left.get(key), right.get(key)
        pa, pb = bindings_a.get(key), bindings_b.get(key)
        comparable = bool(global_equal and x and y and pa and pb and all(
            getattr(pa, k) == getattr(pb, k) for k in (
                "case_id", "case_version", "spec_hash", "fixture_hash", "coverage_kind", "required_rules")))
        vx, vy = x["verdict"] if x else "incomplete", y["verdict"] if y else "incomplete"
        category = "incomparable" if not comparable else "incomplete" if "incomplete" in {vx, vy} else {
            ("pass", "fail"): "new_failure", ("fail", "pass"): "fixed",
            ("fail", "fail"): "persistent_failure", ("pass", "pass"): "passing",
        }[(vx, vy)]
        rows.append({"execution_item_id": key[0], "mode": key[1], "repeat_index": key[2],
                     "coverage_kind": pa.coverage_kind if pa else pb.coverage_kind if pb else None,
                     "comparable": comparable, "classification": category,
                     "baseline": x, "candidate": y})
    known = any(r[s] and r[s]["known_failures"] for r in rows for s in ("baseline", "candidate"))
    incomparable = any(not r["comparable"] for r in rows)
    incomplete = any(r["classification"] == "incomplete" for r in rows)
    diagnostic = manifest_a.plan_kind == "diagnostic" or manifest_b.plan_kind == "diagnostic"
    detected = any(r["candidate"] and fault_detected(
        manifest_b.variants.get("candidate", {}).get("fault", "none"), r["candidate"]) for r in rows)
    return {"baseline_run_id": str(manifest_a.run_id), "candidate_run_id": str(manifest_b.run_id),
            "diagnostic": diagnostic, "canary_not_detected": diagnostic and not detected,
            "groups": rows, "exit_code": 1 if known else 2 if incomparable or diagnostic else 3 if incomplete else 0,
            "release_eligible": False, "repeats": 1, "stability_claim": False}


def read_run(path: Path) -> tuple[RunManifest, list[TrialResult]]:
    from offerpilot.evaluation.evidence.storage import load
    from offerpilot.evaluation.graders import grade_trial
    from offerpilot.evaluation.graders.p0 import GRADERS
    from offerpilot.evaluation.runner import evaluator_hash
    root = path if path.is_dir() else path.parent
    manifest = RunManifest.model_validate_json((root / "run-manifest.json").read_bytes())
    if manifest.evaluator_hash != evaluator_hash():
        raise ValueError("fixed evaluator snapshot unavailable; do not regrade with different code")
    results = []
    for plan in manifest.planned_trials:
        identifier = f"{plan.variant_id}-{plan.execution_item_id}-{plan.repeat_index}"
        result_path = root / "trials" / identifier / "result.json"
        private = root / "private" / identifier
        result = None
        if result_path.is_file():
            try:
                result = TrialResult.model_validate(json.loads(result_path.read_bytes()))
                if (result.identity() != plan.identity()
                        or (result.case_id, result.case_version) != (plan.case_id, plan.case_version)
                        or any(not rule.required or rule.status == "not_applicable" for rule in result.rules
                               if rule.rule_id in plan.required_rules)):
                    result = None
            except (OSError, ValueError, TypeError):
                # A corrupt row remains incomplete in the frozen denominator;
                # intact failures in other rows still determine the exit code.
                result = None
        if private.is_dir():
            evidence = load(private)
            try:
                identity = json.loads((private / ".eval-trial.json").read_bytes())
                expected_identity = plan.model_dump(mode="json", include={
                    "run_id", "variant_id", "execution_item_id", "mode", "repeat_index"})
                variant = manifest.variants[plan.variant_id]
                if identity != expected_identity or any(
                    h.get("checkout_sha") != variant["commit"]
                    or Path(h["interpreter"]).resolve() != Path(variant["interpreter"]).resolve()
                    or {k: h[k] for k in ("python", "sqlite", "dependencies")} != variant["target_runtime"]
                    for h in evidence.facts.get("handshakes", [])
                ):
                    continue
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if not evidence.facts.get("handshakes"):
                evidence = evidence.model_copy(update={"fragment_errors": {
                    **evidence.fragment_errors, "identity": "missing_target_handshake"}})
            # Score intact fragments independently on every read; a stale pass
            # cannot survive deletion of its supporting private evidence.
            results.append(grade_trial(plan, evidence, GRADERS,
                execution_status=result.execution_status if result else "eval_error",
                faulted=bool(evidence.facts.get("fault_witnesses"))))
    return manifest, results
