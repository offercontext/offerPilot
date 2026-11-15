from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Positive = Annotated[int, Field(strict=True, ge=1)]
Mode = Literal["deterministic", "real"]
Variant = Literal["baseline", "candidate"]
CoverageKind = Literal["task", "full_recovery", "observation_only"]
RuleStatus = Literal["pass", "fail", "unknown", "not_applicable"]
Verdict = Literal["pass", "fail", "incomplete"]
Termination = Literal[
    "product_budget", "evaluator_watchdog", "provider_timeout", "explicit_user_stop",
    "normal", "unknown",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductBudgetSpec(Contract):
    policy: Literal["observe_current"] = "observe_current"


class EvaluatorLimits(Contract):
    wall_seconds: Positive = 180
    model_calls: Positive = 6
    tool_attempts: Positive = 12
    script_replies: Positive = 4
    startup_seconds: Positive = 30
    shutdown_seconds: Positive = 30


class GatePolicy(Contract):
    absolute: dict[str, Any] = Field(default_factory=dict)
    relative: dict[str, Any] = Field(default_factory=dict)
    exception_rules: list[dict[str, Any]] = Field(default_factory=list)
    release_eligible: Literal[False] = False


class ExecutionItem(Contract):
    case_id: Literal["E01", "E04", "E06"]
    case_version: Positive
    execution_item_id: str
    spec_path: str
    required: Literal[True] = True
    coverage_kind: CoverageKind
    repeats: dict[Mode, Positive]

    @field_validator("spec_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value
                or path.as_posix() != value):
            raise ValueError("spec_path must be a canonical relative evals path")
        return value


class Suite(Contract):
    schema_version: Literal[1] = 1
    suite_id: Literal["harness-p0-v1"]
    execution_items: list[ExecutionItem]

    @model_validator(mode="after")
    def unique_items(self) -> Suite:
        ids = [item.execution_item_id for item in self.execution_items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate execution_item_id")
        if set(ids) != {"E01.runtime_read", "E04.runtime_edit", "E06.legacy_saved"}:
            raise ValueError("P0 requires all three execution items")
        if any(item.repeats != {"deterministic": 1} for item in self.execution_items):
            raise ValueError("P0 supports exactly one deterministic repeat")
        return self


class CaseSpec(Contract):
    schema_version: Literal[1] = 1
    case_id: Literal["E01", "E04", "E06"]
    case_version: Positive
    execution_item_id: str
    coverage_kind: CoverageKind
    adapter: Literal["runtime-v1", "legacy-chat"]
    fixture_id: Literal["synthetic-interviews-v1"]
    action_id: str
    model_id: Literal["interviews-v1"]
    fault_id: Literal["none", "after_persist"]
    required_rules: list[str]
    product_budget: ProductBudgetSpec = Field(default_factory=ProductBudgetSpec)
    evaluator_limits: EvaluatorLimits = Field(default_factory=EvaluatorLimits)

    @field_validator("required_rules")
    @classmethod
    def unique_rules(cls, values: list[str]) -> list[str]:
        if not values or len(values) != len(set(values)):
            raise ValueError("required rules must be nonempty and unique")
        return values


class TrialKey(Contract):
    run_id: UUID
    variant_id: Variant
    execution_item_id: str
    mode: Mode
    repeat_index: Positive

    def identity(self) -> tuple[str, str, str, str, int]:
        return (str(self.run_id), self.variant_id, self.execution_item_id,
                self.mode, self.repeat_index)


class PlannedTrial(TrialKey):
    case_id: str
    case_version: Positive
    spec_hash: Hash
    fixture_hash: Hash
    coverage_kind: CoverageKind
    required_rules: list[str]

    @field_validator("required_rules")
    @classmethod
    def unique_required_rules(cls, value: list[str]) -> list[str]:
        if not value or any(not name for name in value) or len(set(value)) != len(value):
            raise ValueError("required rules must be nonempty and unique")
        return value


class VariantConfig(Contract):
    variant_id: Variant
    checkout: str
    interpreter: str
    fault: Literal["none", "wrong_target", "wrong_payload", "duplicate_effect",
                   "unapproved_compensation", "bad_receipt", "late_write", "shutdown_timeout"] = "none"


class RunManifest(Contract):
    schema_version: Literal[1] = 1
    run_id: UUID
    plan_kind: Literal["p0", "diagnostic"] = "p0"
    parent_run_id: UUID | None = None
    suite_id: str
    suite_hash: Hash
    config_hash: Hash
    bridge_version: str = "p0-v1"
    observer_version: str = "p0-v1"
    grader_version: str = "p0-v1"
    evaluator_hash: Hash
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    allowed_variables: list[str] = Field(default_factory=lambda: ["product_snapshot"])
    variants: dict[str, dict[str, Any]]
    environment: dict[str, Any]
    planned_trials: list[PlannedTrial]
    release_eligible: Literal[False] = False

    @model_validator(mode="after")
    def consistent_plan(self) -> RunManifest:
        keys = [p.identity() for p in self.planned_trials]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("empty plan or duplicate trial_key")
        bindings: dict[str, tuple[Any, ...]] = {}
        if not self.variants or set(self.variants) - {"baseline", "candidate"}:
            raise ValueError("unregistered variant")
        for p in self.planned_trials:
            if p.run_id != self.run_id or p.variant_id not in self.variants:
                raise ValueError("trial identity outside run")
            binding = (p.case_id, p.case_version, p.spec_hash, p.fixture_hash, p.coverage_kind, tuple(p.required_rules))
            if p.execution_item_id in bindings and bindings[p.execution_item_id] != binding:
                raise ValueError("execution item has conflicting binding")
            bindings[p.execution_item_id] = binding
        if self.plan_kind == "p0":
            expected = {(v, item, "deterministic", 1) for v in self.variants for item in (
                "E01.runtime_read", "E04.runtime_edit", "E06.legacy_saved")}
            actual = {(p.variant_id, p.execution_item_id, p.mode, p.repeat_index)
                      for p in self.planned_trials}
            if actual != expected:
                raise ValueError("P0 plan must contain suite x variants x repeats")
            from offerpilot.evaluation.registry import RULES
            if any(p.required_rules != RULES[p.execution_item_id]
                   or p.case_id != p.execution_item_id.split(".")[0] or p.case_version != 1
                   or p.coverage_kind != ("full_recovery" if p.case_id == "E06" else "task")
                   for p in self.planned_trials):
                raise ValueError("P0 required rules differ from registered contract")
        return self


class ApprovalEvidence(Contract):
    pending: dict[str, Any]
    original_proposal: dict[str, Any]
    approved: bool
    effective_args: dict[str, Any]
    request_hash: Hash
    operation_id: str
    conversation_id: int
    turn_id: str | None
    generation: int | None
    received_seq: int | None = None
    decision_seq: int | None = None


class ObservationCoverage(Contract):
    observer_version: str = "p0-v1"
    tables: list[str] = Field(default_factory=lambda: ["applications", "application_events"])
    operations: list[str] = Field(default_factory=lambda: ["INSERT", "UPDATE", "DELETE"])
    columns: dict[str, list[str]]
    schema_hash: Hash
    trigger_hash: Hash
    sqlite_settings: dict[str, Any]
    epochs: list[int]
    start_seq: int
    end_seq: int | None = None
    status: Literal["open", "closed", "closed_after_crash", "gap"] = "open"
    gaps: list[str] = Field(default_factory=list)
    interference: list[str] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=lambda: ["REPLACE", "cascade", "DDL"])


class CommittedDelta(Contract):
    evidence_id: str
    seq: Positive
    epoch: Positive
    table: Literal["applications", "application_events"]
    operation: Literal["INSERT", "UPDATE", "DELETE"]
    row_key: str
    old: dict[str, Any] | None
    new: dict[str, Any] | None
    transaction_id: str | None = None
    writer_id: str | None = None


class EvidenceBundle(Contract):
    initial: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    milestones: dict[str, Any] = Field(default_factory=dict)
    final: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    deltas: list[CommittedDelta] = Field(default_factory=list)
    markers: list[dict[str, Any]] = Field(default_factory=list)
    approval: ApprovalEvidence | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    http: list[dict[str, Any]] = Field(default_factory=list)
    calls: list[dict[str, Any]] = Field(default_factory=list)
    faults: list[dict[str, Any]] = Field(default_factory=list)
    coverage: ObservationCoverage | None = None
    files: dict[str, dict[str, Any]] = Field(default_factory=dict)
    fragment_errors: dict[str, str] = Field(default_factory=dict)
    fragment_hashes: dict[str, Hash] = Field(default_factory=dict)


class RuleResult(Contract):
    rule_id: str
    required: bool = True
    status: RuleStatus
    evidence_refs: list[str] = Field(default_factory=list)
    reason_code: str


class TrialResult(TrialKey):
    case_id: str
    case_version: Positive
    execution_status: Literal["completed", "not_run", "setup_error", "eval_error", "product_failed",
                              "unsupported", "interrupted"]
    verdict: Verdict
    rules: list[RuleResult]
    known_failures: list[str]
    unknowns: list[str]
    termination_source: Termination = "normal"
    termination_events: list[dict[str, Any]] = Field(default_factory=list)
    consumption: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    faulted: bool = False

    @model_validator(mode="after")
    def result_consistency(self) -> TrialResult:
        ids = [r.rule_id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate rule result")
        failures = [r.rule_id for r in self.rules if r.required and r.status == "fail"]
        unknowns = [r.rule_id for r in self.rules if r.required and r.status == "unknown"]
        if failures != self.known_failures or unknowns != self.unknowns:
            raise ValueError("failure/unknown lists must reflect rules")
        if failures and self.verdict != "fail":
            raise ValueError("known failures must take precedence")
        if self.verdict == "pass" and (unknowns or not self.rules or any(
            r.required and r.status != "pass" for r in self.rules
        ) or self.execution_status != "completed"):
            raise ValueError("pass requires complete execution and required rules")
        return self
