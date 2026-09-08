"""Read-only Agent operation and conversation projections.

Display capabilities are hints to the existing command owner, never credentials.
No execution, recovery, checkpoint, or model entry point belongs in this module.
"""

from __future__ import annotations

import hmac
import json
from contextlib import nullcontext
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from offerpilot.ai.confirmation_receipt import (
    EDITED_CONFIRMATION_RECEIPT_STRATEGY,
    edited_confirmation_receipt,
)
from offerpilot.ai.write_operations import (
    WriteOperationError,
    WriteOperationRepository,
    TerminalPayload,
    _legacy_pending_confirmation_token,
    _pending_confirmation_token,
    ledger_fingerprint,
    payload_from_operation,
)
from offerpilot.models import ChatMessage, Conversation, WriteOperation
from offerpilot.presentation_contracts import (
    ActionPresentationV1,
    PilotPresentationSnapshotV1,
    PilotTurnItemV1,
    build_action_presentation,
    presentation_fingerprint,
)


class AgentActionPresentationBuilder:
    def __init__(
        self,
        repository: WriteOperationRepository,
        *,
        pending_projector: Callable[[Conversation], Mapping[str, Any]],
        pending_is_current: Callable[[Conversation, WriteOperation], bool],
        undo_projector: Callable[[Conversation, WriteOperation, TerminalPayload], str] | None = None,
    ) -> None:
        self.repository = repository
        self.pending_projector = pending_projector
        self.pending_is_current = pending_is_current
        self.undo_projector = undo_projector
        self.verified_receipts: set[str] = set()

    def build(self, operation: WriteOperation, conversation: Conversation) -> ActionPresentationV1:
        values: dict[str, Any] = {
            'source_kind': 'agent', 'operation_id': operation.id,
            'source_revision': presentation_fingerprint([
                operation.id, operation.status, operation.terminal_payload_sha256,
                operation.delivery_generation, operation.delivery_status,
                operation.updated_at, conversation.scope_revision,
                conversation.pending_operation_id, conversation.last_write_operation_id,
            ]),
            'title': 'Pilot 操作', 'target': None, 'summary': '操作状态暂时无法核实，请刷新后重试。',
            'source_label': 'Pilot', 'decision': 'unknown', 'execution': 'unknown',
            'evidence': 'unavailable', 'undo': 'unknown', 'available_actions': ['refresh'],
        }
        if (
            operation.conversation_id != conversation.id
            or operation.operation_role != 'primary'
            or operation.adapter_kind not in {'typed', 'legacy_deterministic'}
            or operation.fingerprint_key_id != self.repository.key.key_id
        ):
            return build_action_presentation(**values)
        if operation.status == 'proposed':
            if not self._pending_identity_valid(operation, conversation):
                return build_action_presentation(**values)
            try:
                current = self.pending_is_current(conversation, operation)
                projected = self.pending_projector(conversation) if current else {}
            except (TypeError, ValueError, WriteOperationError):
                return build_action_presentation(**values)
            if not current:
                values.update(decision='expired', execution='not_started', summary='提案已过期，请重新发起。', available_actions=['reject', 'refresh'])
                return build_action_presentation(**values)
            values.update(
                title=_display_text(projected.get('human')) or '待确认操作',
                summary='请核对内容后确认。', decision='undecided', execution='not_started',
                evidence='verified', undo='unsupported', available_actions=['approve', 'reject', 'refresh'],
            )
            if projected.get('editable_fields'):
                values['available_actions'].insert(1, 'modify')
            if conversation.pending_confirmation_claim_id:
                values.update(execution='running', available_actions=['refresh'])
            return build_action_presentation(**values)
        try:
            payload = payload_from_operation(operation, key=self.repository.key)
        except (TypeError, ValueError, WriteOperationError):
            return build_action_presentation(**values)
        values.update(
            execution='committed' if payload.status == 'committed' else (
                'failed' if payload.status == 'failed' else 'not_started'
            ),
            decision='rejected' if payload.status == 'rejected' else 'unknown',
            evidence='verified', undo='unknown' if payload.undo_json else 'unsupported',
            summary={'committed': '操作已保存。', 'failed': '操作未完成。', 'rejected': '已拒绝此操作。'}[payload.status],
        )
        if operation.confirmation_strategy_version == EDITED_CONFIRMATION_RECEIPT_STRATEGY:
            values['decision'] = 'modified'
        if payload.status == 'committed' and operation.confirmation_strategy_version == EDITED_CONFIRMATION_RECEIPT_STRATEGY:
            values['summary'] = edited_confirmation_receipt(
                result_json=payload.result_json,
                changed_fields=json.loads(operation.confirmation_strategy_fields_json or '[]'),
            )
            try:
                replay = self.repository.replay(operation, operation.operation_request_fingerprint or '')
                if replay.final_message == values['summary']:
                    self.verified_receipts.add(operation.id)
            except (TypeError, ValueError, WriteOperationError):
                # Delivery uncertainty does not erase a verified business commit.
                pass
        if payload.status == 'committed' and self.undo_projector is not None:
            try:
                values['undo'] = self.undo_projector(conversation, operation, payload)
                if values['undo'] == 'available':
                    values['available_actions'].insert(0, 'undo')
            except (TypeError, ValueError, WriteOperationError):
                values['undo'] = 'unknown'
        return build_action_presentation(**values)

    def _pending_identity_valid(self, operation: WriteOperation, conversation: Conversation) -> bool:
        if (
            conversation.pending_operation_id != operation.id
            or conversation.pending_tool_call_id != operation.tool_call_id
            or conversation.pending_tool_name != operation.tool_name
        ):
            return False
        try:
            if operation.adapter_kind == 'typed':
                args = json.loads(conversation.pending_args)
                if not isinstance(args, dict):
                    return False
                token = _pending_confirmation_token(operation.tool_call_id or '', operation.tool_name, args)
            else:
                token, args = _legacy_pending_confirmation_token(
                    operation.tool_call_id or '', operation.tool_name, conversation.pending_args,
                )
            return hmac.compare_digest(
                ledger_fingerprint(self.repository.key, 'write-operation-proposal-v1', args),
                operation.proposal_fingerprint or '',
            ) and hmac.compare_digest(
                ledger_fingerprint(self.repository.key, 'write-operation-confirmation-token-v1', token.encode('ascii')),
                operation.confirmation_token_fingerprint or '',
            )
        except (TypeError, ValueError):
            return False


def _display_text(value: object) -> str | None:
    return value[:2000] if isinstance(value, str) and value.strip() else None


def build_conversation_presentation(
    conversation_id: int,
    builder: AgentActionPresentationBuilder,
    *,
    source_session: Session | None = None,
) -> PilotPresentationSnapshotV1 | None:
    with (nullcontext(source_session) if source_session is not None else builder.repository.session_factory()) as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            return None
        messages = list(session.scalars(select(ChatMessage).where(
            ChatMessage.conversation_id == conversation_id,
        ).order_by(ChatMessage.id)))
        operations = list(session.scalars(select(WriteOperation).where(
            WriteOperation.conversation_id == conversation_id,
            WriteOperation.operation_role == 'primary',
            WriteOperation.adapter_kind.in_(['typed', 'legacy_deterministic']),
        ).order_by(WriteOperation.created_at, WriteOperation.id)))
        cards = {operation.id: builder.build(operation, conversation) for operation in operations}
        by_call = {operation.tool_call_id: operation.id for operation in operations}
        items: list[PilotTurnItemV1] = []
        placed: set[str] = set()

        def add_action(operation_id: str) -> None:
            if operation_id in cards and operation_id not in placed:
                items.append(PilotTurnItemV1(
                    item_id=f'agent_operation:{operation_id}', kind='action',
                    operation_id=operation_id, action=cards[operation_id],
                ))
                placed.add(operation_id)

        for message in messages:
            if message.role in {'user', 'assistant'} and message.content:
                duplicate_receipt = (
                    message.operation_id in builder.verified_receipts
                    and message.delivery_kind == 'continuation_message'
                    and message.role == 'assistant'
                    and message.content == cards[message.operation_id].summary
                )
                if not duplicate_receipt:
                    items.append(PilotTurnItemV1(
                        item_id=f'message:{message.id}', kind=(
                            'user_message' if message.role == 'user' else 'assistant_message'
                        ), message_id=message.id, content=message.content,
                    ))
            if message.tool_calls:
                try:
                    calls = json.loads(message.tool_calls)
                    for call in calls if isinstance(calls, list) else []:
                        if isinstance(call, dict) and call.get('id') in by_call:
                            add_action(by_call[call['id']])
                except (TypeError, ValueError):
                    pass
            if message.operation_id:
                add_action(message.operation_id)
        for operation in operations:
            add_action(operation.id)
        return PilotPresentationSnapshotV1(conversation_id=conversation_id, items=items)
