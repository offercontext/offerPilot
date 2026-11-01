"""Closed MCP inputs. Caller-supplied confirmation/authority is never an input."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from offerpilot.external_agents.catalog import BUSINESS_PROPOSALS

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

PROPOSAL_TOOLS = frozenset({"propose_interview", "propose_reschedule", *BUSINESS_PROPOSALS})


class ApplicationData(StrictInput):
    id: PositiveID
    company_name: str
    position_name: str
    status: str
    job_url: str
    notes: str
    applied_at: str | None
    closed_reason: str = ""
    revision: str = ""


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
    action: Literal[
        "create_interview",
        "reschedule_interview",
        "create_application",
        "update_application",
        "delete_application",
        "create_resume",
        "update_resume",
        "copy_resume",
        "delete_resume",
        "create_note",
        "update_note",
        "delete_note",
    ]
    application_id: PositiveID | None
    expires_at: str
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_command: list[str]
    source_available: bool
    event: EventData | None = None
    application: ApplicationData | None = None
    resume: dict[str, Any] | None = None
    note: dict[str, Any] | None = None
    deleted: bool | None = None
    resource_id: PositiveID | None = None
    replacement_master: dict[str, Any] | None = None
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


class ProposalInput(StrictInput):
    idempotency_key: str = Field(min_length=36, max_length=36)

    @field_validator("idempotency_key")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("canonical UUID required")
        return value


class Changes(StrictInput):
    @model_validator(mode="after")
    def bounded_nonempty(self) -> "Changes":
        if not self.model_fields_set:
            raise ValueError("changes required")
        try:
            raw = json.dumps(self.selected_fields(), ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("JSON changes required") from exc
        if len(raw.encode("utf-8")) > 200000:
            raise ValueError("changes too large")
        return self

    @model_serializer
    def selected_fields(self) -> dict[str, Any]:
        # Defaults make fields optional in JSON Schema, but omission must never
        # clear saved fields. Only explicit patch keys cross the proposal seam.
        return {key: getattr(self, key) for key in self.model_fields_set}


class ApplicationChanges(Changes):
    company_name: str = Field(default="", min_length=1, max_length=200)
    position_name: str = Field(default="", min_length=1, max_length=200)
    job_url: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=20000)
    status: Literal["pending", "applied", "written_test", "interview", "offer", "closed"] = (
        "applied"
    )
    closed_reason: str = Field(default="", max_length=1000)


class ApplicationCreateInput(ProposalInput):
    company_name: str = Field(min_length=1, max_length=200)
    position_name: str = Field(min_length=1, max_length=200)
    job_url: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=20000)
    status: Literal["pending", "applied", "written_test", "interview", "offer", "closed"] = (
        "applied"
    )
    closed_reason: str = Field(default="", max_length=1000)


Revision = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ApplicationTarget(ProposalInput):
    application_id: PositiveID
    expected_revision: Revision


class ApplicationUpdateInput(ApplicationTarget):
    changes: ApplicationChanges


class ResumeData(StrictInput):
    id: PositiveID
    title: str
    is_master: bool
    parent_resume_id: PositiveID | None
    source: str
    parse_status: str
    text: str
    content_json: dict[str, Any]
    revision: Revision


class ResumeChanges(Changes):
    title: str = Field(default="", min_length=1, max_length=200)
    text: str = Field(default="", max_length=60000)
    content_json: dict[str, Any] = Field(default_factory=dict)
    is_master: bool = False

    @field_validator("content_json")
    @classmethod
    def valid_raw_text(cls, value: dict[str, Any]) -> dict[str, Any]:
        if "raw_text" in value and not isinstance(value["raw_text"], str):
            raise ValueError("raw_text must be a string")
        return value


class ResumeCreateInput(ProposalInput):
    title: str = Field(default="未命名简历", min_length=1, max_length=200)
    text: str = Field(default="", max_length=60000)
    content_json: dict[str, Any] = Field(default_factory=dict)
    is_master: bool | None = None

    @field_validator("content_json")
    @classmethod
    def bounded_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        if "raw_text" in value and not isinstance(value["raw_text"], str):
            raise ValueError("raw_text must be a string")
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 200000:
            raise ValueError("resume content too large")
        return value


class GetResume(StrictInput):
    resume_id: PositiveID


class ResumeTarget(ProposalInput):
    resume_id: PositiveID
    expected_revision: Revision


class ResumeUpdateInput(ResumeTarget):
    changes: ResumeChanges


class ResumeCopyInput(ResumeTarget):
    title: str = Field(min_length=1, max_length=200)


class NoteFields(StrictInput):
    company: str = Field(default="", max_length=200)
    position: str = Field(default="", max_length=200)
    round: str = Field(default="", max_length=100)
    date: str = Field(default="", max_length=100)
    questions: str = Field(default="", max_length=60000)
    self_reflection: str = Field(default="", max_length=60000)
    difficulty_points: str = Field(default="", max_length=60000)
    mood: str = Field(default="", max_length=1000)


class NoteCreateInput(ProposalInput, NoteFields):
    application_id: PositiveID | None = None
    application_event_id: PositiveID | None = None


class NoteChanges(Changes, NoteFields):
    application_event_id: PositiveID | None = None


class NoteTarget(ProposalInput):
    note_id: PositiveID
    expected_revision: Revision


class NoteUpdateInput(NoteTarget):
    changes: NoteChanges


class GetNote(StrictInput):
    note_id: PositiveID


class ListNotes(ListApplications):
    application_id: PositiveID | None = None


class NoteData(NoteFields):
    id: PositiveID
    application_id: PositiveID | None
    application_event_id: PositiveID | None
    content_revision: int
    revision: Revision


class ResumePage(StrictInput):
    items: list[ResumeData]
    next_after_id: PositiveID | None


class NotePage(StrictInput):
    items: list[NoteData]
    next_after_id: PositiveID | None


class KnowledgeSearch(StrictInput):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=20)
    max_bytes: int = Field(default=65536, ge=4096, le=262144)


class KnowledgeData(StrictInput):
    items: list[dict[str, Any]]
    omitted_count: int


INPUTS.update(
    {
        "propose_application_create": ApplicationCreateInput,
        "propose_application_update": ApplicationUpdateInput,
        "propose_application_delete": ApplicationTarget,
        "list_resumes": ListApplications,
        "get_resume": GetResume,
        "propose_resume_create": ResumeCreateInput,
        "propose_resume_update": ResumeUpdateInput,
        "propose_resume_copy": ResumeCopyInput,
        "propose_resume_delete": ResumeTarget,
        "list_notes": ListNotes,
        "get_note": GetNote,
        "propose_note_create": NoteCreateInput,
        "propose_note_update": NoteUpdateInput,
        "propose_note_delete": NoteTarget,
        "search_knowledge": KnowledgeSearch,
    }
)
OUTPUTS.update({name: MCPResult[RequestData] for name in BUSINESS_PROPOSALS})
OUTPUTS.update(
    {
        "list_resumes": MCPResult[ResumePage],
        "get_resume": MCPResult[ResumeData],
        "list_notes": MCPResult[NotePage],
        "get_note": MCPResult[NoteData],
        "search_knowledge": MCPResult[KnowledgeData],
    }
)
