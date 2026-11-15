from __future__ import annotations

from typing import Any

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.training_evidence.contracts import parse_command
from offerpilot.training_evidence.repository import TrainingEvidenceError, TrainingEvidenceRepository


def register_training_evidence_routes(app: FastAPI, session_factory: sessionmaker[Session]) -> None:
    repository = TrainingEvidenceRepository(session_factory)

    @app.get("/api/interview-practice/plans/{plan_id}/training-evidence")
    def get_training_evidence(plan_id: int) -> JSONResponse:
        try:
            return JSONResponse(repository.get(plan_id))
        except TrainingEvidenceError as exc:
            return JSONResponse({"error_code": exc.code}, status_code=exc.status)

    @app.post("/api/interview-practice/plans/{plan_id}/training-evidence")
    def mutate_training_evidence(plan_id: int, payload: Any = Body(None)) -> JSONResponse:
        try:
            command = parse_command(payload)
        except (ValueError, UnicodeEncodeError):
            return JSONResponse({"error_code": "training_evidence_invalid"}, status_code=422)
        try:
            return JSONResponse(repository.mutate(plan_id, command))
        except TrainingEvidenceError as exc:
            return JSONResponse({"error_code": exc.code}, status_code=exc.status)
