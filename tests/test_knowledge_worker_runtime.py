"""Knowledge Worker 运行时与 Bundle 重建的定向回归测试。"""

from __future__ import annotations

import io
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from offerpilot.config import Config
from offerpilot.db import init_database, session_factory_for_data_dir
from offerpilot.knowledge.assets import AssetInput
from offerpilot.knowledge.repository import KnowledgeRepository
from offerpilot.knowledge.runtime import KnowledgeWorkerRuntime
from offerpilot.knowledge.service import IngestRequest, KnowledgeIngestService
from offerpilot.knowledge.worker import ExtractionWorker
from offerpilot.models import KnowledgeJob


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (20, 30, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


class _RuntimeRepository:
    def __init__(self) -> None:
        self.recoveries = 0

    def recover_stale_running_jobs(self) -> list[int]:
        self.recoveries += 1
        return []


class _RuntimeRunner:
    def __init__(self) -> None:
        self.extraction_calls = 0
        self.brief_calls = 0

    def tick_extraction(self, **_: Any) -> list[Any]:
        self.extraction_calls += 1
        return []

    def tick_brief(self, **_: Any) -> list[Any]:
        self.brief_calls += 1
        return []


class _BlockingRuntimeRunner:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def tick_extraction(self, **_: Any) -> list[Any]:
        self.entered.set()
        self.release.wait(2.0)
        return []

    def tick_brief(self, **_: Any) -> list[Any]:
        self.entered.set()
        self.release.wait(2.0)
        return []


def test_runtime_starts_two_consumers_and_stops_cleanly() -> None:
    repository = _RuntimeRepository()
    runner = _RuntimeRunner()
    runtime = KnowledgeWorkerRuntime(
        runner,  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
        poll_interval_seconds=0.05,
    )

    runtime.start()
    deadline = time.monotonic() + 1.0
    while (
        (runner.extraction_calls == 0 or runner.brief_calls == 0)
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    runtime.stop(timeout=1.0)

    assert repository.recoveries >= 1
    assert runner.extraction_calls > 0
    assert runner.brief_calls > 0
    assert runtime.running is False


def test_runtime_stop_reports_timeout_and_waits_for_blocking_worker() -> None:
    repository = _RuntimeRepository()
    runner = _BlockingRuntimeRunner()
    runtime = KnowledgeWorkerRuntime(runner, repository)  # type: ignore[arg-type]

    runtime.start()
    assert runner.entered.wait(1.0)

    stopped = runtime.stop(timeout=0.01)

    assert stopped is False
    assert runtime.running is True

    runner.release.set()
    assert runtime.stop(timeout=1.0) is True
    assert runtime.running is False


def test_runtime_run_once_recovers_before_driving_both_queues() -> None:
    repository = _RuntimeRepository()
    runner = _RuntimeRunner()
    runtime = KnowledgeWorkerRuntime(runner, repository)  # type: ignore[arg-type]

    result = runtime.run_once()

    assert repository.recoveries == 1
    assert set(result) == {"extraction", "brief"}
    assert runner.extraction_calls == 1
    assert runner.brief_calls == 1


def test_runtime_requeues_expired_job_before_driving_queues(tmp_path: Path) -> None:
    """真实 Repository 必须把过期 running Job 放回 pending。"""

    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    with session_factory() as session:
        row = KnowledgeJob(
            kind="extract",
            queue="extraction",
            stage="extracting",
            status="running",
            attempt_token="expired-token",
            lease_owner="crashed-worker",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(row)
        session.commit()
        job_id = row.id

    runtime = KnowledgeWorkerRuntime(
        _RuntimeRunner(),  # type: ignore[arg-type]
        repository,
    )
    runtime.run_once()

    recovered = repository.get_job(job_id)
    assert recovered is not None
    assert recovered.status == "pending"
    assert recovered.stage == "recovered_pending"


def test_runtime_finalizes_canceled_running_job_after_restart(tmp_path: Path) -> None:
    """取消后进程崩溃不能留下永久 running/canceling Job。"""

    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    with session_factory() as session:
        row = KnowledgeJob(
            kind="extract",
            queue="extraction",
            stage="canceling",
            status="running",
            canceled=True,
            attempt_token="canceled-token",
            lease_owner="dead-worker",
            lease_expires_at=None,
        )
        session.add(row)
        session.commit()
        job_id = row.id

    runtime = KnowledgeWorkerRuntime(
        _RuntimeRunner(),  # type: ignore[arg-type]
        repository,
    )
    runtime.run_once()

    recovered = repository.get_job(job_id)
    assert recovered is not None
    assert recovered.status == "canceled"
    assert recovered.stage == "canceled"
    assert recovered.error_code == "job_canceled"


def test_database_restart_keeps_expired_job_requeueable(tmp_path: Path) -> None:
    """数据库初始化不能先把过期 Job 标成 failed，掩盖运行时恢复。"""

    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    with session_factory() as session:
        row = KnowledgeJob(
            kind="extract",
            queue="extraction",
            stage="extracting",
            status="running",
            attempt_token="expired-before-restart",
            lease_owner="dead-worker",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(row)
        session.commit()
        job_id = row.id

    init_database(tmp_path / "data.db")
    restarted = KnowledgeRepository(session_factory_for_data_dir(tmp_path)).get_job(job_id)
    assert restarted is not None
    assert restarted.status == "pending"
    assert restarted.stage == "recovered_pending"


def test_extraction_rejects_tampered_bundle_asset(tmp_path: Path) -> None:
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    service = KnowledgeIngestService(repository, tmp_path, session_factory, config=Config())
    main = b"# Bundle\n\n![logo](logo.png)\n"
    asset = _png_bytes()
    result = service.ingest(
        IngestRequest(
            filename="bundle.md",
            content_bytes=main,
            asset_inputs=(AssetInput(logical_name="logo.png", content_bytes=asset),),
        )
    )
    source = repository.get_source(result.source.id)
    assert source is not None
    asset_record = repository.list_assets(source.id)[0]
    asset_path = tmp_path / asset_record.relative_path
    asset_path.write_bytes(b"x" * len(asset))

    worker = ExtractionWorker(repository, tmp_path, session_factory)

    assert worker.verify_source_integrity(source) == "asset hash mismatch: logo.png"
