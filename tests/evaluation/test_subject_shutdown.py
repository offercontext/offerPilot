"""Deterministic subject shutdown interleavings; no model or product server."""
import contextlib
import importlib
import io
import json
import subprocess
from types import SimpleNamespace
import threading

import pytest


@pytest.fixture(params=["bootstrap", "core_bootstrap"])
def bridge(request):
    # The executable bridge reserves stdout at import; restore pytest's stdout.
    with contextlib.redirect_stdout(io.StringIO()):
        return importlib.import_module("scripts.evaluation_subject." + request.param)


def config():
    return {
        "checkout": ".",
        "trial_key": {"execution_item_id": "E06.legacy_saved"},
        "epoch": 2,
        "fault_profile": "none",
        "limits": {"wall_seconds": 180, "shutdown_seconds": 30},
    }


@pytest.mark.parametrize("explicit", [True, False], ids=["shutdown", "eof"])
def test_main_retains_shutdown_before_server_publication(bridge, monkeypatch, explicit):
    """Publish only during main's join: the original one-time server check loses stop."""
    channel_type = bridge.Channel if bridge.__name__.endswith(".bootstrap") else bridge.CoreChannel
    channel = channel_type(config())
    server = SimpleNamespace(should_exit=False)
    joins = []

    class Worker:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def join(self, timeout):
            joins.append(timeout)
            channel.server = server

        def is_alive(self):
            return False

    thread_api = dict(vars(threading))
    thread_api["Thread"] = Worker
    monkeypatch.setattr(bridge, "threading", SimpleNamespace(**thread_api))
    monkeypatch.setattr(bridge, "subprocess", SimpleNamespace(
        check_output=lambda *a, **kw: b"frozen\n", DEVNULL=subprocess.DEVNULL))
    monkeypatch.setattr(bridge, "Channel" if bridge.__name__.endswith(".bootstrap") else "CoreChannel",
                        lambda value: channel)
    commands = json.dumps({"command": "prepare", **config()}) + "\n"
    if explicit:
        commands += json.dumps({"command": "request_shutdown"}) + "\n"
    monkeypatch.setattr(bridge.sys, "stdin", io.StringIO(commands))
    bridge.main()

    assert joins == [30 if explicit else 2]
    assert server.should_exit is True


@pytest.fixture
def channel(bridge):
    cls = bridge.Channel if bridge.__name__.endswith(".bootstrap") else bridge.CoreChannel
    return cls(config())


@pytest.fixture
def wire(monkeypatch):
    with contextlib.redirect_stdout(io.StringIO()):
        base = importlib.import_module("scripts.evaluation_subject.bootstrap")

    class Wire(io.StringIO):
        def __init__(self):
            super().__init__()
            self.sent = threading.Event()

        def flush(self):
            self.sent.set()

    result = Wire()
    monkeypatch.setattr(base, "CONTROL", result)
    return result


@pytest.mark.parametrize("before", [True, False])
def test_server_binding_interleavings(channel, before):
    server = SimpleNamespace(should_exit=False)
    if before:
        channel.request_shutdown()
    assert channel.bind_server(server) is (not before)
    if not before:
        channel.request_shutdown()
    assert channel.server is server and server.should_exit
    deadline = channel.shutdown_deadline
    channel.request_shutdown()
    assert channel.shutdown_deadline == deadline


def emit_in_thread(channel, wire, kind):
    result = []

    def work():
        try:
            if kind == "provider_attempt":
                channel.provider_attempt()
            else:
                channel.emit(kind, {}, ack=True)
            result.append("continued")
        except BaseException as exc:
            result.append(exc)

    worker = threading.Thread(target=work)
    worker.start()
    assert wire.sent.wait(2), "control write did not complete"
    return worker, result, json.loads(wire.getvalue().splitlines()[0])["seq"]


@pytest.mark.parametrize("kind", ["prepared", "ready", "call_attempt"])
def test_shutdown_cancels_ack_without_business_continuation(channel, wire, kind):
    worker, result, seq = emit_in_thread(channel, wire, kind)
    try:
        channel.acknowledge(seq + 100)
        assert not result
        channel.request_shutdown()
        worker.join(2)
        assert not worker.is_alive()
        assert len(result) == 1 and type(result[0]).__name__ == "SubjectStopping"
        assert channel.pending == {}
    finally:
        channel.request_shutdown()
        worker.join(2)


@pytest.mark.parametrize("bridge", ["core_bootstrap"], indirect=True)
def test_provider_send_is_cancelled_at_ack_boundary(bridge, channel, wire):
    worker, result, _ = emit_in_thread(channel, wire, "provider_attempt")
    try:
        channel.request_shutdown()
        worker.join(2)
        assert not worker.is_alive() and type(result[0]).__name__ == "SubjectStopping"
        assert "continued" not in result and not channel.pending
    finally:
        channel.request_shutdown()
        worker.join(2)


@pytest.mark.parametrize("ack_first", [True, False])
def test_shutdown_wins_over_event_wakeup_under_original_lock(channel, wire, ack_first):
    worker, result, seq = emit_in_thread(channel, wire, "ready")
    try:
        with channel.lock:
            if ack_first:
                channel.acknowledge(seq)
            channel.request_shutdown()
            channel.acknowledge(seq)
        worker.join(2)
        assert not worker.is_alive()
        assert len(result) == 1 and type(result[0]).__name__ == "SubjectStopping"
        assert not channel.pending
    finally:
        channel.request_shutdown()
        worker.join(2)


def test_actual_ack_allows_active_continuation(channel, wire):
    worker, result, seq = emit_in_thread(channel, wire, "ready")
    try:
        channel.acknowledge(seq)
        worker.join(2)
        assert not worker.is_alive() and result == ["continued"]
        assert channel.pending == {} and not channel.shutdown_requested
    finally:
        channel.request_shutdown()
        worker.join(2)


def test_two_pending_requests_require_their_own_reverse_order_ack(channel, wire, monkeypatch):
    both_sent = threading.Event()
    done = {"prepared": threading.Event(), "ready": threading.Event()}
    outcomes = {}
    original_flush = wire.flush

    def flush():
        original_flush()
        if len(wire.getvalue().splitlines()) == 2:
            both_sent.set()

    monkeypatch.setattr(wire, "flush", flush)

    def emit(kind):
        try:
            channel.emit(kind, {}, ack=True)
            outcomes[kind] = "continued"
        except BaseException as exc:
            outcomes[kind] = exc
        finally:
            done[kind].set()

    threads = [threading.Thread(target=emit, args=(kind,)) for kind in done]
    try:
        for worker in threads:
            worker.start()
        assert both_sent.wait(2)
        messages = [json.loads(line) for line in wire.getvalue().splitlines()]
        first, second = messages
        assert len(channel.pending) == 2
        channel.acknowledge(second["seq"] + 100)
        assert not any(event.is_set() for event in done.values())
        channel.acknowledge(second["seq"])
        assert done[second["type"]].wait(2)
        assert not done[first["type"]].is_set()
        channel.acknowledge(first["seq"])
        for worker in threads:
            worker.join(2)
        assert not any(worker.is_alive() for worker in threads)
        assert outcomes == {"prepared": "continued", "ready": "continued"}
        assert not channel.pending
    finally:
        channel.request_shutdown()
        for worker in threads:
            if worker.ident is not None:
                worker.join(2)


@pytest.mark.parametrize("controlled", [False, True])
def test_missing_ack_preserves_active_wait_limits(bridge, channel, wire, monkeypatch, controlled):
    base = importlib.import_module("scripts.evaluation_subject.bootstrap")
    waits = []

    class Pending:
        acknowledged = False
        wakeup = SimpleNamespace(wait=lambda timeout: waits.append(timeout) or False)

    monkeypatch.setattr(base, "PendingAck", Pending)
    channel.config["fault_profile"] = "executor_block" if controlled else "none"
    with pytest.raises(RuntimeError, match="ACK missing"):
        channel.emit("fault_reached", {}, ack=True)
    expected = 90 if controlled and bridge.__name__.endswith(".core_bootstrap") else 30
    assert waits == [expected]
    assert not channel.pending


def test_stop_rejects_new_attempts_and_keeps_terminal_records(channel, wire):
    channel.request_shutdown()
    with pytest.raises(RuntimeError, match="shutdown requested"):
        channel.attempt("tool")
    assert wire.getvalue() == ""
    channel.emit("error", {"error_type": "CleanupFailure"})
    assert json.loads(wire.getvalue())["type"] == "error"


@pytest.mark.parametrize("failure", [None, "ready", "cleanup"])
def test_completion_requires_ready_exit_and_resource_cleanup(channel, wire, failure):
    base = importlib.import_module("scripts.evaluation_subject.bootstrap")
    actions = []
    resources = contextlib.ExitStack()

    def close_resource():
        actions.append("resource_closed")
        if failure == "cleanup":
            raise OSError("cleanup failed")

    resources.callback(close_resource)

    class Ready:
        def join(self, timeout):
            actions.append("ready_join")
            assert 0 <= timeout <= 30

        def is_alive(self):
            return failure == "ready"

    if failure:
        with pytest.raises((RuntimeError, OSError)):
            base.finish_subject(channel, resources, Ready())
        assert not channel.closed and wire.getvalue() == ""
    else:
        base.finish_subject(channel, resources, Ready())
        assert channel.closed and json.loads(wire.getvalue())["type"] == "shutdown_complete"
    assert actions == ["ready_join", "resource_closed"]


def test_main_retains_original_failure_for_unresponsive_worker(bridge, monkeypatch, channel):
    joins = []

    class Worker:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def join(self, timeout):
            joins.append(timeout)

        def is_alive(self):
            return True

    api = dict(vars(threading))
    api["Thread"] = Worker
    monkeypatch.setattr(bridge, "threading", SimpleNamespace(**api))
    monkeypatch.setattr(bridge, "subprocess", SimpleNamespace(
        check_output=lambda *a, **kw: b"frozen\n", DEVNULL=subprocess.DEVNULL))
    monkeypatch.setattr(bridge, "Channel" if bridge.__name__.endswith(".bootstrap") else "CoreChannel",
                        lambda value: channel)
    monkeypatch.setattr(bridge.sys, "stdin", io.StringIO(
        json.dumps({"command": "prepare", **config()}) + "\n"
        + json.dumps({"command": "request_shutdown"}) + "\n"))

    def exit_process(code):
        raise SystemExit(code)

    monkeypatch.setattr(bridge, "os", SimpleNamespace(_exit=exit_process))
    with pytest.raises(SystemExit) as caught:
        bridge.main()
    assert caught.value.code == 3 and joins == [30] and channel.shutdown_requested


def test_ready_wrapper_only_swallows_shutdown():
    base = importlib.import_module("scripts.evaluation_subject.bootstrap")

    def cancelled():
        raise base.SubjectStopping("stop")

    base.run_ready(cancelled)

    def broken():
        raise ValueError("ready failed")

    with pytest.raises(ValueError, match="ready failed"):
        base.run_ready(broken)
