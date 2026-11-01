"""Opt-in business adapters. Pending/expiry/decision/receipt ownership stays in service.

Map: docs/architecture/code-map/pilot-runtime.md. Global resources require their
own grants. Domain repositories own writes and are bound to the caller's lock.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, cast

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from offerpilot.context_projector.contracts import ProjectionError
from offerpilot.context_sources.knowledge import recall_knowledge
from offerpilot.external_agents.models import ExternalAgentConnection, ExternalAgentRequest
from offerpilot.external_agents.service import (
    ExternalAgentError,
    ExternalAgentService,
    application_json,
    canonical,
    digest,
    event_json,
    row_revision,
)
from offerpilot.models import APPLICATION_FOREIGN_KEY_MODELS, Application, InterviewNote, Resume
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.notes import NoteCreate, NoteUpdate, NotesRepository
from offerpilot.repositories.resumes import ResumeCreate, ResumesRepository
from offerpilot.resume_rules import is_empty_resume_draft
from offerpilot.schemas import normalize_resume_content

NOTE_FIELDS = (
    "company",
    "position",
    "round",
    "date",
    "questions",
    "self_reflection",
    "difficulty_points",
    "mood",
)


def revision(resource: Any) -> str:
    # Include monotonic Note revision and all stored metadata in the CAS, while
    # keeping local file paths out of the model-visible projection.
    return row_revision(resource)


def resume_json(resume: Resume) -> dict[str, Any]:
    content = normalize_resume_content(resume.content_json)
    if not content and resume.parsed_data:
        content = {"raw_text": resume.parsed_data}
    return {
        "id": resume.id,
        "title": resume.title,
        "is_master": resume.is_master,
        "parent_resume_id": resume.parent_resume_id,
        "source": resume.source,
        "parse_status": resume.parse_status,
        "text": resume.parsed_data,
        "content_json": content,
        "revision": revision(resume),
    }


def note_json(note: InterviewNote) -> dict[str, Any]:
    return {
        "id": note.id,
        "application_id": note.application_id,
        "application_event_id": note.application_event_id,
        "content_revision": note.content_revision,
        "revision": revision(note),
        **{key: getattr(note, key) for key in NOTE_FIELDS},
    }


def note_statement(connection: ExternalAgentConnection) -> Any:
    allowed_bound = InterviewNote.application_id.is_not(None) & Application.deleted_at.is_(None)
    if not connection.workspace:
        allowed_bound &= InterviewNote.application_id.in_(
            json.loads(connection.application_ids_json)
        )
    allow_unbound = connection.workspace and "notes.unbound" in json.loads(
        connection.capabilities_json
    )
    condition = (
        or_(allowed_bound, InterviewNote.application_id.is_(None))
        if allow_unbound
        else allowed_bound
    )
    return (
        select(InterviewNote)
        .outerjoin(Application, InterviewNote.application_id == Application.id)
        .where(condition)
    )


def find(
    service: ExternalAgentService,
    session: Session,
    connection: ExternalAgentConnection,
    kind: str,
    resource_id: int,
) -> Any:
    if kind == "application":
        return service._application(session, connection, resource_id)
    if kind == "resume":
        resource = session.scalar(
            select(Resume).where(Resume.id == resource_id, Resume.deleted_at.is_(None))
        )
    else:
        resource = session.scalar(note_statement(connection).where(InterviewNote.id == resource_id))
    if resource is None:
        raise ExternalAgentError(f"{kind}_not_found")
    return resource


def project(kind: str, resource: Any) -> dict[str, Any]:
    if kind == "application":
        return {
            **application_json(resource),
            "closed_reason": resource.closed_reason,
            "revision": revision(resource),
        }
    return resume_json(resource) if kind == "resume" else note_json(resource)


def visible_request(
    service: ExternalAgentService,
    session: Session,
    connection: ExternalAgentConnection,
    request: ExternalAgentRequest,
    frozen: dict[str, Any],
) -> bool:
    kind = frozen["resource"]["kind"]
    if kind != "application" and f"{kind}s.read" not in json.loads(connection.capabilities_json):
        return False
    resource_id = frozen["resource"]["id"]
    if resource_id is None and request.outcome_json:
        resource_id = json.loads(request.outcome_json).get("resource_id")
    try:
        if request.application_id is not None:
            service._application(session, connection, request.application_id)
        if resource_id is not None:
            find(service, session, connection, kind, resource_id)
        elif kind == "application" and not connection.workspace:
            return False
    except ExternalAgentError:
        return False
    return True


def read(
    service: ExternalAgentService,
    session: Session,
    connection: ExternalAgentConnection,
    tool: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    if tool == "search_knowledge":
        if not args["query"].strip():
            raise ExternalAgentError("invalid_input")
        raw = cast(sqlite3.Connection, session.connection().connection.driver_connection)
        try:
            candidates = recall_knowledge(raw, args["query"])
        except ProjectionError as exc:
            raise ExternalAgentError("knowledge_integrity_failed") from exc
        except sqlite3.Error as exc:
            raise ExternalAgentError("service_unavailable", retryable=True) from exc
        selected: list[dict[str, object]] = []
        size = 0
        for item in candidates:
            amount = len(canonical(item).encode("utf-8"))
            if len(selected) < args["limit"] and size + amount <= args["max_bytes"]:
                selected.append(item)
                size += amount
        return {"items": selected, "omitted_count": len(candidates) - len(selected)}
    kind = "resume" if "resume" in tool else "note"
    if tool.startswith("get_"):
        return project(kind, find(service, session, connection, kind, args[f"{kind}_id"]))
    if kind == "resume":
        statement = (
            select(Resume)
            .where(Resume.deleted_at.is_(None), Resume.id > args["after_id"])
            .order_by(Resume.id)
        )
    else:
        statement = (
            note_statement(connection)
            .where(InterviewNote.id > args["after_id"])
            .order_by(InterviewNote.id)
        )
        if args["application_id"] is not None:
            service._application(session, connection, args["application_id"])
            statement = statement.where(InterviewNote.application_id == args["application_id"])
    items = [project(kind, item) for item in session.scalars(statement.limit(args["limit"] + 1))]
    return service._page(items, args["limit"])


def resume_collection(session: Session) -> list[dict[str, Any]]:
    return [
        resume_json(item)
        for item in session.scalars(
            select(Resume).where(Resume.deleted_at.is_(None)).order_by(Resume.id)
        )
    ]


def application_dependencies(session: Session, application_id: int) -> list[dict[str, Any]]:
    # A parent revision does not change when a child is inserted or edited.
    # Freeze every domain dependency under the same lock, without disclosing
    # its body or local paths. An old deletion approval cannot hide new work.
    return [
        {"resource": model.__tablename__, "count": len(rows), "revision": digest(rows)}
        for model in APPLICATION_FOREIGN_KEY_MODELS
        for rows in (
            [
                row_revision(item)
                for item in session.scalars(
                    select(model).where(model.application_id == application_id).order_by(model.id)
                )
            ],
        )
    ]


def app_fields(application: Application | None, args: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "company_name",
        "position_name",
        "job_url",
        "status",
        "source",
        "notes",
        "closed_reason",
    )
    values = (
        {key: getattr(application, key) for key in fields}
        if application
        else {
            "company_name": "",
            "position_name": "",
            "job_url": "",
            "status": "applied",
            "source": "external_agent",
            "notes": "",
            "closed_reason": "",
        }
    )
    values.update({key: value for key, value in args.items() if key in fields})
    for key in ("company_name", "position_name", "closed_reason"):
        values[key] = values[key].strip()
    if not values["company_name"] or not values["position_name"]:
        raise ExternalAgentError("invalid_input")
    if values["status"] == "closed" and not values["closed_reason"]:
        raise ExternalAgentError("closed_reason_required")
    if application and application.status == "closed" and values["status"] != "closed":
        raise ExternalAgentError("closed_application_terminal")
    if values["status"] != "closed":
        values["closed_reason"] = ""
    return values


def prepare(
    service: ExternalAgentService,
    session: Session,
    connection: ExternalAgentConnection,
    action: str,
    args: dict[str, Any],
) -> tuple[int | None, dict[str, Any]]:
    kind = action.split("_", 1)[1]
    operation = action.split("_", 1)[0]
    resource: Any = None
    before = None
    if operation != "create":
        resource = find(service, session, connection, kind, args[f"{kind}_id"])
        before = project(kind, resource)
        if before["revision"] != args["expected_revision"]:
            raise ExternalAgentError("resource_changed")
    application_id: int | None = None
    frozen: dict[str, Any] = {
        "resource": {"kind": kind, "id": resource.id if resource else None},
        "before": before,
    }
    if kind == "application":
        if operation == "create" and not connection.workspace:
            raise ExternalAgentError("workspace_create_required")
        application_id = resource.id if resource else None
        after = (
            {"deleted": True, "resource_id": resource.id}
            if operation == "delete"
            else app_fields(resource, args.get("changes", args))
        )
        if operation == "delete":
            frozen["dependencies"] = application_dependencies(session, resource.id)
            frozen["effects"] = (
                "软删除此投递并隐藏下列关联业务内容；不物理清空历史记录。"
                "关联面试笔记已捕获的 Knowledge 将因来源不可见停止检索。"
            )
    elif kind == "resume":
        library = resume_collection(session)
        frozen["collection_revision"] = digest(library)
        frozen["current_masters"] = [
            {"id": row["id"], "title": row["title"]} for row in library if row["is_master"]
        ]
        if operation == "delete":
            if resource.is_master and not is_empty_resume_draft(resource):
                raise ExternalAgentError("master_resume_cannot_delete")
            after = {"deleted": True, "resource_id": resource.id}
            replacement = next((row for row in library if not row["is_master"]), None)
            if resource.is_master and replacement:
                after["replacement_master"] = {
                    "id": replacement["id"],
                    "title": replacement["title"],
                    "is_master": True,
                }
        elif operation == "copy":
            after = {"title": args["title"], "parent_resume_id": resource.id, "is_master": False}
        else:
            after = (
                {
                    "title": resource.title,
                    "text": resource.parsed_data,
                    "content_json": normalize_resume_content(resource.content_json),
                    "parse_status": resource.parse_status,
                    "is_master": resource.is_master,
                }
                if resource
                else {
                    "title": args["title"],
                    "text": args["text"],
                    "content_json": args["content_json"],
                    "is_master": args["is_master"]
                    if args["is_master"] is not None
                    else not library,
                }
            )
            changes = args.get("changes", {})
            after.update(changes)
            if not resource:
                # Same precedence as HTTP create: nonempty text wins, otherwise
                # use a string raw_text. Defaults must not erase supplied text.
                after["text"] = after["text"] or after["content_json"].get("raw_text", "")
                if after["text"]:
                    after["content_json"] = {**after["content_json"], "raw_text": after["text"]}
            elif "text" in changes:
                after["content_json"] = {**after["content_json"], "raw_text": after["text"]}
            elif "content_json" in changes and "raw_text" in after["content_json"]:
                after["text"] = after["content_json"]["raw_text"]
            if (
                not resource
                or "text" in changes
                or ("content_json" in changes and "raw_text" in after["content_json"])
            ):
                after["parse_status"] = (
                    "text-ready" if after["text"].strip() else "structured-ready"
                )
            if not after["is_master"] and not any(
                row["is_master"] and (resource is None or row["id"] != resource.id)
                for row in library
            ):
                raise ExternalAgentError("master_resume_required")
        if operation == "copy" and not any(row["is_master"] for row in library):
            raise ExternalAgentError("master_resume_required")
    else:
        application_id = resource.application_id if resource else args["application_id"]
        parent = (
            service._application(session, connection, application_id)
            if application_id is not None
            else None
        )
        if parent is None and not (
            connection.workspace and "notes.unbound" in json.loads(connection.capabilities_json)
        ):
            raise ExternalAgentError("unbound_notes_not_granted")
        frozen["parent"] = (
            {
                "id": parent.id,
                "company_name": parent.company_name,
                "position_name": parent.position_name,
            }
            if parent
            else None
        )
        if operation in {"update", "delete"}:
            frozen["effects"] = (
                "修改或删除后，此面试笔记已捕获的 Knowledge 将因来源指纹变化停止检索；"
                "不会自动重新沉淀知识。"
            )
        if operation == "delete":
            after = {"deleted": True, "resource_id": resource.id}
        else:
            after = (
                {key: getattr(resource, key) for key in (*NOTE_FIELDS, "application_event_id")}
                if resource
                else {key: args[key] for key in (*NOTE_FIELDS, "application_event_id")}
            )
            after.update(args.get("changes", {}))
            after["company"] = after["company"].strip() or (parent.company_name if parent else "")
            after["position"] = after["position"].strip() or (
                parent.position_name if parent else ""
            )
            if not after["company"]:
                raise ExternalAgentError("note_company_required")
            NotesRepository(service.sessions).bind(session)._validate_event_binding(
                session,
                application_id,
                after["application_event_id"],
                resource.id if resource else None,
            )
            if after["application_event_id"] is not None:
                frozen["bound_event"] = event_json(
                    service._event(session, connection, after["application_event_id"])
                )
    frozen["after"] = after
    return application_id, frozen


def recheck(
    service: ExternalAgentService,
    session: Session,
    connection: ExternalAgentConnection,
    action: str,
    args: dict[str, Any],
    frozen: dict[str, Any],
) -> None:
    _, current = prepare(service, session, connection, action, args)
    if current != {
        key: value for key, value in frozen.items() if key not in {"input", "tool", "connection"}
    }:
        raise ExternalAgentError("proposal_source_changed")


def execute(
    service: ExternalAgentService, session: Session, action: str, frozen: dict[str, Any]
) -> dict[str, Any]:
    kind = frozen["resource"]["kind"]
    resource_id = frozen["resource"]["id"]
    operation = action.split("_", 1)[0]
    after: dict[str, Any] = frozen["after"]
    item: Any = None
    if kind == "application":
        applications = ApplicationsRepository(service.sessions).bind(session)
        if operation == "delete":
            applications.delete(resource_id)
            return after
        data = ApplicationCreate(**after)
        item = (
            applications.create(data)
            if operation == "create"
            else applications.update_full(resource_id, data)
        )
    elif kind == "resume":
        resumes = ResumesRepository(service.sessions).bind(session)
        if operation == "delete":
            resumes.delete(resource_id)
            return after
        if operation == "copy":
            item = resumes.copy(resource_id, title=after["title"])
        else:
            values = {
                "title": after["title"],
                "parsed_data": after["text"],
                "content_json": after["content_json"],
                "parse_status": after["parse_status"],
                "is_master": after["is_master"],
            }
            if operation == "create":
                item = resumes.create(ResumeCreate(**values))
            else:
                changed = frozen["input"]["changes"]
                patch = {key: values[key] for key in ("title", "is_master") if key in changed}
                if "content_json" in changed or "text" in changed:
                    patch["content_json"] = values["content_json"]
                if "text" in changed or (
                    "content_json" in changed and "raw_text" in changed["content_json"]
                ):
                    patch.update(
                        {
                            key: values[key]
                            for key in ("parsed_data", "content_json", "parse_status")
                        }
                    )
                item = resumes.update(resource_id, patch)
    else:
        notes = NotesRepository(service.sessions).bind(session)
        if operation == "delete":
            notes.delete(resource_id)
            return after
        application_id = frozen["parent"]["id"] if frozen["parent"] else None
        item = (
            notes.create(NoteCreate(application_id=application_id, **after))
            if operation == "create"
            else notes.update(resource_id, NoteUpdate(**after))
        )
    if item is None:
        raise ExternalAgentError("resource_not_found")
    return {kind: project(kind, item), "resource_id": item.id}
