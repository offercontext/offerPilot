from __future__ import annotations

# 业务地图：docs/architecture/code-map/knowledge-memory.md（用户确认的个人记忆）。
# Memory 是用户偏好等持续认知，不作为 Knowledge 的事实证据。

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from .models import ConfirmedMemory, ConfirmedMemoryMutation, ConfirmedMemoryVersion, ConfirmedMemoryVersionMetadata
from .metadata import MemoryMetadata, temporal_status


class MemoryMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mutation_id: UUID
    action: Literal["confirm", "withdraw", "delete"]
    expected_version: StrictInt = Field(ge=0)
    confirmed: StrictBool
    content: str = Field(default="", max_length=2000)

    metadata: MemoryMetadata | None = None

    @model_validator(mode="after")
    def check_confirmation(self) -> MemoryMutation:
        if self.confirmed is not True:
            raise ValueError("请明确确认此项操作")
        self.content = self.content.strip()
        if self.action == "confirm" and not self.content:
            raise ValueError("确认内容不能为空")
        if self.action != "confirm" and self.content:
            raise ValueError("撤回与删除不能包含正文")
        if "metadata" in self.model_fields_set and (self.metadata is None or self.action != "confirm"):
            raise ValueError("元数据仅可随确认操作提供，且不能为 null")
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
            now = datetime.now(timezone.utc)
            views = [self._view(session, row, now=now) for row in rows]
            return [view for view in views if not active_only or view["temporal_status"] != "expired"]

    def get(self, memory_id: str) -> dict[str, object]:
        with self.sessions() as session:
            row = session.get(ConfirmedMemory, memory_id)
            if row is None or row.state == "deleted":
                raise MemoryGone("memory_gone")
            now = datetime.now(timezone.utc)
            view = self._view(session, row, now=now)
            view["versions"] = [{"version": version.version, "content": version.content,
                                 "confirmed_at": version.confirmed_at,
                                 **self._metadata_view(session, version, now)}
                                for version in session.scalars(select(ConfirmedMemoryVersion).where(
                                    ConfirmedMemoryVersion.memory_id == row.id).order_by(
                                        ConfirmedMemoryVersion.version).limit(100))]
            return view

    def mutate(self, memory_id: str | None, command: MemoryMutation) -> dict[str, object]:
        # 确认、撤回和删除都受版本及 mutation_id 保护；撤回保留内容，删除清空全部版本正文。
        # 同 mutation 的重放核对输入指纹，但返回当前可见状态，不恢复被后续删除的内容。
        body = command.model_dump(mode="json")
        # Omission preserves the exact pre-metadata request fingerprint.
        if "metadata" not in command.model_fields_set:
            body.pop("metadata")
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
                version = ConfirmedMemoryVersion(id=str(uuid4()), memory_id=row.id,
                    version=row.current_version, content=command.content,
                    confirmed_at=datetime.now(timezone.utc).isoformat())
                session.add(version)
                session.flush()
                metadata = command.metadata
                if metadata is None:
                    prior_metadata = self._metadata_view(session, self._latest_version_before(session, row, version.version), datetime.now(timezone.utc))["metadata"]
                    metadata = MemoryMetadata.model_validate({k: prior_metadata[k] for k in ("category", "valid_until")})
                session.add(ConfirmedMemoryVersionMetadata(version_id=version.id,
                    category=metadata.category, valid_until=metadata.valid_until))
            elif command.action == "withdraw":
                row.state = "withdrawn"
            else:
                row.state = "deleted"
                session.execute(delete(ConfirmedMemoryVersionMetadata).where(
                    ConfirmedMemoryVersionMetadata.version_id.in_(select(ConfirmedMemoryVersion.id).where(
                        ConfirmedMemoryVersion.memory_id == row.id))))
                session.execute(update(ConfirmedMemoryVersion).where(
                    ConfirmedMemoryVersion.memory_id == row.id).values(content=""))
            session.add(ConfirmedMemoryMutation(id=str(command.mutation_id), memory_id=row.id,
                fingerprint=fingerprint, version=row.current_version))
            session.flush()
            result = self._view(session, row)
            session.commit()
            return result

    @staticmethod
    def _latest_version_before(session: Session, row: ConfirmedMemory, version: int) -> ConfirmedMemoryVersion | None:
        return session.scalar(select(ConfirmedMemoryVersion).where(
            ConfirmedMemoryVersion.memory_id == row.id, ConfirmedMemoryVersion.version < version
        ).order_by(ConfirmedMemoryVersion.version.desc()).limit(1))

    @staticmethod
    def _latest_version(session: Session, row: ConfirmedMemory) -> ConfirmedMemoryVersion | None:
        return session.scalar(select(ConfirmedMemoryVersion).where(
            ConfirmedMemoryVersion.memory_id == row.id).order_by(
                ConfirmedMemoryVersion.version.desc()).limit(1))

    @staticmethod
    def _metadata_view(session: Session, version: ConfirmedMemoryVersion | None, now: datetime) -> dict[str, Any]:
        record = session.get(ConfirmedMemoryVersionMetadata, version.id) if version else None
        metadata = MemoryMetadata.model_validate({"category": record.category if record else "unclassified",
                                                  "valid_until": record.valid_until if record else None})
        return {"metadata": {**metadata.model_dump(), "evidence_status": "user_confirmed"},
                "temporal_status": temporal_status(metadata.valid_until, now)}

    @classmethod
    def _view(cls, session: Session, row: ConfirmedMemory, *, now: datetime | None = None) -> dict[str, object]:
        version = cls._latest_version(session, row)
        return {"id": row.id, "current_version": row.current_version, "state": row.state,
                "content": version.content if version and row.state != "deleted" else "",
                "confirmed_at": version.confirmed_at if version and row.state != "deleted" else None,
                **cls._metadata_view(session, version, now or datetime.now(timezone.utc))}
