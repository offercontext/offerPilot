"""No paid calls. Full driver integration uses the local fake provider and actual Runtime."""

from decimal import Decimal
import os
import subprocess
import sys
import time
from uuid import uuid5, NAMESPACE_URL

import pytest

from offerpilot.evaluation import core_targeted as targeted
from offerpilot.evaluation.core_contracts import RunGrant
from offerpilot.evaluation.core_governance import store_lock
from offerpilot.evaluation.core_planning import read_plan
from offerpilot.evaluation.core_storage import load
from offerpilot.evaluation.core_targeted_ledger import OfflineLedger
from offerpilot.evaluation.registry import digest
from offerpilot.evaluation.reporting import atomic_json
from offerpilot.evaluation.runner import ROOT


@pytest.fixture
def inputs(tmp_path):
    variants = tmp_path / "variants.json"
    atomic_json(
        variants, [dict(variant_id="candidate", checkout=str(ROOT), interpreter=sys.executable)]
    )
    return ROOT / "evals/suites/harness-core-v1.json", variants, tmp_path / "offline"


@pytest.mark.parametrize(
    "bad",
    [
        [*targeted.SELECTED[:-1], "unregistered"],
        [*targeted.SELECTED[:-1], targeted.SELECTED[0]],
        [*targeted.SELECTED[:-1], "E09.memory_override"],
        list(targeted.SELECTED[:-1]),
    ],
)
def test_selection_rejected_before_output_or_send(inputs, bad):
    with pytest.raises(ValueError):
        targeted.prepare(*inputs, bad)
    assert not inputs[2].exists()


def test_27_cap_binds_probe_and_target_and_survives_reopen(inputs, monkeypatch):
    targeted.prepare(*inputs, list(targeted.SELECTED))
    directory = inputs[2]
    manifest = read_plan(directory / "parent/run-plan.json")
    grant = RunGrant.model_validate_json((directory / "simulation-grant.json").read_bytes())
    binding = targeted.read(directory / "targeted-plan.json")["plan_hash"]
    from offerpilot.evaluation import core_targeted_ledger

    variant = manifest.variants["candidate"]
    monkeypatch.setattr(
        core_targeted_ledger,
        "snapshot",
        lambda p: {k: variant[k] for k in ("commit", "diff_hash", "files_hash", "lock_hash")},
    )

    def event(i, stage):
        item = "E01.runtime_read" if stage == "probe" else stage
        key = dict(
            run_id=str(uuid5(NAMESPACE_URL, str(manifest.run_id) + ":offline:" + stage)),
            variant_id="candidate",
            execution_item_id=item,
            mode="deterministic",
            repeat_index=1,
        )
        return dict(
            purpose="provider_send", input_bytes=100, output_cap=10, trial_key=key, epoch=1, seq=i
        )

    with store_lock(directory / "session-lock"):
        ledger = OfflineLedger(manifest, grant, directory, binding, initialize=True)
        ledger.stage = "probe"
        ledger.reserve(event(1, ledger.stage))
        for i in range(2, 28):
            ledger.stage = targeted.SELECTED[(i - 2) // 8]
            ledger.reserve(event(i, ledger.stage))
        retained = ledger.reserved_cny
    with store_lock(directory / "session-lock"):
        recovered = OfflineLedger(manifest, grant, directory, binding)
        assert recovered.requests == 27 and recovered.reserved_cny == retained
        recovered.stage = targeted.SELECTED[4]
        with pytest.raises(TimeoutError):
            recovered.reserve(event(28, recovered.stage))
        assert recovered.requests == 27 and recovered.rows[-1]["allowed"] is False
        with pytest.raises(ValueError):
            recovered.reserve(event(28, recovered.stage))  # even a denied duplicate cannot send


def test_partial_ledger_refuses_resume_and_does_not_truncate(inputs):
    targeted.prepare(*inputs, list(targeted.SELECTED), lifetime_seconds=-1)
    directory = inputs[2]
    targeted.execute(directory / "targeted-plan.json")
    (directory / "run-budget.jsonl").write_bytes(b'{"partial":')
    with pytest.raises(ValueError, match="partial ledger"):
        targeted.execute(directory / "targeted-plan.json")
    assert (directory / "run-budget.jsonl").read_bytes() == b'{"partial":'


def test_expired_grant_no_child_or_send(inputs):
    targeted.prepare(*inputs, list(targeted.SELECTED), lifetime_seconds=-1)
    result = targeted.execute(inputs[2] / "targeted-plan.json")
    assert result["allowed_requests"] == 0
    assert all(r["result"]["execution_status"] == "not_run" for r in result["matrix"])
    assert not (inputs[2] / "simulation-private").exists()


def test_snapshot_mismatch_precedes_child_and_send(inputs, monkeypatch):
    targeted.prepare(*inputs, list(targeted.SELECTED))
    from offerpilot.evaluation import core_planning

    original = core_planning.snapshot
    monkeypatch.setattr(core_planning, "snapshot", lambda p: original(p) | {"files_hash": "0" * 64})
    with pytest.raises(ValueError, match="source snapshot changed"):
        targeted.execute(inputs[2] / "targeted-plan.json")
    assert not (inputs[2] / "simulation-private").exists()


def test_concurrent_session_lock_denies_second_owner(inputs):
    targeted.prepare(*inputs, list(targeted.SELECTED))
    code = "from pathlib import Path; import sys; from offerpilot.evaluation.core_governance import store_lock; ctx=store_lock(Path(sys.argv[1])); ctx.__enter__(); print('held',flush=True); sys.stdin.read(1); ctx.__exit__(None,None,None)"
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(inputs[2] / "session-lock")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "held"
        with pytest.raises(ValueError, match="busy"):
            targeted.execute(inputs[2] / "targeted-plan.json")
    finally:
        process.communicate("\n", timeout=15)
    assert not (inputs[2] / "simulation-private").exists()


def test_inflight_probe_claim_not_retried_on_resume(inputs):
    targeted.prepare(*inputs, list(targeted.SELECTED))
    directory = inputs[2]
    envelope = targeted.read(directory / "targeted-plan.json")
    manifest = read_plan(directory / "parent/run-plan.json")
    parent = manifest.planned_trials[0]
    child = parent.model_copy(
        update={
            "run_id": uuid5(NAMESPACE_URL, str(manifest.run_id) + ":offline:probe"),
            "mode": "deterministic",
        }
    )
    grant = RunGrant.model_validate_json((directory / "simulation-grant.json").read_bytes())
    ledger = OfflineLedger(manifest, grant, directory, envelope["plan_hash"], initialize=True)
    atomic_json(
        directory / "session-state.json",
        dict(
            schema_version=3,
            simulation_only=True,
            ledger=ledger.receipt(),
            plan_hash=envelope["plan_hash"],
            stages={
                "probe": dict(
                    status="started",
                    parent_trial_key=list(parent.identity()),
                    child_trial_key=list(child.identity()),
                )
            },
        ),
    )
    result = targeted.execute(directory / "targeted-plan.json")
    assert result["stage_states"]["probe"]["status"] == "interrupted_unknown"
    assert result["allowed_requests"] == 0
    assert not (directory / "simulation-private").exists()


def test_changed_selection_or_grant_rejected_before_send(inputs):
    targeted.prepare(*inputs, list(targeted.SELECTED))
    directory = inputs[2]
    envelope = targeted.read(directory / "targeted-plan.json")
    envelope["plan"]["selected"][-1] = "E09.memory_withdraw"
    envelope["plan_hash"] = digest(envelope["plan"])
    atomic_json(directory / "targeted-plan.json", envelope)
    with pytest.raises(ValueError, match="selection"):
        targeted.execute(directory / "targeted-plan.json")
    assert not (directory / "simulation-private").exists()


def test_cli_reports_expired_partial_run_as_incomplete(inputs):
    from typer.testing import CliRunner
    from offerpilot.evaluation.cli import app

    targeted.prepare(*inputs, list(targeted.SELECTED), lifetime_seconds=-1)
    result = CliRunner().invoke(
        app, ["targeted-offline-run", "--plan", str(inputs[2] / "targeted-plan.json")]
    )
    assert result.exit_code == 3 and '"simulation_status": "incomplete"' in result.stdout
    report = targeted.read(inputs[2] / "offline-report.json")
    assert len(report["matrix"]) == 13 and report["allowed_requests"] == 0


def test_non_loopback_and_real_credential_scope_refused(inputs):
    import socket

    with targeted.loopback_only():
        with pytest.raises(PermissionError):
            socket.create_connection(("203.0.113.1", 443))
    targeted.prepare(*inputs, list(targeted.SELECTED))
    directory = inputs[2]
    parent = targeted.read(directory / "parent/run-plan.json")
    parent["manifest"]["provider_profile"]["credential_ref"] = "D:/Users/yuqi.chen/Desktop/key.txt"
    # Even a caller recomputing JSON checksums cannot turn this into a paid driver.
    parent["manifest"]["provider_profile_hash"] = digest(parent["manifest"]["provider_profile"])
    parent["plan_hash"] = digest(parent["manifest"])
    atomic_json(directory / "parent/run-plan.json", parent)
    envelope = targeted.read(directory / "targeted-plan.json")
    from offerpilot.evaluation.registry import file_hash

    envelope["plan"]["parent_hash"] = file_hash(directory / "parent/run-plan.json")
    envelope["plan_hash"] = digest(envelope["plan"])
    atomic_json(directory / "targeted-plan.json", envelope)
    with pytest.raises(ValueError):
        targeted.execute(directory / "targeted-plan.json")
    assert not (directory / "simulation-private").exists()


@pytest.mark.parametrize("cap", [0.000001, 0.03])
def test_full_driver_money_shortage_retains_unknown_and_no_resend(inputs, cap):
    targeted.prepare(*inputs, list(targeted.SELECTED), simulated_cny=cap)
    directory = inputs[2]
    first = targeted.execute(directory / "targeted-plan.json")
    import json

    rows = [json.loads(line) for line in (directory / "run-budget.jsonl").read_text().splitlines()]
    assert rows and any(not r["allowed"] for r in rows)
    if cap == 0.000001:
        assert first["allowed_requests"] == 0
    else:
        assert first["stage_states"]["probe"]["probe_ok"] is True
        assert first["allowed_requests"] > 0 and Decimal(first["simulated_reserved_cny"]) > 0
    before = (directory / "run-budget.jsonl").read_bytes()
    second = targeted.execute(directory / "targeted-plan.json")
    assert second["stopped_after_reservation_denial"] is True
    assert (directory / "run-budget.jsonl").read_bytes() == before
    assert second["allowed_requests"] == first["allowed_requests"]
    assert Decimal(first["simulated_reserved_cny"]) <= Decimal(str(cap))
    assert all(
        not r["selected"] and r["result"]["execution_status"] == "not_run"
        for r in first["matrix"]
        if r["parent_trial_key"][2] not in targeted.SELECTED
    )
    assert first["real_provider_requests"] == 0 and first["real_budget_reservation_cny"] == "0"


@pytest.mark.parametrize("fault", ["http_error", "timeout"])
def test_full_driver_failed_physical_request_counted_without_retry(inputs, fault):
    targeted.prepare(
        *inputs, list(targeted.SELECTED), max_requests=2, faults={"E01.runtime_read": fault}
    )
    directory = inputs[2]
    report = targeted.execute(directory / "targeted-plan.json")
    assert report["stage_states"]["probe"]["probe_ok"] is True
    assert report["allowed_requests"] == 2  # one probe, one failed/timed-out target
    target = directory / "simulation-private/E01.runtime_read"
    row = targeted.read(target / "trial-result.json")
    evidence = load(target, row["evidence_hash"])
    received = [
        v
        for v in evidence["input_observations"]
        if v.get("category") == "offline_provider_received"
    ]
    assert len(received) == 1
    assert evidence["facts"]["simulation_only"] is True
    before = (directory / "run-budget.jsonl").read_bytes()
    assert targeted.execute(directory / "targeted-plan.json")["allowed_requests"] == 2
    assert (directory / "run-budget.jsonl").read_bytes() == before


def test_full_driver_hits_global27_and_never_sends28(inputs):
    targeted.prepare(
        *inputs, list(targeted.SELECTED), faults={s: "extra_reads" for s in targeted.SELECTED}
    )
    directory = inputs[2]
    report = targeted.execute(directory / "targeted-plan.json")
    assert report["stage_states"]["probe"]["probe_ok"] is True
    assert report["allowed_requests"] == 27
    assert report["exit_code"] != 0 and report["simulation_status"] != "pass"
    received = 0
    for result in directory.glob("simulation-private/*/trial-result.json"):
        row = targeted.read(result)
        evidence = load(result.parent, row["evidence_hash"])
        received += sum(
            v.get("category") == "offline_provider_received" for v in evidence["input_observations"]
        )
    assert (
        received == 27
    )  # one probe +26 target physical HTTP sends; the 28th cannot reach fake provider
    before = (directory / "run-budget.jsonl").read_bytes()
    assert targeted.execute(directory / "targeted-plan.json")["allowed_requests"] == 27
    assert (directory / "run-budget.jsonl").read_bytes() == before


def test_full_driver_killed_during_received_request_does_not_resend(inputs):
    targeted.prepare(
        *inputs, list(targeted.SELECTED), max_requests=2, faults={"E01.runtime_read": "timeout"}
    )
    directory = inputs[2]
    code = "from pathlib import Path; import sys; from offerpilot.evaluation.core_targeted import execute; execute(Path(sys.argv[1]))"
    with (
        (directory / "interrupted-parent.stdout").open("w") as stdout,
        (directory / "interrupted-parent.stderr").open("w") as stderr,
    ):
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", code, str(directory / "targeted-plan.json")],
            stdout=stdout,
            stderr=stderr,
            env=os.environ.copy(),
        )
        try:
            until = time.monotonic() + 120
            while time.monotonic() < until:
                control = directory / "simulation-private/E01.runtime_read/epoch-1.control.jsonl"
                if control.exists() and "offline_provider_received" in control.read_text(
                    encoding="utf-8"
                ):
                    break
                assert process.poll() is None, (directory / "interrupted-parent.stderr").read_text()
                time.sleep(0.05)
            else:
                pytest.fail("parent never reached the observed local target request")
            process.kill()
            process.wait(timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=15)
    before = (directory / "run-budget.jsonl").read_bytes()
    report = targeted.execute(directory / "targeted-plan.json")
    assert report["stage_states"]["probe"]["probe_ok"] is True
    assert report["allowed_requests"] == 2 and report["exit_code"] != 0
    assert report["stage_states"]["E01.runtime_read"]["status"] == "interrupted_unknown"
    assert (directory / "run-budget.jsonl").read_bytes() == before


@pytest.mark.parametrize("damage", ["missing", "complete_line_tail", "state_and_ledger_missing"])
def test_full_driver_missing_or_truncated_budget_refuses_resume(inputs, damage):
    targeted.prepare(
        *inputs, list(targeted.SELECTED), max_requests=2, faults={"E01.runtime_read": "http_error"}
    )
    directory = inputs[2]
    first = targeted.execute(directory / "targeted-plan.json")
    assert first["stage_states"]["probe"]["probe_ok"] is True and first["allowed_requests"] == 2
    raw = targeted.read(directory / "simulation-private/E01.runtime_read/trial-result.json")
    evidence = load(directory / "simulation-private/E01.runtime_read", raw["evidence_hash"])
    assert (
        sum(
            v.get("category") == "offline_provider_received" for v in evidence["input_observations"]
        )
        == 1
    )
    budget = directory / "run-budget.jsonl"
    original = budget.read_bytes()
    anchor = (directory / "ledger-anchor.json").read_bytes()
    if damage in {"missing", "state_and_ledger_missing"}:
        budget.unlink()  # this test-created temporary ledger only
        if damage == "state_and_ledger_missing":
            (directory / "session-state.json").unlink()
    else:
        budget.write_bytes(b"".join(original.splitlines(keepends=True)[:-1]))
    with pytest.raises(ValueError, match="missing|anchor|orphan"):
        targeted.execute(directory / "targeted-plan.json")
    assert (
        not budget.exists()
        if damage in {"missing", "state_and_ledger_missing"}
        else budget.read_bytes() != original
    )
    assert (directory / "ledger-anchor.json").read_bytes() == anchor


def test_full_driver_raw_result_written_before_parent_kill_is_preserved(inputs):
    targeted.prepare(*inputs, list(targeted.SELECTED), max_requests=3)
    directory = inputs[2]
    code = """
from pathlib import Path
import sys,time
from offerpilot.evaluation import core_targeted as t
original=t.run_trial
def run(*a,**kw):
    result=original(*a,**kw)
    if kw.get('offline_simulation')=='target':
        print('raw-result-written',flush=True)
        time.sleep(120)
    return result
t.run_trial=run
t.execute(Path(sys.argv[1]))
"""
    stdout_path = directory / "raw-window-parent.stdout"
    with (
        stdout_path.open("w") as stdout,
        (directory / "raw-window-parent.stderr").open("w") as stderr,
    ):
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", code, str(directory / "targeted-plan.json")],
            stdout=stdout,
            stderr=stderr,
            env=os.environ.copy(),
        )
        try:
            until = time.monotonic() + 150
            while time.monotonic() < until:
                if "raw-result-written" in stdout_path.read_text():
                    break
                assert process.poll() is None
                time.sleep(0.05)
            else:
                pytest.fail("no actual target raw result produced")
            process.kill()
            process.wait(timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=15)
    state = targeted.read(directory / "session-state.json")
    assert "result" not in state["stages"]["E01.runtime_read"]
    raw_path = directory / "simulation-private/E01.runtime_read/trial-result.json"
    raw = targeted.read(raw_path)
    before = (directory / "run-budget.jsonl").read_bytes()
    report = targeted.execute(directory / "targeted-plan.json")
    assert report["stage_states"]["probe"]["probe_ok"] is True and report["allowed_requests"] == 3
    assert report["stage_states"]["E01.runtime_read"]["result_recovered_from_sealed_disk"] is True
    row = next(r for r in report["matrix"] if r["parent_trial_key"][2] == "E01.runtime_read")
    assert row["result"] == raw and row["stage_status"] == "interrupted_unknown"
    assert row["control_records_closed"] is True
    assert row["request_observation_binding"]["coverage"] == "complete"
    assert report["exit_code"] != 0 and (directory / "run-budget.jsonl").read_bytes() == before


@pytest.mark.parametrize(
    "damage",
    [
        "none",
        "evidence_key",
        "rules",
        "optional_rule",
        "verdict",
        "rule_status",
        "coverage",
        "private_path",
        "marker",
        "handshake",
        "schema",
        "product_file",
        "database_file",
        "missing_product_file",
        "missing_database_file",
        "completed_missing",
        "probe_ok",
        "control_flag",
        "false_closed",
        "unknown_attempt_mismatch",
        "unknown_attempt_partial",
    ],
)
def test_recovery_regrades_sealed_result_and_rejects_misbindings(inputs, damage):
    """Self-consistent hashes/state cannot substitute for the frozen evidence contract."""
    from offerpilot.evaluation.core_storage import seal
    from offerpilot.evaluation.core_graders import grade
    from offerpilot.evaluation.registry import file_hash

    targeted.prepare(*inputs, list(targeted.SELECTED), lifetime_seconds=-1)
    directory = inputs[2]
    envelope = targeted.read(directory / "targeted-plan.json")
    manifest = read_plan(directory / "parent/run-plan.json")
    parent = next(p for p in manifest.planned_trials if p.execution_item_id == targeted.SELECTED[0])
    stage = "probe" if damage == "probe_ok" else targeted.SELECTED[0]
    child = parent.model_copy(
        update={
            "run_id": uuid5(NAMESPACE_URL, str(manifest.run_id) + ":offline:" + stage),
            "mode": "deterministic",
        }
    )
    key = child.model_dump(
        mode="json", include={"run_id", "variant_id", "execution_item_id", "mode", "repeat_index"}
    )
    variant = manifest.variants["candidate"]
    handshake = dict(
        checkout_sha=variant["commit"], interpreter=variant["interpreter"], **variant["runtime"]
    )
    private = directory / "simulation-private" / stage
    private.mkdir(parents=True)
    handshake.update(
        product_file=str(ROOT / "src/offerpilot/__init__.py"),
        database_file=str(private / "data.db"),
    )
    if damage in {"product_file", "database_file"}:
        handshake[damage] = str(directory / "elsewhere")
    if damage in {"missing_product_file", "missing_database_file"}:
        del handshake[damage.removeprefix("missing_")]
    atomic_json(
        private / ".eval-trial.json", key if damage != "marker" else key | {"repeat_index": 2}
    )
    grant = RunGrant.model_validate_json((directory / "simulation-grant.json").read_bytes())
    ledger = OfflineLedger(manifest, grant, directory, envelope["plan_hash"], initialize=True)
    attempts = []
    if damage.startswith("unknown_attempt"):
        attempt = dict(
            purpose="provider_send", trial_key=key, epoch=1, seq=1, input_bytes=100, output_cap=10
        )
        ledger.stage = stage
        with pytest.raises(TimeoutError):
            ledger.reserve(attempt)  # expired fake grant; no actual child/send
        attempts = (
            [attempt | {"seq": 2}]
            if damage == "unknown_attempt_mismatch"
            else [attempt, attempt | {"seq": 2}]
        )
    evidence = dict(
        provider_attempts=attempts,
        trial_key=key if damage != "evidence_key" else key | {"repeat_index": 2},
        facts=dict(
            simulation_only=True,
            control_closed=damage != "false_closed",
            handshakes=[
                handshake if damage != "handshake" else handshake | {"checkout_sha": "0" * 40}
            ],
        ),
    )
    evidence_hash = seal(private, evidence)
    raw = grade(
        child, load(private, evidence_hash), "eval_error", evidence_hash, str(private)
    ).model_dump(mode="json")
    if damage == "rules":
        raw["rules"] = raw["rules"][:-1]
    if damage == "optional_rule":
        raw["rules"][0]["required"] = False
    if damage == "verdict":
        raw["verdict"] = "pass"
    if damage == "rule_status":
        raw["rules"][0]["status"] = "pass" if raw["rules"][0]["status"] != "pass" else "fail"
    if damage == "coverage":
        raw["coverage_kind"] = (
            "observation_only" if child.coverage_kind != "observation_only" else "task"
        )
    if damage == "private_path":
        raw["private_directory"] = str(directory)
    path = private / "trial-result.json"
    atomic_json(path, raw)
    # Both the result and its state hash agree, including in the forged-pass negative.
    atomic_json(
        directory / "session-state.json",
        dict(
            schema_version=3,
            simulation_only=True,
            plan_hash=envelope["plan_hash"],
            ledger=ledger.receipt(),
            stages={
                stage: dict(
                    status="completed"
                    if damage in {"completed_missing", "probe_ok", "control_flag", "false_closed"}
                    else "started",
                    probe_ok=True,
                    control_records_closed=damage != "control_flag",
                    parent_trial_key=list(parent.identity()),
                    child_trial_key=list(child.identity()),
                    result=raw,
                    result_file=str(path),
                    result_file_hash=file_hash(path),
                )
            },
        ),
    )
    if damage == "schema":
        envelope["plan"]["schema_version"] = 4
        envelope["plan_hash"] = digest(envelope["plan"])
        atomic_json(directory / "targeted-plan.json", envelope)
    if damage == "completed_missing":
        path.unlink()  # only this test-created temporary result
    before = (
        file_hash(path) if path.exists() else None,
        (directory / "run-budget.jsonl").read_bytes(),
    )
    if damage in {"none", "unknown_attempt_partial"}:
        report = targeted.execute(directory / "targeted-plan.json")
        row = next(r for r in report["matrix"] if r["selected"])
        assert row["result"] == raw and row["stage_status"] == "interrupted_unknown"
        assert report["exit_code"] != 0
        if damage == "unknown_attempt_partial":
            assert row["request_observation_binding"] == dict(
                coverage="partial",
                observed_attempts=2,
                allowed_reservations=0,
                denied_reservations=1,
                unreserved_pre_ack_intents=1,
            )
    else:
        with pytest.raises(
            ValueError, match="binding|regrade|plan/selection|completed stage result missing"
        ):
            targeted.execute(directory / "targeted-plan.json")
    assert before == (
        file_hash(path) if path.exists() else None,
        (directory / "run-budget.jsonl").read_bytes(),
    )


@pytest.mark.parametrize(
    "damage", ["none", "trial_key", "epoch", "seq", "input_bytes", "output_cap", "duplicate"]
)
@pytest.mark.parametrize("complete", [True, False])
def test_complete_request_binding_is_one_to_one_not_only_count(damage, complete):
    stage = targeted.SELECTED[0]
    attempt = dict(trial_key={"run_id": "child-a"}, epoch=1, seq=34, input_bytes=100, output_cap=10)
    row = dict(attempt, stage=stage, allowed=True)
    altered = dict(attempt)
    if damage == "trial_key":
        altered[damage] = {"run_id": "child-b"}
    if damage in {"epoch", "seq", "input_bytes", "output_cap"}:
        altered[damage] += 1
    observed = [altered, altered] if damage == "duplicate" else [altered]
    if damage == "none":
        targeted.check_request_bindings(
            stage, {"provider_attempts": observed}, [row], complete=complete
        )
    else:
        with pytest.raises(ValueError, match="request bindings"):
            targeted.check_request_bindings(
                stage, {"provider_attempts": observed}, [row], complete=complete
            )


@pytest.mark.parametrize(
    "terminal",
    ["completed", "failed", "cancelled", "interrupted", "stopped", "result_unknown", None],
)
def test_probe_requires_actual_runtime_completed_not_only_driver_or_http(terminal):
    from types import SimpleNamespace

    driver_result = SimpleNamespace(execution_status="completed")
    evidence = dict(
        facts=dict(control_closed=True, terminal_state=terminal),
        input_observations=[dict(category="provider_response", status=200)],
    )
    assert targeted.probe_succeeded(driver_result, evidence) is (terminal == "completed")


def test_shared_stage_identity_keeps_simulation_and_diagnostic_outside_real_parent(inputs):
    from offerpilot.evaluation.core_targeted_ledger import stage_trial

    targeted.prepare(*inputs, list(targeted.SELECTED))
    manifest = read_plan(inputs[2] / "parent/run-plan.json")
    parent = next(p for p in manifest.planned_trials if p.execution_item_id == targeted.SELECTED[0])
    assert stage_trial(manifest, targeted.SELECTED[0], False).identity() == parent.identity()
    diagnostic = stage_trial(manifest, "probe", False)
    simulation = stage_trial(manifest, targeted.SELECTED[0], True)
    assert diagnostic.mode == "real" and diagnostic.identity() != parent.identity()
    assert simulation.mode == "deterministic" and simulation.identity() not in {
        parent.identity(),
        diagnostic.identity(),
    }


def test_shared_ledger_external_anchor_failure_is_before_ack_and_never_refunded(inputs):
    import json
    from offerpilot.evaluation.core_targeted_ledger import TargetedLedger, stage_trial

    targeted.prepare(*inputs, list(targeted.SELECTED))
    directory = inputs[2]
    manifest = read_plan(directory / "parent/run-plan.json")
    grant = RunGrant.model_validate_json((directory / "simulation-grant.json").read_bytes())
    binding = targeted.read(directory / "targeted-plan.json")["plan_hash"]
    events = []

    def before(receipt):
        assert receipt["requests"] == 0
        events.append("checked")

    def after(row, receipt):
        assert json.loads((directory / "ledger-anchor.json").read_bytes()) == receipt
        assert json.loads((directory / "run-budget.jsonl").read_text().splitlines()[-1]) == row
        assert receipt["requests"] == 1 and row["allowed"] is True
        events.append("local-durable-before-external")
        raise OSError("injected external anchor failure before ACK")

    ledger = TargetedLedger(
        manifest, grant, directory, binding, initialize=True, before_send=before, after_record=after
    )
    ledger.stage = targeted.SELECTED[0]
    child = stage_trial(manifest, ledger.stage, False)
    key = child.model_dump(
        mode="json", include={"run_id", "variant_id", "execution_item_id", "mode", "repeat_index"}
    )
    with pytest.raises(OSError, match="before ACK"):
        ledger.reserve(
            dict(
                purpose="provider_send",
                trial_key=key,
                epoch=1,
                seq=1,
                input_bytes=100,
                output_cap=10,
            )
        )
    recovered = TargetedLedger(manifest, grant, directory, binding)
    assert events == ["checked", "local-durable-before-external"]
    assert recovered.requests == 1 and recovered.reserved_cny == ledger.reserved_cny > 0


def test_shared_ledger_refuses_over27_before_initializing_any_budget(inputs):
    from offerpilot.evaluation.core_targeted_ledger import TargetedLedger

    targeted.prepare(*inputs, list(targeted.SELECTED))
    directory = inputs[2]
    manifest = read_plan(directory / "parent/run-plan.json")
    grant = RunGrant.model_validate_json((directory / "simulation-grant.json").read_bytes())
    with pytest.raises(ValueError, match="<=27"):
        TargetedLedger(
            manifest,
            grant.model_copy(update={"max_requests": 28}),
            directory,
            "0" * 64,
            initialize=True,
        )
    assert not (directory / "run-budget.jsonl").exists()


def test_shared_ledger_concurrent_callbacks_cannot_cross_target8(inputs):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from offerpilot.evaluation.core_targeted_ledger import TargetedLedger, stage_trial

    targeted.prepare(*inputs, list(targeted.SELECTED))
    directory = inputs[2]
    manifest = read_plan(directory / "parent/run-plan.json")
    grant = RunGrant.model_validate_json((directory / "simulation-grant.json").read_bytes())
    ledger = TargetedLedger(manifest, grant, directory, "a" * 64, initialize=True)
    ledger.stage = targeted.SELECTED[0]
    key = stage_trial(manifest, ledger.stage, False).model_dump(
        mode="json", include={"run_id", "variant_id", "execution_item_id", "mode", "repeat_index"}
    )

    def event(seq):
        return dict(
            purpose="provider_send", trial_key=key, epoch=1, seq=seq, input_bytes=100, output_cap=10
        )

    for seq in range(1, 8):
        ledger.reserve(event(seq))
    barrier = Barrier(2)

    def reserve(seq):
        barrier.wait(timeout=10)
        try:
            ledger.reserve(event(seq))
        except TimeoutError:
            return "denied"
        return "allowed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, [8, 9])) == ["allowed", "denied"]
    recovered = TargetedLedger(manifest, grant, directory, "a" * 64)
    assert recovered.requests == 8 and recovered.stage_counts[ledger.stage] == 8
