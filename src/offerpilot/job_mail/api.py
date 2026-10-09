"""Authenticated app routes for user-owned mail review; no AI-tool write route."""
from __future__ import annotations

import logging
import threading
from time import monotonic
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session, sessionmaker

from .model_adapter import Extractor
from .review import JobMailError, JobMailReviewService, purge_expired_evidence
from .schemas import (
    ConfirmRequest, ConnectionRequest, IgnoreRequest, ImportRequest, ReviewRequest, SettingsRequest,
)
from .sync import JobMailSyncService
from .transport import MailTransport
from .secure_setup import COOKIE, DeleteInput, JobMailSecureSetup, local_browser, protected_payload, register_secure_setup_routes

logger = logging.getLogger(__name__)


def job_mail_origin_guard_response(request: Request) -> JSONResponse | None:
    """Reject cross-site mail reads/writes, without weakening the app auth guard.

    Only use the ASGI request URL, as established by the host's trusted proxy
    configuration. Caller-supplied Forwarded/X-Forwarded-* headers are not an
    authority for private mail origins. CLI clients with no Origin still use
    the existing authenticated API path.
    """
    if not (request.url.path == "/api/job-mail" or request.url.path.startswith("/api/job-mail/")):
        return None
    setup = getattr(request.app.state, "job_mail_secure_setup", None)
    if setup is not None and setup.requires_local_access() and not local_browser(request, require_origin=False):
        return JSONResponse(
            {"error": "真实邮件功能只允许本机访问", "error_code": "local_browser_required"},
            status_code=403,
        )
    origins = request.headers.getlist("origin")
    if not origins:
        return None
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if len(origins) != 1 or origins[0] != expected:
        return JSONResponse(
            {"error": "不允许跨站访问邮件数据", "error_code": "job_mail_origin_denied"},
            status_code=403,
        )
    return None


class JobMailRuntime:
    """One lightweight scheduler; admission/fencing is owned by the durable service."""
    def __init__(self, service: JobMailSyncService):
        self.service = service
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="job-mail-scheduler", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        next_retention_check = 0.0
        while not self._stop.is_set():
            try:
                if monotonic() >= next_retention_check:
                    with self.service.sessions() as session:
                        purge_expired_evidence(session)
                        session.commit()
                    next_retention_check = monotonic() + 3600
                self.service.tick()
            except Exception:
                # Never attach source text, auth payloads or arbitrary transport errors.
                logger.warning("Mail scheduler check failed; next bounded check remains pending")
            self._stop.wait(10)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self.service.secure_setup is not None:
            self.service.secure_setup.shutdown()
        else:
            self.service.shutdown()


def register_job_mail_routes(
    app: FastAPI, sessions: sessionmaker[Session], *,
    transport: MailTransport | None = None, extractor: Extractor | None = None,
    local_setup_enabled: bool = False,
) -> JobMailRuntime:
    service = JobMailSyncService(sessions, transport=transport, extractor=extractor)
    review = JobMailReviewService(sessions)
    app.state.job_mail_service = service
    app.state.job_mail_review = review
    setup = JobMailSecureSetup(service, enabled=local_setup_enabled)
    app.state.job_mail_secure_setup = setup
    register_secure_setup_routes(app, setup)

    def run(action: Callable[[], Any]) -> JSONResponse:
        try:
            return JSONResponse(action())
        except JobMailError as exc:
            return JSONResponse({"error": str(exc), "error_code": exc.code}, status_code=exc.status)

    @app.get("/api/job-mail/status")
    def status() -> JSONResponse:
        return run(service.status)

    @app.post("/api/job-mail/connection")
    def connect(payload: ConnectionRequest) -> JSONResponse:
        return run(lambda: service.connect(payload.model_dump(mode="json")))

    @app.put("/api/job-mail/settings")
    def settings(payload: SettingsRequest) -> JSONResponse:
        return run(lambda: service.update_settings(payload.model_dump(mode="json", exclude_none=True)))

    @app.post("/api/job-mail/sync")
    def sync() -> JSONResponse:
        return run(service.start_sync)

    @app.post("/api/job-mail/sync/{run_id}/cancel")
    def cancel_sync(run_id: str) -> JSONResponse:
        return run(lambda: service.cancel_sync(run_id))

    @app.post("/api/job-mail/disconnect")
    async def disconnect(request: Request) -> JSONResponse:
        with sessions() as session:
            connection = service._connection(session)
            real = bool(connection and (connection.provider == "qq" or connection.credential_ref))
        real = real or setup.status(local=False)["deletion_pending"]
        if not real:
            return await run_in_threadpool(lambda: run(service.disconnect))
        try:
            value = await protected_payload(request)
            command = DeleteInput.model_validate(value)
            return await run_in_threadpool(lambda: run(lambda: setup.disconnect(command, request.cookies.get(COOKIE, ""))))
        except ValidationError:
            return JSONResponse({"error": "需要在安全页面明确确认删除授权码", "error_code": "secure_input_invalid"}, status_code=422)
        except JobMailError as exc:
            return JSONResponse({"error": str(exc), "error_code": exc.code}, status_code=exc.status)

    @app.post("/api/job-mail/imports")
    def import_mail(payload: ImportRequest) -> JSONResponse:
        return run(lambda: service.import_text(payload.model_dump(mode="json")))

    @app.get("/api/job-mail/suggestions")
    def suggestions(status: str = "", limit: int = Query(default=100, ge=1, le=200),
                    offset: int = Query(default=0, ge=0, le=100000)) -> JSONResponse:
        return run(lambda: review.list(status, limit, offset))

    @app.get("/api/job-mail/suggestions/{suggestion_id}")
    def suggestion(suggestion_id: str) -> JSONResponse:
        return run(lambda: review.get(suggestion_id))

    @app.post("/api/job-mail/suggestions/{suggestion_id}/preview")
    def preview(suggestion_id: str, payload: ReviewRequest) -> JSONResponse:
        return run(lambda: review.preview(suggestion_id, payload))

    @app.post("/api/job-mail/suggestions/{suggestion_id}/confirm")
    def confirm(suggestion_id: str, payload: ConfirmRequest) -> JSONResponse:
        return run(lambda: review.confirm(suggestion_id, payload))

    @app.post("/api/job-mail/suggestions/{suggestion_id}/ignore")
    def ignore(suggestion_id: str, payload: IgnoreRequest) -> JSONResponse:
        return run(lambda: review.ignore(suggestion_id, payload.suggestion_version))

    @app.get("/api/job-mail/receipts/{operation_id}")
    def receipt(operation_id: str) -> JSONResponse:
        return run(lambda: review.receipt(operation_id))

    return JobMailRuntime(service)
