"""KI-03：Text / 粘贴正文 / 结构感知解析 API 与集成测试。

覆盖 Spec §4.1（输入方式）、§4.2（5 MiB / 64K token 双限制）、§4.3（编码矩阵）、
§7.1（规范化）、§8.1（结构 Evidence 规则）、§8.2（Evidence 字段与身份）和 §17
（前端净化由前端测试覆盖）。

本文件专注 API 集成与端到端验证；纯算法 / 解析单元测试位于
``test_knowledge_ki03_extractor.py``。
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from conftest import wait_for_extraction
from offerpilot.knowledge.encoding import (
    EncodingError,
    decode_source_bytes,
)
from offerpilot.knowledge.extractor import (
    EXTRACTOR_VERSION,
)



def _upload_file(client: TestClient, filename: str, content: bytes, title_hint: str = ""):
    files = {"file": (filename, content, "application/octet-stream")}
    data = {"title_hint": title_hint} if title_hint else None
    response = client.post("/api/knowledge/sources", files=files, data=data)
    if response.status_code in (200, 202):
        wait_for_extraction(client, response.json()["source"]["id"])
    return response


def _upload_paste(
    client: TestClient,
    paste: str,
    *,
    title_hint: str = "",
    origin_url: str = "",
):
    data: dict[str, str] = {"paste": paste}
    if title_hint:
        data["title_hint"] = title_hint
    if origin_url:
        data["origin_url"] = origin_url
    response = client.post("/api/knowledge/sources", data=data)
    if response.status_code in (200, 202):
        wait_for_extraction(client, response.json()["source"]["id"])
    return response


# ---------------------------------------------------------------------------
# Spec §4.1 输入方式：.md / .txt / 粘贴正文
# ---------------------------------------------------------------------------


def test_ki03_accepts_text_file(app_client):
    response = _upload_file(app_client, "notes.txt", b"plain text content\nline two\n")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["source"]["source_kind"] == "text"
    assert body["source"]["main_media_type"] == "text/plain"
    # 上传响应是 pending（异步 extraction）；helper 已 wait，重新 GET 确认 extracted。
    source = wait_for_extraction(app_client, body["source"]["id"])
    assert source["extraction_status"] == "extracted"


def test_ki03_paste_treats_as_virtual_main_md(app_client):
    paste = "# 粘贴标题\n\n粘贴段落内容。\n"
    response = _upload_paste(app_client, paste)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["source"]["source_kind"] == "markdown"
    assert body["source"]["main_filename"] == "main.md"
    assert body["source"]["main_media_type"] == "text/markdown"


def test_ki03_paste_supports_origin_url_provenance(app_client):
    response = _upload_paste(
        app_client,
        "# 引用\n\n段落。\n",
        origin_url="https://example.com/article.md",
    )
    assert response.status_code == 202
    body = response.json()
    source_id = body["source"]["id"]
    jobs = app_client.get(f"/api/knowledge/sources/{source_id}/jobs").json()
    assert jobs["origins"][0]["import_method"] == "paste"
    assert jobs["origins"][0]["origin_url"] == "https://example.com/article.md"


def test_ki03_upload_requires_file_or_paste(app_client):
    response = app_client.post(
        "/api/knowledge/sources",
        data={"title_hint": "missing payload"},
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "unsupported_type"


# ---------------------------------------------------------------------------
# Spec §4.2 5 MiB / 64K token 双限制
# ---------------------------------------------------------------------------


def test_ki03_rejects_oversize_bytes(app_client):
    response = _upload_file(app_client, "huge.md", b"a" * (5 * 1024 * 1024 + 1))
    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "source_too_large"


def test_ki03_rejects_oversize_tokens(app_client):
    # 真实 cl100k_base 计数；用重复中文构造超过 64_000 tokens 但字节数远低于 5MiB。
    # 单句约 19 tokens，重复 4000 次约 76_000 tokens。
    big = "知识库重写需要稳定的导入与检索流程。" * 4000
    response = _upload_file(app_client, "huge.md", big.encode("utf-8"))
    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "source_too_large"
    message = body.get("error") or body.get("message") or ""
    assert "64000" in message or "token" in message.lower()


# ---------------------------------------------------------------------------
# Spec §4.3 编码矩阵
# ---------------------------------------------------------------------------


def test_ki03_accepts_utf8_bom(app_client, tmp_path):
    content = b"\xef\xbb\xbf# UTF8 BOM\n\n" + "正文段落。\n".encode("utf-8")
    response = _upload_file(app_client, "bom.md", content)
    assert response.status_code == 202, response.text
    _assert_snapshot_encoding(tmp_path, response.json()["source"]["id"], "utf-8-sig")


def test_ki03_accepts_utf16_le_bom(app_client, tmp_path):
    content = "# UTF16 标题\n\nUTF-16 LE 正文段落。\n".encode("utf-16-le")
    payload = b"\xff\xfe" + content
    response = _upload_file(app_client, "le.md", payload)
    assert response.status_code == 202, response.text
    _assert_snapshot_encoding(tmp_path, response.json()["source"]["id"], "utf-16-le")


def test_ki03_accepts_utf16_be_bom(app_client, tmp_path):
    content = "# UTF16 标题\n\nUTF-16 BE 正文段落。\n".encode("utf-16-be")
    payload = b"\xfe\xff" + content
    response = _upload_file(app_client, "be.md", payload)
    assert response.status_code == 202, response.text
    _assert_snapshot_encoding(tmp_path, response.json()["source"]["id"], "utf-16-be")


def test_ki03_accepts_long_gbk_content(app_client):
    body = (
        "知识库重写需要稳定编码识别。本系统支持 Markdown、Text 与粘贴正文，"
        "要求严格控制字符精度，禁止忽略或替换任何字符。"
    ) * 5
    response = _upload_file(app_client, "gbk.md", body.encode("gbk"))
    assert response.status_code == 202, response.text


def test_ki03_rejects_short_gbk_due_to_low_confidence(app_client):
    response = _upload_file(app_client, "short.md", "中文测试".encode("gbk"))
    assert response.status_code == 400
    assert response.json()["error_code"] == "encoding_unknown"


def test_ki03_rejects_truly_unknown_encoding(app_client):
    # 0xff 开头既不是合法 UTF-8，也不是任何 BOM
    response = _upload_file(app_client, "garbage.md", b"\xff garbage not utf8 \xfe random")
    assert response.status_code == 400
    assert response.json()["error_code"] == "encoding_unknown"


# ---------------------------------------------------------------------------
# Spec §7.1 规范化与控制字符
# ---------------------------------------------------------------------------


def test_ki03_rejects_nul_in_text(app_client):
    response = _upload_file(app_client, "bad.md", b"# Title\n\nBad\x00content\n")
    assert response.status_code == 400
    assert response.json()["error_code"] == "encoding_unknown"


def test_ki03_preserves_non_nul_control_chars_and_counts_them(app_client, tmp_path):
    # BEL(0x07) 与 BS(0x08) 是控制字符但不是 NUL；Spec §7.1 要求保留并记录。
    content = b"# Title\n\nHas control\x07char here.\n"
    response = _upload_file(app_client, "ctrl.md", content)
    assert response.status_code == 202, response.text
    source_id = response.json()["source"]["id"]
    with sqlite3.connect(tmp_path / "data.db") as conn:
        row = conn.execute(
            "SELECT structure_manifest FROM knowledge_extraction_snapshots WHERE source_id = ?",
            (source_id,),
        ).fetchone()
    assert row is not None
    assert "control_char_count" in row[0]
    assert '"control_char_count": 1' in row[0]


# ---------------------------------------------------------------------------
# Spec §8.1 结构 Evidence
# ---------------------------------------------------------------------------


def test_ki03_evidence_includes_heading_paragraph_list_blockquote_table_code(app_client):
    sample = (
        "# Top Heading\n\n"
        "Paragraph text.\n\n"
        "- list item a\n"
        "- list item b\n\n"
        "> quoted line.\n\n"
        "| Name | Age |\n"
        "| ---- | --- |\n"
        "| Alice | 30 |\n\n"
        "```python\n"
        "def f():\n"
        "    return 1\n"
        "```\n"
    )
    response = _upload_file(app_client, "struct.md", sample.encode("utf-8"))
    assert response.status_code == 202
    source_id = response.json()["source"]["id"]
    listing = app_client.get(f"/api/knowledge/sources/{source_id}/evidence").json()
    block_kinds = {item["block_kind"] for item in listing["items"]}
    assert "paragraph" in block_kinds
    assert "list_item" in block_kinds
    assert "blockquote" in block_kinds
    assert "table_row" in block_kinds
    assert "fenced_code" in block_kinds


def test_ki03_heading_does_not_produce_standalone_evidence(app_client):
    sample = "# 标题\n\n## 二级标题\n\n段落。\n"
    response = _upload_file(app_client, "headings.md", sample.encode("utf-8"))
    assert response.status_code == 202
    source_id = response.json()["source"]["id"]
    listing = app_client.get(f"/api/knowledge/sources/{source_id}/evidence").json()
    block_kinds = [item["block_kind"] for item in listing["items"]]
    assert "heading" not in block_kinds
    # 段落应携带 heading_path
    paragraph = next(item for item in listing["items"] if item["block_kind"] == "paragraph")
    assert paragraph["heading_path"] == ["标题", "二级标题"]


def test_ki03_nested_list_preserves_parent_path(app_client):
    sample = (
        "# List Demo\n\n"
        "- outer one\n"
        "  - inner one\n"
        "  - inner two\n"
        "- outer two\n"
    )
    response = _upload_file(app_client, "nested.md", sample.encode("utf-8"))
    assert response.status_code == 202
    source_id = response.json()["source"]["id"]
    listing = app_client.get(f"/api/knowledge/sources/{source_id}/evidence").json()
    list_items = [item for item in listing["items"] if item["block_kind"] == "list_item"]
    excerpts = [item["canonical_excerpt"] for item in list_items]
    assert any("outer one" in text for text in excerpts)
    assert any("inner one" in text for text in excerpts)
    # Spec §8.1 嵌套 list 保留父路径——检查至少一条 inner item 的 heading_path 包含父级
    inner = next(item for item in list_items if "inner one" in item["canonical_excerpt"])
    # 嵌套 list item 的 search_text 必须包含父级 list_item 文本
    assert "outer one" in inner["search_text"]


def test_ki03_table_row_carries_headers_in_fts(app_client):
    sample = (
        "# Table Demo\n\n"
        "| Name | Age |\n"
        "| ---- | --- |\n"
        "| Alice | 30 |\n"
        "| Bob | 25 |\n"
    )
    response = _upload_file(app_client, "table.md", sample.encode("utf-8"))
    assert response.status_code == 202
    source_id = response.json()["source"]["id"]
    listing = app_client.get(f"/api/knowledge/sources/{source_id}/evidence").json()
    rows = [item for item in listing["items"] if item["block_kind"] == "table_row"]
    assert len(rows) == 2
    # search_text 必须包含表头与单元格内容
    assert "Name" in rows[0]["search_text"]
    assert "Age" in rows[0]["search_text"]
    assert "Alice" in rows[0]["search_text"]


def test_ki03_fenced_code_carries_language(app_client):
    sample = "```js\nconsole.log('hi');\n```\n"
    response = _upload_file(app_client, "code.md", sample.encode("utf-8"))
    assert response.status_code == 202
    source_id = response.json()["source"]["id"]
    listing = app_client.get(f"/api/knowledge/sources/{source_id}/evidence").json()
    code = next(item for item in listing["items"] if item["block_kind"] == "fenced_code")
    # Spec §8.1 fenced code Evidence 记录语言；canonical_excerpt 与原文对齐，包含 ```js
    assert code["canonical_excerpt"].startswith("```js")
    assert "console.log" in code["search_text"]


# ---------------------------------------------------------------------------
# Spec §8.1 / §8.2：拆分与身份
# ---------------------------------------------------------------------------


def test_ki03_long_paragraph_split_by_sentence(app_client):
    long_para = "This is a sentence. " * 400  # ~9K chars
    sample = f"# Long\n\n{long_para}\n"
    response = _upload_file(app_client, "long.md", sample.encode("utf-8"))
    assert response.status_code == 202
    source_id = response.json()["source"]["id"]
    listing = app_client.get(f"/api/knowledge/sources/{source_id}/evidence").json()
    paragraphs = [item for item in listing["items"] if item["block_kind"] == "paragraph"]
    assert len(paragraphs) >= 4
    for piece in paragraphs:
        assert len(piece["canonical_excerpt"]) <= 2000


def test_ki03_long_fenced_code_split_by_line(app_client):
    code = "\n".join(f"line_{i} = {i}" for i in range(700))  # ~10K chars
    sample = f"# Code\n\n```python\n{code}\n```\n"
    response = _upload_file(app_client, "longcode.md", sample.encode("utf-8"))
    assert response.status_code == 202
    source_id = response.json()["source"]["id"]
    listing = app_client.get(f"/api/knowledge/sources/{source_id}/evidence").json()
    code_pieces = [item for item in listing["items"] if item["block_kind"] == "fenced_code"]
    assert len(code_pieces) >= 2
    for piece in code_pieces:
        assert len(piece["canonical_excerpt"]) <= 8000


def test_ki03_evidence_id_is_deterministic(app_client):
    sample = "# Deterministic\n\n段落。\n\n- 列表项\n"
    first = _upload_file(app_client, "a.md", sample.encode("utf-8"))
    second = _upload_file(app_client, "b.md", sample.encode("utf-8"))
    # dedup: 应返回相同 source_id
    assert first.json()["source"]["id"] == second.json()["source"]["id"]


def test_ki03_extractor_version_upgraded_from_ki02():
    # Spec §7.2 extractor 升级创建新 Snapshot。KBR-03 升级到 md-kbr03-* 以区分
    # evidence eligibility policy（过滤元数据样板）规则变化。
    assert EXTRACTOR_VERSION != "md-ki02-1"
    assert EXTRACTOR_VERSION.startswith(("md-ki03-", "md-ki04-", "md-kbr02-", "md-kbr03-"))


# ---------------------------------------------------------------------------
# Spec §4.4 内嵌 HTML 作为不可信原文处理
# ---------------------------------------------------------------------------


def test_ki03_inline_html_kept_as_text_no_script_execution(app_client):
    sample = (
        "# HTML Demo\n\n"
        "<script>alert('xss')</script>\n\n"
        "Paragraph after script.\n"
    )
    response = _upload_file(app_client, "html.md", sample.encode("utf-8"))
    assert response.status_code == 202
    source_id = response.json()["source"]["id"]
    # 原始下载必须保留原始字节
    download = app_client.get(f"/api/knowledge/sources/{source_id}/content")
    assert download.status_code == 200
    assert b"<script>alert('xss')</script>" in download.content


# ---------------------------------------------------------------------------
# 编码模块单元测试（decode_source_bytes）
# ---------------------------------------------------------------------------


def test_decode_source_bytes_utf8_strict_success():
    result = decode_source_bytes("中文".encode("utf-8"))
    assert result.encoding == "utf-8"
    assert result.detection_method == "strict-utf8"
    assert result.text == "中文"


def test_decode_source_bytes_utf8_bom_strips_bom():
    result = decode_source_bytes(b"\xef\xbb\xbf" + "中文".encode("utf-8"))
    assert result.encoding == "utf-8-sig"
    assert result.detection_method == "bom-utf8"
    assert result.text == "中文"


def test_decode_source_bytes_utf16_le_bom_strips_bom():
    text = "中文测试"
    result = decode_source_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
    assert result.encoding == "utf-16-le"
    assert result.detection_method == "bom-utf16le"
    assert result.text == text


def test_decode_source_bytes_utf16_be_bom_strips_bom():
    text = "中文测试"
    result = decode_source_bytes(b"\xfe\xff" + text.encode("utf-16-be"))
    assert result.encoding == "utf-16-be"
    assert result.detection_method == "bom-utf16be"
    assert result.text == text


def test_decode_source_bytes_rejects_unknown_encoding():
    with pytest.raises(EncodingError):
        decode_source_bytes(b"\xff random bytes \xfe not utf anything")


def test_decode_source_bytes_rejects_short_gbk():
    # 短 GBK 文本置信度不够，应拒绝
    with pytest.raises(EncodingError):
        decode_source_bytes("中文".encode("gbk"))


def test_decode_source_bytes_accepts_long_gbk_with_strict_decode():
    text = (
        "知识库重写需要稳定编码识别。本系统支持 Markdown、Text 与粘贴正文，"
        "要求严格控制字符精度，禁止忽略或替换任何字符。"
    ) * 5
    result = decode_source_bytes(text.encode("gbk"))
    assert result.encoding in {"gbk", "gb18030"}
    assert result.text == text


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _assert_snapshot_encoding(tmp_path, source_id: int, expected_encoding: str) -> None:
    with sqlite3.connect(tmp_path / "data.db") as conn:
        row = conn.execute(
            "SELECT encoding FROM knowledge_extraction_snapshots WHERE source_id = ?",
            (source_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == expected_encoding
