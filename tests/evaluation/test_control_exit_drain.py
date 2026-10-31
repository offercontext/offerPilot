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
