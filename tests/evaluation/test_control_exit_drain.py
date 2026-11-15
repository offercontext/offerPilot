"""Process exit must not outrun a still-draining, ACKed control reader."""
import queue
import threading
import time
from types import SimpleNamespace

import pytest

from offerpilot.evaluation.adapters import DriverError
from offerpilot.evaluation.core_subject import CoreSubject
from offerpilot.evaluation.runner import Subject


@pytest.mark.parametrize("subject_type", [Subject, CoreSubject])
def test_shutdown_receipt_published_after_exit_poll_is_consumed_within_original_deadline(subject_type):
    permit = threading.Event()
    ready = threading.Event()

    class FirstEmptyQueue(queue.Queue):
        first = True

        def get(self, *args, **kwargs):
            if self.first:
                self.first = False
                raise queue.Empty
            return super().get(*args, **kwargs)

    subject = object.__new__(subject_type)
    subject.messages = FirstEmptyQueue()
    subject.deadline = time.monotonic() + 1
    subject.process = SimpleNamespace(poll=lambda: (permit.set(), 0)[1])
    receipt = dict(type="shutdown_complete", seq=343)

    def publish():
        ready.set()
        assert permit.wait(1)
        subject.messages.put(receipt)

    subject.reader = threading.Thread(target=publish)
    subject.reader.start()
    assert ready.wait(1)
    try:
        assert subject.wait("shutdown_complete", .5) == receipt
    finally:
        permit.set()
        subject.reader.join(1)
    assert not subject.reader.is_alive()


@pytest.mark.parametrize("subject_type", [Subject, CoreSubject])
def test_exited_reader_without_receipt_still_fails(subject_type):
    subject = object.__new__(subject_type)
    subject.messages = queue.Queue()
    subject.deadline = time.monotonic() + 1
    subject.process = SimpleNamespace(poll=lambda: 0)
    subject.reader = SimpleNamespace(is_alive=lambda: False)
    with pytest.raises(DriverError, match="exited before control event"):
        subject.wait("shutdown_complete", .2)


@pytest.mark.parametrize("subject_type", [Subject, CoreSubject])
def test_stalled_reader_cannot_extend_original_wait_budget(subject_type):
    subject = object.__new__(subject_type)
    subject.messages = queue.Queue()
    subject.deadline = time.monotonic() + 1
    subject.process = SimpleNamespace(poll=lambda: 0)
    subject.reader = SimpleNamespace(is_alive=lambda: True)
    start = time.monotonic()
    with pytest.raises(TimeoutError, match="did not produce"):
        subject.wait("shutdown_complete", .05)
    assert time.monotonic() - start < .3


@pytest.mark.parametrize("subject_type", [Subject, CoreSubject])
@pytest.mark.parametrize("poll_after_empty", [False, True])
def test_receipt_observed_after_original_deadline_is_rejected(subject_type, poll_after_empty, monkeypatch):
    from offerpilot.evaluation import runner
    clock = [100.0]
    receipt = dict(type="shutdown_complete", seq=343)

    class LateQueue:
        def get(self, **kwargs):
            clock[0] = 101.0
            if poll_after_empty:
                raise queue.Empty
            return receipt

        def get_nowait(self):
            return receipt

    subject = object.__new__(subject_type)
    subject.messages = LateQueue()
    subject.deadline = 110
    subject.process = SimpleNamespace(poll=lambda: 0)
    subject.reader = SimpleNamespace(is_alive=lambda: False)
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    with pytest.raises(TimeoutError, match="deadline"):
        subject.wait("shutdown_complete", .5)


@pytest.mark.parametrize("subject_type", [Subject, CoreSubject])
def test_poll_timeout_preserves_dequeued_event_before_later_frames(subject_type, monkeypatch):
    from offerpilot.evaluation import runner
    clock = [100.0]
    fault = dict(type="fault_reached", seq=2)
    later = dict(type="ready", seq=3)

    class RescheduledQueue(queue.Queue):
        def get(self, *args, **kwargs):
            value = super().get(*args, **kwargs)
            clock[0] += .3
            return value

    subject = object.__new__(subject_type)
    subject.messages = RescheduledQueue()
    subject.messages.put(fault)
    subject.messages.put(later)
    subject.deadline = 110
    subject.process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    with pytest.raises(TimeoutError, match="deadline"):
        subject.wait("fault_reached", .2)
    assert subject.wait("fault_reached", .2) is fault
    assert subject.wait("ready", .5) is later
    assert subject.messages.empty()


@pytest.mark.parametrize("subject_type", [Subject, CoreSubject])
def test_deferred_event_never_extends_trial_deadline(subject_type, monkeypatch):
    from offerpilot.evaluation import runner
    clock = [100.0]
    subject = object.__new__(subject_type)
    subject.messages = queue.Queue()
    subject._deferred_event = dict(type="fault_reached", seq=2)
    subject.deadline = 100.0
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    with pytest.raises(TimeoutError):
        subject.wait("fault_reached", 10)
    assert subject._deferred_event["type"] == "fault_reached"


@pytest.mark.parametrize("subject_type", [Subject, CoreSubject])
def test_poll_timeout_preserves_error_for_next_wait(subject_type, monkeypatch):
    from offerpilot.evaluation import runner
    clock = [100.0]
    error = dict(type="error", message="control rejected", error_type="ValueError")

    class RescheduledQueue(queue.Queue):
        def get(self, *args, **kwargs):
            value = super().get(*args, **kwargs)
            clock[0] += .3
            return value

    subject = object.__new__(subject_type)
    subject.messages = RescheduledQueue()
    subject.messages.put(error)
    subject.deadline = 110
    subject.process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    with pytest.raises(TimeoutError, match="deadline"):
        subject.wait("fault_reached", .2)
    with pytest.raises(DriverError, match="control rejected"):
        subject.wait("fault_reached", .2)
    assert subject._deferred_event is None
