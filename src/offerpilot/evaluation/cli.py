from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

app = typer.Typer(help="Run isolated Harness evaluations and evidence gates")
baselines = typer.Typer(help="Immutable local baseline records; promotion requires approval")
app.add_typer(baselines, name="baseline")


def configuration_error(exc: Exception) -> None:
    typer.echo(f"Evaluation configuration error: {type(exc).__name__}", err=True)
    raise typer.Exit(2) from exc


def save(path: Path | None, report: dict[str, Any]) -> None:
    from offerpilot.evaluation.reporting import atomic_json

    if path:
        if path.exists():
            raise ValueError("output exists")
        atomic_json(path, report)


@app.command("import-external")
def import_external(
    receipt: Path = typer.Option(..., "--receipt"), output: Path = typer.Option(..., "--output")
) -> None:
    from offerpilot.evaluation.core_external import import_external as import_check

    try:
        result = import_check(receipt, output)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo(json.dumps(dict(kind=result.kind, verdict=result.verdict, release_eligible=False)))
    raise typer.Exit({"pass": 0, "fail": 1, "incomplete": 3}[result.verdict])


@app.command("targeted-offline-plan")
def targeted_offline_plan(
    suite: Path = typer.Option(..., "--suite"),
    variants: Path = typer.Option(..., "--variants"),
    output: Path = typer.Option(..., "--output"),
    select: list[str] = typer.Option(..., "--select"),
    max_requests: int = typer.Option(27, "--max-requests"),
    simulated_cny: float = typer.Option(1, "--simulated-cny"),
    lifetime_seconds: int = typer.Option(3600, "--lifetime-seconds"),
) -> None:
    """Freeze all13 real parent identities; execution is simulation only, no key/grant loading."""
    from offerpilot.evaluation.core_targeted import prepare

    try:
        result = prepare(suite, variants, output, select, max_requests=max_requests,
                         simulated_cny=simulated_cny, lifetime_seconds=lifetime_seconds)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo(json.dumps(result))


@app.command("targeted-offline-run")
def targeted_offline_run(plan: Path = typer.Option(..., "--plan")) -> None:
    """Run/resume loopback simulation only. Never consumes a real account or provider grant."""
    from offerpilot.evaluation.core_targeted import execute

    try:
        result = execute(plan)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo(json.dumps({k: result[k] for k in (
        "simulation_only", "parent_denominator", "selected_count", "allowed_requests",
        "real_provider_requests", "real_budget_reservation_cny", "release_eligible",
        "simulation_status", "exit_code",
    )}))
    raise typer.Exit(result["exit_code"])


@app.command("targeted-plan")
def targeted_plan(
    suite: Path = typer.Option(..., "--suite"),
    variants: Path = typer.Option(..., "--variants"),
    profile: Path = typer.Option(..., "--profile"),
    grant: Path = typer.Option(..., "--grant"),
    price_audit: Path = typer.Option(..., "--price-audit"),
    gate_receipt: Path = typer.Option(..., "--gate-receipt"),
    authority: str = typer.Option(..., "--authority"),
    authorization: str = typer.Option(..., "--authorization"),
    output: Path = typer.Option(..., "--output"),
    select: list[str] = typer.Option(..., "--select"),
) -> None:
    """Freeze nine targets/all13 parents. Authority registration is a separate trusted action."""
    from offerpilot.evaluation.core_authorized import prepare

    try:
        result = prepare(suite, variants, profile, grant, price_audit, gate_receipt, output,
                         select, authority, authorization)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo(json.dumps(result))


@app.command("targeted-preflight")
def targeted_preflight(plan: Path = typer.Option(..., "--plan")) -> None:
    """Read-only authority/account/plan checks; never loads credentials or sends requests."""
    from offerpilot.evaluation.core_authorized import preflight

    try:
        result = preflight(plan)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo(json.dumps(result))


@app.command("targeted-run")
def targeted_run(plan: Path = typer.Option(..., "--plan")) -> None:
    """Run/resume only the registered authorization, using the one shared budget account."""
    from offerpilot.evaluation.core_authorized import execute

    try:
        result = execute(plan)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo(json.dumps({k: result.get(k) for k in (
        "simulation_only", "parent_denominator", "selected_count", "selected_status",
        "allowed_requests", "transport_observation", "allocation_state", "release_eligible",
    )}))
    raise typer.Exit(result["exit_code"])


@app.command("review-pack")
def review_pack(
    run: list[Path] = typer.Option(..., "--run"),
    output: Path = typer.Option(..., "--output"),
    audit_output: Path = typer.Option(..., "--audit-output"),
) -> None:
    from offerpilot.evaluation.core_review import blinded_pack

    try:
        typer.echo(json.dumps(blinded_pack(run, output, audit_output), ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)


@app.command("acceptance-summary")
def acceptance_summary(
    process_review: Path = typer.Option(..., "--process-review"),
    quality_review: Path = typer.Option(..., "--quality-review"),
    audit: Path = typer.Option(..., "--audit"),
    output: Path = typer.Option(..., "--output"),
) -> None:
    from offerpilot.evaluation.core_acceptance import append_summary

    try:
        result = append_summary(process_review, quality_review, audit, output)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo(json.dumps(dict(counts=result["counts"], network_requests=0, release_eligible=False)))


@app.command("judge-preflight")
def judge_preflight(
    run: list[Path] = typer.Option(..., "--run"), review: Path = typer.Option(..., "--review")
) -> None:
    from offerpilot.evaluation.core_governance import ReviewBundle
    from offerpilot.evaluation.core_runner import read_run
    from offerpilot.evaluation.core_review import judge_eligibility

    try:
        bundle = ReviewBundle.model_validate_json(review.read_bytes())
        evidence = {
            row.identity(): row.evidence_hash
            for directory in run
            for row in read_run(directory)[1]
            if any(v.rule_id == "quality.supported" for v in row.rules)
        }
        result = judge_eligibility(bundle.adjudications, evidence)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo(json.dumps(result))
    raise typer.Exit(0 if result["eligible_for_calibration"] else 3)


@app.command("release-preflight")
def release_preflight(
    suite: Path = typer.Option(..., "--suite"),
    policy: Path = typer.Option(..., "--policy"),
    variants: Path = typer.Option(..., "--variants"),
    output: Path = typer.Option(..., "--output"),
    baseline_id: str = typer.Option(..., "--baseline-id"),
    baseline_store: Path = typer.Option(..., "--baseline-store"),
    review: Path = typer.Option(..., "--review"),
    experiment: Path = typer.Option(..., "--experiment"),
    provider_profile: Path | None = typer.Option(None, "--provider-profile"),
    grant: Path | None = typer.Option(None, "--grant"),
    reuse: list[Path] | None = typer.Option(None, "--reuse"),
) -> None:
    from offerpilot.evaluation.core_release import preflight

    try:
        result = preflight(
            suite,
            policy,
            variants,
            output,
            baseline_id,
            baseline_store,
            review,
            experiment,
            provider_profile,
            grant,
            reuse,
        )
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo(
        json.dumps(
            {
                "phase": "preflight",
                "network_requests": 0,
                "runs": result["runs"],
                "release_eligible": False,
            }
        )
    )


@app.command("release-execute")
def release_execute(
    preflight: Path = typer.Option(..., "--preflight"),
    timeout_seconds: float | None = typer.Option(None, "--timeout-seconds"),
) -> None:
    from offerpilot.evaluation.core_release import execute_bounded

    try:
        result = execute_bounded(preflight, timeout_seconds)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo(json.dumps({"gate_satisfied": result["gate_satisfied"], "release_eligible": False}))
    raise typer.Exit(result["exit_code"])


@app.command("release-receipt")
def release_receipt(preflight: Path = typer.Option(..., "--preflight")) -> None:
    from offerpilot.evaluation.core_release import verify_receipt

    try:
        result = verify_receipt(preflight)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo("EVAL_ENFORCE_VERIFIED " + result["receipt_hash"])


@app.command()
def validate(suite: Path = typer.Option(..., "--suite")) -> None:
    try:
        raw = json.loads(suite.read_bytes())
        if raw.get("schema_version") == 2:
            from offerpilot.evaluation.core_planning import load_core_suite

            loaded, cases = load_core_suite(suite)
            typer.echo(
                f"{loaded.suite_id}: {len(cases)} deterministic / {sum('real' in c.supported_modes for c in cases.values())} real items; release_eligible=false"
            )
        else:
            from offerpilot.evaluation.registry import load_suite

            root = Path(__file__).resolve().parents[3] / "evals"
            legacy, _ = load_suite(suite, root)
            typer.echo(
                f"{legacy.suite_id}: 3 required deterministic execution items; release_eligible=false"
            )
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)


@app.command()
def plan(
    suite: Path = typer.Option(..., "--suite"),
    variants: Path = typer.Option(..., "--variants"),
    output: Path = typer.Option(..., "--output"),
    mode: str = typer.Option("deterministic", "--mode"),
    repeat: int = typer.Option(3, "--repeat"),
    provider_profile: Path | None = typer.Option(None, "--provider-profile"),
    parent_run_id: str | None = typer.Option(None, "--parent-run-id"),
) -> None:
    from offerpilot.evaluation.core_planning import create_plan

    try:
        manifest = create_plan(
            suite, variants, output, mode, repeat, provider_profile, parent_run_id
        )
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo(
        json.dumps(
            dict(
                run_id=str(manifest.run_id),
                planned=len(manifest.planned_trials),
                network_requests=0,
                plan=str(output / "run-plan.json"),
                release_eligible=False,
            )
        )
    )


@app.command(name="run")
def run_command(
    suite: Path | None = typer.Option(None, "--suite"),
    variants: Path | None = typer.Option(None, "--variants"),
    output: Path | None = typer.Option(None, "--output"),
    mode: str = typer.Option("deterministic", "--mode"),
    plan: Path | None = typer.Option(None, "--plan"),
    grant: Path | None = typer.Option(None, "--grant"),
) -> None:
    try:
        if plan:
            if any(v is not None for v in (suite, variants, output)) or mode != "deterministic":
                raise ValueError("execute cannot alter frozen plan")
            from offerpilot.evaluation.core_runner import run_plan

            summary = run_plan(plan, grant)
        else:
            if not suite or not variants or not output or grant:
                raise ValueError("P0 requires suite/variants/output and forbids paid grant")
            from offerpilot.evaluation.contracts import VariantConfig
            from offerpilot.evaluation.runner import run

            configs = [VariantConfig.model_validate(v) for v in json.loads(variants.read_bytes())]
            summary = run(suite, configs, output, mode)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo(json.dumps({k: summary[k] for k in ("planned", "counts", "release_eligible")}))
    raise typer.Exit(summary["exit_code"])


@app.command(name="compare")
def compare_command(
    baseline: Path = typer.Option(..., "--baseline"),
    candidate: Path = typer.Option(..., "--candidate"),
    policy: str = typer.Option("harness-p0-v1", "--policy"),
    output: Path | None = typer.Option(None, "--output"),
    experiment: Path | None = typer.Option(None, "--experiment"),
) -> None:
    try:
        if policy == "harness-p0-v1":
            from offerpilot.evaluation.compare import compare, read_run

            ma, ra = read_run(baseline)
            mb, rb = read_run(candidate)
            report = compare(ma, ra, mb, rb)
        else:
            from offerpilot.evaluation.core_runner import read_run as read_core_run
            from offerpilot.evaluation.core_governance import (
                ExperimentSpec,
                GatePolicyV2,
                compare_core,
                variable_values,
            )

            ma2, ra2 = read_core_run(baseline)
            mb2, rb2 = read_core_run(candidate)
            if policy != "core-observe-v1":
                GatePolicyV2.model_validate_json(Path(policy).read_bytes())
                if not experiment:
                    raise ValueError("strict comparison requires explicit experiment")
            spec = (
                ExperimentSpec.model_validate_json(experiment.read_bytes())
                if experiment
                else ExperimentSpec.model_validate(
                    dict(
                        variables=[
                            dict(
                                name="product_snapshot",
                                baseline=variable_values(
                                    ma2, "baseline" if ma2.run_id == mb2.run_id else "candidate"
                                )["product_snapshot"],
                                candidate=variable_values(mb2, "candidate")["product_snapshot"],
                            )
                        ],
                        rationale="core-observe-v1 explicitly permits product snapshot changes only",
                    )
                )
            )
            report = compare_core(ma2, ra2, mb2, rb2, spec)
        save(output, report)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo(json.dumps(report, ensure_ascii=False))
    raise typer.Exit(report["exit_code"])


@app.command()
def gate(
    candidate: list[Path] = typer.Option(..., "--candidate"),
    policy: Path = typer.Option(..., "--policy"),
    output: Path = typer.Option(..., "--output"),
    baseline: list[Path] | None = typer.Option(None, "--baseline"),
    review: Path | None = typer.Option(None, "--review"),
    purpose: str = typer.Option("release", "--purpose"),
    mode: str = typer.Option("enforce", "--mode"),
    experiment: Path | None = typer.Option(None, "--experiment"),
    baseline_store: Path | None = typer.Option(None, "--baseline-store"),
    baseline_id: str | None = typer.Option(None, "--baseline-id"),
) -> None:
    from offerpilot.evaluation.core_runner import read_run
    from offerpilot.evaluation.core_governance import (
        ExperimentSpec,
        GatePolicyV2,
        ReviewBundle,
        gate_core,
        load_baseline,
    )

    try:
        pairs = [read_run(p) for p in candidate]
        baseline_paths = baseline or []
        records: dict[str, dict[str, Any]] = {}
        if baseline_id:
            if not baseline_store:
                raise ValueError("approved baseline requires store")
            record = load_baseline(baseline_store, baseline_id)
            baseline_paths = [Path(s["directory"]) for s in record["sources"]]
            records = {m.mode: record for m, _ in [read_run(p) for p in baseline_paths]}
        old_pairs = [read_run(p) for p in baseline_paths]
        policy_value = GatePolicyV2.model_validate_json(policy.read_bytes())
        review_value = (
            ReviewBundle.model_validate_json(review.read_bytes()) if review else ReviewBundle()
        )
        experiment_value = (
            ExperimentSpec.model_validate_json(experiment.read_bytes()) if experiment else None
        )
        report = gate_core(
            [m for m, _ in pairs],
            [r for _, r in pairs],
            policy_value,
            review_value,
            purpose.replace("-", "_"),
            mode,
            old_pairs or None,
            experiment_value,
            records,
        )
        for source, directory in zip(report["sources"], candidate, strict=True):
            source["directory"] = str(directory.resolve())
        for source, directory in zip(report["baseline_sources"], baseline_paths, strict=True):
            source["run_directory"] = str(directory.resolve())
        save(output, report)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo(
        json.dumps({k: report[k] for k in ("gate_satisfied", "release_eligible", "exit_code")})
    )
    raise typer.Exit(report["exit_code"])


@baselines.command("promote")
def baseline_promote(
    run: Path = typer.Option(..., "--run"),
    gate: Path = typer.Option(..., "--gate"),
    approval: Path = typer.Option(..., "--approval"),
    id: str = typer.Option(..., "--id"),
    store: Path = typer.Option(..., "--store"),
    expected_current: str | None = typer.Option(None, "--expected-current"),
) -> None:
    from offerpilot.evaluation.core_governance import promote_baseline

    try:
        record = promote_baseline(store, run, gate, approval, id, expected_current)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo(json.dumps(dict(baseline_id=record["baseline_id"], release_eligible=False)))


@baselines.command("revoke")
def baseline_revoke(
    store: Path = typer.Option(..., "--store"),
    id: str = typer.Option(..., "--id"),
    reason: str = typer.Option(..., "--reason"),
    record: Path = typer.Option(..., "--record"),
) -> None:
    from offerpilot.evaluation.core_governance import revoke_baseline
    from offerpilot.evaluation.registry import file_hash

    try:
        revoke_baseline(store, id, reason, str(record.resolve()), file_hash(record))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        configuration_error(exc)
    typer.echo("Baseline revoked; immutable history retained")
