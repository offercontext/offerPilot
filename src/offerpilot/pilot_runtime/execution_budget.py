"""Runtime-owned limits for detached Pilot executions.

The Journal has a deliberately small diagnostic budget.  Detached executions
need a separate budget whose lifetime begins at admission, includes queue wait,
and is shared by the agent and title model calls.  This module contains only
that runtime policy; it does not import the Journal or any HTTP framework.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Callable, Literal


BudgetPurpose = Literal["agent", "title", "other"]


class RuntimeBudgetError(RuntimeError):
    """Base class for an exhausted or invalid Runtime allowance."""


class RuntimeDeadlineExceeded(RuntimeBudgetError):
    """The absolute Runtime deadline has elapsed."""


class RuntimeBudgetExceeded(RuntimeBudgetError):
    """A Runtime count or byte allowance has been exhausted."""

    def __init__(self, resource: str, message: str | None = None) -> None:
        if resource not in {"model", "tool", "event", "event_bytes"}:
            raise ValueError("unsupported runtime budget resource")
        self.resource = resource
        super().__init__(message or f"runtime {resource} budget exhausted")


class RuntimeClockInvalid(RuntimeBudgetError):
    """The monotonic clock failed or moved backwards."""


@dataclass(frozen=True, slots=True)
class RuntimeBudgetSnapshot:
    """A safe, JSON-friendly view of one execution's runtime allowance."""

    admitted_at: float
    deadline_at: float
    now: float
    model_calls: int
    title_model_calls: int
    tool_calls: int
    event_count: int
    event_bytes: int
    max_model_calls: int
    max_title_model_calls: int
    max_tool_calls: int
    max_events: int
    max_event_bytes: int

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_at - self.now)

    @property
    def remaining_model_calls(self) -> int:
        return max(0, self.max_model_calls - self.model_calls)

    @property
    def remaining_title_model_calls(self) -> int:
        return max(0, self.max_title_model_calls - self.title_model_calls)

    @property
    def remaining_tool_calls(self) -> int:
        return max(0, self.max_tool_calls - self.tool_calls)

    @property
    def remaining_events(self) -> int:
        return max(0, self.max_events - self.event_count)

    @property
    def remaining_event_bytes(self) -> int:
        return max(0, self.max_event_bytes - self.event_bytes)

    def as_mapping(self) -> dict[str, object]:
        return {
            "admitted_at": self.admitted_at,
            "deadline_at": self.deadline_at,
            "now": self.now,
            "remaining_seconds": self.remaining_seconds,
            "model_calls": self.model_calls,
            "remaining_model_calls": self.remaining_model_calls,
            "title_model_calls": self.title_model_calls,
            "remaining_title_model_calls": self.remaining_title_model_calls,
            "tool_calls": self.tool_calls,
            "remaining_tool_calls": self.remaining_tool_calls,
            "event_count": self.event_count,
            "remaining_events": self.remaining_events,
            "event_bytes": self.event_bytes,
            "remaining_event_bytes": self.remaining_event_bytes,
        }


class RuntimeBudget:
    """Thread-safe absolute deadline and call/event counters.

    ``admitted_at`` is captured by the caller before an item enters the
    manager queue.  Passing an already-created budget therefore preserves the
    deadline while a task waits for a worker.  Counters are reserved before a
    provider or tool is called, so a timed-out call cannot be retried behind
    the caller's back.
    """

    __slots__ = (
        "admitted_at",
        "deadline_at",
        "max_model_calls",
        "max_title_model_calls",
        "max_tool_calls",
        "max_events",
        "max_event_bytes",
        "clock",
        "_last_clock",
        "_model_calls",
        "_title_model_calls",
        "_tool_calls",
        "_event_count",
        "_event_bytes",
        "_clock_invalid",
        "_lock",
    )

    def __init__(
        self,
        timeout_seconds: float = 120.0,
        *,
        admitted_at: float | None = None,
        deadline_at: float | None = None,
        max_model_calls: int = 20,
        max_title_model_calls: int = 1,
        max_tool_calls: int = 40,
        max_events: int = 512,
        max_event_bytes: int = 1_048_576,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        if type(timeout_seconds) not in {int, float} or isinstance(timeout_seconds, bool):
            raise TypeError("timeout_seconds must be a number")
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        for name, value in (
            ("max_model_calls", max_model_calls),
            ("max_events", max_events),
            ("max_event_bytes", max_event_bytes),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name, value in (
            ("max_title_model_calls", max_title_model_calls),
            ("max_tool_calls", max_tool_calls),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

        try:
            now = float(clock() if admitted_at is None else admitted_at)
        except (BaseException,) as exc:
            raise RuntimeClockInvalid("runtime clock is unavailable") from exc
        if not math.isfinite(now):
            raise RuntimeClockInvalid("runtime clock returned a non-finite value")
        if admitted_at is None:
            admitted = now
        else:
            admitted = now
        if deadline_at is None:
            deadline = admitted + timeout
        else:
            if type(deadline_at) not in {int, float} or not math.isfinite(float(deadline_at)):
                raise ValueError("deadline_at must be finite")
            deadline = float(deadline_at)
            if deadline <= admitted:
                raise ValueError("deadline_at must be after admitted_at")

        self.admitted_at = admitted
        self.deadline_at = deadline
        self.max_model_calls = max_model_calls
        self.max_title_model_calls = max_title_model_calls
        self.max_tool_calls = max_tool_calls
        self.max_events = max_events
        self.max_event_bytes = max_event_bytes
        self.clock = clock
        self._last_clock = admitted
        self._model_calls = 0
        self._title_model_calls = 0
        self._tool_calls = 0
        self._event_count = 0
        self._event_bytes = 0
        self._clock_invalid = False
        self._lock = RLock()

    def _read_clock(self) -> float:
        with self._lock:
            # Clock reads and the monotonicity check must be one critical
            # section.  Otherwise concurrent budget consumers can observe
            # samples out of order and accept a backwards clock.  Once a
            # clock is invalid, every subsequent read fails closed instead of
            # allowing a later sample to revive the execution.
            if self._clock_invalid:
                raise RuntimeClockInvalid("runtime clock is unavailable")
            try:
                value = float(self.clock())
            except (BaseException,) as exc:
                self._clock_invalid = True
                raise RuntimeClockInvalid("runtime clock is unavailable") from exc
            if not math.isfinite(value):
                self._clock_invalid = True
                raise RuntimeClockInvalid("runtime clock returned a non-finite value")
            if value < self._last_clock:
                self._clock_invalid = True
                raise RuntimeClockInvalid("runtime clock moved backwards")
            self._last_clock = value
        return value

    @property
    def remaining_seconds(self) -> float:
        try:
            now = self._read_clock()
        except RuntimeClockInvalid:
            return 0.0
        return max(0.0, self.deadline_at - now)

    @property
    def expired(self) -> bool:
        return self.remaining_seconds <= 0.0

    @property
    def clock_invalid(self) -> bool:
        with self._lock:
            return self._clock_invalid

    def check_deadline(self) -> None:
        now = self._read_clock()
        if now >= self.deadline_at:
            raise RuntimeDeadlineExceeded("runtime deadline exhausted")

    def reserve_model_call(self, *, purpose: BudgetPurpose = "agent") -> None:
        if purpose not in {"agent", "title", "other"}:
            raise ValueError("unsupported model call purpose")
        self.check_deadline()
        with self._lock:
            if self._model_calls >= self.max_model_calls:
                raise RuntimeBudgetExceeded("model", "runtime model call budget exhausted")
            if purpose == "title" and self._title_model_calls >= self.max_title_model_calls:
                raise RuntimeBudgetExceeded(
                    "model", "runtime title model call budget exhausted"
                )
            self._model_calls += 1
            if purpose == "title":
                self._title_model_calls += 1

    def try_reserve_model_call(self, *, purpose: BudgetPurpose = "agent") -> bool:
        try:
            self.reserve_model_call(purpose=purpose)
        except RuntimeBudgetError:
            return False
        return True

    def reserve_tool_call(self) -> None:
        self.check_deadline()
        with self._lock:
            if self._tool_calls >= self.max_tool_calls:
                raise RuntimeBudgetExceeded("tool", "runtime tool call budget exhausted")
            self._tool_calls += 1

    def try_reserve_tool_call(self) -> bool:
        try:
            self.reserve_tool_call()
        except RuntimeBudgetError:
            return False
        return True

    def record_event(self, event_bytes: int) -> None:
        if type(event_bytes) is not int or event_bytes < 0:
            raise ValueError("event_bytes must be a non-negative integer")
        self.check_deadline()
        with self._lock:
            if self._event_count >= self.max_events:
                raise RuntimeBudgetExceeded("event", "runtime event budget exhausted")
            if self._event_bytes + event_bytes > self.max_event_bytes:
                raise RuntimeBudgetExceeded(
                    "event_bytes", "runtime event byte budget exhausted"
                )
            self._event_count += 1
            self._event_bytes += event_bytes

    def try_record_event(self, event_bytes: int) -> bool:
        try:
            self.record_event(event_bytes)
        except RuntimeBudgetError:
            return False
        return True

    def snapshot(self) -> RuntimeBudgetSnapshot:
        try:
            now = self._read_clock()
        except RuntimeClockInvalid:
            with self._lock:
                now = self._last_clock
        with self._lock:
            return RuntimeBudgetSnapshot(
                admitted_at=self.admitted_at,
                deadline_at=self.deadline_at,
                now=now,
                model_calls=self._model_calls,
                title_model_calls=self._title_model_calls,
                tool_calls=self._tool_calls,
                event_count=self._event_count,
                event_bytes=self._event_bytes,
                max_model_calls=self.max_model_calls,
                max_title_model_calls=self.max_title_model_calls,
                max_tool_calls=self.max_tool_calls,
                max_events=self.max_events,
                max_event_bytes=self.max_event_bytes,
            )


__all__ = [
    "BudgetPurpose",
    "RuntimeBudget",
    "RuntimeBudgetError",
    "RuntimeBudgetExceeded",
    "RuntimeBudgetSnapshot",
    "RuntimeClockInvalid",
    "RuntimeDeadlineExceeded",
]
