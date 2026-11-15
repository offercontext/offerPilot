"""A manager terminal projection can precede the committed execution terminal."""

import sqlite3
import time
from types import SimpleNamespace

import httpx
import pytest

from offerpilot.evaluation import core_actions as actions
from offerpilot.evaluation.adapters import ProductFailure


def test_unknown_late_release_waits_for_committed_execution_terminal(tmp_path, monkeypatch):
    database = tmp_path / "data.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TABLE pilot_executions (turn_id TEXT, generation INTEGER, conversation_id INTEGER, state TEXT, protocol TEXT)"
        )
        conn.execute(
            "INSERT INTO pilot_executions VALUES ('turn',2,1,'running','pilot-runtime-v1')"
        )

    def state():
        with sqlite3.connect(database) as conn:
            return conn.execute("SELECT state FROM pilot_executions").fetchone()[0]

    confirmed = False
    released = False
    sleeps = []
    pending = dict(
        tool_name="update_application_event",
        operation_id="operation",
        confirmation_token="synthetic",
        args=dict(id=1, scheduled_at="2026-10-14T15:00:00+08:00"),
    )
    identity = dict(
        turn_id="turn",
        conversation_id=1,
        execution_generation=2,
        protocol_version="pilot-runtime-v1",
    )

    class Driver:
        deadline = time.monotonic() + 10
        client = httpx.Client()

        def request(self, method, path, body=None, allowed=(200, 202)):
            nonlocal confirmed
            if method == "POST" and path.endswith("/confirm"):
                confirmed = True
                return dict(execution_generation=2)
            if method == "POST":
                return dict(turn_id="turn", conversation_id=1, execution_generation=1)
            if path == "/api/chat/conversations/1/execution":
                return dict(execution=identity | dict(state=state()))
            if not confirmed:
                return dict(
                    state="waiting_confirmation",
                    terminal=dict(response=dict(pending_action=pending)),
                )
            terminal_state = (
                "interrupted" if released or state() == "interrupted" else "result_unknown"
            )
            return identity | dict(
                state=terminal_state,
                execution=identity | dict(state=terminal_state),
                worker_done=released,
                actual_worker_alive=not released,
            )

    barrier = dict(
        type="fault_reached",
        boundary="accepted_primary_tool",
        stage="primary_committed_before_result_delivery",
        operation_id="operation",
        observer_high_water=6,
        seq=9,
        epoch=1,
    )

    def send(command):
        nonlocal released
        assert state() == "interrupted", (
            "late primary released before its durable terminal premise was established"
        )
        assert command == dict(command="ack", seq=9)
        released = True

    def sleep(seconds):
        sleeps.append(seconds)
        if seconds != 3:
            with sqlite3.connect(database) as conn:
                conn.execute("UPDATE pilot_executions SET state='interrupted'")

    monkeypatch.setattr(actions.time, "sleep", sleep)
    monkeypatch.setattr(actions.observer, "marker", lambda *a, **k: 6)
    monkeypatch.setattr(actions, "operation_facts", lambda *a, **k: {})
    evidence = dict(
        facts=dict(aliases=dict(target_application=1, technical_interview=1)),
        milestones={},
        http=[],
    )
    driver = Driver()
    try:
        actions.drive(
            "E08.runtime_unknown",
            driver,
            SimpleNamespace(events=[barrier], send=send),
            tmp_path,
            evidence,
            lambda: None,
        )
    finally:
        driver.client.close()
    assert released and 3 in sleeps
    assert evidence["facts"]["terminal_state"] == evidence["facts"]["late_state"] == "interrupted"
    assert evidence["facts"]["generation"] == evidence["facts"]["late_generation"] == 2
    assert evidence["facts"]["unknown_first_projection"]["state"] == "result_unknown"
    assert [v["execution"]["state"] for v in evidence["facts"]["unknown_control_reads"]] == [
        "running",
        "interrupted",
    ]
    assert evidence["facts"]["unknown_before_release"]["worker_done"] is False
    assert evidence["facts"]["unknown_control_read_scope"]["side_effect_free"] is False


@pytest.mark.parametrize(
    "change",
    [
        dict(turn_id="other"),
        dict(conversation_id=2),
        dict(execution_generation=3),
        dict(execution_generation=True),
        dict(protocol_version="legacy"),
        dict(state="completed"),
        dict(state="failed"),
        dict(state=None),
    ],
)
def test_unknown_boundary_rejects_wrong_identity_or_terminal(change):
    identity = dict(
        turn_id="turn",
        conversation_id=1,
        execution_generation=2,
        protocol_version="pilot-runtime-v1",
    )
    requests = []

    def request(method, path):
        requests.append(path)
        return dict(execution=identity | dict(state="interrupted") | change)

    driver = SimpleNamespace(deadline=time.monotonic() + 10, request=request)
    with pytest.raises(ProductFailure):
        actions.unknown_durable_terminal(
            driver, identity | dict(state="result_unknown"), "turn", 1, 2, {}
        )
    assert requests == ["/api/chat/conversations/1/execution"]


@pytest.mark.parametrize("execution", [None, [], {}, "interrupted"])
def test_unknown_boundary_rejects_missing_control_receipt(execution):
    identity = dict(
        turn_id="turn",
        conversation_id=1,
        execution_generation=2,
        protocol_version="pilot-runtime-v1",
    )
    driver = SimpleNamespace(
        deadline=time.monotonic() + 10, request=lambda *a: dict(execution=execution)
    )
    with pytest.raises(ProductFailure):
        actions.unknown_durable_terminal(driver, identity, "turn", 1, 2, {})


def test_unknown_boundary_does_not_extend_watchdog_or_accept_running(monkeypatch):
    identity = dict(
        turn_id="turn",
        conversation_id=1,
        execution_generation=2,
        protocol_version="pilot-runtime-v1",
    )
    driver = SimpleNamespace(
        deadline=1, request=lambda *a: dict(execution=identity | dict(state="running"))
    )
    clock = iter([0, 2])
    monkeypatch.setattr(actions.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(actions.time, "sleep", lambda _: None)
    facts = {}
    with pytest.raises(TimeoutError, match="trial watchdog"):
        actions.unknown_durable_terminal(driver, identity, "turn", 1, 2, facts)
    assert driver.deadline == 1
    assert len(facts["unknown_control_reads"]) == 1
    assert "unknown_before_release" not in facts


def test_unknown_boundary_rejects_runtime_state_disagreement():
    identity = dict(
        turn_id="turn",
        conversation_id=1,
        execution_generation=2,
        protocol_version="pilot-runtime-v1",
    )

    def request(method, path):
        if path.endswith("/execution"):
            return dict(execution=identity | dict(state="interrupted"))
        return identity | dict(state="completed", execution=identity | dict(state="completed"))

    driver = SimpleNamespace(deadline=time.monotonic() + 10, request=request)
    with pytest.raises(ProductFailure, match="disagree"):
        actions.unknown_durable_terminal(driver, identity, "turn", 1, 2, {})
