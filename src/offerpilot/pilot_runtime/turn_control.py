"""Process control bound to a durable execution lease, never an execution queue."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Event, Lock, Thread
from time import monotonic
from typing import TypeVar

from offerpilot.pilot_control import (
    ExecutionLease, ExecutionScope, PilotControlRepository, TurnExecutionFenced, bind_execution_scope,
)
from .contracts import CancelReason, InvocationState, RuntimeInvocationControl
from .errors import RuntimeCancelled
from .event_sink import InMemoryRuntimeInvocationControl


ResultT = TypeVar("ResultT")


class DurableRuntimeInvocationControl:
    def __init__(self, repository: PilotControlRepository, lease: ExecutionLease | None = None,
                 *, monotonic_clock: Callable[[], float] = monotonic,
                 on_finished: Callable[[DurableRuntimeInvocationControl], None] | None = None) -> None:
        self.repository = repository
        self._inner = InMemoryRuntimeInvocationControl()
        self._clock = monotonic_clock
        self._valid_until = float("inf")
        self._heartbeat_stop = Event()
        self._heartbeat: Thread | None = None
        self._on_finished = on_finished
        self.execution_scope = ExecutionScope(repository, locally_active=self._locally_active, on_claim=self._claimed)
        if lease is not None:
            self.execution_scope.lease = lease
            self._claimed(lease)

    @property
    def lease(self) -> ExecutionLease | None:
        return self.execution_scope.lease

    def _claimed(self, _lease: ExecutionLease) -> None:
        self._valid_until = self._clock() + self.repository.lease_ms / 1000
        self._heartbeat = Thread(target=self._heartbeat_loop, name="pilot-execution-heartbeat", daemon=True)
        self._heartbeat.start()

    def _locally_active(self) -> bool:
        return (not self.execution_scope.revoked.is_set()
                and self._inner.state is InvocationState.ACTIVE
                and self._clock() < self._valid_until)

    @property
    def state(self) -> InvocationState:
        state = self._inner.state
        if state is not InvocationState.ACTIVE:
            return state
        if not self._locally_active():
            self.execution_scope.revoked.set()
            self._finish_safely("result_unknown")
            return InvocationState.CANCELLED
        lease = self.lease
        if lease is not None:
            try:
                active = self.repository.is_active(lease)
            except Exception:
                active = False
            if not active:
                self.execution_scope.revoked.set()
                return InvocationState.CANCELLED
        return state

    @property
    def cancel_reason(self) -> CancelReason | None:
        return self._inner.cancel_reason or (CancelReason.EXPLICIT_CANCEL if self.execution_scope.revoked.is_set() else None)

    def is_active(self) -> bool:
        return self.state is InvocationState.ACTIVE

    def request_cancel(self, reason: CancelReason) -> bool:
        changed = self._inner.request_cancel(reason)
        if changed:
            self.execution_scope.revoked.set()
            self._heartbeat_stop.set()
            self._finish_safely("interrupted")
        return changed

    def request_timeout(self) -> bool:
        changed = self._inner.request_timeout()
        if changed:
            self.execution_scope.revoked.set()
            self._heartbeat_stop.set()
            self._finish_safely("interrupted")
        return changed

    def mark_completed(self) -> bool:
        return self.is_active() and self._inner.mark_completed()

    def run_if_active(self, action: Callable[[], ResultT], *, allow_timeout: bool = False) -> tuple[bool, ResultT | None]:
        if allow_timeout and self._inner.state is InvocationState.TIMED_OUT:
            # Only Runtime's fixed timeout/delivery convergence uses this entry.
            # A fresh scope prevents a late worker from inheriting this grant.
            recovery = ExecutionScope(self.repository, self.lease, terminal_states=("interrupted", "result_unknown"),
                                      locally_active=lambda: self._inner.state is InvocationState.TIMED_OUT)
            if recovery.lease is None:
                return False, None
            with bind_execution_scope(recovery):
                return self._inner.run_if_active(action, allow_timeout=True)
        if not self.is_active():
            return False, None

        def execute() -> ResultT:
            with bind_execution_scope(self.execution_scope):
                try:
                    return action()
                except TurnExecutionFenced as exc:
                    raise RuntimeCancelled(CancelReason.EXPLICIT_CANCEL) from exc

        allowed, result = self._inner.run_if_active(execute, allow_timeout=allow_timeout)
        return allowed, result

    def _renew_once(self) -> bool:
        lease = self.lease
        if lease is None or self._heartbeat_stop.is_set() or not self._locally_active():
            return False
        deadline = self._valid_until
        try:
            renewed = self.repository.renew(lease)
        except Exception:
            renewed = False
        if not renewed or self._clock() >= deadline:
            self.execution_scope.revoked.set()
            self._finish_safely("result_unknown")
            return False
        self._valid_until = self._clock() + self.repository.lease_ms / 1000
        return True

    def _heartbeat_loop(self) -> None:
        interval = min(5.0, self.repository.lease_ms / 3000)
        while not self._heartbeat_stop.wait(interval):
            if not self._renew_once():
                return

    def _finish_safely(self, state: str) -> None:
        lease = self.lease
        if lease is not None:
            try:
                self.repository.finish(lease, state)
            except Exception:
                # The lease expires and all local protected commits remain
                # fenced. Never turn a cleanup failure into an execution retry.
                pass

    def finish(self, state: str) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat is not None:
            self._heartbeat.join(timeout=0.1)
        self._finish_safely("result_unknown" if self._clock() >= self._valid_until else state)
        if self._on_finished is not None:
            self._on_finished(self)

    def run_title_if_successful(self, action: Callable[[], object]) -> object | None:
        lease = self.lease
        if lease is None:
            return None
        current = self.repository.get_conversation_execution(lease.conversation_id)
        if (current is None or current["turn_id"] != lease.turn_id
                or current["execution_generation"] != lease.generation
                or current["state"] not in {"completed", "waiting_confirmation"}):
            return None
        scope = ExecutionScope(self.repository, lease, terminal_states=("completed", "waiting_confirmation"))
        with bind_execution_scope(scope):
            return action()


class TurnControlRegistry:
    """Finite-lifetime cancellation handles, with no scheduling or resumption."""

    def __init__(self, repository: PilotControlRepository) -> None:
        self.repository = repository
        self._lock = Lock()
        self._controls: set[DurableRuntimeInvocationControl] = set()
        self._closed = False

    def create(self, lease: ExecutionLease | None = None) -> DurableRuntimeInvocationControl:
        with self._lock:
            if self._closed:
                raise RuntimeError("Service is shutting down")
            control = DurableRuntimeInvocationControl(self.repository, lease, on_finished=self._remove)
            self._controls.add(control)
            return control

    def _remove(self, control: DurableRuntimeInvocationControl) -> None:
        with self._lock:
            self._controls.discard(control)

    def interrupted(self, turn_id: str, generation: int) -> None:
        with self._lock:
            controls = [control for control in self._controls if control.lease is not None
                        and control.lease.turn_id == turn_id and control.lease.generation == generation]
        for control in controls:
            control.request_cancel(CancelReason.EXPLICIT_CANCEL)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            controls = list(self._controls)
        for control in controls:
            control.request_cancel(CancelReason.TRANSPORT_ABORTED)
            control.finish("interrupted")


@contextmanager
def invocation_scope(control: RuntimeInvocationControl) -> Iterator[None]:
    if isinstance(control, DurableRuntimeInvocationControl):
        with bind_execution_scope(control.execution_scope):
            yield
    else:
        yield
