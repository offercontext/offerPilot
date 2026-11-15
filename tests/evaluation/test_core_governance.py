from datetime import datetime, timedelta, timezone
import json

import pytest

from offerpilot.evaluation import core_planning as planning
from offerpilot.evaluation.core_contracts import TrialResultV2
from offerpilot.evaluation.core_contracts import ProviderRunProfile
from offerpilot.evaluation.core_registry import case_definition, required_rules
from offerpilot.evaluation.core_runner import missing
from offerpilot.evaluation.core_governance import (
    ExperimentSpec,
    GatePolicyV2,
    ReviewBundle,
    QualityException,
    compare_core,
    gate_core,
    product_hash,
    statistics,
    variable_values,
)
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.runner import ROOT


@pytest.mark.parametrize("prefix", ["core-initial-baseline", "core-release"])
def test_latest_public_policy_binds_checked_out_suite(manifest, tmp_path, prefix):
    paths = list((ROOT / "evals/policies").glob(f"{prefix}-v*.json"))
    latest = max(paths, key=lambda path: int(path.stem.rsplit("-v", 1)[1]))
    configured = GatePolicyV2.model_validate_json(latest.read_bytes())
    suite_path = ROOT / "evals/suites/harness-core-v1.json"
    assert b"\r" not in suite_path.read_bytes(), "core suite checkout must retain LF bytes"
    assert configured.suite_hash == file_hash(suite_path) == manifest.suite_hash
    assert len(configured.absolute) == 19
    assert all(item.repeats == 3 for item in configured.absolute)
    purpose = configured.purposes[0]
    result = gate_core(
        [manifest], [rows(manifest)], configured, review(manifest, tmp_path), purpose, "enforce"
    )
    assert not result["integrity"]["errors"]
    if purpose == "initial_baseline":
        assert result["gate_satisfied"] and result["exit_code"] == 0
    else:
        assert result["exit_code"] == 3 and not result["gate_satisfied"]


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(
        planning,
        "snapshot",
        lambda path: dict(
            commit=("a" if path.name == "baseline" else "b") * 40,
            diff_hash="c" * 64,
            files_hash=digest(path.name),
            lock_hash="e" * 64,
        ),
    )
    monkeypatch.setattr(
        planning,
        "target_environment",
        lambda v: {"python": "test", "sqlite": "test", "dependencies": {}},
    )
    variants = tmp_path / "variants.json"
    variants.write_text(
        json.dumps(
            [
                dict(variant_id=name, checkout=str(tmp_path / name), interpreter="unused")
                for name in ["baseline", "candidate"]
            ]
        )
    )
    return planning.create_plan(
        ROOT / "evals/suites/harness-core-v1.json", variants, tmp_path / "run", repeat=3
    )


def rows(manifest, variant=None, failure=None):
    result = []
    for p in manifest.planned_trials:
        if variant and p.variant_id != variant:
            continue
        r = missing(p, "synthetic_test_only").model_dump(mode="json")
        for rule in r["rules"]:
            rule["status"] = (
                "fail"
                if failure == p.identity() and rule["rule_id"] == "business.authorized_deltas"
                else "pass"
            )
        r.update(
            execution_status="completed",
            verdict="fail" if failure == p.identity() else "pass",
            evidence_hash="f" * 64,
            termination_source="normal",
        )
        result.append(TrialResultV2.model_validate(r))
    return result


def real_manifest(tmp_path):
    profile = ProviderRunProfile(
        profile_id="synthetic-only",
        endpoint="https://provider.test",
        model="synthetic-model",
        credential_ref="missing-synthetic-key",
        input_cny_per_million=2,
        output_cny_per_million=8,
        price_reference="synthetic price fixture",
        price_checked_at=datetime.now(timezone.utc),
    )
    path = tmp_path / "profile.json"
    path.write_text(profile.model_dump_json(), encoding="utf-8")
    return planning.create_plan(
        ROOT / "evals/suites/harness-core-v1.json",
        tmp_path / "variants.json",
        tmp_path / "real-plan",
        mode="real",
        repeat=3,
        provider_path=path,
    )


def policy(manifest):
    return GatePolicyV2.model_validate(
        dict(
            policy_id="synthetic-policy-v1",
            version=1,
            suite_hash=manifest.suite_hash,
            purposes=["initial_baseline", "release"],
            absolute=[
                dict(
                    execution_item_id=item,
                    mode=manifest.mode,
                    case_version=case_definition(item)["case_version"],
                    coverage_kind=case_definition(item)["coverage_kind"],
                    repeats=3,
                    rules=required_rules(item, manifest.mode),
                )
                for item in dict.fromkeys(p.execution_item_id for p in manifest.planned_trials)
            ],
            rationale="synthetic isolated regression",
            review_reference="synthetic test fixture",
            review_rules=[dict(rule_id="quality.supported")] if manifest.mode == "real" else [],
        )
    )


def experiment(manifest):
    return ExperimentSpec.model_validate(
        dict(
            variables=[
                dict(
                    name="product_snapshot",
                    baseline=product_hash(manifest, "baseline"),
                    candidate=product_hash(manifest),
                )
            ],
            rationale="synthetic product delta",
        )
    )


def review(manifest, tmp_path):
    path = tmp_path / "independent.txt"
    path.write_text(
        "Synthetic independent review fixture, not an actual production review.", encoding="utf-8"
    )
    now = datetime.now(timezone.utc)
    return ReviewBundle.model_validate(
        dict(
            independent_reviews=[
                dict(
                    reviewer="synthetic reviewer",
                    role="independent_reviewer",
                    candidate_snapshot_hash=product_hash(manifest),
                    evaluator_hash=manifest.evaluator_hash,
                    record_ref=str(path),
                    record_hash=file_hash(path),
                    disposition="accepted",
                    created_at=now.isoformat(),
                    expires_at=(now + timedelta(days=1)).isoformat(),
                )
            ]
        )
    )


def test_fixed_baseline_failure_does_not_block_candidate_absolute(manifest, tmp_path):
    first = manifest.planned_trials[0].identity()
    result = rows(manifest, failure=first)
    comparison = compare_core(manifest, result, manifest, result, experiment(manifest))
    assert comparison["groups"][0]["classification"] == "fixed"
    assert comparison["baseline_variant"] == "baseline"
    assert comparison["exit_code"] == 0
    gate = gate_core(
        [manifest],
        [result],
        policy(manifest),
        review(manifest, tmp_path),
        "initial_baseline",
        "enforce",
    )
    assert gate["gate_satisfied"] and gate["release_eligible"] is False
    assert (
        gate_core([manifest], [result], policy(manifest), ReviewBundle(), "release", "enforce")[
            "exit_code"
        ]
        == 3
    )


def test_release_reusing_approved_ab_run_compares_approved_candidate_arm(manifest, tmp_path):
    first = manifest.planned_trials[0].identity()
    result = rows(manifest, failure=first)
    p = policy(manifest)
    bundle = review(manifest, tmp_path)
    old_gate = gate_core([manifest], [result], p, bundle, "initial_baseline", "enforce")
    old_gate_path = tmp_path / "approved-gate.json"
    old_gate_path.write_text(json.dumps(old_gate), encoding="utf-8")
    candidate_hash = product_hash(manifest, "candidate")
    exp = ExperimentSpec(
        variables=[dict(name="product_snapshot", baseline=candidate_hash, candidate=candidate_hash)],
        rationale="Synthetic approved candidate reused without a product change",
    )
    record = dict(
        baseline_id="synthetic",
        sources=[dict(manifest_hash=digest(manifest.model_dump(mode="json")))],
        gate_path=str(old_gate_path),
        gate_hash=digest(old_gate),
    )
    gate = gate_core(
        [manifest], [result], p, bundle, "release", "enforce",
        [(manifest, result)], exp, {manifest.mode: record},
    )
    assert gate["gate_satisfied"] and gate["exit_code"] == 0
    comparison = gate["relative"]["comparisons"][0]
    for view in [comparison, comparison["derived_review_comparison"]]:
        assert view["baseline_variant"] == view["candidate_variant"] == "candidate"
        assert view["changed_variables"] == []
        assert {g["classification"] for g in view["groups"]} == {"passing"}
        assert all(g["baseline"]["variant_id"] == "candidate" for g in view["groups"])


def test_explicit_approved_candidate_comparison_allows_candidate_only_reuse(manifest):
    single = manifest.model_copy(update={
        "variants": {"candidate": manifest.variants["candidate"]},
        "planned_trials": [p for p in manifest.planned_trials if p.variant_id == "candidate"],
    })
    value = product_hash(single)
    exp = ExperimentSpec(
        variables=[dict(name="product_snapshot", baseline=value, candidate=value)],
        rationale="Synthetic candidate-only approved reuse",
    )
    comparison = compare_core(single, rows(single), single, rows(single), exp, baseline_variant="candidate")
    assert comparison["exit_code"] == 0
    with pytest.raises(ValueError, match="variant missing"):
        compare_core(single, rows(single), single, rows(single), exp, baseline_variant="baseline")


def test_persistent_safety_failure_is_absolute_blocker(manifest, tmp_path):
    result = rows(manifest)
    for variant in ["baseline", "candidate"]:
        key = next(p.identity() for p in manifest.planned_trials if p.variant_id == variant)
        replacement = {r.identity(): r for r in rows(manifest, variant, key)}
        result = [replacement.get(r.identity(), r) for r in result]
    comparison = compare_core(manifest, result, manifest, result, experiment(manifest))
    assert comparison["groups"][0]["classification"] == "persistent_failure"
    assert (
        gate_core(
            [manifest],
            [result],
            policy(manifest),
            review(manifest, tmp_path),
            "initial_baseline",
            "enforce",
        )["exit_code"]
        == 1
    )
    with pytest.raises(ValueError):
        QualityException.model_validate({"rule_id": "business.authorized_deltas"})


def test_full_denominator_missing_repeats_and_fluctuation(manifest, tmp_path):
    result = rows(manifest)
    stat = statistics(manifest, result[:-1])
    assert stat["planned"] == 114 and stat["counts"]["incomplete"] == 1
    assert stat["decidable"]["denominator"] == 114
    assert stat["evidence_complete"]["denominator"] == 114
    assert stat["p95"] is None
    gate = gate_core(
        [manifest],
        [result[:-1]],
        policy(manifest),
        review(manifest, tmp_path),
        "initial_baseline",
        "enforce",
    )
    assert gate["exit_code"] == 3


def test_undeclared_dependency_or_wrong_experiment_hash_is_incomparable(manifest):
    changed = manifest.model_copy(deep=True)
    changed.variants["candidate"]["lock_hash"] = "0" * 64
    comparison = compare_core(changed, rows(changed), changed, rows(changed), experiment(changed))
    assert comparison["exit_code"] == 2
    assert "undeclared.dependency_set" in comparison["groups"][0]["reasons"]
    bad = experiment(manifest).model_copy(deep=True)
    bad.variables[0] = bad.variables[0].model_copy(update={"candidate": "0" * 64})
    assert compare_core(manifest, rows(manifest), manifest, rows(manifest), bad)["exit_code"] == 2


def test_self_comparison_review_approved_boolean_and_weakened_rules_rejected(manifest):
    single = manifest.model_copy(
        update={
            "variants": {"candidate": manifest.variants["candidate"]},
            "planned_trials": [p for p in manifest.planned_trials if p.variant_id == "candidate"],
        }
    )
    with pytest.raises(ValueError, match="itself"):
        compare_core(single, rows(single), single, rows(single), experiment(manifest))
    with pytest.raises(ValueError):
        ReviewBundle.model_validate({"approved": True})
    raw = policy(manifest).model_dump(mode="json")
    raw["absolute"][0]["rules"].remove("business.authorized_deltas")
    with pytest.raises(ValueError):
        GatePolicyV2.model_validate(raw)


def test_stale_independent_record_never_approves(manifest, tmp_path):
    bundle = review(manifest, tmp_path)
    bundle.independent_reviews[0] = bundle.independent_reviews[0].model_copy(
        update={"candidate_snapshot_hash": "0" * 64}
    )
    assert (
        gate_core(
            [manifest], [rows(manifest)], policy(manifest), bundle, "initial_baseline", "enforce"
        )["exit_code"]
        == 3
    )


def test_declared_target_dependencies_do_not_waive_evaluator_environment(manifest):
    from uuid import uuid4

    run_id = uuid4()
    other = manifest.model_copy(
        deep=True,
        update={
            "run_id": run_id,
            "planned_trials": [
                p.model_copy(update={"run_id": run_id}) for p in manifest.planned_trials
            ],
        },
    )
    other.environment["sqlite"] = "changed-evaluator-host"
    value = variable_values(manifest, "candidate")["dependency_set"]
    exp = ExperimentSpec(
        variables=[dict(name="dependency_set", baseline=value, candidate=value)],
        rationale="same target dependencies",
    )
    result = compare_core(manifest, rows(manifest), other, rows(other), exp)
    assert "evaluator_environment" in result["groups"][0]["reasons"]
    assert result["joint_change"] is False and result["changed_variables"] == []


def test_joint_change_counts_observed_changes_instead_of_declarations(manifest):
    exp = experiment(manifest)
    value = variable_values(manifest, "candidate")["dependency_set"]
    exp = ExperimentSpec(
        variables=[*exp.variables, dict(name="dependency_set", baseline=value, candidate=value)],
        rationale=exp.rationale,
    )
    result = compare_core(manifest, rows(manifest), manifest, rows(manifest), exp)
    assert not result["joint_change"]
    assert result["changed_variables"] == ["product_snapshot"]


@pytest.mark.parametrize("corrupt", ["initial", "index"])
def test_schema_two_reader_preserves_intact_failure_despite_other_corrupt_fragment(
    manifest, tmp_path, corrupt
):
    from offerpilot.evaluation.core_storage import seal
    from offerpilot.evaluation.core_graders import grade
    from offerpilot.evaluation.core_runner import read_run
    from offerpilot.evaluation.reporting import atomic_json

    plan = manifest.planned_trials[1]
    directory = tmp_path / "saved"
    private = directory / "private" / "candidate-trial"
    private.mkdir(parents=True)
    key = plan.model_dump(
        mode="json", include={"run_id", "variant_id", "execution_item_id", "mode", "repeat_index"}
    )
    atomic_json(private / ".eval-trial.json", key)
    target = manifest.variants["candidate"]
    evidence = {
        "trial_key": key,
        "observed": {"coverage": {"version": "core-observer-v1", "gaps": []}, "deltas": []},
        "facts": {
            "product_failure": "synthetic actual boundary failure",
            "control_closed": True,
            "handshakes": [
                {
                    "checkout_sha": target["commit"],
                    "interpreter": target["interpreter"],
                    **target["runtime"],
                }
            ],
        },
    }
    checksum = seal(private, evidence)
    result = grade(plan, evidence, "product_failed", checksum, str(private))
    atomic_json(directory / "run-manifest.json", manifest.model_dump(mode="json"))
    atomic_json(directory / "trial-results.json", [result.model_dump(mode="json")])
    (
        private / ("evidence-index.json" if corrupt == "index" else "fragment-initial.json")
    ).write_text("{invalid", encoding="utf-8")
    _, loaded = read_run(directory)
    assert loaded[0].verdict == "fail"
    assert (
        next(r.status for r in loaded[0].rules if r.rule_id == "evidence.required_complete")
        == "unknown"
    )
