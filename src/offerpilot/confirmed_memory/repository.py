from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from .models import ConfirmedMemory, ConfirmedMemoryMutation, ConfirmedMemoryVersion


class MemoryMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mutation_id: UUID
    action: Literal["confirm", "withdraw", "delete"]
    expected_version: StrictInt = Field(ge=0)
    confirmed: StrictBool
    content: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def check_confirmation(self) -> MemoryMutation:
        if self.confirmed is not True:
            raise ValueError("请明确确认此项操作")
        self.content = self.content.strip()
        if self.action == "confirm" and not self.content:
            raise ValueError("确认内容不能为空")
        if self.action != "confirm" and self.content:
            raise ValueError("撤回与删除不能包含正文")
        return self


class MemoryConflict(ValueError):
    pass


class MemoryGone(ValueError):
    pass


class ConfirmedMemoryRepository:
    """Single authenticated workspace. No caller-controlled owner or auto writes."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def list(self, *, active_only: bool = False) -> list[dict[str, object]]:
        with self.sessions() as session:
            query = select(ConfirmedMemory).where(ConfirmedMemory.state != "deleted")
            if active_only:
                query = query.where(ConfirmedMemory.state == "active")
            rows = session.scalars(query.order_by(ConfirmedMemory.id).limit(101)).all()
            if len(rows) > 100:
                raise MemoryConflict("memory_capacity_exceeded")
            return [self._view(session, row) for row in rows]

    def get(self, memory_id: str) -> dict[str, object]:
        with self.sessions() as session:
            row = session.get(ConfirmedMemory, memory_id)
            if row is None or row.state == "deleted":
                raise MemoryGone("memory_gone")
            view = self._view(session, row)
            view["versions"] = [{"version": version.version, "content": version.content,
                                 "confirmed_at": version.confirmed_at}
                                for version in session.scalars(select(ConfirmedMemoryVersion).where(
                                    ConfirmedMemoryVersion.memory_id == row.id).order_by(
                                        ConfirmedMemoryVersion.version).limit(100))]
            return view

    def mutate(self, memory_id: str | None, command: MemoryMutation) -> dict[str, object]:
        body = command.model_dump(mode="json")
        body["memory_id"] = memory_id
        fingerprint = sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            previous = session.get(ConfirmedMemoryMutation, str(command.mutation_id))
            if previous is not None:
                if previous.fingerprint != fingerprint:
                    raise MemoryConflict("mutation_id_conflict")
                row = session.get(ConfirmedMemory, previous.memory_id)
                if row is None or row.state == "deleted":
                    if command.action == "delete":
                        return {"id": previous.memory_id, "state": "deleted", "current_version": previous.version}
                    raise MemoryGone("memory_gone")
                return self._view(session, row)
            row = session.get(ConfirmedMemory, memory_id) if memory_id else None
            if memory_id and (row is None or row.state == "deleted"):
                raise MemoryGone("memory_gone")
            if row is None:
                if command.action != "confirm" or command.expected_version != 0:
                    raise MemoryConflict("memory_version_conflict")
                if len(session.scalars(select(ConfirmedMemory.id).where(
                        ConfirmedMemory.state != "deleted").limit(100)).all()) >= 100:
                    raise MemoryConflict("memory_capacity_exceeded")
                row = ConfirmedMemory(id=str(uuid4()), current_version=0, state="active")
                session.add(row)
                session.flush()
            if row.current_version != command.expected_version:
                raise MemoryConflict("memory_version_conflict")
            if command.action == "confirm" and len(session.scalars(select(ConfirmedMemoryVersion.id).where(
                    ConfirmedMemoryVersion.memory_id == row.id).limit(100)).all()) >= 100:
                raise MemoryConflict("memory_version_capacity_exceeded")
            if command.action == "withdraw" and row.state == "withdrawn":
                session.add(ConfirmedMemoryMutation(id=str(command.mutation_id), memory_id=row.id,
                    fingerprint=fingerprint, version=row.current_version))
                result = self._view(session, row)
                session.commit()
                return result
            row.current_version += 1
            if command.action == "confirm":
                row.state = "active"
                session.add(ConfirmedMemoryVersion(id=str(uuid4()), memory_id=row.id,
                    version=row.current_version, content=command.content,
                    confirmed_at=datetime.now(timezone.utc).isoformat()))
            elif command.action == "withdraw":
                row.state = "withdrawn"
            else:
                row.state = "deleted"
                session.execute(update(ConfirmedMemoryVersion).where(
                    ConfirmedMemoryVersion.memory_id == row.id).values(content=""))
            session.add(ConfirmedMemoryMutation(id=str(command.mutation_id), memory_id=row.id,
                fingerprint=fingerprint, version=row.current_version))
            session.flush()
            result = self._view(session, row)
            session.commit()
            return result

    @staticmethod
    def _view(session: Session, row: ConfirmedMemory) -> dict[str, object]:
        version = session.scalar(select(ConfirmedMemoryVersion).where(
            ConfirmedMemoryVersion.memory_id == row.id).order_by(
                ConfirmedMemoryVersion.version.desc()).limit(1))
        return {"id": row.id, "current_version": row.current_version, "state": row.state,
                "content": version.content if version and row.state != "deleted" else "",
                "confirmed_at": version.confirmed_at if version and row.state != "deleted" else None}
