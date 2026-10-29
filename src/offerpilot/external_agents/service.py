"""External request boundary, sharing domain repositories and one SQLite transaction.

Business map: docs/architecture/code-map/pilot-runtime.md. This boundary never
mints Chat Pending/Product Action proofs. Its separate operator command owns
decisions; MCP has only scoped reads, proposals, receipt lookup and cancellation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.external_agents.models import ExternalAgentConnection, ExternalAgentRequest
from offerpilot.external_agents.schemas import INPUTS, PROPOSAL_TOOLS
from offerpilot.external_agents.catalog import (
    BUSINESS_PROPOSALS,
    CAPABILITIES,
    TOOL_CAPABILITIES,
    permitted,
)
from offerpilot.models import Application, ApplicationEvent
from offerpilot.repositories.application_events import (
    ApplicationEventCreate,
    ApplicationEventsRepository,
)


class ExternalAgentError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (
        value.replace(tzinfo=value.tzinfo or timezone.utc)
        .astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@contextmanager
def transaction(sessions: sessionmaker[Session], *, write: bool = True) -> Iterator[Session]:
    try:
        with sessions() as session:
            # Lock before reading the grant, pending state or event snapshot.
            # Repository.bind(session) flushes without a separate commit.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE" if write else "BEGIN")
            yield session
            session.commit()
    except SQLAlchemyError as exc:
        raise ExternalAgentError("service_unavailable", retryable=True) from exc


def create_connection(
    sessions: sessionmaker[Session],
    name: str,
    application_ids: list[int],
    *,
    workspace: bool = False,
    allow_scheduling: bool = False,
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    """Trusted local administration; never exposed as an MCP tool."""
    if not name.strip() or len(name) > 80 or workspace == bool(application_ids):
        raise ExternalAgentError("explicit_scope_required")
    if any(type(value) is not int or not 1 <= value <= 2**63 - 1 for value in application_ids):
        raise ExternalAgentError("invalid_scope")
    granted = set(capabilities or [])
    if not granted <= CAPABILITIES:
        raise ExternalAgentError("unknown_capability")
    if ("applications.create" in granted or "notes.unbound" in granted) and not workspace:
        raise ExternalAgentError("workspace_create_required")
    if any(
        f"{kind}.write" in granted and f"{kind}.read" not in granted
        for kind in ("resumes", "notes")
    ) or ("notes.unbound" in granted and "notes.read" not in granted):
        raise ExternalAgentError("read_grant_required")
    with transaction(sessions) as session:
        for application_id in application_ids:
            application = session.get(Application, application_id)
            if application is None or application.deleted_at is not None:
                raise ExternalAgentError("application_not_found")
        connection = ExternalAgentConnection(
            id=str(uuid4()),
            name=name.strip(),
            application_ids_json=canonical(sorted(set(application_ids))),
            workspace=workspace,
            allow_scheduling=allow_scheduling,
            capabilities_json=canonical(sorted(granted)),
            active=True,
            created_at=naive_utc(utcnow()),
        )
        session.add(connection)
        session.flush()
        return connection_json(connection)


def connection_json(connection: ExternalAgentConnection) -> dict[str, Any]:
    value = {
        "connection_id": connection.id,
        "name": connection.name,
        "application_ids": json.loads(connection.application_ids_json),
        "workspace": connection.workspace,
        "allow_scheduling": connection.allow_scheduling,
        "active": connection.active,
    }
    if connection.capabilities_json and json.loads(connection.capabilities_json):
        value["capabilities"] = json.loads(connection.capabilities_json)
    return value


def row_revision(resource: Any) -> str:
    return digest(
        {
            attribute.key: timestamp(value) if isinstance(value, datetime) else value
            for attribute in resource.__mapper__.column_attrs
            for value in (getattr(resource, attribute.key),)
        }
    )


def application_json(application: Application) -> dict[str, Any]:
    return {
        "id": application.id,
        "company_name": application.company_name,
        "position_name": application.position_name,
        "status": application.status,
        "job_url": application.job_url,
        "notes": application.notes,
        "applied_at": timestamp(application.applied_at),
        "closed_reason": application.closed_reason,
        "revision": row_revision(application),
    }


def event_json(event: ApplicationEvent) -> dict[str, Any]:
    result = {
        "id": event.id,
        "application_id": event.application_id,
        "event_type": event.event_type,
        "subtype": event.subtype,
        "tags": event.tags,
        "round": event.round,
        "scheduled_at": timestamp(event.scheduled_at),
        "duration_minutes": event.duration_minutes,
        "location": event.location,
        "notes": event.notes,
        "remind_at": timestamp(event.remind_at),
        "status": event.status,
        "created_at": timestamp(event.created_at),
    }
    return {**result, "revision": digest(result)}


class ExternalAgentService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        connection_id: str,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.sessions = sessions
        self.connection_id = connection_id
        self.clock = clock

    def _connection(self, session: Session) -> ExternalAgentConnection:
        connection = session.get(ExternalAgentConnection, self.connection_id)
        if connection is None or not connection.active:
            raise ExternalAgentError("connection_unavailable")
        return connection

    def grant(self) -> dict[str, Any]:
        with transaction(self.sessions, write=False) as session:
            return connection_json(self._connection(session))

    def revoke(self) -> None:
        """Trusted local administration; existing pending requests cannot execute."""
        with transaction(self.sessions) as session:
            connection = session.get(ExternalAgentConnection, self.connection_id)
            if connection is None:
                raise ExternalAgentError("connection_unavailable")
            connection.active = False
            session.execute(
                update(ExternalAgentRequest)
                .where(
                    ExternalAgentRequest.connection_id == connection.id,
                    ExternalAgentRequest.status == "pending",
                    ExternalAgentRequest.expires_at <= naive_utc(self.clock()),
                )
                .values(status="expired")
            )
            session.execute(
                update(ExternalAgentRequest)
                .where(
                    ExternalAgentRequest.connection_id == connection.id,
                    ExternalAgentRequest.status == "pending",
                )
                .values(status="cancelled")
            )

    def _application(
        self,
        session: Session,
        connection: ExternalAgentConnection,
        application_id: int,
    ) -> Application:
        if not connection.workspace and application_id not in json.loads(
            connection.application_ids_json
        ):
            raise ExternalAgentError("scope_denied")
        application = session.get(Application, application_id)
        if application is None or application.deleted_at is not None:
            raise ExternalAgentError("application_not_found")
        return application

    def _event(
        self,
        session: Session,
        connection: ExternalAgentConnection,
        event_id: int,
    ) -> ApplicationEvent:
        statement = (
            select(ApplicationEvent)
            .join(Application)
            .where(
                ApplicationEvent.id == event_id,
                Application.deleted_at.is_(None),
            )
        )
        if not connection.workspace:
            statement = statement.where(
                ApplicationEvent.application_id.in_(json.loads(connection.application_ids_json))
            )
        event = session.scalar(statement)
        if event is None:
            raise ExternalAgentError("event_not_found")
        return event

    def _source_available(
        self,
        session: Session,
        connection: ExternalAgentConnection,
        application_id: int | None,
    ) -> bool:
        if application_id is None:
            return False
        try:
            self._application(session, connection, application_id)
        except ExternalAgentError:
            return False
        return True

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        schema = INPUTS.get(tool)
        if schema is None:
            raise ExternalAgentError("unknown_tool")
        try:
            args = schema.model_validate(arguments).model_dump(mode="json")
        except ValidationError as exc:
            raise ExternalAgentError("invalid_input") from exc
        with transaction(
            self.sessions, write=tool in PROPOSAL_TOOLS or tool in {"get_request", "cancel_request"}
        ) as session:
            connection = self._connection(session)
            if tool not in {"propose_interview", "propose_reschedule"} and not permitted(
                tool, connection_json(connection)
            ):
                raise ExternalAgentError("capability_not_granted")
            if tool in BUSINESS_PROPOSALS:
                return self._propose_business(session, connection, tool, args)
            if tool in PROPOSAL_TOOLS:
                if not connection.allow_scheduling:
                    raise ExternalAgentError("scheduling_not_granted")
                return self._propose(session, connection, tool, args)
            if tool in TOOL_CAPABILITIES:
                from offerpilot.external_agents.business import read

                return read(self, session, connection, tool, args)
            if tool in {"get_request", "cancel_request"}:
                request = session.get(ExternalAgentRequest, args["request_id"])
                if request is None or request.connection_id != self.connection_id:
                    raise ExternalAgentError("request_not_found")
                self._expire(request)
                if tool == "cancel_request" and request.status == "pending":
                    request.status = "cancelled"
                return self._request_json(session, connection, request)
            if tool == "get_application":
                return application_json(
                    self._application(session, connection, args["application_id"])
                )
            if tool == "get_event":
                return event_json(self._event(session, connection, args["event_id"]))
            if tool == "list_applications":
                statement = select(Application).where(Application.deleted_at.is_(None))
                if not connection.workspace:
                    statement = statement.where(
                        Application.id.in_(json.loads(connection.application_ids_json))
                    )
                statement = (
                    statement.where(Application.id > args["after_id"])
                    .order_by(Application.id)
                    .limit(args["limit"] + 1)
                )
                rows = list(session.scalars(statement))
                return self._page([application_json(item) for item in rows], args["limit"])
            statement_events = (
                select(ApplicationEvent).join(Application).where(Application.deleted_at.is_(None))
            )
            if not connection.workspace:
                statement_events = statement_events.where(
                    ApplicationEvent.application_id.in_(json.loads(connection.application_ids_json))
                )
            if args["application_id"] is not None:
                self._application(session, connection, args["application_id"])
                statement_events = statement_events.where(
                    ApplicationEvent.application_id == args["application_id"]
                )
            if args["event_type"] is not None:
                statement_events = statement_events.where(
                    ApplicationEvent.event_type == args["event_type"]
                )
            statement_events = (
                statement_events.where(ApplicationEvent.id > args["after_id"])
                .order_by(ApplicationEvent.id)
                .limit(args["limit"] + 1)
            )
            return self._page(
                [event_json(item) for item in session.scalars(statement_events)], args["limit"]
            )

    @staticmethod
    def _page(items: list[dict[str, Any]], limit: int) -> dict[str, Any]:
        return {
            "items": items[:limit],
            "next_after_id": items[limit - 1]["id"] if len(items) > limit else None,
        }

    def _expire(self, request: ExternalAgentRequest) -> None:
        if request.status == "pending" and request.expires_at <= naive_utc(self.clock()):
            request.status = "expired"

    def _request_json(
        self, session: Session, connection: ExternalAgentConnection, request: ExternalAgentRequest
    ) -> dict[str, Any]:
        frozen = json.loads(request.proposal_json)
        if "resource" in frozen:
            from offerpilot.external_agents.business import visible_request

            source_available = visible_request(self, session, connection, request, frozen)
        else:
            source_available = self._source_available(session, connection, request.application_id)
        value: dict[str, Any] = {
            "request_id": request.id,
            "status": request.status,
            "action": request.action,
            "application_id": request.application_id,
            "expires_at": timestamp(request.expires_at),
            "proposal_digest": request.proposal_digest,
            "source_available": source_available,
            "review_command": f"oc mcp review {request.id}",
        }
        if request.outcome_json:
            outcome = json.loads(request.outcome_json)
            # Request ownership permits cancellation/observation after deletion,
            # but cannot expose frozen business text from an invisible source.
            if not source_available:
                for field in ("event", "application", "resume", "note"):
                    outcome.pop(field, None)
            if "replacement_master" in outcome:
                from offerpilot.external_agents.business import find

                try:
                    if "resumes.read" not in json.loads(connection.capabilities_json):
                        raise ExternalAgentError("capability_not_granted")
                    find(self, session, connection, "resume", outcome["replacement_master"]["id"])
                except ExternalAgentError:
                    outcome.pop("replacement_master", None)
            value.update(outcome)
        return value

    def _propose(
        self,
        session: Session,
        connection: ExternalAgentConnection,
        tool: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        prior = session.scalar(
            select(ExternalAgentRequest).where(
                ExternalAgentRequest.connection_id == connection.id,
                ExternalAgentRequest.idempotency_key == args["idempotency_key"],
            )
        )
        if prior is not None:
            if prior.input_digest != digest({"tool": tool, "args": args}):
                raise ExternalAgentError("idempotency_conflict")
            self._expire(prior)
            return self._request_json(session, connection, prior)
        application = self._application(session, connection, args["application_id"])
        before = None
        if tool == "propose_reschedule":
            event = self._event(session, connection, args["event_id"])
            if event.application_id != application.id or event.event_type != "interview":
                raise ExternalAgentError("interview_required")
            before = event_json(event)
            if before["revision"] != args["expected_revision"]:
                raise ExternalAgentError("event_changed")
        reminder = None
        if before is not None and before["remind_at"] is not None:
            if before["scheduled_at"] is None:
                raise ExternalAgentError("invalid_interview_schedule")
            old_time = datetime.fromisoformat(before["scheduled_at"].replace("Z", "+00:00"))
            old_reminder = datetime.fromisoformat(before["remind_at"].replace("Z", "+00:00"))
            new_time = datetime.fromisoformat(args["scheduled_at"].replace("Z", "+00:00"))
            reminder = timestamp(new_time + (old_reminder - old_time))
        after = {
            "application_id": application.id,
            "event_type": "interview",
            "scheduled_at": args["scheduled_at"],
            "duration_minutes": args["duration_minutes"],
            "location": args["location"],
            "round": args.get("round", before["round"] if before else 0),
            "notes": args.get("notes", before["notes"] if before else ""),
            "subtype": before["subtype"] if before else "",
            "tags": before["tags"] if before else [],
            "remind_at": reminder,
            "status": before["status"] if before else "todo",
        }
        frozen = {
            "input": args,
            "before": before,
            "after": after,
            "application": {
                "id": application.id,
                "company_name": application.company_name,
                "position_name": application.position_name,
            },
            "connection": connection_json(connection),
        }
        now = naive_utc(self.clock())
        request = ExternalAgentRequest(
            id=str(uuid4()),
            connection_id=connection.id,
            idempotency_key=args["idempotency_key"],
            input_digest=digest({"tool": tool, "args": args}),
            proposal_digest=digest(frozen),
            action="create_interview" if before is None else "reschedule_interview",
            application_id=application.id,
            proposal_json=canonical(frozen),
            status="pending",
            created_at=now,
            expires_at=now + timedelta(minutes=15),
        )
        session.add(request)
        session.flush()
        return self._request_json(session, connection, request)

    def _propose_business(
        self, session: Session, connection: ExternalAgentConnection, tool: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        from offerpilot.external_agents.business import prepare
        from offerpilot.repositories.notes import NoteBindingError

        prior = session.scalar(
            select(ExternalAgentRequest).where(
                ExternalAgentRequest.connection_id == connection.id,
                ExternalAgentRequest.idempotency_key == args["idempotency_key"],
            )
        )
        if prior is not None:
            if prior.input_digest != digest({"tool": tool, "args": args}):
                raise ExternalAgentError("idempotency_conflict")
            self._expire(prior)
            return self._request_json(session, connection, prior)
        try:
            application_id, frozen = prepare(
                self, session, connection, BUSINESS_PROPOSALS[tool], args
            )
        except NoteBindingError as exc:
            raise ExternalAgentError("note_binding_conflict") from exc
        frozen.update({"input": args, "tool": tool, "connection": connection_json(connection)})
        now = naive_utc(self.clock())
        request = ExternalAgentRequest(
            id=str(uuid4()),
            connection_id=connection.id,
            idempotency_key=args["idempotency_key"],
            input_digest=digest({"tool": tool, "args": args}),
            proposal_digest=digest(frozen),
            action=BUSINESS_PROPOSALS[tool],
            application_id=application_id,
            proposal_json=canonical(frozen),
            status="pending",
            created_at=now,
            expires_at=now + timedelta(minutes=15),
        )
        session.add(request)
        session.flush()
        return self._request_json(session, connection, request)

    def _decide_business(
        self,
        session: Session,
        connection: ExternalAgentConnection,
        request: ExternalAgentRequest,
        frozen: dict[str, Any],
    ) -> dict[str, Any]:
        from offerpilot.external_agents.business import recheck, execute
        from offerpilot.repositories.notes import NoteBindingError

        if not permitted(frozen["tool"], connection_json(connection)):
            raise ExternalAgentError("capability_not_granted")
        try:
            if connection_json(connection) != frozen["connection"]:
                raise ExternalAgentError("proposal_source_changed")
            recheck(self, session, connection, request.action, frozen["input"], frozen)
            with session.begin_nested():
                outcome = execute(self, session, request.action, frozen)
        except (ExternalAgentError, NoteBindingError, ValueError) as exc:
            request.status = "conflict"
            request.outcome_json = canonical(
                {
                    "error_code": exc.code
                    if isinstance(exc, ExternalAgentError)
                    else "business_conflict"
                }
            )
            return self._request_json(session, connection, request)
        request.status = "committed"
        request.outcome_json = canonical(outcome)
        if request.action == "create_application":
            request.application_id = outcome["application"]["id"]
        return self._request_json(session, connection, request)

    def review(self, request_id: str) -> dict[str, Any]:
        """Operator-only preview. Neither this method nor decide is an MCP tool."""
        with transaction(self.sessions) as session:
            connection = self._connection(session)
            request = session.get(ExternalAgentRequest, request_id)
            if request is None or request.connection_id != connection.id:
                raise ExternalAgentError("request_not_found")
            self._expire(request)
            return {
                **self._request_json(session, connection, request),
                "proposal": json.loads(request.proposal_json),
            }

    def decide(
        self,
        request_id: str,
        proposal_digest: str,
        decision: Literal["approve", "reject"],
    ) -> dict[str, Any]:
        """Trusted operator boundary; no wire/boolean confirmation capability."""
        if decision not in {"approve", "reject"}:
            raise ExternalAgentError("invalid_decision")
        with transaction(self.sessions) as session:
            connection = self._connection(session)
            request = session.get(ExternalAgentRequest, request_id)
            if request is None or request.connection_id != connection.id:
                raise ExternalAgentError("request_not_found")
            if proposal_digest != request.proposal_digest:
                raise ExternalAgentError("proposal_changed")
            frozen = json.loads(request.proposal_json)
            if digest(frozen) != proposal_digest:
                raise ExternalAgentError("proposal_changed")
            self._expire(request)
            if request.status != "pending":
                return self._request_json(session, connection, request)
            if decision == "reject":
                request.status = "rejected"
                return self._request_json(session, connection, request)
            if "resource" in frozen:
                return self._decide_business(session, connection, request, frozen)
            if not connection.allow_scheduling:
                raise ExternalAgentError("scheduling_not_granted")
            # Revalidate the exact preview's mutable ownership and event snapshot
            # under the same lock that protects the request receipt and domain write.
            try:
                if request.application_id is None:
                    raise ExternalAgentError("proposal_source_changed")
                application = self._application(session, connection, request.application_id)
                if frozen["application"] != {
                    "id": application.id,
                    "company_name": application.company_name,
                    "position_name": application.position_name,
                } or frozen["connection"] != connection_json(connection):
                    raise ExternalAgentError("proposal_source_changed")
                before = frozen["before"]
                current = None
                if before is not None:
                    current = self._event(session, connection, before["id"])
                    if event_json(current) != before:
                        raise ExternalAgentError("event_changed")
            except ExternalAgentError as exc:
                request.status = "conflict"
                request.outcome_json = canonical({"error_code": exc.code})
                return self._request_json(session, connection, request)
            args = frozen["after"]
            data = ApplicationEventCreate(
                application_id=application.id,
                event_type="interview",
                scheduled_at=datetime.fromisoformat(args["scheduled_at"].replace("Z", "+00:00")),
                duration_minutes=args["duration_minutes"],
                location=args["location"],
                round=args["round"],
                notes=args["notes"],
                subtype=args["subtype"],
                tags=args["tags"],
                remind_at=datetime.fromisoformat(args["remind_at"].replace("Z", "+00:00"))
                if args["remind_at"]
                else None,
                status=args["status"],
            )
            repository = ApplicationEventsRepository(self.sessions).bind(session)
            result = (
                repository.create(data) if current is None else repository.update(current.id, data)
            )
            if result is None:
                raise ExternalAgentError("event_not_found")
            request.status = "committed"
            request.outcome_json = canonical({"event": event_json(result)})
            return self._request_json(session, connection, request)
