"""Mandatory mail review with durable, exact-input previews and atomic receipts.

This is an authenticated, explicit user-action service, never an Agent tool. It
has no provider/network dependency and never consults chat_auto_approve_writes.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import Application, ApplicationCreationWorkspace, ApplicationEvent
from offerpilot.repositories.application_events import ApplicationEventCreate, ApplicationEventsRepository
from .models import (
    JobMailConnection, JobMailEvidence, JobMailPreview, JobMailReceipt, JobMailSuggestion,
)
from .schemas import ConfirmRequest, EventEdits, ReviewRequest

EVENT_FIELDS = frozenset(EventEdits.model_fields)
SUPPORTED_ACTIONS = {"create_event", "update_event"}


class JobMailError(Exception):
    def __init__(self, code: str, message: str | None = None, status: int = 409):
        super().__init__(message or code)
        self.code = code
        self.status = status


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_default(value: object) -> str | None:
    if isinstance(value, datetime):
        return timestamp(value)
    raise ValueError("non-JSON mail value")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=_json_default)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def workspace_id(session: Session) -> str:
    row = session.get(ApplicationCreationWorkspace, 1)
    if row is None:
        raise JobMailError("workspace_unavailable", "当前工作区不可用", 503)
    return row.scope_id


def begin_immediate(session: Session) -> None:
    """Take SQLite's writer lock *before* observing business state."""
    session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _application_snapshot(application: Application) -> str:
    return canonical({column.key: timestamp(value) if isinstance(value, datetime) else value
                      for column in Application.__table__.columns
                      for value in [getattr(application, column.key)]})


def event_json(event: ApplicationEvent) -> dict[str, Any]:
    return {
        "id": event.id, "application_id": event.application_id,
        "event_type": event.event_type, "subtype": event.subtype, "tags": event.tags,
        "round": event.round, "scheduled_at": timestamp(event.scheduled_at),
        "duration_minutes": event.duration_minutes, "location": event.location,
        "notes": event.notes, "remind_at": timestamp(event.remind_at),
        "status": event.status, "created_at": timestamp(event.created_at),
    }


def _request_payload(suggestion_id: str, request: ReviewRequest) -> dict[str, Any]:
    # exclude_unset on edits distinguishes preserving a reminder from clearing it.
    return {
        "suggestion_id": suggestion_id, "operation_id": request.operation_id,
        "suggestion_version": request.suggestion_version,
        "application_id": request.application_id, "target_event_id": request.target_event_id,
        "edited_fields": request.edited_fields.model_dump(mode="json", exclude_unset=True),
    }


def _confirmation_fingerprint(suggestion_id: str, request: ConfirmRequest) -> str:
    return fingerprint({**_request_payload(suggestion_id, request),
                        "preview_token": request.preview_token, "explicit_confirmation": True})


def _get_suggestion(session: Session, suggestion_id: str) -> JobMailSuggestion:
    row = session.scalar(select(JobMailSuggestion).where(
        JobMailSuggestion.id == suggestion_id, JobMailSuggestion.scope_id == workspace_id(session),
    ))
    if row is None:
        raise JobMailError("suggestion_not_found", "邮件建议不存在", 404)
    return row


def _require_source(session: Session, suggestion: JobMailSuggestion) -> None:
    if suggestion.connection_id is not None:
        connection = session.get(JobMailConnection, suggestion.connection_id)
        if (connection is None or connection.scope_id != suggestion.scope_id
                or connection.status != "connected"
                or connection.scope_version != suggestion.scope_version):
            raise JobMailError("mail_scope_changed", "邮箱已断开或读取范围已变化，请重新核对")
    evidence = session.get(JobMailEvidence, suggestion.evidence_id)
    if (evidence is None or evidence.scope_id != suggestion.scope_id
            or evidence.cleared_at is not None or evidence.snippet is None):
        raise JobMailError("mail_evidence_unavailable", "原文已清理，不能继续使用旧确认")


def _editable(session: Session, suggestion: JobMailSuggestion, version: int) -> None:
    if suggestion.version != version:
        raise JobMailError("suggestion_changed", "建议版本已变化，请重新审阅")
    if suggestion.status != "pending" or suggestion.superseded_by:
        raise JobMailError("suggestion_not_pending", "此建议已处理或需要人工处理")
    if suggestion.action not in SUPPORTED_ACTIONS or suggestion.time_mode != "fixed":
        raise JobMailError("mail_manual_required", "本批次仅支持完整时间的事件新增或简单更新", 422)
    _require_source(session, suggestion)


def _resolve(
    session: Session, suggestion: JobMailSuggestion, request: ReviewRequest,
) -> tuple[Application, ApplicationEvent | None, dict[str, Any], dict[str, Any], list[str]]:
    application = session.scalar(select(Application).where(
        Application.id == request.application_id, Application.deleted_at.is_(None),
    ))
    if application is None:
        raise JobMailError("application_unavailable", "所选投递已删除或不存在", 404)
    target: ApplicationEvent | None = None
    if suggestion.action == "update_event":
        if request.target_event_id is None:
            raise JobMailError("target_event_required", "请明确选择要更新的事件", 422)
        target = session.scalar(select(ApplicationEvent).where(
            ApplicationEvent.id == request.target_event_id,
            ApplicationEvent.application_id == application.id,
        ))
        if target is None:
            raise JobMailError("target_scope_mismatch", "目标事件不属于所选投递或已删除", 409)
        if target.status != "todo":
            raise JobMailError("target_not_editable", "已完成或已取消事件请前往日历人工处理", 422)
        if suggestion.target_event_id is not None and suggestion.target_event_id != target.id:
            raise JobMailError("target_scope_mismatch", "所选事件与该建议的目标不一致", 409)
    elif request.target_event_id is not None:
        raise JobMailError("unexpected_target", "新增事件不能携带已有事件目标", 422)

    before = event_json(target) if target is not None else {}
    base: dict[str, Any] = {key: value for key, value in before.items() if key in EVENT_FIELDS}
    if target is None:
        base = {"subtype": "", "tags": [], "round": 0,
                "location": "", "notes": "", "remind_at": None}
    proposed = json.loads(suggestion.proposed_fields_json)
    if not isinstance(proposed, dict) or set(proposed) - EVENT_FIELDS:
        raise JobMailError("invalid_proposal", "建议字段无效，请人工核对", 422)
    # Proposed null/empty placeholders never erase existing user information.
    base.update({key: value for key, value in proposed.items()
                 if value is not None and value != ""})
    edits = request.edited_fields.model_dump(mode="json", exclude_unset=True)
    if any(value is None and key != "remind_at" for key, value in edits.items()):
        raise JobMailError("invalid_event_fields", "只有提醒时间允许显式清空", 422)
    base.update(edits)
    try:
        validated = EventEdits.model_validate(base).model_dump(mode="json", exclude_unset=True)
    except ValidationError as exc:
        raise JobMailError("invalid_event_fields", "请填写有效的事件字段和带时区的完整时间", 422) from exc
    if (validated.get("scheduled_at") is None or validated.get("duration_minutes") is None
            or validated.get("event_type") not in {"interview", "written_test", "custom"}):
        raise JobMailError("incomplete_event", "必须补齐具体开始时间和正数时长", 422)
    if validated.get("subtype") == "assessment" and validated["event_type"] != "written_test":
        raise JobMailError("invalid_event_type", "测评必须是笔试类型下的 assessment 子类型", 422)
    warnings: list[str] = ["本次仅写入事件，不改变投递阶段，不发送邮件。"]
    after = {**validated, "application_id": application.id, "status": "todo"}
    if target is not None:
        after["id"] = target.id
        after["created_at"] = before["created_at"]
        if (before["scheduled_at"] != after["scheduled_at"] and before["remind_at"] is not None
                and "remind_at" not in edits and "remind_at" not in proposed):
            after["remind_at"] = None
            warnings.append("开始时间变化后，旧提醒将清除；请按新安排另设提醒。")
    if suggestion.action == "update_event" and all(before.get(key) == after.get(key) for key in EVENT_FIELDS):
        raise JobMailError("no_event_changes", "没有需要应用的事件变更", 422)
    return application, target, before, after, warnings


def _event_create(after: dict[str, Any]) -> ApplicationEventCreate:
    reminder = after.get("remind_at")
    return ApplicationEventCreate(
        application_id=after["application_id"], event_type=after["event_type"],
        subtype=after.get("subtype", ""), tags=after.get("tags", []),
        scheduled_at=datetime.fromisoformat(after["scheduled_at"]),
        duration_minutes=after["duration_minutes"], round=after.get("round", 0),
        location=after.get("location", ""), notes=after.get("notes", ""),
        remind_at=datetime.fromisoformat(reminder) if reminder is not None else None,
        status=after["status"],
    )


class JobMailReviewService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions
        self.events = ApplicationEventsRepository(sessions)

    def list(self, status: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
        with self.sessions() as session:
            query = select(JobMailSuggestion).where(JobMailSuggestion.scope_id == workspace_id(session))
            if status == "unprocessed":
                query = query.where(JobMailSuggestion.status.in_(["pending", "manual_required"]))
            elif status == "processed":
                query = query.where(JobMailSuggestion.status.in_(["applied", "ignored"]))
            elif status:
                if status not in {"pending", "manual_required", "ignored", "applied", "superseded"}:
                    raise JobMailError("invalid_status", "未知建议状态", 422)
                query = query.where(JobMailSuggestion.status == status)
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            pending_count = session.scalar(select(func.count()).select_from(JobMailSuggestion).where(
                JobMailSuggestion.scope_id == workspace_id(session),
                JobMailSuggestion.status.in_(["pending", "manual_required"]),
            )) or 0
            rows = session.scalars(query.order_by(JobMailSuggestion.created_at.desc(), JobMailSuggestion.id).offset(offset).limit(limit))
            items = [_suggestion_json(session, row) for row in rows]
            return {"items": items, "total": total, "pending_count": pending_count,
                    "has_more": offset + len(items) < total}

    def get(self, suggestion_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            return _suggestion_json(session, _get_suggestion(session, suggestion_id))

    def preview(self, suggestion_id: str, request: ReviewRequest) -> dict[str, Any]:
        with self.sessions() as session:
            begin_immediate(session)
            suggestion = _get_suggestion(session, suggestion_id)
            _editable(session, suggestion, request.suggestion_version)
            if session.scalar(select(JobMailReceipt).where(
                JobMailReceipt.scope_id == suggestion.scope_id,
                JobMailReceipt.operation_id == request.operation_id,
            )) is not None:
                raise JobMailError("operation_already_used", "该操作已完成，请先查询回执")
            application, target, before, after, warnings = _resolve(session, suggestion, request)
            expires_at = utcnow() + timedelta(minutes=15)
            token = str(uuid4())
            changes = [{"field": key, "before": before.get(key), "after": after.get(key)}
                       for key in sorted(EVENT_FIELDS) if before.get(key) != after.get(key)]
            result = {
                **_request_payload(suggestion_id, request), "preview_token": token,
                "action": suggestion.action, "scope_version": suggestion.scope_version,
                "application_snapshot": {"id": application.id, "company_name": application.company_name,
                                         "position_name": application.position_name, "status": application.status,
                                         "updated_at": timestamp(application.updated_at)},
                "before": before, "after": after, "changes": changes,
                "expires_at": timestamp(expires_at), "warnings": warnings,
            }
            session.add(JobMailPreview(
                token=token, scope_id=suggestion.scope_id, operation_id=request.operation_id,
                suggestion_id=suggestion.id, suggestion_version=suggestion.version,
                request_fingerprint=fingerprint(_request_payload(suggestion_id, request)),
                scope_version=suggestion.scope_version,
                application_snapshot=_application_snapshot(application),
                target_snapshot=canonical(event_json(target) if target else None),
                payload_json=canonical(result), expires_at=expires_at,
            ))
            session.commit()
            return result

    def confirm(self, suggestion_id: str, request: ConfirmRequest) -> dict[str, Any]:
        # Runtime check as well as schema validation: internal callers cannot turn
        # a raw model/provider assertion into an approval.
        if request.explicit_confirmation is not True:
            raise JobMailError("confirmation_required", "必须明确确认", 422)
        expected = _confirmation_fingerprint(suggestion_id, request)
        with self.sessions() as session:
            begin_immediate(session)
            scope = workspace_id(session)
            receipt = session.scalar(select(JobMailReceipt).where(
                JobMailReceipt.scope_id == scope, JobMailReceipt.operation_id == request.operation_id,
            ))
            if receipt is not None:
                if receipt.request_fingerprint != expected or receipt.suggestion_id != suggestion_id:
                    raise JobMailError("operation_conflict", "操作ID已用于另一组确认内容")
                result: dict[str, Any] = json.loads(receipt.result_json)
                return {**result, "replayed": True}
            preview = session.get(JobMailPreview, request.preview_token)
            if preview is None or preview.scope_id != scope:
                raise JobMailError("preview_required", "请先预览并明确确认这次变更", 422)
            if (preview.suggestion_id != suggestion_id or preview.operation_id != request.operation_id
                    or preview.request_fingerprint != fingerprint(_request_payload(suggestion_id, request))):
                raise JobMailError("preview_mismatch", "确认字段已变化，请重新预览")
            if preview.expires_at <= utcnow():
                raise JobMailError("preview_expired", "确认预览已过期，请重新审阅")
            suggestion = _get_suggestion(session, suggestion_id)
            _editable(session, suggestion, request.suggestion_version)
            if (suggestion.version != preview.suggestion_version
                    or suggestion.scope_version != preview.scope_version):
                raise JobMailError("suggestion_changed", "建议或邮箱范围已变化，请重新审阅")
            application, target, before, after, _warnings = _resolve(session, suggestion, request)
            if (_application_snapshot(application) != preview.application_snapshot
                    or canonical(event_json(target) if target else None) != preview.target_snapshot):
                raise JobMailError("target_changed", "投递或事件已被修改，本次零写入，请重新预览")
            approved = json.loads(preview.payload_json)
            if approved["after"] != after:
                raise JobMailError("suggestion_changed", "建议内容已变化，请重新预览")
            repository = self.events.bind(session)
            event_data = _event_create(after)
            if target is None:
                event = repository.create(event_data)
            else:
                updated = repository.update(target.id, event_data)
                if updated is None:
                    raise JobMailError("target_changed", "事件已变化，本次零写入")
                event = updated
            result = {
                "operation_id": request.operation_id, "suggestion_id": suggestion.id,
                "suggestion_version": suggestion.version,
                "application_event_id": event.id, "application_id": application.id,
                "action": suggestion.action, "before": before, "after": event_json(event),
                "confirmed_fields": {item["field"]: item["after"] for item in approved["changes"]},
                "confirmed_at": timestamp(utcnow()), "replayed": False,
            }
            session.add(JobMailReceipt(
                id=str(uuid4()), scope_id=scope, operation_id=request.operation_id,
                suggestion_id=suggestion.id, request_fingerprint=expected,
                application_event_id=event.id, result_json=canonical(result),
            ))
            suggestion.status = "applied"
            suggestion.version += 1
            suggestion.processed_at = utcnow()
            # One commit owns the event, suggestion transition, and durable receipt.
            session.commit()
            return result

    def ignore(self, suggestion_id: str, version: int) -> dict[str, Any]:
        with self.sessions() as session:
            begin_immediate(session)
            suggestion = _get_suggestion(session, suggestion_id)
            if suggestion.version != version or suggestion.status not in {"pending", "manual_required"}:
                raise JobMailError("suggestion_changed", "建议已处理，请刷新后再操作")
            suggestion.status = "ignored"
            suggestion.version += 1
            suggestion.processed_at = utcnow()
            session.flush()
            result = _suggestion_json(session, suggestion)
            session.commit()
            return result

    def receipt(self, operation_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            row = session.scalar(select(JobMailReceipt).where(
                JobMailReceipt.scope_id == workspace_id(session), JobMailReceipt.operation_id == operation_id,
            ))
            if row is None:
                raise JobMailError("receipt_not_found", "未查到已提交回执；请勿假定写入成功", 404)
            result: dict[str, Any] = json.loads(row.result_json)
            return result


def _suggestion_json(session: Session, suggestion: JobMailSuggestion) -> dict[str, Any]:
    evidence = session.get(JobMailEvidence, suggestion.evidence_id)
    receipt = session.scalar(select(JobMailReceipt).where(JobMailReceipt.suggestion_id == suggestion.id))
    evidence_payload = None if evidence is None else {
        "id": evidence.id, "subject": evidence.subject, "sender": evidence.sender,
        "received_at": timestamp(evidence.received_at), "snippet": evidence.snippet,
        "body_fingerprint": evidence.body_fingerprint, "truncated": evidence.truncated,
        "cleared_at": timestamp(evidence.cleared_at),
    }
    # Evidence retention also removes the duplicate quote copies from the API.
    field_evidence = json.loads(suggestion.field_evidence_json) if evidence and evidence.snippet is not None else {}
    return {
        "id": suggestion.id, "version": suggestion.version, "status": suggestion.status,
        "action": suggestion.action, "reason": suggestion.reason, "time_mode": suggestion.time_mode,
        "proposed_fields": json.loads(suggestion.proposed_fields_json),
        "field_evidence": field_evidence,
        "application_candidates": json.loads(suggestion.application_candidates_json),
        "target_event_id": suggestion.target_event_id, "scope_version": suggestion.scope_version,
        "evidence": evidence_payload, "receipt": json.loads(receipt.result_json) if receipt else None,
        "created_at": timestamp(suggestion.created_at), "processed_at": timestamp(suggestion.processed_at),
    }


def ingest_candidate(
    session: Session, parsed: dict[str, Any], candidates: list[dict[str, Any]],
    connection_id: str | None = None, scope_version: int = 1,
) -> dict[str, Any]:
    """Persist bounded, verified source data under the caller's fenced transaction."""
    scope = workspace_id(session)
    if connection_id is not None:
        connection = session.get(JobMailConnection, connection_id)
        if (connection is None or connection.scope_id != scope or connection.status != "connected"
                or connection.scope_version != scope_version):
            raise JobMailError("mail_scope_changed", "邮箱范围已撤回，丢弃迟到识别结果")
    body = str(parsed.get("body_text", ""))
    body_hash = str(parsed.get("fingerprint") or hashlib.sha256(body.encode()).hexdigest())
    received_at = parsed.get("received_at")
    if isinstance(received_at, str):
        received_at = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
    if not isinstance(received_at, datetime):
        raise JobMailError("invalid_mail_date", "邮件接收时间无效", 422)
    if received_at.tzinfo is not None:
        received_at = received_at.astimezone(timezone.utc).replace(tzinfo=None)
    message_id = str(parsed.get("message_id", ""))
    identity = fingerprint({"connection": connection_id, "message_id": message_id,
                            "sender": parsed.get("sender", ""), "received_at": timestamp(received_at),
                            "body": body_hash if not message_id else None})
    evidence = session.scalar(select(JobMailEvidence).where(
        JobMailEvidence.scope_id == scope, JobMailEvidence.source_identity == identity,
        JobMailEvidence.body_fingerprint == body_hash,
    ))
    location = {key: parsed[key] for key in ("folder", "uidvalidity", "uid") if key in parsed}
    if evidence is not None:
        locations = json.loads(evidence.locations_json)
        if location and location not in locations:
            evidence.locations_json = canonical([*locations, location])
        rows = list(session.scalars(select(JobMailSuggestion).where(JobMailSuggestion.evidence_id == evidence.id)))
        for row in rows:
            # This path runs only after the caller actually re-read this exact
            # source under the current connection fence, never merely on reconnect.
            if (connection_id is not None and row.connection_id == connection_id
                    and row.status in {"pending", "manual_required"} and not row.superseded_by
                    and row.scope_version != scope_version and evidence.snippet is not None):
                row.scope_version = scope_version
                row.version += 1
        session.flush()
        return {"items": [_suggestion_json(session, row) for row in rows],
                "deduplicated": True, "evidence_id": evidence.id}
    snippet = body[:12000]
    evidence = JobMailEvidence(
        id=str(uuid4()), scope_id=scope, connection_id=connection_id, source_identity=identity,
        message_id=message_id[:1000], subject=str(parsed.get("subject", ""))[:1000],
        sender=str(parsed.get("sender", ""))[:320], received_at=received_at,
        snippet=snippet, body_fingerprint=body_hash, locations_json=canonical([location] if location else []),
        truncated=bool(parsed.get("truncated")) or len(body) > 12000,
    )
    session.add(evidence)
    session.flush()
    results: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates[:20]):
        # Never accept a provider tool call, arbitrary action, or external execution directive.
        if any(key in candidate for key in ("tool_calls", "function_call", "commands", "approved")):
            raise JobMailError("invalid_candidate", "识别输出含不允许的指令", 422)
        action = str(candidate.get("action", "manual_only"))
        mode = str(candidate.get("time_mode", "unknown"))
        manual = action not in SUPPORTED_ACTIONS or mode != "fixed"
        proposed = candidate.get("proposed_fields", {key: candidate[key] for key in EVENT_FIELDS if key in candidate})
        if not isinstance(proposed, dict) or set(proposed) - EVENT_FIELDS:
            raise JobMailError("invalid_candidate", "识别输出包含不允许的业务字段", 422)
        if proposed.get("event_type") not in {None, "interview", "written_test", "custom"}:
            manual = True
        proposed = {key: timestamp(value) if isinstance(value, datetime) else value for key, value in proposed.items() if value is not None}
        field_evidence = candidate.get("field_evidence", candidate.get("evidence", {}))
        if not isinstance(field_evidence, dict):
            raise JobMailError("invalid_evidence", "识别证据格式无效", 422)
        for item in field_evidence.values():
            quote = item if isinstance(item, str) else item.get("quote") if isinstance(item, dict) else None
            if not isinstance(quote, str) or not quote or quote not in body:
                raise JobMailError("invalid_evidence", "识别证据无法在原文定位", 422)
            if isinstance(item, dict) and ("start" in item or "end" in item):
                start, end = item.get("start"), item.get("end")
                if type(start) is not int or type(end) is not int or start < 0 or end <= start or body[start:end] != quote:
                    raise JobMailError("invalid_evidence", "识别证据位置不匹配", 422)
            if quote not in snippet:
                manual = True
        company, position = candidate.get("company", ""), candidate.get("position", "")
        applications = []
        if company:
            query = select(Application).where(Application.deleted_at.is_(None), Application.company_name == company)
            if position:
                query = query.where(Application.position_name == position)
            applications = [{"id": app.id, "company_name": app.company_name, "position_name": app.position_name}
                            for app in session.scalars(query.limit(20))]
        suggestion = JobMailSuggestion(
            id=str(uuid4()), scope_id=scope, evidence_id=evidence.id, connection_id=connection_id,
            scope_version=scope_version, candidate_key=fingerprint({"index": index, "candidate": candidate}),
            version=1, status="manual_required" if manual else "pending", action=action if not manual else "manual_only",
            reason=str(candidate.get("reason", ""))[:2000], time_mode=mode,
            proposed_fields_json=canonical(proposed), field_evidence_json=canonical(field_evidence),
            application_candidates_json=canonical(applications), target_event_id=None,
        )
        session.add(suggestion)
        session.flush()
        results.append(_suggestion_json(session, suggestion))
    return {"items": results, "deduplicated": False, "evidence_id": evidence.id}


def purge_expired_evidence(session: Session, now: datetime | None = None) -> int:
    """Approved retention: 90 days pending / 30 days processed; preserve receipts."""
    now = now or utcnow()
    cleared = 0
    session.execute(delete(JobMailPreview).where(JobMailPreview.expires_at < now - timedelta(days=1)))
    for evidence in session.scalars(select(JobMailEvidence).where(
            JobMailEvidence.cleared_at.is_(None), JobMailEvidence.created_at <= now - timedelta(days=30))):
        suggestions = list(session.scalars(select(JobMailSuggestion).where(JobMailSuggestion.evidence_id == evidence.id)))
        pending = any(row.status in {"pending", "manual_required"} for row in suggestions)
        anchor = evidence.created_at if pending else max([row.processed_at or row.created_at for row in suggestions] or [evidence.created_at])
        if anchor + timedelta(days=90 if pending else 30) <= now:
            evidence.snippet = None
            evidence.cleared_at = now
            for row in suggestions:
                row.field_evidence_json = "{}"
                row.reason = "原文片段已按保留策略清理；已确认业务记录与回执保留。"
            cleared += 1
    return cleared


def rebind_retained_suggestions(
    session: Session, connection: JobMailConnection, old_scope: int,
) -> int:
    """Carry retained source consent across one explicit settings scope edit.

    Only the immediately preceding authorized scope can carry forward. A source
    excluded earlier, or a disconnected/replaced account, needs an actual new
    read before its proposal can be reviewed again. Every carry invalidates old
    previews even though no business data is written.
    """
    if (connection.scope_id != workspace_id(session) or connection.status != "connected"
            or connection.scope_version != old_scope + 1):
        raise JobMailError("mail_scope_changed", "不能恢复不相邻或已断开的邮箱范围")
    retained_folders = set(json.loads(connection.folders_json))
    rows = session.execute(select(JobMailSuggestion, JobMailEvidence).join(
        JobMailEvidence, JobMailEvidence.id == JobMailSuggestion.evidence_id,
    ).where(
        JobMailSuggestion.connection_id == connection.id,
        JobMailSuggestion.scope_id == connection.scope_id,
        JobMailSuggestion.scope_version == old_scope,
        JobMailSuggestion.status.in_(["pending", "manual_required"]),
        JobMailSuggestion.superseded_by.is_(None),
        JobMailEvidence.connection_id == connection.id,
        JobMailEvidence.scope_id == connection.scope_id,
        JobMailEvidence.snippet.is_not(None), JobMailEvidence.cleared_at.is_(None),
    ))
    rebound = 0
    for suggestion, evidence in rows:
        locations = json.loads(evidence.locations_json)
        if any(isinstance(location, dict) and location.get("folder") in retained_folders
               for location in locations):
            suggestion.scope_version = connection.scope_version
            suggestion.version += 1
            rebound += 1
    return rebound
