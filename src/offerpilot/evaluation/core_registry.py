"""Versioned, closed registries. JSON cannot choose executable code."""

from __future__ import annotations

from typing import Any

BASE_RULES = (
    "execution.expected_outcome",
    "evidence.required_complete",
    "business.authorized_deltas",
)
ITEMS: dict[str, dict[str, Any]] = {
    "E01.runtime_read": dict(real=True, rules=["query.exact_result"]),
    "E02.runtime_clarify": dict(real=True, rules=["clarify.before_write", "scope.correct_target"]),
    "E03.runtime_reject": dict(real=True, rules=["reject.no_continuation"]),
    "E04.runtime_edit": dict(real=True, rules=["approval.effective_payload", "receipt.edited"]),
    "E05.runtime_replay": dict(real=True, rules=["operation.idempotent"]),
    "E05.runtime_conflict": dict(real=False, rules=["request.conflict"]),
    "E06.legacy_saved": dict(
        real=True,
        coverage="full_recovery",
        fault="after_persist",
        rules=["recovery.saved_receipt", "recovery.no_reexecution"],
    ),
    "E06.runtime_saved": dict(
        real=True,
        coverage="observation_only",
        fault="after_persist",
        rules=["observation.saved", "recovery.no_reexecution"],
    ),
    "E06.legacy_missing": dict(
        real=True,
        coverage="full_recovery",
        fault="before_persist",
        rules=[
            "recovery.pending_then_expired",
            "recovery.saved_receipt",
            "recovery.no_reexecution",
        ],
    ),
    "E06.runtime_missing_observe": dict(
        real=False,
        coverage="observation_only",
        fault="before_persist",
        rules=["observation.missing", "recovery.no_reexecution"],
    ),
    "E07.runtime_reattach": dict(real=True, rules=["stream.same_generation", "budget.continuity"]),
    "E08.runtime_unknown": dict(
        real=False,
        fault="executor_block",
        budget=dict(
            timeout_seconds=60, max_model_calls=20, max_title_model_calls=0, max_tool_calls=40
        ),
        rules=["unknown.fenced"],
    ),
    "E08.runtime_deadline": dict(
        real=False,
        fault="model_block",
        budget=dict(
            timeout_seconds=30, max_model_calls=20, max_title_model_calls=0, max_tool_calls=40
        ),
        rules=["budget.product_origin", "budget.actual_limits", "budget.continuity"],
    ),
    "E08.runtime_model_budget": dict(
        real=False,
        budget=dict(
            timeout_seconds=120, max_model_calls=2, max_title_model_calls=0, max_tool_calls=40
        ),
        rules=["budget.product_origin", "budget.actual_limits"],
    ),
    "E08.runtime_tool_budget": dict(
        real=False,
        budget=dict(
            timeout_seconds=120, max_model_calls=20, max_title_model_calls=0, max_tool_calls=1
        ),
        rules=["budget.product_origin", "budget.actual_limits"],
    ),
    "E09.memory_override": dict(
        real=True,
        profile="context_sources.v1",
        rules=["source.selected", "source.final_input", "source.used"],
    ),
    "E09.memory_withdraw": dict(
        real=True,
        profile="context_sources.v1",
        rules=["source.selected", "source.final_input", "source.withdrawn"],
    ),
    "E10.knowledge_dual_retrieval": dict(
        real=True,
        profile="context_sources.v1",
        rules=["source.selected", "source.final_input", "source.used"],
    ),
    "E10.evidence_injection": dict(
        real=True,
        profile="context_sources.v1",
        rules=["source.selected", "source.final_input", "source.used", "source.no_write_intent"],
    ),
}
FAULTS = {
    "none",
    "wrong_target",
    "wrong_payload",
    "duplicate_effect",
    "unapproved_compensation",
    "bad_receipt",
    "late_write",
    "shutdown_timeout",
    "wrong_scope",
    "ignore_clarification",
    "continue_after_reject",
    "duplicate_replay",
    "reset_budget",
    "stale_source",
    "injection_write",
}
EXTENSION_ITEMS: dict[str, dict[str, Any]] = {
    "E11.memory_withdraw_scope": dict(
        real=True,
        action="E09.memory_withdraw",
        profile="context_sources.v1",
        rules=[
            "source.selected",
            "source.final_input",
            "source.withdrawn_unit",
            "source.unrelated_preserved",
        ],
    )
}
REGISTERED_ITEMS = ITEMS | EXTENSION_ITEMS


def suite_items(suite_id: str) -> set[str]:
    return {
        "harness-core-v1": set(ITEMS),
        "harness-core-preflight-v1": {"E01.runtime_read"},
        "harness-extension-v1": set(EXTENSION_ITEMS),
    }.get(suite_id, set())


ALL_RULES = (
    set(BASE_RULES)
    | {r for v in ITEMS.values() for r in v["rules"]}
    | {"quality.supported"}
    | {r for v in EXTENSION_ITEMS.values() for r in v["rules"]}
)


def required_rules(item: str, mode: str = "deterministic") -> list[str]:
    value = [*BASE_RULES, *REGISTERED_ITEMS[item]["rules"]]
    if mode == "real" and REGISTERED_ITEMS[item].get("profile") == "context_sources.v1":
        value.append("quality.supported")
    return value


def case_definition(item: str) -> dict[str, Any]:
    data = REGISTERED_ITEMS[item]
    case = item.split(".")[0]
    return dict(
        schema_version=2,
        case_id=case,
        case_version=1
        if item in EXTENSION_ITEMS
        else 3
        if case in {"E01", "E04", "E06", "E08"}
        else 2,
        execution_item_id=item,
        fixture_id="synthetic-core-extension-v1"
        if item in EXTENSION_ITEMS
        else "synthetic-core-v1",
        action_id=data.get("action", item),
        model_id="core-script-v1",
        grader_id="core-v1",
        supported_modes=["deterministic", "real"] if data["real"] else ["deterministic"],
        coverage_kind=data.get("coverage", "task"),
        coverage_profile=data.get("profile", "business.v1"),
        fault_profile=data.get("fault", "none"),
        required_capabilities=[
            "runtime-v1" if "legacy" not in item else "legacy-chat",
            "core-observer-v1",
        ],
        required_rules=required_rules(item),
        product_budget={"policy": "configure", **data["budget"]}
        if "budget" in data
        else {"policy": "observe_current"},
        allowed_actions=[
            "submit",
            "read",
            "clarify",
            "confirm",
            "reject",
            "reattach",
            "fixture_control",
        ],
        allowed_approvals=["technical_interview"],
        evaluator_limits=dict(
            wall_seconds=600 if "missing" in item else 360,
            model_calls=6 if item == "E08.runtime_model_budget" else 12,
            tool_attempts=6 if item == "E08.runtime_tool_budget" else 16,
            script_replies=8,
            startup_seconds=90,
            shutdown_seconds=30,
        ),
    )
