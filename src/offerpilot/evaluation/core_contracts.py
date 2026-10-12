from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from offerpilot.evaluation.contracts import (
    Contract,
    CoverageKind,
    EvaluatorLimits,
    Hash,
    Mode,
    PlannedTrial,
    Positive,
    RuleResult,
    TrialKey,
)
from offerpilot.evaluation.core_registry import (
    FAULTS,
    ITEMS,
    case_definition,
    required_rules,
)


class ProductBudgetSpecV2(Contract):
    policy: Literal["observe_current", "configure"] = "observe_current"
    timeout_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    max_model_calls: int | None = Field(default=None, strict=True, ge=1)
    max_title_model_calls: int | None = Field(default=None, strict=True, ge=0)
    max_tool_calls: int | None = Field(default=None, strict=True, ge=1)

    @model_validator(mode="after")
    def configured(self) -> ProductBudgetSpecV2:
        values = [
            self.timeout_seconds,
            self.max_model_calls,
            self.max_title_model_calls,
            self.max_tool_calls,
        ]
        if (self.policy == "configure" and any(v is None for v in values)) or (
            self.policy == "observe_current" and any(v is not None for v in values)
        ):
            raise ValueError("budget policy and requested limits disagree")
        return self


class CaseSpecV2(Contract):
    schema_version: Literal[2] = 2
    case_id: str
    case_version: Positive
    execution_item_id: str
    fixture_id: Literal["synthetic-core-v1"]
    action_id: str
    model_id: Literal["core-script-v1"]
    grader_id: Literal["core-v1"]
    supported_modes: list[Mode]
    required_capabilities: list[str]
    coverage_kind: CoverageKind
    coverage_profile: Literal["business.v1", "context_sources.v1"]
    fault_profile: Literal[
        "none", "after_persist", "before_persist", "model_block", "executor_block"
    ]
    required_rules: list[str]
    product_budget: ProductBudgetSpecV2
    evaluator_limits: EvaluatorLimits
    allowed_actions: list[
        Literal["submit", "read", "clarify", "confirm", "reject", "reattach", "fixture_control"]
    ]
    allowed_approvals: list[Literal["technical_interview"]]

    @model_validator(mode="after")
    def registered(self) -> CaseSpecV2:
        if self.execution_item_id not in ITEMS or self.model_dump(
            mode="json", exclude_none=True
        ) != case_definition(self.execution_item_id):
            raise ValueError("case differs from versioned registered contract")
        return self


class SuiteItemV2(Contract):
    execution_item_id: str
    spec_path: str
    required: Literal[True] = True

    @model_validator(mode="after")
    def registered(self) -> SuiteItemV2:
        if (
            self.execution_item_id not in ITEMS
            or self.spec_path
            != f"cases/{self.execution_item_id}.v{case_definition(self.execution_item_id)['case_version']}.json"
        ):
            raise ValueError("unregistered item or spec path")
        return self


class SuiteV2(Contract):
    schema_version: Literal[2] = 2
    suite_id: Literal["harness-core-v1", "harness-core-preflight-v1"]
    execution_items: list[SuiteItemV2]

    @model_validator(mode="after")
    def complete(self) -> SuiteV2:
        ids = [i.execution_item_id for i in self.execution_items]
        expected = set(ITEMS) if self.suite_id == "harness-core-v1" else {"E01.runtime_read"}
        if len(ids) != len(set(ids)) or set(ids) != expected:
            raise ValueError("suite denominator differs from registered set")
        return self


class CoreVariant(Contract):
    variant_id: Literal["baseline", "candidate"]
    checkout: str
    interpreter: str
    fault: str = "none"

    @model_validator(mode="after")
    def registered(self) -> CoreVariant:
        if self.fault not in FAULTS:
            raise ValueError("unregistered diagnostic fault")
        return self


class ProviderRunProfile(Contract):
    profile_id: str
    endpoint: str
    model: str
    credential_ref: str
    temperature: float = Field(default=0, ge=0, le=0, allow_inf_nan=False)
    max_input_bytes: Positive = 32000
    max_output_tokens: Positive = 2048
    timeout_seconds: Positive = 45
    max_attempts_per_trial: Positive = 8
    fallback: list[str] = Field(default_factory=list, max_length=0)
    thinking: Literal["disabled"] = "disabled"
    price_currency: Literal["CNY"] = "CNY"
    input_cny_per_million: float = Field(gt=0, allow_inf_nan=False)
    output_cny_per_million: float = Field(gt=0, allow_inf_nan=False)
    price_reference: str
    price_checked_at: datetime

    @model_validator(mode="after")
    def safe(self) -> ProviderRunProfile:
        from urllib.parse import urlsplit

        u = urlsplit(self.endpoint)
        if (
            u.scheme != "https"
            or not u.hostname
            or u.username
            or u.password
            or u.query
            or u.fragment
        ):
            raise ValueError("provider endpoint must be a credential-free HTTPS URL")
        if self.price_checked_at.tzinfo is None:
            raise ValueError("price timestamp requires timezone")
        return self


class RunGrant(Contract):
    grant_id: UUID
    profile_hash: Hash
    suite_hash: Hash
    variants: list[Literal["candidate", "baseline"]]
    mode: Literal["real"] = "real"
    repeats: Positive
    expires_at: datetime
    max_requests: Positive
    max_cny: float = Field(gt=0, allow_inf_nan=False)
    authorization_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def scope(self) -> RunGrant:
        if (
            self.expires_at.tzinfo is None
            or not self.variants
            or len(self.variants) != len(set(self.variants))
        ):
            raise ValueError("grant requires unique variants and aware expiration")
        return self


class RunManifestV2(Contract):
    schema_version: Literal[2] = 2
    run_id: UUID
    parent_run_id: UUID | None = None
    suite_id: str
    suite_hash: Hash
    evaluator_hash: Hash
    environment: dict[str, Any]
    variants: dict[str, dict[str, Any]]
    mode: Mode
    repeats: Positive
    planned_trials: list[PlannedTrial]
    execution_order: list[tuple[str, str, str, str, int]]
    provider_profile: ProviderRunProfile | None = None
    provider_profile_hash: Hash | None = None
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    network_policy: Literal["trial-loopback", "approved-provider-only"]
    run_max_wall_seconds: Positive
    run_max_requests: Positive
    release_eligible: Literal[False] = False

    @model_validator(mode="after")
    def identities(self) -> RunManifestV2:
        keys = [p.identity() for p in self.planned_trials]
        if not keys or len(keys) != len(set(keys)) or keys != self.execution_order:
            raise ValueError("missing, duplicate or reordered plan identities")
        if set(self.variants) not in ({"candidate"}, {"baseline", "candidate"}):
            raise ValueError("run variants require candidate")
        expected_items = (
            set(ITEMS)
            if self.suite_id == "harness-core-v1"
            else {"E01.runtime_read"}
            if self.suite_id == "harness-core-preflight-v1"
            else set()
        )
        expected = {
            (i, v, n)
            for n in range(1, self.repeats + 1)
            for i in expected_items
            if self.mode == "deterministic" or ITEMS[i]["real"]
            for v in self.variants
        }
        actual = {(p.execution_item_id, p.variant_id, p.repeat_index) for p in self.planned_trials}
        if actual != expected or any(
            p.run_id != self.run_id
            or p.mode != self.mode
            or p.required_rules != required_rules(p.execution_item_id, self.mode)
            for p in self.planned_trials
        ):
            raise ValueError("frozen denominator or required rules disagree")
        if (self.mode == "real") != (
            self.provider_profile is not None and self.provider_profile_hash is not None
        ):
            raise ValueError("provider profile disagrees with mode")
        from offerpilot.evaluation.registry import digest

        if self.provider_profile and self.provider_profile_hash != digest(
            self.provider_profile.model_dump(mode="json")
        ):
            raise ValueError("provider profile hash mismatch")
        if self.network_policy != (
            "approved-provider-only" if self.provider_profile else "trial-loopback"
        ):
            raise ValueError("network policy differs from mode")
        return self


class TrialResultV2(TrialKey):
    schema_version: Literal[2] = 2
    case_version: Positive
    spec_hash: Hash
    fixture_hash: Hash
    coverage_kind: CoverageKind
    execution_status: Literal[
        "completed", "product_failed", "not_run", "unsupported", "eval_error", "interrupted"
    ]
    verdict: Literal["pass", "fail", "incomplete"]
    rules: list[RuleResult]
    evidence_hash: Hash
    private_directory: str
    termination_source: Literal[
        "product_budget",
        "evaluator_watchdog",
        "provider_timeout",
        "explicit_user_stop",
        "normal",
        "unknown",
    ]
    termination_events: list[dict[str, Any]] = Field(default_factory=list)
    consumption: dict[str, Any] = Field(default_factory=dict)
    manual_review: list[dict[str, Any]] = Field(default_factory=list)
    fault_witnesses: list[dict[str, Any]] = Field(default_factory=list)
