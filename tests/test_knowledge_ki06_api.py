"""KI-06：Source 归档与永久删除。

覆盖 Spec §5.3（归档）、§5.4（永久删除）、§13（lifecycle=deleting）、§16.1
（/archive、/unarchive、DELETE 路由、?include_archived=true 查询）。

重点：
- 归档/取消归档只改 lifecycle,不删文件、Evidence、Brief 历史、Job 历史。
- 默认 Source 列表与普通 Evidence 搜索排除 archived;显式筛选可以查看归档资料。
- 永久删除是异步危险操作：返回 Delete Job,Source 进入 deleting,取消未完成 Job,
  目录移到 quarantine,单事务清理 FTS/Evidence/Snapshot/Asset/Origin/Job/Source。
- 删除日志只保留 Source ID、时间、结果;不保留标题、正文、URL、路径。
- 删除不保留 source_hash 墓碑;重新上传相同内容创建新 ID 和新 Job。
- 删除处理中的 Source:取消迟到结果,再次上传相同内容得到新 Source。
"""

from __future__ import annotations

import io
import sqlite3

from fastapi.testclient import TestClient
from conftest import wait_for_extraction, wait_for_source_deleted
from PIL import Image

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

def _png_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _upload_bundle(
    client: TestClient,
    *,
    main: tuple[str, bytes],
    assets: list[tuple[str, bytes]],
):
    files: list[tuple[str, tuple[str, bytes]]] = [("file", main)]
    for name, content in assets:
        files.append(("files", (name, content)))
    response = client.post("/api/knowledge/sources", files=files)  # type: ignore[arg-type]
    if response.status_code in (200, 202):
        wait_for_extraction(client, response.json()["source"]["id"])
    return response

# ---------------------------------------------------------------------------
# Spec §5.3：归档与取消归档
# ---------------------------------------------------------------------------


def test_ki06_archive_changes_lifecycle_only(app_client, tmp_path):
    content = "# Title\n\n段落内容。\n".encode("utf-8")
    upload = _upload_file(app_client, "doc.md", content)
    source_id = upload.json()["source"]["id"]

    archive = app_client.post(f"/api/knowledge/sources/{source_id}/archive")
    assert archive.status_code == 200
    body = archive.json()
    assert body["lifecycle"] == "archived"
    assert body["archived_at"]
    # Extraction/Brief 状态保持不变（KI-09 之后 extracted Source 的 brief_status 至少
    # 进入 pending；测试环境无 96K Provider 会显示 provider_unavailable block reason）。
    assert body["extraction_status"] == "extracted"
    assert body["brief_status"] in ("not_started", "pending", "processing")


def test_ki06_archive_does_not_delete_files_or_evidence(app_client, tmp_path):
    content = "# Title\n\n段落内容。\n".encode("utf-8")
    upload = _upload_file(app_client, "doc.md", content)
    source_id = upload.json()["source"]["id"]

    app_client.post(f"/api/knowledge/sources/{source_id}/archive")

    # 原文与 Evidence 均未被删除
    original = app_client.get(f"/api/knowledge/sources/{source_id}/content")
    assert original.status_code == 200
    assert original.content == content

    evidence = app_client.get(f"/api/knowledge/sources/{source_id}/evidence").json()
    assert evidence["items"]

    # 数据库 Snapshot/Evidence/Job 仍存在
    with sqlite3.connect(tmp_path / "data.db") as conn:
        snapshots = conn.execute(
            "SELECT COUNT(*) FROM knowledge_extraction_snapshots WHERE source_id = ?",
            (source_id,),
        ).fetchone()[0]
        evidence_rows = conn.execute(
            "SELECT COUNT(*) FROM knowledge_evidence WHERE source_id = ?",
            (source_id,),
        ).fetchone()[0]
        jobs = conn.execute(
            "SELECT COUNT(*) FROM knowledge_jobs WHERE source_id = ?",
            (source_id,),
        ).fetchone()[0]
    assert snapshots == 1
    assert evidence_rows > 0
    assert jobs >= 1

    # 文件系统原件仍存在
    source_dir = tmp_path / "knowledge" / "sources" / str(source_id)
    assert source_dir.is_dir()


def test_ki06_unarchive_restores_active_lifecycle(app_client, tmp_path):
    content = "# Title\n\n段落。\n".encode("utf-8")
    upload = _upload_file(app_client, "doc.md", content)
    source_id = upload.json()["source"]["id"]

    app_client.post(f"/api/knowledge/sources/{source_id}/archive")
    unarchive = app_client.post(f"/api/knowledge/sources/{source_id}/unarchive")
    assert unarchive.status_code == 200
    body = unarchive.json()
    assert body["lifecycle"] == "active"
    assert body["archived_at"] is None


def test_ki06_default_list_excludes_archived(app_client, tmp_path):
    a = _upload_file(app_client, "a.md", "# A\n\n正文 A。\n".encode("utf-8"))
    b = _upload_file(app_client, "b.md", "# B\n\n正文 B。\n".encode("utf-8"))
    archived_id = a.json()["source"]["id"]
    active_id = b.json()["source"]["id"]
    app_client.post(f"/api/knowledge/sources/{archived_id}/archive")

    default_list = app_client.get("/api/knowledge/sources").json()
    ids = {item["id"] for item in default_list}
    assert active_id in ids
    assert archived_id not in ids

    archived_list = app_client.get(
        "/api/knowledge/sources", params={"include_archived": "true"}
    ).json()
    archived_ids = {item["id"] for item in archived_list}
    assert archived_id in archived_ids
    assert active_id in archived_ids


def test_ki06_default_search_excludes_archived(app_client, tmp_path):
    content = "# Kafka ISR\n\nISR 是 in-sync replica 缩写。\n".encode("utf-8")
    upload = _upload_file(app_client, "kafka.md", content)
    source_id = upload.json()["source"]["id"]

    before = app_client.post(
        "/api/knowledge/evidence/search", json={"query": "Kafka ISR"}
    ).json()
    assert before["hits"]

    app_client.post(f"/api/knowledge/sources/{source_id}/archive")
    after_default = app_client.post(
        "/api/knowledge/evidence/search", json={"query": "Kafka ISR"}
    ).json()
    assert after_default["hits"] == []

    after_include = app_client.post(
        "/api/knowledge/evidence/search",
        json={"query": "Kafka ISR", "include_archived": True},
    ).json()
    assert after_include["hits"]


def test_ki06_archived_source_detail_still_accessible(app_client, tmp_path):
    content = "# Title\n\n段落。\n".encode("utf-8")
    upload = _upload_file(app_client, "doc.md", content)
    source_id = upload.json()["source"]["id"]
    app_client.post(f"/api/knowledge/sources/{source_id}/archive")

    detail = app_client.get(f"/api/knowledge/sources/{source_id}")
    assert detail.status_code == 200
    assert detail.json()["lifecycle"] == "archived"


def test_ki06_archive_unknown_source_returns_404(app_client, tmp_path):
    response = app_client.post("/api/knowledge/sources/99999/archive")
    assert response.status_code == 404


def test_ki06_unarchive_unknown_source_returns_404(app_client, tmp_path):
    response = app_client.post("/api/knowledge/sources/99999/unarchive")
    assert response.status_code == 404


def test_ki06_archive_is_idempotent(app_client, tmp_path):
    content = "# Title\n\n正文。\n".encode("utf-8")
    upload = _upload_file(app_client, "doc.md", content)
    source_id = upload.json()["source"]["id"]
    first = app_client.post(f"/api/knowledge/sources/{source_id}/archive")
    second = app_client.post(f"/api/knowledge/sources/{source_id}/archive")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["lifecycle"] == "archived"
    assert second.json()["lifecycle"] == "archived"


# ---------------------------------------------------------------------------
# Spec §5.4：永久删除 — DB / FTS / 文件系统清理
# ---------------------------------------------------------------------------


def test_ki06_delete_returns_202_with_delete_job(app_client, tmp_path):
    content = "# Title\n\n正文。\n".encode("utf-8")
    upload = _upload_file(app_client, "doc.md", content)
    source_id = upload.json()["source"]["id"]

    response = app_client.delete(f"/api/knowledge/sources/{source_id}")
    assert response.status_code == 202
    assert wait_for_source_deleted(app_client, source_id, db_path=tmp_path / "data.db")
    body = response.json()
    assert body["source_id"] == source_id
    assert body["job"]["kind"] == "delete"
    assert body["job"]["queue"] == "extraction"
    assert body["job"]["status"] == "pending"


def test_ki06_delete_removes_all_database_rows(app_client, tmp_path):
    content = "# Title\n\n段落。\n".encode("utf-8")
    upload = _upload_file(app_client, "doc.md", content)
    source_id = upload.json()["source"]["id"]

    response = app_client.delete(f"/api/knowledge/sources/{source_id}")
    assert response.status_code == 202
    assert wait_for_source_deleted(app_client, source_id, db_path=tmp_path / "data.db")

    with sqlite3.connect(tmp_path / "data.db") as conn:
        source = conn.execute(
            "SELECT id FROM knowledge_sources WHERE id = ?", (source_id,)
        ).fetchall()
        origins = conn.execute(
            "SELECT id FROM knowledge_source_origins WHERE source_id = ?",
            (source_id,),
        ).fetchall()
        snapshots = conn.execute(
            "SELECT id FROM knowledge_extraction_snapshots WHERE source_id = ?",
            (source_id,),
        ).fetchall()
        evidence = conn.execute(
            "SELECT id FROM knowledge_evidence WHERE source_id = ?", (source_id,)
        ).fetchall()
        fts = conn.execute(
            "SELECT evidence_id FROM knowledge_evidence_fts WHERE source_id = ?",
            (source_id,),
        ).fetchall()
        jobs = conn.execute(
            "SELECT id FROM knowledge_jobs WHERE source_id = ?", (source_id,)
        ).fetchall()
    assert source == []
    assert origins == []
    assert snapshots == []
    assert evidence == []
    assert fts == []
    # 删除 Job 自身也应被清理,Spec §5.4 "Source 行不存在"
    assert jobs == []


def test_ki06_delete_removes_files_and_quarantine(app_client, tmp_path):
    content = "# Title\n\n正文。\n".encode("utf-8")
    upload = _upload_file(app_client, "doc.md", content)
    source_id = upload.json()["source"]["id"]
    source_dir = tmp_path / "knowledge" / "sources" / str(source_id)
    assert source_dir.is_dir()

    response = app_client.delete(f"/api/knowledge/sources/{source_id}")
    assert response.status_code == 202
    assert wait_for_source_deleted(app_client, source_id, db_path=tmp_path / "data.db")

    # 正式目录已不存在
    assert not source_dir.exists()
    # quarantine 在删除完成后已被清理
    quarantine_dir = tmp_path / "knowledge" / "quarantine" / str(source_id)
    assert not quarantine_dir.exists()


def test_ki06_delete_logs_only_id_time_result(app_client, tmp_path):
    content = "# Sensitive Title\n\n敏感正文 https://example.com/private\n".encode("utf-8")
    upload = _upload_file(app_client, "doc.md", content, title_hint="敏感标题")
    source_id = upload.json()["source"]["id"]

    response = app_client.delete(f"/api/knowledge/sources/{source_id}")
    assert response.status_code == 202
    assert wait_for_source_deleted(app_client, source_id, db_path=tmp_path / "data.db")

    with sqlite3.connect(tmp_path / "data.db") as conn:
        log_rows = conn.execute(
            "SELECT source_id, action, result, error_code FROM knowledge_logs"
        ).fetchall()
        log_meta = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='knowledge_logs'"
        ).fetchone()
    assert len(log_rows) == 1
    logged_source_id, action, result, error_code = log_rows[0]
    assert logged_source_id == source_id
    assert action == "source_deleted"
    assert result == "succeeded"
    assert error_code == ""

    # 表结构不允许保存标题/正文/URL/路径
    schema = log_meta[0]
    forbidden_columns = ["title", "display_title", "main_filename", "origin_url", "path"]
    for col in forbidden_columns:
        assert col not in schema.lower(), (
            f"knowledge_logs 不得保存 {col},实际 schema: {schema}"
        )


def test_ki06_delete_does_not_keep_source_hash_tombstone(app_client, tmp_path):
    content = "# Title\n\n正文。\n".encode("utf-8")
    upload = _upload_file(app_client, "doc.md", content)
    source_id_before = upload.json()["source"]["id"]

    # 删除
    delete_resp = app_client.delete(f"/api/knowledge/sources/{source_id_before}")
    assert delete_resp.status_code == 202
    assert wait_for_source_deleted(app_client, source_id_before, db_path=tmp_path / "data.db")

    # 再次上传相同内容 → 应得到新 source_id 与新 Extract Job
    reupload = _upload_file(app_client, "doc-again.md", content)
    assert reupload.status_code == 202
    body = reupload.json()
    assert body["deduplicated"] is False
    assert body["source"]["id"] != source_id_before
    assert body["job"]["status"] == "pending"


def test_ki06_delete_unknown_source_returns_404(app_client, tmp_path):
    response = app_client.delete("/api/knowledge/sources/99999")
    assert response.status_code == 404


def test_ki06_delete_already_archived_source_succeeds(app_client, tmp_path):
    content = "# Title\n\n正文。\n".encode("utf-8")
    upload = _upload_file(app_client, "doc.md", content)
    source_id = upload.json()["source"]["id"]
    app_client.post(f"/api/knowledge/sources/{source_id}/archive")

    delete = app_client.delete(f"/api/knowledge/sources/{source_id}")
    assert delete.status_code == 202
    assert wait_for_source_deleted(app_client, source_id, db_path=tmp_path / "data.db")

    detail = app_client.get(f"/api/knowledge/sources/{source_id}")
    assert detail.status_code == 404


def test_ki06_delete_bundle_removes_assets_and_evidence(app_client, tmp_path):
    main = b"# Bundle\n\n![pic](pic.png)\n"
    pic = _png_bytes(15, 15)
    upload = _upload_bundle(
        app_client, main=("b.md", main), assets=[("pic.png", pic)]
    )
    assert upload.status_code == 202
    source_id = upload.json()["source"]["id"]

    asset_dir = tmp_path / "knowledge" / "sources" / str(source_id) / "assets"
    assert asset_dir.is_dir()

    response = app_client.delete(f"/api/knowledge/sources/{source_id}")
    assert response.status_code == 202
    assert wait_for_source_deleted(app_client, source_id, db_path=tmp_path / "data.db")

    with sqlite3.connect(tmp_path / "data.db") as conn:
        assets = conn.execute(
            "SELECT id FROM knowledge_source_assets WHERE source_id = ?",
            (source_id,),
        ).fetchall()
        evidence = conn.execute(
            "SELECT id FROM knowledge_evidence WHERE source_id = ?", (source_id,)
        ).fetchall()
    assert assets == []
    assert evidence == []
    # assets 子目录与 source_dir 一并清理
    assert not asset_dir.exists()


# ---------------------------------------------------------------------------
# Spec §5.4 / §6：删除会拒绝迟到结果;再次上传得到新 Source
# ---------------------------------------------------------------------------


def test_ki06_delete_idempotent_returns_prior_job_or_404(app_client, tmp_path):
    content = "# Title\n\n正文。\n".encode("utf-8")
    upload = _upload_file(app_client, "doc.md", content)
    source_id = upload.json()["source"]["id"]

    first = app_client.delete(f"/api/knowledge/sources/{source_id}")
    assert first.status_code == 202
    assert wait_for_source_deleted(app_client, source_id, db_path=tmp_path / "data.db")
    # 已删除 → 再次 delete 应 404,不返回原 Job
    second = app_client.delete(f"/api/knowledge/sources/{source_id}")
    assert second.status_code == 404


def test_ki06_ingest_after_delete_creates_new_source_and_job(app_client, tmp_path):
    content = "# Same\n\n相同内容。\n".encode("utf-8")
    first = _upload_file(app_client, "doc.md", content)
    source_id = first.json()["source"]["id"]

    delete = app_client.delete(f"/api/knowledge/sources/{source_id}")
    assert delete.status_code == 202
    assert wait_for_source_deleted(app_client, source_id, db_path=tmp_path / "data.db")

    second = _upload_file(app_client, "doc-again.md", content)
    assert second.status_code == 202
    body = second.json()
    assert body["deduplicated"] is False
    new_source_id = body["source"]["id"]
    assert new_source_id != source_id

    with sqlite3.connect(tmp_path / "data.db") as conn:
        extract_jobs = conn.execute(
            "SELECT id FROM knowledge_jobs WHERE kind='extract' AND source_id=?",
            (new_source_id,),
        ).fetchall()
    assert len(extract_jobs) == 1


# ---------------------------------------------------------------------------
# Spec §5.4：删除日志只保留 ID + 时间 + 结果
# ---------------------------------------------------------------------------


def test_ki06_logs_table_does_not_persist_title_url_or_path(app_client, tmp_path):
    content = "# Unique Title\n\nhttps://unique.example.com/path\n".encode("utf-8")
    upload = _upload_file(
        app_client,
        "unique-doc.md",
        content,
        title_hint="独特标题",
    )
    source_id = upload.json()["source"]["id"]

    app_client.delete(f"/api/knowledge/sources/{source_id}")

    db_path = tmp_path / "data.db"
    with sqlite3.connect(db_path) as conn:
        all_log_data = conn.execute("SELECT * FROM knowledge_logs").fetchall()
    # 字段值不允许出现标题/URL/路径
    flat = " ".join(str(value) for row in all_log_data for value in row)
    assert "独特标题" not in flat
    assert "Unique Title" not in flat
    assert "unique.example.com" not in flat
    assert "unique-doc.md" not in flat
    assert "knowledge/sources" not in flat


# ---------------------------------------------------------------------------
# Spec §5.4：Interview Knowledge capture tables are part of the current schema
# ---------------------------------------------------------------------------


def test_ki06_capture_note_tables_are_created_by_knowledge_capture(app_client, tmp_path):
    init_database_call = app_client.get("/api/health")
    assert init_database_call.status_code == 200

    with sqlite3.connect(tmp_path / "data.db") as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            ).fetchall()
        }
    assert {
        "knowledge_notes",
        "knowledge_note_versions",
        "knowledge_note_evidence",
    } <= tables


# ---------------------------------------------------------------------------
# Spec §5.4：删除流程 quarantine 协调 — quarantine 在删除成功后被清理
# ---------------------------------------------------------------------------


def test_ki06_quarantine_dir_cleanup_after_successful_delete(app_client, tmp_path):
    content = "# Title\n\n正文。\n".encode("utf-8")
    upload = _upload_file(app_client, "doc.md", content)
    source_id = upload.json()["source"]["id"]

    app_client.delete(f"/api/knowledge/sources/{source_id}")

    quarantine_root = tmp_path / "knowledge" / "quarantine"
    if quarantine_root.exists():
        leftovers = list(quarantine_root.iterdir())
        assert leftovers == [], f"quarantine 未清空: {leftovers}"


def test_ki06_sources_root_dir_preserved_after_delete(app_client, tmp_path):
    # 删除不应清空整个 sources/ 根目录,只清理自己子目录。
    a = _upload_file(app_client, "a.md", "# A\n\nA 正文。\n".encode("utf-8"))
    b = _upload_file(app_client, "b.md", "# B\n\nB 正文。\n".encode("utf-8"))
    a_id = a.json()["source"]["id"]
    b_id = b.json()["source"]["id"]

    app_client.delete(f"/api/knowledge/sources/{a_id}")
    assert wait_for_source_deleted(app_client, a_id, db_path=tmp_path / "data.db")

    sources_root = tmp_path / "knowledge" / "sources"
    assert sources_root.is_dir()
    remaining = {child.name for child in sources_root.iterdir()}
    assert str(b_id) in remaining
    assert str(a_id) not in remaining


# ---------------------------------------------------------------------------
# Spec §6 / §12：启动恢复完成异常中断的删除
# ---------------------------------------------------------------------------


def test_ki06_startup_recovery_completes_deleting_source(tmp_path):
    """Spec §6 / §12：``begin_delete`` 标记 lifecycle=deleting 后崩溃,重启时
    `_recover_knowledge_deletions` 必须完成事务清理 + quarantine 清理。
    """
    from offerpilot.db import init_database

    content = "# Recovery\n\n正文。\n".encode("utf-8")
    with TestClient(create_app(data_dir=tmp_path)) as client:
        upload = _upload_file(client, "doc.md", content)
        source_id = upload.json()["source"]["id"]

    # 直接走 begin_delete,但不调用 complete_purge,模拟崩溃
    with sqlite3.connect(tmp_path / "data.db") as conn:
        conn.execute(
            "UPDATE knowledge_sources SET lifecycle='deleting' WHERE id=?",
            (source_id,),
        )
        conn.commit()
    # quarantine 残留目录
    quarantine_dir = tmp_path / "knowledge" / "quarantine" / str(source_id)
    quarantine_dir.mkdir(parents=True)
    (quarantine_dir / "leftover.md").write_bytes(b"leftover")

    # 重启 → init_database 应完成清理
    init_database(tmp_path / "data.db")

    with sqlite3.connect(tmp_path / "data.db") as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM knowledge_sources WHERE id=?", (source_id,)
        ).fetchone()
        logs = conn.execute(
            "SELECT action, result FROM knowledge_logs WHERE source_id=?",
            (source_id,),
        ).fetchall()
    assert row[0] == 0
    assert ("source_deleted", "succeeded") in logs
    assert not quarantine_dir.exists()


def test_ki06_startup_recovery_clears_orphan_quarantine(tmp_path):
    """Spec §6：commit 后崩溃 quarantine 残留(Source 行已不存在)→ 启动时清理。"""
    from offerpilot.db import init_database

    init_database(tmp_path / "data.db")
    quarantine_root = tmp_path / "knowledge" / "quarantine"
    quarantine_root.mkdir(parents=True)
    orphan_dir = quarantine_root / "9999"
    orphan_dir.mkdir()
    (orphan_dir / "leftover.md").write_bytes(b"leftover")

    init_database(tmp_path / "data.db")

    assert not orphan_dir.exists()


# ---------------------------------------------------------------------------
# Spec §5.4：删除处理中 Source — ingest 走 dedup 必须创建新 Source
# ---------------------------------------------------------------------------


def test_ki06_deleting_source_not_visible_in_default_list(app_client, tmp_path):
    content = "# Title\n\n正文。\n".encode("utf-8")
    upload = _upload_file(app_client, "doc.md", content)
    source_id = upload.json()["source"]["id"]

    # 走 API delete
    delete = app_client.delete(f"/api/knowledge/sources/{source_id}")
    assert delete.status_code == 202
    assert wait_for_source_deleted(app_client, source_id, db_path=tmp_path / "data.db")

    # 列表默认排除已删除(且 lifecycle=deleting 在事务完成后行已不存在)
    listing = app_client.get("/api/knowledge/sources").json()
    assert all(item["id"] != source_id for item in listing)


def test_ki06_search_does_not_match_deleting_source(app_client, tmp_path):
    """Spec §5.4：deleting Source 不应出现在搜索结果(正常路径中 lifecycle=deleting
    时间窗口很短,但 search_evidence 仍需排除)。"""
    content = "# Searchable\n\n可索引内容。\n".encode("utf-8")
    upload = _upload_file(app_client, "doc.md", content)
    source_id = upload.json()["source"]["id"]

    app_client.delete(f"/api/knowledge/sources/{source_id}")

    search = app_client.post(
        "/api/knowledge/evidence/search", json={"query": "可索引内容"}
    ).json()
    assert all(hit["source_id"] != source_id for hit in search["hits"])


# ---------------------------------------------------------------------------
# Spec §5.4：begin_delete 在 repository 层应取消 pending/running extract/brief Job
# ---------------------------------------------------------------------------


def test_ki06_begin_delete_cancels_active_extract_jobs(tmp_path):
    """Spec §5.4 / KI-06 验收点 7:begin_delete 必须取消未完成 Extract / Brief Job。

    KI-06 时点 Extraction 同步触发,实际并发 worker 取消路径在 KI-07 范围。本测试
    直接在 repository 层验证 ``begin_delete`` 的事务副作用:任何 ``pending`` 或
    ``running`` Extract / Brief Job 都被标记 ``canceled=True, status=canceled,
    stage=canceled_by_delete``,并写入 ``kind=delete, status=running`` Delete Job。
    """
    from offerpilot.api import create_app as _create_app
    from offerpilot.db import session_factory_for_data_dir
    from offerpilot.knowledge.repository import KnowledgeRepository
    from offerpilot.models import KnowledgeJob, KnowledgeSource

    with TestClient(_create_app(data_dir=tmp_path)) as client:
        upload = _upload_file(client, "doc.md", "# Title\n\n正文。\n".encode("utf-8"))
        source_id = upload.json()["source"]["id"]

    # 直接构造一个"假装"还在 running 的 Extract Job 来模拟 KI-07 异步 worker 仍未提交。
    session_factory = session_factory_for_data_dir(tmp_path)
    with session_factory() as session:
        fake_running = KnowledgeJob(
            kind="extract",
            queue="extraction",
            source_id=source_id,
            stage="extracting",
            status="running",
        )
        session.add(fake_running)
        session.commit()
        job_id_before = fake_running.id

    repository = KnowledgeRepository(session_factory)
    begin_result = repository.begin_delete(source_id)
    assert begin_result is not None
    _, delete_job_id = begin_result

    with session_factory() as session:
        source = session.get(KnowledgeSource, source_id)
        assert source is not None
        assert source.lifecycle == "deleting"

        canceled_job = session.get(KnowledgeJob, job_id_before)
        assert canceled_job is not None
        assert canceled_job.status == "canceled"
        assert canceled_job.canceled is True
        assert canceled_job.stage == "canceled_by_delete"

        delete_job = session.get(KnowledgeJob, delete_job_id)
        assert delete_job is not None
        assert delete_job.kind == "delete"
        assert delete_job.queue == "extraction"
        assert delete_job.status == "pending"
        assert delete_job.source_id == source_id


def test_ki06_begin_delete_rejects_already_deleting_source(tmp_path):
    """Spec §5.4：``begin_delete`` 重复调用必须返回 None,避免重复扣费或重复 IO。"""
    from offerpilot.api import create_app as _create_app
    from offerpilot.db import session_factory_for_data_dir
    from offerpilot.knowledge.repository import KnowledgeRepository

    with TestClient(_create_app(data_dir=tmp_path)) as client:
        upload = _upload_file(client, "doc.md", "# Title\n\n正文。\n".encode("utf-8"))
        source_id = upload.json()["source"]["id"]

    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    first = repository.begin_delete(source_id)
    second = repository.begin_delete(source_id)
    assert first is not None
    assert second is None
