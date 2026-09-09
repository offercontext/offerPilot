from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from .summary import ConversationSummaryRepository, SummaryRequest, SummaryUnavailable


def register_summary_routes(app: FastAPI, sessions: sessionmaker[Session]) -> None:
    repository = ConversationSummaryRepository(sessions)

    @app.post("/api/conversations/{conversation_id}/older-summary")
    def generate_summary(conversation_id: int, command: SummaryRequest) -> JSONResponse:
        try:
            return JSONResponse(repository.generate(conversation_id, command))
        except SummaryUnavailable as exc:
            return JSONResponse({"error": "当前没有可整理的较早对话，或本日整理次数已用完", "error_code": str(exc)}, status_code=409)

    @app.delete("/api/conversations/{conversation_id}/older-summary")
    def withdraw_summary(conversation_id: int) -> dict[str, str]:
        repository.withdraw(conversation_id)
        return {"state": "withdrawn"}
