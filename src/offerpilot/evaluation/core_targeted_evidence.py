"""Read-only integrity and probe checks shared by targeted execution backends."""

from __future__ import annotations
from collections import Counter
from pathlib import Path
from typing import Any
from offerpilot.evaluation.contracts import PlannedTrial
from offerpilot.evaluation.core_contracts import TrialResultV2
from offerpilot.evaluation.core_graders import grade
from offerpilot.evaluation.core_storage import load
from offerpilot.evaluation.registry import digest


def read(path: Path) -> dict[str, Any]:
    import json

    value: dict[str, Any] = json.loads(path.read_bytes())
    return value


def checked_result(
    path: Path, child: PlannedTrial, variant: dict[str, Any], *, simulation_only: bool = True
) -> tuple[TrialResultV2, dict[str, Any]]:
    """Recheck this frozen evaluator's evidence without changing any raw result."""
    actual = TrialResultV2.model_validate_json(path.read_bytes())
    evidence = load(path.parent, actual.evidence_hash)
    key = child.model_dump(
        mode="json", include={"run_id", "variant_id", "execution_item_id", "mode", "repeat_index"}
    )
    handshakes = evidence.get("facts", {}).get("handshakes", [])
    if (
        actual.identity() != child.identity()
        or actual.spec_hash != child.spec_hash
        or actual.fixture_hash != child.fixture_hash
        or actual.case_version != child.case_version
        or actual.coverage_kind != child.coverage_kind
        or actual.private_directory != str(path.parent)
        or [r.rule_id for r in actual.rules] != child.required_rules
        or any(not r.required for r in actual.rules)
        or evidence.get("errors")
        or evidence.get("trial_key") != key
        or read(path.parent / ".eval-trial.json") != key
        or evidence.get("facts", {}).get("simulation_only", False) is not simulation_only
        or not handshakes
        or any(
            h.get("checkout_sha") != variant["commit"]
            or Path(h.get("interpreter", "")).resolve() != Path(variant["interpreter"]).resolve()
            or {k: h.get(k) for k in ("python", "sqlite", "dependencies")} != variant["runtime"]
            or not Path(h.get("product_file", ""))
            .resolve()
            .is_relative_to(Path(variant["checkout"]).resolve() / "src")
            or Path(h.get("database_file", "")).resolve() != path.parent / "data.db"
            for h in handshakes
        )
    ):
        raise ValueError("saved simulation result/evidence binding differs")
    regraded = grade(
        child, evidence, actual.execution_status, actual.evidence_hash, str(path.parent)
    )
    if regraded != actual:
        raise ValueError("saved simulation result differs from sealed evidence regrade")
    return actual, evidence


def check_request_bindings(
    stage: str, evidence: dict[str, Any], rows: list[dict[str, Any]], *, complete: bool = True
) -> dict[str, Any]:
    def identity(value: dict[str, Any]) -> str:
        return digest(
            [value.get(k) for k in ("trial_key", "epoch", "seq", "input_bytes", "output_cap")]
        )

    attempts = [v for v in evidence.get("provider_attempts", []) if not v.get("blocked")]
    events = [digest([v.get(k) for k in ("trial_key", "epoch", "seq")]) for v in attempts]
    if len(set(events)) != len(events):
        raise ValueError("duplicate evidence request bindings")
    observed = Counter(identity(v) for v in attempts)
    recorded = Counter(identity(v) for v in rows if v["stage"] == stage)
    allowed = Counter(identity(v) for v in rows if v["stage"] == stage and v["allowed"])
    if recorded - observed or (complete and observed != allowed):
        raise ValueError("evidence and ledger request bindings differ")
    return dict(
        coverage="complete" if complete else "partial",
        observed_attempts=len(attempts),
        allowed_reservations=sum(allowed.values()),
        denied_reservations=sum(recorded.values()) - sum(allowed.values()),
        unreserved_pre_ack_intents=sum((observed - recorded).values()),
    )


def probe_succeeded(result: TrialResultV2, evidence: dict[str, Any]) -> bool:
    return (
        evidence.get("facts", {}).get("control_closed") is True
        and evidence.get("facts", {}).get("terminal_state") == "completed"
        and result.execution_status == "completed"
        and any(
            v.get("category") == "provider_response" and v.get("status") == 200
            for v in evidence.get("input_observations", [])
        )
    )


def recovered_request_bindings(
    stage: str, evidence: dict[str, Any], rows: list[dict[str, Any]], *, stage_completed: bool
) -> dict[str, Any]:
    """Preserve unknown pre-ACK intents; EOF alone is not full proposal coverage."""
    binding = check_request_bindings(stage, evidence, rows, complete=stage_completed)
    if (
        evidence.get("facts", {}).get("control_closed") is True
        and not binding["denied_reservations"]
        and not binding["unreserved_pre_ack_intents"]
    ):
        binding["coverage"] = "complete"
    return binding
