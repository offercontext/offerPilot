"""Explicit, cached extractive summaries of plain older conversation messages.

Generation is deterministic and uses no Provider. Tool transcripts remain raw.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import sqlite3
from time import monotonic
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy import ForeignKey, Integer, String, Text, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from offerpilot.models import Application, Base, ChatMessage, Conversation
from offerpilot.context_projector.contracts import canonical_json

GENERATION_VERSION = "extractive-history-v1"
MAX_INPUT_BYTES = 32768
MAX_OUTPUT_BYTES = 3000
MAX_GENERATIONS_PER_DAY = 4


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True)
    source_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    generation_version: Mapped[str] = mapped_column(String(40), nullable=False, default=GENERATION_VERSION)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    output_digest: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    generation_day: Mapped[str] = mapped_column(String(10), nullable=False)
    generations_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: StrictBool
    through_message_id: StrictInt | None = Field(default=None, gt=0)


class SummaryUnavailable(ValueError):
    pass


def _source_value(row: ChatMessage) -> dict[str, object]:
    return {"id": row.id, "role": row.role, "content": row.content, "tool_calls": row.tool_calls,
            "tool_call_id": row.tool_call_id, "provider_blocks": row.provider_blocks}


class ConversationSummaryRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def generate(self, conversation_id: int, command: SummaryRequest) -> dict[str, object]:
        if not command.confirmed:
            raise SummaryUnavailable("explicit_confirmation_required")
        deadline = monotonic() + 2.0
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            conversation = session.get(Conversation, conversation_id)
            if conversation is None or conversation.archived_at is not None:
                raise SummaryUnavailable("conversation_unavailable")
            if conversation.context_type == "application":
                application = session.get(Application, conversation.context_ref)
                if application is None or application.deleted_at is not None:
                    raise SummaryUnavailable("conversation_scope_unavailable")
            # Keep at least four recent messages raw. Never summarize a partial
            # tool group or provider-specific transcript.
            recent = session.scalars(select(ChatMessage.id).where(ChatMessage.conversation_id == conversation_id)
                                     .order_by(ChatMessage.id.desc()).limit(4)).all()
            if len(recent) < 4:
                raise SummaryUnavailable("not_enough_older_messages")
            end = min(recent) - 1
            if command.through_message_id is not None:
                end = min(end, command.through_message_id)
            # Inspect byte lengths before materializing user-controlled text.
            candidates = session.execute(text("""SELECT id,
                length(CAST(content AS BLOB)) + length(CAST(role AS BLOB)) +
                length(CAST(tool_calls AS BLOB)) + length(CAST(tool_call_id AS BLOB)) +
                length(CAST(provider_blocks AS BLOB)) AS input_bytes
                FROM chat_messages WHERE conversation_id=:conversation_id AND id<=:end
                ORDER BY id LIMIT 32"""), {"conversation_id": conversation_id, "end": end}).all()
            values: list[dict[str, object]] = []
            used = 0
            for message_id, input_bytes in candidates:
                if monotonic() >= deadline:
                    raise SummaryUnavailable("summary_deadline_exceeded")
                if input_bytes is None or used + input_bytes > MAX_INPUT_BYTES:
                    break
                row = session.get(ChatMessage, message_id)
                if row is None:
                    break
                if row.role not in {"user", "assistant"} or row.tool_calls not in {"", "[]"} or row.tool_call_id or row.provider_blocks not in {"", "{}"}:
                    break
                value = _source_value(row)
                size = len(canonical_json(value))
                if used + size > MAX_INPUT_BYTES:
                    break
                values.append(value)
                used += size
            if values:
                next_role = session.scalar(select(ChatMessage.role).where(
                    ChatMessage.conversation_id == conversation_id, ChatMessage.id > values[-1]["id"]
                ).order_by(ChatMessage.id).limit(1))
                if next_role is not None and next_role != "user":
                    # Never leave the raw suffix starting with an orphan reply.
                    while values:
                        removed = values.pop()
                        if removed["role"] == "user":
                            break
            if not values:
                raise SummaryUnavailable("no_plain_older_history")
            digest = sha256(canonical_json(values)).hexdigest()
            stored = session.get(ConversationSummary, conversation_id)
            if stored is not None and stored.source_digest == digest and stored.generation_version == GENERATION_VERSION and stored.summary_json != "{}":
                if sha256(stored.summary_json.encode()).hexdigest() != stored.output_digest:
                    raise SummaryUnavailable("summary_integrity_failed")
                return {"state": "ready", "revision": stored.revision, "cached": True,
                        "summary": json.loads(stored.summary_json), "model_calls": 0}
            day = datetime.now(timezone.utc).date().isoformat()
            if stored is None:
                stored = ConversationSummary(conversation_id=conversation_id, generation_day=day,
                    generations_today=0, revision=0)
                session.add(stored)
            if stored.generation_day != day:
                stored.generation_day, stored.generations_today = day, 0
            if stored.generations_today >= MAX_GENERATIONS_PER_DAY:
                raise SummaryUnavailable("summary_daily_budget_exceeded")
            items: list[dict[str, object]] = []
            for value in values:
                item = {"kind": "user_statement" if value["role"] == "user" else "model_inference",
                        "source_message_id": value["id"], "excerpt": str(value["content"])[:240],
                        "truncated": len(str(value["content"])) > 240}
                if len(canonical_json([*items, item])) > MAX_OUTPUT_BYTES:
                    break
                items.append(item)
            summary = {"generation_version": GENERATION_VERSION, "from_message_id": values[0]["id"],
                       "through_message_id": values[-1]["id"], "items": items,
                       "omitted_messages": len(values) - len(items), "supported_facts": []}
            raw = canonical_json(summary)
            if len(raw) > MAX_OUTPUT_BYTES + 512 or monotonic() >= deadline:
                raise SummaryUnavailable("summary_generation_budget_exceeded")
            stored.source_ids_json = json.dumps([value["id"] for value in values])
            stored.source_digest = digest
            stored.summary_json = raw.decode()
            stored.output_digest = sha256(raw).hexdigest()
            stored.generation_version = GENERATION_VERSION
            stored.generations_today += 1
            stored.revision += 1
            result = {"state": "ready", "revision": stored.revision, "cached": False, "summary": summary, "model_calls": 0}
            session.commit()
            return result

    def withdraw(self, conversation_id: int) -> None:
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(ConversationSummary, conversation_id)
            if row is not None:
                row.summary_json = "{}"
                row.source_digest = ""
                row.source_ids_json = "[]"
                row.output_digest = ""
                row.revision += 1
            session.commit()


def load_summary(connection: sqlite3.Connection, conversation_id: int) -> tuple[list[dict[str, object]], tuple[tuple[str, str], ...]]:
    sizes = connection.execute("SELECT length(CAST(source_ids_json AS BLOB)),length(CAST(summary_json AS BLOB)) FROM conversation_summaries WHERE conversation_id=?", (conversation_id,)).fetchone()
    if sizes is None or sizes[0] > 1024 or sizes[1] > MAX_OUTPUT_BYTES + 512:
        return [], ()
    row = connection.execute("SELECT source_ids_json,source_digest,summary_json,output_digest,generation_version,revision FROM conversation_summaries WHERE conversation_id=?", (conversation_id,)).fetchone()
    if row is None or row[2] == "{}" or row[4] != GENERATION_VERSION:
        return [], ()
    try:
        ids = json.loads(row[0])
    except (TypeError, ValueError):
        return [], ()
    if not isinstance(ids, list) or not ids or len(ids) > 32 or any(type(value) is not int for value in ids):
        return [], ()
    sizes = connection.execute("""SELECT count(*), sum(length(CAST(content AS BLOB)) + length(CAST(role AS BLOB)) +
        length(CAST(tool_calls AS BLOB)) + length(CAST(tool_call_id AS BLOB)) + length(CAST(provider_blocks AS BLOB)))
        FROM chat_messages WHERE conversation_id=? AND id<=?""", (conversation_id, ids[-1])).fetchone()
    if sizes[0] != len(ids) or sizes[1] is None or sizes[1] > MAX_INPUT_BYTES:
        return [], ()
    cursor = connection.execute("SELECT id,role,content,tool_calls,tool_call_id,provider_blocks FROM chat_messages WHERE conversation_id=? AND id<=? ORDER BY id LIMIT 33", (conversation_id, ids[-1]))
    values = [dict(zip((column[0] for column in cursor.description), record, strict=True)) for record in cursor.fetchall()]
    if [value["id"] for value in values] != ids or sha256(canonical_json(values)).hexdigest() != row[1] or sha256(row[2].encode()).hexdigest() != row[3]:
        return [], ()
    try:
        summary: Any = json.loads(row[2])
    except (TypeError, ValueError):
        return [], ()
    if not isinstance(summary, dict) or not isinstance(summary.get("items"), list) or any(not isinstance(item, dict) or item.get("source_message_id") not in ids or item.get("kind") not in {"user_statement", "model_inference"} for item in summary["items"]):
        return [], ()
    return [{"summary": summary, "revision": row[5], "source_digest": row[1]}], tuple((value["role"], value["content"]) for value in values)
