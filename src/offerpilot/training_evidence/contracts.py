from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

Assistance = Literal["unknown", "independent", "hinted", "reference"]
Scenario = Literal["original", "retry_timeout", "concurrent_retry", "payload_conflict", "transaction_gap", "expired_receipt", "stale_revision"]


class DecisionAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rubric_version: Literal["idempotency_retry.v1"]
    question_id: StrictStr = Field(min_length=1, max_length=100)
    choice_id: StrictStr = Field(min_length=1, max_length=100)


class CommandBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=0, le=2**63 - 2)
    idempotency_key: StrictStr

    @field_validator("idempotency_key")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("canonical UUID required")
        return value


class AnswerFields(BaseModel):
    decision_answer: DecisionAnswer | None = None
    answer: StrictStr = Field(min_length=1, max_length=8000)
    assistance: Assistance

    @field_validator("answer")
    @classmethod
    def valid_answer(cls, value: str) -> str:
        value.encode("utf-8")
        if not value.strip():
            raise ValueError("answer required")
        return value


class RecordCommand(CommandBase, AnswerFields):
    action: Literal["record"]
    skill_id: Literal["idempotency_retry"]
    scenario_id: Scenario


class CorrectCommand(CommandBase, AnswerFields):
    action: Literal["correct"]
    record_id: StrictInt = Field(gt=0, le=2**63 - 1)


class WithdrawCommand(CommandBase):
    action: Literal["withdraw"]
    record_id: StrictInt = Field(gt=0, le=2**63 - 1)


Command = RecordCommand | CorrectCommand | WithdrawCommand


def parse_command(value: object) -> Command:
    if type(value) is not dict:
        raise ValueError("command required")
    action = value.get("action")
    if action == "record":
        return RecordCommand.model_validate(value)
    if action == "correct":
        return CorrectCommand.model_validate(value)
    if action == "withdraw":
        return WithdrawCommand.model_validate(value)
    raise ValueError("unknown action")
