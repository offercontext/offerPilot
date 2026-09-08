"""Source Bundle 附件（图片）验证模块。

Spec §4.4 / §4.2 限制：
- 单张图片 10 MiB
- Bundle 总大小 50 MiB
- 附件数量上限 50
- 单张图片像素上限 40 megapixels
- 仅支持 PNG / JPEG / WebP
- 图片必须使用扁平相对路径；禁止远程、绝对、父目录、跨目录

本模块只负责字节与路径验证，不读写数据库或文件系统。Pillow 用作真实解码校验，
扩展名和上传时声明 Content-Type 都不可信。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, UnidentifiedImageError


# Spec §4.2 附件限制
MAX_ASSET_BYTES = 10 * 1024 * 1024
MAX_BUNDLE_BYTES = 50 * 1024 * 1024
MAX_ASSET_COUNT = 50
MAX_PIXELS = 40_000_000
MAX_LOGICAL_NAME_BYTES = 255

# Spec §4.4：扁平相对路径；禁止路径分隔符、绝对前缀、父目录引用。
_FORBIDDEN_PATH_TOKENS = ("/", "\\", ":", "..")
_LOGICAL_NAME_RE = re.compile(r"^[A-Za-z0-9._\- ]+$")

# Spec §4.4 支持的图片媒体类型。
SUPPORTED_IMAGE_MEDIA_TYPES: tuple[str, ...] = ("image/png", "image/jpeg", "image/webp")

# 扩展名 → 媒体类型映射仅用于默认建议；真实判定以 Pillow 解码为准。
_EXTENSION_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _configure_pixel_ceiling() -> None:
    """把 Pillow 内置 DecompressionBomb 阈值对齐到 Spec §4.2 的 40 MP。

    Pillow 默认 ``MAX_IMAGE_PIXELS = 89_478_485``；若不显式调低，40～89MP 之间的
    像素炸弹会先在 ``Image.open`` 拿到尺寸（仅读 header），随后在 ``image.load()``
    时实际分配位图内存——这就构成 DoS 风险。我们把 Pillow 阈值设为本模块的 40 MP，
    让 Pillow 在 load 阶段直接抛 ``DecompressionBombError``，避免真正解码。
    """

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS


_configure_pixel_ceiling()


class AssetValidationError(Exception):
    """附件 Preflight 失败，携带稳定 error_code 与 user-safe message。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AssetInput:
    """Service 层接受的附件输入，调用方负责读字节和给出原始 logical name。"""

    logical_name: str
    content_bytes: bytes


@dataclass(frozen=True)
class VerifiedAsset:
    """通过 Preflight 的附件快照，进入 Service 提交流程。"""

    logical_name: str
    media_type: str
    content_bytes: bytes
    sha256: str
    bytes_size: int
    width: int
    height: int


@dataclass(frozen=True)
class AssetReference:
    """Markdown 中提取出的图片引用，由 extractor 上报给 Service。

    ``logical_name`` 是 Markdown 引用中解码出的扁平相对路径；Service 会与
    Bundle 上传时的附件清单做完整性检查。
    """

    logical_name: str
    alt_text: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int


def safe_logical_name(raw: str) -> str:
    """规范化用户提交的附件名，但保留原始可读性。

    Spec §4.4 要求扁平相对路径。本函数返回归一化后的 ``logical_name``；任何包含
    路径分隔符、父目录引用、绝对前缀的输入都会触发 ``bundle_invalid``。
    """

    name = raw.strip()
    if not name:
        raise AssetValidationError("bundle_invalid", "附件名为空")
    if len(name.encode("utf-8")) > MAX_LOGICAL_NAME_BYTES:
        raise AssetValidationError(
            "bundle_invalid",
            f"附件名 {name!r} 超过 255 字节上限",
        )
    for token in _FORBIDDEN_PATH_TOKENS:
        if token in name:
            raise AssetValidationError(
                "bundle_invalid",
                (
                    f"附件名 {name!r} 必须是扁平相对路径；"
                    "禁止绝对路径、父目录引用或子目录"
                ),
            )
    if not _LOGICAL_NAME_RE.match(name):
        raise AssetValidationError(
            "bundle_invalid",
            (
                f"附件名 {name!r} 只能包含字母、数字、点、下划线、连字符和空格；"
                "请改用 ASCII 扁平文件名"
            ),
        )
    # 控制字符已经在正则里拒绝；这里再确认没有 NUL。
    if "\x00" in name:
        raise AssetValidationError("bundle_invalid", f"附件名 {name!r} 包含 NUL 控制字符")
    return name


def verify_image_asset(asset: AssetInput) -> VerifiedAsset:
    """真实解码一张图片附件，返回校验后的 ``VerifiedAsset``。

    Spec §4.4 要求"必须通过真实解码、媒体类型、大小、像素和 hash 检查"。本函数
    分三阶段：

    1. 字节大小校验（不做解码）；
    2. Pillow ``Image.open`` 只读 header，取 width/height 做 40 MP 像素上限检查；
    3. ``image.verify()`` + ``image.load()`` 完成真实解码；模块 import 时已经把
       ``Image.MAX_IMAGE_PIXELS`` 对齐到 40 MP，即使 header撒谎也会在 load 阶段
       被 Pillow ``DecompressionBombError`` 兜底拦截。
    """

    size = len(asset.content_bytes)
    if size == 0:
        raise AssetValidationError("bundle_invalid", f"附件 {asset.logical_name!r} 内容为空")
    if size > MAX_ASSET_BYTES:
        raise AssetValidationError(
            "source_too_large",
            (
                f"附件 {asset.logical_name!r} 大小 {size} 字节超出上限 "
                f"{MAX_ASSET_BYTES} 字节"
            ),
        )

    # 阶段一：仅读 header 拿 width/height，提前拦截像素炸弹，避免 load 阶段
    # 才发现像素超限导致真实分配位图内存的 DoS 风险。
    try:
        with Image.open(BytesIO(asset.content_bytes)) as header_image:
            width = int(header_image.width)
            height = int(header_image.height)
            raw_format = (header_image.format or "").upper()
    except UnidentifiedImageError as exc:
        raise AssetValidationError(
            "bundle_invalid",
            f"附件 {asset.logical_name!r} 不是有效的图片或媒体类型不支持",
        ) from exc
    except Exception as exc:  # Pillow 抛出多种 header 错误，统一归入 bundle_invalid
        raise AssetValidationError(
            "bundle_invalid",
            f"附件 {asset.logical_name!r} 解码失败：{type(exc).__name__}",
        ) from exc

    if width <= 0 or height <= 0:
        raise AssetValidationError(
            "bundle_invalid",
            f"附件 {asset.logical_name!r} 解析出无效尺寸 {width}x{height}",
        )
    pixels = width * height
    if pixels > MAX_PIXELS:
        raise AssetValidationError(
            "source_too_large",
            (
                f"附件 {asset.logical_name!r} 像素 {pixels} 超过 40MP 上限 "
                f"({MAX_PIXELS})"
            ),
        )

    # 阶段二：真实解码。即使 header 与实际像素不符，``Image.MAX_IMAGE_PIXELS``
    # 已在模块 import 时被对齐到 40 MP，load() 会抛 DecompressionBombError。
    try:
        with Image.open(BytesIO(asset.content_bytes)) as verify_image:
            verify_image.verify()
    except Exception as exc:  # verify 失败归入 bundle_invalid
        raise AssetValidationError(
            "bundle_invalid",
            f"附件 {asset.logical_name!r} 校验失败：{type(exc).__name__}",
        ) from exc
    try:
        with Image.open(BytesIO(asset.content_bytes)) as loaded_image:
            loaded_image.load()
    except Exception as exc:  # load 失败归入 bundle_invalid
        raise AssetValidationError(
            "bundle_invalid",
            f"附件 {asset.logical_name!r} 解码失败：{type(exc).__name__}",
        ) from exc

    media_type = _format_to_media_type(raw_format, asset.logical_name)
    if media_type not in SUPPORTED_IMAGE_MEDIA_TYPES:
        raise AssetValidationError(
            "bundle_invalid",
            (
                f"附件 {asset.logical_name!r} 媒体类型 {media_type or raw_format or '未知'} "
                "不在支持范围内（PNG / JPEG / WebP）"
            ),
        )

    # 部分媒体伪装场景：扩展名声明 X，但 Pillow 识别到 Y；统一拒绝。
    expected_from_name = _extension_media_type(asset.logical_name)
    if expected_from_name is not None and expected_from_name != media_type:
        raise AssetValidationError(
            "bundle_invalid",
            (
                f"附件 {asset.logical_name!r} 扩展名期望 {expected_from_name}，"
                f"实际解码为 {media_type}（媒体类型不一致）"
            ),
        )

    sha256 = hashlib.sha256(asset.content_bytes).hexdigest()
    return VerifiedAsset(
        logical_name=asset.logical_name,
        media_type=media_type,
        content_bytes=asset.content_bytes,
        sha256=sha256,
        bytes_size=size,
        width=width,
        height=height,
    )


def verify_bundle(
    main_bytes: bytes,
    raw_assets: list[AssetInput],
) -> tuple[list[VerifiedAsset], int]:
    """Spec §4.4 / §4.2 Bundle 级 Preflight。

    返回 (已验证附件列表, 主文件字节数)。 ``raw_assets`` 中每个附件逐张走
    ``verify_image_asset``；所有错误以 ``AssetValidationError`` 抛出，调用方负责
    翻译成 HTTP error_code。
    """

    main_size = len(main_bytes)
    if main_size == 0:
        raise AssetValidationError("bundle_invalid", "Bundle 主文件内容为空")
    if len(raw_assets) == 0:
        raise AssetValidationError("bundle_invalid", "Bundle 必须至少包含一个附件")
    if len(raw_assets) > MAX_ASSET_COUNT:
        raise AssetValidationError(
            "source_too_large",
            f"附件数量 {len(raw_assets)} 超过上限 {MAX_ASSET_COUNT}",
        )

    bundle_total = main_size
    verified: list[VerifiedAsset] = []
    seen_logical: set[str] = set()
    for raw in raw_assets:
        safe_name = safe_logical_name(raw.logical_name)
        if safe_name in seen_logical:
            raise AssetValidationError(
                "bundle_invalid",
                f"附件逻辑名 {safe_name!r} 在 Bundle 中重复",
            )
        seen_logical.add(safe_name)
        normalized_input = AssetInput(
            logical_name=safe_name, content_bytes=raw.content_bytes
        )
        verified_asset = verify_image_asset(normalized_input)
        bundle_total += verified_asset.bytes_size
        if bundle_total > MAX_BUNDLE_BYTES:
            raise AssetValidationError(
                "source_too_large",
                (
                    f"Bundle 总大小 {bundle_total} 字节超出上限 "
                    f"{MAX_BUNDLE_BYTES} 字节"
                ),
            )
        verified.append(verified_asset)
    return verified, main_size


def _format_to_media_type(raw_format: str, logical_name: str) -> str:
    """根据 Pillow 解码格式返回标准媒体类型；无法识别时回退到扩展名。"""

    fmt = raw_format.upper()
    if fmt == "PNG":
        return "image/png"
    if fmt in {"JPEG", "JPG"}:
        return "image/jpeg"
    if fmt == "WEBP":
        return "image/webp"
    # Pillow 未识别格式时，尝试扩展名；Service 层会再校验是否在支持范围内。
    return _extension_media_type(logical_name) or ""


def _extension_media_type(logical_name: str) -> str | None:
    lowered = logical_name.lower()
    for ext, media in _EXTENSION_MEDIA_TYPES.items():
        if lowered.endswith(ext):
            return media
    return None
