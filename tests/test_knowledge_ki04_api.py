"""KI-04：Source Bundle API 集成测试。

覆盖 Spec §4.4（Bundle 完整性 / 缺图 / 重复 / 未使用 / 像素炸弹 / 媒体伪装 / 路径穿越）、
§5（source_hash 含 manifest）、§13（Asset 文件存储）、§16.1（multipart bundle）。
"""

from __future__ import annotations

import io
import sqlite3

from fastapi.testclient import TestClient
from conftest import wait_for_extraction
from PIL import Image




def _png_bytes(width: int, height: int, color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (0, 0, 255)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _webp_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (0, 255, 0)).save(buf, format="WEBP", quality=80)
    return buf.getvalue()


def _upload_bundle(
    client: TestClient,
    *,
    main: tuple[str, bytes],
    assets: list[tuple[str, bytes]],
    title_hint: str = "",
):
    files = [("file", main)]
    for name, content in assets:
        files.append(("files", (name, content, "application/octet-stream")))
    data: dict[str, str] = {}
    if title_hint:
        data["title_hint"] = title_hint
    response = client.post("/api/knowledge/sources", files=files, data=data)
    if response.status_code in (200, 202):
        wait_for_extraction(client, response.json()["source"]["id"])
    return response

# ---------------------------------------------------------------------------
# Spec §4.4 正常 Bundle：上传 / Asset / Evidence
# ---------------------------------------------------------------------------


def test_ki04_bundle_upload_creates_source_with_assets(app_client, tmp_path):
    main = b"# Bundle\n\nPara with ![alt](pic.png).\n"
    pic = _png_bytes(20, 20)
    response = _upload_bundle(app_client, main=("bundle.md", main), assets=[("pic.png", pic)])
    assert response.status_code == 202, response.text
    body = response.json()
    source = body["source"]
    assert source["source_kind"] == "bundle"
    source_id = source["id"]

    # Asset 行
    assets = app_client.get(f"/api/knowledge/sources/{source_id}/assets").json()
    assert len(assets["items"]) == 1
    asset = assets["items"][0]
    assert asset["logical_name"] == "pic.png"
    assert asset["media_type"] == "image/png"
    assert asset["width"] == 20
    assert asset["height"] == 20
    assert asset["bytes"] == len(pic)

    # Evidence：含 image Evidence，关联 asset_id
    ev_response = app_client.get(f"/api/knowledge/sources/{source_id}/evidence")
    ev_body = ev_response.json()
    image_evs = [item for item in ev_body["items"] if item["block_kind"] == "image"]
    assert len(image_evs) == 1
    assert image_evs[0]["kind"] == "asset"
    assert image_evs[0]["asset_id"] == asset["id"]


def test_ki04_bundle_files_persisted_under_assets_dir(app_client, tmp_path):
    main = b"# Bundle\n\n![pic](pic.png)\n"
    pic = _png_bytes(8, 8)
    response = _upload_bundle(app_client, main=("b.md", main), assets=[("pic.png", pic)])
    assert response.status_code == 202
    source_id = response.json()["source"]["id"]

    # 数据库 relative_path 指向 knowledge/sources/<id>/assets/<id>-<safe-name>
    db_path = tmp_path / "data.db"
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT relative_path FROM knowledge_source_assets WHERE source_id = ?",
            (source_id,),
        ).fetchone()
    assert row is not None
    rel = row[0]
    assert rel.startswith(f"knowledge/sources/{source_id}/assets/{source_id}-pic.png")
    assert (tmp_path / rel).is_file()


# ---------------------------------------------------------------------------
# Spec §4.4 Asset 下载
# ---------------------------------------------------------------------------


def test_ki04_asset_download_returns_original_bytes(app_client):
    main = b"# Title\n\n![pic](pic.png)\n"
    pic = _png_bytes(15, 15, color=(10, 20, 30))
    response = _upload_bundle(app_client, main=("b.md", main), assets=[("pic.png", pic)])
    assert response.status_code == 202
    source_id = response.json()["source"]["id"]
    asset_id = app_client.get(
        f"/api/knowledge/sources/{source_id}/assets"
    ).json()["items"][0]["id"]

    download = app_client.get(
        f"/api/knowledge/sources/{source_id}/assets/{asset_id}/content"
    )
    assert download.status_code == 200
    assert download.content == pic
    assert download.headers["content-type"] == "image/png"
    assert "pic.png" in download.headers.get("content-disposition", "")


def test_ki04_asset_download_rejects_mismatched_source(app_client):
    main = b"# Title\n\n![pic](pic.png)\n"
    pic = _png_bytes(10, 10)
    response = _upload_bundle(app_client, main=("b.md", main), assets=[("pic.png", pic)])
    source_a_id = response.json()["source"]["id"]

    # 第二次上传相同内容 → 触发去重
    response_b = _upload_bundle(
        app_client, main=("b.md", main), assets=[("pic.png", pic)]
    )
    assert response_b.status_code == 200  # dedup
    source_b_id = response_b.json()["source"]["id"]
    assert source_a_id == source_b_id

    asset_id = app_client.get(
        f"/api/knowledge/sources/{source_a_id}/assets"
    ).json()["items"][0]["id"]
    # 跨 Source 取 asset → 应 404
    other = app_client.get(f"/api/knowledge/sources/{source_a_id + 999}/assets/{asset_id}/content")
    assert other.status_code == 404


# ---------------------------------------------------------------------------
# Spec §4.4 缺图 / 重复 / 未使用 / 路径穿越 / 媒体伪装 / 像素炸弹
# ---------------------------------------------------------------------------


def test_ki04_bundle_rejects_missing_image_reference(app_client):
    main = b"# Bundle\n\n![missing](no-such.png)\n"
    pic = _png_bytes(8, 8)
    response = _upload_bundle(
        app_client,
        main=("b.md", main),
        assets=[("other.png", pic)],
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "bundle_invalid"


def test_ki04_bundle_rejects_unused_attachment(app_client):
    main = b"# Bundle\n\n![pic](pic.png)\n"
    pic = _png_bytes(8, 8)
    extra = _png_bytes(8, 8, color=(1, 1, 1))
    response = _upload_bundle(
        app_client,
        main=("b.md", main),
        assets=[("pic.png", pic), ("unused.png", extra)],
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "bundle_invalid"


def test_ki04_bundle_rejects_duplicate_logical_name_in_upload(app_client):
    main = b"# Bundle\n\n![pic](pic.png)\n"
    pic = _png_bytes(8, 8)
    pic2 = _png_bytes(8, 8, color=(1, 1, 1))
    response = _upload_bundle(
        app_client,
        main=("b.md", main),
        assets=[("pic.png", pic), ("pic.png", pic2)],
    )
    # FastAPI 多文件同名字段应能传给 service；service 会拒绝。
    assert response.status_code == 400


def test_ki04_bundle_rejects_remote_image_reference(app_client):
    main = "# Bundle\n\n![remote](https://example.com/x.png)\n".encode("utf-8")
    pic = _png_bytes(8, 8)
    response = _upload_bundle(
        app_client,
        main=("b.md", main),
        assets=[("x.png", pic)],
    )
    # 远程引用 logical_name 与本地 pic 不匹配 → bundle_invalid
    assert response.status_code == 400
    assert response.json()["error_code"] == "bundle_invalid"


def test_ki04_bundle_rejects_path_traversal_attachment_name(app_client):
    main = b"# Bundle\n\n![pic](pic.png)\n"
    pic = _png_bytes(8, 8)
    response = _upload_bundle(
        app_client,
        main=("b.md", main),
        assets=[("../escape.png", pic)],
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "bundle_invalid"


def test_ki04_bundle_rejects_media_type_mismatch(app_client):
    main = b"# Bundle\n\n![pic](pic.png)\n"
    # 内容是 JPEG 但命名为 .png
    response = _upload_bundle(
        app_client,
        main=("b.md", main),
        assets=[("pic.png", _jpeg_bytes(10, 10))],
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "bundle_invalid"


def test_ki04_bundle_rejects_corrupt_image(app_client):
    main = b"# Bundle\n\n![pic](pic.png)\n"
    response = _upload_bundle(
        app_client,
        main=("b.md", main),
        assets=[("pic.png", b"not an image")],
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "bundle_invalid"


def test_ki04_bundle_rejects_pixel_bomb(app_client):
    main = b"# Bundle\n\n![pic](pic.png)\n"
    # 构造一张超 40MP 的 PNG（>40M 像素）
    from offerpilot.knowledge.assets import MAX_PIXELS

    side = int((MAX_PIXELS + 1) ** 0.5) + 1
    big = _png_bytes(side, side)
    response = _upload_bundle(
        app_client,
        main=("b.md", main),
        assets=[("pic.png", big)],
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "source_too_large"


def test_ki04_bundle_rejects_oversize_single_asset(app_client):
    main = b"# Bundle\n\n![pic](pic.png)\n"
    big = b"\x00" * (10 * 1024 * 1024 + 1)
    response = _upload_bundle(
        app_client,
        main=("b.md", main),
        assets=[("pic.png", big)],
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Spec §4.4 浏览器安全：不执行 image 附近 HTML，不加载远程资源
# ---------------------------------------------------------------------------


def test_ki04_bundle_html_in_markdown_kept_as_text(app_client):
    # HTML 标签应在 Markdown 解析时保留为纯文本（markdown-it html=false）
    main = b"# Title\n\n<script>alert('xss')</script>\n\n![pic](pic.png)\n"
    pic = _png_bytes(8, 8)
    response = _upload_bundle(app_client, main=("b.md", main), assets=[("pic.png", pic)])
    assert response.status_code == 202
    source_id = response.json()["source"]["id"]
    # 原始字节下载必须包含 <script>（原文保留）
    download = app_client.get(f"/api/knowledge/sources/{source_id}/content")
    assert b"<script>alert('xss')</script>" in download.content


# ---------------------------------------------------------------------------
# Spec §4.4 多图片 Bundle
# ---------------------------------------------------------------------------


def test_ki04_bundle_accepts_multiple_images_mixed_media(app_client):
    main = (
        b"# Bundle\n\n"
        b"![a](a.png)\n\n"
        b"![b](b.jpg)\n\n"
        b"![c](c.webp)\n"
    )
    a = _png_bytes(10, 10)
    b = _jpeg_bytes(12, 12)
    c = _webp_bytes(15, 15)
    response = _upload_bundle(
        app_client,
        main=("b.md", main),
        assets=[("a.png", a), ("b.jpg", b), ("c.webp", c)],
    )
    assert response.status_code == 202, response.text
    source_id = response.json()["source"]["id"]
    assets = app_client.get(f"/api/knowledge/sources/{source_id}/assets").json()["items"]
    assert {item["logical_name"] for item in assets} == {"a.png", "b.jpg", "c.webp"}
    assert {item["media_type"] for item in assets} == {
        "image/png",
        "image/jpeg",
        "image/webp",
    }


def test_ki04_bundle_dedup_returns_existing_source(app_client):
    main = b"# Bundle\n\n![pic](pic.png)\n"
    pic = _png_bytes(10, 10)
    first = _upload_bundle(app_client, main=("b.md", main), assets=[("pic.png", pic)])
    second = _upload_bundle(app_client, main=("b.md", main), assets=[("pic.png", pic)])
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["deduplicated"] is True
    assert first.json()["source"]["id"] == second.json()["source"]["id"]


# ---------------------------------------------------------------------------
# Spec §13 事务失败清理（缺图导致 Preflight 失败）
# ---------------------------------------------------------------------------


def test_ki04_bundle_preflight_failure_leaves_no_orphan_assets(app_client, tmp_path):
    main = b"# Bundle\n\n![pic](pic.png) ![missing](no.png)\n"
    pic = _png_bytes(8, 8)
    response = _upload_bundle(
        app_client,
        main=("b.md", main),
        assets=[("pic.png", pic)],
    )
    assert response.status_code == 400
    # 数据库中应无任何 source / asset 行
    db_path = tmp_path / "data.db"
    with sqlite3.connect(db_path) as conn:
        src_count = conn.execute("SELECT COUNT(*) FROM knowledge_sources").fetchone()[0]
        asset_count = conn.execute(
            "SELECT COUNT(*) FROM knowledge_source_assets"
        ).fetchone()[0]
    assert src_count == 0
    assert asset_count == 0
    # sources/ 目录应为空
    sources_dir = tmp_path / "knowledge" / "sources"
    if sources_dir.exists():
        assert not any(sources_dir.iterdir())
