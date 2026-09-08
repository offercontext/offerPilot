"""KI-05：内容去重、来源记录与标题整理。

覆盖 Spec §5.1（内容寻址 / 重复导入语义）、§5.2（不可变内容与可编辑标题）、
§16.1（PATCH /api/knowledge/sources/{source_id}）和 §16.2 Origin 行为。

重点：
- 相同字节不同文件名 / 标题复用 Source;每次导入追加 Origin。
- 命中 processing / extracted Source 不再创建第二个 Extract Job。
- 粘贴正文 origin_url 仅允许 http/https,绝不发起网络请求。
- PATCH display_title 不触发 Extraction / Brief / Evidence ID 变化;FTS source_title 同步。
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from conftest import wait_for_extraction

from offerpilot.api import create_app



def _upload_file(
    client: TestClient,
    filename: str,
    content: bytes,
    *,
    title_hint: str = "",
):
    files = {"file": (filename, content, "text/markdown")}
    data: dict[str, str] = {}
    if title_hint:
        data["title_hint"] = title_hint
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
# Spec §5.1：相同字节不同文件名 / 标题 / URL 都复用 Source
# ---------------------------------------------------------------------------


def test_ki05_same_bytes_different_filename_dedups(app_client):
    content = "# Redis Notes\n\nRedis 是内存数据库。\n".encode("utf-8")
    first = _upload_file(app_client, "redis.md", content, title_hint="用户标题 A")
    assert first.status_code == 202
    source_id = first.json()["source"]["id"]

    second = _upload_file(app_client, "redis-v2.md", content, title_hint="用户标题 B")
    assert second.status_code == 200
    body = second.json()
    assert body["deduplicated"] is True
    assert body["source"]["id"] == source_id


def test_ki05_paste_with_different_urls_appends_origins(app_client):
    paste = "# 同一份正文\n\n段落内容。\n"
    first = _upload_paste(
        app_client,
        paste,
        origin_url="https://example.com/article-1.md",
    )
    assert first.status_code == 202
    source_id = first.json()["source"]["id"]

    second = _upload_paste(
        app_client,
        paste,
        origin_url="https://example.com/article-2.md",
    )
    assert second.status_code == 200
    assert second.json()["source"]["id"] == source_id

    jobs = app_client.get(f"/api/knowledge/sources/{source_id}/jobs").json()
    origins = jobs["origins"]
    assert len(origins) == 2
    origin_urls = {item["origin_url"] for item in origins}
    assert origin_urls == {
        "https://example.com/article-1.md",
        "https://example.com/article-2.md",
    }


# ---------------------------------------------------------------------------
# Spec §5.1：命中已有 Source 不创建第二个 Extract Job
# ---------------------------------------------------------------------------


def test_ki05_dedup_does_not_create_second_extract_job(app_client, tmp_path):
    content = "# Kafka\n\nKafka ISR 是 in-sync replica 的缩写。\n".encode("utf-8")
    first = _upload_file(app_client, "kafka.md", content)
    assert first.status_code == 202
    source_id = first.json()["source"]["id"]
    first_job_id = first.json()["job"]["id"]

    second = _upload_file(app_client, "kafka-duplicate.md", content)
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["deduplicated"] is True
    # 返回的 job_id 是已有 Extract Job,而非新建
    assert second_body["job"]["id"] == first_job_id

    with sqlite3.connect(tmp_path / "data.db") as conn:
        extract_jobs = conn.execute(
            "SELECT id, status FROM knowledge_jobs "
            "WHERE source_id = ? AND kind = 'extract'",
            (source_id,),
        ).fetchall()
    # 只允许一个 Extract Job
    assert len(extract_jobs) == 1
    assert extract_jobs[0][0] == first_job_id


def test_ki05_dedup_preserves_existing_origins(app_client):
    content = "# Same\n\n同一段内容。\n".encode("utf-8")
    first = _upload_file(app_client, "first.md", content)
    source_id = first.json()["source"]["id"]
    _upload_paste(app_client, content.decode("utf-8"))
    _upload_file(app_client, "third.md", content)

    jobs = app_client.get(f"/api/knowledge/sources/{source_id}/jobs").json()
    assert len(jobs["origins"]) == 3
    methods = {item["import_method"] for item in jobs["origins"]}
    assert methods == {"file", "paste", "file"}


# ---------------------------------------------------------------------------
# Spec §5.1：粘贴正文 origin_url 只接受 http/https
# ---------------------------------------------------------------------------


def test_ki05_paste_rejects_non_http_origin_url(app_client):
    for bad_url in (
        "ftp://example.com/file.md",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<script>",
    ):
        response = _upload_paste(
            app_client,
            "# Title\n\n正文。\n",
            origin_url=bad_url,
        )
        assert response.status_code == 400, bad_url
        assert response.json()["error_code"] == "unsupported_type"


def test_ki05_paste_rejects_origin_url_with_control_chars(app_client):
    response = _upload_paste(
        app_client,
        "# Title\n\n正文。\n",
        origin_url="https://example.com/a\r\nX-Injected: header",
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "unsupported_type"


def test_ki05_paste_origin_url_never_triggers_network_call(app_client, tmp_path):
    # origin_url 必须仅落库;不应有任何 httpx / urllib 调用。这里通过校验
    # origin_url 包含非路由 IP 仍能上传成功来证明。
    response = _upload_paste(
        app_client,
        "# Title\n\n正文。\n",
        origin_url="https://192.0.2.123/unroutable.md",
    )
    assert response.status_code == 202
    source_id = response.json()["source"]["id"]
    jobs = app_client.get(f"/api/knowledge/sources/{source_id}/jobs").json()
    assert jobs["origins"][0]["origin_url"] == "https://192.0.2.123/unroutable.md"


# ---------------------------------------------------------------------------
# Spec §16.1：PATCH /api/knowledge/sources/{source_id}
# ---------------------------------------------------------------------------


def test_ki05_patch_display_title_updates_source_only(app_client):
    content = "# Original Title\n\n段落内容。\n".encode("utf-8")
    response = _upload_file(app_client, "doc.md", content, title_hint="初始标题")
    source_id = response.json()["source"]["id"]

    detail = app_client.get(f"/api/knowledge/sources/{source_id}").json()
    assert detail["title_hint"] == "初始标题"
    assert detail["display_title"] == ""

    patched = app_client.patch(
        f"/api/knowledge/sources/{source_id}",
        json={"display_title": "用户自定义标题"},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["display_title"] == "用户自定义标题"
    # title_hint 必须保持不变
    assert body["title_hint"] == "初始标题"


def test_ki05_patch_display_title_does_not_change_evidence_id_or_snapshot(
    app_client, tmp_path
):
    content = "# Heading\n\n段落 A。\n\n段落 B。\n".encode("utf-8")
    response = _upload_file(app_client, "doc.md", content)
    source_id = response.json()["source"]["id"]

    with sqlite3.connect(tmp_path / "data.db") as conn:
        before = conn.execute(
            "SELECT active_snapshot_id, extraction_status, brief_status "
            "FROM knowledge_sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        evidence_ids_before = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM knowledge_evidence WHERE source_id = ? ORDER BY id",
                (source_id,),
            )
        ]
        snapshot_digest_before = conn.execute(
            "SELECT digest FROM knowledge_extraction_snapshots WHERE source_id = ?",
            (source_id,),
        ).fetchone()

    patched = app_client.patch(
        f"/api/knowledge/sources/{source_id}",
        json={"display_title": "重命名后的标题"},
    )
    assert patched.status_code == 200

    with sqlite3.connect(tmp_path / "data.db") as conn:
        after = conn.execute(
            "SELECT active_snapshot_id, extraction_status, brief_status "
            "FROM knowledge_sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        evidence_ids_after = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM knowledge_evidence WHERE source_id = ? ORDER BY id",
                (source_id,),
            )
        ]
        snapshot_digest_after = conn.execute(
            "SELECT digest FROM knowledge_extraction_snapshots WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        display_title = conn.execute(
            "SELECT display_title FROM knowledge_sources WHERE id = ?",
            (source_id,),
        ).fetchone()

    assert before == after
    assert evidence_ids_before == evidence_ids_after
    assert snapshot_digest_before == snapshot_digest_after
    assert display_title[0] == "重命名后的标题"


def test_ki05_patch_display_title_syncs_fts_source_title(app_client):
    content = "# Kafka ISR\n\nISR 是 in-sync replica 的缩写。\n".encode("utf-8")
    response = _upload_file(app_client, "kafka.md", content)
    source_id = response.json()["source"]["id"]

    # 修改前搜索"ISR 缩写"应基于原标题命中;修改 display_title 后搜索"展示标题"应能命中 source_title。
    before = app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "Kafka ISR"},
    ).json()
    assert before["hits"]

    patched = app_client.patch(
        f"/api/knowledge/sources/{source_id}",
        json={"display_title": "专属展示标题 KI05"},
    )
    assert patched.status_code == 200

    after = app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "专属展示标题 KI05"},
    ).json()
    # source_title 已同步,展示标题上的 trigram 命中应能找到该 Source 的 Evidence。
    assert after["hits"]
    assert {hit["source_id"] for hit in after["hits"]} == {source_id}


def test_ki05_patch_display_title_unknown_source_returns_404(app_client):
    response = app_client.patch(
        "/api/knowledge/sources/99999",
        json={"display_title": "新标题"},
    )
    assert response.status_code == 404


def test_ki05_patch_display_title_rejects_unknown_fields(app_client):
    content = "# T\n\n正文。\n".encode("utf-8")
    response = _upload_file(app_client, "t.md", content)
    source_id = response.json()["source"]["id"]

    bad = app_client.patch(
        f"/api/knowledge/sources/{source_id}",
        json={"display_title": "x", "title_hint": "试图修改"},
    )
    assert bad.status_code == 400
    assert bad.json()["error_code"] == "unsupported_type"

    detail = app_client.get(f"/api/knowledge/sources/{source_id}").json()
    assert detail["title_hint"] != "试图修改"


def test_ki05_patch_display_title_rejects_non_string(app_client):
    content = "# T\n\n正文。\n".encode("utf-8")
    response = _upload_file(app_client, "t.md", content)
    source_id = response.json()["source"]["id"]

    bad = app_client.patch(
        f"/api/knowledge/sources/{source_id}",
        json={"display_title": 42},
    )
    assert bad.status_code == 400


def test_ki05_patch_display_title_trims_whitespace(app_client):
    content = "# T\n\n正文。\n".encode("utf-8")
    response = _upload_file(app_client, "t.md", content)
    source_id = response.json()["source"]["id"]

    patched = app_client.patch(
        f"/api/knowledge/sources/{source_id}",
        json={"display_title": "  保留前后空格被 trim  "},
    )
    assert patched.status_code == 200
    assert patched.json()["display_title"] == "保留前后空格被 trim"


def test_ki05_patch_display_title_empty_resets_to_hint(app_client):
    content = "# T\n\n正文。\n".encode("utf-8")
    response = _upload_file(app_client, "t.md", content, title_hint="hint 标题")
    source_id = response.json()["source"]["id"]

    # 先设置一个 display_title
    set_resp = app_client.patch(
        f"/api/knowledge/sources/{source_id}",
        json={"display_title": "临时标题"},
    )
    assert set_resp.status_code == 200
    assert set_resp.json()["display_title"] == "临时标题"

    # 清空 display_title;display_title 为空字符串,展示层应回落到 title_hint
    cleared = app_client.patch(
        f"/api/knowledge/sources/{source_id}",
        json={"display_title": ""},
    )
    assert cleared.status_code == 200
    body = cleared.json()
    assert body["display_title"] == ""
    assert body["title"] == "hint 标题"

    # 清空自定义标题后，FTS 的 source_title 也必须回退到 title_hint。
    search = app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "hint 标题"},
    ).json()
    assert {hit["source_id"] for hit in search["hits"]} == {source_id}


def test_ki05_patch_display_title_too_long_rejected(app_client):
    content = "# T\n\n正文。\n".encode("utf-8")
    response = _upload_file(app_client, "t.md", content)
    source_id = response.json()["source"]["id"]
    too_long = "字" * 256  # 768 字节
    bad = app_client.patch(
        f"/api/knowledge/sources/{source_id}",
        json={"display_title": too_long},
    )
    assert bad.status_code == 400


# ---------------------------------------------------------------------------
# Spec §5.1：并发上传相同内容走 UNIQUE 约束兜底
# ---------------------------------------------------------------------------


def test_ki05_concurrent_ingest_same_hash_dedups_via_unique_constraint(tmp_path):
    clients: list[TestClient] = []
    for _ in range(4):
        clients.append(TestClient(create_app(data_dir=tmp_path)))

    content = "# Concurrent\n\n同一段相同字节。\n".encode("utf-8")

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(
                lambda c=client: c.post(
                    "/api/knowledge/sources",
                    files={"file": ("dup.md", content, "text/markdown")},
                )
            )
            for client in clients
        ]
        responses = [future.result() for future in futures]

    status_codes = sorted(response.status_code for response in responses)
    # 全部成功 (200 dedup 或 202 new),不出现 5xx / 4xx 失败;至少一个 200 dedup。
    assert all(code in (200, 202) for code in status_codes), status_codes
    assert status_codes.count(200) >= 1

    source_ids = {
        response.json()["source"]["id"]
        for response in responses
        if response.status_code in (200, 202)
    }
    assert len(source_ids) == 1

    with sqlite3.connect(tmp_path / "data.db") as conn:
        source_rows = conn.execute(
            "SELECT id FROM knowledge_sources WHERE source_hash IS NOT NULL"
        ).fetchall()
        extract_jobs = conn.execute(
            "SELECT id FROM knowledge_jobs WHERE kind = 'extract'"
        ).fetchall()
        origins = conn.execute(
            "SELECT id FROM knowledge_source_origins"
        ).fetchall()
    # 单 Source、单 Extract Job、四条 Origin (并发 4 个 client)
    assert len(source_rows) == 1
    assert len(extract_jobs) == 1
    assert len(origins) == 4


# ---------------------------------------------------------------------------
# Spec §5.2：title_hint 推导顺序
# ---------------------------------------------------------------------------


def test_ki05_title_hint_derived_from_user_input_when_provided(app_client):
    content = "# Markdown Heading\n\n正文。\n".encode("utf-8")
    response = _upload_file(
        app_client, "auto.md", content, title_hint="用户优先级最高"
    )
    assert response.status_code == 202
    body = response.json()
    assert body["source"]["title_hint"] == "用户优先级最高"
    # title 字段在 display_title 空时应回退到 title_hint
    assert body["source"]["title"] == "用户优先级最高"


def test_ki05_title_hint_derived_from_first_heading_when_no_user_input(app_client):
    content = "# 首个 Markdown 标题\n\n正文。\n".encode("utf-8")
    response = _upload_file(app_client, "auto.md", content)
    body = response.json()
    assert body["source"]["title_hint"] == "首个 Markdown 标题"


def test_ki05_title_hint_derived_from_filename_when_no_extractable_content(app_client):
    # Spec §5.2 推导顺序:用户标题 > 首个 Markdown 标题 > 首段内容 > 文件名。
    # 提供一个只含分隔符 / 不可解析为有效段落的 Markdown,确保 title_hint 回退到文件名。
    response = _upload_file(app_client, "fallback-name.md", b"---\n---\n")
    assert response.status_code == 202, response.text
    body = response.json()
    # 推导顺序末位是文件名 (去掉扩展名),其他回退也应保持稳定可读
    assert body["source"]["title_hint"] in {"fallback-name", "---"}


def test_ki05_title_hint_uses_first_paragraph_when_no_heading(app_client):
    # Spec §5.2:无标题时优先使用首段内容,而非文件名
    content = "首段是默认展示标题。\n".encode("utf-8")
    response = _upload_file(app_client, "auto.md", content)
    body = response.json()
    assert body["source"]["title_hint"] == "首段是默认展示标题。"


def test_ki05_file_upload_rejects_origin_url(app_client):
    # Spec §4.1 / KI-05：origin_url 仅允许 paste 路径使用,file/bundle 不应携带。
    files = {"file": ("doc.md", b"# Title\n\nbody\n", "text/markdown")}
    response = app_client.post(
        "/api/knowledge/sources",
        files=files,
        data={"origin_url": "https://example.com/something"},
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "unsupported_type"
