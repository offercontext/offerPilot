from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from offerpilot.models import Base


class ConfirmedMemory(Base):
    __tablename__ = "confirmed_memories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)


class ConfirmedMemoryVersion(Base):
    __tablename__ = "confirmed_memory_versions"
    __table_args__ = (UniqueConstraint("memory_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    memory_id: Mapped[str] = mapped_column(ForeignKey("confirmed_memories.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ConfirmedMemoryMutation(Base):
    __tablename__ = "confirmed_memory_mutations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    memory_id: Mapped[str] = mapped_column(ForeignKey("confirmed_memories.id"), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
