"""Durable Pilot admission and display storage, with no execution dependencies."""

from __future__ import annotations

import json
import base64
import hmac
from secrets import token_hex
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import (
    ChatMessage, Conversation, PilotRequestReceipt, PilotTurnRecord, PilotTurnMessage,
    PilotTurnOperation,
    PilotTimelineState, PilotTimelineItem, PilotTimelineChange,
)
from offerpilot.presentation_contracts import PilotTurnItemV1
from offerpilot.repositories.chat import (
    ConversationScopeMutationSnapshot,
    _require_active_application,
)


class AdmissionConflict(ValueError):
    """A logical request key is already bound to a different request."""


class AdmissionGone(LookupError):
    """The original conversation was deleted; this request can never execute again."""


class AdmissionNotFound(LookupError):
    """No conversation exists for a new request."""


class TimelineResyncRequired(ValueError):
    """The client must replace its cached timeline with a fresh snapshot."""


@dataclass(frozen=True, slots=True)
class TimelineSource:
    turn_id: str
    item: PilotTurnItemV1
    source_refs: tuple[str, ...]
    source_revision: str = ""


@dataclass(frozen=True, slots=True)
class TurnAdmission:
    turn_id: str
    conversation_id: int
    user_message_id: int | None
    created: bool


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


class PilotTimelineRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def admit(
        self,
        request_id: str,
        request: Mapping[str, Any],
        *,
        freeze_sources: Callable[[Session, Conversation], Mapping[str, str]] | None = None,
    ) -> TurnAdmission:
        """Persist the receipt, turn, conversation and user message atomically.

        The caller supplies the validated request, never a client-computed digest.
        This database is the authenticated local workspace boundary. The key is
        scoped to this workspace and command; the receipt separately binds the
        submitted and resolved conversation. A retry may use either zero or
        the returned ID for a newly created conversation, never another one.
        No provider or tool is called here.
        """
        if not isinstance(request_id, str):
            raise ValueError("request_id must be a UUID v4")
        try:
            parsed_id = UUID(request_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError("request_id must be a UUID v4") from exc
        if parsed_id.version != 4 or str(parsed_id) != request_id:
            raise ValueError("request_id must be a canonical UUID v4")
        conversation_id = request.get("conversation_id") or 0
        if type(conversation_id) is not int or conversation_id < 0:
            raise ValueError("conversation_id must be a non-negative integer")
        message = request.get("message")
        if not isinstance(message, str) or not message:
            raise ValueError("message is required")
        canonical = dict(request)
        canonical.pop("request_id", None)
        canonical.pop("conversation_id", None)
        request_key = _digest(["pilot-start-v1", request_id])
        request_digest = _digest(canonical)
        with self.session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            receipt = session.get(PilotRequestReceipt, request_key)
            if receipt is not None:
                if receipt.turn_id is None:
                    raise AdmissionGone("Original conversation was deleted")
                if receipt.request_digest != request_digest:
                    raise AdmissionConflict("request_id already belongs to a different request")
                turn = session.get(PilotTurnRecord, receipt.turn_id)
                if turn is None:
                    raise AdmissionGone("Original conversation was deleted")
                if conversation_id not in {receipt.submitted_conversation_id, turn.conversation_id}:
                    raise AdmissionConflict("request_id belongs to a different conversation")
                return TurnAdmission(turn.id, turn.conversation_id, turn.user_message_id, False)

            if conversation_id:
                conversation = session.get(Conversation, conversation_id)
                if conversation is None:
                    raise AdmissionNotFound("Conversation is unavailable")
                if conversation.archived_at is not None:
                    raise AdmissionConflict("Conversation is archived")
            else:
                scope = ConversationScopeMutationSnapshot(
                    context_type=request.get("context_type", "workspace"),
                    context_ref=request.get("context_ref", ""),
                    mode=request.get("mode", "general"),
                )
                _require_active_application(session, scope)
                conversation = Conversation(
                    title=message[:40], title_source="fallback",
                    context_type=scope.context_type, context_ref=scope.context_ref,
                    mode=scope.mode, scope_revision=0,
                )
                session.add(conversation)
                session.flush()
            versions = dict(freeze_sources(session, conversation)) if freeze_sources is not None else {}
            if any(not isinstance(k, str) or not isinstance(v, str) for k, v in versions.items()):
                raise ValueError("Source versions must contain string references and versions")
            user = ChatMessage(conversation_id=conversation.id, role="user", content=message)
            conversation.updated_at = datetime.now(timezone.utc)
            session.add(user)
            session.flush()
            turn = PilotTurnRecord(
                id=str(uuid4()), conversation_id=conversation.id, user_message_id=user.id,
                state="accepted", source_versions_json=_json(versions),
            )
            session.add(turn)
            session.flush()
            session.add(PilotTurnMessage(message_id=user.id, turn_id=turn.id))
            session.add(PilotRequestReceipt(
                request_key=request_key, request_digest=request_digest, turn_id=turn.id,
                submitted_conversation_id=conversation_id,
            ))
            result = TurnAdmission(turn.id, conversation.id, user.id, True)
            session.commit()
            return result

    def claim(self, turn_id: str) -> bool:
        """One database CAS grants the initial attempt, never a resume lease."""
        with self.session_factory() as session:
            result = session.execute(update(PilotTurnRecord).where(
                PilotTurnRecord.id == turn_id, PilotTurnRecord.state == "accepted",
            ).values(state="started"))
            session.commit()
            return bool(getattr(result, "rowcount", 0))

    def find_by_request(self, request_id: str) -> dict[str, Any] | None:
        try:
            parsed = UUID(request_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError("request_id must be a canonical UUID v4") from exc
        if parsed.version != 4 or str(parsed) != request_id:
            raise ValueError("request_id must be a canonical UUID v4")
        with self.session_factory() as session:
            receipt = session.get(PilotRequestReceipt, _digest(["pilot-start-v1", request_id]))
            if receipt is None:
                return None
            if receipt.turn_id is None:
                raise AdmissionGone("Original conversation was deleted")
            turn_id = receipt.turn_id
        return self.get_turn(turn_id)

    def get_turn(self, turn_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            session.execute(text("BEGIN"))
            turn = session.get(PilotTurnRecord, turn_id)
            if turn is None:
                return None
            return {
                "turn_id": turn.id, "conversation_id": turn.conversation_id,
                "user_message_id": turn.user_message_id, "state": turn.state,
                "source_versions": json.loads(turn.source_versions_json),
                "message_ids": list(session.scalars(select(PilotTurnMessage.message_id).where(
                    PilotTurnMessage.turn_id == turn_id,
                ).order_by(PilotTurnMessage.message_id))),
                "operation_ids": list(session.scalars(select(PilotTurnOperation.operation_id).where(
                    PilotTurnOperation.turn_id == turn_id,
                ).order_by(PilotTurnOperation.operation_id))),
            }

    def finish(self, turn_id: str, state: str) -> None:
        if state not in {"completed", "failed", "interrupted"}:
            raise ValueError("Invalid terminal turn state")
        with self.session_factory() as session:
            session.execute(update(PilotTurnRecord).where(
                PilotTurnRecord.id == turn_id, PilotTurnRecord.state == "started",
            ).values(state=state))
            session.commit()

    def read_timeline(
        self,
        conversation_id: int,
        project: Callable[[Session], list[TimelineSource]],
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Repair display from authoritative sources and read one consistent page.

        Only display tables and missing provenance bindings may be written by
        the projector. BEGIN IMMEDIATE orders source reads, item revisions and
        change visibility under the same SQLite commit. It never runs a tool.
        """
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self.session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            if session.get(Conversation, conversation_id) is None:
                raise AdmissionGone("Conversation is unavailable")
            state = session.get(PilotTimelineState, conversation_id)
            if state is None:
                state = PilotTimelineState(
                    conversation_id=conversation_id, change_seq=0, ordinal=0,
                    epoch=0, cursor_key=token_hex(32),
                )
                session.add(state)
                session.flush()
            position = _decode_cursor(cursor, state) if cursor is not None else None
            sources = project(session)
            if len(sources) > 10000:
                raise ValueError("Timeline source batch exceeds its bound")
            self._reconcile(session, state, sources)
            session.flush()
            # Deletion removes retained payload copies and invalidates pages
            # that depended on the removed historical snapshot. Checkpoints
            # still receive the new tombstones on their next incremental read.
            if position is not None and position["kind"] != "checkpoint" and position["epoch"] != state.epoch:
                # The old page is unusable, but source removal is authoritative.
                # Never roll back its privacy purge merely to signal resync.
                session.commit()
                raise TimelineResyncRequired("Timeline changed during pagination")
            kind = "snapshot" if position is None else position["kind"]
            after = 0 if position is None else position["after"]
            high = state.change_seq if position is None or kind == "checkpoint" else position["high"]
            if kind == "checkpoint":
                kind = "changes"
            if high > state.change_seq or after > (state.ordinal if kind == "snapshot" else high):
                raise TimelineResyncRequired("Timeline cursor is outside the retained range")
            query = select(PilotTimelineChange).where(
                PilotTimelineChange.conversation_id == conversation_id,
            )
            if kind == "snapshot":
                latest = select(func.max(PilotTimelineChange.change_seq)).where(
                    PilotTimelineChange.conversation_id == conversation_id,
                    PilotTimelineChange.change_seq <= high,
                ).group_by(PilotTimelineChange.item_id)
                query = query.where(
                    PilotTimelineChange.change_seq.in_(latest),
                    PilotTimelineChange.ordinal > after,
                    PilotTimelineChange.deleted.is_(False),
                ).order_by(PilotTimelineChange.ordinal)
            else:
                query = query.where(
                    PilotTimelineChange.change_seq > after,
                    PilotTimelineChange.change_seq <= high,
                ).order_by(PilotTimelineChange.change_seq)
            rows = list(session.scalars(query.limit(limit + 1)))
            has_more = len(rows) > limit
            rows = rows[:limit]
            next_cursor = None
            if has_more:
                last = rows[-1]
                next_cursor = _encode_cursor(state, {
                    "kind": kind, "after": last.ordinal if kind == "snapshot" else last.change_seq,
                    "high": high,
                })
            result = {
                "schema_version": 1, "conversation_id": conversation_id,
                "mode": kind, "high_watermark": high,
                "items": [json.loads(row.payload_json) for row in rows],
                "next_cursor": next_cursor,
                "cursor": _encode_cursor(state, {"kind": "checkpoint", "after": high, "high": high}),
            }
            session.commit()
            return result

    def _reconcile(
        self, session: Session, state: PilotTimelineState, sources: list[TimelineSource],
    ) -> None:
        existing = {row.item_id: row for row in session.scalars(select(PilotTimelineItem).where(
            PilotTimelineItem.conversation_id == state.conversation_id,
        ))}
        seen: set[str] = set()
        for source in sources:
            turn = session.get(PilotTurnRecord, source.turn_id)
            if turn is None or turn.conversation_id != state.conversation_id:
                raise ValueError("Timeline source belongs to another conversation")
            item_id = f"turn:{source.turn_id}:{source.item.item_id}"
            if item_id in seen:
                raise ValueError("Duplicate timeline source identity")
            seen.add(item_id)
            payload = source.item.model_dump(mode="json")
            digest = _digest(payload)
            revision = source.source_revision or (
                source.item.action.source_revision if source.item.action is not None else digest
            )
            refs = _json(source.source_refs)
            item = existing.get(item_id)
            if item is not None and not item.deleted and (
                item.payload_digest == digest and item.source_revision == revision and item.source_refs_json == refs
            ):
                continue
            state.change_seq += 1
            if item is None:
                state.ordinal += 1
                item = PilotTimelineItem(
                    conversation_id=state.conversation_id, item_id=item_id,
                    turn_id=source.turn_id, item_type=source.item.kind,
                    ordinal=state.ordinal, revision=1, display_revision=1,
                )
                session.add(item)
            else:
                item.revision += 1
                if item.payload_digest != digest or item.deleted:
                    item.display_revision += 1
            item.source_refs_json = refs
            item.source_revision = revision
            item.change_seq = state.change_seq
            item.payload_json = _json(payload)
            item.payload_digest = digest
            item.deleted = False
            session.flush()
            session.add(_change(item))
        for item_id, item in existing.items():
            if item_id in seen or item.deleted:
                continue
            state.change_seq += 1
            state.epoch += 1
            item.revision += 1
            item.display_revision += 1
            item.change_seq = state.change_seq
            item.deleted = True
            item.payload_json = "null"
            item.payload_digest = ""
            item.source_revision = ""
            item.source_refs_json = "[]"
            # True removal clears every retained content copy, not just the
            # current row. Only the latest content-free tombstone survives.
            session.execute(delete(PilotTimelineChange).where(
                PilotTimelineChange.conversation_id == state.conversation_id,
                PilotTimelineChange.item_id == item_id,
            ))
            session.add(_change(item))


def _change(item: PilotTimelineItem) -> PilotTimelineChange:
    dto = {
        "schema_version": 1, "item_id": item.item_id, "turn_id": item.turn_id,
        "conversation_id": item.conversation_id, "item_type": item.item_type,
        "revision": item.revision, "display_revision": item.display_revision,
        "ordinal": item.ordinal, "change_seq": item.change_seq,
        "source_refs": json.loads(item.source_refs_json), "source_revision": item.source_revision,
        "payload_digest": item.payload_digest, "deleted": item.deleted,
        "payload": json.loads(item.payload_json),
    }
    return PilotTimelineChange(
        conversation_id=item.conversation_id, change_seq=item.change_seq,
        item_id=item.item_id, ordinal=item.ordinal, deleted=item.deleted,
        payload_json=_json(dto),
    )


def _encode_cursor(state: PilotTimelineState, values: Mapping[str, Any]) -> str:
    payload = _json({"v": 1, "c": state.conversation_id, "epoch": state.epoch, **values}).encode("utf-8")
    body = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(bytes.fromhex(state.cursor_key), body.encode("ascii"), sha256).hexdigest()
    return f"{body}.{signature}"


def _decode_cursor(cursor: str, state: PilotTimelineState) -> dict[str, Any]:
    try:
        if len(cursor) > 1024:
            raise ValueError("Cursor is too long")
        body, signature = cursor.split(".")
        expected = hmac.new(bytes.fromhex(state.cursor_key), body.encode("ascii"), sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Cursor signature mismatch")
        value = json.loads(base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True))
        if not isinstance(value, dict) or set(value) != {"v", "c", "epoch", "kind", "after", "high"}:
            raise ValueError("Cursor shape mismatch")
        if value["v"] != 1 or value["c"] != state.conversation_id or value["kind"] not in {"snapshot", "changes", "checkpoint"}:
            raise ValueError("Cursor version or scope mismatch")
        if any(type(value[key]) is not int or value[key] < 0 for key in ("epoch", "after", "high")):
            raise ValueError("Cursor bounds are invalid")
        return value
    except (ValueError, TypeError, UnicodeError) as exc:
        raise TimelineResyncRequired("Timeline cursor is invalid; load a new snapshot") from exc
