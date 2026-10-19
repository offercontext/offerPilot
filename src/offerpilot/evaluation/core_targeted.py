"""Nine-item driver with an explicitly zero-cost, loopback-only execution surface.

The real 13-item parent manifest is retained. Simulation child identities use mode
deterministic and a separate run ID, so they cannot be imported as the parent results.
There is intentionally no paid execution mode or shared-account write path.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
import socket
from typing import Any, Iterator
from uuid import NAMESPACE_URL, uuid4, uuid5

from offerpilot.evaluation.contracts import PlannedTrial
from offerpilot.evaluation.core_contracts import RunGrant, TrialResultV2
from offerpilot.evaluation.core_graders import grade
from offerpilot.evaluation.core_governance import store_lock
from offerpilot.evaluation.core_planning import create_plan, read_plan
from offerpilot.evaluation.core_registry import ITEMS
from offerpilot.evaluation.core_runner import missing, run_trial
from offerpilot.evaluation.core_storage import load
from offerpilot.evaluation.core_targeted_ledger import OfflineLedger, SELECTED
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.reporting import atomic_json


@contextmanager
def loopback_only() -> Iterator[None]:
    originals = (
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.getaddrinfo,
        socket.socket.sendto,
    )

    def allowed(address: Any) -> None:
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            raise PermissionError("offline driver refuses non-loopback networking")

    def connect(sock: Any, address: Any) -> Any:
        allowed(address)
        return originals[0](sock, address)

    def connect_ex(sock: Any, address: Any) -> Any:
        allowed(address)
        return originals[1](sock, address)

    def resolve(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        allowed((host, port))
        return originals[2](host, port, *args, **kwargs)

    def sendto(sock: Any, data: Any, *args: Any) -> Any:
        allowed(args[-1])
        return originals[3](sock, data, *args)

    setattr(socket.socket, "connect", connect)
    setattr(socket.socket, "connect_ex", connect_ex)
    socket.getaddrinfo = resolve
    setattr(socket.socket, "sendto", sendto)
    try:
        yield
    finally:
        setattr(socket.socket, "connect", originals[0])
        setattr(socket.socket, "connect_ex", originals[1])
        socket.getaddrinfo = originals[2]
        setattr(socket.socket, "sendto", originals[3])


def read(path: Path) -> dict[str, Any]:
    import json

    value: dict[str, Any] = json.loads(path.read_bytes())
    return value


def checked_result(
    path: Path, child: PlannedTrial, variant: dict[str, Any]
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
        or evidence.get("facts", {}).get("simulation_only") is not True
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


def prepare(
    suite: Path,
    variants: Path,
    output: Path,
    selected: list[str],
    *,
    max_requests: int = 27,
    simulated_cny: float = 1,
    lifetime_seconds: int = 3600,
    faults: dict[str, str] | None = None,
) -> dict[str, Any]:
    if (
        len(selected) != 9
        or len(set(selected)) != 9
        or set(selected) != set(SELECTED)
        or type(max_requests) is not int
        or not 0 < max_requests <= 27
    ):
        raise ValueError("selection must be exactly the approved nine; requests <=27")
    faults = faults or {}
    if set(faults) - set(SELECTED) or set(faults.values()) - {
        "http_error",
        "timeout",
        "extra_reads",
    }:
        raise ValueError("invalid offline transport fault")
    if output.exists():
        raise ValueError("offline output already exists")
    output.mkdir(parents=True)
    profile = dict(
        profile_id="offline-simulation-not-priced",
        endpoint="https://offline.invalid/v1",
        model="eval-core",
        credential_ref="offline:no-credential",
        temperature=0,
        max_input_bytes=32000,
        max_output_tokens=1024,
        timeout_seconds=10,
        max_attempts_per_trial=8,
        fallback=[],
        thinking="disabled",
        price_currency="CNY",
        input_cny_per_million=1,
        output_cny_per_million=1,
        price_reference="simulation-only; artificial arithmetic, no price quote",
        price_checked_at="1970-01-01T00:00:00+00:00",
    )
    atomic_json(output / "dummy-profile.json", profile)
    manifest = create_plan(
        suite,
        variants,
        output / "parent",
        mode="real",
        repeat=1,
        provider_path=output / "dummy-profile.json",
    )
    if (
        manifest.suite_id != "harness-core-v1"
        or set(manifest.variants) != {"candidate"}
        or len(manifest.planned_trials) != 13
    ):
        raise ValueError("targeted driver requires the complete 13-item candidate parent")
    assert manifest.provider_profile_hash is not None
    grant = RunGrant(
        grant_id=uuid4(),
        profile_hash=manifest.provider_profile_hash,
        suite_hash=manifest.suite_hash,
        variants=["candidate"],
        repeats=1,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=lifetime_seconds),
        max_requests=max_requests,
        max_cny=simulated_cny,
        authorization_ref="OFFLINE SIMULATION ONLY; no provider authorization or real allocation",
    )
    atomic_json(output / "simulation-grant.json", grant.model_dump(mode="json"))
    plan = dict(
        schema_version=3,
        simulation_only=True,
        selected=selected,
        execution_directory=str(output.resolve()),
        parent_file="parent/run-plan.json",
        parent_hash=file_hash(output / "parent/run-plan.json"),
        grant_hash=file_hash(output / "simulation-grant.json"),
        real_budget_reservation=0,
        max_http_requests_including_probe=max_requests,
        automatic_retries=0,
        parent_denominator=13,
        faults=faults,
        release_eligible=False,
    )
    atomic_json(output / "targeted-plan.json", dict(plan=plan, plan_hash=digest(plan)))
    return plan


def execute(path: Path) -> dict[str, Any]:
    with loopback_only():
        return _execute(path)


def _execute(path: Path) -> dict[str, Any]:
    directory = path.parent.resolve()
    for child_path in directory.rglob("*"):
        if child_path.is_symlink() or not child_path.resolve().is_relative_to(directory):
            raise ValueError("offline session must not contain linked/escaped files")
    envelope = read(path)
    plan = envelope["plan"]
    if (
        digest(plan) != envelope["plan_hash"]
        or plan.get("schema_version") != 3
        or plan.get("simulation_only") is not True
        or plan.get("automatic_retries") != 0
        or plan.get("parent_denominator") != 13
        or plan.get("execution_directory") != str(directory)
        or plan.get("parent_file") != "parent/run-plan.json"
        or len(plan["selected"]) != 9
        or set(plan["selected"]) != set(SELECTED)
    ):
        raise ValueError("invalid offline plan/selection")
    if set(plan.get("faults", {})) - set(SELECTED) or set(plan.get("faults", {}).values()) - {
        "http_error",
        "timeout",
        "extra_reads",
    }:
        raise ValueError("invalid offline fault selection")
    parent_path = directory / "parent/run-plan.json"
    grant_path = directory / "simulation-grant.json"
    if file_hash(parent_path) != plan["parent_hash"] or file_hash(grant_path) != plan["grant_hash"]:
        raise ValueError("bound parent/grant changed")
    # Snapshot/environment mismatch refuses the entire execution before any send.
    manifest = read_plan(parent_path)
    grant = RunGrant.model_validate_json(grant_path.read_bytes())
    profile = manifest.provider_profile
    if (
        manifest.mode != "real"
        or manifest.repeats != 1
        or set(manifest.variants) != {"candidate"}
        or len(manifest.planned_trials) != 13
        or {p.execution_item_id for p in manifest.planned_trials}
        != {k for k, v in ITEMS.items() if v["real"]}
        or profile is None
        or profile.credential_ref != "offline:no-credential"
        or profile.endpoint != "https://offline.invalid/v1"
        or profile.model != "eval-core"
        or profile.max_attempts_per_trial != 8
        or grant.max_requests > 27
        or grant.max_requests != plan["max_http_requests_including_probe"]
        or grant.profile_hash != manifest.provider_profile_hash
        or grant.suite_hash != manifest.suite_hash
        or grant.variants != ["candidate"]
        or grant.repeats != 1
    ):
        raise ValueError("offline scope differs from approved envelope")
    with store_lock(directory / "session-lock"):
        state_path = directory / "session-state.json"
        existing = state_path.exists()
        if existing:
            state = read(state_path)
            if (
                state.get("plan_hash") != envelope["plan_hash"]
                or state.get("simulation_only") is not True
                or state.get("schema_version") != 3
            ):
                raise ValueError("session binding changed")
        else:
            if any(
                (directory / name).exists()
                for name in (
                    "ledger-anchor.json",
                    "run-budget.jsonl",
                    "simulation-private",
                    "offline-report.json",
                )
            ):
                raise ValueError("orphan session artifacts without state; never reset reservations")
            state = dict(
                schema_version=3, simulation_only=True, plan_hash=envelope["plan_hash"], stages={}
            )
            atomic_json(state_path, state)
        ledger = OfflineLedger(
            manifest, grant, directory, envelope["plan_hash"], initialize=not existing
        )
        if existing:
            ledger.check_prefix(state.get("ledger", {}))
        else:
            state["ledger"] = ledger.receipt()
            atomic_json(state_path, state)
        if any(r["stage"] not in state["stages"] for r in ledger.rows):
            raise ValueError("ledger has a stage absent from recovery state; no reset allowed")
        stages = ["probe", *plan["selected"]]
        if set(state["stages"]) - set(stages):
            raise ValueError("foreign recovery stage")
        recovered_raw = False
        for stage, saved in state["stages"].items():
            item = SELECTED[0] if stage == "probe" else stage
            parent = next(p for p in manifest.planned_trials if p.execution_item_id == item)
            child = parent.model_copy(
                update={
                    "run_id": uuid5(NAMESPACE_URL, str(manifest.run_id) + ":offline:" + stage),
                    "mode": "deterministic",
                }
            )
            if (
                saved.get("parent_trial_key") != list(parent.identity())
                or saved.get("child_trial_key") != list(child.identity())
                or saved.get("status") not in {"started", "completed", "interrupted_unknown"}
            ):
                raise ValueError("recovery identity/status invalid")
            result_path = directory / "simulation-private" / stage / "trial-result.json"
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
                    result_path, child, manifest.variants[child.variant_id]
                )
                if actual.model_dump(mode="json") != saved.get("result"):
                    raise ValueError("saved simulation result/evidence binding differs")
                closed = evidence.get("facts", {}).get("control_closed") is True
                if (
                    (saved["status"] == "completed" and not closed)
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
                binding = check_request_bindings(
                    stage, evidence, ledger.rows, complete=saved["status"] == "completed"
                )
                if saved.get("request_observation_binding") != binding:
                    saved["request_observation_binding"] = binding
                    recovered_raw = True
            if saved["status"] == "started":
                saved["status"] = "interrupted_unknown"
                recovered_raw = True
        if recovered_raw:
            atomic_json(state_path, state)  # keep raw result; do not promote the unknown stage
        for stage in stages:
            if stage in state["stages"]:
                # An interrupted claim is never resubmitted, even if ACK/send outcome is unknown.
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
            child = parent.model_copy(
                update={
                    "run_id": uuid5(NAMESPACE_URL, str(manifest.run_id) + ":offline:" + stage),
                    "mode": "deterministic",
                }
            )
            state["stages"][stage] = dict(
                status="started",
                parent_trial_key=list(parent.identity()),
                child_trial_key=list(child.identity()),
            )
            atomic_json(state_path, state)  # before starting any child or allowing a send
            ledger.stage = stage
            private = directory / "simulation-private" / stage
            result = run_trial(
                manifest,
                child,
                private,
                ledger,
                offline_simulation="probe" if stage == "probe" else "target",
                offline_fault=plan.get("faults", {}).get(stage),
            )
            evidence = load(private, result.evidence_hash)
            complete = evidence.get("facts", {}).get("control_closed") is True
            binding = check_request_bindings(stage, evidence, ledger.rows, complete=complete)
            state["stages"][stage].update(
                status="completed" if complete else "interrupted_unknown",
                result=result.model_dump(mode="json"),
                result_file=str(private / "trial-result.json"),
                result_file_hash=file_hash(private / "trial-result.json"),
                probe_ok=probe_succeeded(result, evidence),
                control_records_closed=complete,
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
                child = parent.model_copy(
                    update={
                        "run_id": uuid5(
                            NAMESPACE_URL,
                            str(manifest.run_id) + ":offline:" + parent.execution_item_id,
                        ),
                        "mode": "deterministic",
                    }
                )
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
                    simulation_only=True,
                    result=result_data,
                )
            )
        report: dict[str, Any] = dict(
            schema_version=3,
            simulation_only=True,
            parent_denominator=13,
            selected_count=9,
            matrix=matrix,
            stage_states=state["stages"],
            allowed_requests=ledger.requests,
            simulated_reserved_cny=str(ledger.reserved_cny),
            real_provider_requests=0,
            real_budget_reservation_cny="0",
            automatic_retries=0,
            stopped_after_reservation_denial=any(not row["allowed"] for row in ledger.rows),
            release_eligible=False,
        )
        selected_results = [r["result"] for r in matrix if r["selected"]]
        report["simulation_status"] = (
            "fail"
            if any(r["verdict"] == "fail" for r in selected_results)
            else "incomplete"
            if any(r["verdict"] != "pass" for r in selected_results)
            or any(r["stage_status"] != "completed" for r in matrix if r["selected"])
            else "pass"
        )
        report["exit_code"] = {"pass": 0, "fail": 1, "incomplete": 3}[report["simulation_status"]]
        atomic_json(directory / "offline-report.json", report)
        return report
