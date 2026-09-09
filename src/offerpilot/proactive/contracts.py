from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator


class ProactivePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool = False
    reminders_enabled: StrictBool = False
    drafts_enabled: StrictBool = False
    application_ids: list[StrictInt] = Field(default_factory=list, max_length=100)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)
    quiet_start_hour: StrictInt = Field(default=22, ge=0, le=23)
    quiet_end_hour: StrictInt = Field(default=8, ge=0, le=23)
    max_reminders_per_day: StrictInt = Field(default=4, ge=1, le=20)
    max_drafts_per_day: StrictInt = Field(default=1, ge=1, le=3)

    @field_validator("application_ids")
    @classmethod
    def valid_scope(cls, value: list[int]) -> list[int]:
        if any(item <= 0 or item > 2**63 - 1 for item in value) or len(set(value)) != len(value):
            raise ValueError("application_ids must be unique positive integers")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown timezone") from exc
        return value


class ProactivePolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=0)
    confirmed: StrictBool
    settings: ProactivePolicy
