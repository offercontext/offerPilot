"""Bounded, source-only repair of Pilot display and missing historical bindings."""

from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from offerpilot.models import (
    ChatMessage, PilotTurnRecord, PilotTurnMessage, PilotTurnOperation, WriteOperation,
)
from offerpilot.pilot_timeline import TimelineSource
from offerpilot.presentation import AgentActionPresentationBuilder, build_conversation_presentation
from offerpilot.presentation_contracts import PilotTurnItemV1


def build_timeline_sources(
    session: Session, conversation_id: int, builder: AgentActionPresentationBuilder,
) -> list[TimelineSource]:
    messages = list(session.scalars(select(ChatMessage).where(
        ChatMessage.conversation_id == conversation_id,
    ).order_by(ChatMessage.id).limit(4097)))
    operations = list(session.scalars(select(WriteOperation).where(
        WriteOperation.conversation_id == conversation_id,
        WriteOperation.operation_role == "primary",
    ).order_by(WriteOperation.created_at, WriteOperation.id).limit(4097)))
    if len(messages) > 4096 or len(operations) > 4096:
        raise ValueError("Timeline source repair exceeds its bounded batch")
    turns = {row.id: row for row in session.scalars(select(PilotTurnRecord).where(
        PilotTurnRecord.conversation_id == conversation_id,
    ))}
    message_turns = {message_id: turn_id for message_id, turn_id in session.execute(select(PilotTurnMessage.message_id, PilotTurnMessage.turn_id).join(
        ChatMessage, ChatMessage.id == PilotTurnMessage.message_id,
    ).where(ChatMessage.conversation_id == conversation_id))}
    operation_turns = {operation_id: turn_id for operation_id, turn_id in session.execute(select(PilotTurnOperation.operation_id, PilotTurnOperation.turn_id).join(
        WriteOperation, WriteOperation.id == PilotTurnOperation.operation_id,
    ).where(WriteOperation.conversation_id == conversation_id))}

    def historical_turn(user_message_id: int | None = None) -> str:
        turn = PilotTurnRecord(
            id=str(uuid4()), conversation_id=conversation_id, user_message_id=user_message_id,
            state="incomplete", source_versions_json="{}",
        )
        session.add(turn)
        session.flush()
        turns[turn.id] = turn
        return turn.id

    legacy_turn: str | None = None
    calls: dict[str, set[str]] = {}
    for message in messages:
        turn_id = message_turns.get(message.id)
        if turn_id is None:
            turn_id = operation_turns.get(message.operation_id or "")
            if turn_id is None:
                if message.role == "user" or legacy_turn is None:
                    legacy_turn = historical_turn(message.id if message.role == "user" else None)
                turn_id = legacy_turn
            session.add(PilotTurnMessage(message_id=message.id, turn_id=turn_id))
            message_turns[message.id] = turn_id
        elif message.role == "user":
            # Do not attribute unbound facts to a modern Turn by proximity.
            legacy_turn = turn_id if turns[turn_id].state == "incomplete" else None
        if message.tool_calls:
            try:
                parsed = json.loads(message.tool_calls)
                for call in parsed if isinstance(parsed, list) else []:
                    if isinstance(call, dict) and isinstance(call.get("id"), str):
                        calls.setdefault(call["id"], set()).add(turn_id)
            except ValueError:
                pass
    for operation in operations:
        if operation.id not in operation_turns:
            candidates = calls.get(operation.tool_call_id or "", set())
            turn_id = next(iter(candidates)) if len(candidates) == 1 else historical_turn()
            session.add(PilotTurnOperation(operation_id=operation.id, turn_id=turn_id))
            operation_turns[operation.id] = turn_id
    session.flush()
    snapshot = build_conversation_presentation(conversation_id, builder, source_session=session)
    if snapshot is None:
        return []
    sources: list[TimelineSource] = []
    seen_turns: set[str] = set()
    labels = {
        "accepted": "任务已接纳，执行结果尚未确认。",
        "started": "本轮结束状态尚未记录，以下为已保存记录。",
        "completed": "本轮记录已保存。",
        "failed": "本轮未完成，以下为已保存记录。",
        "interrupted": "本轮已中断，以下为已保存记录。",
        "incomplete": "历史记录，过程信息不完整。",
    }
    for item in snapshot.items:
        turn_id = (
            message_turns.get(item.message_id) if item.message_id is not None
            else operation_turns.get(item.operation_id or "")
        )
        if turn_id is None:
            raise ValueError("Timeline source has no durable identity")
        turn = turns[turn_id]
        if turn_id not in seen_turns:
            seen_turns.add(turn_id)
            sources.append(TimelineSource(
                turn_id=turn_id,
                item=PilotTurnItemV1(
                    item_id=f"run:{turn_id}", kind="run_boundary", content=labels[turn.state],
                ),
                source_refs=(f"turn:{turn_id}",), source_revision=turn.state,
            ))
        sources.append(TimelineSource(
            turn_id=turn_id, item=item, source_refs=(item.item_id,),
        ))
    return sources
