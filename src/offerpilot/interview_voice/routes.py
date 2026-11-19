from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.interview_voice.admission import CONSENT_REVISION, VoiceAdmissionError
from offerpilot.interview_voice.repository import (
    InterviewVoiceRepository, VoiceOffer, VoiceRuntimeInspect, VoiceSessionAccess, VoiceSessionCreate,
)
from offerpilot.repositories.mock_interviews import MockInterviewSourceChanged

RUNTIME_INSPECT_PATH = "/api/interview-voice/runtime/inspect"


def runtime_url() -> str | None:
    value = os.getenv("OFFERPILOT_VOICE_RUNTIME_URL", "").rstrip("/")
    if not value:
        return None
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise VoiceAdmissionError("voice_runtime_config", "语音进程必须部署在服务端本机回环地址。", 503)
    return value


async def runtime_request(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    base = runtime_url()
    if base is None:
        raise VoiceAdmissionError("voice_runtime_unavailable", "尚未启用本地实时语音进程。", 503)
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await (client.get(base + path) if payload is None
                              else client.post(base + path, json=payload))
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("invalid runtime response")
            return result
    except (httpx.HTTPError, ValueError) as exc:
        raise VoiceAdmissionError(
            "voice_runtime_unavailable", "本地语音进程不可用，请检查模型与启动状态。", 503,
        ) from exc


def _check_same_origin(request: Request) -> None:
    # Native runtimes send no browser origin. A browser cannot grant itself a
    # microphone lease on a different workspace origin.
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin is not None and origin != f"{request.url.scheme}://{request.url.netloc}"
    ):
        raise VoiceAdmissionError("voice_origin_rejected", "实时语音请求来源无效。", 403)


def register_interview_voice_routes(
    app: FastAPI, sessions: sessionmaker[Session], secret: str,
    serialize_turn: Callable[[Any, list[Any]], dict[str, Any]],
) -> None:
    repository = InterviewVoiceRepository(sessions, secret)
    router = APIRouter(dependencies=[Depends(_check_same_origin)])

    @app.exception_handler(VoiceAdmissionError)
    async def admission_error(_request: Request, exc: VoiceAdmissionError) -> JSONResponse:
        return JSONResponse({"error": str(exc), "error_code": exc.code}, status_code=exc.status)

    def snapshot(session_id: str, token: str, *, active: bool = False) -> dict[str, Any]:
        try:
            result, turns = repository.snapshot(session_id, token, active=active)
        except MockInterviewSourceChanged as exc:
            raise VoiceAdmissionError("voice_source_changed", "冻结来源已变化，请重新开始练习。") from exc
        result["turns"] = [
            {**serialize_turn(turn, turns), "turn_status": turn.turn_status,
             "turn_idempotency_key": turn.turn_idempotency_key,
             "question_idempotency_key": turn.question_idempotency_key}
            for turn in turns
        ]
        return result

    @router.get("/api/interview-voice/capabilities")
    async def capabilities() -> dict[str, Any]:
        try:
            health = await runtime_request("/health")
            ready = health.get("ready") is True and health.get("protocol") == "offerpilot-voice-v1"
            reason = "" if ready else "语音模型尚未就绪，请检查独立语音进程。"
        except VoiceAdmissionError as exc:
            ready, reason = False, str(exc)
        return {
            "available": ready, "reason": reason, "consent_revision": CONSENT_REVISION,
            "audio_processing": "offerpilot_host", "third_party_speech": False,
            "stores_audio": False,
        }

    @router.post("/api/interview-voice/sessions")
    async def create_session(payload: VoiceSessionCreate) -> dict[str, Any]:
        if not (await capabilities())["available"]:
            raise VoiceAdmissionError("voice_runtime_unavailable", "语音模型尚未就绪。", 503)
        try:
            return repository.create(payload)
        except MockInterviewSourceChanged as exc:
            raise VoiceAdmissionError("voice_source_changed", "冻结来源已变化，请重新开始练习。") from exc

    @router.post("/api/interview-voice/sessions/{session_id}/heartbeat")
    def heartbeat(session_id: str, payload: VoiceSessionAccess) -> dict[str, bool]:
        repository.heartbeat(session_id, payload.token)
        return {"active": True}

    @router.post("/api/interview-voice/sessions/{session_id}/close")
    def close(session_id: str, payload: VoiceSessionAccess) -> dict[str, bool]:
        repository.close(session_id, payload.token)
        return {"active": False}

    @router.post("/api/interview-voice/sessions/{session_id}/snapshot")
    def read_snapshot(session_id: str, payload: VoiceSessionAccess) -> dict[str, Any]:
        return snapshot(session_id, payload.token)

    @router.post("/api/interview-voice/sessions/{session_id}/offer")
    async def offer(session_id: str, payload: VoiceOffer) -> dict[str, Any]:
        snapshot(session_id, payload.token, active=True)
        return await runtime_request("/offer", {
            "session_id": session_id, "token": payload.token, "sdp": payload.sdp, "type": "offer",
        })

    @router.post(RUNTIME_INSPECT_PATH)
    def runtime_inspect(payload: VoiceRuntimeInspect) -> dict[str, Any]:
        # This one route uses the narrowly scoped capability instead of the workspace token.
        # It is read-only, checks the live lease, and never exposes unrelated workspace data.
        result = snapshot(payload.session_id, payload.token, active=True)
        turns = result.pop("turns")
        last = turns[-1] if turns else None
        result["turn"] = ({key: last[key] for key in (
            "turn_no", "question", "turn_status", "question_idempotency_key",
        )} if last else None)
        return result

    app.include_router(router)
