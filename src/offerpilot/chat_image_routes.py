"""Authenticated image upload/preview routes mounted under the existing API gate."""

from __future__ import annotations

import io
import hashlib
import secrets
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request
from starlette.datastructures import UploadFile
from starlette.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from PIL import Image, ImageDraw, ImageFont

from offerpilot.chat_images import ChatImageStore, normalize_image
from offerpilot.config import AIProviderProfile, Config
from offerpilot.image_contracts import (
    MAX_IMAGE_BYTES, MAX_MESSAGE_IMAGES, MAX_MESSAGE_IMAGE_BYTES,
    ImageInputError, ImageReference, VISION_ADAPTERS, image_error_message,
)


def image_capability(config: Config) -> dict[str, Any]:
    primary = config.active_provider()
    reason = ""
    if not primary.enabled or not primary.api_key:
        reason = "当前模型尚未配置或已停用"
    elif not primary.supports_vision:
        reason = "当前模型未开启图片输入，请在 AI 设置中确认能力或切换模型"
    elif primary.provider not in VISION_ADAPTERS:
        reason = "当前模型适配器暂不支持图片传输"
    return {
        "enabled": not reason, "reason": reason, "provider_id": primary.id,
        "model": primary.model, "max_images": MAX_MESSAGE_IMAGES,
        "max_file_bytes": MAX_IMAGE_BYTES, "max_total_bytes": MAX_MESSAGE_IMAGE_BYTES,
        "accepted_mime_types": ["image/png", "image/jpeg", "image/webp"],
    }


def require_image_capability(config: Config) -> None:
    if not image_capability(config)["enabled"]:
        raise ImageInputError("provider_vision_required")


def image_error(error: ImageInputError, status_code: int = 422) -> JSONResponse:
    return JSONResponse({"error": image_error_message(error),
                         "error_code": str(error)}, status_code=status_code)


def image_router(store: ChatImageStore, get_config: Callable[[], Config],
                 resolve_provider: Callable[[dict[str, Any], Config], tuple[AIProviderProfile | None, str | None]]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/chat/image-capability")
    def capability() -> dict[str, Any]:
        return image_capability(get_config())

    @router.post("/api/chat/attachments")
    async def upload(request: Request) -> Response:
        try:
            require_image_capability(get_config())
            # Bound the complete multipart stream before parsing, including
            # clients without Content-Length. Starlette's file limit alone
            # only caps text parts and would permit unlimited spooled uploads.
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_IMAGE_BYTES + 65536:
                    raise ImageInputError("image_size_exceeded")
                body.extend(chunk)
            async def receive() -> dict[str, Any]:
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            bounded = Request(request.scope, receive)
            async with bounded.form(max_files=1, max_fields=3, max_part_size=4096) as form:
                file = form.get("file")
                if not isinstance(file, UploadFile):
                    raise ImageInputError("image_file_required")
                content = await file.read(MAX_IMAGE_BYTES + 1)
                raw_conversation = form.get("conversation_id")
                conversation_id = int(str(raw_conversation)) if raw_conversation else None
                if conversation_id is not None and conversation_id < 1:
                    raise ImageInputError("image_conversation_unavailable")
                ref = await run_in_threadpool(store.upload, content, context_type=str(form.get("context_type") or "workspace"),
                                              context_ref=str(form.get("context_ref") or ""), conversation_id=conversation_id)
            return JSONResponse(ref.as_dict(), status_code=201, headers={"Cache-Control": "no-store"})
        except ImageInputError as exc:
            return image_error(exc)
        except (ValueError, LookupError):
            return image_error(ImageInputError("image_scope_mismatch"))

    @router.get("/api/chat/attachments/{attachment_id}")
    def preview(attachment_id: str) -> Response:
        try:
            ref, content = store.read_authorized(attachment_id)
            return Response(content, media_type=ref.mime_type, headers={
                "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "inline", "Content-Security-Policy": "default-src 'none'",
            })
        except (ImageInputError, ValueError, LookupError):
            return image_error(ImageInputError("image_unavailable"), 404)

    @router.post("/api/settings/providers/test-image")
    def test_image(payload: dict[str, Any]) -> dict[str, Any]:
        from offerpilot.ai.client import ConfiguredAIClient, _is_deepseek_v4_provider
        from offerpilot.ai.types import Message

        provider, error = resolve_provider(payload, get_config())
        if provider is None:
            return {"ok": False, "error": error or "模型配置不可用"}
        if not provider.supports_vision or provider.provider not in VISION_ADAPTERS:
            return {"ok": False, "error": "请先确认当前配置接受图片输入"}
        answer = str(secrets.randbelow(9000) + 1000)
        canvas = Image.new("RGB", (280, 100), "white")
        ImageDraw.Draw(canvas).text((25, 15), answer, font=ImageFont.load_default(size=60), fill="black")
        sample = io.BytesIO()
        canvas.save(sample, format="PNG")
        content, mime, width, height = normalize_image(sample.getvalue())
        ref = ImageReference("img_" + uuid4().hex, hashlib.sha256(content).hexdigest(), mime,
                             len(content), width, height)
        started = time.monotonic()
        try:
            client = ConfiguredAIClient(Config(providers=[provider], active_provider_id=provider.id),
                                        image_reader=lambda _: content)
            result = client._complete_with_provider(provider, [Message(
                role="user", content="读取图片中的四位数字，只输出这四位数字。", images=(ref,),
            )], [], timeout_seconds=30, output_limit=128, disable_thinking=_is_deepseek_v4_provider(provider))
            ok = result.content.strip() == answer
            return {"ok": ok, "provider_id": provider.id, "model": provider.model,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "message": "图片样例识别通过" if ok else "请求完成，但图片内容未正确识别",
                    **({} if ok else {"error": "图片样例未通过，请检查模型与接口的视觉能力"})}
        except Exception:
            # A provider exception may include the complete image-bearing body.
            return {"ok": False, "error": "图片测试失败，请检查模型、鉴权和网络后重试"}

    return router
