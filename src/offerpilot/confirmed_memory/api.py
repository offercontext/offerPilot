from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from .repository import ConfirmedMemoryRepository, MemoryConflict, MemoryGone, MemoryMutation


def register_memory_routes(app: FastAPI, sessions: sessionmaker[Session]) -> None:
    repository = ConfirmedMemoryRepository(sessions)

    @app.get("/api/confirmed-memory")
    def list_memory() -> list[dict[str, object]]:
        return repository.list()

    @app.get("/api/confirmed-memory/{memory_id}")
    def get_memory(memory_id: str) -> JSONResponse:
        try:
            return JSONResponse(repository.get(memory_id))
        except MemoryGone:
            return JSONResponse({"error": "该偏好已删除或不存在"}, status_code=404)

    def apply(memory_id: str | None, command: MemoryMutation) -> JSONResponse:
        try:
            return JSONResponse(repository.mutate(memory_id, command))
        except MemoryGone:
            return JSONResponse({"error": "该偏好已删除", "error_code": "memory_gone"}, status_code=410)
        except MemoryConflict as exc:
            return JSONResponse({"error": "偏好已变化，请刷新后确认", "error_code": str(exc)}, status_code=409)

    @app.post("/api/confirmed-memory")
    def create_memory(command: MemoryMutation) -> JSONResponse:
        return apply(None, command)

    @app.post("/api/confirmed-memory/{memory_id}")
    def change_memory(memory_id: str, command: MemoryMutation) -> JSONResponse:
        return apply(memory_id, command)
