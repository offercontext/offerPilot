"""Strict user-review inputs. Mail content cannot carry an approval or a tool call."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator


FULL_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})"
)


def require_full_timestamp(value: object) -> object:
    if value is not None and not isinstance(value, datetime):
        if not isinstance(value, str) or FULL_TIMESTAMP.fullmatch(value) is None:
            raise ValueError("请填写包含日期、时间与时区的完整时间")
    return value


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventEdits(StrictInput):
    event_type: Literal["written_test", "interview", "custom"] | None = None
    subtype: StrictStr | None = Field(default=None, max_length=80)
    scheduled_at: datetime | None = None
    duration_minutes: StrictInt | None = Field(default=None, ge=1, le=10080)
    location: StrictStr | None = Field(default=None, max_length=2000)
    notes: StrictStr | None = Field(default=None, max_length=10000)
    round: StrictInt | None = Field(default=None, ge=0, le=100)
    tags: list[StrictStr] | None = Field(default=None, max_length=20)
    remind_at: datetime | None = None

    @field_validator("scheduled_at", "remind_at", mode="before")
    @classmethod
    def timestamps_are_strings(cls, value: object) -> object:
        return require_full_timestamp(value)

    @field_validator("scheduled_at", "remind_at")
    @classmethod
    def timestamps_have_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("时间必须包含明确时区")
            return value.astimezone(timezone.utc)
        return None

    @field_validator("tags")
    @classmethod
    def bounded_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(len(tag) > 80 for tag in value):
            raise ValueError("标签过长")
        return value


class ReviewRequest(StrictInput):
    operation_id: str
    suggestion_version: StrictInt = Field(ge=1)
    application_id: StrictInt = Field(ge=1)
    target_event_id: StrictInt | None = Field(default=None, ge=1)
    edited_fields: EventEdits = Field(default_factory=EventEdits)

    @field_validator("operation_id")
    @classmethod
    def canonical_operation_id(cls, value: str) -> str:
        return str(UUID(value))


class ConfirmRequest(ReviewRequest):
    preview_token: str = Field(min_length=32, max_length=128)
    explicit_confirmation: StrictBool

    @field_validator("explicit_confirmation")
    @classmethod
    def user_confirmation_required(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("邮件业务变更必须由用户明确确认")
        return value


class IgnoreRequest(StrictInput):
    suggestion_version: StrictInt = Field(ge=1)


class ImportRequest(StrictInput):
    subject: StrictStr = Field(default="", max_length=1000)
    sender: StrictStr = Field(default="", max_length=320)
    received_at: datetime
    body_text: StrictStr = Field(min_length=1, max_length=100000)

    @field_validator("received_at", mode="before")
    @classmethod
    def full_received_timestamp(cls, value: object) -> object:
        return require_full_timestamp(value)

    @field_validator("received_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("接收时间必须包含时区")
        return value.astimezone(timezone.utc)


class SettingsRequest(StrictInput):
    sync_mode: Literal["manual", "automatic"]
    interval_minutes: StrictInt = Field(ge=5, le=1440)
    ai_enabled: StrictBool = False
    folders: list[StrictStr] | None = Field(default=None, min_length=1, max_length=20)


class ConnectionRequest(StrictInput):
    provider: Literal["synthetic", "qq"] = "qq"
    email: StrictStr = Field(default="", max_length=320)
    folders: list[StrictStr] = Field(min_length=1, max_length=20)
    backfill_days: Literal[0, 7] = 0
