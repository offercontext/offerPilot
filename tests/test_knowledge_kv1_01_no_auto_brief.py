"""KV1-01：导入完成后不再自动触发 Brief 的回归测试。

V1 范围（ADR-0002 / Spec 2026-07-18）：Imported Source 完成 Preflight / Extraction /
Snapshot / Evidence / FTS 提交后即为可用，不自动创建 Brief Job、不检查 Provider，
也不把 Brief pending / blocked / failed 写成 Source 的未完成状态。显式 Brief rebuild
代码保留，但不由 V1 导入触发。

本文件覆盖三条自动触发路径全部关闭：

1. 同步 Ingest（``service.ingest``）不在 Evidence 提交后直接调用 Brief 入队，
   也不写入 ``brief_enqueue_failed``。
2. 异步 ExtractionWorker success callback 不注册自动 Brief；生产入口（``create_app``）
   注册的 ExtractionWorker 与 KnowledgeWorkerRuntime 均不携带 Brief callback。
3. KnowledgeWorkerRuntime 重启恢复（``_repair_missing_brief_jobs``）在无 callback 时
   不为 extracted + ``not_started`` 的 Source 补建 Brief Job。

显式 Brief rebuild（``service.rebuild_brief``）的独立路径必须保留：无 Provider 时返回
稳定 block reason，不创建 Brief Job，但方法仍可调用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from offerpilot.config import Config
from offerpilot.db import init_database, session_factory_for_data_dir
from offerpilot.knowledge.repository import KnowledgeRepository
from offerpilot.knowledge.service import IngestRequest, KnowledgeIngestService
from offerpilot.knowledge.worker import (
    BriefWorker,
    ExtractionWorker,
    KnowledgeJobRunner,
    KnowledgeWorkerRuntime,
)
from conftest import wait_for_extraction


def _make_service(
    tmp_path: Path,
) -> tuple[KnowledgeRepository, KnowledgeIngestService, Any]:
    """构造真实 repository + service（独立临时数据库），不走生产 callback 注册。"""
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    # 显式传入无 Provider 的 Config：模拟 V1 默认环境，让 rebuild_brief 的 block 评估
    # 走 provider_unavailable 分支，而非 config=None 时误判 Provider 可用。
    service = KnowledgeIngestService(repository, tmp_path, session_factory, config=Config())
    return repository, service, session_factory


def _has_brief_job(repository: KnowledgeRepository, source_id: int) -> bool:
    return any(job.kind == "brief" for job in repository.list_jobs_for_source(source_id))


# ---------------------------------------------------------------------------
# 路径 1：同步 Ingest 不自动触发 Brief
# ---------------------------------------------------------------------------


def test_sync_ingest_keeps_brief_not_started_and_no_brief_job(tmp_path: Path) -> None:
    """service.ingest 创建 Source(pending) + Extract Job，不创建 Brief Job，
    不写入 brief_enqueue_failed；Brief 保持 not_started。"""
    repository, service, _ = _make_service(tmp_path)
    result = service.ingest(
        IngestRequest(filename="doc.md", content_bytes="# Title\n\n正文证据片段。\n".encode("utf-8"))
    )
    source = repository.get_source(result.source.id)
    assert source is not None
    # ingest 不同步执行 Extraction；Source 初始即 pending，等待 ExtractionWorker 消费。
    assert source.extraction_status == "pending"
    assert source.brief_status == "not_started"
    assert source.brief_error_code == ""
    assert source.brief_block_reason == ""
    assert _has_brief_job(repository, source.id) is False


# ---------------------------------------------------------------------------
# 路径 2：异步 ExtractionWorker success callback 不自动触发 Brief
# ---------------------------------------------------------------------------


def test_extraction_worker_without_callback_does_not_enqueue_brief(tmp_path: Path) -> None:
    """生产式 ExtractionWorker（不传 on_extraction_succeeded，与 KV1-01 create_app 一致）
    完成 Extraction 后 Source 为 extracted、Brief 保持 not_started、不存在 Brief Job。"""
    repository, service, session_factory = _make_service(tmp_path)
    result = service.ingest(
        IngestRequest(filename="doc.md", content_bytes="# Title\n\n正文证据片段。\n".encode("utf-8"))
    )
    # 不传 callback：模拟 KV1-01 后 create_app 的 ExtractionWorker 注册。
    extraction_worker = ExtractionWorker(repository, tmp_path, session_factory)
    runner = KnowledgeJobRunner(repository, extraction_worker)
    runner.tick_extraction(lease_owner="kv1-01-test")

    source = repository.get_source(result.source.id)
    assert source is not None
    assert source.extraction_status == "extracted"
    assert source.brief_status == "not_started"
    assert source.brief_block_reason == ""
    assert _has_brief_job(repository, source.id) is False


def test_duplicate_ingest_and_extraction_replay_create_no_brief_job(tmp_path: Path) -> None:
    """重复导入同一内容（dedup）与 Worker 重放 tick_extraction 均不产生 Brief Job。

    覆盖 KV1-01 checklist 第 6 项：重复导入、Worker 重放和恢复 pending Extraction
    不产生重复或迟到 Brief Job。
    """
    repository, service, session_factory = _make_service(tmp_path)
    content = "# Title\n\n正文证据片段。\n".encode("utf-8")
    first = service.ingest(IngestRequest(filename="doc.md", content_bytes=content))
    # 重复导入命中 dedup：复用 Source，不创建第二个 Extract Job，也不触发 Brief。
    second = service.ingest(IngestRequest(filename="doc-dup.md", content_bytes=content))
    assert second.deduplicated is True
    assert second.source.id == first.source.id

    extraction_worker = ExtractionWorker(repository, tmp_path, session_factory)
    runner = KnowledgeJobRunner(repository, extraction_worker)
    runner.tick_extraction(lease_owner="kv1-01-dedup-1")
    # Worker 重放：再次 tick_extraction 不应制造迟到 Brief Job。
    runner.tick_extraction(lease_owner="kv1-01-dedup-2")

    source = repository.get_source(first.source.id)
    assert source is not None
    assert source.extraction_status == "extracted"
    assert source.brief_status == "not_started"
    assert _has_brief_job(repository, first.source.id) is False


def test_production_app_upload_keeps_brief_not_started(app_client: TestClient) -> None:
    """端到端：生产 create_app 上传 Source，等待 Extraction 完成，Brief 保持 not_started，
    无 Brief Job、无 block_reason、无 brief_enqueue_failed。这是 KV1-01 的核心 red→green。"""
    response = app_client.post(
        "/api/knowledge/sources",
        files={"file": ("doc.md", "# Title\n\n正文证据片段。\n".encode("utf-8"), "text/markdown")},
    )
    assert response.status_code == 202, response.text
    source_id = response.json()["source"]["id"]
    refreshed = wait_for_extraction(app_client, source_id)
    assert refreshed["extraction_status"] == "extracted"
    # KV1-01：无合格 Provider 也不再自动评估 Brief；Brief 维持 not_started。
    assert refreshed["brief_status"] == "not_started"
    assert refreshed["brief_block_reason"] == ""
    assert refreshed["brief_error_code"] == ""
    jobs = app_client.get(f"/api/knowledge/sources/{source_id}/jobs").json()
    assert not any(job["kind"] == "brief" for job in jobs["jobs"])


# ---------------------------------------------------------------------------
# 路径 3：KnowledgeWorkerRuntime 重启恢复不补 Brief Job
# ---------------------------------------------------------------------------


def test_worker_runtime_recovery_does_not_enqueue_brief(tmp_path: Path) -> None:
    """无 callback 的 KnowledgeWorkerRuntime.run_once 触发 _repair_missing_brief_jobs，
    对 extracted + not_started 的 Source 不补建 Brief Job。"""
    repository, service, session_factory = _make_service(tmp_path)
    result = service.ingest(
        IngestRequest(filename="doc.md", content_bytes="# Title\n\n正文证据片段。\n".encode("utf-8"))
    )
    extraction_worker = ExtractionWorker(
        repository, tmp_path, session_factory
    )
    runner = KnowledgeJobRunner(repository, extraction_worker)
    runner.tick_extraction(lease_owner="kv1-01-test")
    # Source 现在 extracted + brief not_started，正是旧 _repair_missing_brief_jobs 会补 Job 的状态。
    source = repository.get_source(result.source.id)
    assert source is not None
    assert source.extraction_status == "extracted"
    assert source.brief_status == "not_started"

    # 生产式 runtime：不传 on_extraction_succeeded（与 KV1-01 create_app 一致）。
    brief_worker = BriefWorker(repository, Config())
    recovery_runner = KnowledgeJobRunner(
        repository,
        ExtractionWorker(repository, tmp_path, session_factory),
        brief_worker=brief_worker,
    )
    runtime = KnowledgeWorkerRuntime(recovery_runner, repository)
    runtime.run_once()  # 内部调用 _repair_missing_brief_jobs

    assert _has_brief_job(repository, result.source.id) is False
    after = repository.get_source(result.source.id)
    assert after is not None
    assert after.brief_status == "not_started"
    assert after.brief_block_reason == ""


# ---------------------------------------------------------------------------
# 显式 Brief rebuild 路径保留（不纳入 V1 成功定义，但代码不破坏）
# ---------------------------------------------------------------------------


def test_explicit_rebuild_brief_path_preserved_without_provider(tmp_path: Path) -> None:
    """显式 rebuild_brief 在无 Provider 时返回稳定 block reason，不创建 Brief Job；
    证明 KV1-01 只关闭自动触发，未破坏显式入口。"""
    repository, service, session_factory = _make_service(tmp_path)
    result = service.ingest(
        IngestRequest(filename="doc.md", content_bytes="# Title\n\n正文证据片段。\n".encode("utf-8"))
    )
    extraction_worker = ExtractionWorker(
        repository, tmp_path, session_factory
    )
    KnowledgeJobRunner(repository, extraction_worker).tick_extraction(lease_owner="kv1-01-test")

    updated, message = service.rebuild_brief(result.source.id)
    assert updated is not None
    # 无合格 Provider → 稳定 block reason，不创建 Brief Job / Attempt。
    assert updated.brief_block_reason
    assert message == updated.brief_block_reason
    assert _has_brief_job(repository, result.source.id) is False
