from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from .contracts import CompanionEventCommand, CompanionPresenceCommand, CompanionResetCommand, CompanionSettingsCommand
from .repository import CompanionConflict, CompanionRepository
from .persona_api import register_persona_routes

logger = logging.getLogger(__name__)


def register_companion_routes(app: FastAPI, sessions: sessionmaker[Session],
                              on_change: Callable[[], None] | None = None) -> CompanionRepository:
    register_persona_routes(app, sessions)
    repository = CompanionRepository(sessions)
    app.state.companion_repository = repository

    def run(operation: Callable[[], dict[str, Any]], *, reconcile: bool = True) -> JSONResponse:
        try:
            result = operation()
        except CompanionConflict as exc:
            return JSONResponse({"error": "陪伴状态已变化，请刷新后重试", "error_code": str(exc)}, status_code=409)
        if reconcile and on_change is not None:
            try:
                on_change()
            except Exception:
                # State/receipt already committed. Retrying queue scheduling must
                # never execute the life command a second time.
                logger.exception("Companion queue reconciliation deferred")
        return JSONResponse(result)

    @app.get("/api/companion")
    def read_companion() -> dict[str, Any]:
        return repository.read()

    @app.post("/api/companion/events")
    def companion_event(command: CompanionEventCommand) -> JSONResponse:
        return run(lambda: repository.apply_event(command))

    @app.post("/api/companion/presence")
    def companion_presence(command: CompanionPresenceCommand) -> JSONResponse:
        return run(lambda: repository.presence(command), reconcile=False)

    @app.put("/api/companion/settings")
    def companion_settings(command: CompanionSettingsCommand) -> JSONResponse:
        return run(lambda: repository.update_settings(command))

    @app.delete("/api/companion")
    def reset_companion(command: CompanionResetCommand) -> JSONResponse:
        return run(lambda: repository.reset(command))

    return repository
