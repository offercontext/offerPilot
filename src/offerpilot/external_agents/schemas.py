"""Closed MCP inputs. Caller-supplied confirmation/authority is never an input."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

PositiveID = Annotated[int, Field(ge=1, le=2**63 - 1)]
EventType = Literal["written_test", "interview", "offer_step", "deadline", "custom"]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ListApplications(StrictInput):
    limit: int = Field(default=50, ge=1, le=100)
    after_id: int = Field(default=0, ge=0, le=2**63 - 1)


class GetApplication(StrictInput):
    application_id: PositiveID


class ListEvents(ListApplications):
    application_id: PositiveID | None = None
    event_type: EventType | None = None


class GetEvent(StrictInput):
    event_id: PositiveID


class RequestID(StrictInput):
    request_id: str = Field(min_length=36, max_length=36)

    @field_validator("request_id")
    @classmethod
    def valid_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("canonical UUID required")
        return value


class Schedule(StrictInput):
    idempotency_key: str = Field(min_length=36, max_length=36)
    application_id: PositiveID
    scheduled_at: str = Field(min_length=20, max_length=40)
    duration_minutes: int = Field(ge=1, le=1440)
    location: str = Field(default="", max_length=1000)

    @field_validator("idempotency_key")
    @classmethod
    def valid_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("canonical UUID required")
        return value

    @field_validator("scheduled_at")
    @classmethod
    def aware_time(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("RFC3339 timezone required")
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ProposeInterview(Schedule):
    round: int = Field(default=0, ge=0, le=100)
    notes: str = Field(default="", max_length=10000)


class ProposeReschedule(Schedule):
    event_id: PositiveID
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


INPUTS: dict[str, type[StrictInput]] = {
    "list_applications": ListApplications,
    "get_application": GetApplication,
    "list_events": ListEvents,
    "get_event": GetEvent,
    "propose_interview": ProposeInterview,
    "propose_reschedule": ProposeReschedule,
    "get_request": RequestID,
    "cancel_request": RequestID,
}

PROPOSAL_TOOLS = frozenset({"propose_interview", "propose_reschedule"})


class ApplicationData(StrictInput):
    id: PositiveID
    company_name: str
    position_name: str
    status: str
    job_url: str
    notes: str
    applied_at: str | None


class EventData(StrictInput):
    id: PositiveID
    application_id: PositiveID
    event_type: EventType
    subtype: str
    tags: list[str]
    round: int
    scheduled_at: str | None
    duration_minutes: int
    location: str
    notes: str
    remind_at: str | None
    status: str
    created_at: str | None
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class ApplicationPage(StrictInput):
    items: list[ApplicationData]
    next_after_id: PositiveID | None


class EventPage(StrictInput):
    items: list[EventData]
    next_after_id: PositiveID | None


class RequestData(StrictInput):
    request_id: str
    status: Literal["pending", "committed", "rejected", "cancelled", "expired", "conflict"]
    action: Literal["create_interview", "reschedule_interview"]
    application_id: PositiveID
    expires_at: str
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_command: list[str]
    source_available: bool
    event: EventData | None = None
    error_code: str | None = None


class StableError(StrictInput):
    code: str
    retryable: bool


DataT = TypeVar("DataT")


class MCPResult(StrictInput, Generic[DataT]):
    ok: bool
    data: DataT | None
    error: StableError | None


OUTPUTS: dict[str, type[MCPResult[Any]]] = {
    "list_applications": MCPResult[ApplicationPage],
    "get_application": MCPResult[ApplicationData],
    "list_events": MCPResult[EventPage],
    "get_event": MCPResult[EventData],
    "propose_interview": MCPResult[RequestData],
    "propose_reschedule": MCPResult[RequestData],
    "get_request": MCPResult[RequestData],
    "cancel_request": MCPResult[RequestData],
}
