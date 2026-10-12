"""Strict comparison and gate records; local attestations do not authenticate people."""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import json
import importlib
import os
from pathlib import Path
import re
from typing import Any, Iterator, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from offerpilot.evaluation.contracts import Contract, Hash, Positive
from offerpilot.evaluation.core_contracts import RunManifestV2, TrialResultV2
from offerpilot.evaluation.core_registry import ITEMS, required_rules
from offerpilot.evaluation.core_runner import aggregate, read_run
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.reporting import atomic_json

Variable = Literal[
    "product_snapshot",
    "provider_model",
    "prompt_or_tool_surface",
    "dependency_set",
    "context_policy",
]


class ExperimentVariable(Contract):
    name: Variable
    baseline: Hash
    candidate: Hash


class ExperimentSpec(Contract):
    schema_version: Literal[2] = 2
    variables: list[ExperimentVariable] = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def unique(self) -> ExperimentSpec:
        if len({v.name for v in self.variables}) != len(self.variables):
            raise ValueError("duplicate experiment variable")
        return self


class RequiredItem(Contract):
    execution_item_id: str
    mode: Literal["deterministic", "real"]
    case_version: Positive
    coverage_kind: Literal["task", "full_recovery", "observation_only"]
    repeats: Positive = 3
    rules: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def registered(self) -> RequiredItem:
        from offerpilot.evaluation.core_registry import case_definition

        if self.execution_item_id not in ITEMS:
            raise ValueError("unregistered item")
        spec = case_definition(self.execution_item_id)
        if (
            self.case_version != spec["case_version"]
            or self.coverage_kind != spec["coverage_kind"]
            or self.mode not in spec["supported_modes"]
            or self.rules != required_rules(self.execution_item_id, self.mode)
        ):
            raise ValueError("policy cannot weaken registered case contract")
        return self


class ReviewRequirement(Contract):
    rule_id: Literal["quality.supported"]
    rubric: Literal["quality-supported-v1"] = "quality-supported-v1"
    role: Literal["human"] = "human"
    max_age_days: Positive = 30


class ExceptionRequirement(Contract):
    rule_id: Literal["quality.supported"]
    approver_role: Literal["maintainer"] = "maintainer"
    max_days: Positive = Field(le=30)


class RelativePolicy(Contract):
    baseline_required: Literal[True] = True
    approved_baseline_id: str | None = None
    block_new_failures: Literal[True] = True
    latency_cost: Literal["observe"] = "observe"


class GatePolicyV2(Contract):
    schema_version: Literal[2] = 2
    policy_id: str = Field(min_length=1)
    version: Positive
    suite_id: Literal["harness-core-v1"] = "harness-core-v1"
    suite_hash: Hash
    purposes: list[Literal["release", "initial_baseline"]] = Field(min_length=1)
    absolute: list[RequiredItem] = Field(min_length=1)
    relative: RelativePolicy = Field(default_factory=RelativePolicy)
    review_rules: list[ReviewRequirement] = Field(default_factory=list)
    exception_rules: list[ExceptionRequirement] = Field(default_factory=list)
    independent_review_required: Literal[True] = True
    rationale: str = Field(min_length=1)
    review_reference: str = Field(min_length=1)

    @model_validator(mode="after")
    def closed(self) -> GatePolicyV2:
        keys = [(r.execution_item_id, r.mode) for r in self.absolute]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate required policy item")
        for mode in {r.mode for r in self.absolute}:
            expected = {i for i, v in ITEMS.items() if mode == "deterministic" or v["real"]}
            if {i for i, m in keys if m == mode} != expected:
                raise ValueError("core policy must require complete registered denominator")
        if len(set(self.purposes)) != len(self.purposes):
            raise ValueError("duplicate purpose")
        required_human = any("quality.supported" in r.rules for r in self.absolute)
        if (
            required_human != bool(self.review_rules)
            or len(self.review_rules) > 1
            or len(self.exception_rules) > 1
        ):
            raise ValueError("human quality rubric mismatch")
        return self


class Adjudication(Contract):
    schema_version: Literal[2] = 2
    adjudication_id: str
    run_id: str
    trial_key: tuple[str, str, str, str, int]
    evidence_hash: Hash
    rule_id: Literal["quality.supported"]
    rubric: Literal["quality-supported-v1"]
    reviewer: str = Field(min_length=1)
    role: Literal["human"]
    decision: Literal["pass", "fail", "abstain"]
    reason: str = Field(min_length=1)
    created_at: datetime
    expires_at: datetime
    record_ref: str
    record_hash: Hash
    dimensions: dict[Literal["relevance", "grounding", "actionability", "interaction_burden"], int]

    @model_validator(mode="after")
    def complete(self) -> Adjudication:
        if (
            self.created_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or self.expires_at <= self.created_at
            or set(self.dimensions)
            != {"relevance", "grounding", "actionability", "interaction_burden"}
            or any(type(v) is not int or not 0 <= v <= 3 for v in self.dimensions.values())
        ):
            raise ValueError("invalid human rubric record")
        return self


class IndependentReview(Contract):
    reviewer: str = Field(min_length=1)
    role: Literal["independent_reviewer"]
    candidate_snapshot_hash: Hash
    evaluator_hash: Hash
    record_ref: str
    record_hash: Hash
    disposition: Literal["accepted", "changes_required"]
    created_at: datetime
    expires_at: datetime


class QualityException(Contract):
    exception_id: str
    candidate_snapshot_hash: Hash
    trial_key: tuple[str, str, str, str, int]
    evidence_hash: Hash
    rule_id: Literal["quality.supported"]
    owner: str = Field(min_length=1)
    approver: str = Field(min_length=1)
    role: Literal["maintainer"]
    reason: str = Field(min_length=1)
    reassessment_trigger: str = Field(min_length=1)
    created_at: datetime
    expires_at: datetime
    status: Literal["active", "revoked"]
    record_ref: str
    record_hash: Hash


class ReviewBundle(Contract):
    schema_version: Literal[2] = 2
    adjudications: list[Adjudication] = Field(default_factory=list)
    independent_reviews: list[IndependentReview] = Field(default_factory=list)
    exceptions: list[QualityException] = Field(default_factory=list)


def verified_reference(path: str, expected: str) -> bool:
    p = Path(path)
    return p.is_file() and not p.is_symlink() and p.stat().st_size > 0 and file_hash(p) == expected


def aware_active(created: datetime, expires: datetime, max_days: int = 30) -> bool:
    now = datetime.now(timezone.utc)
    return bool(
        created.tzinfo
        and expires.tzinfo
        and created <= now < expires
        and 0 < (expires - created).total_seconds() <= max_days * 86400
    )


def product_hash(manifest: RunManifestV2, variant: str = "candidate") -> str:
    v = manifest.variants[variant]
    return digest({k: v[k] for k in ("commit", "diff_hash", "files_hash", "lock_hash", "runtime")})


def variable_values(m: RunManifestV2, variant: str) -> dict[str, str]:
    v = m.variants[variant]
    return {
        "product_snapshot": product_hash(m, variant),
        "provider_model": digest(
            m.provider_profile.model_dump(mode="json") if m.provider_profile else None
        ),
        "dependency_set": digest({"lock_hash": v["lock_hash"], "runtime": v["runtime"]}),
        "prompt_or_tool_surface": digest(v.get("prompt_or_tool_surface", None)),
        "context_policy": digest(v.get("context_policy", None)),
    }


def compare_core(
    a: RunManifestV2,
    ar: list[TrialResultV2],
    b: RunManifestV2,
    br: list[TrialResultV2],
    experiment: ExperimentSpec,
) -> dict[str, Any]:
    if a.run_id == b.run_id and set(a.variants) != {"baseline", "candidate"}:
        raise ValueError("candidate-only run cannot compare with itself")
    av = "baseline" if a.run_id == b.run_id else "candidate"
    left_values, right_values = variable_values(a, av), variable_values(b, "candidate")
    declarations: dict[str, ExperimentVariable] = {v.name: v for v in experiment.variables}
    reasons = []
    for name in left_values:
        changed = left_values[name] != right_values[name]
        declared = declarations.get(name)
        if declared and (
            declared.baseline != left_values[name] or declared.candidate != right_values[name]
        ):
            reasons.append(f"experiment.{name}.observation_differs")
        if changed and not declared:
            reasons.append(f"undeclared.{name}")
    for name in (
        "suite_id",
        "suite_hash",
        "evaluator_hash",
        "timezone",
        "mode",
        "repeats",
        "network_policy",
    ):
        if getattr(a, name) != getattr(b, name):
            reasons.append(name)
    env_a, env_b = ({k: v for k, v in m.environment.items() if k != "executable"} for m in (a, b))
    if env_a != env_b:
        reasons.append("evaluator_environment")
    if a.variants[av].get("fault") != b.variants["candidate"].get("fault"):
        reasons.append("fault_protocol")
    groups = []
    left = {
        (r["execution_item_id"], r["mode"], r["repeat_index"]): r
        for r in aggregate(a, ar)["trials"]
        if r["variant_id"] == av
    }
    right = {
        (r["execution_item_id"], r["mode"], r["repeat_index"]): r
        for r in aggregate(b, br)["trials"]
        if r["variant_id"] == "candidate"
    }
    for key in sorted(set(left) | set(right)):
        x, y = left.get(key), right.get(key)
        local = list(reasons)
        if not x or not y:
            local.append("unpaired_trial")
        elif any(
            x[k] != y[k] for k in ("case_version", "spec_hash", "fixture_hash", "coverage_kind")
        ) or [r["rule_id"] for r in x["rules"]] != [r["rule_id"] for r in y["rules"]]:
            local.append("case_contract")
        if x and y and a.mode == "real":
            # Provider alias alone cannot establish actual model identity.
            xa, ya = (
                x.get("consumption", {}).get("actual_models"),
                y.get("consumption", {}).get("actual_models"),
            )
            if not xa or not ya or xa != ya and "provider_model" not in declarations:
                local.append("actual_model_identity_unknown_or_changed")
        vx, vy = (x or {}).get("verdict", "incomplete"), (y or {}).get("verdict", "incomplete")
        category = (
            "incomparable"
            if local
            else "incomplete"
            if "incomplete" in {vx, vy}
            else {
                ("pass", "pass"): "passing",
                ("pass", "fail"): "new_failure",
                ("fail", "pass"): "fixed",
                ("fail", "fail"): "persistent_failure",
            }[(vx, vy)]
        )
        groups.append(
            dict(
                execution_item_id=key[0],
                mode=key[1],
                repeat_index=key[2],
                classification=category,
                reasons=local,
                baseline=x,
                candidate=y,
            )
        )
    item_groups = []
    for item in dict.fromkeys(r["execution_item_id"] for r in groups):
        rows = [r for r in groups if r["execution_item_id"] == item]
        values = {r["classification"] for r in rows}
        item_groups.append(
            dict(
                execution_item_id=item,
                repeats=len(rows),
                fluctuating=len(values) > 1,
                classification="incomparable"
                if "incomparable" in values
                else "incomplete"
                if "incomplete" in values
                else "fluctuating"
                if len(values) > 1
                else next(iter(values)),
            )
        )
    return dict(
        schema_version=2,
        baseline_run_id=str(a.run_id),
        candidate_run_id=str(b.run_id),
        groups=groups,
        item_groups=item_groups,
        changed_variables=[n for n in left_values if left_values[n] != right_values[n]],
        joint_change=sum(left_values[n] != right_values[n] for n in left_values) > 1,
        causality_claim=False,
        release_eligible=False,
        exit_code=1
        if any(r["classification"] in {"new_failure", "persistent_failure"} for r in groups)
        else 2
        if any(r["classification"] == "incomparable" for r in groups)
        else 3
        if any(r["classification"] == "incomplete" for r in groups)
        else 0,
    )


def gate_core(
    manifests: list[RunManifestV2],
    results: list[list[TrialResultV2]],
    policy: GatePolicyV2,
    review: ReviewBundle,
    purpose: str,
    mode: str,
    baseline: list[tuple[RunManifestV2, list[TrialResultV2]]] | None = None,
    experiment: ExperimentSpec | None = None,
    baseline_records: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if purpose not in policy.purposes or mode not in {"enforce", "observe"}:
        raise ValueError("gate purpose/mode not permitted")
    if (
        len(manifests) != len(results)
        or not manifests
        or len({m.mode for m in manifests}) != len(manifests)
    ):
        raise ValueError("one run per required mode")
    failures, errors, incomplete = [], [], []
    snapshot_hash = product_hash(manifests[0])
    selected: dict[tuple[str, str, int], TrialResultV2] = {}
    for manifest, rows in zip(manifests, results, strict=True):
        if manifest.suite_id != policy.suite_id or manifest.suite_hash != policy.suite_hash:
            errors.append("policy_suite_mismatch")
        if (
            product_hash(manifest) != snapshot_hash
            or manifest.variants["candidate"].get("fault") != "none"
        ):
            errors.append("candidate_snapshot_or_diagnostic_fault")
        aggregate(manifest, rows)
        selected.update(
            {
                (r.execution_item_id, r.mode, r.repeat_index): r
                for r in rows
                if r.variant_id == "candidate"
            }
        )
    required_modes = {r.mode for r in policy.absolute}
    if {m.mode for m in manifests} != required_modes:
        incomplete.append("required_mode_missing")
    adjudicated = []
    exception_status = []
    human_checks = []
    for requirement in policy.absolute:
        required_manifest = next((m for m in manifests if m.mode == requirement.mode), None)
        if required_manifest and required_manifest.repeats != requirement.repeats:
            incomplete.append(f"{requirement.execution_item_id}:{requirement.mode}:repeat_mismatch")
        for repeat in range(1, requirement.repeats + 1):
            key = (requirement.execution_item_id, requirement.mode, repeat)
            row = selected.get(key)
            if not row:
                incomplete.append(f"{key}:planned_trial_missing")
                continue
            if row.execution_status != "completed":
                incomplete.append(f"{key}:execution_incomplete")
            if row.mode == "real" and not row.consumption.get("actual_models"):
                incomplete.append(f"{key}:actual_model_identity_unknown")
            for rule in row.rules:
                status = rule.status
                if rule.rule_id == "quality.supported":
                    matching = [
                        r
                        for r in review.adjudications
                        if r.trial_key == row.identity()
                        and r.run_id == str(row.run_id)
                        and r.evidence_hash == row.evidence_hash
                        and r.rule_id == rule.rule_id
                        and aware_active(
                            r.created_at, r.expires_at, policy.review_rules[0].max_age_days
                        )
                        and verified_reference(r.record_ref, r.record_hash)
                    ]
                    decisions = {r.decision for r in matching}
                    status = (
                        "pass"
                        if decisions == {"pass"}
                        else "fail"
                        if decisions == {"fail"}
                        else "unknown"
                    )
                    human_completed = decisions in ({"pass"}, {"fail"})
                    human_checks.append(
                        dict(trial_key=row.identity(), decisions=sorted(decisions), status=status)
                    )
                    adjudicated += [r.adjudication_id for r in matching]
                    if rule.status == "fail":
                        status = "fail"
                    # Only a completed, explicit human failure can be excepted.
                    exceptions = [
                        v
                        for v in review.exceptions
                        if v.trial_key == row.identity()
                        and v.evidence_hash == row.evidence_hash
                        and v.candidate_snapshot_hash == snapshot_hash
                    ]
                    for exception in exceptions:
                        rule_config = next(
                            (v for v in policy.exception_rules if v.rule_id == exception.rule_id),
                            None,
                        )
                        active = bool(
                            rule_config
                            and status == "fail"
                            and human_completed
                            and exception.status == "active"
                            and aware_active(
                                exception.created_at, exception.expires_at, rule_config.max_days
                            )
                            and verified_reference(exception.record_ref, exception.record_hash)
                        )
                        exception_status.append(
                            dict(
                                exception_id=exception.exception_id,
                                active=active,
                                original_status=status,
                            )
                        )
                        if active:
                            status = "pass"
                if status == "fail":
                    failures.append(f"{key}:{rule.rule_id}")
                elif status != "pass":
                    incomplete.append(f"{key}:{rule.rule_id}")
    accepted_reviews = [
        r
        for r in review.independent_reviews
        if r.candidate_snapshot_hash == snapshot_hash
        and r.evaluator_hash == manifests[0].evaluator_hash
        and r.disposition == "accepted"
        and aware_active(r.created_at, r.expires_at)
        and verified_reference(r.record_ref, r.record_hash)
    ]
    if not accepted_reviews:
        incomplete.append("independent_review_record_missing_or_stale")
    if any(
        r.disposition == "changes_required"
        and r.candidate_snapshot_hash == snapshot_hash
        and aware_active(r.created_at, r.expires_at)
        for r in review.independent_reviews
    ):
        failures.append("independent_review_changes_required")
    relative: dict[str, Any] = {"applicable": purpose == "release", "status": "not_applicable"}
    if purpose == "release":
        if baseline is None and policy.relative.baseline_required:
            incomplete.append("approved_baseline_missing")
        elif baseline and experiment:
            relative = {"comparisons": []}
            for candidate_manifest, candidate_rows in zip(manifests, results, strict=True):
                old = next((v for v in baseline if v[0].mode == candidate_manifest.mode), None)
                record = (baseline_records or {}).get(candidate_manifest.mode)
                if not old:
                    incomplete.append("baseline_mode_missing")
                    continue
                if (
                    not record
                    or policy.relative.approved_baseline_id is not None
                    and record.get("baseline_id") != policy.relative.approved_baseline_id
                    or not any(
                        s.get("manifest_hash") == digest(old[0].model_dump(mode="json"))
                        for s in record.get("sources", [])
                    )
                ):
                    incomplete.append("approved_baseline_record_missing_or_mismatched")
                comparison = compare_core(*old, candidate_manifest, candidate_rows, experiment)
                if record and record.get("gate_path"):
                    old_gate_path = Path(record["gate_path"])
                    old_gate = json.loads(old_gate_path.read_bytes())
                    if digest(old_gate) != record["gate_hash"]:
                        errors.append("approved_baseline_gate_changed")
                    else:
                        old_policy = GatePolicyV2.model_validate(old_gate["frozen_policy"])
                        old_review = ReviewBundle.model_validate(old_gate["frozen_review"])
                        derived = compare_core(
                            old[0],
                            reviewed_trials(old[0], old[1], old_policy, old_review),
                            candidate_manifest,
                            reviewed_trials(candidate_manifest, candidate_rows, policy, review),
                            experiment,
                        )
                        comparison["derived_review_comparison"] = derived
                        comparison["raw_exit_code"] = comparison["exit_code"]
                        comparison["exit_code"] = derived["exit_code"]
                relative["comparisons"].append(comparison)
                if comparison["exit_code"] == 1:
                    failures.append("relative_new_or_persistent_failure")
                elif comparison["exit_code"] == 2:
                    errors.append("relative_incomparable")
                elif comparison["exit_code"] == 3:
                    incomplete.append("relative_incomplete")
        elif baseline:
            errors.append("experiment_missing")
    exit_code = 1 if failures else 2 if errors else 3 if incomplete else 0
    return dict(
        schema_version=2,
        gate_id=str(uuid4()),
        created_at=datetime.now(timezone.utc).isoformat(),
        policy_hash=digest(policy.model_dump(mode="json")),
        policy_id=policy.policy_id,
        purpose=purpose,
        mode=mode,
        candidate_snapshot_hash=snapshot_hash,
        evaluator_hash=manifests[0].evaluator_hash,
        sources=[
            dict(
                run_id=str(m.run_id),
                manifest_hash=digest(m.model_dump(mode="json")),
                directory=None,
            )
            for m in manifests
        ],
        frozen_policy=policy.model_dump(mode="json"),
        frozen_review=review.model_dump(mode="json"),
        frozen_experiment=experiment.model_dump(mode="json") if experiment else None,
        baseline_records=baseline_records or {},
        baseline_sources=[
            {"run_directory": None, "manifest_hash": digest(m.model_dump(mode="json"))}
            for m, _ in (baseline or [])
        ],
        integrity={"errors": errors},
        absolute={"failures": failures, "incomplete": incomplete},
        relative=relative,
        human_review={
            "adjudications": adjudicated,
            "checks": human_checks,
            "independent_records": [r.record_hash for r in accepted_reviews],
            "identity_authenticated": False,
        },
        exceptions=exception_status,
        gate_satisfied=exit_code == 0,
        release_eligible=False,
        exit_code=exit_code,
    )


def reviewed_trials(
    manifest: RunManifestV2, rows: list[TrialResultV2], policy: GatePolicyV2, review: ReviewBundle
) -> list[TrialResultV2]:
    """Separate derived views; original TrialResult and comparison rows stay intact."""
    derived = []
    for row in rows:
        rules = []
        for rule in row.rules:
            status = rule.status
            if rule.rule_id == "quality.supported":
                requirement = next(
                    (v for v in policy.review_rules if v.rule_id == rule.rule_id), None
                )
                decisions = {
                    v.decision
                    for v in review.adjudications
                    if requirement
                    and v.trial_key == row.identity()
                    and v.run_id == str(row.run_id)
                    and v.evidence_hash == row.evidence_hash
                    and aware_active(v.created_at, v.expires_at, requirement.max_age_days)
                    and verified_reference(v.record_ref, v.record_hash)
                }
                human_completed = decisions in ({"pass"}, {"fail"})
                status = (
                    "fail"
                    if rule.status == "fail" or decisions == {"fail"}
                    else "pass"
                    if decisions == {"pass"}
                    else "unknown"
                )
                exception_rule = next(
                    (v for v in policy.exception_rules if v.rule_id == rule.rule_id), None
                )
                if (
                    status == "fail"
                    and human_completed
                    and exception_rule
                    and any(
                        v.trial_key == row.identity()
                        and v.evidence_hash == row.evidence_hash
                        and v.candidate_snapshot_hash == product_hash(manifest, row.variant_id)
                        and v.status == "active"
                        and aware_active(v.created_at, v.expires_at, exception_rule.max_days)
                        and verified_reference(v.record_ref, v.record_hash)
                        for v in review.exceptions
                    )
                ):
                    status = "pass"
            rules.append(rule.model_copy(update={"status": status}))
        verdict = (
            "fail"
            if any(v.status == "fail" for v in rules)
            else "incomplete"
            if row.execution_status != "completed" or any(v.status != "pass" for v in rules)
            else "pass"
        )
        derived.append(row.model_copy(update={"rules": rules, "verdict": verdict}))
    return derived


def append_adjudication(directory: Path, record: Adjudication) -> Path:
    if not verified_reference(record.record_ref, record.record_hash):
        raise ValueError("actual review record unavailable")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", record.adjudication_id):
        raise ValueError("invalid adjudication id")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (record.adjudication_id + ".json")
    with path.open("x", encoding="utf-8") as f:
        json.dump(record.model_dump(mode="json"), f, ensure_ascii=False, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    return path


class PromotionApproval(Contract):
    baseline_id: str
    gate_hash: Hash
    run_manifest_hash: Hash
    reviewer: str = Field(min_length=1)
    role: Literal["maintainer"]
    created_at: datetime
    expires_at: datetime
    record_ref: str
    record_hash: Hash


def validate_baseline_id(baseline_id: str) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", baseline_id):
        raise ValueError("invalid baseline id")


@contextmanager
def store_lock(store: Path) -> Iterator[None]:
    """Kernel-owned lock: process death releases ownership; the file is harmless."""
    store.mkdir(parents=True, exist_ok=True)
    with (store / ".promotion-lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                posix_lock: Any = importlib.import_module("fcntl")
                posix_lock.flock(stream.fileno(), posix_lock.LOCK_EX | posix_lock.LOCK_NB)
        except OSError as exc:
            raise ValueError("baseline store busy") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                posix_lock.flock(stream.fileno(), posix_lock.LOCK_UN)


def store_events(store: Path) -> list[dict[str, Any]]:
    values = [json.loads(p.read_bytes()) for p in sorted((store / "events").glob("*.json"))]
    if [v.get("sequence") for v in values] != list(range(1, len(values) + 1)):
        raise ValueError("baseline journal sequence invalid")
    return values


def current_from_journal(store: Path) -> tuple[str | None, list[dict[str, Any]]]:
    """A durable event is the commit point; current.json is a recoverable cache."""
    current = None
    committed: set[str] = set()
    revoked: set[str] = set()
    events = store_events(store)
    for event in events:
        validate_baseline_id(event["baseline_id"])
        if event["previous"] != current or event["action"] not in {"promote", "revoke"}:
            raise ValueError("baseline journal transition invalid")
        path = store / "records" / (event["baseline_id"] + ".json")
        record = json.loads(path.read_bytes())
        if (
            path.is_symlink()
            or record.get("baseline_id") != event["baseline_id"]
            or digest(record) != event["record_hash"]
            or path.with_suffix(".reservation").read_text(encoding="utf-8") != digest(record)
        ):
            raise ValueError("baseline journal record changed")
        if event["action"] == "promote":
            if event["baseline_id"] in committed:
                raise ValueError("baseline id reused")
            committed.add(event["baseline_id"])
            current = event["baseline_id"]
        else:
            if event["baseline_id"] not in committed or event["baseline_id"] in revoked:
                raise ValueError("revocation transition invalid")
            revoked.add(event["baseline_id"])
            if current == event["baseline_id"]:
                current = None
    return current, events


def recover_current(store: Path) -> str | None:
    current, _ = current_from_journal(store)
    value = dict(baseline_id=current)
    path = store / "current.json"
    try:
        cached = json.loads(path.read_bytes()) if path.exists() else None
    except ValueError:
        cached = None
    if cached != value:
        atomic_json(path, value)
    return current


def append_store_event(store: Path, event: dict[str, Any]) -> None:
    sequence = len(store_events(store)) + 1
    atomic_json(store / "events" / f"{sequence:08d}_{uuid4()}.json", event | {"sequence": sequence})


def promote_baseline(
    store: Path,
    directory: Path,
    gate_path: Path,
    approval_path: Path,
    baseline_id: str,
    expected_current: str | None,
) -> dict[str, Any]:
    with store_lock(store):
        recover_current(store)
        return _promote_baseline(
            store, directory, gate_path, approval_path, baseline_id, expected_current
        )


def _promote_baseline(
    store: Path,
    directory: Path,
    gate_path: Path,
    approval_path: Path,
    baseline_id: str,
    expected_current: str | None,
) -> dict[str, Any]:
    validate_baseline_id(baseline_id)
    if expected_current is not None:
        validate_baseline_id(expected_current)
    manifest, rows = read_run(directory)
    gate = json.loads(gate_path.read_bytes())
    approval = PromotionApproval.model_validate_json(approval_path.read_bytes())
    if (
        approval.baseline_id != baseline_id
        or approval.gate_hash != digest(gate)
        or approval.run_manifest_hash != digest(manifest.model_dump(mode="json"))
        or not aware_active(approval.created_at, approval.expires_at)
        or not verified_reference(approval.record_ref, approval.record_hash)
        or gate.get("mode") != "enforce"
        or gate.get("gate_satisfied") is not True
        or gate.get("exit_code") != 0
        or gate.get("candidate_snapshot_hash") != product_hash(manifest)
        or gate.get("evaluator_hash") != manifest.evaluator_hash
        or not any(
            s.get("manifest_hash") == approval.run_manifest_hash for s in gate.get("sources", [])
        )
    ):
        raise ValueError("promotion approval or gate binding invalid")
    if gate.get("purpose") != ("initial_baseline" if expected_current is None else "release"):
        raise ValueError("promotion purpose differs from baseline lifecycle")
    policy = GatePolicyV2.model_validate(gate["frozen_policy"])
    review = ReviewBundle.model_validate(gate["frozen_review"])
    if digest(policy.model_dump(mode="json")) != gate["policy_hash"]:
        raise ValueError("frozen policy mismatch")
    run_pairs = []
    for source in gate["sources"]:
        source_directory = Path(source["directory"])
        m, r = read_run(source_directory)
        if digest(m.model_dump(mode="json")) != source["manifest_hash"]:
            raise ValueError("gate source changed")
        run_pairs.append((m, r))
    if not any(m.run_id == manifest.run_id for m, _ in run_pairs):
        raise ValueError("promoted run is absent from gate")
    baseline_pairs = []
    baseline_records: dict[str, dict[str, Any]] = {}
    if expected_current is not None:
        approved_record = load_baseline(store, expected_current)
        for source in approved_record["sources"]:
            m, r = read_run(Path(source["directory"]))
            if digest(m.model_dump(mode="json")) != source["manifest_hash"]:
                raise ValueError("approved baseline source changed")
            baseline_pairs.append((m, r))
            baseline_records[m.mode] = approved_record
    recomputed = gate_core(
        [m for m, _ in run_pairs],
        [r for _, r in run_pairs],
        policy,
        review,
        gate["purpose"],
        "enforce",
        baseline_pairs or None,
        ExperimentSpec.model_validate(gate["frozen_experiment"])
        if gate.get("frozen_experiment")
        else None,
        baseline_records,
    )
    if not recomputed["gate_satisfied"]:
        raise ValueError("gate no longer satisfies regraded evidence and actual review records")
    # Re-read every private fragment: a hash-consistent old summary is insufficient.
    aggregate(manifest, rows)
    if sum(r.variant_id == "candidate" for r in rows) != sum(
        p.variant_id == "candidate" for p in manifest.planned_trials
    ):
        raise ValueError("baseline candidate denominator incomplete")
    if any(
        r.execution_status != "completed"
        or any(
            v.status == "fail" or v.status == "unknown" and v.rule_id != "quality.supported"
            for v in r.rules
        )
        for r in rows
        if r.variant_id == "candidate"
    ):
        raise ValueError("baseline private evidence incomplete or failing")
    current_path = store / "current.json"
    current = recover_current(store)
    if current != expected_current:
        raise ValueError("current baseline changed; compare-and-swap conflict")
    records = store / "records"
    records.mkdir(exist_ok=True)
    record_path = records / (baseline_id + ".json")
    if record_path.exists():
        raise ValueError("baseline id already exists")
    record = dict(
        schema_version=2,
        baseline_id=baseline_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        run_directory=str(directory.resolve()),
        manifest_hash=digest(manifest.model_dump(mode="json")),
        sources=gate["sources"],
        product_snapshot_hash=product_hash(manifest),
        suite_hash=manifest.suite_hash,
        evaluator_hash=manifest.evaluator_hash,
        gate_hash=digest(gate),
        gate_path=str(gate_path.resolve()),
        approval=approval.model_dump(mode="json"),
        purpose=gate["purpose"],
        limitations=[
            "local attestation; no identity authentication",
            "private evidence required for strict comparison",
        ],
    )
    # Exclusive ID reservation plus atomic replacement; an interrupted write leaves only an explicit reservation.
    reservation = records / (baseline_id + ".reservation")
    with reservation.open("x", encoding="utf-8") as f:
        f.write(digest(record))
        f.flush()
        os.fsync(f.fileno())
    atomic_json(record_path, record)
    append_store_event(
        store,
        dict(
            action="promote", previous=current, baseline_id=baseline_id, record_hash=digest(record)
        ),
    )
    atomic_json(current_path, dict(baseline_id=baseline_id))
    return record


def load_baseline(store: Path, baseline_id: str) -> dict[str, Any]:
    validate_baseline_id(baseline_id)
    path = store / "records" / (baseline_id + ".json")
    value: dict[str, Any] = json.loads(path.read_bytes())
    reservation = path.with_suffix(".reservation")
    if (
        path.is_symlink()
        or value.get("baseline_id") != baseline_id
        or digest(value) != reservation.read_text(encoding="utf-8")
    ):
        raise ValueError("baseline immutable record changed")
    if not value.get("sources"):
        raise ValueError("baseline evidence sources unavailable")
    _, events = current_from_journal(store)
    if not any(
        e.get("action") == "promote"
        and e.get("baseline_id") == baseline_id
        and e.get("record_hash") == digest(value)
        for e in events
    ):
        raise ValueError("baseline promotion not committed")
    for event in events:
        if event.get("action") == "revoke" and event.get("baseline_id") == baseline_id:
            raise ValueError("baseline revoked; history remains available")
    return value


def revoke_baseline(
    store: Path, baseline_id: str, reason: str, reference: str, reference_hash: str
) -> None:
    validate_baseline_id(baseline_id)
    if not reason or not verified_reference(reference, reference_hash):
        raise ValueError("revocation requires actual maintainer record")
    with store_lock(store):
        current = recover_current(store)
        record = load_baseline(store, baseline_id)
        append_store_event(
            store,
            dict(
                action="revoke",
                previous=current,
                baseline_id=baseline_id,
                record_hash=digest(record),
                reason=reason,
                reference=reference,
                reference_hash=reference_hash,
            ),
        )
        recover_current(store)


def statistics(manifest: RunManifestV2, rows: list[TrialResultV2]) -> dict[str, Any]:
    return aggregate(manifest, rows)
