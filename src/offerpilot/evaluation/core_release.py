"""Frozen evaluation contribution to the existing complete release scripts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any
from uuid import UUID

from offerpilot.evaluation.core_contracts import CoreVariant, ProviderRunProfile
from offerpilot.evaluation.core_governance import (
    ExperimentSpec,
    GatePolicyV2,
    ReviewBundle,
    gate_core,
    product_hash,
)
from offerpilot.evaluation.core_refresh import resolve_baseline as load_baseline
from offerpilot.evaluation.core_planning import (
    create_plan,
    load_core_suite,
    read_plan,
    validate_grant,
)
from offerpilot.evaluation.core_runner import read_run, run_plan
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.reporting import atomic_json
from offerpilot.evaluation.runner import (
    ROOT,
    environment,
    evaluator_hash,
    snapshot,
    process_tree,
    process_environment,
)


def reference(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("release input is unavailable")
    return {"path": str(path.resolve()), "hash": file_hash(path)}


def preflight(
    suite: Path,
    policy_path: Path,
    variants_path: Path,
    output: Path,
    baseline_id: str,
    baseline_store: Path,
    review_path: Path,
    experiment_path: Path,
    provider_path: Path | None = None,
    grant_path: Path | None = None,
    reuse: list[Path] | None = None,
) -> dict[str, Any]:
    output = output.resolve()
    if output.is_relative_to(ROOT.resolve()):
        relative = output.relative_to(ROOT.resolve()).as_posix()
        ignored = subprocess.run(
            ["git", "check-ignore", "--no-index", relative], cwd=ROOT, capture_output=True
        )
        if ignored.returncode or not relative.startswith("artifacts/harness/"):
            raise ValueError(
                "release output must be dedicated ignored artifacts/harness or outside checkout"
            )
    if output.exists():
        raise ValueError("release output must be new")
    loaded, _ = load_core_suite(suite)
    policy = GatePolicyV2.model_validate_json(policy_path.read_bytes())
    review = ReviewBundle.model_validate_json(review_path.read_bytes())
    ExperimentSpec.model_validate_json(experiment_path.read_bytes())
    variants = [CoreVariant.model_validate(v) for v in json.loads(variants_path.read_bytes())]
    candidate = next((v for v in variants if v.variant_id == "candidate"), None)
    if not candidate or Path(candidate.checkout).resolve() != ROOT.resolve():
        raise ValueError("candidate must be the actual release checkout")
    if len({v.variant_id for v in variants}) != len(variants) or any(
        v.fault != "none" for v in variants
    ):
        raise ValueError("release variants cannot be diagnostic or duplicate")
    if (
        policy.suite_id != loaded.suite_id
        or policy.suite_hash != file_hash(suite)
        or "release" not in policy.purposes
    ):
        raise ValueError("release policy and suite differ")
    record = load_baseline(baseline_store, baseline_id)
    if policy.relative.approved_baseline_id not in {None, baseline_id}:
        raise ValueError("approved baseline differs from policy")
    for source in record["sources"]:
        manifest, _ = read_run(Path(source["directory"]))
        if digest(manifest.model_dump(mode="json")) != source["manifest_hash"]:
            raise ValueError("approved baseline evidence changed")
    modes = {r.mode for r in policy.absolute}
    if ("real" in modes) != bool(provider_path and grant_path):
        raise ValueError(
            "required real mode needs profile and authorized grant; deterministic forbids them"
        )
    if provider_path:
        profile = ProviderRunProfile.model_validate_json(provider_path.read_bytes())
        credential = Path(profile.credential_ref)
        if not credential.is_file() or credential.stat().st_size == 0:
            raise ValueError("credential reference unavailable")
    refs = {
        k: reference(p)
        for k, p in {
            "suite": suite,
            "policy": policy_path,
            "variants": variants_path,
            "review": review_path,
            "experiment": experiment_path,
            **({"provider": provider_path} if provider_path else {}),
            **({"grant": grant_path} if grant_path else {}),
        }.items()
    }
    if not review.independent_reviews:
        raise ValueError("release requires an actual independent review record")
    # Every input and baseline is checked before execution can authorize a send.
    manifests = [read_run(p)[0] for p in reuse or []]
    if manifests and {m.mode for m in manifests} != modes:
        raise ValueError("reuse must contain exactly every required mode")
    if len({m.mode for m in manifests}) != len(manifests):
        raise ValueError("duplicate reused mode")
    output.mkdir(parents=True)
    runs: list[dict[str, Any]] = []
    for mode in sorted(modes):
        repeats = {r.repeats for r in policy.absolute if r.mode == mode}
        if len(repeats) != 1:
            raise ValueError("release requires a fixed repeat count per mode")
        if manifests:
            index = next(i for i, m in enumerate(manifests) if m.mode == mode)
            manifest = manifests[index]
            directory = (reuse or [])[index].resolve()
            # read_plan checks dirty diff, actual runtime and evaluator, not only HEAD.
            frozen = read_plan(directory / "run-plan.json")
            if digest(frozen.model_dump(mode="json")) != digest(manifest.model_dump(mode="json")):
                raise ValueError("reused manifest differs from its plan")
        else:
            directory = output / mode
            manifest = create_plan(
                suite,
                variants_path,
                directory,
                mode,
                next(iter(repeats)),
                provider_path if mode == "real" else None,
            )
        if (
            manifest.suite_hash != policy.suite_hash
            or manifest.repeats != next(iter(repeats))
            or {
                k: {n: v[n] for n in ("checkout", "interpreter", "fault")}
                for k, v in manifest.variants.items()
            }
            != {
                v.variant_id: {n: v.model_dump()[n] for n in ("checkout", "interpreter", "fault")}
                for v in variants
            }
        ):
            raise ValueError("frozen run differs from release parameters")
        if mode == "real":
            assert provider_path
            if manifest.provider_profile_hash != digest(
                ProviderRunProfile.model_validate_json(provider_path.read_bytes()).model_dump(
                    mode="json"
                )
            ):
                raise ValueError("reused provider identity differs")
            validate_grant(manifest, grant_path)
        runs.append(
            {
                "directory": str(directory),
                "reuse": bool(manifests),
                "manifest_hash": digest(manifest.model_dump(mode="json")),
            }
        )
    candidate_manifest = read_plan(Path(runs[0]["directory"]) / "run-plan.json")
    prospective = gate_core([candidate_manifest], [[]], policy, review, "release", "enforce")
    if "independent_review_record_missing_or_stale" in prospective["absolute"]["incomplete"]:
        raise ValueError("independent review is stale or unbound")
    value = {
        "schema_version": 2,
        "phase": "preflight",
        "purpose": "release",
        "mode": "enforce",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": refs,
        "baseline_id": baseline_id,
        "baseline_store": str(baseline_store.resolve()),
        "baseline_record_hash": digest(record),
        "runs": runs,
        "snapshot": snapshot(ROOT),
        "environment": environment(),
        "evaluator_hash": evaluator_hash(),
        "candidate_snapshot_hash": product_hash(candidate_manifest),
        "release_eligible": False,
    }
    atomic_json(output / "release-preflight.json", {"value": value, "hash": digest(value)})
    return value


def read_preflight(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_bytes())
    value: dict[str, Any] = raw["value"]
    if raw["hash"] != digest(value) or value["phase"] != "preflight":
        raise ValueError("release preflight checksum invalid")
    if (
        value["snapshot"] != snapshot(ROOT)
        or value["environment"] != environment()
        or value["evaluator_hash"] != evaluator_hash()
    ):
        raise ValueError("release source/environment changed after preflight")
    for ref in value["inputs"].values():
        if reference(Path(ref["path"])) != ref:
            raise ValueError("release input changed")
    record = load_baseline(Path(value["baseline_store"]), value["baseline_id"])
    if digest(record) != value["baseline_record_hash"]:
        raise ValueError("approved baseline changed")
    return value


def execute(path: Path) -> dict[str, Any]:
    value = read_preflight(path)
    inputs = value["inputs"]
    policy = GatePolicyV2.model_validate_json(Path(inputs["policy"]["path"]).read_bytes())
    review = ReviewBundle.model_validate_json(Path(inputs["review"]["path"]).read_bytes())
    experiment = ExperimentSpec.model_validate_json(Path(inputs["experiment"]["path"]).read_bytes())
    pairs = []
    for run in value["runs"]:
        directory = Path(run["directory"])
        manifest = read_plan(directory / "run-plan.json")
        if digest(manifest.model_dump(mode="json")) != run["manifest_hash"]:
            raise ValueError("frozen release plan changed")
        if not run["reuse"]:
            run_plan(
                directory / "run-plan.json",
                Path(inputs["grant"]["path"]) if manifest.mode == "real" else None,
            )
        pairs.append(read_run(directory))
    read_preflight(path)
    record = load_baseline(Path(value["baseline_store"]), value["baseline_id"])
    baseline = [read_run(Path(s["directory"])) for s in record["sources"]]
    report = gate_core(
        [m for m, _ in pairs],
        [r for _, r in pairs],
        policy,
        review,
        "release",
        "enforce",
        baseline,
        experiment,
        {m.mode: record for m, _ in baseline},
    )
    for source, run in zip(report["sources"], value["runs"], strict=True):
        source["directory"] = run["directory"]
    receipt = {
        "preflight_hash": file_hash(path),
        "evaluation_gate": report,
        "gate_hash": digest(report),
        "release_eligible": False,
    }
    atomic_json(path.parent / "release-evaluation.json", receipt)
    return report


def owned_command(argv: list[str], timeout: float, log: Path) -> int:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("positive finite execution timeout required")
    with log.open("x", encoding="utf-8") as output:
        tree = process_tree(
            argv,
            cwd=ROOT,
            env=process_environment(ROOT, log.parent),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            return int(tree.wait(timeout))
        except (subprocess.TimeoutExpired, TimeoutError) as exc:
            tree.kill()
            tree.wait(30)
            raise TimeoutError("release evaluation process tree timed out and was closed") from exc
        finally:
            tree.close()


def execute_bounded(path: Path, timeout: float | None = None) -> dict[str, Any]:
    value = read_preflight(path)
    maximum = 60 + sum(
        read_plan(Path(r["directory"]) / "run-plan.json").run_max_wall_seconds
        for r in value["runs"]
        if not r["reuse"]
    )
    code = "import sys; from pathlib import Path; sys.path.insert(0,str(Path(sys.argv[2])/'src')); sys.path.insert(1,sys.argv[2]); from offerpilot.evaluation.core_release import execute;\ntry:\n result=execute(Path(sys.argv[1])); sys.exit(result['exit_code'])\nexcept Exception as exc:\n print(type(exc).__name__,file=sys.stderr); sys.exit(2)\n"
    try:
        exit_code = owned_command(
            [sys.executable, "-c", code, str(path.resolve()), str(ROOT)],
            min(timeout, maximum) if timeout is not None else maximum,
            path.parent / "release-execution.log",
        )
    except TimeoutError:
        atomic_json(
            path.parent / "release-execution.json",
            dict(closed=True, termination_source="evaluator_watchdog", exit_code=3),
        )
        return dict(gate_satisfied=False, exit_code=3, release_eligible=False)
    atomic_json(
        path.parent / "release-execution.json",
        dict(closed=True, termination_source="normal", exit_code=exit_code),
    )
    receipt = path.parent / "release-evaluation.json"
    if not receipt.is_file():
        raise ValueError("evaluation child exited without a report")
    report: dict[str, Any] = json.loads(receipt.read_bytes())["evaluation_gate"]
    if report["exit_code"] != exit_code:
        raise ValueError("child exit differs from enforce gate")
    return report


def verify_receipt(path: Path) -> dict[str, Any]:
    value = read_preflight(path)
    receipt = json.loads((path.parent / "release-evaluation.json").read_bytes())
    saved = receipt["evaluation_gate"]
    inputs = value["inputs"]
    policy = GatePolicyV2.model_validate_json(Path(inputs["policy"]["path"]).read_bytes())
    review = ReviewBundle.model_validate_json(Path(inputs["review"]["path"]).read_bytes())
    experiment = ExperimentSpec.model_validate_json(Path(inputs["experiment"]["path"]).read_bytes())
    if (
        saved["frozen_policy"] != policy.model_dump(mode="json")
        or saved["frozen_review"] != review.model_dump(mode="json")
        or saved["frozen_experiment"] != experiment.model_dump(mode="json")
    ):
        raise ValueError("receipt cannot replace frozen policy/review/experiment")
    if (
        set(receipt) != {"preflight_hash", "evaluation_gate", "gate_hash", "release_eligible"}
        or receipt.get("release_eligible") is not False
        or saved.get("release_eligible") is not False
        or receipt["gate_hash"] != digest(saved)
        or receipt["preflight_hash"] != file_hash(path)
        or saved["mode"] != "enforce"
        or saved["purpose"] != "release"
        or not saved["gate_satisfied"]
        or saved["exit_code"] != 0
    ):
        raise ValueError("release evaluation absent, observe, incomplete or failing")
    # Regrade saved private evidence without re-executing or spending.
    pairs = [read_run(Path(r["directory"])) for r in value["runs"]]
    record = load_baseline(Path(value["baseline_store"]), value["baseline_id"])
    baseline = [read_run(Path(s["directory"])) for s in record["sources"]]
    checked = gate_core(
        [m for m, _ in pairs],
        [r for _, r in pairs],
        policy,
        review,
        "release",
        "enforce",
        baseline,
        experiment,
        {m.mode: record for m, _ in baseline},
    )
    # Regrading creates a new audit ID/time; retain the saved audit identity
    # after validating its syntax and time, and compare every semantic field.
    UUID(saved["gate_id"])
    created = datetime.fromisoformat(saved["created_at"])
    if created.tzinfo is None or not datetime.fromisoformat(
        value["created_at"]
    ) <= created <= datetime.now(timezone.utc):
        raise ValueError("receipt audit timestamp outside release execution")
    checked.update(gate_id=saved["gate_id"], created_at=saved["created_at"])
    for source, run in zip(checked["sources"], value["runs"], strict=True):
        source["directory"] = run["directory"]
    if (
        not checked["gate_satisfied"]
        or checked["candidate_snapshot_hash"] != value["candidate_snapshot_hash"]
        or digest(saved) != digest(checked)
    ):
        raise ValueError("release evidence no longer satisfies enforce gate")
    return {
        "evaluation_satisfied": True,
        "release_eligible": False,
        "receipt_hash": file_hash(path.parent / "release-evaluation.json"),
    }
