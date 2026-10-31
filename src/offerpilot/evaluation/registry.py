from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from offerpilot.evaluation.contracts import CaseSpec, Suite

RULES = {
    "E01.runtime_read": ["execution.expected_outcome", "query.exact_result", "query.no_write_proposal",
                         "business.zero_committed_dml", "scope.expected_application",
                         "evidence.required_complete"],
    "E04.runtime_edit": ["execution.expected_outcome", "approval.before_commit", "approval.effective_payload",
                         "event.exact_target", "business.no_unexpected_delta",
                         "operation.single_effect", "receipt.edited_no_model_continuation",
                         "evidence.required_complete"],
    "E06.legacy_saved": ["execution.expected_outcome", "approval.before_commit", "approval.effective_payload",
                         "event.exact_target", "business.no_unexpected_delta",
                         "operation.single_effect", "receipt.edited_no_model_continuation",
                         "recovery.saved_receipt", "recovery.zero_calls", "recovery.zero_committed_dml",
                         "evidence.required_complete"],
}

FAULT_FAILURE_RULES = {
    "wrong_target": "business.no_unexpected_delta", "wrong_payload": "approval.effective_payload",
    "duplicate_effect": "operation.single_effect", "unapproved_compensation": "approval.before_commit",
    "bad_receipt": "receipt.edited_no_model_continuation", "late_write": "business.zero_committed_dml",
}


def fault_detected(fault: str, result: dict[str, Any]) -> bool:
    return bool(result.get("faulted") and FAULT_FAILURE_RULES.get(fault) in result.get("known_failures", []))


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_suite(path: Path, root: Path) -> tuple[Suite, dict[str, CaseSpec]]:
    root = root.resolve(strict=True)
    suite = Suite.model_validate_json(path.read_bytes())
    specs: dict[str, CaseSpec] = {}
    for item in suite.execution_items:
        expected_path = f"cases/{item.execution_item_id}.v1.json"
        if item.spec_path != expected_path or item.case_version != 1:
            raise ValueError("unregistered spec")
        spec_path = (root / item.spec_path).resolve(strict=True)
        if not spec_path.is_relative_to(root):
            raise ValueError("spec escapes evals root")
        spec = CaseSpec.model_validate_json(spec_path.read_bytes())
        expected = (
            item.case_id, item.case_version, item.execution_item_id, item.coverage_kind,
            "legacy-chat" if item.case_id == "E06" else "runtime-v1",
            item.execution_item_id, "after_persist" if item.case_id == "E06" else "none",
        )
        actual = (spec.case_id, spec.case_version, spec.execution_item_id, spec.coverage_kind,
                  spec.adapter, spec.action_id, spec.fault_id)
        if actual != expected or spec.required_rules != RULES[item.execution_item_id]:
            raise ValueError("spec differs from registered P0 contract")
        if item.coverage_kind != ("full_recovery" if item.case_id == "E06" else "task"):
            raise ValueError("invalid registered coverage")
        specs[item.execution_item_id] = spec
    return suite, specs
