"""HTTP-facing helpers for the detached Pilot Runtime.

The execution manager deliberately has no FastAPI dependency.  This module is
the small adapter at the other side of that boundary: it turns a read-only
``RuntimeSubscription`` into an SSE response and provides the direct host used
by a manager worker.  Admission, persistence and authorization stay in the
composition root so this module cannot accidentally invent a second execution
or bypass the durable Pilot control fence.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

from starlette.responses import StreamingResponse

from offerpilot.pilot_runtime.contracts import (
    CancelReason,
    RuntimeInvocationControl,
)
from offerpilot.pilot_runtime.errors import RuntimeAgentTimedOut, RuntimeCancelled, RuntimeTransportAborted
from offerpilot.pilot_runtime.managed_execution import (
    RuntimeControlFrame,
    RuntimeEventEnvelope,
    RuntimeStreamFrame,
    RuntimeSubscription,
)


class DirectRuntimeExecutionHost:
    """Run one Pilot thunk on the manager's already bounded worker.

    ``SyncAgentExecutionHost`` is intentionally not used here: it creates a
    second executor per request, which would make a detached runtime's
    physical worker limit meaningless.  The manager worker is the only host
    thread.  A late provider result is checked against the shared control when
    it returns, so it cannot cross the P3A persistence fence.
    """

    __slots__ = ()

    def run(
        self,
        thunk: Any,
        invocation_control: RuntimeInvocationControl,
    ) -> Any:
        if not callable(thunk):
            raise TypeError("thunk must be callable")
        _require_active(invocation_control)
        try:
            result = thunk()
        except (RuntimeCancelled, RuntimeTransportAborted, RuntimeAgentTimedOut):
            raise
        if not invocation_control.is_active():
            _raise_control(invocation_control)
        return result


def _require_active(control: RuntimeInvocationControl) -> None:
    if control.is_active():
        return
    _raise_control(control)


def _raise_control(control: RuntimeInvocationControl) -> None:
    state = control.state
    if getattr(state, "value", state) == "timed_out":
        raise RuntimeAgentTimedOut()
    if getattr(state, "value", state) == "cancelled":
        if control.cancel_reason is CancelReason.TRANSPORT_ABORTED:
            raise RuntimeTransportAborted()
        raise RuntimeCancelled(control.cancel_reason)
    raise RuntimeTransportAborted()


def encode_runtime_sse(frame: RuntimeStreamFrame) -> str:
    """Encode a manager frame as one bounded SSE data frame.

    The JSON body carries the complete identity and sequence information.  A
    named SSE event is intentionally omitted because the frontend parser uses
    the versioned ``event``/``kind`` field and this keeps control frames and
    typed runtime events on one wire shape.
    """

    if not isinstance(frame, (RuntimeEventEnvelope, RuntimeControlFrame)):
        raise TypeError("frame must be a RuntimeStreamFrame")
    return f"data: {json.dumps(frame.as_mapping(), ensure_ascii=False, separators=(',', ':'))}\n\n"


def runtime_subscription_response(
    subscription: RuntimeSubscription,
    *,
    heartbeat_seconds: float = 15.0,
    can_read: Callable[[], bool] | None = None,
) -> StreamingResponse:
    """Return a read-only SSE view whose disconnect never cancels the run."""

    if not isinstance(subscription, RuntimeSubscription):
        raise TypeError("subscription must be a RuntimeSubscription")
    if type(heartbeat_seconds) not in {int, float} or isinstance(heartbeat_seconds, bool):
        raise TypeError("heartbeat_seconds must be a number")
    heartbeat = float(heartbeat_seconds)
    if heartbeat <= 0:
        raise ValueError("heartbeat_seconds must be positive")

    def stream() -> Iterator[str]:
        try:
            while True:
                if can_read is not None and not can_read():
                    return
                try:
                    frame = subscription.get(timeout=heartbeat)
                except TimeoutError:
                    # A heartbeat is a control frame, not a Runtime event, so
                    # it does not consume the event budget or sequence.
                    try:
                        frame = subscription.heartbeat()
                    except RuntimeError:
                        return
                except StopIteration:
                    return
                if can_read is not None and not can_read():
                    return
                yield encode_runtime_sse(frame)
                if isinstance(frame, RuntimeControlFrame) and frame.kind == "completed":
                    return
                if isinstance(frame, RuntimeControlFrame) and frame.kind == "resync_required":
                    return
        finally:
            # Closing only detaches this reader.  RuntimeSubscription.close
            # never calls request_cancel on the manager's invocation control.
            subscription.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = [
    "DirectRuntimeExecutionHost",
    "encode_runtime_sse",
    "runtime_subscription_response",
]
