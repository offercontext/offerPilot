from __future__ import annotations

from collections import Counter
import copy
import json
from pathlib import Path
from statistics import median
import secrets
import time
from typing import Any, Literal
from uuid import uuid4

from offerpilot.evaluation.adapters import ProductFailure
from offerpilot.evaluation.contracts import PlannedTrial
from offerpilot.evaluation.core_actions import CoreHTTPDriver, drive
from offerpilot.evaluation.core_contracts import (
    CaseSpecV2,
    CoreVariant,
    RunManifestV2,
    TrialResultV2,
)
from offerpilot.evaluation.core_graders import grade
from offerpilot.evaluation.core_origin_reads import CaptureOrigin
from offerpilot.evaluation.core_planning import read_plan, validate_grant
from offerpilot.evaluation.core_registry import case_definition
from offerpilot.evaluation.core_storage import load, seal
from offerpilot.evaluation.core_subject import CoreSubject, RunLedger
from offerpilot.evaluation.registry import file_hash
from offerpilot.evaluation.reporting import atomic_json
from offerpilot.evaluation.runner import cleanup_resources, snapshot
from scripts.evaluation_subject import core_observer as observer


def _control_observations(subjects: list[CoreSubject]) -> list[dict[str, Any] | None]:
    result = []
    for subject in subjects:
        getter = getattr(subject, "control_observation", None)
        try:
            value = getter() if callable(getter) else None
            result.append(copy.deepcopy(value) if isinstance(value, dict) else None)
        except Exception:
            result.append(None)  # A missing diagnostic must not replace the primary failure.
    return result


def _http_observations(drivers: list[tuple[CoreSubject, CoreHTTPDriver]]) -> list[dict[str, Any]]:
    result = []
    for subject, driver in drivers:
        getter = getattr(driver, "request_error_observations", None)
        errors: list[dict[str, Any]] | None = None
        capture_errors: list[str] | None = None
        epoch, trial_key = None, None
        try:
            config = getattr(subject, "config", {})
            epoch = config.get("epoch")
            key = config.get("trial_key")
            if isinstance(key, dict):
                trial_key = {name: key.get(name) for name in (
                    "run_id", "variant_id", "execution_item_id", "mode", "repeat_index")}
            observed = getter() if callable(getter) else None
            if isinstance(observed, list) and all(isinstance(value, dict) for value in observed):
                errors = copy.deepcopy(observed)
            failed = getattr(driver, "request_error_capture_errors", None)
            capture_errors = list(failed) if isinstance(failed, list) else None
        except Exception as exc:
            capture_errors = [type(exc).__name__]
        result.append(dict(epoch=epoch, trial_key=trial_key,
            request_errors=errors, capture_errors=capture_errors))
    return result


def run_trial(
    manifest: RunManifestV2, plan: PlannedTrial, private: Path, ledger: RunLedger,
    *, offline_simulation: Literal["probe", "target"] | None = None,
    offline_fault: str | None = None,
    diagnostic_probe: bool = False,
) -> TrialResultV2:
    if offline_fault and (not offline_simulation or offline_fault not in {"http_error", "timeout", "extra_reads"}):
        raise ValueError("fault is offline-only")
    if offline_simulation is not None and (
        plan.mode != "deterministic"
        or manifest.provider_profile is None
        or manifest.provider_profile.credential_ref != "offline:no-credential"
        or manifest.provider_profile.endpoint != "https://offline.invalid/v1"
        or manifest.provider_profile.model != "eval-core"
    ):
        raise ValueError("offline simulation requires a deterministic child and dummy profile")
    spec = CaseSpecV2.model_validate(case_definition(plan.execution_item_id))
    variant = CoreVariant.model_validate(
        {
            k: v
            for k, v in manifest.variants[plan.variant_id].items()
            if k in {"variant_id", "checkout", "interpreter", "fault"}
        }
    )
    private.mkdir(parents=True, exist_ok=False)
    key = plan.model_dump(
        mode="json", include={"run_id", "variant_id", "execution_item_id", "mode", "repeat_index"}
    )
    atomic_json(private / ".eval-trial.json", key)
    events: list[dict[str, Any]] = []
    e: dict[str, Any] = {
        "trial_key": key,
        "initial": {},
        "observed": {},
        "milestones": {},
        "facts": {},
        "http": [],
        "calls": [],
        "faults": [],
        "provider_attempts": [],
        "input_observations": [],
        "budget_events": [],
        "manual_review": [],
        "files": {},
    }
    f = e["facts"]
    if offline_simulation:
        f["simulation_only"] = True
    f["diagnostic_fault"] = variant.fault
    started = time.monotonic()
    deadline = started + spec.evaluator_limits.wall_seconds
    token = secrets.token_urlsafe(32)
    subject = None
    driver = None
    status = "completed"
    subjects = []
    drivers: list[tuple[CoreSubject, CoreHTTPDriver]] = []
    script_replies = [0]
    config = {
        "command": "prepare",
        "checkout": variant.checkout,
        "data_dir": str(private.resolve()),
        "trial_key": key,
        "token": token,
        "limits": spec.evaluator_limits.model_dump(),
        "fault": variant.fault,
        "action_id": spec.action_id,
        "fault_profile": spec.fault_profile,
        "coverage_profile": spec.coverage_profile,
        "required_capabilities": spec.required_capabilities,
        "product_budget": spec.product_budget.model_dump(exclude_none=True),
        "provider_profile": manifest.provider_profile.model_dump(mode="json")
        if manifest.provider_profile
        else None,
    }
    if offline_simulation:
        config["offline_simulation"] = offline_simulation
        config["offline_fault"] = offline_fault

    def start(epoch: int) -> CoreSubject:
        value = CoreSubject(variant, config | {"epoch": epoch}, private, events, deadline, ledger)
        subjects.append(value)
        return value

    def handshake(value: CoreSubject) -> CoreHTTPDriver:
        ready = value.wait(
            "ready",
            max(0, spec.evaluator_limits.startup_seconds - (time.monotonic() - value.started)),
        )
        target = manifest.variants[plan.variant_id]
        if (
            ready["checkout_sha"] != target["commit"]
            or Path(ready["interpreter"]).resolve() != Path(variant.interpreter).resolve()
            or not Path(ready["product_file"])
            .resolve()
            .is_relative_to(Path(variant.checkout).resolve() / "src")
            or ready["python"] != target["runtime"]["python"]
            or ready["sqlite"] != target["runtime"]["sqlite"]
            or ready["dependencies"] != target["runtime"]["dependencies"]
            or Path(ready["database_file"]).resolve() != private.resolve() / "data.db"
        ):
            raise ValueError("actual target handshake differs from frozen plan")
        f.setdefault("handshakes", []).append(ready)
        f["budget_effective"] = ready["product_budget_effective"]
        f["budget_requested"] = spec.product_budget.model_dump(exclude_none=True)
        blocking_waits = ready.get("legacy_sync_waits")
        if "legacy-chat" in ready.get("capabilities", []) and (
            not isinstance(blocking_waits, dict)
            or set(blocking_waits) != {"/api/chat", "/api/chat/confirm"}
        ):
            raise ValueError("actual legacy response wait unavailable")
        value_driver = CoreHTTPDriver(
            ready["address"],
            token,
            deadline,
            e["http"],
            spec.evaluator_limits.script_replies,
            script_replies,
            blocking_waits=blocking_waits,
            cleanup_reserve=spec.evaluator_limits.shutdown_seconds,
            diagnostic_dir=private,
            request_context={"trial_key": key, "epoch": value.config.get("epoch")},
        )
        drivers.append((value, value_driver))
        return value_driver

    def restart() -> tuple[CoreSubject, CoreHTTPDriver]:
        epoch = observer.next_epoch(private / "data.db")
        value = start(epoch)
        return value, handshake(value)

    try:
        subject = start(1)
        prepared = subject.wait(
            "prepared",
            max(0, spec.evaluator_limits.startup_seconds - (time.monotonic() - subject.started)),
        )
        e["initial"] = prepared["initial"]
        f["aliases"] = prepared["aliases"]
        driver = handshake(subject)
        if diagnostic_probe or offline_simulation == "probe":
            submitted = driver.request("POST", "/api/pilot/runtime/v1/turns", {
                "request_id": str(uuid4()), "conversation_id": 0,
                "context_type": "application", "context_ref": str(f["aliases"]["target_application"]),
                "message": "只读连接探测；不要调用工具。",
            })
            from offerpilot.evaluation.core_actions import wait, TERMINAL

            terminal = wait(driver, submitted["turn_id"], TERMINAL, f)
            f.update(terminal_state=terminal["state"], diagnostic_only=True,
                     answer=terminal.get("terminal", {}).get("response", {}).get("message", ""))
        else:
            subject, driver = drive(spec.action_id, driver, subject, private, e, restart)
        observer.marker(private / "data.db", "driver_finished", {"terminal_received": True})
    except Exception as exc:
        f["driver_failure_observation"] = dict(
            captured_at_monotonic=time.monotonic(), error_type=type(exc).__name__,
            subjects=_control_observations(subjects), drivers=_http_observations(drivers),
        )
        f["driver_failure_observation"]["capture_finished_monotonic"] = time.monotonic()
        status = (
            "product_failed"
            if isinstance(exc, ProductFailure)
            else "interrupted"
            if isinstance(exc, TimeoutError)
            else "eval_error"
        )
        f["driver_error"] = type(exc).__name__
        if isinstance(exc, ProductFailure):
            f["product_failure"] = str(exc)
        f["termination_source"] = (
            "evaluator_watchdog" if isinstance(exc, TimeoutError) else "unknown"
        )
    finally:
        cleanup_errors = cleanup_resources(subjects,
            [(value.config.get("epoch"), resource) for value, resource in drivers])
        if cleanup_errors:
            f["cleanup_errors"] = cleanup_errors
        f["control_observations_after_stop"] = _control_observations(subjects)
        http_diagnostics = _http_observations(drivers)
        errors = [dict(error, epoch=value["epoch"], trial_key=value["trial_key"])
                  for value in http_diagnostics for error in value["request_errors"] or []]
        if errors:
            f["http_request_errors"] = errors
        capture_errors = [dict(epoch=value["epoch"], error_types=value["capture_errors"])
                          for value in http_diagnostics if value["capture_errors"]]
        if capture_errors:
            f["http_diagnostic_capture_errors"] = capture_errors
        f["control_closed"] = not cleanup_errors and bool(subjects) and all(
            s.closed and not s.errors for s in subjects)
        f["owned_subjects_exited"] = bool(subjects) and all(
            getattr(s, "owned_tree_exit_proven", False) for s in subjects
        )
        f["control_readers_exited"] = bool(subjects) and all(
            not s.reader.is_alive() for s in subjects
        )
        if not f["control_closed"] and status == "completed":
            status = "eval_error"
        if (private / "data.db").exists():
            try:
                observer.backup(private / "data.db", private / "sealed.db")
                e["observed"] = observer.read(private / "sealed.db")
            except Exception as exc:
                f["seal_error"] = type(exc).__name__
                status = "eval_error"
        e["calls"] = [v for v in events if v["type"] == "call_attempt"]
        e["faults"] += [
            v
            for v in events
            if v["type"] in {"fault_reached", "negative_control_reached"} and v not in e["faults"]
        ]
        e["input_observations"] = [
            v
            for v in events
            if v.get("category")
            in {"sources", "final_input", "usage", "provider_response", "provider_error", "provider_stream_terminal", "process", "process_worker", "offline_provider_received"}
        ]
        e["provider_attempts"] = [v for v in events if v.get("purpose") == "provider_send"]
        f["local_http_transport_observations"] = [copy.deepcopy(record)
            for _, resource in drivers for record in resource.local_http_observations]
        f["provider_stream_coverage"] = _stream_coverage(e["input_observations"])
        if not f["provider_stream_coverage"]["complete"]:
            # Observation failure is an evaluator failure, not zero activity or
            # a product failure. No grading rule or missing signal is rewritten.
            status = "eval_error"
        e["budget_events"] = [
            v for v in events if v.get("category") in {"budget", "budget_termination"}
        ]
        queries = [v["query_result"] for v in events if v.get("category") == "query"]
        if queries:
            f["query_rows"] = queries[0]
        executors = [v for v in events if v["type"] == "executor_observation"]
        if executors:
            f["effective_args"] = executors[-1]["effective_args"]
        budget_failures = [v for v in events if v.get("category") == "budget_termination"]
        if budget_failures:
            manager = [v for v in budget_failures if v.get("origin") == "RuntimeExecutionManager"]
            if manager:
                f["manager_terminal_state"] = manager[-1].get("manager_state")
            f["budget_reason"] = (
                "runtime deadline exhausted"
                if budget_failures[-1].get("resource") == "deadline"
                else "runtime budget exhausted"
            )
            f["termination_source"] = "product_budget"
        f.setdefault("termination_source", "normal" if status == "completed" else "unknown")
        if (
            snapshot(Path(variant.checkout))["files_hash"]
            != manifest.variants[plan.variant_id]["files_hash"]
        ):
            f["source_changed"] = True
            status = "eval_error"
        f["consumption"] = {
            "wall_seconds": time.monotonic() - started,
            "provider_attempts": len(e["provider_attempts"]) if f["control_closed"] else None,
            "observed_provider_attempts": len(e["provider_attempts"]),
            "cost": "not_applicable"
            if plan.mode == "deterministic"
            else "usage_or_conservative_reservation",
            "in_flight_unknown": not f["control_closed"],
            "actual_models": sorted(
                {
                    v.get("actual_model")
                    for v in e["input_observations"]
                    if v.get("category") == "usage" and v.get("actual_model")
                }
            ),
            "usage": [v for v in e["input_observations"] if v.get("category") == "usage"],
        }
        e["files"] = {
            p.name: {"sha256": file_hash(p), "bytes": p.stat().st_size}
            for p in private.iterdir()
            if p.is_file() and not p.name.startswith(("fragment-", "evidence-"))
        }
    evidence_hash = seal(private, e)
    result = grade(plan, load(private, evidence_hash), status, evidence_hash, str(private))
    atomic_json(private / "trial-result.json", result.model_dump(mode="json"))
    return result


def missing(plan: PlannedTrial, reason: str) -> TrialResultV2:
    return TrialResultV2.model_validate(
        {
            **plan.model_dump(exclude={"case_id", "required_rules"}),
            "execution_status": "not_run",
            "verdict": "incomplete",
            "evidence_hash": "0" * 64,
            "private_directory": "",
            "termination_source": "unknown",
            "rules": [
                {"rule_id": name, "status": "unknown", "reason_code": reason}
                for name in plan.required_rules
            ],
        }
    )


def aggregate(manifest: RunManifestV2, results: list[TrialResultV2]) -> dict[str, Any]:
    by_key = {r.identity(): r for r in results}
    if len(by_key) != len(results) or set(by_key) - {p.identity() for p in manifest.planned_trials}:
        raise ValueError("duplicate/foreign result")
    rows = [
        by_key.get(p.identity()) or missing(p, "planned_trial_missing")
        for p in manifest.planned_trials
    ]
    for p, r in zip(manifest.planned_trials, rows, strict=True):
        if (
            r.case_version != p.case_version
            or r.spec_hash != p.spec_hash
            or r.fixture_hash != p.fixture_hash
            or [v.rule_id for v in r.rules] != p.required_rules
            or any(not v.required for v in r.rules)
        ):
            raise ValueError("result contract mismatch")
        actual = (
            "fail"
            if any(v.status == "fail" for v in r.rules)
            else "incomplete"
            if r.execution_status != "completed" or any(v.status != "pass" for v in r.rules)
            else "pass"
        )
        if actual != r.verdict:
            raise ValueError("verdict differs from required rules")
    counts: dict[str, int] = dict(Counter(r.verdict for r in rows))
    groups = []
    for item, variant, mode in dict.fromkeys(
        (p.execution_item_id, p.variant_id, p.mode) for p in manifest.planned_trials
    ):
        group = [
            r for r in rows if (r.execution_item_id, r.variant_id, r.mode) == (item, variant, mode)
        ]
        groups.append(
            {
                "execution_item_id": item,
                "variant_id": variant,
                "mode": mode,
                "planned": len(group),
                "pass": sum(r.verdict == "pass" for r in group),
                "fail": sum(r.verdict == "fail" for r in group),
                "incomplete": sum(r.verdict == "incomplete" for r in group),
                "all_pass": all(r.verdict == "pass" for r in group),
                "fluctuating": len({r.verdict for r in group}) > 1,
            }
        )
    return {
        "schema_version": 2,
        "run_id": str(manifest.run_id),
        "planned": len(rows),
        "counts": {k: counts.get(k, 0) for k in ("pass", "fail", "incomplete")},
        "groups": groups,
        "trials": [r.model_dump(mode="json") for r in rows],
        "exit_code": 1
        if counts.get("fail")
        else 2
        if any(r.execution_status in {"unsupported", "eval_error"} for r in rows)
        else 3
        if counts.get("incomplete")
        else 0,
        "release_eligible": False,
        "repeats": manifest.repeats,
        "stability_claim": False,
        "decidable": {
            "numerator": sum(r.verdict != "incomplete" for r in rows),
            "denominator": len(rows),
        },
        "evidence_complete": {
            "numerator": sum(
                any(
                    v.rule_id == "evidence.required_complete" and v.status == "pass"
                    for v in r.rules
                )
                for r in rows
            ),
            "denominator": len(rows),
        },
        "resources": [{"trial_key": r.identity(), "consumption": r.consumption} for r in rows],
        "wall_seconds_median": median(
            [
                r.consumption["wall_seconds"]
                for r in rows
                if r.consumption.get("wall_seconds") is not None
            ]
        )
        if any(r.consumption.get("wall_seconds") is not None for r in rows)
        else None,
        "resource_samples": sum(r.consumption.get("wall_seconds") is not None for r in rows),
        "p95": None,
    }


def authority_home() -> Path:
    """OS account authority, independent of caller-controlled home variables."""
    import os
    import sys

    if sys.platform != "win32":
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    import ctypes
    from uuid import UUID

    folder = (ctypes.c_ubyte * 16).from_buffer_copy(
        UUID("5e6c858f-0e22-4760-9afe-ea3317b67173").bytes_le
    )
    value = ctypes.c_void_p()
    get_path = ctypes.windll.shell32.SHGetKnownFolderPath
    get_path.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_path.restype = ctypes.c_long
    release = ctypes.windll.ole32.CoTaskMemFree
    release.argtypes = [ctypes.c_void_p]
    release.restype = None
    try:
        if get_path(ctypes.byref(folder), 0, None, ctypes.byref(value)) != 0 or not value.value:
            raise OSError("OS account profile unavailable; grant cannot be claimed")
        return Path(ctypes.wstring_at(value.value)).resolve()
    finally:
        if value.value:
            release(value)


def claim_grant(grant: Any, grant_path: Path, run_id: str) -> None:
    import os

    claims = authority_home() / ".codex/evaluation/grant-claims"
    claims.mkdir(parents=True, exist_ok=True)
    with (claims / (str(grant.grant_id) + ".json")).open("x", encoding="utf-8") as claim:
        json.dump({"run_id": run_id, "grant_hash": file_hash(grant_path)}, claim)
        claim.flush()
        os.fsync(claim.fileno())


def run_plan(path: Path, grant_path: Path | None = None) -> dict[str, Any]:
    manifest = read_plan(path)
    grant = validate_grant(manifest, grant_path)
    output = path.parent
    if grant and grant_path:
        claim_grant(grant, grant_path, str(manifest.run_id))
    with (output / ".run-claim").open("x", encoding="utf-8") as f:
        f.write(str(manifest.run_id))
    atomic_json(output / "run-manifest.json", manifest.model_dump(mode="json"))
    ledger = RunLedger(manifest, grant, output)
    results = []
    failures = []
    stop_after_started_failure = False
    start = time.monotonic()
    for plan in manifest.planned_trials:
        if stop_after_started_failure:
            results.append(missing(plan, "previous_trial_closure_unknown"))
            continue
        if time.monotonic() - start >= manifest.run_max_wall_seconds or (
            grant
            and (ledger.requests >= grant.max_requests or ledger.reserved_cny >= grant.max_cny)
        ):
            results.append(missing(plan, "run_authorization_or_protection_exhausted"))
            continue
        phase, variant_id = "source_snapshot", None
        try:
            for variant_id, v in manifest.variants.items():
                current = snapshot(Path(v["checkout"]))
                if any(current[k] != v[k] for k in current):
                    raise ValueError("target snapshot changed")
            phase, variant_id = "trial_execution", plan.variant_id
            private = (
                output
                / "private"
                / f"{plan.variant_id}-{plan.mode}-{plan.execution_item_id}-{plan.repeat_index}"
            )
            result = run_trial(manifest, plan, private, ledger)
            results.append(result)
            if result.consumption.get("in_flight_unknown"):
                stop_after_started_failure = True
        except Exception as exc:
            try:
                failure = _execution_failure(plan, phase, variant_id, exc)
            except Exception as capture_error:
                failure = dict(trial_key=plan.model_dump(mode="json", include={"run_id",
                    "variant_id", "execution_item_id", "mode", "repeat_index"}),
                    phase=phase, error_type=type(exc).__name__,
                    capture_error_type=type(capture_error).__name__,
                    execution_status="not_run" if phase == "source_snapshot" else "eval_error",
                    retried=False)
            failures.append(failure)
            if phase == "source_snapshot":
                results.append(missing(plan, type(exc).__name__))
            else:
                results.append(_started_failure(plan, private, failure))
                stop_after_started_failure = True
            atomic_json(output / "execution-failures.json", failures)
        atomic_json(output / "trial-results.json", [r.model_dump(mode="json") for r in results])
    atomic_json(output / "trial-results.json", [r.model_dump(mode="json") for r in results])
    summary = aggregate(manifest, results)
    atomic_json(output / "summary.json", summary)
    (output / "report.md").write_text(
        f"# Harness {manifest.suite_id}\n\nplanned={summary['planned']}, {summary['counts']}\n\nrelease_eligible=false\n\nManifest: run-manifest.json; independent private fragments: private/.\n",
        encoding="utf-8",
    )
    return summary


def _execution_failure(plan: PlannedTrial, phase: str, variant_id: str | None,
                       exc: BaseException) -> dict[str, Any]:
    """A diagnostic sidecar does not turn pre-trial failures into executed trials."""
    from offerpilot.evaluation.runner import ROOT

    chain: list[dict[str, Any]] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        frames = []
        tb = current.__traceback__
        while tb is not None:
            file = Path(tb.tb_frame.f_code.co_filename).resolve()
            if file.is_relative_to(ROOT):
                frames.append(dict(file=file.relative_to(ROOT).as_posix(), line=tb.tb_lineno))
            tb = tb.tb_next
        chain.append(dict(type=type(current).__name__, module=type(current).__module__,
                          frames=frames[-8:]))
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    return dict(trial_key=plan.model_dump(mode="json", include={"run_id", "variant_id",
        "execution_item_id", "mode", "repeat_index"}), phase=phase,
        snapshot_variant_id=variant_id if phase == "source_snapshot" else None,
        exception_chain=chain, chain_truncated=current is not None,
        execution_status="not_run" if phase == "source_snapshot" else "eval_error", retried=False)


def _started_failure(plan: PlannedTrial, private: Path, failure: dict[str, Any]) -> TrialResultV2:
    from offerpilot.evaluation.registry import digest

    # Never overwrite an existing trial's fragments, logs, or partial output.
    archive = private.with_name(private.name + "-failure")
    archive.mkdir(exist_ok=False)
    key = plan.model_dump(mode="json", include={"run_id", "variant_id", "execution_item_id",
                                               "mode", "repeat_index"})
    atomic_json(archive / ".eval-trial.json", key)
    partial = [{"path": str(p.resolve()), "sha256": file_hash(p), "bytes": p.stat().st_size}
               for p in sorted(private.rglob("*")) if p.is_file() and not p.is_symlink()]
    evidence = dict(trial_key=key, facts=dict(attempt_started=True,
        attempted_private_directory=str(private), partial_evidence=partial,
        partial_evidence_hash=digest(partial), execution_failure=failure,
        control_closed=False, driver_completed=False,
        consumption=dict(in_flight_unknown=True, provider_attempts=None)))
    evidence_hash = seal(archive, evidence)
    result = missing(plan, "started_trial_exception")
    return result.model_copy(update=dict(execution_status="eval_error", evidence_hash=evidence_hash,
        private_directory=str(archive), consumption=evidence["facts"]["consumption"]))


def _stream_coverage(observations: list[dict[str, Any]]) -> dict[str, Any]:
    expected = [v for v in observations if v.get("category") == "provider_response"
                and v.get("wire_stream") is True]
    terminals = [v for v in observations if v.get("category") == "provider_stream_terminal"]
    gaps = []
    expected_keys = Counter((v.get("epoch"), v.get("attempt")) for v in expected)
    for terminal in terminals:
        if (terminal.get("epoch"), terminal.get("attempt")) not in expected_keys:
            gaps.append(dict(epoch=terminal.get("epoch"), attempt=terminal.get("attempt"),
                             reason="stream_terminal_without_response"))
    for response in expected:
        matches = [v for v in terminals if (v.get("epoch"), v.get("attempt")) ==
                   (response.get("epoch"), response.get("attempt"))]
        if (expected_keys[(response.get("epoch"), response.get("attempt"))] != 1
                or len(matches) != 1 or matches[0].get("identity") != response.get("identity")
                or matches[0].get("observation_complete") is not True):
            gaps.append(dict(epoch=response.get("epoch"), attempt=response.get("attempt"),
                             reason="stream_terminal_missing_duplicate_or_incomplete"))
    return dict(expected=len(expected), observed=len(terminals), complete=not gaps, gaps=gaps)


def read_run(
    directory: Path, *, _capture_origin: CaptureOrigin | None = None
) -> tuple[RunManifestV2, list[TrialResultV2]]:
    manifest = RunManifestV2.model_validate_json((directory / "run-manifest.json").read_bytes())
    from offerpilot.evaluation.runner import evaluator_hash

    if manifest.evaluator_hash != evaluator_hash():
        raise ValueError("original frozen evaluator required for schema 2 evidence regrading")
    saved = json.loads((directory / "trial-results.json").read_bytes())
    planned = {p.identity(): p for p in manifest.planned_trials}
    results = []
    seen = set()
    for row in saved:
        result = TrialResultV2.model_validate(row)
        if result.identity() not in planned or result.identity() in seen:
            raise ValueError("foreign/duplicate result")
        seen.add(result.identity())
        plan = planned[result.identity()]
        if (
            result.case_version != plan.case_version
            or result.spec_hash != plan.spec_hash
            or result.fixture_hash != plan.fixture_hash
            or result.coverage_kind != plan.coverage_kind
            or [r.rule_id for r in result.rules] != plan.required_rules
            or any(not r.required for r in result.rules)
        ):
            raise ValueError("saved result differs from frozen contract")
        if result.execution_status == "not_run":
            results.append(missing(plan, "not_run"))
            continue
        private = Path(result.private_directory)
        if (
            not private.resolve().is_relative_to((directory / "private").resolve())
            or private.is_symlink()
        ):
            raise ValueError("private evidence path escapes run")
        evidence = load(private, result.evidence_hash)
        if _capture_origin is not None:
            _capture_origin(directory, manifest, result, evidence)
        key = plan.model_dump(
            mode="json",
            include={"run_id", "variant_id", "execution_item_id", "mode", "repeat_index"},
        )
        try:
            marker_key = json.loads((private / ".eval-trial.json").read_bytes())
        except (ValueError, OSError):
            marker_key = None
        if marker_key != key or evidence.get("trial_key") not in (key, None):
            results.append(missing(plan, "evidence_trial_identity_differs"))
            continue
        target = manifest.variants[plan.variant_id]
        handshakes = evidence.get("facts", {}).get("handshakes", [])
        if not handshakes or any(
            h.get("checkout_sha") != target["commit"]
            or Path(h.get("interpreter", "")).resolve() != Path(target["interpreter"]).resolve()
            or {k: h.get(k) for k in ("python", "sqlite", "dependencies")} != target["runtime"]
            for h in handshakes
        ):
            evidence["errors"]["handshake"] = "target_identity_missing_or_changed"
        results.append(
            grade(
                plan,
                evidence,
                result.execution_status,
                result.evidence_hash,
                result.private_directory,
            )
        )
    aggregate(manifest, results)
    return manifest, results
