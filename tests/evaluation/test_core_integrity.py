import copy
import json
import subprocess
import sys

import pytest

from offerpilot.evaluation.core_graders import grade_rule
from offerpilot.evaluation.core_runner import aggregate, missing
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
