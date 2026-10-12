from __future__ import annotations

import json
from pathlib import Path

import typer

app = typer.Typer(help="Run isolated deterministic Harness P0 checks (never a release gate)")


@app.command()
def validate(suite: Path = typer.Option(..., "--suite")) -> None:
    from offerpilot.evaluation.registry import load_suite
    root = Path(__file__).resolve().parents[3] / "evals"
    try:
        loaded, _ = load_suite(suite, root)
    except (ValueError, OSError) as exc:
        typer.echo(f"Invalid evaluation configuration: {type(exc).__name__}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"{loaded.suite_id}: 3 required deterministic execution items; release_eligible=false")


@app.command(name="run")
def run_command(suite: Path = typer.Option(..., "--suite"),
                variants: Path = typer.Option(..., "--variants"),
                output: Path = typer.Option(..., "--output"),
                mode: str = typer.Option("deterministic", "--mode")) -> None:
    from offerpilot.evaluation.contracts import VariantConfig
    from offerpilot.evaluation.runner import run
    try:
        configs = [VariantConfig.model_validate(v) for v in json.loads(variants.read_bytes())]
        summary = run(suite, configs, output, mode)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        typer.echo(f"Evaluation configuration error: {type(exc).__name__}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(json.dumps({k: summary[k] for k in ("planned", "counts", "release_eligible")}))
    raise typer.Exit(summary["exit_code"])


@app.command(name="compare")
def compare_command(baseline: Path = typer.Option(..., "--baseline"),
                    candidate: Path = typer.Option(..., "--candidate"),
                    policy: str = typer.Option("harness-p0-v1", "--policy"),
                    output: Path | None = typer.Option(None, "--output")) -> None:
    from offerpilot.evaluation.compare import compare, read_run
    from offerpilot.evaluation.reporting import atomic_json
    if policy != "harness-p0-v1":
        raise typer.Exit(2)
    try:
        ma, ra = read_run(baseline)
        mb, rb = read_run(candidate)
        report = compare(ma, ra, mb, rb)
        if output:
            if output.exists():
                raise ValueError("output exists")
            atomic_json(output, report)
    except (ValueError, OSError) as exc:
        typer.echo(f"Comparison configuration error: {type(exc).__name__}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(json.dumps(report, ensure_ascii=False))
    raise typer.Exit(report["exit_code"])
