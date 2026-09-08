from __future__ import annotations

from threading import Lock
from typing import Generic, Literal, TypeVar

SignalResult = Literal["emitted", "duplicate", "closed", "full", "degraded"]
RegistrationState = Literal["not_attempted", "registered", "registration_failed", "closed"]
T = TypeVar("T")


class RuntimeSignalSink(Generic[T]):
    """Capacity-one, non-blocking and fail-open runtime signal handoff."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._value: T | None = None
        self._emitted = False
        self._closed = False
        self._degraded = False

    def try_emit(self, value: T) -> SignalResult:
        try:
            with self._lock:
                if self._degraded:
                    return "degraded"
                if self._closed:
                    return "closed"
                if self._emitted:
                    return "duplicate"
                if self._value is not None:
                    return "full"
                self._value = value
                self._emitted = True
                return "emitted"
        except Exception:
            self._degraded = True
            return "degraded"

    def drain(self) -> T | None:
        try:
            with self._lock:
                value = self._value
                self._value = None
                return value
        except Exception:
            self._degraded = True
            return None

    def close(self) -> None:
        try:
            with self._lock:
                self._closed = True
                self._value = None
        except Exception:
            self._degraded = True
