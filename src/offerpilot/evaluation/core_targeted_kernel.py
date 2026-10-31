"""One targeted stage scheduler for offline and authorized execution adapters."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from offerpilot.evaluation.contracts import PlannedTrial
from offerpilot.evaluation.core_contracts import RunGrant, RunManifestV2, TrialResultV2
from offerpilot.evaluation.core_governance import store_lock
from offerpilot.evaluation.core_runner import missing
from offerpilot.evaluation.core_storage import load
from offerpilot.evaluation.core_targeted_ledger import SELECTED, TargetedLedger, stage_trial
from offerpilot.evaluation.core_targeted_evidence import (
    read,
    checked_result,
    check_request_bindings,
    probe_succeeded,
    recovered_request_bindings,
)
from offerpilot.evaluation.registry import file_hash
from offerpilot.evaluation.reporting import atomic_json


class StageBackend(Protocol):
    simulation_only: bool
    private_root: str
    report_filename: str

    def run(
        self,
        stage: str,
        manifest: RunManifestV2,
        child: PlannedTrial,
        private: Path,
        ledger: TargetedLedger,
    ) -> TrialResultV2: ...


@dataclass(frozen=True)
class SessionOutcome:
    report: dict[str, Any]
    ledger: TargetedLedger


def run_session(
    manifest: RunManifestV2,
    grant: RunGrant,
    directory: Path,
    session_binding: str,
    selected: list[str],
    backend: StageBackend,
    make_ledger: Callable[[bool], TargetedLedger],
) -> SessionOutcome:
    if (
        len(selected) != 9
        or len(set(selected)) != 9
        or set(selected) != set(SELECTED)
        or not 0 < grant.max_requests <= 27
    ):
        raise ValueError("invalid targeted selection or request scope")
    for child_path in directory.rglob("*"):
        if child_path.is_symlink() or not child_path.resolve().is_relative_to(directory):
            raise ValueError("targeted session must not contain linked/escaped files")
    with store_lock(directory / "session-lock"):
        require_owned_exit = getattr(backend, "require_owned_exit", not backend.simulation_only)
        state_path = directory / "session-state.json"
        existing = state_path.exists()
        if existing:
            state = read(state_path)
            if (
                state.get("plan_hash") != session_binding
                or state.get("simulation_only") is not backend.simulation_only
                or state.get("schema_version") != 3
            ):
                raise ValueError("session binding changed")
        else:
            if any(
                (directory / name).exists()
                for name in (
                    "ledger-anchor.json",
                    "run-budget.jsonl",
                    backend.private_root,
                    backend.report_filename,
                )
            ):
                raise ValueError("orphan session artifacts without state; never reset reservations")
            state = dict(
                schema_version=3,
                simulation_only=backend.simulation_only,
                plan_hash=session_binding,
                stages={},
            )
            atomic_json(state_path, state)
        ledger = make_ledger(not existing)
        if existing:
            ledger.check_prefix(state.get("ledger", {}))
        else:
            state["ledger"] = ledger.receipt()
            atomic_json(state_path, state)
        if any(r["stage"] not in state["stages"] for r in ledger.rows):
            raise ValueError("ledger has a stage absent from recovery state; no reset allowed")
        stages = ["probe", *selected]
        if set(state["stages"]) - set(stages):
            raise ValueError("foreign recovery stage")
        recovered_raw = False
        for stage, saved in state["stages"].items():
            item = SELECTED[0] if stage == "probe" else stage
            parent = next(p for p in manifest.planned_trials if p.execution_item_id == item)
            child = stage_trial(manifest, stage, backend.simulation_only)
            if (
                saved.get("parent_trial_key") != list(parent.identity())
                or saved.get("child_trial_key") != list(child.identity())
                or saved.get("status") not in {"started", "completed", "interrupted_unknown"}
            ):
                raise ValueError("recovery identity/status invalid")
            result_path = directory / backend.private_root / stage / "trial-result.json"
            if saved["status"] == "completed" and not result_path.is_file():
                raise ValueError("completed stage result missing; no reset or resubmission")
            if "result" not in saved and result_path.is_file():
                raw = TrialResultV2.model_validate_json(result_path.read_bytes())
                saved.update(
                    result=raw.model_dump(mode="json"),
                    result_file=str(result_path),
                    result_file_hash=file_hash(result_path),
                    result_recovered_from_sealed_disk=True,
                )
                recovered_raw = True
            if "result" in saved:
                if str(result_path) != saved.get("result_file") or file_hash(
                    result_path
                ) != saved.get("result_file_hash"):
                    raise ValueError("saved simulation result changed")
                actual, evidence = checked_result(
                    result_path,
                    child,
                    manifest.variants[child.variant_id],
                    simulation_only=backend.simulation_only,
                )
                if actual.model_dump(mode="json") != saved.get("result"):
                    raise ValueError("saved simulation result/evidence binding differs")
                closed = evidence.get("facts", {}).get("control_closed") is True
                exited = evidence.get("facts", {}).get("owned_subjects_exited")
                completed = closed and (not require_owned_exit or exited is True)
                if (
                    (saved["status"] == "completed" and not completed)
                    or (
                        "control_records_closed" in saved
                        and saved["control_records_closed"] is not closed
                    )
                    or (
                        stage == "probe"
                        and saved["status"] == "completed"
                        and saved.get("probe_ok") is not probe_succeeded(actual, evidence)
                    )
                ):
                    raise ValueError(
                        "saved control/probe status differs from sealed evidence binding"
                    )
                for attempt in evidence.get("provider_attempts", []):
                    if not attempt.get("blocked"):
                        ledger.validate_event(stage, attempt)
                binding = recovered_request_bindings(
                    stage, evidence, ledger.rows, stage_completed=saved["status"] == "completed"
                )
                known = dict(control_records_closed=closed, request_observation_binding=binding,
                             owned_subjects_exited=exited)
                if stage == "probe":
                    known["probe_ok"] = probe_succeeded(actual, evidence)
                if any(saved.get(k) != v for k, v in known.items()):
                    saved.update(known)
                    recovered_raw = True
            if saved["status"] == "started":
                saved["status"] = "interrupted_unknown"
                recovered_raw = True
        if recovered_raw:
            atomic_json(state_path, state)  # keep raw result; do not promote the unknown stage
        for stage in stages:
            if stage in state["stages"]:
                # An interrupted claim is never resubmitted, even if ACK/send outcome is unknown.
                if require_owned_exit and state["stages"][stage].get("owned_subjects_exited") is not True:
                    break
                if stage == "probe" and (
                    state["stages"][stage]["status"] != "completed"
                    or not state["stages"][stage].get("probe_ok")
                ):
                    break
                continue
            if (
                grant.expires_at <= datetime.now(timezone.utc)
                or ledger.requests >= grant.max_requests
                or ledger.reserved_cny >= grant.max_cny
                or any(not row["allowed"] for row in ledger.rows)
            ):
                break
            item = SELECTED[0] if stage == "probe" else stage
            parent = next(p for p in manifest.planned_trials if p.execution_item_id == item)
            child = stage_trial(manifest, stage, backend.simulation_only)
            state["stages"][stage] = dict(
                status="started",
                parent_trial_key=list(parent.identity()),
                child_trial_key=list(child.identity()),
            )
            atomic_json(state_path, state)  # before starting any child or allowing a send
            ledger.stage = stage
            private = directory / backend.private_root / stage
            result = backend.run(stage, manifest, child, private, ledger)
            evidence = load(private, result.evidence_hash)
            closed = evidence.get("facts", {}).get("control_closed") is True
            exited = evidence.get("facts", {}).get("owned_subjects_exited")
            complete = closed and (not require_owned_exit or exited is True)
            binding = check_request_bindings(stage, evidence, ledger.rows, complete=closed)
            state["stages"][stage].update(
                status="completed" if complete else "interrupted_unknown",
                result=result.model_dump(mode="json"),
                result_file=str(private / "trial-result.json"),
                result_file_hash=file_hash(private / "trial-result.json"),
                probe_ok=probe_succeeded(result, evidence),
                control_records_closed=closed,
                owned_subjects_exited=exited,
                request_observation_binding=binding,
            )
            state["ledger"] = ledger.receipt()
            atomic_json(state_path, state)
            if not complete or (stage == "probe" and not state["stages"][stage]["probe_ok"]):
                break
        matrix: list[dict[str, Any]] = []
        for parent in manifest.planned_trials:
            stored = state["stages"].get(parent.execution_item_id)
            result_data = missing(parent, "unselected_or_not_executed").model_dump(mode="json")
            if stored and "result" in stored:
                saved = Path(stored["result_file"])
                if file_hash(saved) != stored["result_file_hash"]:
                    raise ValueError("saved simulation result changed")
                actual = TrialResultV2.model_validate_json(saved.read_bytes())
                if list(actual.identity()) != stored["child_trial_key"]:
                    raise ValueError("saved simulation identity differs")
                result_data = actual.model_dump(mode="json")
            elif stored:
                child = stage_trial(manifest, parent.execution_item_id, backend.simulation_only)
                result_data = missing(
                    child, "claimed_stage_interrupted_without_final_result"
                ).model_dump(mode="json")
                result_data["execution_status"] = "interrupted"
            matrix.append(
                dict(
                    parent_trial_key=list(parent.identity()),
                    selected=parent.execution_item_id in SELECTED,
                    stage_status=stored["status"] if stored else "not_run",
                    control_records_closed=stored.get("control_records_closed") if stored else None,
                    trial_execution_status=result_data["execution_status"],
                    request_observation_binding=stored.get("request_observation_binding")
                    if stored
                    else None,
                    simulation_only=backend.simulation_only,
                    result=result_data,
                )
            )
        report: dict[str, Any] = dict(
            schema_version=3,
            simulation_only=backend.simulation_only,
            parent_denominator=13,
            selected_count=9,
            matrix=matrix,
            stage_states=state["stages"],
            allowed_requests=ledger.requests,
            reserved_cny=str(ledger.reserved_cny),
            real_provider_requests=0 if backend.simulation_only else None,
            real_budget_reservation_cny="0"
            if backend.simulation_only
            else str(ledger.reserved_cny),
            automatic_retries=0,
            stopped_after_reservation_denial=any(not row["allowed"] for row in ledger.rows),
            release_eligible=False,
        )
        selected_results = [r["result"] for r in matrix if r["selected"]]
        report["selected_status"] = (
            "fail"
            if any(r["verdict"] == "fail" for r in selected_results)
            else "incomplete"
            if any(r["verdict"] != "pass" for r in selected_results)
            or any(r["stage_status"] != "completed" for r in matrix if r["selected"])
            else "pass"
        )
        report["exit_code"] = {"pass": 0, "fail": 1, "incomplete": 3}[report["selected_status"]]
        if backend.simulation_only:
            report["simulation_status"] = report["selected_status"]
            report["simulated_reserved_cny"] = str(ledger.reserved_cny)
        atomic_json(directory / backend.report_filename, report)
        return SessionOutcome(report, ledger)
