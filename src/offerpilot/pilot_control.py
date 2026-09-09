"""Durable Turn execution ownership and exact, idempotent interrupt commands."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from hashlib import sha256
from secrets import token_hex
from time import time
from threading import Event, Lock
from typing import Any
from uuid import UUID, uuid4
from weakref import WeakSet

from sqlalchemy import event, func, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import Conversation, PilotExecution, PilotInterruptCommand, PilotTurnOperation, PilotTurnRecord, WriteOperation


class TurnControlConflict(ValueError):
    """The command identity or conversation execution owner conflicts."""


class TurnExecutionFenced(RuntimeError):
    """An execution no longer has authority to commit."""


@dataclass(frozen=True, slots=True)
class ExecutionLease:
    turn_id: str
    conversation_id: int
    generation: int
    owner_token: str = field(repr=False)


@dataclass(eq=False)
class ExecutionScope:
    repository: PilotControlRepository = field(repr=False)
    lease: ExecutionLease | None = None
    locally_active: Callable[[], bool] = field(default=lambda: True, repr=False)
    revoked: Event = field(default_factory=Event, repr=False)
    on_claim: Callable[[ExecutionLease], None] | None = field(default=None, repr=False)
    terminal_states: tuple[str, ...] = ()


_EXECUTION_SCOPE: ContextVar[ExecutionScope | None] = ContextVar("pilot_execution_scope", default=None)
_FENCED_ENGINES: WeakSet[Engine] = WeakSet()
_INSTALL_LOCK = Lock()
_CONNECTION_SCOPE = "offerpilot_execution_scope"


@contextmanager
def bind_execution_scope(scope: ExecutionScope | None) -> Iterator[None]:
    token = _EXECUTION_SCOPE.set(scope)
    try:
        yield
    finally:
        _EXECUTION_SCOPE.reset(token)


def current_execution_scope() -> ExecutionScope | None:
    return _EXECUTION_SCOPE.get()


def claim_confirmation_execution(operation_id: str) -> bool:
    """Called only after live approval validation, never during terminal replay."""
    scope = _EXECUTION_SCOPE.get()
    if scope is None or scope.lease is not None:
        return True
    if scope.revoked.is_set() or not scope.locally_active():
        raise TurnExecutionFenced("Execution was cancelled before approval")
    lease = scope.repository.claim_confirmation(operation_id)
    if lease is None:
        return False
    scope.lease = lease
    if scope.on_claim is not None:
        scope.on_claim(lease)
    return True


def _commit_fence(connection: Connection) -> None:
    captured = connection.info.get(_CONNECTION_SCOPE)
    current = _EXECUTION_SCOPE.get()
    scope = captured or current
    if scope is None or scope.lease is None:
        return
    try:
        if captured is not None and current is not None and captured is not current:
            raise TurnExecutionFenced("Execution scope changed during a transaction")
        lease = scope.lease
        if scope.revoked.is_set() or not scope.locally_active():
            raise TurnExecutionFenced("Execution permission expired")
        now = scope.repository.now_ms()
        conditions = [
            PilotExecution.turn_id == lease.turn_id, PilotExecution.generation == lease.generation,
            PilotExecution.conversation_id == lease.conversation_id,
            PilotExecution.owner_token == lease.owner_token,
        ]
        if scope.terminal_states:
            # A separate, trusted source-only delivery may finish after timeout.
            # It cannot revive the worker or overwrite a newer execution.
            conditions.append(PilotExecution.state.in_(scope.terminal_states))
        else:
            conditions.extend([PilotExecution.state == "running", PilotExecution.renewed_at_ms <= now,
                               PilotExecution.lease_until_ms > now])
        result = connection.execute(update(PilotExecution).where(*conditions).values(
            lease_until_ms=PilotExecution.lease_until_ms,
        ).returning(
            PilotExecution.renewed_at_ms, PilotExecution.lease_until_ms, PilotExecution.conversation_sequence,
        ))
        row = result.first()
        checked_at = scope.repository.now_ms()
        if (row is None or (not scope.terminal_states and not row.renewed_at_ms <= checked_at < row.lease_until_ms)
                or scope.revoked.is_set() or not scope.locally_active()):
            raise TurnExecutionFenced("Execution permission expired")
        if scope.terminal_states:
            latest_sequence = connection.scalar(select(func.max(PilotExecution.conversation_sequence)).where(
                PilotExecution.conversation_id == lease.conversation_id,
            ))
            if row.conversation_sequence != latest_sequence:
                raise TurnExecutionFenced("A newer execution owns the conversation")
    except BaseException:
        scope.revoked.set()
        # A commit-event exception can deactivate SQLAlchemy's transaction
        # before Session.close gets to roll it back. Explicitly roll back the
        # DBAPI transaction so the pool cannot carry a rejected write forward.
        connection.connection.rollback()
        raise


def require_execution_before_handler(session: Session) -> None:
    """Linearize handler entry and Pending claim with stop in the same transaction."""
    scope = _EXECUTION_SCOPE.get()
    if scope is None:
        return
    if scope.lease is None or scope.terminal_states:
        raise TurnExecutionFenced("No execution permission for a handler")
    _commit_fence(session.connection())


def _install_fence(engine: Engine) -> None:
    with _INSTALL_LOCK:
        if engine in _FENCED_ENGINES:
            return

        def begin(connection: Connection) -> None:
            connection.info[_CONNECTION_SCOPE] = _EXECUTION_SCOPE.get()

        def rollback(connection: Connection) -> None:
            connection.info.pop(_CONNECTION_SCOPE, None)

        def checkin(_dbapi_connection: object, record: Any) -> None:
            record.info.pop(_CONNECTION_SCOPE, None)

        def invalidate(_dbapi_connection: object, record: Any, _exception: object) -> None:
            record.info.pop(_CONNECTION_SCOPE, None)

        event.listen(engine, "begin", begin)
        event.listen(engine, "commit", _commit_fence)
        event.listen(engine, "rollback", rollback)
        event.listen(engine, "checkin", checkin)
        event.listen(engine, "invalidate", invalidate)
        _FENCED_ENGINES.add(engine)


def _uuid(value: str) -> None:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("Expected a canonical UUID v4") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("Expected a canonical UUID v4")


def _digest(value: object) -> str:
    return sha256(json.dumps(value, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def execution_state(row: PilotExecution, now_ms: int) -> str:
    if row.state == "running" and not row.renewed_at_ms <= now_ms < row.lease_until_ms:
        return "result_unknown"
    return row.state


def reconcile_execution_state(row: PilotExecution, now_ms: int) -> str:
    """Record observed expiry under the caller's BEGIN IMMEDIATE transaction."""
    row.state = execution_state(row, now_ms)
    return row.state


class PilotControlRepository:
    def __init__(self, session_factory: sessionmaker[Session], *,
                 now_ms: Callable[[], int] | None = None, lease_ms: int = 30_000) -> None:
        if type(lease_ms) is not int or lease_ms <= 0:
            raise ValueError("lease_ms must be positive")
        self.session_factory = session_factory
        self.now_ms = now_ms or (lambda: int(time() * 1000))
        self.lease_ms = lease_ms
        _install_fence(session_factory.kw["bind"])

    @contextmanager
    def _session(self) -> Iterator[Session]:
        # Control and read-only reconciliation are independent trusted commands,
        # never writes delegated to the old Agent worker.
        with bind_execution_scope(None), self.session_factory() as session:
            yield session

    def reconcile_runtime_epoch(self, runtime_epoch: str) -> int:
        """Fence Runtime executions left running by an older process.

        A fresh process has no in-memory worker for an old ``runtime_epoch``.
        Mark those durable rows as ``result_unknown`` before workers accept
        new work, and keep the corresponding Turn from looking executable.
        Legacy rows, current-epoch rows, and already terminal rows are left
        untouched.  The operation is a trusted control-plane write and never
        invokes the old worker or provider.
        """

        if type(runtime_epoch) is not str or not runtime_epoch:
            raise ValueError("runtime_epoch must be a non-empty string")
        with self._session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            rows = list(session.scalars(select(PilotExecution).where(
                PilotExecution.protocol == "pilot-runtime-v1",
                PilotExecution.state == "running",
                PilotExecution.runtime_epoch.is_not(None),
                PilotExecution.runtime_epoch != runtime_epoch,
            )))
            turn_ids = {row.turn_id for row in rows}
            for row in rows:
                row.state = "result_unknown"
            if turn_ids:
                turns = session.scalars(select(PilotTurnRecord).where(
                    PilotTurnRecord.id.in_(turn_ids),
                ))
                for turn in turns:
                    turn.state = "incomplete"
            session.commit()
            return len(rows)

    @contextmanager
    def execution_scope(self, lease: ExecutionLease) -> Iterator[ExecutionScope]:
        scope = ExecutionScope(self, lease)
        with bind_execution_scope(scope):
            yield scope

    def _latest(self, session: Session, turn_id: str) -> PilotExecution | None:
        return session.scalar(select(PilotExecution).where(PilotExecution.turn_id == turn_id)
                              .order_by(PilotExecution.generation.desc()).limit(1))

    def _release_expired(self, session: Session, conversation_id: int, now: int) -> None:
        for row in session.scalars(select(PilotExecution).where(
            PilotExecution.conversation_id == conversation_id, PilotExecution.state == "running",
        )):
            if execution_state(row, now) == "running":
                raise TurnControlConflict("Conversation already has an active execution")
            row.state = "result_unknown"
        session.flush()

    def claim_start(
        self,
        turn_id: str,
        *,
        protocol: str = "legacy",
        runtime_epoch: str | None = None,
        submission_key: str | None = None,
        submission_request_id: str | None = None,
        source_refs_json: str = "[]",
    ) -> ExecutionLease:
        with self._session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            lease = self.claim_start_in_session(
                session,
                turn_id,
                protocol=protocol,
                runtime_epoch=runtime_epoch,
                submission_key=submission_key,
                submission_request_id=submission_request_id,
                source_refs_json=source_refs_json,
            )
            session.commit()
            return lease

    def claim_start_in_session(
        self,
        session: Session,
        turn_id: str,
        *,
        protocol: str = "legacy",
        runtime_epoch: str | None = None,
        submission_key: str | None = None,
        submission_request_id: str | None = None,
        source_refs_json: str = "[]",
    ) -> ExecutionLease:
        """The caller owns BEGIN IMMEDIATE and admission's atomic commit."""
        self._validate_runtime_metadata(
            protocol,
            runtime_epoch=runtime_epoch,
            submission_key=submission_key,
            submission_request_id=submission_request_id,
            source_refs_json=source_refs_json,
        )
        turn = session.get(PilotTurnRecord, turn_id)
        if turn is None:
            raise LookupError("Turn not found")
        if turn.state != "accepted" or self._latest(session, turn_id) is not None:
            raise TurnControlConflict("Turn was already claimed")
        conversation = session.get(Conversation, turn.conversation_id)
        if conversation is None or conversation.pending_operation_id:
            raise TurnControlConflict("Conversation has a pending confirmation")
        return self._claim(
            session,
            turn,
            1,
            protocol=protocol,
            runtime_epoch=runtime_epoch,
            submission_key=submission_key,
            submission_request_id=submission_request_id,
            source_refs_json=source_refs_json,
        )

    @staticmethod
    def _validate_runtime_metadata(
        protocol: str,
        *,
        runtime_epoch: str | None,
        submission_key: str | None,
        submission_request_id: str | None,
        source_refs_json: str,
    ) -> None:
        if type(protocol) is not str or not protocol:
            raise ValueError("protocol must be a non-empty string")
        if protocol == "legacy":
            if any(value is not None for value in (runtime_epoch, submission_key, submission_request_id)):
                raise ValueError("legacy execution cannot carry Runtime metadata")
            if source_refs_json != "[]":
                raise ValueError("legacy execution cannot carry source refs")
            return
        if protocol != "pilot-runtime-v1":
            raise ValueError("unsupported execution protocol")
        for field_name, value in (
            ("runtime_epoch", runtime_epoch),
            ("submission_key", submission_key),
            ("submission_request_id", submission_request_id),
        ):
            if type(value) is not str or not value:
                raise ValueError(f"{field_name} is required for Runtime execution")
        if type(source_refs_json) is not str:
            raise TypeError("source_refs_json must be a string")
        try:
            decoded = json.loads(source_refs_json)
        except json.JSONDecodeError as exc:
            raise ValueError("source_refs_json must be valid JSON") from exc
        if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
            raise ValueError("source_refs_json must be a JSON string array")

    def _claim(
        self,
        session: Session,
        turn: PilotTurnRecord,
        generation: int,
        *,
        protocol: str = "legacy",
        runtime_epoch: str | None = None,
        submission_key: str | None = None,
        submission_request_id: str | None = None,
        source_refs_json: str = "[]",
    ) -> ExecutionLease:
        self._validate_runtime_metadata(
            protocol,
            runtime_epoch=runtime_epoch,
            submission_key=submission_key,
            submission_request_id=submission_request_id,
            source_refs_json=source_refs_json,
        )
        now = self.now_ms()
        self._release_expired(session, turn.conversation_id, now)
        lease = ExecutionLease(turn.id, turn.conversation_id, generation, token_hex(32))
        sequence = session.scalar(select(func.max(PilotExecution.conversation_sequence)).where(
            PilotExecution.conversation_id == turn.conversation_id,
        )) or 0
        session.add(PilotExecution(
            turn_id=turn.id, conversation_id=turn.conversation_id, generation=generation,
            conversation_sequence=sequence + 1,
            owner_token=lease.owner_token, state="running", renewed_at_ms=now,
            lease_until_ms=now + self.lease_ms,
            protocol=protocol,
            runtime_epoch=runtime_epoch,
            submission_key=submission_key,
            submission_request_id=submission_request_id,
            source_refs_json=source_refs_json,
        ))
        turn.state = "started"
        return lease

    def claim_confirmation(
        self,
        operation_id: str,
        *,
        expected_turn_id: str | None = None,
        protocol: str = "legacy",
        runtime_epoch: str | None = None,
        submission_key: str | None = None,
        submission_request_id: str | None = None,
        source_refs_json: str = "[]",
    ) -> ExecutionLease | None:
        self._validate_runtime_metadata(
            protocol,
            runtime_epoch=runtime_epoch,
            submission_key=submission_key,
            submission_request_id=submission_request_id,
            source_refs_json=source_refs_json,
        )
        with self._session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            operation = session.get(WriteOperation, operation_id)
            if operation is None or operation.operation_role != "primary":
                raise TurnControlConflict("Approval does not identify a primary operation")
            if operation.status != "proposed":
                return None
            conversation = session.get(Conversation, operation.conversation_id)
            if conversation is None or conversation.archived_at is not None or (
                conversation.pending_operation_id != operation.id
                or conversation.pending_tool_call_id != operation.tool_call_id
            ):
                raise TurnControlConflict("Approval no longer owns Pending")
            binding = session.get(PilotTurnOperation, operation_id)
            if binding is None:
                # An old Pending has a precise operation identity even when
                # its historical user-message relationship is unavailable.
                turn = PilotTurnRecord(id=str(uuid4()), conversation_id=conversation.id,
                                       state="incomplete", source_versions_json="{}")
                session.add(turn)
                session.flush()
                session.add(PilotTurnOperation(operation_id=operation_id, turn_id=turn.id))
            else:
                existing_turn = session.get(PilotTurnRecord, binding.turn_id)
                if existing_turn is None or existing_turn.conversation_id != conversation.id:
                    raise TurnControlConflict("Approval belongs to another conversation")
                turn = existing_turn
            if expected_turn_id is not None and turn.id != expected_turn_id:
                raise TurnControlConflict("Approval belongs to another turn")
            previous = self._latest(session, turn.id)
            lease = self._claim(
                session,
                turn,
                1 if previous is None else previous.generation + 1,
                protocol=protocol,
                runtime_epoch=runtime_epoch,
                submission_key=submission_key,
                submission_request_id=submission_request_id,
                source_refs_json=source_refs_json,
            )
            session.commit()
            return lease

    def is_active(self, lease: ExecutionLease) -> bool:
        with self._session() as session:
            row = session.get(PilotExecution, (lease.turn_id, lease.generation))
            return bool(row is not None and row.owner_token == lease.owner_token
                        and row.conversation_id == lease.conversation_id
                        and execution_state(row, self.now_ms()) == "running")

    def renew(self, lease: ExecutionLease) -> bool:
        with self._session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(PilotExecution, (lease.turn_id, lease.generation))
            now = self.now_ms()
            if (row is None or row.owner_token != lease.owner_token
                    or row.conversation_id != lease.conversation_id or row.state != "running"):
                return False
            active = execution_state(row, now) == "running"
            if active:
                row.renewed_at_ms = now
                row.lease_until_ms = now + self.lease_ms
            else:
                row.state = "result_unknown"
            session.commit()
            return active

    @staticmethod
    def _validate_finish_state(state: str) -> None:
        if state not in {"waiting_confirmation", "completed", "failed", "interrupted", "result_unknown"}:
            raise ValueError("Invalid execution outcome")

    def _finish_in_session(self, session: Session, lease: ExecutionLease, state: str) -> None:
        row = session.get(PilotExecution, (lease.turn_id, lease.generation))
        if (row is None or row.owner_token != lease.owner_token
                or row.conversation_id != lease.conversation_id or row.state != "running"):
            return
        row.state = state if execution_state(row, self.now_ms()) == "running" else "result_unknown"
        conversation = session.get(Conversation, lease.conversation_id)
        if row.state == "completed" and conversation is not None and conversation.pending_operation_id:
            binding = session.get(PilotTurnOperation, conversation.pending_operation_id)
            if binding is not None and binding.turn_id == lease.turn_id:
                row.state = "waiting_confirmation"
        turn = session.get(PilotTurnRecord, lease.turn_id)
        if turn is not None:
            turn.state = {"waiting_confirmation": "started", "result_unknown": "incomplete"}.get(row.state, row.state)

    def finish(self, lease: ExecutionLease, state: str) -> None:
        self._validate_finish_state(state)
        with self._session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            self._finish_in_session(session, lease, state)
            session.commit()

    def try_finish(self, lease: ExecutionLease, state: str) -> bool:
        """Best-effort terminal update that never waits behind an Agent write.

        Timeout/cancellation is a transport boundary.  Its caller must be able
        to return while the worker may still hold the database transaction.  A
        regular ``BEGIN IMMEDIATE`` honors SQLite's connection busy timeout and
        can therefore hold the HTTP response for seconds.  Use the raw driver
        connection for this one probe so a busy result can be rolled back and
        the pooled connection's original timeout restored without invalidating
        it.  A later lease expiry/recovery remains authoritative when this
        probe loses the lock race.
        """

        self._validate_finish_state(state)
        engine = self.session_factory.kw.get("bind")
        if not isinstance(engine, Engine):
            raise RuntimeError("database engine unavailable")
        # Hold the checked-out connection until the Session has completed and
        # the raw PRAGMA has been restored.  Restoring it after ``_session``
        # returns could mutate a different request's connection from the pool.
        with bind_execution_scope(None), engine.connect() as connection:
            raw = getattr(connection.connection, "driver_connection", None)
            if raw is None:
                raw = getattr(connection.connection, "connection", None)
            if raw is None:
                raise RuntimeError("database driver connection unavailable")
            previous_timeout = int(raw.execute("PRAGMA busy_timeout").fetchone()[0] or 0)
            raw.execute("PRAGMA busy_timeout=0")
            try:
                try:
                    raw.execute("BEGIN IMMEDIATE")
                except sqlite3.OperationalError as exc:
                    raw.rollback()
                    code = getattr(exc, "sqlite_errorcode", None)
                    # These constants are absent from older supported Python
                    # sqlite3 stubs; SQLite's stable primary error codes are
                    # still 5 (BUSY) and 6 (LOCKED).
                    busy_codes = {
                        getattr(sqlite3, "SQLITE_BUSY", 5),
                        getattr(sqlite3, "SQLITE_LOCKED", 6),
                    }
                    if code in busy_codes:
                        return False
                    raise
                with self.session_factory(bind=connection) as session:
                    try:
                        self._finish_in_session(session, lease, state)
                        session.commit()
                        return True
                    except BaseException:
                        session.rollback()
                        raise
            finally:
                raw.execute(f"PRAGMA busy_timeout={previous_timeout}")

    def get_conversation_execution(self, conversation_id: int) -> dict[str, Any] | None:
        with self._session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.scalar(select(PilotExecution).where(PilotExecution.conversation_id == conversation_id)
                                 .order_by(PilotExecution.conversation_sequence.desc()).limit(1))
            if row is None:
                return None
            reconcile_execution_state(row, self.now_ms())
            result = self._view(row)
            session.commit()
            return result

    def _view(self, row: PilotExecution) -> dict[str, Any]:
        payload = {
            "turn_id": row.turn_id,
            "conversation_id": row.conversation_id,
            "execution_generation": row.generation,
            "state": row.state,
        }
        if row.protocol == "pilot-runtime-v1":
            payload.update(
                {
                    "protocol": row.protocol,
                    "protocol_version": row.protocol,
                    "runtime_epoch": row.runtime_epoch,
                    "submission_key": row.submission_key,
                    "submission_request_id": row.submission_request_id,
                    "source_refs": json.loads(row.source_refs_json or "[]"),
                }
            )
        return payload

    def get_execution(self, turn_id: str) -> dict[str, Any] | None:
        with self._session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = self._latest(session, turn_id)
            if row is None:
                return None
            reconcile_execution_state(row, self.now_ms())
            result = self._view(row)
            session.commit()
            return result

    def get_runtime_execution(
        self,
        turn_id: str,
        generation: int | None = None,
    ) -> dict[str, Any] | None:
        """Read the exact Runtime execution metadata for one Turn."""

        with self._session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            if generation is None:
                row = self._latest(session, turn_id)
            else:
                row = session.get(PilotExecution, (turn_id, generation))
            if row is None or row.protocol != "pilot-runtime-v1":
                session.commit()
                return None
            reconcile_execution_state(row, self.now_ms())
            result = self._view(row)
            session.commit()
            return result

    def get_runtime_execution_by_request(self, request_id: str) -> dict[str, Any] | None:
        """Look up only the exact Runtime submission request identity."""

        if type(request_id) is not str or not request_id:
            raise ValueError("request_id must be a non-empty string")
        with self._session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.scalar(
                select(PilotExecution)
                .where(
                    PilotExecution.protocol == "pilot-runtime-v1",
                    PilotExecution.submission_request_id == request_id,
                )
                .order_by(PilotExecution.conversation_sequence.desc())
                .limit(1)
            )
            if row is None:
                session.commit()
                return None
            reconcile_execution_state(row, self.now_ms())
            result = self._view(row)
            session.commit()
            return result

    def interrupt(self, command_id: str, turn_id: str, expected_generation: int) -> dict[str, Any]:
        _uuid(command_id)
        _uuid(turn_id)
        if type(expected_generation) is not int or expected_generation <= 0:
            raise ValueError("expected_generation must be positive")
        key = _digest(["pilot-interrupt-v1", command_id])
        digest = _digest([turn_id, expected_generation])
        with self._session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            receipt = session.get(PilotInterruptCommand, key)
            if receipt is not None:
                if receipt.request_digest != digest:
                    raise TurnControlConflict("Command ID belongs to another request")
                return dict(json.loads(receipt.result_json))
            if session.get(PilotTurnRecord, turn_id) is None:
                raise LookupError("Turn not found")
            row = self._latest(session, turn_id)
            if row is None:
                status = "no_active_execution"
            elif row.generation != expected_generation:
                status = "generation_changed"
            else:
                state = execution_state(row, self.now_ms())
                if state in {"running", "stopped"}:
                    row.state = status = "stopped"
                elif state == "result_unknown":
                    row.state = status = "result_unknown"
                elif state == "waiting_confirmation":
                    status = "no_active_execution"
                else:
                    status = "already_ended"
            result = {"command_id": command_id, "turn_id": turn_id,
                      "execution_generation": expected_generation, "status": status}
            session.add(PilotInterruptCommand(command_key=key, request_digest=digest,
                                             turn_id=turn_id, result_json=json.dumps(result)))
            session.commit()
            return result
