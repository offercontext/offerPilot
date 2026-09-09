"""In-process owner for detached Runtime executions.

The manager deliberately sits below HTTP.  It owns the worker lifetime,
deadline, bounded event history, and read-only subscribers.  A subscriber is
only a view of a run: closing one never requests cancellation from the worker.
The application can provide a durable status/snapshot reader around this
small in-process core when it needs to recover facts after a service restart.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
import json
import math
import secrets
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Condition, Event, Lock, RLock, Thread
from time import monotonic
from types import MappingProxyType
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from .contracts import (
    CancelReason,
    CompletedEvent,
    ConfirmationRequiredOutcome,
    ErrorEvent,
    MessageOutcome,
    OperationPendingOutcome,
    OperationReplayOutcome,
    RuntimeEvent,
    RuntimeEventSink,
    RuntimeFailureOutcome,
    RuntimeInvocationControl,
    StatusEvent,
)
from .errors import RuntimeAgentTimedOut, RuntimeCancelled, RuntimeTransportAborted
from .event_sink import InMemoryRuntimeInvocationControl, runtime_event_payload
from .execution_budget import (
    RuntimeBudget,
    RuntimeBudgetError,
    RuntimeBudgetExceeded,
    RuntimeClockInvalid,
    RuntimeDeadlineExceeded,
)


RUNTIME_PROTOCOL_VERSION = "pilot-runtime-v1"
RUNTIME_STREAM_VERSION = RUNTIME_PROTOCOL_VERSION
_SAFE_TIMEOUT_MESSAGE = "任务已达到运行期限，结果待核对。"
_SAFE_BUDGET_MESSAGE = "任务已达到运行预算，结果待核对。"
_SAFE_STOP_MESSAGE = "任务已停止。"
_SAFE_SHUTDOWN_MESSAGE = "运行服务正在停止，结果待核对。"
_SAFE_FAILURE_MESSAGE = "任务执行失败，请读取任务状态。"

RuntimeExecutionState = Literal[
    "accepted",
    "queued",
    "running",
    "waiting_confirmation",
    "completed",
    "failed",
    "interrupted",
    "stopped",
    "result_unknown",
]


class RuntimeManagerError(RuntimeError):
    """Base class for manager admission and observation failures."""


class RuntimeCapacityExhausted(RuntimeManagerError):
    """The bounded waiting queue is full."""

    def __init__(self, retry_after_seconds: int = 1) -> None:
        self.retry_after_seconds = max(0, int(retry_after_seconds))
        super().__init__("runtime execution capacity exhausted")


class RuntimeSubmissionConflict(RuntimeManagerError):
    """An idempotency key or execution identity was reused incompatibly."""


class RuntimeTurnNotFound(RuntimeManagerError):
    """The manager has no in-process handle for a requested turn."""


class RuntimeResyncRequired(RuntimeManagerError):
    """A cursor cannot be replayed from the bounded in-memory history."""

    error_code = "runtime_resync_required"


class RuntimeCursorInvalid(RuntimeResyncRequired):
    """A cursor is malformed, unsigned, or belongs to another run/epoch."""


class RuntimeOperation(Protocol):
    def __call__(
        self,
        sink: RuntimeEventSink,
        invocation_control: RuntimeInvocationControl,
        budget: RuntimeBudget,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class RuntimeEventEnvelope:
    """One bounded, JSON-safe Runtime event with a monotonic per-run id."""

    stream_version: str
    turn_id: str
    conversation_id: int
    execution_generation: int
    event_seq: int
    timestamp: float
    kind: str
    data: Mapping[str, object]
    cursor: str
    durable: bool = False

    def as_mapping(self) -> dict[str, object]:
        return {
            "stream_version": self.stream_version,
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "execution_generation": self.execution_generation,
            "event_seq": self.event_seq,
            "seq": self.event_seq,
            "ts": self.timestamp,
            "event": self.kind,
            "data": _plain(self.data),
            "cursor": self.cursor,
            "durable": self.durable,
        }


@dataclass(frozen=True, slots=True)
class RuntimeControlFrame:
    """A subscription lifecycle frame, separate from Runtime typed events."""

    kind: Literal["subscribed", "heartbeat", "resync_required", "completed"]
    turn_id: str
    conversation_id: int
    execution_generation: int
    event_seq: int
    cursor: str
    data: Mapping[str, object] = field(default_factory=dict)
    stream_version: str = RUNTIME_STREAM_VERSION

    def as_mapping(self) -> dict[str, object]:
        return {
            "stream_version": self.stream_version,
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "execution_generation": self.execution_generation,
            "event_seq": self.event_seq,
            "seq": self.event_seq,
            "event": self.kind,
            "kind": self.kind,
            "cursor": self.cursor,
            "data": _plain(self.data),
        }


RuntimeStreamFrame = RuntimeEventEnvelope | RuntimeControlFrame


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """Consistent bounded read of durable facts plus current transient events."""

    stream_version: str
    turn_id: str
    conversation_id: int
    execution_generation: int
    state: RuntimeExecutionState
    events: tuple[RuntimeEventEnvelope, ...]
    durable: Mapping[str, object]
    snapshot_cursor: str
    high_watermark: int
    next_cursor: str | None
    runtime_epoch: str
    budget: Mapping[str, object]
    # A fresh snapshot may start at the retained ring tail after transient
    # history has been reclaimed.  The caller can render the durable facts and
    # continue from ``snapshot_cursor`` while knowing that older progress is
    # unavailable.  An explicit stale incremental cursor still raises
    # RuntimeResyncRequired.
    history_truncated: bool = False
    progress_gap: bool = False

    def as_mapping(self) -> dict[str, object]:
        return {
            "stream_version": self.stream_version,
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "generation": self.execution_generation,
            "execution_generation": self.execution_generation,
            "state": self.state,
            "events": [event.as_mapping() for event in self.events],
            "durable": _plain(self.durable),
            "snapshot_cursor": self.snapshot_cursor,
            "high_watermark": self.high_watermark,
            "next_cursor": self.next_cursor,
            "runtime_epoch": self.runtime_epoch,
            "budget": _plain(self.budget),
            "history_truncated": self.history_truncated,
            "progress_gap": self.progress_gap,
        }


@dataclass(frozen=True, slots=True)
class RuntimeSubmission:
    """Admission result returned before or during worker execution."""

    protocol_version: str
    request_id: str | None
    turn_id: str
    conversation_id: int
    execution_generation: int
    state: RuntimeExecutionState
    accepted_at: float
    deadline_at: float
    snapshot_cursor: str
    event_cursor: str
    runtime_epoch: str
    replayed: bool = False

    def as_mapping(self, *, snapshot_url: str | None = None, events_url: str | None = None) -> dict[str, object]:
        subscription: dict[str, object] = {}
        if snapshot_url is not None:
            subscription["snapshot_url"] = snapshot_url
        if events_url is not None:
            subscription["events_url"] = events_url
        return {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "execution_generation": self.execution_generation,
            "state": self.state,
            "accepted_at": self.accepted_at,
            "deadline_at": self.deadline_at,
            "snapshot_cursor": self.snapshot_cursor,
            "event_cursor": self.event_cursor,
            "runtime_epoch": self.runtime_epoch,
            "replayed": self.replayed,
            "subscription": subscription,
        }


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    """Observation result with no worker ownership material."""

    protocol_version: str
    request_id: str | None
    turn_id: str
    conversation_id: int
    execution_generation: int
    state: RuntimeExecutionState
    accepted_at: float
    deadline_at: float
    event_cursor: str
    snapshot_cursor: str
    runtime_epoch: str
    worker_done: bool
    actual_worker_alive: bool
    outcome: object | None
    budget: Mapping[str, object]

    def as_mapping(self) -> dict[str, object]:
        outcome = _outcome_payload(self.outcome) if self.outcome is not None else None
        return {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "execution_generation": self.execution_generation,
            "state": self.state,
            "accepted_at": self.accepted_at,
            "deadline_at": self.deadline_at,
            "event_cursor": self.event_cursor,
            "snapshot_cursor": self.snapshot_cursor,
            "runtime_epoch": self.runtime_epoch,
            "worker_done": self.worker_done,
            "actual_worker_alive": self.actual_worker_alive,
            "outcome": outcome,
            "budget": _plain(self.budget),
        }


class RuntimeSubscription:
    """Bounded read-only stream view over one manager handle."""

    __slots__ = (
        "_manager",
        "_key",
        "_max_events",
        "_max_bytes",
        "_condition",
        "_frames",
        "_queued_bytes",
        "_closed",
        "_finish_after_queue",
        "_overflowed",
    )

    def __init__(
        self,
        manager: RuntimeExecutionManager,
        key: tuple[str, int],
        *,
        max_events: int,
        max_bytes: int,
    ) -> None:
        self._manager = manager
        self._key = key
        self._max_events = max_events
        self._max_bytes = max_bytes
        self._condition = Condition(RLock())
        self._frames: deque[RuntimeStreamFrame] = deque()
        self._queued_bytes = 0
        self._closed = False
        self._finish_after_queue = False
        self._overflowed = False

    @staticmethod
    def _frame_size(frame: RuntimeStreamFrame) -> int:
        try:
            if isinstance(frame, RuntimeEventEnvelope):
                value: object = frame.as_mapping()
            else:
                value = frame.as_mapping()
            return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        except (TypeError, ValueError):
            return 0

    def _push(self, frame: RuntimeStreamFrame) -> bool:
        size = self._frame_size(frame)
        with self._condition:
            if self._closed:
                return False
            if len(self._frames) >= self._max_events or self._queued_bytes + size > self._max_bytes:
                self._frames.clear()
                self._queued_bytes = 0
                self._overflowed = True
                # A resync marker is intentionally the only frame retained on
                # overflow.  It gives the client a deterministic recovery path
                # while keeping producer work non-blocking.
                marker = (
                    frame
                    if isinstance(frame, RuntimeControlFrame) and frame.kind == "resync_required"
                    else RuntimeControlFrame(
                        kind="resync_required",
                        turn_id=(
                            frame.turn_id
                            if isinstance(frame, (RuntimeEventEnvelope, RuntimeControlFrame))
                            else self._key[0]
                        ),
                        conversation_id=(
                            frame.conversation_id
                            if isinstance(frame, (RuntimeEventEnvelope, RuntimeControlFrame))
                            else 0
                        ),
                        execution_generation=(
                            frame.execution_generation
                            if isinstance(frame, (RuntimeEventEnvelope, RuntimeControlFrame))
                            else self._key[1]
                        ),
                        event_seq=(
                            frame.event_seq
                            if isinstance(frame, (RuntimeEventEnvelope, RuntimeControlFrame))
                            else 0
                        ),
                        cursor=(
                            frame.cursor
                            if isinstance(frame, (RuntimeEventEnvelope, RuntimeControlFrame))
                            else ""
                        ),
                        data=MappingProxyType({"error_code": RuntimeResyncRequired.error_code}),
                    )
                )
                marker_size = self._frame_size(marker)
                if marker_size <= self._max_bytes:
                    self._frames.append(marker)
                    self._queued_bytes = marker_size
                self._finish_after_queue = True
                self._condition.notify_all()
                return False
            self._frames.append(frame)
            self._queued_bytes += size
            self._condition.notify_all()
            return True

    def _finish(self) -> None:
        with self._condition:
            self._finish_after_queue = True
            self._condition.notify_all()

    def _force_resync(self, frame: RuntimeControlFrame) -> None:
        """Drop queued progress and retain one bounded recovery marker."""

        with self._condition:
            if self._closed:
                return
            self._frames.clear()
            self._queued_bytes = 0
            self._overflowed = True
            size = self._frame_size(frame)
            if size <= self._max_bytes:
                self._frames.append(frame)
                self._queued_bytes = size
            self._finish_after_queue = True
            self._condition.notify_all()

    def get(self, timeout: float | None = None) -> RuntimeStreamFrame:
        if timeout is not None:
            if type(timeout) not in {int, float} or isinstance(timeout, bool) or timeout < 0:
                raise ValueError("timeout must be a non-negative number")
            timeout_value: float | None = float(timeout)
        else:
            timeout_value = None
        with self._condition:
            deadline = None if timeout_value is None else monotonic() + timeout_value
            while not self._frames:
                if self._closed or self._finish_after_queue:
                    self._closed = True
                    raise StopIteration
                remaining = None if deadline is None else deadline - monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("runtime subscription timed out")
                self._condition.wait(remaining)
            frame = self._frames.popleft()
            self._queued_bytes = max(0, self._queued_bytes - self._frame_size(frame))
            if self._finish_after_queue and not self._frames:
                self._closed = True
            return frame

    def __iter__(self) -> Iterator[RuntimeStreamFrame]:
        while True:
            try:
                yield self.get()
            except StopIteration:
                return

    def close(self) -> None:
        self._manager._unsubscribe(self)

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def heartbeat(self) -> RuntimeControlFrame:
        """Return a current control frame without consuming event history."""

        return self._manager.heartbeat(self._key[0], generation=self._key[1])


@dataclass
class _ManagedRun:
    request_id: str | None
    turn_id: str
    conversation_id: int
    generation: int
    operation: RuntimeOperation | None
    budget: RuntimeBudget
    accepted_at: float
    invocation_control: RuntimeInvocationControl
    title_action: Callable[[], object] | None
    title_fallback: Callable[[], object] | None
    key: tuple[str, int] = field(init=False)
    state: RuntimeExecutionState = "accepted"
    outcome: object | None = None
    worker_done: bool = False
    timeout_requested: bool = False
    stop_requested: bool = False
    logical_terminal: bool = False
    terminal_event_emitted: bool = False
    budget_failure_emitted: bool = False
    next_seq: int = 0
    history: deque[RuntimeEventEnvelope] = field(default_factory=deque)
    history_bytes: int = 0
    history_truncated: bool = False
    history_floor: int = 0
    event_budget_truncated: bool = False
    subscribers: set[RuntimeSubscription] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.key = (self.turn_id, self.generation)


class RuntimeExecutionManager:
    """Own bounded detached execution workers and their read-only subscribers."""

    def __init__(
        self,
        *,
        run_workers: int = 2,
        max_queue: int = 32,
        ring_max_events: int = 256,
        ring_max_bytes: int = 1_048_576,
        subscriber_max_events: int = 128,
        subscriber_max_bytes: int = 512_000,
        default_timeout_seconds: float = 120.0,
        default_max_model_calls: int = 20,
        default_max_title_model_calls: int = 1,
        default_max_tool_calls: int = 40,
        default_max_events: int = 512,
        default_max_event_bytes: int = 1_048_576,
        max_retained_runs: int = 256,
        clock: Callable[[], float] = monotonic,
        cursor_secret: bytes | str | None = None,
        durable_snapshot_loader: Callable[[str, int], Mapping[str, object]] | None = None,
        startup_reconciler: Callable[[], object] | None = None,
    ) -> None:
        for name, value in (
            ("run_workers", run_workers),
            ("max_queue", max_queue),
            ("ring_max_events", ring_max_events),
            ("ring_max_bytes", ring_max_bytes),
            ("subscriber_max_events", subscriber_max_events),
            ("subscriber_max_bytes", subscriber_max_bytes),
            ("max_retained_runs", max_retained_runs),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if type(default_timeout_seconds) not in {int, float} or isinstance(default_timeout_seconds, bool):
            raise TypeError("default_timeout_seconds must be a number")
        if not math.isfinite(float(default_timeout_seconds)) or default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be finite and positive")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if cursor_secret is None:
            secret = secrets.token_bytes(32)
        elif isinstance(cursor_secret, bytes):
            secret = cursor_secret
        elif isinstance(cursor_secret, str):
            secret = cursor_secret.encode("utf-8")
        else:
            raise TypeError("cursor_secret must be bytes or string")
        if len(secret) < 16:
            raise ValueError("cursor_secret must contain at least 16 bytes")
        self.run_workers = run_workers
        self.max_queue = max_queue
        self.ring_max_events = ring_max_events
        self.ring_max_bytes = ring_max_bytes
        self.subscriber_max_events = subscriber_max_events
        self.subscriber_max_bytes = subscriber_max_bytes
        self.default_timeout_seconds = float(default_timeout_seconds)
        self.default_max_model_calls = default_max_model_calls
        self.default_max_title_model_calls = default_max_title_model_calls
        self.default_max_tool_calls = default_max_tool_calls
        self.default_max_events = default_max_events
        self.default_max_event_bytes = default_max_event_bytes
        self.max_retained_runs = max_retained_runs
        self.clock = clock
        self.runtime_epoch = uuid4().hex
        self._cursor_secret = secret
        self._durable_snapshot_loader = durable_snapshot_loader
        self._startup_reconciler = startup_reconciler
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._runs: dict[tuple[str, int], _ManagedRun] = {}
        self._request_index: dict[str, tuple[str, int]] = {}
        self._queued_count = 0
        self._admission_reservations: set[object] = set()
        self._admission_gate = Lock()
        self._closed = False
        self._watchdog_stop = Event()
        self._work_queue: deque[_ManagedRun] = deque()
        self._workers: tuple[Thread, ...] = ()
        self._watchdog: Thread | None = None
        self._started = False
        if startup_reconciler is not None:
            startup_reconciler()

    def _ensure_started_locked(self) -> None:
        """Start the fixed pool exactly once, after the first real submit.

        Constructing an application creates a manager even when no detached
        run will ever be submitted (for example, a short-lived health check or
        a test that does not enter the application lifespan).  Keep those
        managers thread-free while retaining the same fixed worker capacity
        once work is admitted.

        The caller holds ``_condition``.  Workers therefore cannot dequeue a
        run until the caller has finished publishing it and released the lock.
        """

        if self._started:
            return
        if self._closed:
            raise RuntimeManagerError("runtime manager is shut down")
        try:
            watchdog = Thread(
                target=self._watchdog_loop,
                name="offerpilot-runtime-watchdog",
                daemon=True,
            )
            workers = tuple(
                Thread(
                    target=self._worker_loop,
                    name=f"offerpilot-runtime-worker-{index + 1}",
                    daemon=True,
                )
                for index in range(self.run_workers)
            )
            self._watchdog = watchdog
            self._workers = workers
            self._started = True
            for worker in self._workers:
                worker.start()
            watchdog.start()
        except BaseException:
            # A thread-start failure must leave the manager closed and must
            # not strand already-started workers waiting on an empty queue.
            self._closed = True
            self._watchdog_stop.set()
            self._condition.notify_all()
            raise

    def _now(self) -> float:
        try:
            value = float(self.clock())
        except (BaseException,) as exc:
            raise RuntimeClockInvalid("runtime clock is unavailable") from exc
        if not math.isfinite(value):
            raise RuntimeClockInvalid("runtime clock returned a non-finite value")
        return value

    @staticmethod
    def _validate_identity(turn_id: str, conversation_id: int, generation: int) -> None:
        if type(turn_id) is not str or not turn_id:
            raise ValueError("turn_id must be a non-empty string")
        if type(conversation_id) is not int or conversation_id <= 0:
            raise ValueError("conversation_id must be positive")
        if type(generation) is not int or generation <= 0:
            raise ValueError("generation must be positive")

    @staticmethod
    def _validate_request_id(request_id: str | None) -> None:
        if request_id is not None and (type(request_id) is not str or not request_id):
            raise ValueError("request_id must be a non-empty string")

    def _cursor(self, handle: _ManagedRun, seq: int) -> str:
        raw = json.dumps(
            {
                "v": 1,
                "e": self.runtime_epoch,
                "t": handle.turn_id,
                "g": handle.generation,
                "s": seq,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded = _b64(raw)
        signature = hmac.new(self._cursor_secret, encoded.encode("ascii"), hashlib.sha256).digest()
        return f"{encoded}.{_b64(signature)}"

    def _decode_cursor(self, cursor: str, handle: _ManagedRun) -> int:
        if type(cursor) is not str or "." not in cursor:
            raise RuntimeCursorInvalid("runtime cursor is invalid")
        encoded, signature = cursor.split(".", 1)
        expected = hmac.new(self._cursor_secret, encoded.encode("ascii"), hashlib.sha256).digest()
        try:
            valid_signature = hmac.compare_digest(_unb64(signature), expected)
            payload = json.loads(_unb64(encoded).decode("utf-8"))
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
            valid_signature = False
            payload = None
        if not valid_signature or not isinstance(payload, dict):
            raise RuntimeCursorInvalid("runtime cursor signature is invalid")
        sequence = payload.get("s")
        if (
            payload.get("v") != 1
            or payload.get("e") != self.runtime_epoch
            or payload.get("t") != handle.turn_id
            or payload.get("g") != handle.generation
            or type(sequence) is not int
            or sequence < 0
        ):
            raise RuntimeCursorInvalid("runtime cursor belongs to another execution")
        return sequence

    def _submission(self, handle: _ManagedRun, *, replayed: bool) -> RuntimeSubmission:
        current = self._cursor(handle, handle.next_seq)
        return RuntimeSubmission(
            protocol_version=RUNTIME_PROTOCOL_VERSION,
            request_id=handle.request_id,
            turn_id=handle.turn_id,
            conversation_id=handle.conversation_id,
            execution_generation=handle.generation,
            state=handle.state,
            accepted_at=handle.accepted_at,
            deadline_at=handle.budget.deadline_at,
            snapshot_cursor=current,
            event_cursor=current,
            runtime_epoch=self.runtime_epoch,
            replayed=replayed,
        )

    @contextmanager
    def serialized_admission(self) -> Iterator[None]:
        """Bound host admission waits without blocking workers or watchdogs.

        The host rechecks durable idempotency inside this gate, before queue
        reservation, so a concurrent retry cannot mistake in-flight admission
        of its own request for a proven capacity rejection.
        """
        if not self._admission_gate.acquire(timeout=5.0):
            raise RuntimeManagerError("runtime admission is busy")
        try:
            yield
        finally:
            self._admission_gate.release()

    @contextmanager
    def reserve_admission(self) -> Iterator[object]:
        """Reserve bounded queue capacity before the host persists admission."""
        token = object()
        with self._condition:
            if self._closed:
                raise RuntimeManagerError("runtime manager is shut down")
            if self._queued_count + len(self._admission_reservations) >= self.max_queue:
                raise RuntimeCapacityExhausted()
            self._admission_reservations.add(token)
        try:
            yield token
        finally:
            with self._condition:
                self._admission_reservations.discard(token)

    def submit(
        self,
        *,
        turn_id: str,
        conversation_id: int,
        generation: int = 1,
        operation: RuntimeOperation,
        request_id: str | None = None,
        budget: RuntimeBudget | None = None,
        invocation_control: RuntimeInvocationControl | None = None,
        timeout_seconds: float | None = None,
        max_model_calls: int | None = None,
        max_title_model_calls: int | None = None,
        max_tool_calls: int | None = None,
        max_events: int | None = None,
        max_event_bytes: int | None = None,
        title_action: Callable[[], object] | None = None,
        title_fallback: Callable[[], object] | None = None,
        admission_reservation: object | None = None,
    ) -> RuntimeSubmission:
        """Admit exactly one operation and schedule it on the fixed run pool."""

        self._validate_identity(turn_id, conversation_id, generation)
        self._validate_request_id(request_id)
        if not callable(operation):
            raise TypeError("operation must be callable")
        if title_action is not None and not callable(title_action):
            raise TypeError("title_action must be callable")
        if title_fallback is not None and not callable(title_fallback):
            raise TypeError("title_fallback must be callable")
        now = self._now()
        with self._condition:
            if self._closed:
                raise RuntimeManagerError("runtime manager is shut down")
            key = (turn_id, generation)
            if request_id is not None:
                existing_key = self._request_index.get(request_id)
                if existing_key is not None:
                    if existing_key != key:
                        raise RuntimeSubmissionConflict("request_id belongs to another execution")
                    existing = self._runs.get(existing_key)
                    if existing is None:
                        raise RuntimeSubmissionConflict("request_id execution is no longer retained")
                    return self._submission(existing, replayed=True)
            existing = self._runs.get(key)
            if existing is not None:
                raise RuntimeSubmissionConflict("execution identity was already submitted")
            if admission_reservation is not None and admission_reservation not in self._admission_reservations:
                raise RuntimeSubmissionConflict("admission reservation is not active")
            if admission_reservation is None and self._queued_count + len(self._admission_reservations) >= self.max_queue:
                raise RuntimeCapacityExhausted()
            if budget is None:
                budget = RuntimeBudget(
                    timeout_seconds=self.default_timeout_seconds
                    if timeout_seconds is None
                    else timeout_seconds,
                    admitted_at=now,
                    max_model_calls=self.default_max_model_calls
                    if max_model_calls is None
                    else max_model_calls,
                    max_title_model_calls=self.default_max_title_model_calls
                    if max_title_model_calls is None
                    else max_title_model_calls,
                    max_tool_calls=self.default_max_tool_calls
                    if max_tool_calls is None
                    else max_tool_calls,
                    max_events=self.default_max_events if max_events is None else max_events,
                    max_event_bytes=(
                        self.default_max_event_bytes
                        if max_event_bytes is None
                        else max_event_bytes
                    ),
                    clock=self.clock,
                )
            elif not isinstance(budget, RuntimeBudget):
                raise TypeError("budget must be a RuntimeBudget")
            control = invocation_control or InMemoryRuntimeInvocationControl()
            handle = _ManagedRun(
                request_id=request_id,
                turn_id=turn_id,
                conversation_id=conversation_id,
                generation=generation,
                operation=operation,
                budget=budget,
                accepted_at=now,
                invocation_control=control,
                title_action=title_action,
                title_fallback=title_fallback,
            )
            self._ensure_started_locked()
            handle.state = "queued"
            if admission_reservation is not None:
                self._admission_reservations.remove(admission_reservation)
            self._runs[key] = handle
            if request_id is not None:
                self._request_index[request_id] = key
            self._queued_count += 1
            self._emit_status_locked(handle, "queued", "排队中")
            self._work_queue.append(handle)
            self._condition.notify()
            return self._submission(handle, replayed=False)

    def _emit_status_locked(self, handle: _ManagedRun, phase: str, label: str) -> None:
        self._emit_event_locked(handle, StatusEvent(phase=phase, label=label), force=False)

    @staticmethod
    def _call_operation(
        operation: RuntimeOperation,
        sink: RuntimeEventSink,
        control: RuntimeInvocationControl,
        budget: RuntimeBudget,
    ) -> object:
        # Runtime operations are owned by the application and should use the
        # three argument contract.  Supporting fewer positional arguments keeps
        # the manager useful for deterministic tests and management adapters,
        # while still rejecting ambiguous *args/**kwargs callables.
        try:
            signature = inspect.signature(operation)
        except (TypeError, ValueError):
            return operation(sink, control, budget)
        parameters = list(signature.parameters.values())
        if any(parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD} for parameter in parameters):
            return operation(sink, control, budget)
        positional = [
            parameter
            for parameter in parameters
            if parameter.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
        ]
        required = [parameter for parameter in positional if parameter.default is inspect.Parameter.empty]
        if len(required) > 3 or len(positional) < 0:
            raise TypeError("runtime operation signature is unsupported")
        if len(positional) >= 3:
            return operation(sink, control, budget)
        if len(positional) == 2:
            return cast(Any, operation)(sink, control)
        if len(positional) == 1:
            return cast(Any, operation)(sink)
        return cast(Any, operation)()

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._work_queue and not self._closed:
                    self._condition.wait()
                if not self._work_queue:
                    return
                handle = self._work_queue.popleft()
                # Capacity is released only when the fixed worker has actually
                # dequeued the item.  A watchdog timeout cannot make a queued
                # WorkItem disappear from an unbounded executor queue.
                self._queued_count = max(0, self._queued_count - 1)
                self._condition.notify_all()
            self._worker_entry(handle)

    def _worker_entry(self, handle: _ManagedRun) -> object | None:
        with self._condition:
            if handle.logical_terminal:
                handle.worker_done = True
                self._condition.notify_all()
                self._maybe_release_operation_locked(handle)
                return None
            try:
                handle.budget.check_deadline()
            except RuntimeBudgetError:
                self._finish_before_start_locked(handle, "runtime deadline exhausted")
                handle.worker_done = True
                self._maybe_release_operation_locked(handle)
                self._condition.notify_all()
                return None
            handle.state = "running"
            self._emit_status_locked(handle, "running", "执行中")

        result: object | None = None
        try:
            handle.budget.check_deadline()
            operation = handle.operation
            if operation is None:
                raise RuntimeError("runtime operation is unavailable")
            result = self._call_operation(
                operation,
                _HandleEventSink(self, handle),
                handle.invocation_control,
                handle.budget,
            )
            self._run_title_if_allowed(handle)
            with self._condition:
                if not handle.logical_terminal:
                    if handle.stop_requested:
                        self._finish_exception_locked(handle, "stopped", _SAFE_STOP_MESSAGE)
                    else:
                        self._finish_from_outcome_locked(handle, result)
            return result
        except (RuntimeAgentTimedOut, RuntimeDeadlineExceeded, RuntimeClockInvalid) as exc:
            with self._condition:
                if not handle.logical_terminal:
                    self._finish_unknown_locked(handle, str(exc) or "runtime deadline exhausted")
            return None
        except RuntimeBudgetExceeded:
            with self._condition:
                if not handle.logical_terminal:
                    self._finish_unknown_locked(
                        handle,
                        "runtime budget exhausted",
                        safe_message=_SAFE_BUDGET_MESSAGE,
                        request_timeout=False,
                    )
            return None
        except RuntimeCancelled:
            with self._condition:
                if not handle.logical_terminal:
                    state: RuntimeExecutionState = "stopped" if handle.stop_requested else "interrupted"
                    self._finish_exception_locked(
                        handle,
                        state,
                        _SAFE_STOP_MESSAGE if state == "stopped" else _SAFE_FAILURE_MESSAGE,
                    )
            return None
        except RuntimeTransportAborted:
            with self._condition:
                if not handle.logical_terminal:
                    self._finish_unknown_locked(
                        handle,
                        "runtime transport aborted",
                        safe_message=_SAFE_SHUTDOWN_MESSAGE,
                        request_timeout=False,
                    )
            return None
        except BaseException:
            with self._condition:
                if not handle.logical_terminal:
                    self._finish_exception_locked(handle, "failed", _SAFE_FAILURE_MESSAGE)
            return None
        finally:
            with self._condition:
                handle.worker_done = True
                self._maybe_release_operation_locked(handle)
                self._condition.notify_all()

    def _maybe_release_operation_locked(self, handle: _ManagedRun) -> None:
        """Drop executable closures while retaining bounded terminal facts."""

        if not handle.worker_done or not handle.logical_terminal or handle.subscribers:
            return
        handle.operation = None
        handle.title_action = None
        handle.title_fallback = None
        terminal = [item for item in self._runs.values() if item.worker_done and item.logical_terminal]
        if len(terminal) <= self.max_retained_runs:
            return
        terminal.sort(key=lambda item: item.accepted_at)
        for candidate in terminal[: len(terminal) - self.max_retained_runs]:
            if candidate is handle:
                continue
            if candidate.subscribers:
                continue
            self._runs.pop(candidate.key, None)
            if candidate.request_id is not None:
                self._request_index.pop(candidate.request_id, None)

    def _run_title_if_allowed(self, handle: _ManagedRun) -> None:
        action = handle.title_action
        # A watchdog/stop may have already converged the Runtime to a
        # terminal or unknown result while the physical operation was still
        # unwinding.  Do not start a presentation model call after that
        # boundary; doing so would spend budget and mutate a title after the
        # execution has ceased to own the turn.
        if action is None or handle.logical_terminal or handle.stop_requested:
            return
        if handle.budget.try_reserve_model_call(purpose="title"):
            try:
                handle.budget.check_deadline()
                action()
            except RuntimeBudgetError:
                if handle.title_fallback is not None:
                    handle.title_fallback()
            except BaseException:
                # Title generation is an optional presentation side effect.  A
                # deterministic fallback is allowed, but it cannot change the
                # already persisted Runtime outcome.
                if handle.title_fallback is not None:
                    try:
                        handle.title_fallback()
                    except BaseException:
                        pass
        elif handle.title_fallback is not None:
            try:
                handle.title_fallback()
            except BaseException:
                pass

    def _finish_before_start_locked(self, handle: _ManagedRun, _message: str) -> None:
        handle.state = "failed"
        handle.logical_terminal = True
        outcome = RuntimeFailureOutcome(
            code=cast(Any, _runtime_failure_code("operation_failed")),
            message=_SAFE_TIMEOUT_MESSAGE,
            status_code=504,
            retryable=True,
            degraded=False,
            conversation_id=handle.conversation_id,
        )
        handle.outcome = outcome
        self._emit_terminal_locked(handle, outcome, _SAFE_TIMEOUT_MESSAGE)

    def _finish_from_outcome_locked(self, handle: _ManagedRun, outcome: object) -> None:
        handle.outcome = outcome
        if isinstance(outcome, ConfirmationRequiredOutcome):
            state: RuntimeExecutionState = "waiting_confirmation"
        elif isinstance(outcome, RuntimeFailureOutcome):
            state = "failed"
        elif isinstance(outcome, (MessageOutcome, OperationPendingOutcome, OperationReplayOutcome)):
            state = "completed"
        else:
            state = "completed"
        handle.state = state
        handle.logical_terminal = True
        if not handle.terminal_event_emitted:
            self._emit_terminal_locked(handle, outcome, "")
        else:
            self._close_subscribers_locked(handle)

    def _finish_exception_locked(
        self,
        handle: _ManagedRun,
        state: RuntimeExecutionState,
        safe_message: str = _SAFE_FAILURE_MESSAGE,
    ) -> None:
        handle.state = state
        handle.logical_terminal = True
        outcome = RuntimeFailureOutcome(
            code=cast(Any, _runtime_failure_code("operation_failed")),
            message=safe_message,
            status_code=499 if state in {"interrupted", "stopped"} else 500,
            retryable=state not in {"interrupted", "stopped"},
            degraded=False,
            conversation_id=handle.conversation_id,
        )
        handle.outcome = outcome
        self._emit_terminal_locked(handle, outcome, safe_message)

    def _finish_unknown_locked(
        self,
        handle: _ManagedRun,
        _message: str,
        *,
        safe_message: str = _SAFE_TIMEOUT_MESSAGE,
        request_timeout: bool = True,
    ) -> None:
        handle.timeout_requested = handle.timeout_requested or request_timeout
        handle.state = "result_unknown"
        handle.logical_terminal = True
        handle.outcome = None
        self._emit_terminal_locked(handle, None, safe_message)
        if request_timeout:
            try:
                handle.invocation_control.request_timeout()
            except BaseException:
                pass

    def _emit_terminal_locked(self, handle: _ManagedRun, outcome: object | None, message: str) -> None:
        if message:
            error = ErrorEvent(
                code=cast(Any, _runtime_failure_code("operation_failed")),
                message=message,
                retryable=handle.state == "failed",
                degraded=False,
            )
            self._emit_event_locked(handle, error, force=True)
        self._emit_event_locked(handle, CompletedEvent(response=cast(Any, outcome)), force=True)
        self._close_subscribers_locked(handle)

    def _emit_event_locked(self, handle: _ManagedRun, event: RuntimeEvent, *, force: bool) -> bool:
        if handle.logical_terminal and not isinstance(event, CompletedEvent) and not force:
            return False
        try:
            payload = runtime_event_payload(event)
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            return False
        if not force:
            try:
                handle.budget.record_event(len(raw))
            except RuntimeBudgetExceeded as exc:
                if exc.resource in {"event", "event_bytes"}:
                    if not handle.event_budget_truncated:
                        handle.event_budget_truncated = True
                        self._drop_subscribers_for_resync_locked(handle)
                    return False
                self._finish_unknown_locked(handle, "runtime budget exhausted")
                return False
            except RuntimeBudgetError:
                self._finish_unknown_locked(handle, "runtime deadline exhausted")
                return False
        handle.next_seq += 1
        sequence = handle.next_seq
        envelope = RuntimeEventEnvelope(
            stream_version=RUNTIME_STREAM_VERSION,
            turn_id=handle.turn_id,
            conversation_id=handle.conversation_id,
            execution_generation=handle.generation,
            event_seq=sequence,
            timestamp=self._safe_now(),
            kind=_event_kind(event),
            data=MappingProxyType(payload),
            cursor=self._cursor(handle, sequence),
            durable=isinstance(event, (CompletedEvent, ErrorEvent)),
        )
        size = len(raw)
        handle.history.append(envelope)
        handle.history_bytes += size
        while handle.history and (
            len(handle.history) > self.ring_max_events or handle.history_bytes > self.ring_max_bytes
        ):
            removed = handle.history.popleft()
            handle.history_bytes = max(0, handle.history_bytes - _envelope_size(removed))
            handle.history_truncated = True
        if isinstance(event, CompletedEvent):
            handle.terminal_event_emitted = True
            handle.outcome = event.response
            if isinstance(event.response, ConfirmationRequiredOutcome):
                handle.state = "waiting_confirmation"
            elif isinstance(event.response, RuntimeFailureOutcome):
                handle.state = "failed"
            elif event.response is not None:
                handle.state = "completed"
            handle.logical_terminal = True
        for subscriber in tuple(handle.subscribers):
            if not subscriber._push(envelope):
                handle.subscribers.discard(subscriber)
        return True

    def _safe_now(self) -> float:
        try:
            value = self._now()
        except RuntimeClockInvalid:
            return 0.0
        return value

    def _close_subscribers_locked(self, handle: _ManagedRun) -> None:
        for subscriber in tuple(handle.subscribers):
            subscriber._finish()
        handle.subscribers.clear()

    def _drop_subscribers_for_resync_locked(self, handle: _ManagedRun) -> None:
        """Detach slow/overflowed readers while the worker keeps running."""

        marker = RuntimeControlFrame(
            kind="resync_required",
            turn_id=handle.turn_id,
            conversation_id=handle.conversation_id,
            execution_generation=handle.generation,
            event_seq=handle.next_seq,
            cursor=self._cursor(handle, handle.next_seq),
            data=MappingProxyType({"error_code": RuntimeResyncRequired.error_code}),
        )
        for subscriber in tuple(handle.subscribers):
            subscriber._force_resync(marker)
        handle.subscribers.clear()

    def _lookup(self, turn_id: str, generation: int | None = None) -> _ManagedRun:
        with self._lock:
            if generation is not None:
                handle = self._runs.get((turn_id, generation))
            else:
                matches = [handle for key, handle in self._runs.items() if key[0] == turn_id]
                handle = max(matches, key=lambda item: item.generation) if matches else None
            if handle is None:
                raise RuntimeTurnNotFound("runtime turn is not retained")
            return handle

    def status(self, turn_id: str, generation: int | None = None) -> dict[str, object]:
        handle = self._lookup(turn_id, generation)
        with self._lock:
            cursor = self._cursor(handle, handle.next_seq)
            return RuntimeStatus(
                protocol_version=RUNTIME_PROTOCOL_VERSION,
                request_id=handle.request_id,
                turn_id=handle.turn_id,
                conversation_id=handle.conversation_id,
                execution_generation=handle.generation,
                state=handle.state,
                accepted_at=handle.accepted_at,
                deadline_at=handle.budget.deadline_at,
                event_cursor=cursor,
                snapshot_cursor=cursor,
                runtime_epoch=self.runtime_epoch,
                worker_done=handle.worker_done,
                actual_worker_alive=not handle.worker_done,
                outcome=handle.outcome,
                budget=MappingProxyType(handle.budget.snapshot().as_mapping()),
            ).as_mapping()

    def snapshot(
        self,
        turn_id: str,
        *,
        generation: int | None = None,
        after: str | None = None,
        limit: int | None = None,
        durable: Mapping[str, object] | None = None,
    ) -> RuntimeSnapshot:
        handle = self._lookup(turn_id, generation)
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError("limit must be a positive integer")
        with self._lock:
            fresh = after is None
            after_seq = 0 if fresh else self._decode_cursor(cast(str, after), handle)
            if not fresh:
                self._require_replayable_locked(handle, after_seq)
            high = handle.next_seq
            events = tuple(event for event in handle.history if after_seq < event.event_seq <= high)
            if limit is not None and len(events) > limit:
                events = events[:limit]
                next_cursor = events[-1].cursor if events else self._cursor(handle, after_seq)
            else:
                next_cursor = None
            if durable is None:
                loader = self._durable_snapshot_loader
                if loader is None:
                    durable_payload: Mapping[str, object] = MappingProxyType({})
                else:
                    loaded = loader(handle.turn_id, handle.generation)
                    durable_payload = MappingProxyType(dict(loaded))
            else:
                durable_payload = MappingProxyType(dict(durable))
            return RuntimeSnapshot(
                stream_version=RUNTIME_STREAM_VERSION,
                turn_id=handle.turn_id,
                conversation_id=handle.conversation_id,
                execution_generation=handle.generation,
                state=handle.state,
                events=events,
                durable=durable_payload,
                snapshot_cursor=self._cursor(handle, high),
                high_watermark=high,
                next_cursor=next_cursor,
                runtime_epoch=self.runtime_epoch,
                budget=MappingProxyType(handle.budget.snapshot().as_mapping()),
                history_truncated=handle.history_truncated,
                progress_gap=fresh and handle.history_truncated,
            )

    def _require_replayable_locked(self, handle: _ManagedRun, after_seq: int) -> None:
        if after_seq > handle.next_seq:
            raise RuntimeCursorInvalid("runtime cursor is ahead of the execution")
        if not handle.history:
            if after_seq < handle.next_seq:
                raise RuntimeResyncRequired("runtime event history was reclaimed")
            return
        first_seq = handle.history[0].event_seq
        if handle.history_truncated and after_seq < first_seq - 1:
            raise RuntimeResyncRequired("runtime event history was truncated")

    def snapshot_and_subscribe(
        self,
        turn_id: str,
        *,
        generation: int | None = None,
        after: str | None = None,
        limit: int | None = None,
        durable: Mapping[str, object] | None = None,
    ) -> tuple[RuntimeSnapshot, RuntimeSubscription]:
        handle = self._lookup(turn_id, generation)
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError("limit must be a positive integer")
        with self._lock:
            fresh = after is None
            after_seq = 0 if fresh else self._decode_cursor(cast(str, after), handle)
            if not fresh:
                self._require_replayable_locked(handle, after_seq)
            high = handle.next_seq
            events = tuple(event for event in handle.history if after_seq < event.event_seq <= high)
            if limit is not None and len(events) > limit:
                events = events[:limit]
                next_cursor = events[-1].cursor if events else self._cursor(handle, after_seq)
            else:
                next_cursor = None
            if durable is None:
                loader = self._durable_snapshot_loader
                loaded = loader(handle.turn_id, handle.generation) if loader is not None else {}
                durable_payload = MappingProxyType(dict(loaded))
            else:
                durable_payload = MappingProxyType(dict(durable))
            snapshot = RuntimeSnapshot(
                stream_version=RUNTIME_STREAM_VERSION,
                turn_id=handle.turn_id,
                conversation_id=handle.conversation_id,
                execution_generation=handle.generation,
                state=handle.state,
                events=events,
                durable=durable_payload,
                snapshot_cursor=self._cursor(handle, high),
                high_watermark=high,
                next_cursor=next_cursor,
                runtime_epoch=self.runtime_epoch,
                budget=MappingProxyType(handle.budget.snapshot().as_mapping()),
                history_truncated=handle.history_truncated,
                progress_gap=fresh and handle.history_truncated,
            )
            # Register the reader at the last sequence included in the
            # snapshot.  The registration happens under this same lock as the
            # high-watermark read, so events committed after the snapshot are
            # delivered exactly once and cannot fall into a gap.  When a
            # bounded snapshot page is requested, resume after the last
            # returned event so the reader receives the remainder of that
            # page's history as well.
            subscription_after = events[-1].event_seq if events else after_seq
            subscription = self._subscribe_locked(handle, subscription_after)
            return snapshot, subscription

    def subscribe(
        self,
        turn_id: str,
        *,
        generation: int | None = None,
        after: str | None = None,
    ) -> RuntimeSubscription:
        handle = self._lookup(turn_id, generation)
        with self._lock:
            after_seq = 0 if after is None else self._decode_cursor(after, handle)
            self._require_replayable_locked(handle, after_seq)
            return self._subscribe_locked(handle, after_seq)

    def heartbeat(
        self,
        turn_id: str,
        *,
        generation: int | None = None,
    ) -> RuntimeControlFrame:
        """Return a non-sequenced liveness frame for a slow subscriber."""

        handle = self._lookup(turn_id, generation)
        with self._lock:
            return RuntimeControlFrame(
                kind="heartbeat",
                turn_id=handle.turn_id,
                conversation_id=handle.conversation_id,
                execution_generation=handle.generation,
                event_seq=handle.next_seq,
                cursor=self._cursor(handle, handle.next_seq),
                data=MappingProxyType({"state": handle.state}),
            )

    def _subscribe_locked(self, handle: _ManagedRun, after_seq: int) -> RuntimeSubscription:
        subscription = RuntimeSubscription(
            self,
            handle.key,
            max_events=self.subscriber_max_events,
            max_bytes=self.subscriber_max_bytes,
        )
        handle.subscribers.add(subscription)
        current_cursor = self._cursor(handle, handle.next_seq)
        subscription._push(
            RuntimeControlFrame(
                kind="subscribed",
                turn_id=handle.turn_id,
                conversation_id=handle.conversation_id,
                execution_generation=handle.generation,
                event_seq=handle.next_seq,
                cursor=current_cursor,
                data=MappingProxyType({"high_watermark": handle.next_seq}),
            )
        )
        for event in tuple(handle.history):
            if event.event_seq > after_seq:
                if not subscription._push(event):
                    handle.subscribers.discard(subscription)
                    break
        if handle.logical_terminal:
            subscription._finish()
            handle.subscribers.discard(subscription)
        return subscription

    def _unsubscribe(self, subscription: RuntimeSubscription) -> None:
        with self._lock:
            handle = self._runs.get(subscription._key)
            if handle is not None:
                handle.subscribers.discard(subscription)
        with subscription._condition:
            subscription._closed = True
            subscription._frames.clear()
            subscription._queued_bytes = 0
            subscription._condition.notify_all()

    def wait(self, turn_id: str, *, generation: int | None = None, timeout: float | None = None) -> bool:
        handle = self._lookup(turn_id, generation)
        if timeout is not None and (type(timeout) not in {int, float} or isinstance(timeout, bool) or timeout < 0):
            raise ValueError("timeout must be a non-negative number")
        deadline = None if timeout is None else monotonic() + float(timeout)
        with self._condition:
            while not handle.worker_done:
                remaining = None if deadline is None else deadline - monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def interrupt(self, turn_id: str, *, generation: int | None = None) -> dict[str, object]:
        handle = self._lookup(turn_id, generation)
        with self._condition:
            if handle.logical_terminal:
                return self.status(turn_id, handle.generation)
            handle.stop_requested = True
            if handle.state == "queued":
                # Keep the queue reservation until the fixed worker physically
                # dequeues this entry.  Otherwise repeated interrupt/deadline
                # calls can admit more entries than the bounded queue owns.
                self._finish_exception_locked(handle, "stopped", _SAFE_STOP_MESSAGE)
            else:
                try:
                    handle.invocation_control.request_cancel(CancelReason.EXPLICIT_CANCEL)
                except BaseException:
                    pass
                handle.state = "stopped"
            return self.status(turn_id, handle.generation)

    def _watchdog_loop(self) -> None:
        while not self._watchdog_stop.wait(0.02):
            try:
                now = self._now()
            except RuntimeClockInvalid:
                now = float("inf")
            with self._condition:
                if self._closed:
                    return
                for handle in tuple(self._runs.values()):
                    if handle.logical_terminal or handle.budget.deadline_at > now:
                        continue
                    if handle.state == "queued":
                        self._finish_before_start_locked(handle, "runtime deadline exhausted")
                    elif handle.state in {"running", "accepted"}:
                        self._finish_unknown_locked(handle, "runtime deadline exhausted")

    def close(self, *, wait: bool = False) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._watchdog_stop.set()
            for handle in tuple(self._runs.values()):
                if handle.logical_terminal:
                    continue
                if handle.state == "queued":
                    self._finish_exception_locked(handle, "stopped", _SAFE_SHUTDOWN_MESSAGE)
                else:
                    handle.stop_requested = True
                    self._finish_unknown_locked(
                        handle,
                        "runtime manager is shutting down",
                        safe_message=_SAFE_SHUTDOWN_MESSAGE,
                        request_timeout=False,
                    )
                    try:
                        handle.invocation_control.request_cancel(CancelReason.TRANSPORT_ABORTED)
                    except BaseException:
                        pass
            # The private queue is the capacity boundary.  Once shutdown has
            # been requested, queued operations can be discarded because they
            # have never entered a worker and therefore have no side effects.
            for handle in tuple(self._work_queue):
                if not handle.worker_done:
                    handle.worker_done = True
                    self._maybe_release_operation_locked(handle)
            self._work_queue.clear()
            self._queued_count = 0
            self._condition.notify_all()
        watchdog = self._watchdog
        if watchdog is not None and watchdog.ident is not None:
            watchdog.join(timeout=1.0)
        if wait:
            for worker in self._workers:
                if worker.ident is not None:
                    worker.join()

    shutdown = close

    @property
    def active_worker_count(self) -> int:
        with self._lock:
            return sum(1 for handle in self._runs.values() if not handle.worker_done)

    @property
    def queued_count(self) -> int:
        with self._lock:
            return self._queued_count


class _HandleEventSink:
    __slots__ = ("_manager", "_handle")

    def __init__(self, manager: RuntimeExecutionManager, handle: _ManagedRun) -> None:
        self._manager = manager
        self._handle = handle

    def emit(self, event: RuntimeEvent) -> None:
        if not isinstance(event, RuntimeEvent.__args__):
            raise TypeError("event must be a typed RuntimeEvent")
        with self._manager._condition:
            self._manager._emit_event_locked(self._handle, event, force=False)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    if type(value) is not str:
        raise ValueError("base64 value must be a string")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(child) for child in value]
    return value


def _envelope_size(envelope: RuntimeEventEnvelope) -> int:
    try:
        return len(json.dumps(envelope.as_mapping(), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _event_kind(event: RuntimeEvent) -> str:
    return {
        "MetaEvent": "meta",
        "UserMessageSavedEvent": "user_message_saved",
        "StatusEvent": "status",
        "AssistantDeltaEvent": "assistant_delta",
        "ToolCallEvent": "tool_call",
        "ToolResultEvent": "tool_result",
        "ConfirmationRequiredEvent": "confirmation_required",
        "AssistantMessageEvent": "assistant_message",
        "ErrorEvent": "error",
        "CompletedEvent": "completed",
    }[type(event).__name__]


def _outcome_payload(outcome: object) -> object:
    try:
        from .event_sink import runtime_outcome_payload

        return runtime_outcome_payload(outcome)
    except (TypeError, ValueError):
        return {"type": type(outcome).__name__, "message": str(outcome)}


def _runtime_failure_code(value: str) -> object:
    # Keep this import/value lookup local so this manager remains usable by
    # deterministic tests even when a future RuntimeFailureCode grows.
    from .errors import RuntimeFailureCode

    try:
        return RuntimeFailureCode(value)
    except ValueError:
        return RuntimeFailureCode.OPERATION_FAILED


__all__ = [
    "RUNTIME_PROTOCOL_VERSION",
    "RUNTIME_STREAM_VERSION",
    "RuntimeCapacityExhausted",
    "RuntimeControlFrame",
    "RuntimeCursorInvalid",
    "RuntimeEventEnvelope",
    "RuntimeExecutionManager",
    "RuntimeExecutionState",
    "RuntimeManagerError",
    "RuntimeResyncRequired",
    "RuntimeSnapshot",
    "RuntimeStatus",
    "RuntimeStreamFrame",
    "RuntimeSubmission",
    "RuntimeSubmissionConflict",
    "RuntimeSubscription",
    "RuntimeTurnNotFound",
]
