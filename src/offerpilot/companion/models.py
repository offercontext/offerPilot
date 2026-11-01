"""Isolated companion storage; no business or confirmed-memory foreign keys."""
from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from offerpilot.models import Base


class CompanionState(Base):
    __tablename__ = "companion_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    identity: Mapped[str] = mapped_column(String(36), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    persona_version: Mapped[str] = mapped_column(String(40), nullable=False)
    settings_json: Mapped[str] = mapped_column(Text, nullable=False)
    life_json: Mapped[str] = mapped_column(Text, nullable=False)
    latest_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)


class CompanionEvent(Base):
    __tablename__ = "companion_events"
    __table_args__ = (Index("idx_companion_event_revision", "identity", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identity: Mapped[str] = mapped_column(String(36), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    activity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Structured virtual-object binding. Legacy unknown history remains null;
    # prose is never parsed to manufacture an activity's identity or type.
    template_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[float] = mapped_column(Float, nullable=False)


class CompanionParticipation(Base):
    __tablename__ = "companion_participation"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identity: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    activity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    promise: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="control", server_default="control")
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    completed_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class CompanionCommandReceipt(Base):
    __tablename__ = "companion_command_receipts"
    command_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identity: Mapped[str] = mapped_column(String(36), nullable=False)
    resulting_identity: Mapped[str] = mapped_column(String(36), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)


class CompanionPresence(Base):
    __tablename__ = "companion_presence"
    client_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identity: Mapped[str] = mapped_column(String(36), nullable=False)
    flags_json: Mapped[str] = mapped_column(Text, nullable=False)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    seen_at: Mapped[float] = mapped_column(Float, nullable=False)


class CompanionUsageSession(Base):
    """Content-free attention budget survives clearing companion memories."""
    __tablename__ = "companion_usage_session"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    last_seen_at: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    unanswered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    session_quiet: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CompanionChatEvidence(Base):
    """Only actual admitted user instructions, never model-authored state."""
    __tablename__ = "companion_chat_evidence"
    command_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identity: Mapped[str] = mapped_column(String(36), nullable=False)
    activity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    participation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False)
    turn_id: Mapped[str] = mapped_column(ForeignKey("pilot_turns.id", ondelete="CASCADE"), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    step_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    value: Mapped[str | None] = mapped_column(String(40), nullable=True)
