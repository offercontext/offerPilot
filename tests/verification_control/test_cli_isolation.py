"""Actual official CLI bootstrap using synthetic budgets and SDK MockTransport.

These are controlled qualification failures, never real-provider acceptance.
"""

import json
from pathlib import Path
import sys

import pytest

from scripts.real_ai_control.lifecycle import supervise
from scripts.real_ai_control.offline import run_probe


def events(output):
    path = output / "archive/events.jsonl"
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def test_official_cli_two_servers_unconfigured_503_and_first_model_failure(tmp_path):
    out = tmp_path / "probe"
    result = run_probe(out)
    assert result["offline_qualification_passed"] is True
    assert result["owned_tree_exited"] is True and result["forced_kill"] is False
    assert result["raw_cli_exit"] == 1 and result["control_complete"] is True
    assert result["allocation_closed"] is True
    rows = events(out)
    assert len([r for r in rows if r["kind"] == "server-ready"]) == 2
    assert len([r for r in rows if r["kind"] == "temporary-cleanup"]) == 2
    assert len([r for r in rows if r["kind"] == "provider-dispatch"]) == 1
    assert all(r.get("real_seven_stage_pass") is not True for r in rows)
    assert result["child_report"]["real_seven_stage_pass"] is False


def test_native_product_results_and_distinct_parser_repairs_are_archived_before_cleanup(tmp_path):
    out = tmp_path / "probe"
    result = run_probe(out, fault="invalid_model_result")
    assert result["native_exit"] == 1 and result["allocation_closed"] is True
    rows = events(out)
    assistants = [r for r in rows if r["kind"] == "product-assistant-result"]
    assert len(assistants) >= 1
    for row in assistants:
        assert row["value"]["content"] == '{"offline":"intentionally invalid feature schema"}'
        assert row["value"]["tool_calls"][0]["id"] == "offline-tool-call"
        assert row["value"]["provider_blocks"]["request_id"] == "offline-native-model-result"
        assert row["api_request_id"] is not None
    physical = [r for r in rows if r["kind"] == "provider-dispatch"]
    assert len({r["links"]["logical_call_id"] for r in physical}) == len(physical)
    assert len({r["links"]["product_call_id"] for r in physical}) == len(physical)
    outputs = {r["product_call_id"]: r["api_request_id"] for r in assistants}
    assert all(
        r["links"]["api_request_id"] == outputs[r["links"]["product_call_id"]] for r in physical
    )
    from collections import Counter

    per_api = Counter(r["links"]["api_request_id"] for r in physical)
    assert len(per_api) >= 1 and all(count >= 2 for count in per_api.values())
    cleanup = min(
        r["ordinal"]
        for r in rows
        if r["kind"] == "temporary-cleanup" and r["ordinal"] > assistants[0]["ordinal"]
    )
    assert all(r["ordinal"] < cleanup for r in assistants)
    assert result["journal"]["allowed_requests"] == len(physical)


@pytest.mark.parametrize("fault", ["storage_escape", "extra_bind"])
def test_extra_database_and_listener_are_refused_without_loosening_temp_or_network(tmp_path, fault):
    out = tmp_path / "probe"
    result = run_probe(out, fault=fault)
    assert result["native_exit"] == 1 and result["owned_tree_exited"] is True
    assert result["journal"]["allowed_requests"] == 0
    assert result["child_report"]["error_type"] == "PermissionError"
    assert not (Path(result["private_work"]) / "outside-registered-roots.db").exists()
    assert all(row["status"] == "not_run" for row in result["child_report"]["stages"])


def test_actual_startup_failure_preserves_fifteen_second_contract_and_no_model_send(tmp_path):
    out = tmp_path / "probe"
    result = run_probe(out, fault="startup_failure")
    assert result["elapsed_seconds"] >= 15
    assert result["forced_kill"] is False and result["native_exit"] == 1
    assert result["child_report"]["server_count"] == 1
    assert result["child_report"]["unconfigured_503_observed"] is False
    assert result["journal"]["allowed_requests"] == 0
    assert all(row["status"] == "not_run" for row in result["child_report"]["stages"])


def test_server_stopped_but_knowledge_hangs_does_not_delete_or_release_allocation(tmp_path):
    out = tmp_path / "probe"
    result = run_probe(out, fault="worker_hang", watchdog_seconds=90)
    assert result["error_type"] == "TimeoutError"
    assert result["forced_kill"] is True and result["owned_tree_exited"] is True
    assert result["allocation_closed"] is False
    assert result["fake_account_after"]["allocated_cny"] == "12.39"
    rows = events(out)
    assert any(r["kind"] == "injected-knowledge-stop" and r["timeout"] is None for r in rows)
    assert not any(r["kind"] == "temporary-cleanup" for r in rows)
    assert Path(result["private_work"]).exists()


def test_capture_disk_failure_after_allowance_keeps_whole_allocation_and_raw_failure(tmp_path):
    out = tmp_path / "probe"
    result = run_probe(out, fault="archive_disk")
    assert result["owned_tree_exited"] is True and result["native_exit"] != 0
    assert result["journal"]["allowed_requests"] >= 1
    assert result["allocation_closed"] is False
    assert result["fake_account_after"]["allocated_cny"] == "12.39"
    assert result.get("child_report", {}).get("real_seven_stage_pass") is not True


def test_owned_watchdog_keeps_partial_logs_and_never_deletes_active_directory(tmp_path):
    work = tmp_path / "working"
    work.mkdir()
    code = 'import pathlib,time;pathlib.Path("active").write_text("alive");print("started",flush=True);time.sleep(60)'
    env = {"PATH": str(Path(sys.executable).parent)}
    import os

    env.update({k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR"}})
    result = supervise(
        [sys.executable, "-c", code],
        cwd=work,
        output=tmp_path / "owned",
        watchdog_seconds=1,
        env=env,
    )
    assert result["watchdog_expired"] is True and result["owned_tree_exited"] is True
    assert result["forced_kill"] is True and result["allocation_releasable"] is False
    assert (work / "active").exists()
    assert b"started" in (tmp_path / "owned/stdout.log").read_bytes()


def test_parent_death_terminates_owned_child_and_keeps_allocated_fake_budget(tmp_path):
    from scripts.real_ai_control.account import once, read
    from offerpilot.evaluation.runner import process_tree
    import os
    import subprocess

    account = tmp_path / "fake-account.json"
    once(
        account,
        dict(
            authorized_cny="57.605790",
            retained_cny="0",
            allocated_cny="0",
            unallocated_cny="57.605790",
            refund_cny="0",
            closed_runs=[],
            authorization_history=["offline fake"],
        ),
    )
    code = f"""from pathlib import Path
import os,subprocess,sys,time
from offerpilot.evaluation.runner import process_tree
from scripts.real_ai_control.account import AccountSession
from scripts.real_ai_control.protocol import sha
p=Path({str(account)!r})
s=AccountSession(p,Path({str(tmp_path / "tx")!r}),expected_sha256=sha(p.read_bytes()),authorized_cny='57.605790',cap_cny='12.39',prior_journals=())
s.__enter__()
child=process_tree([sys.executable,'-c','import time;time.sleep(60)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
Path({str(tmp_path / "child-pid")!r}).write_text(str(child.process.pid))
os._exit(0)
"""
    tree = process_tree(
        [sys.executable, "-c", code],
        cwd=Path.cwd(),
        env=dict(os.environ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert tree.wait(timeout=10) == 0
    finally:
        tree.close(timeout=5)
    assert (tmp_path / "child-pid").is_file()
    assert read(account)["allocated_cny"] == "12.39" and read(account)["refund_cny"] == "0"


def test_metadata_failure_is_reported_on_private_control_before_any_generation(tmp_path):
    out = tmp_path / "probe"
    result = run_probe(out, fault="metadata_initial")
    assert result["allocation_closed"] is False and result["journal"]["allowed_requests"] == 0
    assert result["archive_control_fault"]["phase"] == "metadata"
    assert result["archive_control_fault"]["last_successful_ordinal"] == 0
    assert (out / "control-fault.json").exists()
    assert result["fake_account_after"]["allocated_cny"] == "12.39"
    assert result["owned_tree_exited"] is True


def test_split_python_and_native_diagnostics_never_deliver_key_in_artifacts(tmp_path):
    out = tmp_path / "probe"
    key = "sk-offline-split-diagnostic-key"
    result = run_probe(out, fault="diagnostic_secret", fake_key=key)
    assert result["owned_tree_exited"] is True and result["native_exit"] == 1
    assert not Path(result["private_work"]).is_relative_to(out)
    assert result["private_copy_cleanup"]["held_for_audit"] is False
    assert not list(Path(result["private_work"]).rglob("config.json"))
    for path in out.rglob("*"):
        if path.is_file():
            assert key.encode() not in path.read_bytes()
    assert result["native_diagnostics"]["complete"] is True


def test_bootstrap_write_failure_cleans_owned_tree_and_retains_fake_allocation(
    tmp_path, monkeypatch
):
    import scripts.real_ai_control.offline as control

    def fail(*args, **kwargs):
        raise OSError("offline bootstrap write failure")

    monkeypatch.setattr(control, "write_packet", fail)
    out = tmp_path / "probe"
    with pytest.raises(OSError):
        control.run_probe(out)
    evidence = json.loads((out / "emergency-owned-exit.json").read_bytes())
    assert evidence["owned_tree_exited"] is True
    assert json.loads((out / "fake-task-budget.json").read_bytes())["allocated_cny"] == "12.39"
