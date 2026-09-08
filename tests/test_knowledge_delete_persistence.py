"""Knowledge 删除协议的故障注入测试。"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from conftest import wait_for_extraction, wait_for_source_deleted
from offerpilot.api import create_app
from offerpilot.db import init_database, session_factory_for_data_dir
from offerpilot.knowledge.repository import KnowledgeRepository
from offerpilot.knowledge.service import IngestRequest, KnowledgeIngestService


def _upload(client: TestClient, name: str, content: bytes) -> int:
    response = client.post(
        "/api/knowledge/sources",
        files={"file": (name, content, "text/markdown")},
    )
    assert response.status_code == 202
    source_id = int(response.json()["source"]["id"])
    wait_for_extraction(client, source_id)
    return source_id


def test_purge_does_not_delete_db_when_quarantine_move_fails(tmp_path, monkeypatch):
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    service = KnowledgeIngestService(repository, tmp_path, session_factory)
    result = service.ingest(
        IngestRequest(filename="doc.md", content_bytes=b"# title\n\nbody\n")
    )
    source_id = result.source.id
    source_dir = tmp_path / "knowledge" / "sources" / str(source_id)

    def fail_replace(self, target, *_args, **_kwargs):
        raise OSError("simulated rename failure")

    # worker 用 Path.replace 原子移动 source → quarantine；注入失败模拟文件系统错误。
    from pathlib import Path
    monkeypatch.setattr(Path, "replace", fail_replace)
    # 异步：purge_source 入队 delete job（返回 PurgeResult，非 None）；移动在 worker。
    assert service.purge_source(source_id) is not None
    # 驱动 worker tick：replace 失败 → delete 未完成（quarantine_retry），source 保留 deleting。
    from offerpilot.knowledge.worker import ExtractionWorker, KnowledgeJobRunner
    KnowledgeJobRunner(
        repository, ExtractionWorker(repository, tmp_path, session_factory)
    ).tick_extraction(lease_owner="test")

    with sqlite3.connect(tmp_path / "data.db") as conn:
        row = conn.execute(
            "SELECT lifecycle FROM knowledge_sources WHERE id = ?", (source_id,)
        ).fetchone()
    assert row == ("deleting",)
    assert source_dir.is_dir()


def test_startup_recovery_handles_deleting_source_without_quarantine_root(tmp_path):
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    service = KnowledgeIngestService(repository, tmp_path, session_factory)
    result = service.ingest(
        IngestRequest(filename="doc.md", content_bytes=b"# title\n\nbody\n")
    )
    source_id = result.source.id
    assert repository.begin_delete(source_id) is not None
    quarantine_root = tmp_path / "knowledge" / "quarantine"
    assert not quarantine_root.exists()

    init_database(tmp_path / "data.db")

    with sqlite3.connect(tmp_path / "data.db") as conn:
        assert conn.execute(
            "SELECT id FROM knowledge_sources WHERE id = ?", (source_id,)
        ).fetchone() is None
    assert not (tmp_path / "knowledge" / "sources" / str(source_id)).exists()


def test_purge_removes_only_traces_referencing_deleted_source(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        deleted_id = _upload(client, "deleted.md", b"# deleted\n\nneedle-a\n")
        kept_id = _upload(client, "kept.md", b"# kept\n\nneedle-b\n")

        deleted_search = client.post(
            "/api/knowledge/evidence/search", json={"query": "needle-a"}
        )
        kept_search = client.post(
            "/api/knowledge/evidence/search", json={"query": "needle-b"}
        )
        assert deleted_search.status_code == 200
        assert kept_search.status_code == 200
        assert deleted_search.json()["hits"]
        assert kept_search.json()["hits"]

        assert client.delete(f"/api/knowledge/sources/{deleted_id}").status_code == 202
        assert wait_for_source_deleted(client, deleted_id, db_path=tmp_path / "data.db")

    with sqlite3.connect(tmp_path / "data.db") as conn:
        traces = conn.execute(
            "SELECT hits_json FROM knowledge_retrieval_traces"
        ).fetchall()
    assert traces
    assert all(f'"source_id": {deleted_id}' not in row[0] for row in traces)
    assert any(f'"source_id": {kept_id}' in row[0] for row in traces)


def test_get_source_hides_deleting_source(tmp_path):
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    service = KnowledgeIngestService(repository, tmp_path, session_factory)
    result = service.ingest(
        IngestRequest(filename="doc.md", content_bytes=b"# title\n\nbody\n")
    )
    assert repository.begin_delete(result.source.id) is not None
    assert repository.get_source(result.source.id) is None
