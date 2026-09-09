from __future__ import annotations

from sqlalchemy import Float, Integer, String, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from offerpilot.models import Base


class ProactiveSettings(Base):
    __tablename__ = "proactive_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    settings_json: Mapped[str] = mapped_column(Text, nullable=False)


class ProactiveJob(Base):
    __tablename__ = "proactive_jobs"
    __table_args__ = (Index("idx_proactive_due", "state", "due_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    application_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    due_at: Mapped[float] = mapped_column(Float, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    published_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    owner: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    lease_until: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    turn_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    execution_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_code: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    model_started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
