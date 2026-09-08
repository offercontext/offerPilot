from __future__ import annotations

import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from typing import Generic, TypeVar

from offerpilot.context_projector.contracts import ProjectionError

T = TypeVar("T")
R = TypeVar("R")
TOTAL_DEADLINE_SECONDS = 2.0
WORK_DEADLINE_SECONDS = 1.8
CLEANUP_RESERVE_SECONDS = 0.2
POOL_SIZE = 4
FETCH_BATCH_SIZE = 32


class _WriterFairGate:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active_readers = 0
        self._writer_active = False
        self._waiting_writers = 0

    @contextmanager
    def reader(self, deadline: float) -> Iterator[None]:
        with self._condition:
            while self._writer_active or self._waiting_writers:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._condition.wait(remaining):
                    raise ProjectionError("source_load_failed")
            self._active_readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._active_readers -= 1
                self._condition.notify_all()

    @contextmanager
    def writer(self, deadline: float) -> Iterator[None]:
        with self._condition:
            self._waiting_writers += 1
            try:
                while self._writer_active or self._active_readers:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not self._condition.wait(remaining):
                        raise ProjectionError("source_load_failed")
                self._writer_active = True
            finally:
                self._waiting_writers -= 1
        try:
            yield
        finally:
            with self._condition:
                self._writer_active = False
                self._condition.notify_all()


_COORDINATORS_LOCK = threading.Lock()
_COORDINATORS: dict[str, _WriterFairGate] = {}


def normalized_database_identity(database: str | Path) -> str:
    raw = str(database)
    if raw == ":memory:" or raw.startswith("file:"):
        return raw
    return str(Path(raw).resolve()).casefold()


def database_coordinator(database: str | Path) -> _WriterFairGate:
    identity = normalized_database_identity(database)
    with _COORDINATORS_LOCK:
        return _COORDINATORS.setdefault(identity, _WriterFairGate())


@dataclass
class ConnectionLease:
    connection: sqlite3.Connection = field(repr=False)
    generation: int
    work_deadline: float
    finished_event: threading.Event = field(default_factory=threading.Event, repr=False)
    watchdog_exited: threading.Event = field(default_factory=threading.Event, repr=False)


class WarmConnectionPool:
    def __init__(self, database: str | Path, *, size: int = POOL_SIZE):
        if size != POOL_SIZE:
            raise ValueError("context source pool size must be four")
        self.database = str(database)
        self._queue: Queue[sqlite3.Connection] = Queue(maxsize=size)
        self._generation = 0
        self._generation_lock = threading.Lock()
        self._reservation_condition = threading.Condition()
        self._reservation_tickets: deque[int] = deque()
        self._next_ticket = 0
        for _ in range(size):
            connection = sqlite3.connect(
                self.database, check_same_thread=False, isolation_level=None
            )
            connection.execute("PRAGMA journal_mode=DELETE")
            self._queue.put_nowait(connection)

    def reserve_nowait(self, work_deadline: float) -> ConnectionLease:
        with self._reservation_condition:
            self._next_ticket += 1
            ticket = self._next_ticket
            self._reservation_tickets.append(ticket)
            try:
                while self._reservation_tickets[0] != ticket or self._queue.empty():
                    remaining = work_deadline - time.monotonic()
                    if remaining <= 0 or not self._reservation_condition.wait(remaining):
                        raise ProjectionError("source_load_failed")
                self._reservation_tickets.popleft()
                connection = self._queue.get_nowait()
                self._reservation_condition.notify_all()
            except BaseException:
                try:
                    self._reservation_tickets.remove(ticket)
                except ValueError:
                    pass
                self._reservation_condition.notify_all()
                raise
        with self._generation_lock:
            self._generation += 1
            generation = self._generation
        return ConnectionLease(connection, generation, work_deadline)

    def release(self, lease: ConnectionLease, *, valid: bool) -> None:
        if valid:
            self._queue.put_nowait(lease.connection)
        else:
            try:
                lease.connection.close()
            finally:
                replacement = sqlite3.connect(
                    self.database, check_same_thread=False, isolation_level=None
                )
                replacement.execute("PRAGMA journal_mode=DELETE")
                self._queue.put_nowait(replacement)
        with self._reservation_condition:
            self._reservation_condition.notify_all()

    def close(self) -> None:
        while True:
            try:
                self._queue.get_nowait().close()
            except Empty:
                return


def fetch_rows(
    cursor: sqlite3.Cursor,
    *,
    deadline: float | None = None,
    max_rows: int = 4096,
) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            raise ProjectionError("source_load_failed")
        batch = cursor.fetchmany(FETCH_BATCH_SIZE)
        if not batch:
            break
        if len(rows) + len(batch) > max_rows:
            raise ProjectionError("source_load_failed")
        rows.extend(tuple(row) for row in batch)
    return tuple(rows)


class ContextSourceLoader(Generic[T, R]):
    """One-snapshot loader with a bounded SQLite connection lease."""

    def __init__(self, database: str | Path, *, pool: WarmConnectionPool | None = None):
        self._database = database
        self._pool = pool or WarmConnectionPool(database)
        self._coordinator = database_coordinator(database)

    def load(self, read: Callable[[sqlite3.Connection], T], freeze: Callable[[T], R]) -> R:
        started = time.monotonic()
        work_deadline = started + WORK_DEADLINE_SECONDS
        total_deadline = started + TOTAL_DEADLINE_SECONDS
        frozen: R
        with self._coordinator.reader(work_deadline):
            lease = self._pool.reserve_nowait(work_deadline)
            valid = False
            watchdog = threading.Thread(
                target=self._watchdog,
                args=(lease,),
                name=f"context-loader-{lease.generation}",
                daemon=True,
            )
            try:
                watchdog.start()
                remaining_ms = max(0, int((work_deadline - time.monotonic()) * 1000))
                lease.connection.execute(f"PRAGMA busy_timeout={remaining_ms}")

                def progress() -> int:
                    return int(time.monotonic() >= work_deadline)

                lease.connection.set_progress_handler(progress, 100)
                lease.connection.execute("BEGIN")
                raw = read(lease.connection)
                lease.connection.rollback()
                if time.monotonic() >= work_deadline:
                    raise ProjectionError("source_load_failed")
                frozen = freeze(raw)
                if time.monotonic() >= work_deadline:
                    raise ProjectionError("source_load_failed")
                valid = True
            except BaseException as exc:
                try:
                    lease.connection.rollback()
                except Exception:
                    valid = False
                if not isinstance(exc, Exception):
                    raise
                if isinstance(exc, ProjectionError):
                    raise
                raise ProjectionError("source_load_failed") from exc
            finally:
                lease.finished_event.set()
                remaining_cleanup = max(0.0, total_deadline - time.monotonic())
                if watchdog.ident is not None:
                    watchdog.join(min(CLEANUP_RESERVE_SECONDS, remaining_cleanup))
                if not lease.watchdog_exited.is_set():
                    valid = False
                try:
                    lease.connection.set_progress_handler(None, 0)
                    lease.connection.execute("PRAGMA busy_timeout=0")
                except Exception:
                    valid = False
                self._pool.release(lease, valid=valid)
        if time.monotonic() > total_deadline:
            raise ProjectionError("source_load_failed")
        return frozen

    @staticmethod
    def _watchdog(lease: ConnectionLease) -> None:
        try:
            remaining = max(0.0, lease.work_deadline - time.monotonic())
            if not lease.finished_event.wait(remaining):
                lease.connection.interrupt()
        finally:
            lease.watchdog_exited.set()

    @contextmanager
    def heartbeat_writer(self) -> Iterator[None]:
        deadline = time.monotonic() + WORK_DEADLINE_SECONDS
        with self._coordinator.writer(deadline):
            yield

    def close(self) -> None:
        self._pool.close()
