from __future__ import annotations

import threading
import time

import pytest

import offerpilot.pilot_runtime.managed_execution as managed_execution
from offerpilot.pilot_runtime.contracts import (
    AssistantDeltaEvent,
    MessageOutcome,
)
from offerpilot.pilot_runtime.managed_execution import (
    RuntimeExecutionManager,
    RuntimeCapacityExhausted,
    RuntimeManagerError,
    RuntimeResyncRequired,
    RuntimeSubmissionConflict,
)


def test_manager_starts_fixed_pool_only_after_first_submission(monkeypatch) -> None:
    created: list[threading.Thread] = []

    class RecordingThread(threading.Thread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(managed_execution, "Thread", RecordingThread)
    manager = RuntimeExecutionManager(run_workers=2, max_queue=2)
    try:
        assert created == []
        with manager.reserve_admission():
            assert created == []

        manager.submit(
            turn_id="lazy-start-1",
            conversation_id=1,
            operation=lambda *_: MessageOutcome("done", conversation_id=1),
        )
        assert len(created) == 3
        assert all(thread.is_alive() for thread in created)
        assert manager.wait("lazy-start-1", timeout=1.0)

        manager.submit(
            turn_id="lazy-start-2",
            conversation_id=1,
            operation=lambda *_: MessageOutcome("done", conversation_id=1),
        )
        assert len(created) == 3
        assert manager.wait("lazy-start-2", timeout=1.0)
    finally:
        manager.close(wait=True)
    assert all(not thread.is_alive() for thread in created)


def test_manager_can_close_before_start(monkeypatch) -> None:
    created: list[threading.Thread] = []

    class RecordingThread(threading.Thread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(managed_execution, "Thread", RecordingThread)
    manager = RuntimeExecutionManager(run_workers=2)
    manager.close(wait=True)
    assert created == []
    with pytest.raises(RuntimeManagerError):
        manager.submit(
            turn_id="closed-before-start",
            conversation_id=1,
            operation=lambda *_: MessageOutcome("done", conversation_id=1),
        )


def test_partial_worker_start_failure_closes_without_stranded_run(monkeypatch) -> None:
    created: list[threading.Thread] = []

    class PartialStartThread(threading.Thread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

        def start(self) -> None:
            if self.name == "offerpilot-runtime-worker-2":
                raise RuntimeError("synthetic worker start failure")
            super().start()

    monkeypatch.setattr(managed_execution, "Thread", PartialStartThread)
    manager = RuntimeExecutionManager(run_workers=2)
    with pytest.raises(RuntimeError, match="synthetic worker start failure"):
        manager.submit(
            turn_id="partial-start-failure",
            conversation_id=1,
            operation=lambda *_: MessageOutcome("done", conversation_id=1),
        )

    assert len(created) == 3
    assert manager.queued_count == 0
    assert manager.active_worker_count == 0
    with pytest.raises(RuntimeManagerError):
        manager.submit(
            turn_id="rejected-after-start-failure",
            conversation_id=1,
            operation=lambda *_: MessageOutcome("done", conversation_id=1),
        )

    first_worker = next(
        thread for thread in created if thread.name == "offerpilot-runtime-worker-1"
    )
    deadline = time.monotonic() + 1.0
    while first_worker.is_alive():
        if time.monotonic() >= deadline:
            raise AssertionError("started worker did not exit after startup failure")
        time.sleep(0.005)
    manager.close(wait=True)
    assert all(not thread.is_alive() for thread in created)


def test_admission_reservation_excludes_other_producers_and_releases_on_failure() -> None:
    manager = RuntimeExecutionManager(run_workers=1, max_queue=1)
    started = threading.Event()
    release = threading.Event()

    def run(*_args):
        started.set()
        release.wait(2)
        return MessageOutcome("done", conversation_id=1)

    try:
        with pytest.raises(ValueError):
            with manager.reserve_admission():
                with pytest.raises(RuntimeCapacityExhausted):
                    manager.submit(turn_id="other", conversation_id=2, operation=run)
                raise ValueError("host admission failed before persistence")
        with manager.reserve_admission() as token:
            with pytest.raises(RuntimeCapacityExhausted):
                with manager.reserve_admission():
                    pytest.fail("second producer entered a reserved queue")
            manager.submit(turn_id="reserved", conversation_id=1, operation=run,
                           admission_reservation=token)
        assert started.wait(1)
        with pytest.raises(RuntimeSubmissionConflict):
            manager.submit(turn_id="forged", conversation_id=3, operation=run,
                           admission_reservation=token)
    finally:
        release.set()
        manager.close(wait=True)


def test_submit_is_idempotent_and_disconnect_does_not_cancel_worker() -> None:
    manager = RuntimeExecutionManager(run_workers=1, max_queue=2)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def run(sink, _control, _budget):
        nonlocal calls
        calls += 1
        started.set()
        sink.emit(AssistantDeltaEvent(delta="hello"))
        release.wait(1.0)
        return MessageOutcome("done", conversation_id=7)

    first = manager.submit(
        turn_id="turn-1",
        conversation_id=7,
        generation=1,
        request_id="request-1",
        operation=run,
    )
    second = manager.submit(
        turn_id="turn-1",
        conversation_id=7,
        generation=1,
        request_id="request-1",
        operation=run,
    )
    assert first.turn_id == second.turn_id
    assert second.replayed is True
    assert started.wait(0.5)

    subscription = manager.subscribe("turn-1", after=first.event_cursor)
    subscription.close()
    release.set()
    assert manager.wait("turn-1", timeout=1.0)
    assert calls == 1
    assert manager.status("turn-1")["state"] == "completed"
    manager.close()


def test_snapshot_cursor_and_subscription_have_no_gap() -> None:
    manager = RuntimeExecutionManager(run_workers=1)
    release = threading.Event()

    def run(sink, _control, _budget):
        sink.emit(AssistantDeltaEvent(delta="one"))
        release.wait(1.0)
        sink.emit(AssistantDeltaEvent(delta="two"))
        return MessageOutcome("done", conversation_id=3)

    manager.submit(
        turn_id="turn-2",
        conversation_id=3,
        generation=1,
        operation=run,
    )
    snapshot, subscription = manager.snapshot_and_subscribe("turn-2")
    assert snapshot.high_watermark >= 1
    release.set()
    frames = []
    while True:
        frame = subscription.get(timeout=1.0)
        frames.append(frame)
        if getattr(frame, "kind", None) == "completed":
            break
    assert any(getattr(frame, "kind", None) == "subscribed" for frame in frames)
    assert any(getattr(frame, "data", {}).get("delta") == "two" for frame in frames)
    subscription.close()
    manager.close()


def test_slow_subscriber_overflow_requires_resync_and_does_not_block_worker() -> None:
    manager = RuntimeExecutionManager(
        run_workers=1,
        subscriber_max_events=1,
        ring_max_events=4,
    )
    started = threading.Event()
    release = threading.Event()

    def run(sink, _control, _budget):
        started.set()
        release.wait(1.0)
        for value in ("one", "two", "three"):
            sink.emit(AssistantDeltaEvent(delta=value))
        return MessageOutcome("done", conversation_id=4)

    manager.submit(turn_id="turn-3", conversation_id=4, generation=1, operation=run)
    assert started.wait(1.0)
    subscription = manager.subscribe("turn-3")
    release.set()
    assert manager.wait("turn-3", timeout=1.0)
    frames = []
    while True:
        try:
            frames.append(subscription.get(timeout=0.05))
        except TimeoutError:
            break
        except StopIteration:
            break
    assert any(getattr(frame, "kind", None) == "resync_required" for frame in frames)
    subscription.close()
    manager.close()


def test_event_budget_detaches_readers_but_does_not_abort_business_execution() -> None:
    manager = RuntimeExecutionManager(run_workers=1, subscriber_max_events=16)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def run(sink, _control, _budget):
        started.set()
        release.wait(1.0)
        for value in ("one", "two", "three"):
            sink.emit(AssistantDeltaEvent(delta=value))
        finished.set()
        return MessageOutcome("done", conversation_id=8)

    manager.submit(
        turn_id="event-budget",
        conversation_id=8,
        generation=1,
        operation=run,
        max_events=2,
    )
    assert started.wait(1.0)
    subscription = manager.subscribe("event-budget", generation=1)
    release.set()
    assert finished.wait(1.0)
    assert manager.wait("event-budget", timeout=1.0)
    assert manager.status("event-budget")["state"] == "completed"
    frames = []
    while True:
        try:
            frames.append(subscription.get(timeout=0.05))
        except (TimeoutError, StopIteration):
            break
    assert any(getattr(frame, "kind", None) == "resync_required" for frame in frames)
    subscription.close()
    manager.close()


def test_queue_deadline_expires_without_starting_operation() -> None:
    manager = RuntimeExecutionManager(run_workers=1, max_queue=2)
    blocker = threading.Event()
    started = threading.Event()
    calls = 0

    def blocking(_sink, _control, _budget):
        blocker.wait(1.0)
        return MessageOutcome("block", conversation_id=1)

    manager.submit(turn_id="block", conversation_id=1, generation=1, operation=blocking)
    assert manager.status("block")["state"] in {"queued", "running"}

    def queued(_sink, _control, _budget):
        nonlocal calls
        calls += 1
        started.set()
        return MessageOutcome("should not run", conversation_id=2)

    manager.submit(
        turn_id="expired",
        conversation_id=2,
        generation=1,
        operation=queued,
        timeout_seconds=0.02,
    )
    time.sleep(0.08)
    assert manager.status("expired")["state"] == "failed"
    assert calls == 0
    blocker.set()
    manager.wait("block", timeout=1.0)
    manager.close()


def test_expired_queue_entry_keeps_capacity_until_physical_dequeue() -> None:
    manager = RuntimeExecutionManager(run_workers=1, max_queue=1)
    blocker = threading.Event()
    started = threading.Event()

    def blocking(_sink, _control, _budget):
        started.set()
        blocker.wait(1.0)
        return MessageOutcome("block", conversation_id=1)

    manager.submit(turn_id="queue-block", conversation_id=1, generation=1, operation=blocking)
    assert started.wait(1.0)
    queued = manager.submit(
        turn_id="queue-expired",
        conversation_id=2,
        generation=1,
        operation=lambda *_: MessageOutcome("expired", conversation_id=2),
        timeout_seconds=0.02,
    )
    time.sleep(0.08)
    assert manager.status(queued.turn_id)["state"] == "failed"
    # The expired entry is still physically in the fixed worker queue.  It
    # cannot be replaced until that worker dequeues and discards it.
    assert manager.queued_count == 1
    with pytest.raises(RuntimeCapacityExhausted):
        manager.submit(
            turn_id="queue-third",
            conversation_id=3,
            generation=1,
            operation=lambda *_: MessageOutcome("third", conversation_id=3),
        )
    blocker.set()
    assert manager.wait("queue-block", timeout=1.0)
    assert manager.wait("queue-expired", timeout=1.0)
    assert manager.queued_count == 0
    manager.close()


def test_timed_out_physical_worker_keeps_pool_bounded_for_new_submissions() -> None:
    """Logical timeout must not make an in-flight provider worker reusable."""

    manager = RuntimeExecutionManager(run_workers=1, max_queue=2)
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def blocked(_sink, _control, _budget):
        nonlocal calls
        calls += 1
        entered.set()
        release.wait(1.0)
        return MessageOutcome("late", conversation_id=11)

    first = manager.submit(
        turn_id="physical-blocked",
        conversation_id=11,
        generation=1,
        operation=blocked,
        timeout_seconds=0.02,
    )
    assert entered.wait(1.0)

    # The watchdog can mark the run unknown, but the provider call still owns
    # the sole physical worker.  At most the finite waiting queue is admitted;
    # no replacement provider thread may be created for the third request.
    deadline = time.monotonic() + 1.0
    while manager.status(first.turn_id)["state"] != "result_unknown":
        if time.monotonic() >= deadline:
            raise AssertionError("timed-out worker did not converge")
        time.sleep(0.01)
    manager.submit(
        turn_id="physical-queued-1",
        conversation_id=12,
        generation=1,
        operation=lambda *_: MessageOutcome("queued", conversation_id=12),
    )
    manager.submit(
        turn_id="physical-queued-2",
        conversation_id=13,
        generation=1,
        operation=lambda *_: MessageOutcome("queued", conversation_id=13),
    )
    with pytest.raises(RuntimeCapacityExhausted):
        manager.submit(
            turn_id="physical-overflow",
            conversation_id=14,
            generation=1,
            operation=lambda *_: MessageOutcome("overflow", conversation_id=14),
        )
    assert calls == 1
    assert manager.active_worker_count == 3

    release.set()
    assert manager.wait(first.turn_id, timeout=1.0)
    assert manager.wait("physical-queued-1", timeout=1.0)
    assert manager.wait("physical-queued-2", timeout=1.0)
    manager.close()


def test_fresh_snapshot_recovers_after_ring_reclamation_and_hwm_subscribes() -> None:
    manager = RuntimeExecutionManager(run_workers=1, ring_max_events=2)

    def run(sink, _control, _budget):
        for value in ("one", "two", "three", "four"):
            sink.emit(AssistantDeltaEvent(delta=value))
        return MessageOutcome("done", conversation_id=6)

    manager.submit(turn_id="ring-turn", conversation_id=6, generation=1, operation=run)
    assert manager.wait("ring-turn", timeout=1.0)

    snapshot = manager.snapshot("ring-turn")
    assert snapshot.progress_gap is True
    assert snapshot.history_truncated is True
    assert snapshot.high_watermark >= snapshot.events[-1].event_seq
    # The fresh snapshot's signed high watermark is a valid continuation
    # cursor even though an older incremental cursor would require a resync.
    subscription = manager.subscribe("ring-turn", after=snapshot.snapshot_cursor)
    assert subscription.get(timeout=1.0).kind == "subscribed"
    subscription.close()
    manager.close()


def test_cursor_from_another_epoch_requires_resync() -> None:
    first = RuntimeExecutionManager(run_workers=1)
    first.submit(
        turn_id="turn-4",
        conversation_id=5,
        generation=1,
        operation=lambda *_: MessageOutcome("done", conversation_id=5),
    )
    assert first.wait("turn-4", timeout=1.0)
    cursor = first.status("turn-4")["event_cursor"]
    first.close()

    second = RuntimeExecutionManager(run_workers=1)
    second.submit(
        turn_id="turn-4",
        conversation_id=5,
        generation=1,
        operation=lambda *_: MessageOutcome("done", conversation_id=5),
    )
    with pytest.raises(RuntimeResyncRequired):
        second.subscribe("turn-4", after=cursor)
    second.close()


def test_conflicting_request_identity_is_rejected() -> None:
    manager = RuntimeExecutionManager(run_workers=1)
    manager.submit(
        turn_id="turn-5",
        conversation_id=1,
        generation=1,
        request_id="request-5",
        operation=lambda *_: MessageOutcome("done", conversation_id=1),
    )
    with pytest.raises(RuntimeSubmissionConflict):
        manager.submit(
            turn_id="different",
            conversation_id=1,
            generation=1,
            request_id="request-5",
            operation=lambda *_: MessageOutcome("other", conversation_id=1),
        )
    manager.close()
