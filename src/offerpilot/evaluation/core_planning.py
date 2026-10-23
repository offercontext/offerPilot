from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from offerpilot.evaluation.contracts import PlannedTrial, VariantConfig
from offerpilot.evaluation.core_contracts import (
    CaseSpecV2,
    CoreVariant,
    ProviderRunProfile,
    RunGrant,
    RunManifestV2,
    SuiteV2,
)
from offerpilot.evaluation.core_registry import required_rules
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.reporting import atomic_json
from offerpilot.evaluation.runner import (
    ROOT,
    environment,
    evaluator_hash,
    snapshot,
    target_environment,
)


def load_core_suite(path: Path) -> tuple[SuiteV2, dict[str, CaseSpecV2]]:
    suite = SuiteV2.model_validate_json(path.read_bytes())
    cases = {
        i.execution_item_id: CaseSpecV2.model_validate_json(
            (ROOT / "evals" / i.spec_path).read_bytes()
        )
        for i in suite.execution_items
    }
    if any(k != v.execution_item_id for k, v in cases.items()):
        raise ValueError("spec identity mismatch")
    return suite, cases


def create_plan(
    suite_path: Path,
    variants_path: Path,
    output: Path,
    mode: str = "deterministic",
    repeat: int = 1,
    provider_path: Path | None = None,
    parent_run_id: str | None = None,
) -> RunManifestV2:
    suite, cases = load_core_suite(suite_path)
    if mode not in {"deterministic", "real"} or type(repeat) is not int or repeat < 1:
        raise ValueError("invalid mode/repeat")
    variants = [CoreVariant.model_validate(v) for v in json.loads(variants_path.read_bytes())]
    ids = [v.variant_id for v in variants]
    if len(ids) != len(set(ids)) or set(ids) not in ({"candidate"}, {"baseline", "candidate"}):
        raise ValueError("duplicate or invalid variants")
    if len({Path(v.checkout).resolve() for v in variants}) != len(variants):
        raise ValueError("two variants require distinct checkouts")
    provider = (
        ProviderRunProfile.model_validate_json(provider_path.read_bytes())
        if provider_path
        else None
    )
    if (mode == "real") != (provider is not None):
        raise ValueError("real requires provider profile; deterministic forbids it")
    variant_data = {
        v.variant_id: {
            **v.model_dump(),
            **snapshot(Path(v.checkout)),
            "runtime": target_environment(
                VariantConfig(
                    variant_id=v.variant_id, checkout=v.checkout, interpreter=v.interpreter
                )
            ),
            "prompt_or_tool_surface": digest(
                {
                    str(p.relative_to(Path(v.checkout))): file_hash(p)
                    for pattern in (
                        "src/offerpilot/ai/*.py",
                        "src/offerpilot/ai/tool_specs/*.py",
                        "src/offerpilot/pilot_runtime/composition.py",
                    )
                    for p in Path(v.checkout).glob(pattern)
                }
            ),
            "context_policy": digest(
                {
                    str(p.relative_to(Path(v.checkout))): file_hash(p)
                    for p in Path(v.checkout).glob("src/offerpilot/context_sources/**/*.py")
                }
            ),
        }
        for v in variants
    }
    run_id = uuid4()
    trials = [
        PlannedTrial(
            run_id=run_id,
            variant_id=v.variant_id,
            execution_item_id=item,
            mode=mode,
            repeat_index=n,
            case_id=spec.case_id,
            case_version=spec.case_version,
            spec_hash=digest(spec.model_dump(mode="json")),
            fixture_hash=file_hash(ROOT / "evals/fixtures/synthetic-core-v1.json")
            if spec.fixture_id == "synthetic-core-v1"
            else digest(
                {
                    name: file_hash(ROOT / "evals/fixtures" / name)
                    for name in (
                        "synthetic-interviews-v1.json",
                        "synthetic-core-v1.json",
                        "synthetic-core-extension-v1.json",
                    )
                }
            ),
            coverage_kind=spec.coverage_kind,
            required_rules=required_rules(item, mode),
        )
        for n in range(1, repeat + 1)
        for item, spec in cases.items()
        if mode in spec.supported_modes
        for v in variants
    ]
    raw: dict[str, Any] = dict(
        run_id=run_id,
        parent_run_id=parent_run_id,
        suite_id=suite.suite_id,
        suite_hash=file_hash(suite_path),
        evaluator_hash=evaluator_hash(),
        environment=environment(),
        variants=variant_data,
        mode=mode,
        repeats=repeat,
        planned_trials=trials,
        execution_order=[t.identity() for t in trials],
        provider_profile=provider,
        provider_profile_hash=digest(provider.model_dump(mode="json")) if provider else None,
        network_policy="approved-provider-only" if provider else "trial-loopback",
        run_max_wall_seconds=sum(
            cases[t.execution_item_id].evaluator_limits.wall_seconds for t in trials
        ),
        run_max_requests=sum(
            provider.max_attempts_per_trial
            if provider
            else cases[t.execution_item_id].evaluator_limits.model_calls
            for t in trials
        ),
    )
    manifest = RunManifestV2.model_validate(raw)
    if output.exists():
        raise ValueError("plan directory already exists")
    output.mkdir(parents=True)
    atomic_json(
        output / "run-plan.json",
        {
            "manifest": manifest.model_dump(mode="json"),
            "plan_hash": digest(manifest.model_dump(mode="json")),
        },
    )
    atomic_json(
        output / "plan-summary.json",
        {
            "planned": len(trials),
            "mode": mode,
            "max_requests": manifest.run_max_requests,
            "upper_estimate_cny": manifest.run_max_requests
            * (
                provider.max_input_bytes * provider.input_cny_per_million
                + provider.max_output_tokens * provider.output_cny_per_million
            )
            / 1e6
            if provider
            else None,
            "cost_basis": "conservative UTF-8 byte upper bound; uncached peak prices; unknown in-flight billed at reservation"
            if provider
            else "not_applicable",
            "release_eligible": False,
        },
    )
    return manifest


def read_plan(path: Path, *, check_targets: bool = True) -> RunManifestV2:
    raw = json.loads(path.read_bytes())
    if set(raw) != {"manifest", "plan_hash"} or digest(raw["manifest"]) != raw["plan_hash"]:
        raise ValueError("plan checksum mismatch")
    manifest = RunManifestV2.model_validate(raw["manifest"])
    if manifest.evaluator_hash != evaluator_hash():
        raise ValueError("frozen evaluator differs; use original evaluator checkout")
    if check_targets:
        _validate_targets(list(manifest.variants.values()))
    return manifest


def _validate_targets(variants: list[dict[str, Any]]) -> None:
    def source(v: dict[str, Any]) -> None:
        now = snapshot(Path(v["checkout"]))
        if any(now[k] != v[k] for k in now):
            raise ValueError("target source snapshot changed; regenerate plan")

    def runtime(v: dict[str, Any]) -> None:
        observed = target_environment(VariantConfig(
            variant_id=v["variant_id"], checkout=v["checkout"], interpreter=v["interpreter"]))
        if observed != v["runtime"]:
            raise ValueError("target runtime changed")

    if len(variants) == 1:
        source(variants[0])
        runtime(variants[0])
        return
    # A source failure blocks every target program. Each phase joins all work
    # before returning or raising; manifest order, not completion order, wins.
    with ThreadPoolExecutor(max_workers=2) as pool:
        for check in (source, runtime):
            futures = [pool.submit(check, variant) for variant in variants]
            errors: list[BaseException] = []
            for future in futures:
                try:
                    future.result()
                except BaseException as exc:
                    errors.append(exc)
            if errors:
                if len(errors) == 1:
                    raise errors[0]
                secondary = errors[1]
                if errors[0].__cause__ is not None:
                    # Python 3.10 has no ExceptionGroup. Keep the original
                    # primary cause and secondary object without replacing it.
                    details = Exception("additional target validation failure", secondary)
                    details.__cause__ = errors[0].__cause__
                    secondary = details
                raise errors[0] from secondary


def validate_grant(manifest: RunManifestV2, path: Path | None) -> RunGrant | None:
    if manifest.mode == "deterministic":
        if path:
            raise ValueError("deterministic does not use a paid grant")
        return None
    if not path:
        raise ValueError("real requires explicit user grant before any request")
    grant = RunGrant.model_validate_json(path.read_bytes())
    if (
        grant.expires_at <= datetime.now(timezone.utc)
        or grant.profile_hash != manifest.provider_profile_hash
        or grant.suite_hash != manifest.suite_hash
        or set(grant.variants) != set(manifest.variants)
        or grant.repeats != manifest.repeats
    ):
        raise ValueError("grant expired or scope differs")
    return grant
