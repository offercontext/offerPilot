"""Content-addressed image references shared by storage and model surfaces.

References contain no paths, URLs, credentials or image bytes. Only the trusted
storage boundary can resolve them to bytes, immediately before a provider call.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_MESSAGE_IMAGE_BYTES = 20 * 1024 * 1024
MAX_MESSAGE_IMAGES = 4
MAX_CALL_IMAGES = 8
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_SIDE = 10_000
IMAGE_REQUEST_BODY_CAP = 32 * 1024 * 1024
IMAGE_NORMALIZATION_VERSION = "image-normalize-v1"
IMAGE_ESTIMATOR_VERSION = "image-patch-upper-bound-v1"
IMAGE_ID_PATTERN = re.compile(r"img_[0-9a-f]{32}")
VISION_ADAPTERS = frozenset({"openai", "openai_compatible", "litellm_proxy", "anthropic", "gemini"})


class ImageInputError(ValueError):
    """Finite, content-free error codes safe for API and diagnostic surfaces."""


def image_error_message(error: ImageInputError) -> str:
    labels = {
        "provider_vision_required": "当前模型不支持图片输入，请在 AI 设置中确认能力或切换模型。",
        "image_scope_mismatch": "图片不属于当前对话，请重新添加。",
        "image_size_exceeded": "单张图片不能超过 10 MiB。",
        "image_normalized_size_exceeded": "图片处理后过大，请裁剪或拆分后重试。",
        "image_dimensions_exceeded": "图片尺寸过大，请裁剪或拆分长截图。",
        "image_format_unsupported": "仅支持 PNG、JPEG 和静态 WebP 图片。",
        "image_animation_unsupported": "暂不支持动图，请使用静态截图。",
        "image_decode_failed": "图片无法读取，请重新导出后添加。",
        "image_content_changed": "图片内容校验失败，请重新添加。",
        "image_unavailable": "图片已失效或被删除，请重新添加。",
        "image_total_size_exceeded": "一条消息的图片总大小不能超过 20 MiB。",
        "image_context_count_exceeded": "本轮历史图片过多，请新建对话并只引用需要的图片。",
        "image_context_budget_exceeded": "本轮必需图片超出当前模型上下文预算，请裁剪、减少图片或切换到上下文更大的视觉模型。",
        "image_context_size_exceeded": "本轮图片总量过大，请减少图片或新建对话。",
    }
    return labels.get(str(error), "图片不可用，请重新添加。")


@dataclass(frozen=True, slots=True)
class ImageReference:
    id: str
    sha256: str
    mime_type: str
    size_bytes: int
    width: int
    height: int
    normalization_version: str = IMAGE_NORMALIZATION_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or IMAGE_ID_PATTERN.fullmatch(self.id) is None:
            raise ImageInputError("invalid_image_reference")
        if not isinstance(self.sha256, str) or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None:
            raise ImageInputError("invalid_image_reference")
        if self.mime_type not in {"image/png", "image/jpeg"}:
            raise ImageInputError("invalid_image_reference")
        if any(type(value) is not int or value <= 0 for value in (self.size_bytes, self.width, self.height)):
            raise ImageInputError("invalid_image_reference")
        if (self.size_bytes > MAX_IMAGE_BYTES or self.width * self.height > MAX_IMAGE_PIXELS
                or max(self.width, self.height) > MAX_IMAGE_SIDE
                or self.normalization_version != IMAGE_NORMALIZATION_VERSION):
            raise ImageInputError("invalid_image_reference")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ImageReference:
        try:
            return cls(**dict(value))
        except (TypeError, KeyError) as exc:
            raise ImageInputError("invalid_image_reference") from exc

    @property
    def input_units(self) -> int:
        # Conservative patch allowance, deliberately independent of base64 size.
        # Unknown compatible endpoints use this bounded product policy. Actual
        # model/adapter compatibility must still be verified for that endpoint.
        return 1024 + 4 * ((self.width + 31) // 32) * ((self.height + 31) // 32)


def image_references(value: object) -> tuple[ImageReference, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_MESSAGE_IMAGES:
        raise ImageInputError("invalid_image_references")
    refs = tuple(item if isinstance(item, ImageReference) else ImageReference.from_dict(item)
                 for item in value if isinstance(item, (ImageReference, Mapping)))
    if len(refs) != len(value) or len({ref.id for ref in refs}) != len(refs):
        raise ImageInputError("invalid_image_references")
    if sum(ref.size_bytes for ref in refs) > MAX_MESSAGE_IMAGE_BYTES:
        raise ImageInputError("image_total_size_exceeded")
    return refs


def image_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_MESSAGE_IMAGES:
        raise ImageInputError("image_count_exceeded")
    if any(type(item) is not str or IMAGE_ID_PATTERN.fullmatch(item) is None for item in value):
        raise ImageInputError("invalid_image_reference")
    if len(set(value)) != len(value):
        raise ImageInputError("duplicate_image_reference")
    return tuple(value)


def require_image_call_limits(refs: tuple[ImageReference, ...]) -> None:
    if len(refs) > MAX_CALL_IMAGES:
        raise ImageInputError("image_context_count_exceeded")
    if sum(ref.size_bytes for ref in refs) > MAX_MESSAGE_IMAGE_BYTES:
        raise ImageInputError("image_context_size_exceeded")
