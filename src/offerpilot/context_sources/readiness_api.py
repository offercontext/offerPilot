"""HTTP routes for explicit conversation Readiness bindings."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from .readiness import (
    ReadinessContextClearRequest,
    ReadinessContextConflict,
    ReadinessContextRepository,
    ReadinessContextRequest,
    ReadinessContextUnavailable,
)


_PATHS = (
    "/api/chat/conversations/{conversation_id}/readiness-context",
    "/api/conversations/{conversation_id}/readiness-context",
)


def _error_response(exc: ValueError) -> JSONResponse:
    code = str(exc) or "readiness_context_unavailable"
    if isinstance(exc, ReadinessContextConflict):
        status = 409
        message = "准备重点已变化，请刷新后重试"
    elif isinstance(exc, ReadinessContextUnavailable):
        status = 404 if code in {
            "readiness_conversation_unavailable",
            "readiness_application_unavailable",
            "readiness_requires_application_conversation",
        } else 422
        message = "当前对话或准备重点不可用"
    else:
        status = 422
        message = "准备重点请求无效"
    return JSONResponse(
        {"error": message, "error_code": code, "retryable": status == 409},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def register_readiness_context_routes(
    app: FastAPI,
    sessions: sessionmaker[Session],
) -> None:
    """Register read/bind/clear routes against the authenticated workspace DB."""

    repository = ReadinessContextRepository(sessions)

    def read_context(conversation_id: int) -> JSONResponse:
        try:
            return JSONResponse(
                repository.read(conversation_id),
                headers={"Cache-Control": "no-store"},
            )
        except (ReadinessContextConflict, ReadinessContextUnavailable) as exc:
            return _error_response(exc)

    def confirm_context(
        conversation_id: int,
        command: ReadinessContextRequest,
    ) -> JSONResponse:
        try:
            return JSONResponse(
                repository.confirm(conversation_id, command),
                headers={"Cache-Control": "no-store"},
            )
        except (ReadinessContextConflict, ReadinessContextUnavailable) as exc:
            return _error_response(exc)

    def clear_context(
        conversation_id: int,
        command: ReadinessContextClearRequest,
    ) -> JSONResponse:
        try:
            return JSONResponse(
                repository.clear(conversation_id, command),
                headers={"Cache-Control": "no-store"},
            )
        except (ReadinessContextConflict, ReadinessContextUnavailable) as exc:
            return _error_response(exc)

    for path in _PATHS:
        app.add_api_route(path, read_context, methods=["GET"], name="readiness_context_read")
        app.add_api_route(path, confirm_context, methods=["PUT", "POST"], name="readiness_context_confirm")
        app.add_api_route(
            path,
            clear_context,
            methods=["DELETE"],
            name="readiness_context_clear",
        )
        app.add_api_route(
            f"{path}/clear",
            clear_context,
            methods=["POST"],
            name="readiness_context_clear_action",
        )


__all__ = ["register_readiness_context_routes"]
