from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.repositories.interview_knowledge_capture import InterviewKnowledgeCaptureRepository
from .note_lifecycle import KnowledgeNoteConflict, KnowledgeNoteGone, KnowledgeNoteLifecycle, NoteMutation


def register_knowledge_note_routes(app: FastAPI, sessions: sessionmaker[Session]) -> None:
    lifecycle = KnowledgeNoteLifecycle(sessions)
    reader = InterviewKnowledgeCaptureRepository(sessions)

    @app.get("/api/knowledge/note-management")
    def list_manageable_notes() -> dict[str, object]:
        return {"items": reader.list_knowledge_notes(include_archived=True)}

    @app.post("/api/knowledge/notes/{note_id}/lifecycle")
    def mutate_note(note_id: int, command: NoteMutation) -> JSONResponse:
        try:
            return JSONResponse(lifecycle.mutate(note_id, command))
        except KnowledgeNoteGone:
            return JSONResponse({"error": "笔记或来源不可用"}, status_code=410)
        except KnowledgeNoteConflict:
            return JSONResponse({"error": "笔记已变化，请刷新后重新确认"}, status_code=409)
