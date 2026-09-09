"""Explicit, current Readiness Signal selections for one application chat.

Readiness is a user-selected source.  The conversation stores the exact target
Event, Resume, and ordered Signal Version IDs that the user confirmed.  The
selection is revalidated by the existing Preparation owner every time it is
consumed; a changed source, target, or withdrawn Signal therefore stops the
injection on the next projection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
import sqlite3
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from sqlalchemy import ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from offerpilot.context_projector.contracts import ProjectionError, canonical_json
from offerpilot.models import Application, Base, Conversation
from offerpilot.review_readiness.preparation_selection import (
    MAX_READINESS_FEEDBACK_ENVELOPE_BYTES,
    PreparationReadinessSelectionError,
    PreparationReadinessSelectionLoader,
)
from .contracts import ContextPolicies
from .readonly import readonly_session


_MAX_INT64 = 2**63 - 1
_MAX_SELECTED_VERSIONS = 8
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_MAX_RECEIPT_BYTES = 8 * 1024


class ConversationReadinessContext(Base):
    """The latest explicit selection for a conversation.

    Empty ``version_ids_json`` and ``selection_fingerprint`` mean that the
    binding is withdrawn.  Target and Resume remain as audit-friendly last
    selection values and are never consumed while the fingerprint is empty.
    """

    __tablename__ = "conversation_readiness_contexts"
    __table_args__ = (
        Index("idx_conversation_readiness_contexts_application", "application_id"),
    )

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    application_id: Mapped[int] = mapped_column(Integer, nullable=False)
    scope_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    target_event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    resume_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    selection_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    mutation_id: Mapped[str] = mapped_column(String(36), nullable=False)


class ConversationReadinessMutation(Base):
    """Durable idempotency receipt for a readiness bind or clear mutation."""

    __tablename__ = "conversation_readiness_mutations"
    __table_args__ = (
        Index("idx_conversation_readiness_mutations_conversation", "conversation_id"),
    )

    mutation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ReadinessContextRequest(BaseModel):
    """The complete selection supplied by an explicitly confirmed user action."""

    model_config = ConfigDict(extra="forbid")

    mutation_id: UUID
    expected_revision: StrictInt = Field(ge=0, le=_MAX_INT64)
    confirmed: StrictBool
    target_event_id: StrictInt = Field(gt=0, le=_MAX_INT64)
    resume_id: StrictInt = Field(gt=0, le=_MAX_INT64)
    ordered_version_ids: list[StrictInt] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_explicit_selection(self) -> ReadinessContextRequest:
        if self.confirmed is not True:
            raise ValueError("readiness_confirmation_required")
        values = self.ordered_version_ids
        if any(type(value) is not int or value < 1 or value > _MAX_INT64 for value in values):
            raise ValueError("readiness_selection_invalid")
        if len(set(values)) != len(values):
            raise ValueError("readiness_selection_invalid")
        return self


class ReadinessContextClearRequest(BaseModel):
    """An explicit, idempotent withdrawal of the current readiness binding."""

    model_config = ConfigDict(extra="forbid")

    mutation_id: UUID
    expected_revision: StrictInt = Field(ge=0, le=_MAX_INT64)
    confirmed: StrictBool

    @model_validator(mode="after")
    def validate_confirmation(self) -> ReadinessContextClearRequest:
        if self.confirmed is not True:
            raise ValueError("readiness_confirmation_required")
        return self


class ReadinessContextConflict(ValueError):
    """A caller CAS or mutation identity conflict."""


class ReadinessContextUnavailable(ValueError):
    """The conversation or selected source is no longer available."""


@dataclass(frozen=True, slots=True)
class ReadinessContextBinding:
    """Frozen source identity passed from pre-dispatch admission to loading."""

    conversation_id: int
    application_id: int
    target_event_id: int
    resume_id: int
    ordered_version_ids: tuple[int, ...]
    scope_revision: int
    revision: int
    selection_fingerprint: str

    def __post_init__(self) -> None:
        for field_name in (
            "conversation_id",
            "application_id",
            "target_event_id",
            "resume_id",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 1 or value > _MAX_INT64:
                raise ValueError(f"readiness_{field_name}_invalid")
        for field_name in ("scope_revision", "revision"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0 or value > _MAX_INT64:
                raise ValueError(f"readiness_{field_name}_invalid")
        if type(self.ordered_version_ids) is not tuple:
            raise ValueError("readiness_selection_invalid")
        if len(self.ordered_version_ids) > _MAX_SELECTED_VERSIONS:
            raise ValueError("readiness_selection_invalid")
        if any(
            type(value) is not int or value < 1 or value > _MAX_INT64
            for value in self.ordered_version_ids
        ) or len(set(self.ordered_version_ids)) != len(self.ordered_version_ids):
            raise ValueError("readiness_selection_invalid")
        if not _SHA256_PATTERN.fullmatch(self.selection_fingerprint):
            raise ValueError("readiness_selection_fingerprint_invalid")

    @property
    def source_revision(self) -> str:
        """Stable identity for projector diagnostics and frozen source records."""

        return (
            "readiness:v1:"
            f"scope={self.scope_revision}:revision={self.revision}:"
            f"selection={self.selection_fingerprint}"
        )


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value < 1 or value > _MAX_INT64:
        raise ReadinessContextUnavailable(f"readiness_{field}_invalid")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if type(value) is not int or value < 0 or value > _MAX_INT64:
        raise ReadinessContextUnavailable(f"readiness_{field}_invalid")
    return value


def _canonical_uuid(value: object, field: str = "mutation_id") -> str:
    if type(value) is not str or not _UUID_PATTERN.fullmatch(value):
        raise ReadinessContextUnavailable(f"readiness_{field}_invalid")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError) as exc:
        raise ReadinessContextUnavailable(f"readiness_{field}_invalid") from exc
    if str(parsed) != value:
        raise ReadinessContextUnavailable(f"readiness_{field}_invalid")
    return value


def _selection_fingerprint(value: object, *, allow_empty: bool = False) -> str:
    if allow_empty and value == "":
        return ""
    if type(value) is not str or not _SHA256_PATTERN.fullmatch(value):
        raise ReadinessContextUnavailable("readiness_selection_fingerprint_invalid")
    return value


def _ordered_ids(value: object, *, allow_empty: bool = True) -> tuple[int, ...]:
    if type(value) is not list or len(value) > _MAX_SELECTED_VERSIONS:
        raise ReadinessContextUnavailable("readiness_selection_invalid")
    values = tuple(value)
    if any(type(item) is not int or item < 1 or item > _MAX_INT64 for item in values):
        raise ReadinessContextUnavailable("readiness_selection_invalid")
    if len(set(values)) != len(values):
        raise ReadinessContextUnavailable("readiness_selection_invalid")
    if not allow_empty and not values:
        raise ReadinessContextUnavailable("readiness_selection_invalid")
    return values


def _request_fingerprint(
    conversation_id: int,
    operation: str,
    command: ReadinessContextRequest | ReadinessContextClearRequest,
) -> str:
    payload = {
        "schema_version": 1,
        "conversation_id": conversation_id,
        "operation": operation,
        **command.model_dump(mode="json"),
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _result_from_receipt(receipt: ConversationReadinessMutation) -> dict[str, object]:
    try:
        value: Any = json.loads(receipt.result_json)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ReadinessContextUnavailable("readiness_receipt_invalid") from exc
    if type(value) is not dict:
        raise ReadinessContextUnavailable("readiness_receipt_invalid")
    return cast(dict[str, object], value)


def _store_receipt(
    session: Session,
    *,
    command: ReadinessContextRequest | ReadinessContextClearRequest,
    conversation_id: int,
    operation: str,
    request_fingerprint: str,
    result: dict[str, object],
) -> None:
    result_json = canonical_json(result).decode("utf-8")
    if len(result_json.encode("utf-8")) > _MAX_RECEIPT_BYTES:
        raise ReadinessContextUnavailable("readiness_receipt_too_large")
    session.add(
        ConversationReadinessMutation(
            mutation_id=str(command.mutation_id),
            conversation_id=conversation_id,
            operation=operation,
            request_fingerprint=request_fingerprint,
            result_json=result_json,
        )
    )


def _receipt_or_conflict(
    session: Session,
    *,
    command: ReadinessContextRequest | ReadinessContextClearRequest,
    conversation_id: int,
    operation: str,
    request_fingerprint: str,
) -> dict[str, object] | None:
    receipt = session.get(ConversationReadinessMutation, str(command.mutation_id))
    if receipt is None:
        return None
    if (
        receipt.conversation_id != conversation_id
        or receipt.operation != operation
        or receipt.request_fingerprint != request_fingerprint
    ):
        raise ReadinessContextConflict("readiness_mutation_conflict")
    return _result_from_receipt(receipt)


def application_conversation(session: Session, conversation_id: int) -> tuple[Conversation, Application]:
    """Resolve the only supported owner scope using the canonical context ref."""

    conversation_id = _positive_int(conversation_id, "conversation_id")
    conversation = session.get(Conversation, conversation_id)
    if conversation is None or conversation.archived_at is not None:
        raise ReadinessContextUnavailable("readiness_conversation_unavailable")
    if conversation.context_type != "application":
        raise ReadinessContextUnavailable("readiness_requires_application_conversation")
    context_ref = conversation.context_ref
    if type(context_ref) is not str or not re.fullmatch(r"[1-9][0-9]*", context_ref):
        raise ReadinessContextUnavailable("readiness_application_scope_invalid")
    application_id = _positive_int(int(context_ref), "application_id")
    if str(application_id) != context_ref:
        raise ReadinessContextUnavailable("readiness_application_scope_invalid")
    application = session.get(Application, application_id)
    if application is None or application.deleted_at is not None:
        raise ReadinessContextUnavailable("readiness_application_unavailable")
    _nonnegative_int(conversation.scope_revision, "scope_revision")
    return conversation, application


def _binding_from_row(
    conversation: Conversation,
    application: Application,
    row: ConversationReadinessContext | None,
) -> ReadinessContextBinding | None:
    if row is None:
        return None
    try:
        decoded = json.loads(row.version_ids_json)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ReadinessContextUnavailable("readiness_selection_invalid") from exc
    ordered_ids = _ordered_ids(decoded, allow_empty=True)
    if row.application_id != application.id:
        raise ReadinessContextUnavailable("readiness_context_scope_changed")
    if row.scope_revision != conversation.scope_revision:
        raise ReadinessContextUnavailable("readiness_context_scope_changed")
    _positive_int(row.target_event_id, "target_event_id")
    _positive_int(row.resume_id, "resume_id")
    revision = _positive_int(row.revision, "revision")
    _canonical_uuid(row.mutation_id)
    if row.selection_fingerprint == "":
        if ordered_ids:
            raise ReadinessContextUnavailable("readiness_selection_invalid")
        return None
    fingerprint = _selection_fingerprint(row.selection_fingerprint)
    return ReadinessContextBinding(
        conversation_id=conversation.id,
        application_id=application.id,
        target_event_id=row.target_event_id,
        resume_id=row.resume_id,
        ordered_version_ids=ordered_ids,
        scope_revision=conversation.scope_revision,
        revision=revision,
        selection_fingerprint=fingerprint,
    )


def _public_context(
    conversation: Conversation,
    application: Application,
    row: ConversationReadinessContext | None,
    binding: ReadinessContextBinding | None,
) -> dict[str, object]:
    if row is None:
        return {
            "schema_version": 1,
            "state": "not_applicable",
            "conversation_id": conversation.id,
            "application_id": application.id,
            "target_event_id": None,
            "resume_id": None,
            "ordered_version_ids": [],
            "selection_fingerprint": "",
            "scope_revision": conversation.scope_revision,
            "revision": 0,
        }
    return {
        "schema_version": 1,
        "state": "confirmed" if binding is not None else "withdrawn",
        "conversation_id": conversation.id,
        "application_id": application.id,
        "target_event_id": row.target_event_id,
        "resume_id": row.resume_id,
        "ordered_version_ids": list(binding.ordered_version_ids) if binding else [],
        "selection_fingerprint": binding.selection_fingerprint if binding else "",
        "scope_revision": conversation.scope_revision,
        "revision": row.revision,
    }


def _readiness_policy_enabled(session: Session) -> bool:
    row = session.execute(
        text("SELECT settings_json FROM context_contributor_settings WHERE id=1")
    ).scalar_one_or_none()
    if row is None:
        return False
    try:
        policies = ContextPolicies.model_validate_json(row)
    except (TypeError, ValueError) as exc:
        raise ReadinessContextUnavailable("readiness_policy_invalid") from exc
    return policies.confirmed_readiness.enabled


class ReadinessContextRepository:
    """CAS and receipt owner for conversation Readiness selections."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    @staticmethod
    def load_binding_in_session(
        session: Session,
        conversation_id: int,
        *,
        respect_policy: bool = False,
    ) -> ReadinessContextBinding | None:
        """Load a binding from a caller-owned read session.

        Workspace/global conversations deliberately have no Readiness owner.
        Return ``None`` for those scopes before looking at any Readiness Signal
        rows; application conversations use the canonical scope resolver and
        validate the stored binding against that application.

        ``respect_policy`` is used by Runtime admission.  It checks the
        contributor switch before resolving the conversation, so a disabled
        Readiness contributor cannot make an ordinary chat fail because an old
        binding points at a changed or deleted application.
        """

        normalized_id = _positive_int(conversation_id, "conversation_id")
        if respect_policy and not _readiness_policy_enabled(session):
            return None
        conversation = session.get(Conversation, normalized_id)
        if conversation is None or conversation.archived_at is not None:
            raise ReadinessContextUnavailable("readiness_conversation_unavailable")
        if conversation.context_type != "application":
            return None
        conversation, application = application_conversation(session, normalized_id)
        row = session.get(ConversationReadinessContext, conversation.id)
        return _binding_from_row(conversation, application, row)

    @staticmethod
    def capture_enabled_binding_in_session(
        session: Session,
        conversation_id: int,
    ) -> ReadinessContextBinding | None:
        """Capture a binding only when the Readiness contributor is enabled."""

        return ReadinessContextRepository.load_binding_in_session(
            session,
            conversation_id,
            respect_policy=True,
        )

    def load_binding(self, conversation_id: int) -> ReadinessContextBinding | None:
        with self.sessions() as session:
            return self.load_binding_in_session(session, conversation_id)

    def read(self, conversation_id: int) -> dict[str, object]:
        """Return transport-safe identity without Signal content or Evidence."""

        with self.sessions() as session:
            conversation, application = application_conversation(session, conversation_id)
            row = session.get(ConversationReadinessContext, conversation.id)
            binding = _binding_from_row(conversation, application, row)
            return _public_context(conversation, application, row, binding)

    def confirm(self, conversation_id: int, command: ReadinessContextRequest) -> dict[str, object]:
        operation = "confirm"
        request_fingerprint = _request_fingerprint(conversation_id, operation, command)
        with self.sessions() as session:
            try:
                session.execute(text("BEGIN IMMEDIATE"))
                conversation, application = application_conversation(session, conversation_id)
                replay = _receipt_or_conflict(
                    session,
                    command=command,
                    conversation_id=conversation.id,
                    operation=operation,
                    request_fingerprint=request_fingerprint,
                )
                if replay is not None:
                    session.rollback()
                    return replay
                existing = session.get(ConversationReadinessContext, conversation.id)
                if existing is not None:
                    current = _binding_from_row(conversation, application, existing)
                    current_revision = existing.revision
                    if current is None:
                        _nonnegative_int(current_revision, "revision")
                else:
                    current_revision = 0
                if command.expected_revision != current_revision:
                    raise ReadinessContextConflict("readiness_context_changed")
                try:
                    selection = PreparationReadinessSelectionLoader(session).load(
                        application_id=application.id,
                        target_event_id=command.target_event_id,
                        resume_id=command.resume_id,
                        ordered_version_ids=tuple(command.ordered_version_ids),
                    )
                except PreparationReadinessSelectionError as exc:
                    raise ReadinessContextUnavailable(exc.code) from exc
                next_revision = _positive_int(current_revision + 1, "revision")
                if existing is None:
                    existing = ConversationReadinessContext(
                        conversation_id=conversation.id,
                        application_id=application.id,
                        scope_revision=conversation.scope_revision,
                        target_event_id=command.target_event_id,
                        resume_id=command.resume_id,
                        version_ids_json="[]",
                        selection_fingerprint="",
                        revision=0,
                        mutation_id=str(command.mutation_id),
                    )
                    session.add(existing)
                existing.application_id = application.id
                existing.scope_revision = conversation.scope_revision
                existing.target_event_id = command.target_event_id
                existing.resume_id = command.resume_id
                existing.version_ids_json = json.dumps(
                    list(command.ordered_version_ids),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                existing.selection_fingerprint = selection.selection_fingerprint
                existing.revision = next_revision
                existing.mutation_id = str(command.mutation_id)
                result = {
                    "schema_version": 1,
                    "state": "confirmed",
                    "conversation_id": conversation.id,
                    "application_id": application.id,
                    "target_event_id": command.target_event_id,
                    "resume_id": command.resume_id,
                    "ordered_version_ids": list(command.ordered_version_ids),
                    "selection_fingerprint": selection.selection_fingerprint,
                    "scope_revision": conversation.scope_revision,
                    "revision": next_revision,
                }
                _store_receipt(
                    session,
                    command=command,
                    conversation_id=conversation.id,
                    operation=operation,
                    request_fingerprint=request_fingerprint,
                    result=result,
                )
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise

    def clear(
        self,
        conversation_id: int,
        command: ReadinessContextClearRequest,
    ) -> dict[str, object]:
        operation = "clear"
        request_fingerprint = _request_fingerprint(conversation_id, operation, command)
        with self.sessions() as session:
            try:
                session.execute(text("BEGIN IMMEDIATE"))
                conversation, application = application_conversation(session, conversation_id)
                replay = _receipt_or_conflict(
                    session,
                    command=command,
                    conversation_id=conversation.id,
                    operation=operation,
                    request_fingerprint=request_fingerprint,
                )
                if replay is not None:
                    session.rollback()
                    return replay
                existing = session.get(ConversationReadinessContext, conversation.id)
                if existing is not None:
                    _binding_from_row(conversation, application, existing)
                    current_revision = _nonnegative_int(existing.revision, "revision")
                else:
                    current_revision = 0
                if command.expected_revision != current_revision:
                    raise ReadinessContextConflict("readiness_context_changed")
                next_revision = current_revision
                if existing is not None:
                    next_revision = _positive_int(current_revision + 1, "revision")
                    existing.version_ids_json = "[]"
                    existing.selection_fingerprint = ""
                    existing.scope_revision = conversation.scope_revision
                    existing.application_id = application.id
                    existing.revision = next_revision
                    existing.mutation_id = str(command.mutation_id)
                result = {
                    "schema_version": 1,
                    "state": "withdrawn",
                    "conversation_id": conversation.id,
                    "application_id": application.id,
                    "scope_revision": conversation.scope_revision,
                    "revision": next_revision,
                }
                _store_receipt(
                    session,
                    command=command,
                    conversation_id=conversation.id,
                    operation=operation,
                    request_fingerprint=request_fingerprint,
                    result=result,
                )
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise

    def withdraw(
        self,
        conversation_id: int,
        expected_revision: int,
        *,
        mutation_id: UUID | None = None,
        confirmed: bool = True,
    ) -> dict[str, object]:
        """Compatibility helper; transport callers should use ``clear``."""

        command = ReadinessContextClearRequest(
            mutation_id=mutation_id or UUID(int=0),
            expected_revision=expected_revision,
            confirmed=confirmed,
        )
        return self.clear(conversation_id, command)


def _raw_scope_and_row(
    connection: sqlite3.Connection,
    binding: ReadinessContextBinding,
) -> tuple[tuple[object, ...], tuple[object, ...]]:
    scope = connection.execute(
        """SELECT c.context_type, c.context_ref, c.archived_at, c.scope_revision,
                   a.id, a.deleted_at
            FROM conversations c LEFT JOIN applications a ON a.id=CAST(c.context_ref AS INTEGER)
            WHERE c.id=?""",
        (binding.conversation_id,),
    ).fetchone()
    if scope is None or scope[2] is not None:
        raise ProjectionError("readiness_context_scope_unavailable")
    if scope[0] != "application" or type(scope[1]) is not str:
        raise ProjectionError("readiness_context_scope_changed")
    context_ref = scope[1]
    if not re.fullmatch(r"[1-9][0-9]*", context_ref) or int(context_ref) != binding.application_id:
        raise ProjectionError("readiness_context_scope_changed")
    if scope[3] != binding.scope_revision or scope[4] != binding.application_id or scope[5] is not None:
        raise ProjectionError("readiness_context_scope_changed")
    row = connection.execute(
        """SELECT application_id, scope_revision, target_event_id, resume_id,
                          version_ids_json, selection_fingerprint, revision
            FROM conversation_readiness_contexts WHERE conversation_id=?""",
        (binding.conversation_id,),
    ).fetchone()
    if row is None:
        raise ProjectionError("readiness_context_unavailable")
    if (
        row[0] != binding.application_id
        or row[1] != binding.scope_revision
        or row[2] != binding.target_event_id
        or row[3] != binding.resume_id
        or row[5] != binding.selection_fingerprint
        or row[6] != binding.revision
    ):
        raise ProjectionError("readiness_context_changed")
    return tuple(scope), tuple(row)


def load_readiness_source(
    connection: sqlite3.Connection,
    binding: ReadinessContextBinding | None,
) -> list[dict[str, object]]:
    """Load one frozen selection from the caller's read snapshot.

    ``None`` is an intentional no-target state.  It returns before touching a
    Readiness Signal table, which keeps ordinary workspace/Haru projections
    free of global or guessed Signal queries.
    """

    if binding is None:
        return []
    if not isinstance(binding, ReadinessContextBinding):
        raise ProjectionError("readiness_context_binding_invalid")
    _scope, row = _raw_scope_and_row(connection, binding)
    try:
        raw_ids = json.loads(str(row[4]))
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ProjectionError("readiness_selection_invalid") from exc
    ordered_ids = _ordered_ids(raw_ids, allow_empty=True)
    if ordered_ids != binding.ordered_version_ids:
        raise ProjectionError("readiness_context_changed")
    try:
        with readonly_session(connection) as session:
            selection = PreparationReadinessSelectionLoader(session).load(
                application_id=binding.application_id,
                target_event_id=binding.target_event_id,
                resume_id=binding.resume_id,
                ordered_version_ids=ordered_ids,
            )
    except PreparationReadinessSelectionError as exc:
        raise ProjectionError("readiness_source_unavailable") from exc
    if selection.selection_fingerprint != binding.selection_fingerprint:
        raise ProjectionError("readiness_selection_changed")
    feedback = selection.readiness_feedback
    if len(feedback) != len(ordered_ids):
        raise ProjectionError("readiness_selection_invalid")
    items: list[dict[str, object]] = []
    for version_id, item in zip(ordered_ids, feedback, strict=True):
        value: dict[str, object] = {
            "kind": "confirmed_readiness",
            "signal_version_id": version_id,
            "target_event_id": binding.target_event_id,
            "resume_id": binding.resume_id,
            "statement": item.statement,
            "user_note": item.user_note,
            "source_event": item.source_event.to_json(),
            "practice_state": item.practice_state,
            "evidence": [evidence_item.to_json() for evidence_item in item.evidence],
        }
        if len(canonical_json(value)) > MAX_READINESS_FEEDBACK_ENVELOPE_BYTES:
            raise ProjectionError("readiness_source_too_large")
        items.append(value)
    return items


def load_readiness_binding(
    connection: sqlite3.Connection,
    conversation_id: int,
) -> ReadinessContextBinding | None:
    """Load the current binding through the caller's read-only snapshot."""

    with readonly_session(connection) as session:
        return ReadinessContextRepository.capture_enabled_binding_in_session(
            session,
            conversation_id,
        )


__all__ = [
    "ConversationReadinessContext",
    "ConversationReadinessMutation",
    "ReadinessContextBinding",
    "ReadinessContextClearRequest",
    "ReadinessContextConflict",
    "ReadinessContextRepository",
    "ReadinessContextRequest",
    "ReadinessContextUnavailable",
    "application_conversation",
    "load_readiness_binding",
    "load_readiness_source",
]
