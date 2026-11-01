from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt


class CompanionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool = True
    proactivity: Literal["off", "less", "default"] = "default"
    hide_status: StrictBool = False


class CompanionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity: UUID
    expected_revision: StrictInt = Field(ge=0)
    command_id: UUID


class CompanionEventCommand(CompanionCommand):
    event: Literal[
        "app_open", "chat_end", "work_completed", "user_interaction",
        "pause", "resume", "abandon", "join", "complete_shared", "withdraw_shared", "decline_wish", "accept_wish",
        "quiet_session", "resume_session",
    ]
    activity_id: UUID | None = None
    wish_id: UUID | None = None


class CompanionSettingsCommand(CompanionCommand):
    settings: CompanionPolicy


class CompanionResetCommand(CompanionCommand):
    confirmed: StrictBool


class CompanionPresenceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity: UUID
    client_id: UUID
    sequence: StrictInt = Field(ge=0)
    visible: StrictBool = True
    typing: StrictBool = False
    work_running: StrictBool = False
    awaiting_approval: StrictBool = False
    mock_interview: StrictBool = False
    quiet: StrictBool = False
    failure: StrictBool = False
