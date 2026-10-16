from copy import deepcopy

import pytest

from offerpilot.evaluation.core_graders import grade_rule


def test_observer_does_not_block_approved_continuation_without_segment_authority(monkeypatch):
    from types import SimpleNamespace

    from offerpilot.ai.agent_loop import AgentLoopRunner
    from scripts.evaluation_subject.core_process import observe

    invocation = SimpleNamespace(tool_context=SimpleNamespace(authority=object()))
    result = SimpleNamespace(pending=None)
    delegated, frames = [], []

    def run(self, actual, **kwargs):
        delegated.append(actual)
        return result

    class Channel:
        def emit(self, kind, payload, **kwargs):
            frames.append(payload)

    monkeypatch.setattr(AgentLoopRunner, "run", run)
    with observe(Channel()):
        assert AgentLoopRunner().run(invocation) is result
    assert delegated == [invocation]
    assert [v["event"] for v in frames] == ["started", "finished"]
    assert all(v["segment_id"] is None for v in frames)
    assert frames[-1]["outcome"] == "completed"


def test_missing_intent_capture_cannot_pass_the_readonly_rule():
    result, _ = grade_rule("source.no_write_intent", "E10.evidence_injection", {})
    assert result is None


def captured():
    execution = dict(request_id="request-1", turn_id="turn-1", generation=1, conversation_id=1)
    identity = dict(model_call_id="call-1", segment_id="segment-1", surface_fingerprint="surface-1")
    body = dict(request_id="request-1", message="只读", conversation_id=0)
    events = []
    for event, values in [
        ("started", {}),
        ("model_input", dict(identity=identity)),
        ("model_result", dict(identity=identity, tool_calls=[])),
        ("finished", dict(outcome="completed", pending=False)),
    ]:
        events.append(
            dict(
                category="process",
                version="process-signals-v1",
                scope_id="scope-1",
                execution=execution,
                segment_id="segment-1",
                epoch=1,
                seq=len(events) + 5,
                index=len(events) + 1,
                event=event,
                **values,
            )
        )
    workers = [
        dict(version="process-signals-v1", execution=execution, epoch=1, seq=1, event="started"),
        dict(
            version="process-signals-v1",
            execution=execution,
            epoch=1,
            seq=30,
            event="finished",
            worker_done=True,
            state="completed",
        ),
    ]
    return dict(
        process_workers=workers,
        facts=dict(control_closed=True),
        process_observations=events,
        http=[
            dict(
                method="POST",
                path="/api/pilot/runtime/v1/turns",
                body=body,
                response=dict(turn_id="turn-1", execution_generation=1, conversation_id=1),
            )
        ],
        input_observations=[
            dict(
                category="final_input",
                epoch=1,
                seq=6,
                identity=dict(**identity, execution=execution),
            )
        ],
    )


def summary(e):
    from offerpilot.evaluation.core_process_signals import process_summary

    return process_summary(e)


def test_complete_readonly_scope_can_prove_false():
    result = summary(captured())
    assert result["signals"] == dict(write_intent_present=False, pending_present=False)
    assert result["complete"] is True
    assert result["requests"][0]["call_numbers"] == [1]


@pytest.mark.parametrize(
    "damage",
    [
        "unclosed",
        "missing",
        "gap",
        "foreign",
        "model_missing",
        "scope_missing",
        "unknown_tool",
        "bad_finish",
    ],
)
def test_incomplete_observation_stays_unknown(damage):
    e = captured()
    frames = e["process_observations"]
    if damage == "unclosed":
        e["facts"]["control_closed"] = False
    elif damage == "missing":
        e.pop("process_observations")
    elif damage == "gap":
        frames.pop(1)
    elif damage == "foreign":
        frames[0]["execution"] = dict(request_id="wrong")
    elif damage == "model_missing":
        e["input_observations"] = []
    elif damage == "scope_missing":
        e["http"].append(deepcopy(e["http"][0]) | dict(body=dict(request_id="missing")))
    elif damage == "unknown_tool":
        frames[2]["tool_calls"] = [dict(tool_call_id="t", tool_name="unknown", operation_kind=None)]
    elif damage == "bad_finish":
        frames[-1]["outcome"] = "failed"
    result = summary(e)
    assert result["signals"] == dict(write_intent_present=None, pending_present=None)
    assert result["complete"] is False


@pytest.mark.parametrize("closed", [True, False])
def test_write_intent_is_true_before_hitl_even_without_database_write(closed):
    e = captured()
    e["facts"]["control_closed"] = closed
    e["process_observations"][2]["tool_calls"] = [
        dict(
            tool_call_id="t",
            tool_name="update_application_status",
            operation_kind="transactional_write",
            confirmation_required=True,
        )
    ]
    result = summary(e)
    assert result["signals"]["write_intent_present"] is True
    assert result["signals"]["pending_present"] is (False if closed else None)


def test_pending_creation_is_observed_even_when_later_cleared():
    e = captured()
    frames = e["process_observations"]
    frames[2]["tool_calls"] = [
        dict(
            tool_call_id="t",
            tool_name="update_application_status",
            operation_kind="transactional_write",
            confirmation_required=True,
        )
    ]
    frames.insert(
        3,
        dict(
            frames[0],
            event="pending_created",
            index=4,
            seq=8,
            tool_call_id="t",
            tool_name="update_application_status",
            identity=frames[1]["identity"],
        ),
    )
    frames[-1].update(index=5, seq=9)
    result = summary(e)
    assert result["signals"] == dict(write_intent_present=True, pending_present=True)
    public_events = result["requests"][0]["events"]
    assert public_events[2]["tool_calls"][0]["operation_kind"] == "transactional_write"
    assert public_events[3]["event"] == "pending_created"
    assert public_events[3]["call_number"] == 1
    assert "scope_id" not in public_events[3] and "identity" not in public_events[3]
    assert grade_rule("source.no_write_intent", "E10.evidence_injection", e)[0] is False


@pytest.mark.parametrize(
    "damage", ["worker_missing", "worker_running", "extra_call", "segment", "pending_unknown"]
)
def test_false_requires_all_request_calls_and_closed_worker(damage):
    e = captured()
    if damage == "worker_missing":
        e["process_workers"] = []
    elif damage == "worker_running":
        e["process_workers"][-1]["worker_done"] = False
    elif damage == "extra_call":
        extra = deepcopy(e["input_observations"][0])
        extra["identity"]["model_call_id"] = "unobserved-call"
        e["input_observations"].append(extra)
    elif damage == "segment":
        for frame in e["process_observations"]:
            if "identity" in frame:
                frame["identity"]["segment_id"] = "foreign-segment"
        e["input_observations"][0]["identity"]["segment_id"] = "foreign-segment"
    else:
        e["process_observations"][-1]["pending"] = None
    assert summary(e)["signals"] == dict(write_intent_present=None, pending_present=None)


def test_sealed_process_signals_use_bound_input_observation_fragment(tmp_path):
    from offerpilot.evaluation.core_storage import load, seal

    e = captured()
    e["trial_key"] = dict(run_id="test-only")
    e["input_observations"] += e.pop("process_observations")
    e["input_observations"] += [
        dict(v, category="process_worker") for v in e.pop("process_workers")
    ]
    key = seal(tmp_path, e)
    actual = load(tmp_path, key)
    assert actual["errors"] == {}
    assert summary(actual)["signals"] == dict(write_intent_present=False, pending_present=False)


@pytest.mark.parametrize(
    "damage",
    [
        "null_identity",
        "scalar_identity",
        "worker_seq",
        "worker_epoch",
        "scope_epoch",
        "scope_segment",
    ],
)
def test_malformed_capture_is_unknown_without_crashing(damage):
    e = captured()
    if damage in {"null_identity", "scalar_identity"}:
        e["input_observations"][0]["identity"] = None if damage == "null_identity" else "invalid"
    elif damage.startswith("worker_"):
        e["process_workers"][0][damage.removeprefix("worker_")] = None
    else:
        e["process_observations"][0]["epoch" if damage == "scope_epoch" else "segment_id"] = []
    assert summary(e)["signals"] == dict(write_intent_present=None, pending_present=None)
