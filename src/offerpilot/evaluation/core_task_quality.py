"""Versioned, output-bound task quality and explicit scenario risk acceptance.

These are local attestations, not authenticated identities or judge calibration.
Neither a task label nor a scenario acceptance changes the original trial.
"""

from __future__ import annotations

from datetime import datetime
import json
from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import Any, Literal

from pydantic import Field, model_validator

from offerpilot.evaluation.contracts import Contract, Hash, Positive
from offerpilot.evaluation.core_contracts import RunManifestV2, TrialResultV2
from offerpilot.evaluation.core_governance import (
    IndependentReview, RelativePolicy, RequiredItem, aware_active, product_hash,
    verified_reference,
)
from offerpilot.evaluation.core_quality_outputs import output_inventory
from offerpilot.evaluation.core_registry import ITEMS, suite_case_version
from offerpilot.evaluation.registry import digest

Decision = Literal["pass", "fail", "abstain"]
TASK_RULES = frozenset({"quality.supported", "quality.current_goal"})


class ScenarioQualityPolicy(Contract):
    execution_item_id: str
    mode: Literal["real"] = "real"
    rule_id: Literal["quality.supported", "quality.current_goal"]
    rubric: Literal["business-quality-v2", "current-goal-v2"]
    method: Literal["human_scenario_acceptance"] = "human_scenario_acceptance"
    approver_role: Literal["maintainer"] = "maintainer"
    max_age_days: Positive = Field(default=30, le=30)

    @model_validator(mode="after")
    def registered(self) -> ScenarioQualityPolicy:
        override = self.execution_item_id == "E09.memory_override"
        if (
            self.execution_item_id not in ITEMS
            or ITEMS[self.execution_item_id].get("profile") != "context_sources.v1"
            or self.rule_id != ("quality.current_goal" if override else "quality.supported")
            or self.rubric != ("current-goal-v2" if override else "business-quality-v2")
        ):
            raise ValueError("unregistered scenario quality contract")
        return self


class GatePolicyV3(Contract):
    schema_version: Literal[3] = 3
    policy_id: str = Field(min_length=1)
    version: Positive
    suite_id: Literal["harness-core-business-v1"] = "harness-core-business-v1"
    suite_hash: Hash
    purposes: list[Literal["release", "initial_baseline"]] = Field(min_length=1)
    absolute: list[RequiredItem] = Field(min_length=1)
    relative: RelativePolicy = Field(default_factory=RelativePolicy)
    quality_scenarios: list[ScenarioQualityPolicy] = Field(default_factory=list)
    independent_review_required: Literal[True] = True
    rationale: str = Field(min_length=1)
    review_reference: str = Field(min_length=1)

    @model_validator(mode="after")
    def closed(self) -> GatePolicyV3:
        keys = [(r.execution_item_id, r.mode) for r in self.absolute]
        if len(keys) != len(set(keys)) or len(set(self.purposes)) != len(self.purposes):
            raise ValueError("duplicate required item or purpose")
        for mode in {r.mode for r in self.absolute}:
            expected = {i for i, v in ITEMS.items() if mode == "deterministic" or v["real"]}
            if {i for i, m in keys if m == mode} != expected:
                raise ValueError("business policy must retain complete registered denominator")
        if any(r.case_version != suite_case_version(self.suite_id, r.execution_item_id)
               for r in self.absolute):
            raise ValueError("business policy case version mismatch")
        actual = {(q.execution_item_id, q.mode, q.rule_id) for q in self.quality_scenarios}
        expected_quality = {
            (r.execution_item_id, r.mode, rule)
            for r in self.absolute for rule in r.rules if rule in TASK_RULES
        }
        if len(actual) != len(self.quality_scenarios) or actual != expected_quality:
            raise ValueError("quality policy cannot omit or reclassify required quality rules")
        return self


class HumanQualityRecord(Contract):
    schema_version: Literal[3] = 3
    review_id: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    trial_key: tuple[str, str, str, str, int]
    evidence_hash: Hash
    request_number: Positive
    call_numbers: list[Positive] = Field(min_length=1)
    answer_sha256: Hash
    output_binding_hash: Hash
    review_pack_ref: str
    review_pack_hash: Hash
    rubric: Literal["business-quality-v2", "current-goal-v2"]
    task_decision: Decision
    critical_decision: Decision
    task_reason: str = Field(min_length=1)
    critical_reason: str = Field(min_length=1)
    supporting_sentences: list[str]
    expression_notes: list[str] = Field(default_factory=list)
    reviewer: str = Field(min_length=1)
    role: Literal["human"] = "human"
    confirmation: Literal["confirmed"]
    label_source: Literal["manual_human", "human_adopted_ai"]
    created_at: datetime
    expires_at: datetime
    record_ref: str
    record_hash: Hash
    confirmation_source: dict[str, str] | None = None

    @model_validator(mode="after")
    def bound(self) -> HumanQualityRecord:
        if self.call_numbers != sorted(set(self.call_numbers)):
            raise ValueError("quality call identities missing or repeated")
        return self


class ScenarioAcceptance(Contract):
    schema_version: Literal[3] = 3
    acceptance_id: str = Field(min_length=1)
    run_id: str
    manifest_hash: Hash
    candidate_snapshot_hash: Hash
    policy_hash: Hash
    execution_item_id: str
    mode: Literal["real"] = "real"
    variant_id: Literal["candidate", "baseline"] = "candidate"
    reviewed_outputs_hash: Hash
    decision: Literal["accepted", "rejected"]
    accepted_failure_ids: list[str]
    reason: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    role: Literal["maintainer"]
    confirmation: Literal["confirmed"]
    created_at: datetime
    expires_at: datetime
    record_ref: str
    record_hash: Hash
    confirmation_source: dict[str, str] | None = None


class TaskReviewBundle(Contract):
    schema_version: Literal[3] = 3
    quality_reviews: list[HumanQualityRecord] = Field(default_factory=list)
    scenario_acceptances: list[ScenarioAcceptance] = Field(default_factory=list)
    independent_reviews: list[IndependentReview] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self) -> TaskReviewBundle:
        for values in [
            [v.review_id for v in self.quality_reviews],
            [v.sample_id for v in self.quality_reviews],
            [v.acceptance_id for v in self.scenario_acceptances],
        ]:
            if len(values) != len(set(values)):
                raise ValueError("duplicate quality review or sample")
        scopes = [(v.run_id, v.variant_id, v.execution_item_id, v.mode)
                  for v in self.scenario_acceptances]
        if len(scopes) != len(set(scopes)):
            raise ValueError("duplicate scenario acceptance")
        return self


def confirmed_record(value: HumanQualityRecord | ScenarioAcceptance, max_days: int) -> bool:
    if not aware_active(value.created_at, value.expires_at, max_days):
        return False
    if not verified_reference(value.record_ref, value.record_hash):
        return False
    try:
        original = json.loads(Path(value.record_ref).read_bytes())
        if isinstance(value, HumanQualityRecord):
            from offerpilot.evaluation.core_review import CURRENT_GOAL_RUBRIC
            from offerpilot.evaluation.core_task_review import BUSINESS_QUALITY_RUBRIC

            if not verified_reference(value.review_pack_ref, value.review_pack_hash):
                return False
            pack = json.loads(Path(value.review_pack_ref).read_bytes())
            matches = []
            for sample in pack.get("samples", []):
                binding = sample.get("output_binding") or {}
                if (
                    sample.get("sample_id") == value.sample_id
                    and digest(binding) == value.output_binding_hash
                    and sample.get("answer") == binding.get("answer")
                    and isinstance(sample.get("answer"), str)
                    and sha256(sample["answer"].encode()).hexdigest()
                    == sample.get("answer_sha256") == binding.get("answer_sha256") == value.answer_sha256
                    and sample.get("request_number") == binding.get("request_number") == value.request_number
                    and sample.get("call_numbers") == binding.get("call_numbers") == value.call_numbers
                    and sample.get("user_request") == binding.get("user_request")
                    and sample.get("rubric") == (
                        CURRENT_GOAL_RUBRIC if value.rubric == "current-goal-v2" else BUSINESS_QUALITY_RUBRIC
                    )
                ):
                    matches.append(sample)
            if pack.get("schema_version") != 3 or pack.get("kind") != "task-quality-pack" or len(matches) != 1:
                return False
        if value.confirmation_source is not None and (
            set(value.confirmation_source) != {"path", "sha256"}
            or not verified_reference(value.confirmation_source["path"], value.confirmation_source["sha256"])
        ):
            return False
    except (OSError, ValueError, TypeError, KeyError):
        return False
    # A file hash alone cannot bind the actual decisions to the signed source.
    return bool(original == value.model_dump(mode="json", exclude={"record_ref", "record_hash"}))


def quality_views(
    manifest: RunManifestV2, rows: list[TrialResultV2], policy: GatePolicyV3,
    review: TaskReviewBundle,
) -> tuple[list[TrialResultV2], list[dict[str, Any]]]:
    """Derive dispositions; raw task failures remain in the attached statistics."""
    dispositions: dict[tuple[str, str, str, str, int], tuple[str, str]] = {}
    summaries = []
    for variant in manifest.variants:
        for scenario in policy.quality_scenarios:
            if manifest.mode != scenario.mode:
                continue
            plans = [
                p for p in manifest.planned_trials
                if p.variant_id == variant and p.execution_item_id == scenario.execution_item_id
            ]
            selected = [
                r for r in rows
                if r.variant_id == variant and r.execution_item_id == scenario.execution_item_id
            ]
            selected.sort(key=lambda r: r.repeat_index)
            gaps, outputs, labels, observed_outputs = [], [], [], []
            execution_observed = 0
            subject_started, request_accepted = 0, 0
            observed_output_trials: set[tuple[str, str, str, str, int]] = set()
            inventories: dict[tuple[str, str, str, str, int], dict[str, Any]] = {}
            known_critical_failures = []
            requirement = next(
                r for r in policy.absolute
                if r.execution_item_id == scenario.execution_item_id and r.mode == scenario.mode
            )
            if len(plans) != requirement.repeats:
                gaps.append("policy_required_repeat_coverage_incomplete")
            for row in selected:
                if row.execution_status != "completed":
                    gaps.append(f"{row.identity()}:execution_incomplete")
                if row.mode == "real" and not row.consumption.get("actual_models"):
                    gaps.append(f"{row.identity()}:actual_model_identity_unknown")
                for rule in row.rules:
                    if rule.rule_id not in TASK_RULES and rule.status != "pass":
                        gaps.append(f"{row.identity()}:{rule.rule_id}:{rule.status}")
                try:
                    inventory = output_inventory(row)
                except (ValueError, OSError, KeyError, TypeError, sqlite3.Error):
                    gaps.append(f"{row.identity()}:output_evidence_incomplete")
                    continue
                actual = inventory["outputs"]
                inventories[row.identity()] = inventory
                observed_outputs.extend(inventory["observed_outputs"])
                if inventory["observed_outputs"]:
                    observed_output_trials.add(row.identity())
                gaps.extend(f"{row.identity()}:{v}" for v in inventory["gaps"])
                execution_observed += inventory["execution_observed"] is True
                subject_started += inventory["subject_started_observed"] is True
                request_accepted += inventory["request_accepted_observed"] is True
                outputs.extend(actual)
                for output in actual:
                    matching = [
                        q for q in review.quality_reviews
                        if q.sample_id == output["sample_id"]
                        and q.trial_key == row.identity()
                        and q.evidence_hash == row.evidence_hash
                        and q.request_number == output["request_number"]
                        and q.call_numbers == output["call_numbers"]
                        and q.answer_sha256 == output["answer_sha256"]
                        and q.output_binding_hash == digest(output)
                        and q.rubric == scenario.rubric
                        and confirmed_record(q, scenario.max_age_days)
                    ]
                    if len(matching) != 1:
                        gaps.append(f"{output['sample_id']}:unreviewed_or_misbound")
                        continue
                    label = matching[0]
                    labels.append(label)
                    if label.critical_decision == "fail":
                        known_critical_failures.append(label.review_id)
                    if "abstain" in {label.task_decision, label.critical_decision}:
                        gaps.append(f"{output['sample_id']}:abstain")
            if {r.identity() for r in selected} != {p.identity() for p in plans}:
                gaps.append("planned_trial_missing")
            if not plans or len(outputs) != len(labels):
                gaps.append("quality_output_coverage_incomplete")
            failures = [q.review_id for q in labels if q.task_decision == "fail"]
            review_digest = digest([
                dict(output=o, review=q.model_dump(mode="json"))
                for o, q in zip(outputs, labels, strict=False)
            ]) if not gaps else None
            candidates = [
                a for a in review.scenario_acceptances
                if a.run_id == str(manifest.run_id) and a.variant_id == variant
                and a.execution_item_id == scenario.execution_item_id and a.mode == manifest.mode
                and a.manifest_hash == digest(manifest.model_dump(mode="json"))
                and a.candidate_snapshot_hash == product_hash(manifest, variant)
                and a.policy_hash == digest(policy.model_dump(mode="json"))
                and a.reviewed_outputs_hash == review_digest
                and sorted(a.accepted_failure_ids) == sorted(failures)
                and confirmed_record(a, scenario.max_age_days)
            ]
            acceptance = candidates[0] if len(candidates) == 1 else None
            disposition = (
                "fail" if known_critical_failures
                else "unknown" if gaps or acceptance is None
                else "pass" if acceptance.decision == "accepted" else "fail"
            )
            reason = (
                "critical_business_floor_failure" if known_critical_failures
                else "scenario_review_incomplete" if disposition == "unknown"
                else "scenario_risk_accepted" if disposition == "pass"
                else "scenario_risk_rejected"
            )
            for row in selected:
                dispositions[row.identity()] = (disposition, reason)
            judged = [
                q for q in labels
                if q.task_decision != "abstain" and q.critical_decision != "abstain"
            ]
            task_passes = [q for q in judged if q.task_decision == q.critical_decision == "pass"]
            expected_outputs = len(plans) * (2 if scenario.execution_item_id == "E09.memory_withdraw" else 1)
            successful_trials = 0
            for row in selected:
                own = [q for q in labels if q.trial_key == row.identity()]
                inv = inventories.get(row.identity())
                if (
                    inv and not inv["gaps"] and len(own) == inv["expected_requests"]
                    and all(q.task_decision == q.critical_decision == "pass" for q in own)
                    and row.execution_status == "completed"
                    and all(r.status == "pass" for r in row.rules if r.rule_id not in TASK_RULES)
                ):
                    successful_trials += 1
            summaries.append(dict(
                execution_item_id=scenario.execution_item_id, variant_id=variant, mode=manifest.mode,
                run_id=str(manifest.run_id),
                manifest_hash=digest(manifest.model_dump(mode="json")),
                evaluator_hash=manifest.evaluator_hash,
                product_snapshot_hash=product_hash(manifest, variant),
                provider_profile_hash=manifest.provider_profile_hash,
                case_versions=sorted({r.case_version for r in selected}),
                spec_hashes=sorted({r.spec_hash for r in selected}),
                fixture_hashes=sorted({r.fixture_hash for r in selected}),
                actual_models=sorted({
                    model for r in selected for model in (r.consumption.get("actual_models") or [])
                }),
                rubric=scenario.rubric, planned_trials=len(plans),
                policy_required_trials=requirement.repeats,
                expected_output_slots=expected_outputs,
                subject_started_observed_trials=subject_started,
                subject_start_unknown_trials=len(plans)-subject_started,
                request_accepted_observed_trials=request_accepted,
                request_acceptance_unknown_trials=len(plans)-request_accepted,
                execution_observed_trials=execution_observed,
                execution_unknown_trials=len(plans)-execution_observed,
                execution_unit="Bound product Runtime worker started or completed terminal; a subject handshake is only startup",
                observed_output_trials=len(observed_output_trials),
                observed_outputs=len(observed_outputs), bound_outputs=len(outputs),
                reviewed_outputs=len(labels),
                judged_outputs=len(judged),
                unreviewed_observed_outputs=max(0, len(observed_outputs)-len(labels)),
                unobserved_output_slots=max(0, expected_outputs-len(observed_outputs)),
                task_pass=sum(q.task_decision == "pass" for q in labels),
                task_fail=sum(q.task_decision == "fail" for q in labels),
                task_abstain=sum(q.task_decision == "abstain" for q in labels),
                critical_failures=known_critical_failures, task_failure_ids=failures,
                expression_notes=[dict(review_id=q.review_id, notes=q.expression_notes) for q in labels],
                required_evidence_or_review_gaps=gaps, reviewed_outputs_hash=review_digest,
                scenario_acceptance=None if acceptance is None else acceptance.acceptance_id,
                disposition=disposition, raw_labels_changed=False,
                review_state="needs_review" if disposition == "unknown" else "accepted"
                if disposition == "pass" else "rejected_or_critical_failure",
                end_to_end_task_completion=dict(numerator=successful_trials, denominator=len(plans)),
                conditional_quality_success=dict(numerator=len(task_passes), denominator=len(judged)),
                output_coverage=dict(numerator=len(observed_outputs), denominator=expected_outputs),
                judgeability=dict(numerator=len(judged), denominator=len(observed_outputs)),
                failure_impact=[
                    dict(
                        sample_id=q.sample_id, trial_key=q.trial_key,
                        request_number=q.request_number, call_numbers=q.call_numbers,
                        answer_sha256=q.answer_sha256,
                        task_decision=q.task_decision, critical_decision=q.critical_decision,
                        task_reason=q.task_reason, critical_reason=q.critical_reason,
                        supporting_sentences=q.supporting_sentences,
                        source=dict(path=q.record_ref, sha256=q.record_hash),
                    )
                    for q in labels if "fail" in {q.task_decision, q.critical_decision}
                ],
                independent_sample_count=None,
                scope="Correlated outputs/repeats; scenario dispositions in derived trial views "
                "are not new independent output labels. Quality acceptance does not waive hard trial rules",
            ))
    derived = []
    for row in rows:
        disposition, reason = dispositions.get(
            row.identity(), ("unknown", "scenario_review_incomplete")
        )
        rules = [
            rule.model_copy(update={"status": disposition, "reason_code": reason})
            if rule.rule_id in TASK_RULES else rule.model_copy()
            for rule in row.rules
        ]
        verdict = (
            "fail" if any(rule.status == "fail" for rule in rules)
            else "incomplete" if row.execution_status != "completed"
            or any(rule.status != "pass" for rule in rules) else "pass"
        )
        derived.append(row.model_copy(update={"rules": rules, "verdict": verdict}))
    return derived, summaries
