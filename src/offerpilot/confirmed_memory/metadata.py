from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class MemoryMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["unclassified", "preference", "goal", "experience", "skill", "preparation"]
    valid_until: str | None

    @field_validator("valid_until")
    @classmethod
    def validate_deadline(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Never infer a timezone or treat numeric epochs/date-only strings as deadlines.
        parsed = datetime.fromisoformat(value)
        if "T" not in value or parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("有效期必须包含时间和时区")
        try:
            return parsed.astimezone(timezone.utc).isoformat()
        except OverflowError as exc:
            raise ValueError("有效期超出支持范围") from exc


def temporal_status(valid_until: str | None, now: datetime) -> str:
    if valid_until is None:
        return "unknown"
    deadline = datetime.fromisoformat(valid_until)
    if deadline.tzinfo is None:
        raise ValueError("memory_invalid_deadline")
    return "expired" if deadline <= now else "current"
