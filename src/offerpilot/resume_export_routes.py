"""Authenticated by the application's existing API middleware; no persistence writes."""
import json
from typing import Any

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from offerpilot import resume_export
from offerpilot.repositories.resumes import ResumesRepository


def register_resume_export_routes(app: FastAPI, resumes: ResumesRepository) -> None:
    def export(resume_id: int, payload: Any, *, preview: bool) -> Response:
        try:
            locale, fmt, expected = resume_export.validate_request(payload, preview=preview)
            saved = resumes.get(resume_id)
            if saved is None:
                raise resume_export.ResumeExportError('not_found', '简历不存在。', 404)
            if not preview and resume_export.source_fingerprint(saved, locale) != expected:
                raise resume_export.ResumeExportError(
                    'source_conflict', '已保存简历已变化，请重新预览后下载。', 409
                )
            source = resume_export.snapshot(saved, locale)
            data = (resume_export.render_pdf(source) if fmt == 'pdf'
                    else resume_export.render_docx(source))
            # Recheck after expensive rendering. Never send an already-stale download.
            current = resumes.get(resume_id)
            if current is None:
                raise resume_export.ResumeExportError('not_found', '简历不存在。', 404)
            if resume_export.source_fingerprint(current, locale) != source.fingerprint:
                raise resume_export.ResumeExportError(
                    'source_conflict', '已保存简历已变化，请重新预览后下载。', 409
                )
            media_type = ('application/pdf' if fmt == 'pdf' else
                          'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
            return Response(data, media_type=media_type, headers=resume_export.response_headers(
                source, fmt, preview=preview,
            ))
        except resume_export.ResumeExportError as exc:
            return JSONResponse({'error': exc.message, 'error_code': exc.code},
                                status_code=exc.status, headers={'Cache-Control': 'no-store'})
        except Exception:
            # Do not log resume content, renderer errors, or local source paths.
            return JSONResponse({
                'error': '简历导出失败，请检查内容后重试。',
                'error_code': 'resume_export_render_failed',
            }, status_code=500, headers={'Cache-Control': 'no-store'})

    async def handle(request: Request, resume_id: int, *, preview: bool) -> Response:
        # Bound before JSON parsing; clients never submit document content here.
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 2048:
                return JSONResponse({
                    'error': '导出请求过大。', 'error_code': 'resume_export_invalid_request',
                }, status_code=422, headers={'Cache-Control': 'no-store'})
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeError, RecursionError):
            return JSONResponse({
                'error': '导出请求必须是有效的 JSON。',
                'error_code': 'resume_export_invalid_request',
            }, status_code=422, headers={'Cache-Control': 'no-store'})
        return await run_in_threadpool(export, resume_id, payload, preview=preview)

    @app.post('/api/resumes/{resume_id}/export-preview')
    async def preview_resume_export(resume_id: int, request: Request) -> Response:
        return await handle(request, resume_id, preview=True)

    @app.post('/api/resumes/{resume_id}/export')
    async def download_resume_export(resume_id: int, request: Request) -> Response:
        return await handle(request, resume_id, preview=False)
