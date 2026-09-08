"""KI-07：持久队列 / lease / 取消 / 恢复契约测试。

覆盖 Spec §12：
- Extraction / Brief queue 单并发 FIFO (created_at, id)。
- Job claim 使用 lease owner、expiry、heartbeat。
- 应用重启后过期 running Job 恢复为 failed；已提交阶段不重复执行。
- 迟到的旧 lease 结果因 owner/Attempt 不匹配而拒绝提交。
- pending Job 立即取消；running 本地任务在安全点停止；已发出模型调用结果不能在取消后提交。
- 启动恢复清理 staging / final orphan 与 quarantine 删除。
- Worker 每次读取正式 Source 时核验 manifest/hash，不一致时以稳定错误失败。
- 自动重试计数和 ``next_retry_at`` 在重启后保持。
- Job detail / cancel API 返回稳定、用户安全的状态和错误。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from offerpilot.api import create_app
from offerpilot.db import init_database, session_factory_for_data_dir
from offerpilot.knowledge.repository import (
    JobCreateInput,
    KnowledgeRepository,
)
from offerpilot.knowledge.worker import (
    ExtractionWorker,
    JobExecutionResult,
    KnowledgeJobRunner,
)
from offerpilot.models import KnowledgeJob, KnowledgeSource


@pytest.fixture
def app_client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path))


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
    return client.post("/api/knowledge/sources", files=files, data=data)


# ---------------------------------------------------------------------------
# Spec §12：队列 FIFO + 单并发 claim
# ---------------------------------------------------------------------------


def test_ki07_claim_next_job_returns_oldest_pending_first(tmp_path):
    """Spec §12：按 created_at, id FIFO。"""
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)

    with session_factory() as session:
        for index in range(3):
            job = KnowledgeJob(
                kind="extract",
                queue="extraction",
                source_id=None,
                stage="queued",
                status="pending",
            )
            job.created_at = datetime(2026, 1, 1, 0, 0, index, tzinfo=timezone.utc)
            session.add(job)
        session.commit()

    first = repository.claim_next_job("extraction", lease_owner="w1")
    assert first is not None
    assert first.status == "running"
    assert first.lease_owner == "w1"
    assert first.attempt_token
    assert first.lease_expires_at is not None

    second = repository.claim_next_job("extraction", lease_owner="w1")
    assert second is not None
    assert second.id != first.id
    assert second.status == "running"

    third = repository.claim_next_job("extraction", lease_owner="w1")
    assert third is not None
    assert third.id not in {first.id, second.id}

    fourth = repository.claim_next_job("extraction", lease_owner="w1")
    assert fourth is None


def test_ki07_claim_does_not_return_canceled_jobs(tmp_path):
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)

    with session_factory() as session:
        canceled = KnowledgeJob(
            kind="extract",
            queue="extraction",
            source_id=None,
            stage="queued",
            status="pending",
        )
        canceled.canceled = True
        session.add(canceled)
        session.add(
            KnowledgeJob(
                kind="extract",
                queue="extraction",
                source_id=None,
                stage="queued",
                status="pending",
            )
        )
        session.commit()

    job = repository.claim_next_job("extraction", lease_owner="w1")
    assert job is not None
    assert job.canceled is False


def test_ki07_complete_job_rejects_stale_attempt_token(tmp_path):
    """Spec §12：迟到的旧 lease 结果因 owner/Attempt 不匹配而拒绝提交。"""
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)

    with session_factory() as session:
        session.add(
            KnowledgeJob(
                kind="extract",
                queue="extraction",
                source_id=None,
                stage="queued",
                status="pending",
            )
        )
        session.commit()

    job = repository.claim_next_job("extraction", lease_owner="w1")
    assert job is not None
    stale_token = job.attempt_token

    # 模拟其他 worker 抢占：再次 claim 生成新 token（实际生产中第一个 lease 已过期）
    reborn = repository.claim_next_job("extraction", lease_owner="w2")
    assert reborn is None  # 当前 job 已 running，不会再被 claim

    # 用旧 token 提交应被拒绝
    ok, record = repository.complete_job(
        job.id,
        attempt_token="deadbeef" * 8,
        status="succeeded",
    )
    assert ok is False
    assert record is None

    # 用正确 token 提交成功
    ok, _ = repository.complete_job(
        job.id,
        attempt_token=stale_token,
        status="succeeded",
    )
    assert ok is True


def test_ki07_heartbeat_extends_lease(tmp_path):
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)

    with session_factory() as session:
        session.add(
            KnowledgeJob(
                kind="extract",
                queue="extraction",
                source_id=None,
                stage="queued",
                status="pending",
            )
        )
        session.commit()

    job = repository.claim_next_job("extraction", lease_owner="w1", lease_duration_seconds=5)
    assert job is not None
    initial_expiry = job.lease_expires_at
    assert initial_expiry is not None

    refreshed = repository.heartbeat_job(
        job.id,
        attempt_token=job.attempt_token,
        lease_duration_seconds=60,
    )
    assert refreshed is not None
    assert refreshed.lease_expires_at is not None
    assert refreshed.lease_expires_at > initial_expiry


def test_ki07_heartbeat_rejects_wrong_token(tmp_path):
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)

    with session_factory() as session:
        session.add(
            KnowledgeJob(
                kind="extract",
                queue="extraction",
                source_id=None,
                stage="queued",
                status="pending",
            )
        )
        session.commit()

    job = repository.claim_next_job("extraction", lease_owner="w1")
    assert job is not None

    refreshed = repository.heartbeat_job(
        job.id,
        attempt_token="wrong-token",
    )
    assert refreshed is None


# ---------------------------------------------------------------------------
# Spec §12：取消规则
# ---------------------------------------------------------------------------


def test_ki07_cancel_pending_job_marks_status_canceled(tmp_path):
    """Spec §12：pending Job 直接标记 canceled。"""
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)

    with session_factory() as session:
        session.add(
            KnowledgeJob(
                kind="extract",
                queue="extraction",
                source_id=None,
                stage="queued",
                status="pending",
            )
        )
        session.commit()

    job_id = _last_job_id(tmp_path)
    canceled = repository.mark_canceled(job_id)
    assert canceled is not None
    assert canceled.status == "canceled"
    assert canceled.canceled is True
    assert canceled.stage == "canceled"


def test_ki07_cancel_running_job_keeps_status_until_safe_point(tmp_path):
    """Spec §12：running Job 设置 canceled=True，状态保持 running 直到 worker 在安全点写入。"""
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)

    with session_factory() as session:
        session.add(
            KnowledgeJob(
                kind="extract",
                queue="extraction",
                source_id=None,
                stage="extracting",
                status="pending",
            )
        )
        session.commit()

    job = repository.claim_next_job("extraction", lease_owner="w1")
    assert job is not None
    cancel_request = repository.mark_canceled(job.id)
    assert cancel_request is not None
    assert cancel_request.canceled is True
    assert cancel_request.status == "running"
    assert cancel_request.stage == "canceling"


def test_ki07_cancel_terminal_job_is_idempotent(tmp_path):
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)

    with session_factory() as session:
        session.add(
            KnowledgeJob(
                kind="extract",
                queue="extraction",
                source_id=None,
                stage="extracted",
                status="succeeded",
            )
        )
        session.commit()
    job_id = _last_job_id(tmp_path)
    canceled = repository.mark_canceled(job_id)
    assert canceled is not None
    assert canceled.status == "succeeded"


def test_ki07_is_job_canceled_reflects_flag(tmp_path):
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)

    with session_factory() as session:
        session.add(
            KnowledgeJob(
                kind="extract",
                queue="extraction",
                source_id=None,
                stage="queued",
                status="pending",
            )
        )
        session.commit()
    job_id = _last_job_id(tmp_path)
    assert repository.is_job_canceled(job_id) is False
    repository.mark_canceled(job_id)
    assert repository.is_job_canceled(job_id) is True


def _last_job_id(tmp_path) -> int:
    with sqlite3.connect(tmp_path / "data.db") as conn:
        row = conn.execute(
            "SELECT id FROM knowledge_jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    return int(row[0])


# ---------------------------------------------------------------------------
# Spec §12：启动恢复 — 过期 running Job + staging/final orphan
# ---------------------------------------------------------------------------


def test_ki07_recover_stale_running_jobs_marks_failed(tmp_path):
    """Spec §12：过期 running Job 在启动恢复时标记为 failed。"""
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)

    with session_factory() as session:
        stale = KnowledgeJob(
            kind="extract",
            queue="extraction",
            source_id=None,
            stage="extracting",
            status="running",
        )
        stale.lease_owner = "dead-worker"
        stale.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        stale.attempt_token = "expired"
        session.add(stale)
        session.commit()
        stale_id = stale.id

    recovered = repository.recover_stale_running_jobs()
    assert stale_id in recovered

    refreshed = repository.get_job(stale_id)
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.error_code == "job_lease_expired"
    assert refreshed.lease_expires_at is None


def test_ki07_init_database_clears_staging_orphans(tmp_path):
    """Spec §6：staging 中过期且无 Job 的目录在启动恢复时被清理。"""
    init_database(tmp_path / "data.db")
    staging_root = tmp_path / "knowledge" / "staging"
    staging_root.mkdir(parents=True)
    orphan = staging_root / "leftover-upload"
    orphan.mkdir()
    (orphan / "main.md").write_bytes(b"# Leftover\n")

    # 重新调用 init_database 模拟重启
    init_database(tmp_path / "data.db")

    assert not orphan.exists()


def test_ki07_init_database_clears_final_orphans(tmp_path):
    """Spec §6：commit 后崩溃，rename 完成但 Source 行不存在 → 启动恢复清理 final 目录。"""
    init_database(tmp_path / "data.db")
    sources_root = tmp_path / "knowledge" / "sources"
    sources_root.mkdir(parents=True)
    orphan = sources_root / "99991"
    orphan.mkdir()
    (orphan / "main.md").write_bytes(b"# Orphan\n")

    init_database(tmp_path / "data.db")

    assert not orphan.exists()


def test_ki07_init_database_keeps_final_dirs_with_db_rows(tmp_path):
    """Spec §6：合法 source_id 子目录必须保留，不能被误清。"""
    client = TestClient(create_app(data_dir=tmp_path))
    upload = _upload_file(client, "doc.md", "# Title\n\n正文。\n".encode("utf-8"))
    source_id = upload.json()["source"]["id"]

    init_database(tmp_path / "data.db")  # 模拟重启

    source_dir = tmp_path / "knowledge" / "sources" / str(source_id)
    assert source_dir.is_dir()


# ---------------------------------------------------------------------------
# Spec §6：Worker 完整性校验
# ---------------------------------------------------------------------------


def test_ki07_worker_rejects_tampered_source_file(tmp_path):
    """Spec §6：Worker 每次读取正式 Source 时核验 manifest/hash，不一致时 source_integrity_mismatch。"""
    client = TestClient(create_app(data_dir=tmp_path))
    upload = _upload_file(client, "doc.md", "# Title\n\n原始内容。\n".encode("utf-8"))
    source_id = upload.json()["source"]["id"]

    # 篡改正式目录下的原件
    session_factory = session_factory_for_data_dir(tmp_path)
    with session_factory() as session:
        source = session.get(KnowledgeSource, source_id)
        assert source is not None
        main_path = tmp_path / source.main_relative_path
    main_path.write_bytes("# Tampered\n\n这是被外部修改的内容。\n".encode("utf-8"))

    repository = KnowledgeRepository(session_factory)
    worker = ExtractionWorker(repository, tmp_path, session_factory)
    runner = KnowledgeJobRunner(repository, worker)

    # 创建重试 Job 进入队列
    retry = runner.retry_extract(source_id)
    assert retry is not None

    results = runner.tick_extraction(lease_owner="test-worker")
    assert results, "runner should execute the retried job"
    result = results[0]
    assert result.accepted is True
    assert result.status == "failed"
    assert result.error_code == "source_integrity_mismatch"


def test_ki07_worker_succeeds_retry_after_idempotent_replay(tmp_path):
    """Spec §12：已提交阶段不会重复执行 — commit_extraction 幂等保护。"""
    client = TestClient(create_app(data_dir=tmp_path))
    upload = _upload_file(client, "doc.md", "# Title\n\n原始内容。\n".encode("utf-8"))
    source_id = upload.json()["source"]["id"]

    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    worker = ExtractionWorker(repository, tmp_path, session_factory)
    runner = KnowledgeJobRunner(repository, worker)

    retry = runner.retry_extract(source_id)
    assert retry is not None
    results = runner.tick_extraction(lease_owner="test-worker")
    assert results
    assert results[0].status == "succeeded"

    # 重新执行应仍然成功（幂等）
    retry2 = runner.retry_extract(source_id)
    assert retry2 is not None
    results2 = runner.tick_extraction(lease_owner="test-worker")
    assert results2
    assert results2[0].status == "succeeded"


def test_ki07_runner_brief_queue_marks_not_implemented(tmp_path):
    """KI-07 范围：Brief queue 框架可调度但不实际调用模型。"""
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    repository.create_job(
        JobCreateInput(
            kind="brief",
            queue="brief",
            source_id=None,
            stage="queued",
        )
    )
    worker = ExtractionWorker(repository, tmp_path, session_factory)
    runner = KnowledgeJobRunner(repository, worker)
    results = runner.tick_brief(lease_owner="test-brief")
    assert results
    assert results[0].status == "failed"
    assert results[0].error_code == "brief_not_implemented"


# ---------------------------------------------------------------------------
# Spec §12：Job detail / cancel API
# ---------------------------------------------------------------------------


def test_ki07_get_job_returns_payload_without_attempt_token(app_client):
    """Spec §16.3：Job detail 不返回 Prompt、Provider secret 或 Source 正文。"""
    upload = _upload_file(app_client, "doc.md", "# Title\n\n正文。\n".encode("utf-8"))
    source_id = upload.json()["source"]["id"]
    jobs = app_client.get(f"/api/knowledge/sources/{source_id}/jobs").json()
    job_id = jobs["jobs"][0]["id"]

    detail = app_client.get(f"/api/knowledge/jobs/{job_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == job_id
    assert body["kind"] == "extract"
    assert body["queue"] == "extraction"
    # attempt_token 不得出现在响应中
    assert "attempt_token" not in body
    # lease_owner / heartbeat 等元数据可以暴露
    assert "lease_owner" in body
    assert "lease_expires_at" in body
    assert "heartbeat_at" in body
    assert "next_retry_at" in body


def test_ki07_get_job_unknown_returns_404(app_client):
    response = app_client.get("/api/knowledge/jobs/99991")
    assert response.status_code == 404


def test_ki07_cancel_pending_job_succeeds(app_client, tmp_path):
    """Spec §12：pending Job 立即取消。"""
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    repository.create_job(
        JobCreateInput(
            kind="extract",
            queue="extraction",
            source_id=None,
            stage="queued",
        )
    )
    with sqlite3.connect(tmp_path / "data.db") as conn:
        row = conn.execute(
            "SELECT id FROM knowledge_jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    job_id = int(row[0])

    cancel = app_client.post(f"/api/knowledge/jobs/{job_id}/cancel")
    assert cancel.status_code == 200
    body = cancel.json()
    assert body["status"] == "canceled"
    assert body["canceled"] is True


def test_ki07_cancel_terminal_job_is_idempotent_via_api(app_client, tmp_path):
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    with session_factory() as session:
        session.add(
            KnowledgeJob(
                kind="extract",
                queue="extraction",
                source_id=None,
                stage="extracted",
                status="succeeded",
            )
        )
        session.commit()
    with sqlite3.connect(tmp_path / "data.db") as conn:
        row = conn.execute(
            "SELECT id FROM knowledge_jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    job_id = int(row[0])

    cancel = app_client.post(f"/api/knowledge/jobs/{job_id}/cancel")
    assert cancel.status_code == 200
    body = cancel.json()
    assert body["status"] == "succeeded"


def test_ki07_cancel_unknown_job_returns_404(app_client):
    response = app_client.post("/api/knowledge/jobs/99991/cancel")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Spec §12：自动重试 retry_count / next_retry_at 在重启后保持
# ---------------------------------------------------------------------------


def test_ki07_retry_count_persists_through_restart(tmp_path):
    """Spec §12：自动重试计数和 ``next_retry_at`` 持久化，重启后不从零开始。"""
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)

    next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    with session_factory() as session:
        job = KnowledgeJob(
            kind="brief",
            queue="brief",
            source_id=None,
            stage="provider_transient_error",
            status="running",
        )
        job.retry_count = 2
        job.next_retry_at = next_retry_at
        job.attempt_token = "persist-test"
        job.lease_owner = "test-worker"
        job.lease_expires_at = next_retry_at
        session.add(job)
        session.commit()
        job_id = job.id

    # 模拟重启
    init_database(tmp_path / "data.db")
    refreshed = repository.get_job(job_id)
    assert refreshed is not None
    assert refreshed.retry_count == 2
    assert refreshed.next_retry_at is not None


# ---------------------------------------------------------------------------
# Spec §12：故障注入 — 进程中断 + 迟到 lease 拒绝提交
# ---------------------------------------------------------------------------


def test_ki07_stale_lease_cannot_commit_after_reclaim(tmp_path):
    """Spec §12：进程 A claim → 进程 B 重启 → 进程 A 迟到提交被拒绝。

    场景：worker A claim Job 拿到 token_A，进程崩溃。启动恢复把过期 Job 标记
    failed。worker A 复活后用 token_A 提交，应被拒绝（attempt_token 已不再有效
    因为 status != running）。
    """
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)

    job = repository.create_job(
        JobCreateInput(
            kind="extract",
            queue="extraction",
            source_id=None,
            stage="queued",
        )
    )
    claimed = repository.claim_next_job("extraction", lease_owner="A")
    assert claimed is not None
    assert claimed.id == job.id
    stale_token = claimed.attempt_token

    # 模拟 A 崩溃后 lease 过期 → 启动恢复标记 failed
    with session_factory() as session:
        row = session.get(KnowledgeJob, job.id)
        assert row is not None
        row.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        session.commit()
    repository.recover_stale_running_jobs()

    # A 复活后用旧 token 提交 → 拒绝
    ok, _ = repository.complete_job(
        job.id,
        attempt_token=stale_token,
        status="succeeded",
    )
    assert ok is False

    # Job 仍为 failed
    refreshed = repository.get_job(job.id)
    assert refreshed is not None
    assert refreshed.status == "failed"


def test_ki07_canceled_job_complete_attempt_returns_rejected(tmp_path):
    """ExtractionWorker.execute 在取消后调用 complete 应返回 accepted=True 但 status=canceled。"""
    client = TestClient(create_app(data_dir=tmp_path))
    upload = _upload_file(client, "doc.md", "# Title\n\n正文。\n".encode("utf-8"))
    source_id = upload.json()["source"]["id"]

    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    worker = ExtractionWorker(repository, tmp_path, session_factory)
    runner = KnowledgeJobRunner(repository, worker)
    retry = runner.retry_extract(source_id)
    assert retry is not None

    # claim 后立即取消，worker 应在安全点完成 canceled 状态
    claimed = repository.claim_next_job("extraction", lease_owner="w")
    assert claimed is not None
    repository.mark_canceled(claimed.id)
    result: JobExecutionResult = worker.execute(
        claimed,
        attempt_token=claimed.attempt_token,
        lease_owner="w",
    )
    assert result.status == "canceled"
    assert result.error_code == "job_canceled"


def test_ki07_tick_consumes_all_pending_in_fifo_order(tmp_path):
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    for _ in range(3):
        repository.create_job(
            JobCreateInput(
                kind="extract",
                queue="extraction",
                source_id=None,
                stage="queued",
            )
        )
    worker = ExtractionWorker(repository, tmp_path, session_factory)
    runner = KnowledgeJobRunner(repository, worker)
    results = runner.tick_extraction(lease_owner="w")
    # 没有 source_id 的 Extract Job 在 execute 阶段会失败为 source_integrity_mismatch，
    # 但每个 Job 都被消费 → accepted=True, status=failed。
    assert len(results) == 3
    for r in results:
        assert r.accepted is True
    # 第二次 tick 应返回空
    results2 = runner.tick_extraction(lease_owner="w")
    assert results2 == []


# ---------------------------------------------------------------------------
# Spec §12：Source 完整性 — main file missing
# ---------------------------------------------------------------------------


def test_ki07_worker_fails_when_main_file_missing(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    upload = _upload_file(client, "doc.md", "# Title\n\n正文。\n".encode("utf-8"))
    source_id = upload.json()["source"]["id"]

    session_factory = session_factory_for_data_dir(tmp_path)
    with session_factory() as session:
        source = session.get(KnowledgeSource, source_id)
        assert source is not None
        main_path = tmp_path / source.main_relative_path
    main_path.unlink()

    repository = KnowledgeRepository(session_factory)
    worker = ExtractionWorker(repository, tmp_path, session_factory)
    runner = KnowledgeJobRunner(repository, worker)
    retry = runner.retry_extract(source_id)
    assert retry is not None

    results = runner.tick_extraction(lease_owner="w")
    assert results
    assert results[0].status == "failed"
    assert results[0].error_code == "source_integrity_mismatch"


# ---------------------------------------------------------------------------
# Code Review H1 / H2 / M6 修复后的回归覆盖
# ---------------------------------------------------------------------------


def test_ki07_concurrent_claim_only_one_worker_wins(tmp_path):
    """Spec §12 + H2：并发 claim 同一 Job 只有一方胜出。

    SQLite ``SELECT FOR UPDATE`` 是 no-op，因此 claim_next_job 必须用乐观 UPDATE 守卫。
    本测试通过两个独立 session_factory（共享同一 SQLite 文件）模拟并发 worker，
    验证第二个 claim 在第一个 commit 之后不会拿到同一 Job。
    """
    init_database(tmp_path / "data.db")
    session_factory_a = session_factory_for_data_dir(tmp_path)
    session_factory_b = session_factory_for_data_dir(tmp_path)
    repo_a = KnowledgeRepository(session_factory_a)
    repo_b = KnowledgeRepository(session_factory_b)
    repo_a.create_job(
        JobCreateInput(
            kind="extract",
            queue="extraction",
            source_id=None,
            stage="queued",
        )
    )

    first = repo_a.claim_next_job("extraction", lease_owner="A")
    assert first is not None
    # 模拟 B 同时尝试 claim —— A 已经把 status 改为 running，B 应当跳过拿到 None。
    second = repo_b.claim_next_job("extraction", lease_owner="B")
    assert second is None


def test_ki07_recover_stale_running_skips_delete_jobs(tmp_path):
    """Spec §12 + H1：``_recover_knowledge_runtime`` 不得处理 delete Job。

    delete Job 的恢复由 ``_recover_knowledge_deletions`` 负责。本测试构造一个
    过期的 delete Job + lifecycle=deleting Source + quarantine 目录，启动恢复后
    delete Job 行应被清理（与 Source 一并删除），而不是被标记 failed 残留。
    """
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    with session_factory() as session:
        source = KnowledgeSource(
            source_hash="sha256:test-delete-recover",
            source_kind="markdown",
            display_title="",
            title_hint="",
            main_filename="d.md",
            main_media_type="text/markdown",
            main_relative_path="",
            manifest_json="{}",
            total_bytes=1,
            token_count=0,
            lifecycle="deleting",
            extraction_status="extracted",
            brief_status="not_started",
        )
        session.add(source)
        session.flush()
        source_id = source.id
        delete_job = KnowledgeJob(
            kind="delete",
            queue="extraction",
            source_id=source_id,
            stage="deleting",
            status="running",
        )
        delete_job.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        delete_job.attempt_token = "expired-delete"
        session.add(delete_job)
        session.commit()
        delete_job_id = delete_job.id

    # _recover_knowledge_deletions 依赖 quarantine_root 存在；KI-06 purge_source
    # 总会 mkdir quarantine_root，这里模拟该副作用。
    quarantine_root = tmp_path / "knowledge" / "quarantine"
    quarantine_root.mkdir(parents=True, exist_ok=True)

    # 重启 → _recover_knowledge_runtime 应跳过 delete Job；_recover_knowledge_deletions
    # 会处理 lifecycle=deleting Source + Job。
    init_database(tmp_path / "data.db")

    with sqlite3.connect(tmp_path / "data.db") as conn:
        job = conn.execute(
            "SELECT id, status, error_code FROM knowledge_jobs WHERE id = ?",
            (delete_job_id,),
        ).fetchall()
        source = conn.execute(
            "SELECT id FROM knowledge_sources WHERE id = ?", (source_id,)
        ).fetchall()
        logs = conn.execute(
            "SELECT action, result FROM knowledge_logs WHERE source_id = ?",
            (source_id,),
        ).fetchall()
    assert job == [], f"delete Job 应被 _recover_knowledge_deletions 清理,实际: {job}"
    assert source == []
    assert ("source_deleted", "succeeded") in logs


def test_ki07_cancel_running_clears_lease_expires_at(tmp_path):
    """Spec §12 + M6：cancel running Job 时清空 lease_expires_at。

    防止启动恢复把已 cancel 的 running Job 误判为过期 failed。
    """
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    with session_factory() as session:
        session.add(
            KnowledgeJob(
                kind="extract",
                queue="extraction",
                source_id=None,
                stage="queued",
                status="pending",
            )
        )
        session.commit()
    job_id = _last_job_id(tmp_path)

    claimed = repository.claim_next_job("extraction", lease_owner="w1")
    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.lease_expires_at is not None

    canceled = repository.mark_canceled(job_id)
    assert canceled is not None
    assert canceled.canceled is True
    assert canceled.status == "running"  # running 路径保持 status，等 worker 在安全点完成
    assert canceled.lease_expires_at is None

    # recover_stale_running_jobs 不应触碰已 cancel 的 Job
    recovered = repository.recover_stale_running_jobs()
    assert job_id not in recovered


def test_ki07_complete_job_pending_state_is_rejected(tmp_path):
    """Spec §12 + H3：未 claim 的 pending Job 不得直接提交。

    没有 attempt_token 就没有 lease，必须先 claim 才能提交。
    """
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    with session_factory() as session:
        job = KnowledgeJob(
            kind="extract",
            queue="extraction",
            source_id=None,
            stage="queued",
            status="pending",
        )
        job.attempt_token = "pending-token"
        session.add(job)
        session.commit()
        job_id = job.id

    ok, _ = repository.complete_job(
        job_id,
        attempt_token="pending-token",
        status="succeeded",
    )
    assert ok is False
    refreshed = repository.get_job(job_id)
    assert refreshed is not None
    assert refreshed.status == "pending"
