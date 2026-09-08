"""KI-04：Source Bundle（Markdown + 图片附件）单元测试。

覆盖 Spec §4.2（Bundle 限制）、§4.4（自包含 Bundle）、§5.1（内容寻址含附件 manifest）、
§8.1（image reference 映射 Asset Evidence）、§8.3（图片不进 FTS / 不 OCR）、
§13（Asset 文件存储）。
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from offerpilot.knowledge.assets import (
    MAX_ASSET_BYTES,
    MAX_ASSET_COUNT,
    MAX_BUNDLE_BYTES,
    MAX_PIXELS,
    AssetInput,
    AssetValidationError,
    safe_logical_name,
    verify_bundle,
    verify_image_asset,
)
from offerpilot.knowledge.extractor import (
    EXTRACTOR_VERSION,
    MarkdownExtractor,
    compute_bundle_source_hash,
)


def _png_bytes(width: int, height: int, color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(width: int, height: int, color: tuple[int, int, int] = (0, 0, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _webp_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (0, 255, 0)).save(buf, format="WEBP", quality=80)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Spec §4.2 / §4.4 Bundle 限制
# ---------------------------------------------------------------------------


def test_verify_image_asset_accepts_png():
    raw = _png_bytes(10, 10)
    verified = verify_image_asset(AssetInput(logical_name="a.png", content_bytes=raw))
    assert verified.media_type == "image/png"
    assert verified.width == 10
    assert verified.height == 10
    assert verified.bytes_size == len(raw)


def test_verify_image_asset_accepts_jpeg():
    verified = verify_image_asset(
        AssetInput(logical_name="a.jpg", content_bytes=_jpeg_bytes(20, 10))
    )
    assert verified.media_type == "image/jpeg"
    assert verified.width == 20
    assert verified.height == 10


def test_verify_image_asset_accepts_webp():
    verified = verify_image_asset(
        AssetInput(logical_name="a.webp", content_bytes=_webp_bytes(15, 15))
    )
    assert verified.media_type == "image/webp"
    assert verified.width == 15


def test_verify_image_asset_rejects_empty_bytes():
    with pytest.raises(AssetValidationError) as exc:
        verify_image_asset(AssetInput(logical_name="a.png", content_bytes=b""))
    assert exc.value.code == "bundle_invalid"


def test_verify_image_asset_rejects_oversize_bytes():
    # 构造一张超过 10 MiB 的"PNG"——通过 Pillow 生成 10MiB+1 字节
    big = b"x" * (MAX_ASSET_BYTES + 1)
    with pytest.raises(AssetValidationError) as exc:
        verify_image_asset(AssetInput(logical_name="big.png", content_bytes=big))
    assert exc.value.code == "source_too_large"


def test_verify_image_asset_rejects_corrupt_image():
    with pytest.raises(AssetValidationError) as exc:
        verify_image_asset(
            AssetInput(logical_name="bad.png", content_bytes=b"\x89PNG\r\n\x1a\n not a png")
        )
    assert exc.value.code == "bundle_invalid"


def test_verify_image_asset_rejects_extension_mismatch():
    # 真实是 PNG，但 logical_name 声明 .jpg
    with pytest.raises(AssetValidationError) as exc:
        verify_image_asset(
            AssetInput(logical_name="lie.jpg", content_bytes=_png_bytes(8, 8))
        )
    assert exc.value.code == "bundle_invalid"


def test_verify_image_asset_rejects_pixel_bomb():
    # 像素炸弹：高分辨率但压缩比高。直接用 Pillow 生成 7000x7000 PNG 太大；
    # 这里直接构造超大像素触发 40MP 限制。
    over_pixels = MAX_PIXELS + 1
    side = int(over_pixels ** 0.5) + 1
    big = _png_bytes(side, side)
    with pytest.raises(AssetValidationError) as exc:
        verify_image_asset(AssetInput(logical_name="boom.png", content_bytes=big))
    assert exc.value.code == "source_too_large"


# ---------------------------------------------------------------------------
# Spec §4.4 路径白名单：扁平相对路径
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../escape.png",
        "sub/dir/a.png",
        "/abs/path.png",
        "C:/windows.png",
        "a/b.png",
        "..",
        "a/../../../etc/passwd",
    ],
)
def test_safe_logical_name_rejects_non_flat_paths(name: str) -> None:
    with pytest.raises(AssetValidationError):
        safe_logical_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "image.png",
        "屏幕截图.png",  # 非 ASCII 应拒绝（仅允许 ASCII）
    ],
)
def test_safe_logical_name_policy(name: str) -> None:
    # ASCII 标准名应通过；非 ASCII 应拒绝
    if name == "image.png":
        assert safe_logical_name(name) == "image.png"
    else:
        with pytest.raises(AssetValidationError):
            safe_logical_name(name)


def test_safe_logical_name_rejects_long_names() -> None:
    huge = "a" * 300 + ".png"
    with pytest.raises(AssetValidationError):
        safe_logical_name(huge)


# ---------------------------------------------------------------------------
# Spec §4.4 Bundle 级 Preflight
# ---------------------------------------------------------------------------


def test_verify_bundle_rejects_zero_assets() -> None:
    with pytest.raises(AssetValidationError):
        verify_bundle(b"# main\n", [])


def test_verify_bundle_rejects_more_than_50_assets() -> None:
    assets = [
        AssetInput(logical_name=f"a{i:02d}.png", content_bytes=_png_bytes(2, 2))
        for i in range(MAX_ASSET_COUNT + 1)
    ]
    with pytest.raises(AssetValidationError) as exc:
        verify_bundle(b"# main\n", assets)
    assert exc.value.code == "source_too_large"


def test_verify_bundle_rejects_duplicate_logical_name() -> None:
    with pytest.raises(AssetValidationError) as exc:
        verify_bundle(
            b"# main\n",
            [
                AssetInput(logical_name="dup.png", content_bytes=_png_bytes(2, 2)),
                AssetInput(logical_name="dup.png", content_bytes=_png_bytes(3, 3)),
            ],
        )
    assert exc.value.code == "bundle_invalid"


def test_verify_bundle_rejects_total_oversize() -> None:
    # 主文件 1 MiB + 单张 6 MiB（合法单图） + 6 MiB（合法单图）= 13 MiB < 50 MiB；
    # 但若让总大小超 50 MiB，需要 5 张 10 MiB 图——单张上限是 10 MiB，因此用 6 张近 9 MiB
    # 的 PNG（PNG 压缩后可能不大，这里直接用 bytes 构造非法 PNG 触发 bundle_invalid 也行）。
    # 简化：直接通过 monkey patching MAX_BUNDLE_BYTES 不合适；改为构造大主文件 + 多附件。
    # 主文件 49 MiB + 一张 2 MiB PNG → 总 51 MiB → 超过 50 MiB
    big_main = b"a" * (MAX_BUNDLE_BYTES - 1)
    one_mib_png = _png_bytes(8, 8)
    # 主文件已经接近 50 MiB（但小于 5 MiB 主文件上限），加任何附件就超 Bundle 上限
    # 这里我们绕过 MAX_FILE_BYTES 检查（属于 service 层）
    with pytest.raises(AssetValidationError) as exc:
        verify_bundle(big_main, [AssetInput("a.png", one_mib_png)])
    assert exc.value.code == "source_too_large"


# ---------------------------------------------------------------------------
# Spec §5.1 Bundle source_hash
# ---------------------------------------------------------------------------


def test_bundle_source_hash_stable_for_same_inputs() -> None:
    main = b"# title\n\n![alt](a.png)\n"
    a = _png_bytes(2, 2)
    h1 = compute_bundle_source_hash(main, [("a.png", a)])
    h2 = compute_bundle_source_hash(main, [("a.png", a)])
    assert h1 == h2


def test_bundle_source_hash_differs_for_different_logical_name() -> None:
    main = b"# title\n![alt](a.png)\n"
    a = _png_bytes(2, 2)
    h1 = compute_bundle_source_hash(main, [("a.png", a)])
    h2 = compute_bundle_source_hash(main, [("b.png", a)])
    assert h1 != h2


def test_bundle_source_hash_differs_when_asset_bytes_change() -> None:
    main = b"# title\n![alt](a.png)\n"
    a1 = _png_bytes(2, 2, color=(1, 2, 3))
    a2 = _png_bytes(2, 2, color=(4, 5, 6))
    h1 = compute_bundle_source_hash(main, [("a.png", a1)])
    h2 = compute_bundle_source_hash(main, [("a.png", a2)])
    assert h1 != h2


def test_bundle_source_hash_invariant_to_asset_order() -> None:
    main = b"# title\n![a](a.png) ![b](b.png)\n"
    a = _png_bytes(2, 2)
    b = _png_bytes(3, 3, color=(0, 0, 0))
    h1 = compute_bundle_source_hash(main, [("a.png", a), ("b.png", b)])
    h2 = compute_bundle_source_hash(main, [("b.png", b), ("a.png", a)])
    assert h1 == h2


# ---------------------------------------------------------------------------
# Spec §8.1 / §8.3 image reference Evidence
# ---------------------------------------------------------------------------


def test_extractor_emits_image_evidence_with_logical_name_and_alt() -> None:
    sample = "# H\n\nPara with ![alt text](a.png) inline.\n"
    drafts = MarkdownExtractor().extract(sample).evidence_drafts
    image_drafts = [d for d in drafts if d.block_kind == "image"]
    assert len(image_drafts) == 1
    draft = image_drafts[0]
    assert draft.extra["logical_name"] == "a.png"
    assert draft.extra["alt_text"] == "alt text"
    assert draft.canonical_excerpt == "![alt text](a.png)"


def test_extractor_records_multiple_references_to_same_image() -> None:
    sample = "# H\n\nFirst ![a](pic.png).\n\nSecond ![a](pic.png).\n"
    drafts = MarkdownExtractor().extract(sample).evidence_drafts
    image_drafts = [d for d in drafts if d.block_kind == "image"]
    assert len(image_drafts) == 2
    locators = {d.locator for d in image_drafts}
    assert len(locators) == 2
    assert all(d.extra["logical_name"] == "pic.png" for d in image_drafts)


def test_extractor_image_evidence_char_range_aligned_with_canonical() -> None:
    sample = "# H\n\nText ![my alt](pic.png) after.\n"
    extraction = MarkdownExtractor().extract(sample)
    image_draft = next(d for d in extraction.evidence_drafts if d.block_kind == "image")
    assert (
        extraction.canonical_text[image_draft.char_start:image_draft.char_end]
        == image_draft.canonical_excerpt
    )


def test_extractor_image_locator_fallback_when_alt_has_escape() -> None:
    """alt 含 markdown 转义字符时，child.content 与 canonical 字面值可能不一致；
    回退到 URL 边界扫描仍应正确定位。"""

    sample = "# H\n\n![\\*bold\\*](pic.png)\n"
    extraction = MarkdownExtractor().extract(sample)
    image_drafts = [d for d in extraction.evidence_drafts if d.block_kind == "image"]
    assert len(image_drafts) == 1
    draft = image_drafts[0]
    assert (
        extraction.canonical_text[draft.char_start:draft.char_end]
        == draft.canonical_excerpt
    )
    assert draft.extra["logical_name"] == "pic.png"


def test_extractor_version_is_kbr03() -> None:
    # KBR-03：evidence eligibility policy（过滤元数据样板）规则变化，升级版本。
    # KBR-02 的 frontmatter/provenance 规则变化已包含在本版本上游。
    assert EXTRACTOR_VERSION == "md-kbr03-2"
