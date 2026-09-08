from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import Wakeup


@dataclass
class WakeupCreate:
    kind: str
    due_at: datetime
    payload: dict[str, Any]


class WakeupsRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def create(self, data: WakeupCreate) -> Wakeup:
        wakeup = Wakeup(
            kind=data.kind,
            due_at=data.due_at,
            payload_json=json.dumps(data.payload, ensure_ascii=False, separators=(",", ":")),
            status="pending",
        )
        with self._session_factory() as session:
            session.add(wakeup)
            session.commit()
            session.refresh(wakeup)
            return wakeup

    def list_wakeups(self, status: str = "") -> list[Wakeup]:
        statement = select(Wakeup)
        if status:
            statement = statement.where(Wakeup.status == status)
        statement = statement.order_by(Wakeup.due_at.asc(), Wakeup.id.asc())
        with self._session_factory() as session:
            return list(session.scalars(statement))

    def dispatch_due(self, now: datetime, limit: int = 25) -> list[Wakeup]:
        limit = min(max(limit or 25, 1), 100)
        with self._session_factory() as session:
            statement = (
                select(Wakeup)
                .where(Wakeup.status == "pending", Wakeup.due_at <= now)
                .order_by(Wakeup.due_at.asc(), Wakeup.id.asc())
                .limit(limit)
            )
            wakeups = list(session.scalars(statement))
            for wakeup in wakeups:
                wakeup.status = "dispatched"
                wakeup.dispatched_at = now
            session.commit()
            for wakeup in wakeups:
                session.refresh(wakeup)
            return wakeups


def wakeup_payload(wakeup: Wakeup) -> dict[str, Any]:
    try:
        payload = json.loads(wakeup.payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "id": wakeup.id,
        "kind": wakeup.kind,
        "due_at": wakeup.due_at.isoformat().replace("+00:00", "Z"),
        "payload": payload,
        "status": wakeup.status,
        "dispatched_at": wakeup.dispatched_at.isoformat().replace("+00:00", "Z")
        if wakeup.dispatched_at
        else None,
        "created_at": wakeup.created_at.isoformat().replace("+00:00", "Z"),
    }
