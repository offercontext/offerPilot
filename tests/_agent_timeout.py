"""Drive the real Agent host deadline after a test's blocked work has entered."""

from threading import Event

import offerpilot.chat_transport as transport


def timeout_after_signal(monkeypatch, entered: Event) -> Event:
    """Expire one real Future wait, and report when its whole worker exits.

    The synchronization limit only detects a broken fixture. The Agent deadline
    itself is a zero-duration Future wait after the required business boundary.
    Untimed SSE pump waits retain their normal behavior.
    """

    worker_done = Event()
    executor_type = transport.ThreadPoolExecutor
    deadline_waits = []

    class SignalExecutor(executor_type):
        def submit(self, fn, /, *args, **kwargs):
            future = super().submit(fn, *args, **kwargs)
            original_result = future.result

            def result(timeout=None):
                if timeout is None:
                    return original_result(timeout=timeout)
                assert not deadline_waits, "Expected exactly one Agent deadline wait"
                deadline_waits.append(future)
                future.add_done_callback(lambda _future: worker_done.set())
                assert entered.wait(20), "Agent work did not reach the timeout probe"
                assert not future.done(), "Agent work must remain blocked at timeout"
                return original_result(timeout=0)

            future.result = result
            return future

    monkeypatch.setattr(transport, "ThreadPoolExecutor", SignalExecutor)
    return worker_done
