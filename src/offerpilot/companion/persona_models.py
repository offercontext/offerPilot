"""Single workspace profile and append-only expression revisions, isolated from life."""
from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from offerpilot.models import Base


class PersonaProfile(Base):
    __tablename__ = "companion_persona_profile"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)


class PersonaRevision(Base):
    __tablename__ = "companion_persona_revisions"
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("companion_persona_profile.profile_id"), primary_key=True,
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    restored_from_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PersonaCommandReceipt(Base):
    __tablename__ = "companion_persona_command_receipts"
    command_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
