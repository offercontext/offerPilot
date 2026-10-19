"""Authorized targeted controller; no alternative scheduler or budget algorithm.

Production registry/claims derive from the OS authority home. Tests replace only
the outer registry root and backend, retaining this controller and file account.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from offerpilot.evaluation.contracts import PlannedTrial
from offerpilot.evaluation.core_contracts import RunGrant, RunManifestV2, TrialResultV2
from offerpilot.evaluation.core_planning import create_plan, read_plan, validate_grant
from offerpilot.evaluation.core_registry import ITEMS
from offerpilot.evaluation.core_runner import authority_home, run_trial
from offerpilot.evaluation.core_storage import load
from offerpilot.evaluation.core_targeted_account import (
    AuthorityRegistry, FileBudgetAccount, SESSION_CEILING, bound_file, paid_lock, require,
    safe_path, slug,
)
from offerpilot.evaluation.core_targeted_evidence import (
    checked_result, recovered_request_bindings, read, probe_succeeded,
)
from offerpilot.evaluation.core_targeted_kernel import StageBackend, run_session
from offerpilot.evaluation.core_targeted_ledger import SELECTED, TargetedLedger, stage_trial
from offerpilot.evaluation.core_targeted_trust import require_owned
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.reporting import atomic_json

PROBE = dict(kind="independent-runtime-connectivity", version=1,
             message="只读连接探测；不要调用工具。", counts_in_parent_denominator=False,
             max_requests=1, automatic_retries=0)
EXPORT_NAMES = ("run-manifest.json", "trial-results.json")


class RuntimeBackend:
    simulation_only = False
    require_owned_exit = True
    private_root = "private"
    report_filename = "targeted-report.json"

    def run(self, stage: str, manifest: RunManifestV2, child: PlannedTrial, private: Path,
            ledger: TargetedLedger) -> TrialResultV2:
        return run_trial(manifest, child, private, ledger, diagnostic_probe=stage == "probe")


@dataclass(frozen=True)
class Assembly:
    registry: AuthorityRegistry
    backend: StageBackend


def assembly() -> Assembly:
    # No environment/CLI account or registry-root override, nor implicit registration.
    return Assembly(AuthorityRegistry(authority_home() / ".codex/evaluation"), RuntimeBackend())


def ref(path: Path) -> dict[str, str]:
    return dict(path=str(path.resolve()), sha256=file_hash(path))


def export_files(directory: Path, payloads: dict[str, Any], *, write: bool,
                 expected_refs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Seal derived exports before close; conflicts/partial writes require audit."""
    require(set(payloads) == set(EXPORT_NAMES), "formal export denominator files missing")
    require(write or expected_refs is not None, "closed export references missing; audit required")
    refs = {}
    for name in EXPORT_NAMES:
        path = safe_path(directory / name)
        require(not path.with_suffix(path.suffix + ".tmp").exists(),
                "partial formal export; audit required")
        if write:
            if not path.exists():
                atomic_json(path, payloads[name])
        require(path.is_file() and json.loads(path.read_bytes()) == payloads[name],
                "formal export missing/conflicting; audit required")
        refs[name] = ref(path)
    require(expected_refs is None or refs == expected_refs, "closed export hash differs; audit required")
    return refs


def verify_prerequisites(manifest: RunManifestV2, price: dict[str, Any], gate: dict[str, Any],
                         simulation_only: bool) -> None:
    profile = manifest.provider_profile
    assert profile
    now = datetime.now(timezone.utc)
    require(price.get("schema_version") == 3 and
            price.get("profile_hash") == manifest.provider_profile_hash and
            price.get("automatic_retries") == 0 and price.get("currency") == "CNY" and
            price.get("simulation_only", False) is simulation_only and
            price.get("source_url") == profile.price_reference and
            price.get("checked_at") == profile.price_checked_at.isoformat() and
            datetime.fromisoformat(price["valid_until"]) > now,
            "price audit missing/expired or profile differs")
    bound_file(price["source_capture"])
    expected = {k: manifest.variants["candidate"][k]
                for k in ("commit", "diff_hash", "files_hash", "lock_hash")}
    require(gate.get("schema_version") == 3 and gate.get("source_snapshot") == expected and
            gate.get("exit_code") == 0 and gate.get("native_process_returned") is True and
            gate.get("execution_complete") is True and
            gate.get("simulation_only", False) is simulation_only,
            "matching complete local gate not proven")
    bound_file(gate["command_receipt"])
    command = read(bound_file(gate["command_receipt"]))
    argv = command.get("argv", [])
    checkout = Path(manifest.variants["candidate"]["checkout"]).resolve()
    require(isinstance(argv, list) and all(isinstance(a, str) for a in argv) and bool(argv) and
            bool(command.get("cwd")) and
            Path(command.get("cwd", "")).resolve() == checkout,
            "full gate command/cwd missing")
    executable = Path(argv[0]).name.lower()
    if executable in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        require(len(argv) == 6 and [a.lower() for a in argv[1:5]] ==
                ["-noprofile", "-executionpolicy", "bypass", "-file"],
                "complete base PowerShell gate command required")
        script = argv[5]
    else:
        require(executable in {"bash", "bash.exe"} and len(argv) == 2,
                "complete base release gate command required")
        script = argv[1]
    expected_script = checkout / "scripts" / ("release-gate.sh" if executable.startswith("bash") else "release-gate.ps1")
    require((checkout / script).resolve() == expected_script and
            command.get("source_unchanged") is True and command.get("source_after") == expected,
            "full gate entrypoint/snapshot differs")
    require(command.get("exit_code") == 0 and command.get("source_snapshot") == expected and
            command.get("native_process_returned") is True and
            command.get("execution_complete") is True,
            "gate command receipt differs")


def prepare(suite: Path, variants: Path, profile: Path, grant_path: Path, price_audit: Path,
            gate_receipt: Path, output: Path, selected: list[str], authority_id: str,
            authorization_id: str) -> dict[str, Any]:
    ports = assembly()
    require(len(selected) == 9 and len(set(selected)) == 9 and set(selected) == set(SELECTED),
            "selection must be exactly the approved nine")
    config = ports.registry.authority(slug(authority_id))
    slug(authorization_id)
    account = FileBudgetAccount(ports.registry, config)
    saved, _, _, _ = account.inspect()  # no lock/claim/mkdir/account write in plan/preflight
    require(Decimal(saved["allocated_cny"]) == 0, "shared allocation remains open")
    require(not output.exists(), "targeted output already exists")
    output = safe_path(output.resolve())
    manifest = create_plan(suite, variants, output / "parent", mode="real", repeat=1,
                           provider_path=profile)
    grant = validate_grant(manifest, grant_path)
    assert grant
    require(manifest.suite_id == "harness-core-v1" and set(manifest.variants) == {"candidate"} and
            len(manifest.planned_trials) == 13 and
            {p.execution_item_id for p in manifest.planned_trials} == {k for k,v in ITEMS.items() if v["real"]},
            "targeted parent must contain all 13 real items")
    require(0 < grant.max_requests <= 27 and
            Decimal(str(grant.max_cny)) <= min(SESSION_CEILING, Decimal(saved["unallocated_cny"]),
                                             Decimal(config["max_session_cny"])),
            "grant exceeds current shared authorization")
    profile_value = manifest.provider_profile
    assert profile_value
    require(profile_value.max_attempts_per_trial == 8, "trial request protection must remain eight")
    verify_prerequisites(manifest, read(price_audit), read(gate_receipt), ports.backend.simulation_only)
    atomic_json(output / "run-grant.json", grant.model_dump(mode="json"))
    atomic_json(output / "price-audit.json", read(price_audit))
    atomic_json(output / "gate-receipt.json", read(gate_receipt))
    plan = dict(schema_version=3, simulation_only=ports.backend.simulation_only,
                authority_id=authority_id, authorization_id=authorization_id,
                authority_hash=digest(config), account_version=file_hash(account.path),
                account_path=str(account.path),
                execution_directory=str(output), parent_denominator=13, selected=selected,
                parent_hash=file_hash(output / "parent/run-plan.json"),
                grant_hash=file_hash(output / "run-grant.json"),
                price_audit_hash=file_hash(output / "price-audit.json"),
                gate_receipt_hash=file_hash(output / "gate-receipt.json"), probe=PROBE,
                max_http_requests_including_probe=grant.max_requests, automatic_retries=0,
                max_cny=str(grant.max_cny), release_eligible=False)
    atomic_json(output / "targeted-authorized-plan.json", dict(plan=plan, plan_hash=digest(plan)))
    return plan


def context(path: Path, ports: Assembly) -> tuple[dict[str, Any], RunManifestV2, RunGrant, FileBudgetAccount]:
    directory = safe_path(path.parent.absolute())
    require_owned(directory)
    for child in directory.rglob("*"):
        safe_path(child)
    envelope = read(path)
    plan = envelope["plan"]
    require(digest(plan) == envelope["plan_hash"] and plan.get("schema_version") == 3 and
            plan.get("simulation_only") is ports.backend.simulation_only and
            plan.get("execution_directory") == str(directory) and
            plan.get("parent_denominator") == 13 and plan.get("automatic_retries") == 0 and
            plan.get("probe") == PROBE and len(plan["selected"]) == len(set(plan["selected"])) == 9 and
            set(plan["selected"]) == set(SELECTED), "invalid targeted authorized envelope")
    for name, key in [("parent/run-plan.json", "parent_hash"), ("run-grant.json", "grant_hash"),
                      ("price-audit.json", "price_audit_hash"), ("gate-receipt.json", "gate_receipt_hash")]:
        require(file_hash(directory / name) == plan[key], "frozen authorized input changed")
    manifest = read_plan(directory / "parent/run-plan.json")
    grant = validate_grant(manifest, directory / "run-grant.json")
    assert grant
    require(manifest.suite_id == "harness-core-v1" and manifest.repeats == 1 and
            set(manifest.variants) == {"candidate"} and len(manifest.planned_trials) == 13 and
            0 < grant.max_requests <= 27 and grant.max_requests == plan["max_http_requests_including_probe"] and
            str(grant.max_cny) == plan["max_cny"] and Decimal(str(grant.max_cny)) <= SESSION_CEILING and
            manifest.provider_profile is not None and manifest.provider_profile.max_attempts_per_trial == 8,
            "targeted scope changed")
    config = ports.registry.authority(plan["authority_id"])
    require(digest(config) == plan["authority_hash"], "account identity changed; migration needs authorization")
    approval = ports.registry.approval(plan["authorization_id"])
    require(approval.get("authority_id") == plan["authority_id"] and
            approval.get("plan_hash") == envelope["plan_hash"] and
            approval.get("grant_hash") == plan["grant_hash"] and
            approval.get("grant_id") == str(grant.grant_id) and
            approval.get("simulation_only") is ports.backend.simulation_only and
            datetime.fromisoformat(approval["expires_at"]) > datetime.now(timezone.utc),
            "authorization binding/expiration differs")
    verify_prerequisites(manifest, read(directory / "price-audit.json"),
                         read(directory / "gate-receipt.json"), ports.backend.simulation_only)
    account = FileBudgetAccount(ports.registry, config)
    require(plan.get("account_path") == str(account.path), "frozen canonical account path changed")
    saved, last, _, _ = account.inspect()
    if not ports.backend.simulation_only and any(
        (directory / (name + suffix)).exists() for name in EXPORT_NAMES for suffix in ("", ".tmp")
    ):
        require(last is not None and last["session_binding"] == envelope["plan_hash"] and
                last["kind"] == "close", "formal finalization interrupted; allocation retained for audit")
    if not last or last["session_binding"] != envelope["plan_hash"]:
        require(file_hash(account.path) == plan["account_version"] and
                Decimal(saved["allocated_cny"]) == 0 and
                Decimal(saved["unallocated_cny"]) >= Decimal(str(grant.max_cny)),
                "latest account version/balance differs")
    return envelope, manifest, grant, account


def preflight(path: Path) -> dict[str, Any]:
    ports = assembly()
    envelope, _, grant, account = context(path, ports)
    saved, last, _, _ = account.inspect()
    return dict(schema_version=3, plan_hash=envelope["plan_hash"], ready=True,
                simulation_only=ports.backend.simulation_only, parent_denominator=13,
                selected_count=9, max_requests_including_probe=grant.max_requests,
                automatic_retries=0, latest_unallocated_cny=saved["unallocated_cny"],
                account_state=last["kind"] if last else "genesis", network_requests=0,
                new_reservation_cny="0", release_eligible=False)


def observed_transports(report: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
    responses, headers = set(), set()
    exit_proven = True
    for stage, saved in report["stage_states"].items():
        if "result_file" not in saved:
            exit_proven = False
            continue
        raw = TrialResultV2.model_validate_json(Path(saved["result_file"]).read_bytes())
        evidence = load(Path(raw.private_directory), raw.evidence_hash)
        facts = evidence["facts"]
        exit_proven &= facts.get("owned_subjects_exited") is True and facts.get("control_readers_exited") is True
        for obs in evidence.get("input_observations", []):
            attempt = obs.get("attempt", "")
            if not attempt.startswith("http-"):
                continue
            identity = (stage, obs["epoch"], int(attempt[5:]))
            if obs.get("category") == "provider_response" or obs.get("http_response_received") is True:
                responses.add(identity)
            if any(v["event"].endswith("send_request_headers.complete") for v in obs.get("transport_events", [])):
                headers.add(identity)
    allowed = {(r["stage"], r["epoch"], r["seq"]) for r in rows if r["allowed"]}
    require(responses <= allowed and headers <= allowed, "unreserved transport observation")
    return dict(pre_ack_reserved_requests=len(allowed), provider_responses_observed=len(responses),
                transport_headers_sent_observed=len(headers),
                reserved_without_observed_response=len(allowed - responses),
                external_receive_count=None,
                scope="HTTP responses/trace only; reservation does not prove send/receipt"), exit_proven


def execute(path: Path) -> dict[str, Any]:
    ports = assembly()
    # Read-only validation happens before acquiring a mutable lock/claim/account allocation.
    _, _, _, initial = context(path, ports)
    with paid_lock(initial.lock_path):
        envelope, manifest, grant, account = context(path, ports)
        directory, binding = path.parent.absolute(), envelope["plan_hash"]
        claim = dict(schema_version=3, authorization_id=envelope["plan"]["authorization_id"],
                     run_id=str(manifest.run_id), plan_hash=binding,
                     grant_hash=envelope["plan"]["grant_hash"], account_path=str(account.path),
                     authority_hash=digest(account.config), session_directory=str(directory),
                     simulation_only=ports.backend.simulation_only)
        anchor_root = ports.registry.claims(envelope["plan"]["authorization_id"], str(grant.grant_id), claim)
        cap = Decimal(str(grant.max_cny))
        phase = account.allocate(binding, cap)
        anchor_path = anchor_root / "spend-anchor.json"

        def anchored(receipt: dict[str, Any]) -> None:
            ports.registry.check(anchor_path)
            anchor = read(anchor_path)
            require(anchor == dict(claim_hash=digest(claim), ledger=receipt),
                    "local ledger differs from trusted spend anchor; no reset/replay")

        def before_send(receipt: dict[str, Any]) -> None:
            # Recheck trusted approval, frozen code/profile/prices and account on every pre-ACK.
            context(path, ports)
            account.assert_open(binding, cap)
            anchored(receipt)

        def after_record(row: dict[str, Any], receipt: dict[str, Any]) -> None:
            account.checkpoint("before_spend_anchor")
            atomic_json(anchor_path, dict(claim_hash=digest(claim), ledger=receipt))
            account.checkpoint("after_spend_anchor")

        def make_ledger(initialize: bool) -> TargetedLedger:
            ports.registry.check(anchor_path)
            trusted = read(anchor_path)
            require(trusted.get("claim_hash") == digest(claim), "claim anchor binding changed")
            if initialize:
                require(trusted.get("ledger") is None, "spent trusted anchor; local records missing")
            ledger = TargetedLedger(manifest, grant, directory, binding, initialize=initialize,
                                    simulation_only=ports.backend.simulation_only,
                                    before_send=before_send, after_record=after_record)
            if initialize:
                atomic_json(anchor_path, dict(claim_hash=digest(claim), ledger=ledger.receipt()))
            else:
                anchored(ledger.receipt())
            return ledger

        if phase == "close":
            # A closed session is read-only forever. Validate raw, ledger and external anchor.
            ledger = make_ledger(False)
            report = read(directory / ports.backend.report_filename)
            require(report.get("plan_hash") == binding, "closed report binding differs")
            saved_account = account.inspect()[0]
            closed = next(r for r in saved_account["closed_runs"] if r.get("session_binding") == binding)
            require(closed.get("report") == ref(directory / ports.backend.report_filename),
                    "closed report hash changed")
            if not ports.backend.simulation_only:
                export_files(directory, {
                    "run-manifest.json": manifest.model_dump(mode="json"),
                    "trial-results.json": [row["result"] for row in report["matrix"]],
                }, write=False, expected_refs=report.get("formal_exports"))
            for stage, saved in report["stage_states"].items():
                child = stage_trial(manifest, stage, ports.backend.simulation_only)
                actual, evidence = checked_result(Path(saved["result_file"]), child,
                    manifest.variants[child.variant_id], simulation_only=ports.backend.simulation_only)
                require(actual.model_dump(mode="json") == saved["result"] and
                        file_hash(Path(saved["result_file"])) == saved["result_file_hash"],
                        "closed raw result changed")
                for attempt in evidence.get("provider_attempts", []):
                    if not attempt.get("blocked"):
                        ledger.validate_event(stage, attempt)
                binding_result = recovered_request_bindings(stage, evidence, ledger.rows,
                    stage_completed=saved["status"] == "completed")
                require(binding_result == saved["request_observation_binding"] and
                        saved["control_records_closed"] is (evidence["facts"].get("control_closed") is True) and
                        saved["owned_subjects_exited"] is evidence["facts"].get("owned_subjects_exited") and
                        (stage != "probe" or saved["probe_ok"] is probe_succeeded(actual,evidence)),
                        "closed control/probe/request binding differs")
            return report
        outcome = run_session(manifest, grant, directory, binding, envelope["plan"]["selected"],
                              ports.backend, make_ledger)
        report, ledger = outcome.report, outcome.ledger
        anchored(ledger.receipt())
        transport, exit_proven = observed_transports(report, ledger.rows)
        report.update(plan_hash=binding, transport_observation=transport,
                      account_allocation_cny=str(cap), allocation_state="open",
                      owned_process_and_control_exit_proven=exit_proven,
                      session_status=report["selected_status"] if exit_proven else "incomplete")
        if not exit_proven:
            report["exit_code"] = 3
        if exit_proven:
            if not ports.backend.simulation_only:
                report["formal_exports"] = export_files(directory, {
                    "run-manifest.json": manifest.model_dump(mode="json"),
                    "trial-results.json": [row["result"] for row in report["matrix"]],
                }, write=True)
            report["allocation_state"] = "closed"
            report["closure_requires_committed_account_receipt"] = True
            atomic_json(directory / ports.backend.report_filename, report)
            account.close(binding, cap, directory / "run-budget.jsonl", exit_proven=True,
                          report=ref(directory / ports.backend.report_filename))
        if not exit_proven:
            atomic_json(directory / ports.backend.report_filename, report)
        return report
