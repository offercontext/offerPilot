"""Credential-free, versioned display contracts; no execution dependencies."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from typing_extensions import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

DecisionState = Literal['undecided', 'approved', 'modified', 'rejected', 'cancelled', 'expired', 'not_applicable', 'unknown']
ExecutionState = Literal['not_started', 'running', 'committed', 'failed', 'unknown']
EvidenceState = Literal['verified', 'incomplete', 'unavailable']
UndoState = Literal['unsupported', 'available', 'running', 'undone', 'conflict', 'unknown']
ActionCommand = Literal['approve', 'modify', 'reject', 'undo', 'refresh']


class ActionPresentationV1(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    schema_version: Literal[1] = 1
    source_kind: Literal['agent', 'product_action']
    operation_id: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    presentation_revision: str = Field(min_length=1)
    title: str
    target: str | None = None
    summary: str
    source_label: str
    decision: DecisionState
    execution: ExecutionState
    evidence: EvidenceState
    undo: UndoState
    available_actions: list[ActionCommand] = Field(default_factory=list)

    @model_validator(mode='after')
    def legal_states(self) -> Self:
        if self.decision in {'rejected', 'cancelled', 'expired'} and self.execution in {'running', 'committed'}:
            raise ValueError('A declined operation cannot be running or committed')
        if self.decision == 'undecided' and self.execution == 'committed':
            raise ValueError('A committed operation cannot be undecided')
        if self.undo in {'available', 'running', 'undone', 'conflict'} and self.execution != 'committed':
            raise ValueError('Undo state requires an original commit')
        if len(set(self.available_actions)) != len(self.available_actions):
            raise ValueError('Duplicate display command')
        return self


class PilotTurnItemV1(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    schema_version: Literal[1] = 1
    item_id: str = Field(min_length=1)
    kind: Literal['user_message', 'assistant_message', 'action', 'error_info', 'run_boundary']
    message_id: int | None = None
    operation_id: str | None = None
    content: str = ''
    action: ActionPresentationV1 | None = None

    @model_validator(mode='after')
    def source_identity(self) -> Self:
        if self.kind in {'user_message', 'assistant_message'}:
            if self.message_id is None or self.message_id <= 0 or self.item_id != f'message:{self.message_id}':
                raise ValueError('Message display identity mismatch')
            if self.action is not None:
                raise ValueError('Messages cannot carry an action')
        if self.kind == 'action':
            if self.action is None or self.action.source_kind != 'agent' or self.operation_id != self.action.operation_id:
                raise ValueError('Action source identity mismatch')
            if self.item_id != f'agent_operation:{self.operation_id}':
                raise ValueError('Action item namespace mismatch')
        return self


class PilotPresentationSnapshotV1(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    schema_version: Literal[1] = 1
    conversation_id: int = Field(gt=0)
    items: list[PilotTurnItemV1]


def presentation_fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)
    return 'sha256:' + hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def build_action_presentation(**values: Any) -> ActionPresentationV1:
    """Opaque deterministic revision of the public projection, never an ordering key."""
    values.pop('presentation_revision', None)
    values['presentation_revision'] = presentation_fingerprint(values)
    return ActionPresentationV1.model_validate(values)
