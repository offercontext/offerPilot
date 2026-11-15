"""Backup control routes behind the application's authentication middleware."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from offerpilot import backup_restore
from offerpilot.backup_archive import MAX_COMPRESSED


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    return (request.headers.get("sec-fetch-site") != "cross-site"
            and (not origin or origin == str(request.base_url).rstrip("/")))


async def _payload(request: Request) -> dict[str, str]:
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 4096:
            raise ValueError("restore_request_too_large")
        data.extend(chunk)
    payload = json.loads(data)
    if not isinstance(payload, dict) or not all(isinstance(value, str) for value in payload.values()):
        raise ValueError("restore_request_invalid")
    return payload


def register_backup_routes(app: FastAPI, root: Path) -> None:
    @app.get("/api/backups/restore")
    def restore_status() -> Any:
        try:
            return backup_restore.status(root)
        except (OSError, ValueError):
            return JSONResponse({"error": "restore_busy_or_invalid"}, status_code=409)

    @app.post("/api/backups/restore/preview")
    async def restore_preview(request: Request) -> Any:
        if not _same_origin(request):
            return JSONResponse({"error": "restore_origin_rejected"}, status_code=403)
        try:
            with tempfile.TemporaryDirectory(prefix="offerpilot-restore-") as directory:
                archive = Path(directory) / "upload.zip"
                total = 0
                with archive.open("xb") as stream:
                    archive.chmod(0o600)
                    async for chunk in request.stream():
                        total += len(chunk)
                        if total > MAX_COMPRESSED:
                            return JSONResponse({"error": "archive_size_limit"}, status_code=413)
                        stream.write(chunk)
                return await run_in_threadpool(backup_restore.prepare, root, archive)
        except ValueError as error:
            code = str(error)
            public = code if code in {
                "archive_schema_mismatch", "archive_size_limit", "archive_manifest_limit",
                "archive_manifest_mismatch", "archive_database_sidecars", "archive_attachment_missing",
                "archive_attachment_mismatch", "restore_already_pending",
            } else "archive_invalid"
            return JSONResponse({"error": public}, status_code=409)
        except Exception:
            return JSONResponse({"error": "archive_invalid"}, status_code=400)

    @app.post("/api/backups/restore/confirm")
    async def restore_confirm(request: Request) -> Any:
        if not _same_origin(request):
            return JSONResponse({"error": "restore_origin_rejected"}, status_code=403)
        try:
            payload = await _payload(request)
            return await run_in_threadpool(backup_restore.confirm, root, payload["id"],
                                          payload["sha256"], payload["confirmation"])
        except (ValueError, KeyError, TypeError, OSError):
            return JSONResponse({"error": "restore_confirmation_rejected"}, status_code=409)

    @app.post("/api/backups/restore/cancel")
    async def restore_cancel(request: Request) -> Any:
        if not _same_origin(request):
            return JSONResponse({"error": "restore_origin_rejected"}, status_code=403)
        try:
            payload = await _payload(request)
            return await run_in_threadpool(backup_restore.cancel, root, payload["id"])
        except (ValueError, KeyError, TypeError, OSError):
            return JSONResponse({"error": "restore_cancel_rejected"}, status_code=409)
