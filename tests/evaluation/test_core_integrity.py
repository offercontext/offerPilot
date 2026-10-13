import copy
import json
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from offerpilot.evaluation.core_graders import grade_rule
from offerpilot.evaluation.core_runner import aggregate, missing
from offerpilot.evaluation.core_actions import delivery_lease_expiry, stream_cursor
from offerpilot.evaluation import core_planning as planning
from offerpilot.evaluation.runner import ROOT


def test_missing_is_unknown_keeps_full_denominator(tmp_path, monkeypatch):
    monkeypatch.setattr(
        planning,
        "snapshot",
        lambda p: dict(
            commit="a" * 40, diff_hash="b" * 64, lock_hash="c" * 64, files_hash="d" * 64
        ),
    )
    monkeypatch.setattr(planning, "target_environment", lambda v: {})
    config = tmp_path / "variants.json"
    config.write_text(
        json.dumps([dict(variant_id="candidate", checkout=str(tmp_path), interpreter="unused")])
    )
    manifest = planning.create_plan(
        ROOT / "evals/suites/harness-core-v1.json", config, tmp_path / "out", repeat=3
    )
    summary = aggregate(manifest, [])
    assert summary["planned"] == 57
    assert summary["counts"] == {"pass": 0, "fail": 0, "incomplete": 57}
    assert all(r.status == "unknown" for r in missing(manifest.planned_trials[0], "missing").rules)


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "late_attach",
        "wrong_generation",
        "wrong_turn",
        "resync",
        "missing_generation",
        "missing_resume_parameter",
        "replayed_old_frame",
        "resync_recovered",
        "resync_wrong_turn",
        "resync_wrong_generation",
        "resync_stale_snapshot",
        "duplicate_actual_post",
        "wrong_submit_response",
    ],
)
def test_stream_success_requires_live_reattach_and_actual_generation_frames(mutation):
    facts = dict(
        disconnect_state="running",
        reattached=True,
        submit_ids=["request"],
        generation=1,
        turn_id="turn",
        reattached_before_release=True,
        disconnect_cursor="signed",
        reattach_after="signed",
        disconnect_event_seq=4,
        reattached_event_sequences=[5, 6],
        reattached_generations=[1],
        reattached_turns=["turn"],
        reattachment_errors=[],
    )
    http = [
        dict(
            method="POST",
            path="/api/pilot/runtime/v1/turns",
            status=202,
            body=dict(request_id="request"),
            response=dict(request_id="request", turn_id="turn", execution_generation=1),
        )
    ]
    if mutation == "late_attach":
        facts["reattached_before_release"] = False
    elif mutation == "wrong_generation":
        facts["reattached_generations"] = [2]
    elif mutation == "wrong_turn":
        facts["reattached_turns"] = ["other"]
    elif mutation == "resync":
        facts["reattachment_errors"] = ["runtime_resync_required"]
    elif mutation == "missing_generation":
        facts["generation"] = None
        facts["reattached_generations"] = [None]
    elif mutation == "missing_resume_parameter":
        facts["reattach_after"] = None
    elif mutation == "replayed_old_frame":
        facts["reattached_event_sequences"] = [3, 5]
    elif mutation == "duplicate_actual_post":
        http.append(copy.deepcopy(http[0]))
    elif mutation == "wrong_submit_response":
        http[0]["response"]["turn_id"] = "other"
    elif mutation.startswith("resync_"):
        facts["reattachment_errors"] = ["runtime_resync_required"]
        facts["reattachment_resync_snapshot"] = dict(
            turn_id="turn", execution_generation=1, snapshot_cursor="current", high_watermark=6
        )
        if mutation == "resync_wrong_turn":
            facts["reattachment_resync_snapshot"]["turn_id"] = "other"
        elif mutation == "resync_wrong_generation":
            facts["reattachment_resync_snapshot"]["execution_generation"] = 2
        elif mutation == "resync_stale_snapshot":
            facts["reattachment_resync_snapshot"]["high_watermark"] = 4
    assert grade_rule(
        "stream.same_generation", "E07.runtime_reattach", dict(facts=facts, http=http)
    )[0] == (mutation in {"none", "resync_recovered"})


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "reset",
        "missing_before",
        "wrong_generation",
        "missing_resync",
        "counter_reset",
        "allowance_reset",
    ],
)
def test_stream_budget_compares_disconnect_and_post_reattach_readback(mutation):
    before = dict(
        turn_id="turn",
        execution_generation=1,
        admitted_at=10,
        deadline_at=130,
        observation_stage="sse_disconnect",
        model_calls=1,
        title_model_calls=0,
        tool_calls=0,
        event_count=2,
        event_bytes=77,
        remaining_model_calls=19,
        remaining_title_model_calls=1,
        remaining_tool_calls=40,
        remaining_events=510,
        remaining_event_bytes=1048499,
        remaining_seconds=100,
    )
    after = {k: v for k, v in before.items() if k != "observation_stage"}
    facts = dict(turn_id="turn", generation=1, budget_snapshots=[before, after])
    if mutation == "reset":
        after["deadline_at"] = 140
    elif mutation == "missing_before":
        facts["budget_snapshots"] = [after]
    elif mutation == "wrong_generation":
        after["execution_generation"] = 2
    elif mutation == "missing_resync":
        facts["reattachment_errors"] = ["runtime_resync_required"]
    elif mutation == "counter_reset":
        after["model_calls"] = 0
    elif mutation == "allowance_reset":
        after["remaining_model_calls"] = 20
    assert grade_rule("budget.continuity", "E07.runtime_reattach", dict(facts=facts))[0] == (
        mutation == "none"
    )


def test_runtime_resume_route_rejects_invalid_after_cursor(tmp_path):
    from fastapi.testclient import TestClient
    from offerpilot.ai.types import Assistant
    from offerpilot.api import create_app
    from test_managed_runtime_api import _wait_for_status
    from uuid import uuid4

    class Model:
        def complete(self, messages, tools):
            return Assistant(content="完成。")

    with TestClient(create_app(data_dir=tmp_path, chat_model=Model())) as client:
        response = client.post(
            "/api/pilot/runtime/v1/turns",
            json={"request_id": str(uuid4()), "conversation_id": 0, "message": "只读回答"},
        )
        assert response.status_code == 202
        submitted = response.json()
        assert (
            _wait_for_status(client, submitted["turn_id"], terminal={"completed", "failed"})[
                "state"
            ]
            == "completed"
        )
        # Unknown query names are ignored by FastAPI; only `after` resumes.
        fresh = client.get(submitted["links"]["events_url"], params={"cursor": "invalid"})
        assert fresh.status_code == 200
        resumed = client.get(submitted["links"]["events_url"], params={"after": "invalid"})
        assert resumed.status_code == 409
        assert resumed.json()["error_code"] == "runtime_resync_required"


def withdrawal():
    old = {"id": "m", "current_version": 1, "state": "active"}
    new = old | {"current_version": 2, "state": "withdrawn"}
    mutation = {"id": "operation", "memory_id": "m", "version": 2, "fingerprint": "hash"}
    return {
        "facts": {
            "control_writes": [
                dict(
                    start=1,
                    end=5,
                    actor="fixture_control",
                    action="withdraw",
                    memory_id="m",
                    mutation_id="operation",
                    expected_version=1,
                    wire_hash="wire",
                    result=new,
                )
            ]
        },
        "observed": {
            "markers": [
                dict(
                    name="http_ingress", seq=2, request_hash="wire", path="/api/confirmed-memory/m"
                )
            ],
            "deltas": [
                dict(
                    seq=3,
                    epoch=1,
                    table="confirmed_memories",
                    operation="UPDATE",
                    old_json=old,
                    new_json=new,
                ),
                dict(
                    seq=4,
                    epoch=1,
                    table="confirmed_memory_mutations",
                    operation="INSERT",
                    old_json=None,
                    new_json=mutation,
                ),
            ],
        },
    }


def test_unreached_diagnostic_fault_can_never_produce_pass():
    evidence = {
        "facts": {"control_closed": True, "diagnostic_fault": "stale_source"},
        "observed": {"coverage": {"version": "core-observer-v1", "gaps": []}},
        "faults": [],
    }
    assert grade_rule("evidence.required_complete", "E09.memory_withdraw", evidence)[0] is None


def test_lease_uses_real_epoch_seconds_and_unobserved_expiry_is_unknown():
    assert delivery_lease_expiry(1791829345) == datetime.fromtimestamp(1791829345, tz=timezone.utc)
    with pytest.raises(ValueError):
        delivery_lease_expiry("2026-10-13T00:00:00Z")
    assert (
        grade_rule(
            "recovery.pending_then_expired",
            "E06.legacy_missing",
            {"facts": {"active_lease_pending": True}},
        )[0]
        is None
    )


def test_stream_cursor_reads_actual_runtime_data_frame():
    assert (
        stream_cursor('data: {"cursor":"signed-runtime-cursor","event_seq":2}')
        == "signed-runtime-cursor"
    )
    assert stream_cursor("id: other-provider-cursor") == "other-provider-cursor"
    assert stream_cursor('data: {"cursor":2}') is None
    assert stream_cursor("data: [DONE]") is None


@pytest.mark.parametrize(
    "mutation", ["other_memory", "extra_delta", "wrong_version", "bad_receipt"]
)
def test_fixture_control_rejects_concurrent_or_wrong_objects(mutation):
    evidence = withdrawal()
    assert grade_rule("business.authorized_deltas", "E09.memory_withdraw", evidence)[0] is True
    if mutation == "other_memory":
        evidence["observed"]["deltas"][1]["new_json"]["memory_id"] = "other"
    elif mutation == "extra_delta":
        evidence["observed"]["deltas"].append(copy.deepcopy(evidence["observed"]["deltas"][0]))
    elif mutation == "wrong_version":
        evidence["observed"]["deltas"][0]["new_json"]["current_version"] = 3
    else:
        evidence["observed"]["markers"][0]["request_hash"] = "wrong"
    assert grade_rule("business.authorized_deltas", "E09.memory_withdraw", evidence)[0] is False


def test_raw_socket_to_approved_provider_requires_validated_send_permission():
    script = """
import socket, json, sys, tempfile
from scripts.evaluation_subject.core_bootstrap import fence
from scripts.evaluation_subject.core_transport import SEND_PERMISSION
class Channel:
    config = {'data_dir': tempfile.gettempdir()}
    def attempt(self, purpose, **metadata): pass
server = socket.socket()
server.bind(('127.0.0.1', 0))
server.listen()
port = server.getsockname()[1]
fence(Channel(), set(), f'http://127.0.0.1:{port}/v1')
blocked = False
try:
    socket.create_connection(('127.0.0.1', port), timeout=1)
except PermissionError:
    blocked = True
SEND_PERMISSION.permitted = True
with socket.create_connection(('127.0.0.1', port), timeout=1): pass
SEND_PERMISSION.permitted = False
sys.__stdout__.write(json.dumps({'blocked': blocked}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, timeout=15
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert json.loads(completed.stdout) == {"blocked": True}


def test_script_reply_budget_includes_expected_error_and_restart():
    import time
    import httpx
    from offerpilot.evaluation.core_actions import CoreHTTPDriver

    counter = [0]
    sent = []
    observations = []
    for epoch in range(2):
        driver = CoreHTTPDriver(
            "http://localhost", "synthetic", time.monotonic() + 30, observations, 8, counter
        )
        driver.client.close()

        def response(request):
            sent.append(request)
            return httpx.Response(409, json={"error_code": "operation_delivery_pending"})

        driver.client = httpx.Client(
            base_url="http://localhost", transport=httpx.MockTransport(response)
        )
        for _ in range(4):
            assert driver.request("POST", "/expected-error", {}, allowed=(409,))["error_code"]
        if epoch == 1:
            with pytest.raises(TimeoutError, match="script reply watchdog"):
                driver.request("POST", "/expected-error", {}, allowed=(409,))
        driver.close()
    assert len(sent) == len(observations) == 8


def test_budget_continuity_groups_distinct_turn_admissions():
    from offerpilot.evaluation.core_graders import grade_rule

    budgets = [
        dict(turn_id=str(i), execution_generation=1, admitted_at=10 + i, deadline_at=40 + i)
        for i in range(3)
    ]
    facts = dict(
        budget_snapshots=budgets + budgets,
        queued_probe=dict(state="interrupted", budget=dict(model_calls=0)),
        queued_admission=dict(state="queued"),
        deadline_model_barriers=2,
    )
    evidence = dict(facts=facts, observed=dict(deltas=[]), calls=[])
    assert grade_rule("budget.continuity", "E08.runtime_deadline", evidence)[0] is True
    facts["queued_probe"]["state"] = "failed"
    assert grade_rule("budget.continuity", "E08.runtime_deadline", evidence)[0] is True
    facts["budget_snapshots"][-1] = budgets[-1] | dict(deadline_at=100)
    assert grade_rule("budget.continuity", "E08.runtime_deadline", evidence)[0] is False


def test_rejection_requires_actual_cancelled_receipt_without_continuation():
    from offerpilot.evaluation.core_graders import grade_rule

    facts = dict(
        reject_high_water=10,
        operation_id="op",
        rejection_receipt=dict(write_status="cancelled", operation_id="op"),
    )
    evidence = dict(facts=facts, observed=dict(deltas=[]), calls=[])
    assert grade_rule("reject.no_continuation", "E03.runtime_reject", evidence)[0] is True
    evidence["calls"].append(dict(observer_high_water=11, purpose="chat"))
    assert grade_rule("reject.no_continuation", "E03.runtime_reject", evidence)[0] is False


def test_unknown_requires_real_primary_and_no_late_success_or_write():
    from offerpilot.evaluation.core_graders import grade_rule

    f = dict(
        primary_barrier=True,
        primary_barrier_high_water=12,
        manager_terminal_state="result_unknown",
        terminal_state="interrupted",
        late_state="interrupted",
        generation=2,
        late_generation=2,
    )
    e = dict(facts=f, observed=dict(deltas=[]), calls=[dict(purpose="execute_primary")])
    assert grade_rule("unknown.fenced", "E08.runtime_unknown", e)[0] is True
    f["late_state"] = "completed"
    assert grade_rule("unknown.fenced", "E08.runtime_unknown", e)[0] is False
    f["late_state"] = "interrupted"
    e["observed"]["deltas"].append(dict(seq=13))
    assert grade_rule("unknown.fenced", "E08.runtime_unknown", e)[0] is False


def test_budget_failed_state_requires_actual_product_budget_origin():
    from offerpilot.evaluation.core_graders import grade_rule

    f = dict(
        terminal_state="failed",
        termination_source="product_budget",
        budget_reason="runtime budget exhausted",
    )
    e = dict(
        facts=f,
        observed=dict(deltas=[]),
        calls=[],
        budget_events=[
            dict(category="budget_termination", origin="RuntimeBudget", resource="model")
        ],
    )
    assert grade_rule("budget.product_origin", "E08.runtime_model_budget", e)[0] is True
    f["termination_source"] = "normal"
    assert grade_rule("budget.product_origin", "E08.runtime_model_budget", e)[0] is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows platform version fallback")
def test_windows_metadata_fallback_does_not_weaken_product_process_fence(tmp_path):
    code = r'''
import json, os, platform, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))
from scripts.evaluation_subject.core_bootstrap import fence
# Model the public metadata seam's native process audit boundary; no actual
# command or live WMI service is needed to exercise the cold fallback.
platform._uname_cache = None
def metadata_version(*args, **kwargs):
    sys.audit("subprocess.Popen", "cmd.exe", ["cmd.exe", "/c", "ver"], None, None)
    return "10", "10.0.19045", "", "Multiprocessor"
platform.win32_ver = metadata_version
class Channel:
    config = {"data_dir": sys.argv[2]}
    calls = []
    def attempt(self, purpose, **fields):
        self.calls.append({"purpose": purpose, **fields})
channel = Channel()
fence(channel, set(), None)
try:
    machine = platform.machine()
    metadata_error = None
except PermissionError:
    machine, metadata_error = None, "PermissionError"
metadata_blocked = len(channel.calls)
try:
    os.system("echo forbidden-product-command")
    forbidden_blocked = False
except PermissionError:
    forbidden_blocked = True
print(json.dumps({"machine": machine, "metadata_error": metadata_error,
                  "metadata_blocked": metadata_blocked,
                  "forbidden_blocked": forbidden_blocked,
                  "blocked_after_product_attempt": len(channel.calls)}), file=sys.__stdout__)
'''
    process = subprocess.run(
        [sys.executable, "-c", code, str(ROOT), str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True, timeout=60, check=False,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["machine"] and result["metadata_error"] is None
    assert result["metadata_blocked"] == 0
    assert result["forbidden_blocked"] and result["blocked_after_product_attempt"] == 1
