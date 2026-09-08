"""Brief Job lease/cancel 原子门禁回归测试。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from offerpilot.config import Config
from offerpilot.db import init_database, session_factory_for_data_dir
from offerpilot.knowledge.brief import (
    BRIEF_LANGUAGE,
    BRIEF_MIN_CONTEXT_WINDOW,
    BRIEF_PROMPT_VERSION,
    BRIEF_SCHEMA_VERSION,
)
from offerpilot.knowledge.repository import (
    BRIEF_LEASE_REQUEUE_MAX,
    BriefAttemptCreateInput,
    JobCreateInput,
    KnowledgeRepository,
)
from offerpilot.knowledge.service import IngestRequest, KnowledgeIngestService
from offerpilot.knowledge.worker import (
    BRIEF_HEARTBEAT_LEASE_SECONDS,
    BRIEF_MODEL_TIMEOUT_SECONDS,
    BriefWorker,
    ExtractionWorker,
    KnowledgeJobRunner,
)
from offerpilot.models import KnowledgeExtractionSnapshot, KnowledgeJob, KnowledgeSource


def _setup(tmp_path: Path) -> tuple[KnowledgeRepository, int, int]:
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    service = KnowledgeIngestService(repository, tmp_path, session_factory, config=Config())
    result = service.ingest(
        IngestRequest(
            filename="brief-lease.md",
            content_bytes="# 标题\n\n正文内容。\n".encode("utf-8"),
        )
    )
    # ingest 只入队 extraction Job，需显式驱动 ExtractionWorker 完成 Snapshot/Evidence，
    # 否则 active_snapshot_id 为 None、extraction_status 未到 extracted。
    runner = KnowledgeJobRunner(
        repository,
        ExtractionWorker(repository, tmp_path, session_factory),
    )
    runner.tick_extraction(lease_owner="test", lease_duration_seconds=30)
    source = repository.get_source(result.source.id)
    assert source is not None
    assert source.active_snapshot_id is not None
    return repository, result.source.id, source.active_snapshot_id


def _create_attempt(
    repository: KnowledgeRepository, source_id: int, snapshot_id: int
) -> tuple[int, int, str]:
    attempt, job_id, token = repository.create_brief_attempt(
        BriefAttemptCreateInput(
            source_id=source_id,
            snapshot_id=snapshot_id,
            provider_id="primary",
            provider_model="brief-model",
            provider_base_url="",
            context_window=BRIEF_MIN_CONTEXT_WINDOW,
            max_output_tokens=4096,
            prompt_version=BRIEF_PROMPT_VERSION,
            schema_version=BRIEF_SCHEMA_VERSION,
            language=BRIEF_LANGUAGE,
        )
    )
    return attempt.id, job_id, token


def test_canceled_running_job_cannot_renew_or_complete_success(tmp_path: Path) -> None:
    repository, source_id, snapshot_id = _setup(tmp_path)
    _, job_id, token = _create_attempt(repository, source_id, snapshot_id)

    canceled = repository.mark_canceled(job_id)
    assert canceled is not None
    assert canceled.status == "running"
    assert canceled.canceled is True
    assert repository.heartbeat_job(job_id, attempt_token=token) is None

    ok, record = repository.complete_job(
        job_id,
        attempt_token=token,
        status="succeeded",
    )
    assert ok is False
    assert record is None

    ok, record = repository.complete_job(
        job_id,
        attempt_token=token,
        status="canceled",
        stage="canceled",
        error_code="job_canceled",
    )
    assert ok is True
    assert record is not None
    assert record.status == "canceled"


def test_brief_commit_rejects_canceled_job_without_publishing_brief(
    tmp_path: Path,
) -> None:
    repository, source_id, snapshot_id = _setup(tmp_path)
    attempt_id, job_id, token = _create_attempt(repository, source_id, snapshot_id)
    repository.mark_canceled(job_id)

    ok, brief, _ = repository.commit_brief_attempt_success(
        attempt_id,
        job_id=job_id,
        attempt_token=token,
        payload_json="{}",
        validation_report_json="{}",
    )
    assert ok is False
    assert brief is None
    assert repository.get_source_brief(source_id) is None
    attempt = repository.get_brief_attempt(attempt_id)
    assert attempt is not None
    assert attempt.status == "processing"


def test_expired_brief_lease_rejects_failure_and_success_commit(tmp_path: Path) -> None:
    repository, source_id, snapshot_id = _setup(tmp_path)
    attempt_id, job_id, token = _create_attempt(repository, source_id, snapshot_id)
    expired = datetime.now(timezone.utc) - timedelta(seconds=5)
    with repository._session_factory() as session:  # noqa: SLF001 - 故障注入
        row = session.get(KnowledgeJob, job_id)
        assert row is not None
        row.lease_expires_at = expired
        session.commit()

    ok, attempt, job = repository.fail_brief_attempt(
        attempt_id,
        job_id=job_id,
        attempt_token=token,
        error_code="provider_transient_error",
        error_message="expired",
    )
    assert ok is False
    assert attempt is None
    assert job is None
    assert repository.get_brief_attempt(attempt_id).status == "processing"  # type: ignore[union-attr]

    ok, job_record = repository.complete_job(
        job_id,
        attempt_token=token,
        status="failed",
        error_code="provider_transient_error",
    )
    assert ok is False
    assert job_record is None


def test_brief_commit_rejects_stale_snapshot_generation(tmp_path: Path) -> None:
    """Extraction 切代后，旧 Attempt 不能覆盖当前 Brief。"""

    repository, source_id, snapshot_id = _setup(tmp_path)
    attempt_id, job_id, token = _create_attempt(repository, source_id, snapshot_id)
    with repository._session_factory() as session:  # noqa: SLF001 - 故障注入
        source = session.get(KnowledgeSource, source_id)
        assert source is not None
        newer = KnowledgeExtractionSnapshot(
            source_id=source_id,
            extractor_version="test-next-extractor",
            parser_version="test-parser",
            normalization_version="test-normalization",
            tokenizer_version="test-tokenizer",
            encoding="utf-8",
            detection_method="test",
            canonical_text="新版本正文",
            structure_manifest="{}",
            digest="sha256:test-next-snapshot",
            token_count=2,
            char_count=5,
        )
        session.add(newer)
        session.flush()
        source.active_snapshot_id = newer.id
        session.commit()

    ok, brief, _ = repository.commit_brief_attempt_success(
        attempt_id,
        job_id=job_id,
        attempt_token=token,
        payload_json="{}",
        validation_report_json="{}",
    )
    assert ok is False
    assert brief is None
    assert repository.get_source_brief(source_id) is None
    attempt = repository.get_brief_attempt(attempt_id)
    assert attempt is not None
    assert attempt.status == "failed"
    assert attempt.error_code == "brief_snapshot_stale"
    job = repository.get_job(job_id)
    assert job is not None
    assert job.status == "failed"


def test_cancel_pending_brief_job_restores_source_status(tmp_path: Path) -> None:
    """cancel pending brief Job 必须同步恢复 Source brief_status：pending Job 没有
    worker 处理，不能依赖安全点或 recover 兜底，否则 Source 永久卡在 pending/processing，
    前端 rebuild 按钮永久禁用。"""

    repository, source_id, snapshot_id = _setup(tmp_path)
    job = repository.create_job(
        JobCreateInput(
            kind="brief",
            queue="brief",
            source_id=source_id,
            snapshot_id=snapshot_id,
            stage="brief_pending",
        )
    )
    repository.update_source_state(source_id, brief_status="pending")

    canceled = repository.mark_canceled(job.id)
    assert canceled is not None
    assert canceled.status == "canceled"

    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "not_started"


def test_cancel_running_brief_job_recover_restores_source_status(
    tmp_path: Path,
) -> None:
    """worker 阻塞在 LLM 调用里到不了安全点时，cancel running brief Job 靠 recover
    兜底：recover 必须在终结 Attempt 的同时把 Source brief_status 从 processing
    恢复为 not_started，否则 Source 永久卡死、前端 rebuild 按钮永久禁用。"""

    repository, source_id, snapshot_id = _setup(tmp_path)
    attempt_id, job_id, _ = _create_attempt(repository, source_id, snapshot_id)
    assert repository.get_source(source_id).brief_status == "processing"  # type: ignore[union-attr]

    repository.mark_canceled(job_id)
    # running Job 进入 canceling，Source 仍 processing（等 recover 兜底）
    assert repository.get_source(source_id).brief_status == "processing"  # type: ignore[union-attr]

    repository.recover_stale_running_jobs(requeue=True)

    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "not_started"
    job = repository.get_job(job_id)
    assert job is not None
    assert job.status == "canceled"
    attempt = repository.get_brief_attempt(attempt_id)
    assert attempt is not None
    assert attempt.status == "failed"


def test_lease_expired_requeue_keeps_source_processing(tmp_path: Path) -> None:
    """lease expired 走 requeue 路径（非 cancel）：Job 回 pending 会被 tick_brief
    重新消费，Source 应保持 processing，recover 不能把它误恢复为 not_started。"""

    repository, source_id, snapshot_id = _setup(tmp_path)
    _, job_id, _ = _create_attempt(repository, source_id, snapshot_id)
    expired = datetime.now(timezone.utc) - timedelta(seconds=5)
    with repository._session_factory() as session:  # noqa: SLF001 - 故障注入
        row = session.get(KnowledgeJob, job_id)
        assert row is not None
        row.lease_expires_at = expired
        session.commit()

    repository.recover_stale_running_jobs(requeue=True)

    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "processing"
    job = repository.get_job(job_id)
    assert job is not None
    assert job.status == "pending"
    assert job.retry_count == 1  # requeue 递增计数，超限后转 failed


def test_lease_expired_requeue_exhausted_marks_failed_and_restores_source(
    tmp_path: Path,
) -> None:
    """lease expired 反复超过 BRIEF_LEASE_REQUEUE_MAX 后，Job 转终态 failed、
    source brief_status 恢复 not_started，不再无限 requeue 死循环。"""

    repository, source_id, snapshot_id = _setup(tmp_path)
    _, job_id, _ = _create_attempt(repository, source_id, snapshot_id)
    expired = datetime.now(timezone.utc) - timedelta(seconds=5)
    with repository._session_factory() as session:  # noqa: SLF001 - 故障注入
        row = session.get(KnowledgeJob, job_id)
        assert row is not None
        row.lease_expires_at = expired
        row.retry_count = BRIEF_LEASE_REQUEUE_MAX  # recover 再 +1 即超限
        session.commit()

    repository.recover_stale_running_jobs(requeue=True)

    job = repository.get_job(job_id)
    assert job is not None
    assert job.status == "failed"
    assert job.stage == "lease_requeue_exhausted"
    assert job.error_code == "job_lease_requeue_exhausted"
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "not_started"


def test_call_model_once_passes_timeout(tmp_path: Path) -> None:
    """_call_model_once 必须把 BRIEF_MODEL_TIMEOUT_SECONDS 传给 litellm，防止卡死
    调用无限阻塞到 lease 过期。"""

    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)

    captured: dict[str, object] = {}

    def _capture(**payload: object) -> dict[str, object]:
        captured.update(payload)
        return {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    # 走正式 __init__ 构造 BriefWorker，使其拥有完整 trace 初始状态
    # （_trace_phase / _trace_iteration / _trace_block_path），与生产调用路径一致。
    worker = BriefWorker(repository, Config(), model_client=_capture)
    profile = SimpleNamespace(
        id="test-provider",
        provider="openai_compatible",
        model="m",
        api_key="k",
        base_url="http://x/v1",
    )
    worker._call_model_once(profile, [{"role": "user", "content": "x"}])  # type: ignore[arg-type]
    assert captured["timeout"] == BRIEF_MODEL_TIMEOUT_SECONDS


def test_outer_lease_heartbeat_renews_and_stops(tmp_path: Path) -> None:
    """_outer_lease_heartbeat 在 with 块内周期续约 lease、退出后停止守护线程。"""

    repository, source_id, snapshot_id = _setup(tmp_path)
    _, job_id, token = _create_attempt(repository, source_id, snapshot_id)
    with repository._session_factory() as session:  # noqa: SLF001 - 故障注入
        row = session.get(KnowledgeJob, job_id)
        assert row is not None
        row.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=300)
        session.commit()

    renew_count = 0
    original = repository.heartbeat_job

    def _counting(job_id_: int, **kwargs: object) -> object:
        nonlocal renew_count
        renew_count += 1
        assert kwargs.get("lease_duration_seconds") == BRIEF_HEARTBEAT_LEASE_SECONDS
        return original(job_id_, **kwargs)  # type: ignore[arg-type]

    repository.heartbeat_job = _counting  # type: ignore[assignment]
    worker = BriefWorker(repository, Config(), heartbeat_interval_seconds=0.05)
    with worker._outer_lease_heartbeat(job_id, token):
        time.sleep(0.2)
    assert renew_count >= 2
