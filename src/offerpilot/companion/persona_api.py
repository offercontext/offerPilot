from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from .persona_contracts import PersonaMutation
from .persona_repository import PersonaConflict, PersonaRepository


def register_persona_routes(app: FastAPI, sessions: sessionmaker[Session]) -> PersonaRepository:
    repository = PersonaRepository(sessions)
    app.state.persona_repository = repository

    @app.get("/api/companion/persona")
    def read_persona() -> dict[str, Any]:
        return repository.read()

    @app.get("/api/companion/persona/revisions")
    def read_persona_revisions() -> dict[str, Any]:
        return repository.revisions()

    @app.post("/api/companion/persona/mutations")
    def mutate_persona(command: PersonaMutation) -> JSONResponse:
        try:
            return JSONResponse(repository.mutate(command))
        except PersonaConflict as exc:
            return JSONResponse({"error": "伙伴人设已变化，请刷新后重试", "error_code": str(exc)},
                                status_code=409)

    return repository
