"""Knowledge Job Worker / Runner。

KI-07 范围：
- ``ExtractionWorker.execute`` 从持久队列消费一个 Extract Job，重新构建
  Snapshot/Evidence/FTS，并保留 Spec §6 / §9 的单事务提交语义。
- ``KnowledgeJobRunner`` 提供 ``tick_extraction`` / ``tick_brief`` / ``retry_extract``
  方法，按 Spec §12 单并发 FIFO 调度。不启动后台线程，由测试或未来 CLI 驱动。

KI-09 范围：
- ``BriefWorker.execute`` 实现 Brief generation + 程序校验 + 独立 Validator + 一次
  受约束修复 + 单事务提交当前 Brief。
- ``KnowledgeJobRunner.tick_brief`` 替换 KI-07 占位实现，从队列消费 brief Job 后
  委托给 ``BriefWorker``。

Spec §12 关键约束：
- Extraction queue 同时承载 Source 永久删除等本地维护 Job；Delete Job 由本类消费。
- Brief queue 并发固定为 1；与 Extraction queue 可以并行。
- 迟到的旧 lease 结果因 owner/Attempt 不匹配而拒绝提交（``complete_job`` 验证
  ``attempt_token``）。
- pending Job 立即 cancel；running 本地任务在安全点检查 ``is_job_canceled`` 并停止。
- 已发出的模型调用即使无法中止，其返回也不能在取消后提交（``complete_job`` 在
  ``status != running`` 时返回 ``False``）。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import random
import re
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Callable, Generator, Optional

from sqlalchemy.orm import Session, sessionmaker

from offerpilot.config import AIProviderProfile, Config
from offerpilot.knowledge.brief import (
    BRIEF_LANGUAGE,
    BRIEF_MIN_CONTEXT_WINDOW,
    BRIEF_PROMPT_VERSION,
    BRIEF_REPAIR_UNAUTHORIZED,
    BRIEF_SCHEMA_VERSION,
    BriefPayload,
    BriefSchemaError,
    ISSUE_CITATION_MISSING,
    ISSUE_CITATION_OWNERSHIP,
    ISSUE_COVERAGE_MISSING,
    ISSUE_VALIDATOR_PARSE_FAILED,
    SectionCoveragePlan,
    SUPPORT_DECISION_ISSUE_TYPE,
    ValidationIssue,
    _section_key_for_heading,
    apply_repair_patch,
    program_reason_for,
    redact_reason_echo,
    build_generation_prompt,
    build_repair_prompt,
    build_schema_repair_prompt,
    build_section_coverage_plan,
    build_validation_prompt,
    collect_brief_statement_blocks,
    parse_brief_payload,
    parse_repair_patch,
    parse_support_decision,
    validate_brief_against_evidence,
)
from offerpilot.knowledge.encoding import DecodedContent, decode_source_bytes
from offerpilot.knowledge.extractor import (
    EXTRACTOR_VERSION,
    METADATA_EXTRACTION_VERSION,
    NORMALIZATION_VERSION,
    PARSER_VERSION,
    MarkdownExtractor,
    compute_bundle_source_hash,
    compute_source_hash,
)
from offerpilot.knowledge.repository import (
    AssetCreateInput,
    BriefAttemptCreateInput,
    EvidenceDraftInput,
    EvidenceRecord,
    JobRecord,
    KnowledgeBriefAttemptError,
    KnowledgeRepository,
    SnapshotCreateInput,
    SourceRecord,
    commit_extraction,
)
from offerpilot.knowledge.provider import build_knowledge_brief_provider_client
from offerpilot.knowledge.tokenizer import max_token_limit
from offerpilot.models import KnowledgeJob, KnowledgeSource


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DecodedMarkdown(DecodedContent):
    """兼容旧类型名；KI-03 之后请直接使用 ``DecodedContent``。"""


def decode_markdown_bytes(raw_bytes: bytes) -> Optional[DecodedMarkdown]:
    """兼容 KI-02 接口的薄包装。新代码请直接使用 ``decode_source_bytes``。

    返回 ``None`` 表示编码识别失败；新 ``decode_source_bytes`` 会抛 ``EncodingError``。
    """

    try:
        result = decode_source_bytes(raw_bytes)
    except Exception:
        return None
    return DecodedMarkdown(
        text=result.text,
        encoding=result.encoding,
        detection_method=result.detection_method,
    )


@dataclass(frozen=True)
class JobExecutionResult:
    """``ExtractionWorker.execute`` 返回。

    ``accepted`` 表示 lease 仍有效且 Job 未取消，``complete_job`` 已被调用。
    ``rejected`` 表示 lease 已过期或 token 不匹配，worker 应停止。
    """

    job_id: int
    accepted: bool
    status: str
    error_code: str
    error_message: str


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """将 SQLite 返回的 naive 时间按 UTC 解释，避免 lease 判断抛出类型错误。"""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _resolve_data_path(
    data_dir: Path,
    relative_path: str,
    expected_dir: Optional[Path] = None,
) -> Optional[Path]:
    """解析 Source/Asset 路径，并拒绝绝对路径和目录穿越。

    ``expected_dir`` 用于把路径进一步限制在当前 Source（或其 assets 子目录）内；
    仅限制到 data_dir 会允许被篡改的 SQLite 路径指向另一个 Source 的原件。
    """

    candidate = Path(relative_path)
    if candidate.is_absolute():
        return None
    root = data_dir.resolve()
    resolved = (data_dir / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    if expected_dir is not None:
        try:
            resolved.relative_to(expected_dir.resolve())
        except (OSError, ValueError):
            return None
    return resolved


def _remove_path(path: Path) -> None:
    """删除 Worker 管辖的文件/目录而不跟随符号链接。"""

    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    except OSError:
        # SQLite 已提交后只能等待下一次启动恢复继续清理。
        pass


def _job_record_is_active(
    job: Optional[JobRecord],
    *,
    attempt_token: str,
    lease_owner: Optional[str] = None,
    now: Optional[datetime] = None,
) -> bool:
    """检查提交前的 Job lease/cancel 门禁。

    该检查用于 Worker 在提交事务之前快速拒绝迟到结果；真正的事务内检查在
    ``_commit_extraction_if_active`` 中再次执行。
    """

    if job is None or job.status != "running" or job.canceled:
        return False
    if job.attempt_token != attempt_token:
        return False
    if lease_owner is not None and job.lease_owner != lease_owner:
        return False
    expires_at = _as_utc(job.lease_expires_at)
    if expires_at is not None and expires_at <= (now or datetime.now(timezone.utc)):
        return False
    return True


class ExtractionWorker:
    """Spec §6 / §12：从队列消费 Extract Job，重新构建 Snapshot/Evidence/FTS。

    Extraction 由本类在持久队列中完成；本类还负责在成功提交后触发 Brief 入队。
    主要用途：
    1. KI-07 持久队列调度（``KnowledgeJobRunner.tick_extraction`` 调用）。
    2. 未来手动重试 / 恢复路径。
    3. 单元测试幂等性验证。
    """

    def __init__(
        self,
        repository: KnowledgeRepository,
        data_dir: Path,
        session_factory: sessionmaker[Session],
        extractor: Optional[MarkdownExtractor] = None,
        on_extraction_succeeded: Optional[Callable[[int], Any]] = None,
    ) -> None:
        self._repository = repository
        self._data_dir = data_dir
        self._session_factory = session_factory
        self._extractor = extractor or MarkdownExtractor()
        self._on_extraction_succeeded = on_extraction_succeeded

    def can_decode(self, source: "object") -> bool:
        source_id = getattr(source, "id", None)
        expected_dir = (
            self._data_dir / "knowledge" / "sources" / str(source_id)
            if source_id is not None
            else None
        )
        path = _resolve_data_path(
            self._data_dir,
            str(getattr(source, "main_relative_path", "")),
            expected_dir,
        )
        if path is None or not path.is_file():
            return False
        return decode_markdown_bytes(path.read_bytes()) is not None

    def verify_source_integrity(self, source: SourceRecord) -> Optional[str]:
        """Spec §6：Worker 每次读取正式 Source 时核验 manifest/hash。

        返回 ``None`` 表示通过；返回字符串为 ``source_integrity_mismatch`` 错误细节。
        校验项：
        - 主文件存在且大小匹配 ``total_bytes``。
        - manifest 中 ``source_hash`` 与重新计算的 hash 一致。
        """
        source_dir = self._data_dir / "knowledge" / "sources" / str(source.id)
        main_path = _resolve_data_path(
            self._data_dir,
            source.main_relative_path,
            source_dir,
        )
        if main_path is None:
            return "main file path is outside data directory"
        if not main_path.is_file():
            return "main file missing"
        raw_bytes = main_path.read_bytes()
        if len(raw_bytes) != source.total_bytes:
            return (
                f"main file size mismatch: expected {source.total_bytes}, "
                f"got {len(raw_bytes)}"
            )
        try:
            manifest = json.loads(source.manifest_json or "{}")
        except json.JSONDecodeError:
            manifest = {}
        expected_hash = str(manifest.get("source_hash") or "")
        if not expected_hash:
            return "manifest missing source_hash"

        bundle = manifest.get("bundle")
        if isinstance(bundle, dict):
            # Bundle hash 必须覆盖主文件、每个附件的原始字节和逻辑路径。
            # 仅比较主文件会让附件被替换后仍被错误地视为完整 Source。
            raw_assets = self._repository.list_assets(source.id)
            expected_names = bundle.get("asset_logical_names")
            if not isinstance(expected_names, list):
                return "bundle manifest missing asset names"
            expected_name_set = {str(name) for name in expected_names}
            actual_name_set = {asset.logical_name for asset in raw_assets}
            if actual_name_set != expected_name_set:
                return "bundle asset manifest mismatch"
            assets: list[tuple[str, bytes]] = []
            for asset in raw_assets:
                asset_path = _resolve_data_path(
                    self._data_dir,
                    asset.relative_path,
                    source_dir / "assets",
                )
                if asset_path is None:
                    return f"asset path is outside data directory: {asset.logical_name}"
                if not asset_path.is_file():
                    return f"asset file missing: {asset.logical_name}"
                content = asset_path.read_bytes()
                if len(content) != asset.bytes_size:
                    return f"asset size mismatch: {asset.logical_name}"
                digest = hashlib.sha256(content).hexdigest()
                if digest != asset.sha256:
                    return f"asset hash mismatch: {asset.logical_name}"
                assets.append((asset.logical_name, content))
            actual_hash = compute_bundle_source_hash(raw_bytes, assets)
        else:
            actual_hash = compute_source_hash(raw_bytes)
        if actual_hash != expected_hash:
            return (
                f"source_hash mismatch: expected {expected_hash}, got {actual_hash}"
            )
        return None

    def _mark_extraction_failed(
        self, source: SourceRecord, *, code: str, message: str
    ) -> None:
        """首次 Extraction 失败时同步暴露 Source 错误。

        已有 active Snapshot 的重试失败必须保留 ``extracted`` 和旧 Evidence；只有
        尚无可用 Snapshot 的 Source 才切换为 ``failed``。
        """
        if source.extraction_status == "extracted" and source.active_snapshot_id is not None:
            return
        self._repository.update_source_state(
            source.id,
            extraction_status="failed",
            extraction_error_code=code,
            extraction_error_message=message[:500],
        )

    def _asset_inputs_for_source(
        self, source: SourceRecord
    ) -> tuple[AssetCreateInput, ...]:
        """从不可变 Asset 元数据构造 Snapshot 提交所需的输入。

        Asset 行由首次 ingest 创建，重建时只能复用其逻辑路径和 hash，不能从
        Worker 重新推导展示路径或创建第二份文件。
        """

        manifest = json.loads(source.manifest_json or "{}")
        if not isinstance(manifest.get("bundle"), dict):
            return ()
        records = self._repository.list_assets(source.id)
        return tuple(
            AssetCreateInput(
                logical_name=asset.logical_name,
                media_type=asset.media_type,
                relative_path=asset.relative_path,
                bytes_size=asset.bytes_size,
                sha256=asset.sha256,
                width=asset.width,
                height=asset.height,
            )
            for asset in records
        )

    def _commit_extraction_if_active(
        self,
        *,
        job: JobRecord,
        attempt_token: str,
        lease_owner: str,
        snapshot_input: SnapshotCreateInput,
        evidence_drafts: list[EvidenceDraftInput],
        source_title: str,
        asset_inputs: tuple[AssetCreateInput, ...],
    ) -> bool:
        """在同一事务内复核 lease/cancel 后提交 Extraction 产物。

        ``complete_job`` 只能在产物提交之后验证 token，单独调用会留下“取消后仍
        发布产物”的窗口。这里直接读取 Job/Source 行并把校验与
        ``commit_extraction`` 放进同一个事务，避免该窗口。
        """

        moment = datetime.now(timezone.utc)
        with self._session_factory() as session:
            with session.begin():
                job_row = session.get(KnowledgeJob, job.id)
                if job_row is None:
                    return False
                job_view = JobRecord(
                    id=job_row.id,
                    kind=job_row.kind,
                    queue=job_row.queue,
                    source_id=job_row.source_id,
                    snapshot_id=job_row.snapshot_id,
                    stage=job_row.stage,
                    status=job_row.status,
                    progress=job_row.progress,
                    retry_count=job_row.retry_count,
                    next_retry_at=job_row.next_retry_at,
                    error_code=job_row.error_code,
                    error_message=job_row.error_message,
                    canceled=bool(job_row.canceled),
                    lease_owner=job_row.lease_owner,
                    lease_expires_at=job_row.lease_expires_at,
                    heartbeat_at=job_row.heartbeat_at,
                    attempt_token=job_row.attempt_token,
                    created_at=job_row.created_at,
                    updated_at=job_row.updated_at,
                )
                if not _job_record_is_active(
                    job_view,
                    attempt_token=attempt_token,
                    lease_owner=lease_owner,
                    now=moment,
                ):
                    return False
                source_row = session.get(KnowledgeSource, snapshot_input.source_id)
                if source_row is None or source_row.lifecycle == "deleting":
                    return False
                commit_extraction(
                    session,
                    snapshot_input=snapshot_input,
                    evidence_drafts=evidence_drafts,
                    source_id=snapshot_input.source_id,
                    source_title=source_title,
                    extractor_version=EXTRACTOR_VERSION,
                    asset_inputs=asset_inputs,
                )
                # Evidence 提交与 Extraction Job 终态必须属于同一事务。若先提交
                # Evidence、再单独 complete_job，进程可能在两次事务之间崩溃，留下
                # 可检索产物但仍是 running 的 Job，重启后会重复 claim。
                job_row.status = "succeeded"
                job_row.stage = "extracted"
                job_row.progress = 100
                job_row.error_code = ""
                job_row.error_message = ""
                job_row.lease_expires_at = None
                job_row.updated_at = moment
        return True

    def _execute_delete(
        self,
        job: JobRecord,
        *,
        attempt_token: str,
        lease_owner: str,
    ) -> JobExecutionResult:
        """消费 Delete Job，完成 quarantine → SQLite → 物理清理协议。

        文件系统 rename 与 SQLite 提交无法共享一个事务，因此每个顺序点都必须可由
        启动恢复重做：rename 前崩溃由 deleting Source 重试，提交后崩溃由孤儿
        quarantine 清理。请求线程不调用此方法。
        """

        if not _job_record_is_active(
            job, attempt_token=attempt_token, lease_owner=lease_owner
        ):
            return JobExecutionResult(
                job_id=job.id,
                accepted=False,
                status=job.status,
                error_code="job_lease_mismatch",
                error_message="Delete Job lease 已失效",
            )
        source_id = job.source_id
        if source_id is None:
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=attempt_token,
                status="failed",
                stage="source_missing",
                error_code="source_integrity_mismatch",
                error_message="Delete Job 缺少 source_id",
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="failed",
                error_code="source_integrity_mismatch",
                error_message="Delete Job 缺少 source_id",
            )

        source = self._repository.get_deleting_source(source_id)
        if source is None:
            # 启动恢复可能已经完成了同一删除并清理了 Source/Job。此时不能再写入
            # 任何新行，也不能把一个已完成的删除报告为普通 Source 缺失错误。
            return JobExecutionResult(
                job_id=job.id,
                accepted=True,
                status="succeeded",
                error_code="",
                error_message="",
            )

        knowledge_root = self._data_dir / "knowledge"
        source_dir = knowledge_root / "sources" / str(source_id)
        sources_root = knowledge_root / "sources"
        quarantine_root = knowledge_root / "quarantine"
        quarantine_dir = quarantine_root / str(source_id)
        try:
            if knowledge_root.is_symlink() or sources_root.is_symlink():
                raise OSError("Source 根目录不能是符号链接")
            quarantine_root.mkdir(parents=True, exist_ok=True)
            if quarantine_root.is_symlink():
                raise OSError("quarantine 根目录不能是符号链接")
            if source_dir.is_symlink() or quarantine_dir.is_symlink():
                raise OSError("Source/quarantine 目录不能是符号链接")
            if not quarantine_dir.exists() and source_dir.exists():
                # 两个目录位于同一 data_dir 下，Path.replace 使用原子 rename；不使用
                # copy+unlink，避免删除事务期间出现半份原件。
                source_dir.replace(quarantine_dir)
        except OSError as exc:
            self._repository.update_job(
                job.id,
                stage="quarantine_retry",
                error_code="source_filesystem_error",
                error_message=str(exc)[:500],
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=True,
                status="running",
                error_code="source_filesystem_error",
                error_message=str(exc)[:500],
            )

        # rename 后可能正好跨过 lease 到期或被外部恢复路径接管；失去 lease 的
        # Worker 不能继续提交 SQLite 删除事务。quarantine 保留给下一次恢复重试。
        if not _job_record_is_active(
            self._repository.get_job(job.id),
            attempt_token=attempt_token,
            lease_owner=lease_owner,
        ):
            return JobExecutionResult(
                job_id=job.id,
                accepted=False,
                status="failed",
                error_code="job_lease_mismatch",
                error_message="Delete Job 在提交前 lease 已失效",
            )

        try:
            committed = self._repository.complete_purge(source_id)
        except Exception as exc:  # noqa: BLE001 - 下次 lease/启动恢复重试
            self._repository.update_job(
                job.id,
                stage="purge_retry",
                error_code="delete_commit_failed",
                error_message=str(exc)[:500],
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=True,
                status="running",
                error_code="delete_commit_failed",
                error_message=str(exc)[:500],
            )

        if not committed:
            # 另一个恢复路径已经删除了 DB 行；删除请求本身仍然已经完成。
            committed = True

        # SQLite 已提交后物理删除失败不能回滚数据库；启动恢复会继续清理孤儿目录。
        for path in (quarantine_dir, source_dir):
            _remove_path(path)
        return JobExecutionResult(
            job_id=job.id,
            accepted=True,
            status="succeeded",
            error_code="",
            error_message="",
        )

    def execute(
        self,
        job: JobRecord,
        *,
        attempt_token: str,
        lease_owner: str,
    ) -> JobExecutionResult:
        """执行一个 Extract Job。

        步骤：
        1. 验证 attempt_token 与 lease_owner 匹配（迟到 lease 拒绝）。
        2. 检查 canceled。
        3. 读取 Source + 校验 manifest/hash。
        4. 重新执行 Extraction（commit_extraction 幂等）。
        5. complete_job(status=succeeded | failed)。
        """
        if job.attempt_token != attempt_token or job.lease_owner != lease_owner:
            return JobExecutionResult(
                job_id=job.id,
                accepted=False,
                status=job.status,
                error_code="job_lease_mismatch",
                error_message="attempt_token or lease_owner mismatch",
            )
        if job.status != "running":
            return JobExecutionResult(
                job_id=job.id,
                accepted=False,
                status=job.status,
                error_code="job_not_running",
                error_message=f"job status is {job.status}, expected running",
            )
        if job.kind == "delete":
            return self._execute_delete(
                job,
                attempt_token=attempt_token,
                lease_owner=lease_owner,
            )
        if self._repository.is_job_canceled(job.id):
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=attempt_token,
                status="canceled",
                stage="canceled",
                error_code="job_canceled",
                error_message="用户取消",
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="canceled",
                error_code="job_canceled",
                error_message="用户取消",
            )

        source = self._repository.get_source(job.source_id or 0)
        if source is None:
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=attempt_token,
                status="failed",
                stage="source_missing",
                error_code="source_integrity_mismatch",
                error_message="Source 不存在或已被删除",
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="failed",
                error_code="source_integrity_mismatch",
                error_message="Source 不存在或已被删除",
            )

        integrity_error = self.verify_source_integrity(source)
        if integrity_error is not None:
            self._mark_extraction_failed(
                source, code="source_integrity_mismatch", message=integrity_error
            )
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=attempt_token,
                status="failed",
                stage="integrity_check",
                error_code="source_integrity_mismatch",
                error_message=integrity_error,
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="failed",
                error_code="source_integrity_mismatch",
                error_message=integrity_error,
            )

        try:
            main_path = _resolve_data_path(
                self._data_dir,
                source.main_relative_path,
                self._data_dir / "knowledge" / "sources" / str(source.id),
            )
            if main_path is None:
                raise ValueError("main file path is outside data directory")
            raw_bytes = main_path.read_bytes()
            decoded = decode_source_bytes(raw_bytes)
            # Spec KBR-03：把确定性来源信号（首条 Origin 的 origin_url、主文件名扩展名）
            # 传入 extractor，供 select_adapters 选择平台适配器。web-article adapter 仅在
            # ingest origin_url 非空时激活；Obsidian/Evernote 由 extractor 扫描结构语法判定。
            origins = self._repository.list_origins(source.id)
            origin_url = origins[0].origin_url if origins else ""
            extraction = self._extractor.extract(
                decoded.text,
                encoding=decoded.encoding,
                detection_method=decoded.detection_method,
                origin_url=origin_url,
                filename=source.main_filename,
            )
        except Exception as exc:
            self._mark_extraction_failed(
                source, code="extraction_failed", message=str(exc)
            )
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=attempt_token,
                status="failed",
                stage="extraction_failed",
                error_code="extraction_failed",
                error_message=str(exc)[:500],
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="failed",
                error_code="extraction_failed",
                error_message=str(exc)[:500],
            )

        if self._repository.is_job_canceled(job.id):
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=attempt_token,
                status="canceled",
                stage="canceled",
                error_code="job_canceled",
                error_message="用户在 Extraction 后取消",
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="canceled",
                error_code="job_canceled",
                error_message="用户在 Extraction 后取消",
            )

        token_count_value = extraction.token_count
        token_limit = max_token_limit()
        if token_count_value > token_limit:
            self._mark_extraction_failed(
                source,
                code="source_too_large",
                message=(
                    f"原文 {token_count_value} tokens 超出上限 {token_limit} tokens"
                ),
            )
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=attempt_token,
                status="failed",
                stage="token_limit",
                error_code="source_too_large",
                error_message=(
                    f"原文 {token_count_value} tokens 超出上限 {token_limit} tokens"
                ),
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="failed",
                error_code="source_too_large",
                error_message=(
                    f"原文 {token_count_value} tokens 超出上限 {token_limit} tokens"
                ),
            )

        drafts = [
            EvidenceDraftInput(
                block_kind=draft.block_kind,
                heading_path=tuple(draft.heading_path),
                char_start=draft.char_start,
                char_end=draft.char_end,
                line_start=draft.line_start,
                line_end=draft.line_end,
                canonical_excerpt=draft.canonical_excerpt,
                search_text=draft.search_text,
                content_hash=draft.content_hash,
                locator=draft.locator,
                kind="asset" if draft.block_kind == "image" else "text",
                logical_name=str(draft.extra.get("logical_name", "")),
                alt_text=str(draft.extra.get("alt_text", "")),
            )
            for draft in extraction.evidence_drafts
        ]
        snapshot_input = SnapshotCreateInput(
            source_id=source.id,
            extractor_version=EXTRACTOR_VERSION,
            parser_version=PARSER_VERSION,
            normalization_version=NORMALIZATION_VERSION,
            tokenizer_version=extraction.tokenizer_version,
            encoding=extraction.encoding,
            detection_method=extraction.detection_method,
            canonical_text=extraction.canonical_text,
            structure_manifest=extraction.structure_manifest,
            digest=extraction.digest,
            token_count=token_count_value,
            char_count=extraction.char_count,
            metadata_extraction_version=METADATA_EXTRACTION_VERSION,
            provenance_title=extraction.provenance.title,
            provenance_author=extraction.provenance.author,
            provenance_url=extraction.provenance.url,
            provenance_published_at=extraction.provenance.published_at,
        )
        # Spec KBR-02：单字段非法只忽略+安全警告。warnings 只含字段名与原因，不含
        # Source 正文，符合隐私边界（普通日志不打印 Source/Evidence 正文）。
        if extraction.provenance.warnings:
            _LOGGER.warning(
                "knowledge source %s provenance fields ignored: %s",
                source.id,
                "; ".join(extraction.provenance.warnings),
            )
        title_for_search = source.display_title or source.title_hint or source.main_filename
        try:
            asset_inputs = self._asset_inputs_for_source(source)
            # 主文件或附件可能在 Extraction 期间被外部进程替换；提交前再次校验
            # 完整 manifest，避免把已漂移的 Bundle 写成新的 active Snapshot。
            integrity_error = self.verify_source_integrity(source)
            if integrity_error is not None:
                raise ValueError(integrity_error)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._mark_extraction_failed(
                source, code="source_integrity_mismatch", message=str(exc)
            )
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=attempt_token,
                status="failed",
                stage="integrity_check",
                error_code="source_integrity_mismatch",
                error_message=str(exc)[:500],
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="failed",
                error_code="source_integrity_mismatch",
                error_message=str(exc)[:500],
            )
        try:
            committed = self._commit_extraction_if_active(
                job=job,
                attempt_token=attempt_token,
                lease_owner=lease_owner,
                snapshot_input=snapshot_input,
                evidence_drafts=drafts,
                source_title=title_for_search,
                asset_inputs=asset_inputs,
            )
            if not committed:
                error_code = (
                    "job_canceled"
                    if self._repository.is_job_canceled(job.id)
                    else "job_lease_mismatch"
                )
                status = "canceled" if error_code == "job_canceled" else "failed"
                ok, _ = self._repository.complete_job(
                    job.id,
                    attempt_token=attempt_token,
                    status=status,
                    stage="canceled" if status == "canceled" else "commit_conflict",
                    error_code=error_code,
                    error_message=(
                        "用户取消" if status == "canceled" else "提交时 lease 已失效"
                    ),
                )
                return JobExecutionResult(
                    job_id=job.id,
                    accepted=ok,
                    status=status,
                    error_code=error_code,
                    error_message=(
                        "用户取消" if status == "canceled" else "提交时 lease 已失效"
                    ),
                )
        except Exception as exc:
            self._mark_extraction_failed(
                source, code="extraction_failed", message=str(exc)
            )
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=attempt_token,
                status="failed",
                stage="commit_failed",
                error_code="extraction_failed",
                error_message=str(exc)[:500],
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="failed",
                error_code="extraction_failed",
                error_message=str(exc)[:500],
            )

        # ``_commit_extraction_if_active`` 已在同一事务内将 Job 标记 succeeded；
        # 这里不能再次开启独立事务，否则会重新引入提交窗口。
        ok = True
        if self._on_extraction_succeeded is not None and job.source_id is not None:
            try:
                self._on_extraction_succeeded(job.source_id)
            except Exception as exc:  # noqa: BLE001 - Brief 入队失败需持久化可见
                self._repository.update_source_state(
                    job.source_id,
                    brief_status="failed",
                    brief_block_reason="",
                    brief_error_code="brief_enqueue_failed",
                    brief_error_message=str(exc)[:500] or "Brief Job 入队失败",
                )
        return JobExecutionResult(
            job_id=job.id,
            accepted=ok,
            status="succeeded",
            error_code="",
            error_message="",
        )


def _hash_main_bytes(raw_bytes: bytes) -> str:
    """保留旧内部调用方的单文件 hash 兼容函数。"""

    return compute_source_hash(raw_bytes)


# ---------------------------------------------------------------------------
# KI-09 Brief generation / validation
# ---------------------------------------------------------------------------


BriefModelClient = Callable[..., Any]

# KI-10 / Spec §11.4：Provider 层重试与退避参数。
# 每个 Provider 最多调用 3 次（首次 + 2 次自动重试）；退避优先 Retry-After，否则 2s/10s。
BRIEF_PROVIDER_MAX_ATTEMPTS = 3
BRIEF_RETRY_BACKOFF_SECONDS = (2.0, 10.0)
BRIEF_RETRY_MAX_DELAY_SECONDS = 60.0  # Retry-After 上限，避免 Provider 要求过长等待

# Spec §13 / §11.4：Brief Provider 错误归类。permanent（auth/model）不重试不切 fallback；
# transient（timeout/ratelimit/5xx）重试且可在耗尽后切 fallback。内容质量错误
# （brief_schema_invalid 等）由程序校验产生，不走 Provider 重试/failover 路径。
BRIEF_PROVIDER_TRANSIENT_ERRORS = frozenset({"provider_transient_error"})

# Brief 单次模型调用超时（秒）。litellm 默认 600s 过长——配合后台 heartbeat，
# 超过此值的卡死调用转为 transient error 走 retry/failover，而非无限阻塞到 lease 过期。
BRIEF_MODEL_TIMEOUT_SECONDS = 270
# 后台 heartbeat：LLM 同步阻塞调用期间每 N 秒续约外层 queue lease，防止单次
# generation/validator 调用超过 lease 时长被并行 recover 回收。
BRIEF_HEARTBEAT_INTERVAL_SECONDS = 30.0
BRIEF_HEARTBEAT_LEASE_SECONDS = 120


def _is_transient(error_code: str) -> bool:
    return error_code in BRIEF_PROVIDER_TRANSIENT_ERRORS


def _extract_retry_after_seconds(exc: BaseException) -> Optional[float]:
    """Spec §11.4 优先使用 Retry-After。

    litellm 异常族未统一导出响应头，尝试常见属性；解析失败返回 ``None``。
    """
    for attr in ("retry_after", "response_headers", "headers"):
        value = getattr(exc, attr, None)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return float(max(0.0, value))
        candidate: Optional[str] = None
        if isinstance(value, dict):
            for key in ("Retry-After", "retry-after", "RetryAfter"):
                if key in value:
                    candidate = value[key]
                    break
        elif isinstance(value, str):
            candidate = value
        if candidate is None:
            continue
        try:
            return float(max(0.0, float(candidate)))
        except (TypeError, ValueError):
            return None
    return None


def _retry_delay_seconds(exc: BaseException, attempt_index: int) -> float:
    """Spec §11.4：Retry-After 优先；否则 2s/10s 退避 + 少量抖动。

    ``attempt_index`` 从 1 开始（即将进行的第几次重试）。
    """
    retry_after = _extract_retry_after_seconds(exc)
    if retry_after is not None:
        return min(retry_after, BRIEF_RETRY_MAX_DELAY_SECONDS)
    # attempt_index 1 → 第 1 次重试退避 2s；attempt_index 2 → 10s。
    idx = max(1, min(attempt_index, len(BRIEF_RETRY_BACKOFF_SECONDS)))
    base = BRIEF_RETRY_BACKOFF_SECONDS[idx - 1]
    jitter = random.uniform(0.0, 0.25)
    return base + jitter


def _provider_label(provider: AIProviderProfile) -> str:
    return f"{provider.id}/{provider.model}"


@dataclass(frozen=True)
class BriefGenerationResult:
    """``BriefWorker.execute`` 单次 generation 返回。"""

    payload: BriefPayload
    validation_report_json: str
    support_results: list[dict[str, Any]]
    token_input_count: int
    token_output_count: int
    latency_ms: int
    # KI-10 / Spec §11.3：实际成功 Provider（可能为 fallback）与 Provider 层重试总次数。
    actual_provider_id: str = ""
    actual_provider_model: str = ""
    provider_retry_count: int = 0
    # Spec §10.3：到达当前 Brief 所经历的 repair 次数（含 support repair）。
    repair_count: int = 0


@dataclass(frozen=True)
class BriefQualityOutcome:
    """KBR-05 汇总质量校验结果。

    Schema 合法的候选先完成全部 citation（per-block）、support（citation 有效 block 逐条）
    与 coverage 检查，再合并成统一 ``issues``；repair 输入与 Attempt 详情共用该结构。
    """

    issues: list[ValidationIssue]
    support_results: list[dict[str, Any]]
    coverage_statuses: list[Any]
    programmatic_issues: list[str]


@dataclass
class _RetryState:
    """Spec §11.4 Provider 层重试进度的可变累积器。

    ``total_retries`` 跨 primary/fallback 与 generation/validation 累计，持久化到
    Attempt.provider_retry_count，保证重启后不从零开始。实际成功 Provider 由
    ``_run_generation_with_repair`` 单独跟踪并传给 validation 与 commit。
    """

    attempt_id: int
    total_retries: int = 0
    next_retry_at: Optional[datetime] = None
    last_error_code: str = ""

    def bump(
        self,
        repository: KnowledgeRepository,
        *,
        error_code: str,
        error_message: str,
        delay_seconds: float,
        sleeper: Callable[[float], None],
    ) -> None:
        """持久化重试进度并执行退避 sleep。"""
        self.total_retries += 1
        self.last_error_code = error_code
        self.next_retry_at = datetime.now(timezone.utc) + timedelta(
            seconds=delay_seconds
        )
        repository.bump_brief_attempt_retry(
            self.attempt_id,
            provider_retry_count=self.total_retries,
            next_retry_at=self.next_retry_at,
            error_code=error_code,
            error_message=error_message,
        )
        sleeper(delay_seconds)


class BriefWorker:
    """Spec §10 / §11.1 / §10.3 / §11.3 / §11.4：单 Source Brief 生成与校验。

    KI-09 已交付：固定 JSON Schema、单次 generation + 一次 repair、程序校验、
    独立 Validator、单事务提交当前 Brief。

    KI-10 在此之上：
    - Provider 层重试（Spec §11.4）：每个 Provider 最多 3 次，只重试 transient，
      Retry-After 优先，否则 2s/10s 退避；重试计数与 next_retry_at 持久化。
    - fallback（Spec §11.3）：只有 transient 基础设施失败切到已配置 fallback；
      鉴权 / 模型不存在 / 上下文超限 / 非法 JSON / 内容质量失败不切。
    - 重建失败保留旧 Brief（Spec §10.4）。
    - Provider/Prompt/Schema/Snapshot 变化由 service 层标记 outdated，本 worker
      不自动重建。
    """

    def __init__(
        self,
        repository: KnowledgeRepository,
        config: Config,
        *,
        model_client: Optional[BriefModelClient] = None,
        sleeper: Optional[Callable[[float], None]] = None,
        heartbeat_interval_seconds: float = BRIEF_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self._repository = repository
        self._config = config
        self._model_client = (
            model_client or build_knowledge_brief_provider_client().complete_once
        )
        # Spec §11.4 退避 sleep；测试注入同步 no-op 以避免真实等待。
        self._sleep = sleeper or time.sleep
        # heartbeat 间隔，测试注入小值避免真实 30s 等待。
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        # 当前 Attempt 的观测上下文；未处于 Brief 执行时保持 None，不影响旧调用方。
        self._trace_attempt_id: Optional[int] = None
        self._trace_iteration = 0
        self._trace_phase = ""
        self._trace_block_path = ""
        self._trace_evidence_ids: list[str] = []

    def _set_trace_context(
        self,
        *,
        phase: str,
        iteration: int = 0,
        block_path: str = "",
        evidence_ids: Optional[list[str]] = None,
    ) -> None:
        self._trace_phase = phase
        self._trace_iteration = iteration
        self._trace_block_path = block_path
        self._trace_evidence_ids = list(evidence_ids or [])

    def _append_attempt_step(self, **kwargs: Any) -> None:
        """写入过程记录；旧 Repository 替身或日志故障不阻断 Brief 主流程。"""
        attempt_id = self._trace_attempt_id
        append = getattr(self._repository, "append_brief_attempt_step", None)
        if attempt_id is None or not callable(append):
            return
        try:
            append(attempt_id, **kwargs)
        except Exception:  # noqa: BLE001 - 可观测性失败不能改变业务结果
            _LOGGER.warning("Brief Attempt step append failed", exc_info=True)

    def _append_attempt_terminal_failure(
        self,
        *,
        error_code: str,
        error_message: str,
        iteration: int = 0,
        retry_count: int = 0,
    ) -> None:
        """为取消/lease 等 worker 外层失败补齐可回放终态。"""
        self._append_attempt_step(
            phase="attempt_failed",
            iteration=iteration,
            status="failed",
            output={"status": "failed", "error_code": error_code},
            retry_count=retry_count,
            error_code=error_code,
            error_message=error_message[:500],
        )
        self._append_attempt_step(
            phase="final",
            iteration=iteration,
            status="failed",
            output={"status": "failed", "error_code": error_code},
            retry_count=retry_count,
            error_code=error_code,
            error_message=error_message[:500],
        )

    def update_config(self, config: Config) -> None:
        """同步设置更新后的 Provider 配置，不重建运行时线程。"""

        self._config = config

    # ------------------------------------------------------------------
    # Provider 适配
    # ------------------------------------------------------------------

    def _candidate_provider(
        self, profile: Optional[AIProviderProfile]
    ) -> Optional[AIProviderProfile]:
        """Spec §4.2 / §11.2：校验单个 profile 是否满足 96K context 与启用状态。

        ``context_window == 0`` 视为未知，不得根据模型名称猜测；不满足直接淘汰。
        """
        if profile is None or not profile.enabled or not profile.api_key:
            return None
        if profile.context_window < BRIEF_MIN_CONTEXT_WINDOW:
            return None
        return profile

    def resolve_provider(self) -> Optional[AIProviderProfile]:
        """Spec §4.2 / §11.2：返回满足 96K context 的 active Provider（primary）。

        - ``context_window == 0`` 视为未知，按 Spec §4.2 不得根据模型名称猜测。
        - ``context_window < BRIEF_MIN_CONTEXT_WINDOW`` 视为不足窗口，拒绝发出请求。
        """
        return self._candidate_provider(self._config.active_provider())

    def resolve_fallback_provider(self) -> Optional[AIProviderProfile]:
        """Spec §11.3：返回满足 96K context 的 fallback Provider 候选。

        fallback 必须与 active 不同；否则返回 ``None``，避免 self-failover。
        """
        fallback = self._config.fallback_provider()
        candidate = self._candidate_provider(fallback)
        if candidate is None:
            return None
        active = self._config.active_provider()
        if active is not None and candidate.id == active.id:
            return None
        return candidate

    def provider_block_reason(self) -> str:
        """Spec §11.2：active 与 fallback 都不满足 96K 时返回稳定 block reason。"""
        primary = self.resolve_provider()
        fallback = self.resolve_fallback_provider()
        if primary is not None or fallback is not None:
            return ""
        # 两者均不可用：区分"未配置 api_key"与"上下文窗口不足"。
        active = self._config.active_provider()
        any_api_key = bool(active and active.api_key) or any(
            profile.api_key for profile in self._config.provider_profiles()
        )
        if not any_api_key:
            return "provider_unavailable"
        return "provider_context_too_small"

    def _refresh_outer_lease(self, job: JobRecord) -> bool:
        """在 Brief 长调用前复核并延长外层 queue Job lease。"""

        current = self._repository.get_job(job.id)
        if not _job_record_is_active(
            current,
            attempt_token=job.attempt_token,
            lease_owner=job.lease_owner,
        ):
            return False
        refreshed = self._repository.heartbeat_job(
            job.id,
            attempt_token=job.attempt_token,
            lease_duration_seconds=120,
        )
        return _job_record_is_active(
            refreshed,
            attempt_token=job.attempt_token,
            lease_owner=job.lease_owner,
        )

    def _outer_lease_active(self, job_id: int, attempt_token: str) -> bool:
        return _job_record_is_active(
            self._repository.get_job(job_id),
            attempt_token=attempt_token,
        )

    def _renew_outer_lease(self, job_id: int, attempt_token: str) -> bool:
        if not self._outer_lease_active(job_id, attempt_token):
            return False
        refreshed = self._repository.heartbeat_job(
            job_id,
            attempt_token=attempt_token,
            lease_duration_seconds=120,
        )
        return _job_record_is_active(refreshed, attempt_token=attempt_token)

    @contextmanager
    def _outer_lease_heartbeat(
        self, job_id: int, attempt_token: str
    ) -> Generator[None, None, None]:
        """在 Brief generation/validator 长调用期间周期续约外层 queue lease。

        litellm 同步阻塞调用期间主线程无法到达安全点续约，lease 会在时长到达后被
        并行 recover 回收（标记 failed、attempt 终结）。本守护线程每
        ``BRIEF_HEARTBEAT_INTERVAL_SECONDS`` 续约一次，让 lease 扛住任意长的模型调用。
        主线程在安全点仍会检测 cancel/lease 失效并退出，二者不冲突。
        """

        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(job_id, attempt_token, stop_event),
            name=f"brief-heartbeat-{job_id}",
            daemon=True,
        )
        thread.start()
        try:
            yield
        finally:
            stop_event.set()
            thread.join(timeout=5.0)

    def _heartbeat_loop(
        self, job_id: int, attempt_token: str, stop_event: threading.Event
    ) -> None:
        """守护线程主体：周期续约 lease，失效或取消时自行退出。"""

        while not stop_event.wait(self._heartbeat_interval_seconds):
            try:
                refreshed = self._repository.heartbeat_job(
                    job_id,
                    attempt_token=attempt_token,
                    lease_duration_seconds=BRIEF_HEARTBEAT_LEASE_SECONDS,
                )
            except Exception:  # noqa: BLE001 - heartbeat 失败不杀主流程
                _LOGGER.debug(
                    "Brief heartbeat failed for job %s", job_id, exc_info=True
                )
                continue
            if refreshed is None:
                # lease 已失效或 job 被 cancel/抢走，停止续约；主线程安全点会处理退出。
                return

    # ------------------------------------------------------------------
    # 主入口：消费一个 brief Job
    # ------------------------------------------------------------------

    def execute(self, job: JobRecord) -> JobExecutionResult:
        """Spec §10.3 / §10.4：从队列消费一个 brief Job。

        步骤：
        1. 校验 Job 来源与 Source 状态。
        2. 校验 Provider 96K context（不满足直接 failed + block reason）。
        3. 调用 generation；通过 Schema/citation/coverage/support 全部门禁后单事务提交。
        4. 失败一次允许一次受约束修复；第二次失败标记 Attempt failed。
        """
        if job.kind != "brief":
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=job.attempt_token,
                status="failed",
                stage="brief_kind_mismatch",
                error_code="brief_kind_mismatch",
                error_message=f"Job kind={job.kind} 不是 brief",
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="failed",
                error_code="brief_kind_mismatch",
                error_message=f"Job kind={job.kind} 不是 brief",
            )
        if not self._refresh_outer_lease(job):
            canceled = self._repository.is_job_canceled(job.id)
            error_code = "job_canceled" if canceled else "job_lease_mismatch"
            return JobExecutionResult(
                job_id=job.id,
                accepted=False,
                status="canceled" if canceled else "failed",
                error_code=error_code,
                error_message="用户取消" if canceled else "Brief queue lease 已失效",
            )
        if self._repository.is_job_canceled(job.id):
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=job.attempt_token,
                status="canceled",
                stage="canceled",
                error_code="job_canceled",
                error_message="用户取消",
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="canceled",
                error_code="job_canceled",
                error_message="用户取消",
            )
        source_id = job.source_id
        if source_id is None:
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=job.attempt_token,
                status="failed",
                stage="source_missing",
                error_code="source_integrity_mismatch",
                error_message="Brief Job 缺少 source_id",
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="failed",
                error_code="source_integrity_mismatch",
                error_message="Brief Job 缺少 source_id",
            )
        source = self._repository.get_source(source_id)
        if source is None or source.lifecycle == "deleting":
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=job.attempt_token,
                status="failed",
                stage="source_missing",
                error_code="source_integrity_mismatch",
                error_message="Source 不存在或处于 deleting",
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="failed",
                error_code="source_integrity_mismatch",
                error_message="Source 不存在或处于 deleting",
            )
        if (
            job.snapshot_id is not None
            and source.active_snapshot_id != job.snapshot_id
        ):
            # Snapshot 代际切换后，旧 pending/running Job 不能读取新 Snapshot；
            # Repository 会在 Extraction 提交时优先终结它，但这里仍保留执行前
            # 门禁，覆盖进程间竞态和旧库恢复场景。
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=job.attempt_token,
                status="failed",
                stage="brief_snapshot_stale",
                error_code="brief_snapshot_stale",
                error_message="Source active Snapshot 已更新",
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="failed",
                error_code="brief_snapshot_stale",
                error_message="Source active Snapshot 已更新",
            )
        if source.active_snapshot_id is None or source.extraction_status != "extracted":
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=job.attempt_token,
                status="failed",
                stage="extraction_required",
                error_code="extraction_required",
                error_message="Source 尚未完成 Extraction",
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="failed",
                error_code="extraction_required",
                error_message="Source 尚未完成 Extraction",
            )

        primary_provider = self.resolve_provider()
        fallback_provider = self.resolve_fallback_provider()
        if primary_provider is None and fallback_provider is None:
            block_reason = self.provider_block_reason() or "provider_unavailable"
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=job.attempt_token,
                status="failed",
                stage="brief_provider_unavailable",
                error_code=block_reason,
                error_message=(
                    "未配置满足 Brief 96K context 的 Provider，请先在设置中配置"
                ),
            )
            self._repository.update_source_state(
                source_id,
                brief_status="pending",
                brief_block_reason=block_reason,
                brief_error_code=block_reason,
                brief_error_message=(
                    "未配置满足 Brief 96K context 的 Provider，请先在设置中配置"
                ),
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="failed",
                error_code=block_reason,
                error_message="Brief Provider 不可用",
            )
        # KI-10 / Spec §11.2：active 不满足 96K 但 fallback 满足时，用 fallback 作为
        # primary（无第二个 failover 候选）；两者都满足时 active=primary、fallback=候选。
        if primary_provider is None:
            generation_primary = fallback_provider
            generation_fallback: Optional[AIProviderProfile] = None
        else:
            generation_primary = primary_provider
            generation_fallback = fallback_provider
        assert generation_primary is not None

        snapshot_id = source.active_snapshot_id
        assert snapshot_id is not None

        # KBR-04：没有可引用文本 Evidence 的 Source 不发 generation 请求，使用稳定
        # block 语义。Source 保持 extracted/Evidence 可搜索；不创建无意义 Attempt。
        evidence_rows = self._load_evidence(source_id, snapshot_id)
        text_evidence_rows = [row for row in evidence_rows if row.kind != "asset"]
        if not text_evidence_rows:
            block_reason = "brief_no_text_evidence"
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=job.attempt_token,
                status="failed",
                stage="brief_no_text_evidence",
                error_code=block_reason,
                error_message="Source 缺少可引用的文本 Evidence",
            )
            self._repository.update_source_state(
                source_id,
                brief_status="pending",
                brief_block_reason=block_reason,
                brief_error_code=block_reason,
                brief_error_message=(
                    "Source 缺少可引用的文本 Evidence，请补充正文后重新导入"
                ),
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="failed",
                error_code=block_reason,
                error_message="Source 缺少可引用的文本 Evidence",
            )

        try:
            attempt_record, brief_job_id, attempt_token = (
                self._repository.create_brief_attempt(
                    BriefAttemptCreateInput(
                        source_id=source_id,
                        snapshot_id=snapshot_id,
                        provider_id=generation_primary.id,
                        provider_model=generation_primary.model,
                        provider_base_url=generation_primary.base_url,
                        context_window=generation_primary.context_window,
                        max_output_tokens=generation_primary.max_output_tokens,
                        prompt_version=BRIEF_PROMPT_VERSION,
                        schema_version=BRIEF_SCHEMA_VERSION,
                        language=BRIEF_LANGUAGE,
                        fallback_provider_id=(
                            generation_fallback.id if generation_fallback else ""
                        ),
                        fallback_provider_model=(
                            generation_fallback.model if generation_fallback else ""
                        ),
                    )
                )
            )
        except KnowledgeBriefAttemptError as exc:
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=job.attempt_token,
                status="failed",
                stage="brief_attempt_conflict",
                error_code=exc.code,
                error_message=exc.message,
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="failed",
                error_code=exc.code,
                error_message=exc.message,
            )

        # 关联：通过 brief queue Job 的 attempt_token 与 Attempt 同时操作的另一个
        # brief Job（``create_brief_attempt`` 内部复用原始 queue Job）。
        # Attempt 与 Brief/Job 终态由 Repository 在同一事务提交，避免先发布 Attempt
        # 失败再用第二个 complete_job 覆盖状态。
        attempt_id = attempt_record.id
        self._trace_attempt_id = attempt_id
        self._set_trace_context(phase="attempt_started", evidence_ids=[])
        self._append_attempt_step(
            phase="attempt_started",
            status="completed",
            output={"status": "processing"},
        )

        if self._repository.is_job_canceled(job.id):
            self._repository.fail_brief_attempt(
                attempt_id,
                job_id=brief_job_id,
                attempt_token=attempt_token,
                error_code="job_canceled",
                error_message="用户在 Brief generation 之前取消",
            )
            self._append_attempt_terminal_failure(
                error_code="job_canceled", error_message="用户在 Brief generation 之前取消"
            )
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=job.attempt_token,
                status="canceled",
                stage="canceled",
                error_code="job_canceled",
                error_message="用户取消",
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="canceled",
                error_code="job_canceled",
                error_message="用户取消",
            )

        coverage_plan = build_section_coverage_plan(evidence_rows)
        title_for_prompt = (
            source.display_title or source.title_hint or source.main_filename
        )

        with self._outer_lease_heartbeat(job.id, job.attempt_token):
            generation_result, failure = self._run_generation_with_repair(
                attempt_id=attempt_id,
                brief_job_id=brief_job_id,
                attempt_token=attempt_token,
                primary_provider=generation_primary,
                fallback_provider=generation_fallback,
                source_title=title_for_prompt,
                evidence_rows=evidence_rows,
                coverage_plan=coverage_plan,
                outer_job_id=job.id,
                outer_attempt_token=job.attempt_token,
            )
        if generation_result is None or failure is not None:
            # 已经在 helper 内部 fail_brief_attempt，直接退出。
            error_code = failure[0] if failure else "brief_generation_failed"
            error_message = failure[1] if failure else "Brief generation 未通过门禁"
            if error_code in {"job_canceled", "job_lease_mismatch"}:
                # lease/cancel 安全点可能发生在模型调用返回之后；尽力终结内部
                # Attempt，失败时由启动恢复处理遗留 processing Attempt。
                self._repository.fail_brief_attempt(
                    attempt_id,
                    job_id=brief_job_id,
                    attempt_token=attempt_token,
                    error_code=error_code,
                    error_message=error_message,
                )
                self._append_attempt_terminal_failure(
                    error_code=error_code, error_message=error_message
                )
            if error_code == "job_canceled":
                status = "canceled"
            else:
                status = "failed"
            completed, _ = self._repository.complete_job(
                job.id,
                attempt_token=job.attempt_token,
                status=status,
                stage=error_code,
                error_code=error_code,
                error_message=error_message,
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=completed,
                status=status,
                error_code=error_code,
                error_message=error_message,
            )

        # KBR-05：``_run_generation_with_repair`` 已在内部对 quality/support/citation/coverage
        # 失败统一 ``fail_brief_attempt`` 并返回 ``(None, failure)``；走到这里说明候选已通过
        # 全部门禁（``generation_result`` 非 None），``support_results`` 必为全 supported。
        outer_active = self._refresh_outer_lease(job)
        if not outer_active or self._repository.is_job_canceled(job.id):
            canceled = self._repository.is_job_canceled(job.id)
            cancel_code = "job_canceled" if canceled else "job_lease_mismatch"
            self._repository.fail_brief_attempt(
                attempt_id,
                job_id=brief_job_id,
                attempt_token=attempt_token,
                error_code=cancel_code,
                error_message=(
                    "用户在 Brief 校验通过后、提交前取消"
                    if canceled
                    else "Brief 校验通过后外层 queue lease 已失效"
                ),
            )
            self._append_attempt_terminal_failure(
                error_code=cancel_code,
                error_message=(
                    "用户在 Brief 校验通过后、提交前取消"
                    if canceled
                    else "Brief 校验通过后外层 queue lease 已失效"
                ),
                iteration=generation_result.repair_count,
                retry_count=generation_result.provider_retry_count,
            )
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=job.attempt_token,
                status="canceled" if canceled else "failed",
                stage="canceled" if canceled else "commit_conflict",
                error_code=cancel_code,
                error_message="用户取消" if canceled else "Brief lease 已失效",
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=ok,
                status="canceled" if canceled else "failed",
                error_code=cancel_code,
                error_message="用户取消" if canceled else "Brief lease 已失效",
            )

        if not self._outer_lease_active(job.id, job.attempt_token):
            self._repository.fail_brief_attempt(
                attempt_id,
                job_id=brief_job_id,
                attempt_token=attempt_token,
                error_code="job_lease_mismatch",
                error_message="Brief 提交前外层 queue lease 已失效",
            )
            self._append_attempt_terminal_failure(
                error_code="job_lease_mismatch",
                error_message="Brief 提交前外层 queue lease 已失效",
                iteration=generation_result.repair_count,
                retry_count=generation_result.provider_retry_count,
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=False,
                status="failed",
                error_code="job_lease_mismatch",
                error_message="Brief 提交前外层 queue lease 已失效",
            )

        ok, _, _ = self._repository.commit_brief_attempt_success(
            attempt_id,
            job_id=brief_job_id,
            attempt_token=attempt_token,
            payload_json=generation_result.payload.model_dump_json(
                by_alias=False, exclude_none=False
            ),
            validation_report_json=generation_result.validation_report_json,
            token_input_count=generation_result.token_input_count,
            token_output_count=generation_result.token_output_count,
            latency_ms=generation_result.latency_ms,
            actual_provider_id=generation_result.actual_provider_id,
            actual_provider_model=generation_result.actual_provider_model,
            provider_retry_count=generation_result.provider_retry_count,
            repair_count=generation_result.repair_count,
        )
        if not ok:
            # lease 失效或 attempt_token 不匹配（迟到结果）；保留 Attempt 状态不变。
            self._append_attempt_terminal_failure(
                iteration=generation_result.repair_count,
                retry_count=generation_result.provider_retry_count,
                error_code="job_lease_mismatch",
                error_message="Brief 提交时 lease 已失效",
            )
            self._repository.complete_job(
                job.id,
                attempt_token=job.attempt_token,
                status="failed",
                stage="brief_commit_conflict",
                error_code="job_lease_mismatch",
                error_message="Brief 提交时 lease 已失效",
            )
            return JobExecutionResult(
                job_id=job.id,
                accepted=False,
                status="failed",
                error_code="job_lease_mismatch",
                error_message="Brief 提交时 lease 已失效",
            )
        self._append_attempt_step(
            phase="final",
            iteration=generation_result.repair_count,
            status="succeeded",
            output={"status": "succeeded"},
            retry_count=generation_result.provider_retry_count,
        )
        ok, _ = self._repository.complete_job(
            job.id,
            attempt_token=job.attempt_token,
            status="succeeded",
            stage="brief_ready",
            progress=100,
        )
        return JobExecutionResult(
            job_id=job.id,
            accepted=ok,
            status="succeeded",
            error_code="",
            error_message="",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_evidence(
        self, source_id: int, snapshot_id: int
    ) -> list[EvidenceRecord]:
        """Spec §10.2：generation 单次读取完整 Source 文本 Evidence。"""
        items: list[EvidenceRecord] = []
        cursor: Optional[int] = None
        while True:
            page = self._repository.list_evidence(
                source_id,
                snapshot_id=snapshot_id,
                after_ordinal=cursor,
                limit=200,
            )
            items.extend(page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        return items

    def _run_generation_with_repair(
        self,
        *,
        attempt_id: int,
        brief_job_id: int,
        attempt_token: str,
        primary_provider: AIProviderProfile,
        fallback_provider: Optional[AIProviderProfile],
        source_title: str,
        evidence_rows: list[EvidenceRecord],
        coverage_plan: SectionCoveragePlan,
        outer_job_id: Optional[int] = None,
        outer_attempt_token: str = "",
    ) -> tuple[Optional[BriefGenerationResult], Optional[tuple[str, str]]]:
        """Spec §10.3 / §11.3 / §11.4 + KBR-06：generation + 程序校验；一次 repair。

        两条 repair 路径共享「最多一次 repair」预算（``repair_count`` 最多到 1）：
        - Schema 不可解析（``schema`` 模式）：没有可 patch 的候选，请求模型重新输出完整
          Brief JSON。repair 后仍非法或仍存在质量问题 → Attempt failed。
        - 合法候选质量失败（``quality`` 模式）：请求模型只对失败 block 返回结构化 patch
          （replace/delete/split）。程序解析、权限校验、原子应用 patch 后重新执行完整
          Schema/数量/citation ownership/coverage/support 门禁；任一非 supported/missing/
          citation 失败 → Attempt failed。

        patch 权限由程序按失败 block 集合硬约束（``apply_repair_patch``），不采信模型自述，
        因此 Source/Evidence/previous candidate 中的注入指令不能扩大 patch 权限。

        KI-10：generation/repair/validator 均走 ``_call_model_with_failover``；内容质量
        失败不切 Provider，actual_provider 持久化到 Attempt。

        返回 ``(generation_result, failure)``：成功 ``(BriefGenerationResult, None)``；
        失败 ``(None, (error_code, error_message))``（已 ``fail_brief_attempt``）。
        """
        repair_count = 0
        retry_state = _RetryState(attempt_id=attempt_id)
        actual_provider = primary_provider
        schema_error_message = ""
        # quality repair 上下文：(候选, 失败 issues, 允许修改的失败 block 集合)。
        quality_repair_context: Optional[
            tuple[BriefPayload, list[ValidationIssue], set[str]]
        ] = None
        source_evidence_ids = {str(row.id) for row in evidence_rows}
        # Finding 1/5 + 二轮 Review P1-A：Evidence 的 id -> (section_key, kind) 映射，含当前
        # Snapshot 全部 Evidence（含 Asset）。apply_repair_patch 用它做 coverage_missing upsert
        # 授权（eligible 过滤 Asset，citations ⊆ section 文本 Evidence）与非 guide block
        # replace/split 的章节边界校验（Asset citation 也受约束，不得新增主题）。
        evidence_section_index = {
            str(row.id): (
                _section_key_for_heading(list(getattr(row, "heading_path", ()) or ())),
                str(getattr(row, "kind", "text")),
            )
            for row in evidence_rows
        }

        while True:
            if (
                outer_job_id is not None
                and not self._renew_outer_lease(outer_job_id, outer_attempt_token)
            ):
                return None, (
                    "job_canceled"
                    if self._repository.is_job_canceled(outer_job_id)
                    else "job_lease_mismatch",
                    "Brief generation 前 queue lease 已失效或已取消",
                )

            if quality_repair_context is not None:
                candidate_brief, failed_issues, failed_block_set = quality_repair_context
                messages = build_repair_prompt(
                    source_title=source_title,
                    evidence_rows=evidence_rows,
                    coverage_plan=coverage_plan,
                    candidate=candidate_brief,
                    failed_issues=failed_issues,
                    failed_block_paths=sorted(failed_block_set),
                )
            elif repair_count >= 1:
                # schema repair：无候选可 patch，请求完整 Brief。
                messages = build_schema_repair_prompt(
                    source_title=source_title,
                    evidence_rows=evidence_rows,
                    coverage_plan=coverage_plan,
                    schema_error_message=schema_error_message,
                )
            else:
                messages = build_generation_prompt(
                    source_title=source_title,
                    evidence_rows=evidence_rows,
                    coverage_plan=coverage_plan,
                )

            self._set_trace_context(
                phase=("repair" if quality_repair_context is not None or repair_count >= 1 else "generation"),
                iteration=repair_count,
                evidence_ids=sorted(source_evidence_ids),
            )
            if repair_count >= 1:
                self._append_attempt_step(
                    phase="repair_requested",
                    iteration=repair_count,
                    evidence_ids=source_evidence_ids,
                    output={
                        "mode": "quality_patch" if quality_repair_context is not None else "schema_rebuild",
                        "failed_block_paths": sorted(
                            quality_repair_context[2]
                            if quality_repair_context is not None
                            else set()
                        ),
                    },
                    retry_count=retry_state.total_retries,
                )

            try:
                (
                    raw_text,
                    token_in,
                    token_out,
                    latency_ms,
                    actual_provider,
                ) = self._call_model_with_failover(
                    primary_provider,
                    fallback_provider,
                    messages,
                    state=retry_state,
                )
                self._append_attempt_step(
                    phase="model_response_parsed",
                    iteration=repair_count,
                    provider_id=actual_provider.id,
                    provider_model=actual_provider.model,
                    evidence_ids=self._trace_evidence_ids,
                    output={"response_received": True},
                    token_input_count=token_in,
                    token_output_count=token_out,
                    latency_ms=latency_ms,
                    retry_count=retry_state.total_retries,
                )
            except BriefModelCallError as exc:
                self._repository.fail_brief_attempt(
                    attempt_id,
                    job_id=brief_job_id,
                    attempt_token=attempt_token,
                    error_code=exc.code,
                    error_message=exc.message,
                    repair_count=repair_count,
                    actual_provider_id=actual_provider.id,
                    actual_provider_model=actual_provider.model,
                    provider_retry_count=retry_state.total_retries,
                )
                self._append_attempt_step(
                    phase="attempt_failed",
                    iteration=repair_count,
                    status="failed",
                    evidence_ids=source_evidence_ids,
                    output={"error_code": exc.code, "stage": "model_call"},
                    retry_count=retry_state.total_retries,
                    error_code=exc.code,
                    error_message=exc.message[:500],
                )
                self._append_attempt_step(
                    phase="final",
                    iteration=repair_count,
                    status="failed",
                    evidence_ids=source_evidence_ids,
                    output={"status": "failed", "error_code": exc.code},
                    retry_count=retry_state.total_retries,
                    error_code=exc.code,
                    error_message=exc.message[:500],
                )
                return None, (exc.code, exc.message)

            # KBR-06：quality repair 路径把响应解析为 patch 并原子应用。
            if quality_repair_context is not None:
                # 显式重新解包：apply_repair_patch 用的 candidate_brief/failed_block_set
                # 在本块内显式绑定，不依赖 1738 块的跨 if 控制流推理。
                candidate_brief, _failed_issues, failed_block_set = quality_repair_context
                try:
                    patch = parse_repair_patch(raw_text)
                    self._append_attempt_step(
                        phase="repair_patch_parsed",
                        iteration=repair_count,
                        evidence_ids=source_evidence_ids,
                        output={"untrusted": True, **patch.model_dump(mode="json")},
                        retry_count=retry_state.total_retries,
                    )
                    brief = apply_repair_patch(
                        candidate_brief,
                        patch,
                        failed_block_paths=failed_block_set,
                        source_evidence_ids=source_evidence_ids,
                        coverage_plan=coverage_plan,
                        evidence_section_index=evidence_section_index,
                    )
                except BriefSchemaError as exc:
                    # patch 非法/越权 → 稳定错误码 + 安全 report，repair 预算已消耗。
                    report_json = self._build_repair_failure_report(
                        error_code=exc.code,
                        reason=exc.message,
                        repair_count=repair_count,
                    )
                    self._append_attempt_step(
                        phase="repair_failed",
                        iteration=repair_count,
                        status="failed",
                        evidence_ids=source_evidence_ids,
                        output={"error_code": exc.code, "stage": "patch_parse_or_apply"},
                        retry_count=retry_state.total_retries,
                        error_code=exc.code,
                        error_message=exc.message[:500],
                    )
                    self._repository.fail_brief_attempt(
                        attempt_id,
                        job_id=brief_job_id,
                        attempt_token=attempt_token,
                        error_code=exc.code,
                        error_message=exc.message[:500],
                        candidate_payload_json=candidate_brief.model_dump_json(),
                        validation_report_json=report_json,
                        repair_count=repair_count,
                        token_input_count=token_in,
                        token_output_count=token_out,
                        latency_ms=latency_ms,
                        actual_provider_id=actual_provider.id,
                        actual_provider_model=actual_provider.model,
                        provider_retry_count=retry_state.total_retries,
                    )
                    self._append_attempt_step(
                        phase="attempt_failed",
                        iteration=repair_count,
                        status="failed",
                        evidence_ids=source_evidence_ids,
                        output={"error_code": exc.code, "stage": "repair"},
                        retry_count=retry_state.total_retries,
                        error_code=exc.code,
                        error_message=exc.message[:500],
                    )
                    self._append_attempt_step(
                        phase="final",
                        iteration=repair_count,
                        status="failed",
                        evidence_ids=source_evidence_ids,
                        output={"status": "failed", "error_code": exc.code},
                        retry_count=retry_state.total_retries,
                        error_code=exc.code,
                        error_message=exc.message[:500],
                    )
                    return None, (exc.code, exc.message)
                # patch 应用成功：清空 quality repair 上下文，进入完整复验。
                quality_repair_context = None
            else:
                try:
                    brief = parse_brief_payload(raw_text)
                except BriefSchemaError as exc:
                    if repair_count >= 1:
                        # schema repair 后仍非法 → Attempt failed（独立 schema_invalid report）。
                        schema_report = json.dumps(
                            {
                                "stage": "schema_invalid",
                                "error_code": exc.code,
                                "failure_count": 1,
                                "summary": f"Brief Schema 无法解析：{exc.message[:80]}",
                                "issues": [
                                    {
                                        "block_path": "",
                                        "issue_type": "schema_invalid",
                                        "decision": "",
                                        "reason": exc.message,
                                        "evidence_ids": [],
                                    }
                                ],
                                "repair_count": repair_count,
                            },
                            ensure_ascii=False,
                        )
                        self._append_attempt_step(
                            phase="schema_parse_failed",
                            iteration=repair_count,
                            status="failed",
                            evidence_ids=source_evidence_ids,
                            output={"error_code": exc.code, "stage": "repair"},
                            retry_count=retry_state.total_retries,
                            error_code=exc.code,
                            error_message=exc.message[:500],
                        )
                        self._repository.fail_brief_attempt(
                            attempt_id,
                            job_id=brief_job_id,
                            attempt_token=attempt_token,
                            error_code=exc.code,
                            error_message=exc.message,
                            candidate_payload_json="",
                            # schema_invalid 独立构造 report（不复用 _build_structured_report）：
                            # error_code 必须是 brief_schema_invalid（与 Attempt error_code 一致），
                            # 而 _build_structured_report 硬编码 brief_quality_failed；且此时 brief
                            # 尚未解析、无 coverage/support 数据。详见该函数 docstring。
                            validation_report_json=schema_report,
                            repair_count=repair_count,
                            token_input_count=token_in,
                            token_output_count=token_out,
                            latency_ms=latency_ms,
                            actual_provider_id=actual_provider.id,
                            actual_provider_model=actual_provider.model,
                            provider_retry_count=retry_state.total_retries,
                        )
                        self._append_attempt_step(
                            phase="attempt_failed",
                            iteration=repair_count,
                            status="failed",
                            evidence_ids=source_evidence_ids,
                            output={"error_code": exc.code, "stage": "schema"},
                            retry_count=retry_state.total_retries,
                            error_code=exc.code,
                            error_message=exc.message[:500],
                        )
                        self._append_attempt_step(
                            phase="final",
                            iteration=repair_count,
                            status="failed",
                            evidence_ids=source_evidence_ids,
                            output={"status": "failed", "error_code": exc.code},
                            retry_count=retry_state.total_retries,
                            error_code=exc.code,
                            error_message=exc.message[:500],
                        )
                        return None, (exc.code, exc.message)
                    # 首次 Schema 不可解析 → schema repair（消耗唯一 repair 预算）。
                    self._append_attempt_step(
                        phase="schema_parse_failed",
                        iteration=repair_count,
                        status="failed",
                        evidence_ids=source_evidence_ids,
                        output={"error_code": exc.code, "stage": "initial"},
                        retry_count=retry_state.total_retries,
                        error_code=exc.code,
                        error_message=exc.message[:500],
                    )
                    repair_count += 1
                    schema_error_message = exc.message
                    quality_repair_context = None
                    continue

            # KBR-05：Schema 合法（或 patch 已应用）后汇总全部质量检查（citation per-block →
            # citation 有效 block 逐条 support → coverage），形成完整结构化报告。不按首个失败
            # 抢占 repair；citation 无效的 block 不发起 Validator，其问题进入统一 report。
            quality = self._evaluate_brief_quality(
                brief=brief,
                evidence_rows=evidence_rows,
                coverage_plan=coverage_plan,
                primary_provider=actual_provider,
                fallback_provider=(
                    fallback_provider
                    if fallback_provider is not None
                    and fallback_provider.id != actual_provider.id
                    else None
                ),
                retry_state=retry_state,
                outer_job_id=outer_job_id,
                outer_attempt_token=outer_attempt_token,
            )
            if (
                outer_job_id is not None
                and not self._renew_outer_lease(outer_job_id, outer_attempt_token)
            ):
                return None, (
                    "job_canceled"
                    if self._repository.is_job_canceled(outer_job_id)
                    else "job_lease_mismatch",
                    "Brief 质量校验后 queue lease 已失效或已取消",
                )
            validation_report_json = self._build_structured_report(
                quality=quality, repair_count=repair_count
            )
            try:
                validation_report_payload = json.loads(validation_report_json)
            except (TypeError, json.JSONDecodeError):
                validation_report_payload = {"untrusted": True}
            self._append_attempt_step(
                phase="validation_report",
                iteration=repair_count,
                evidence_ids=source_evidence_ids,
                output=validation_report_payload,
                retry_count=retry_state.total_retries,
            )
            if quality.issues:
                if repair_count >= 1:
                    # repair 后仍存在质量问题 → Attempt failed；旧 current Brief 由
                    # ``fail_brief_attempt`` 保证继续可见（Spec §10.4）。完整结构化
                    # report 归属本 Attempt，Source 状态区只显示稳定摘要。
                    summary = self._format_quality_summary(quality.issues)
                    self._repository.fail_brief_attempt(
                        attempt_id,
                        job_id=brief_job_id,
                        attempt_token=attempt_token,
                        error_code="brief_quality_failed",
                        error_message=summary,
                        candidate_payload_json=brief.model_dump_json(),
                        validation_report_json=validation_report_json,
                        repair_count=repair_count,
                        token_input_count=token_in,
                        token_output_count=token_out,
                        latency_ms=latency_ms,
                        actual_provider_id=actual_provider.id,
                        actual_provider_model=actual_provider.model,
                        provider_retry_count=retry_state.total_retries,
                    )
                    self._append_attempt_step(
                        phase="attempt_failed",
                        iteration=repair_count,
                        status="failed",
                        evidence_ids=source_evidence_ids,
                        output={"error_code": "brief_quality_failed", "report": validation_report_payload},
                        retry_count=retry_state.total_retries,
                        error_code="brief_quality_failed",
                        error_message=summary[:500],
                    )
                    self._append_attempt_step(
                        phase="final",
                        iteration=repair_count,
                        status="failed",
                        evidence_ids=source_evidence_ids,
                        output={"status": "failed", "error_code": "brief_quality_failed"},
                        retry_count=retry_state.total_retries,
                        error_code="brief_quality_failed",
                        error_message=summary[:500],
                    )
                    return None, ("brief_quality_failed", summary)
                # 首次质量失败：汇总后单次 quality repair（patch），repair 输入含全部已发现
                # 问题（citation + support + coverage）。失败 block 集合即 patch 允许修改范围。
                repair_count += 1
                failed_block_set = {issue.block_path for issue in quality.issues}
                quality_repair_context = (brief, list(quality.issues), failed_block_set)
                schema_error_message = ""
                continue

            # 全部门禁通过（citation/support/coverage 全 supported）。
            self._append_attempt_step(
                phase="attempt_ready",
                iteration=repair_count,
                evidence_ids=source_evidence_ids,
                output={"status": "ready", "report": validation_report_payload},
                retry_count=retry_state.total_retries,
            )
            self._append_attempt_step(
                phase="final",
                iteration=repair_count,
                status="ready",
                evidence_ids=source_evidence_ids,
                output={"status": "ready"},
                retry_count=retry_state.total_retries,
            )
            result = BriefGenerationResult(
                payload=brief,
                validation_report_json=validation_report_json,
                support_results=quality.support_results,
                token_input_count=token_in,
                token_output_count=token_out,
                latency_ms=latency_ms,
                actual_provider_id=actual_provider.id,
                actual_provider_model=actual_provider.model,
                provider_retry_count=retry_state.total_retries,
                repair_count=repair_count,
            )
            return result, None

    def _build_repair_failure_report(
        self, *, error_code: str, reason: str, repair_count: int
    ) -> str:
        """KBR-06：repair patch 非法/越权失败的安全 report。

        记录稳定错误码与被拒原因类别（不含 Evidence 正文、不复制完整 patch）。issue_type
        映射自 error_code：``brief_repair_invalid``→``repair_invalid``、
        ``brief_repair_unauthorized``→``repair_unauthorized``，供 UI 区分 repair 类失败。
        """
        issue_type = (
            "repair_unauthorized"
            if error_code == BRIEF_REPAIR_UNAUTHORIZED
            else "repair_invalid"
        )
        summary = (
            "Brief repair patch 被拒绝"
            f"（{ '越权' if error_code == BRIEF_REPAIR_UNAUTHORIZED else '非法'}）"
            "，详见处理记录"
        )
        return json.dumps(
            {
                "stage": "repair_failed",
                "error_code": error_code,
                "failure_count": 1,
                "summary": summary,
                "issues": [
                    {
                        "block_path": "",
                        "issue_type": issue_type,
                        "decision": "",
                        "reason": reason,
                        "evidence_ids": [],
                    }
                ],
                "repair_count": repair_count,
            },
            ensure_ascii=False,
        )

    def _run_support_validation(
        self,
        *,
        brief: BriefPayload,
        evidence_index: dict[str, EvidenceRecord],
        provider: AIProviderProfile,
        fallback: Optional[AIProviderProfile],
        retry_state: "_RetryState",
        outer_job_id: Optional[int] = None,
        outer_attempt_token: str = "",
        skip_block_paths: Optional[set[str]] = None,
    ) -> list[dict[str, Any]]:
        """Spec §10.3 独立 Validator：逐条 statement 判定 supported/partial/...

        KI-10：Validator 调用也走 Provider 重试（Spec §11.4 仍适用）。网络/超时/限流
        /5xx 等基础设施失败可切换固定 fallback；Validator 的非法 JSON、unsupported
        等内容判定属于质量结论，不切换 Provider。Validator 调用共享同一
        ``retry_state``，保证诊断完整。

        KBR-05：``skip_block_paths`` 中的 block（citation 无效）不发起 Validator 调用，
        其 citation 问题已进入统一 repair report；其余 citation 有效 block 继续逐条
        support validation，以收集尽可能完整的质量反馈。Validator 仍只读取单条
        statement 及其声明的 Evidence。
        """
        skip_set = skip_block_paths or set()
        results: list[dict[str, Any]] = []
        current_provider = provider
        current_fallback = fallback
        for block_name, statement, evidence_ids in collect_brief_statement_blocks(
            brief
        ):
            if block_name in skip_set:
                # citation 无效的 block 不调 Validator（Spec Implementation Decisions）。
                continue
            if (
                outer_job_id is not None
                and not self._renew_outer_lease(outer_job_id, outer_attempt_token)
            ):
                # 让调用方在下一安全点将外层 Job 标记 canceled；不再发起新的
                # Validator 请求，也不把迟到模型结果写入当前 Brief。
                return results
            cited = [
                self._evidence_for_prompt(evidence_index.get(eid))
                for eid in evidence_ids
                if eid in evidence_index
            ]
            self._set_trace_context(
                phase="validation",
                iteration=retry_state.total_retries,
                block_path=block_name,
                evidence_ids=list(evidence_ids),
            )
            try:
                (
                    raw_text,
                    _,
                    _,
                    _,
                    validator_provider,
                ) = self._call_model_with_failover(
                    current_provider,
                    current_fallback,
                    build_validation_prompt(statement=statement, cited_evidence=cited),
                    state=retry_state,
                )
                current_provider = validator_provider
                if (
                    current_fallback is not None
                    and validator_provider.id == current_fallback.id
                ):
                    # fallback 已成为当前 Validator Provider，后续 statement
                    # 不再把同一 profile 当作自己的 failover 候选。
                    current_fallback = None
                decision = parse_support_decision(raw_text, statement=statement)
                self._append_attempt_step(
                    phase="validation_result",
                    iteration=retry_state.total_retries,
                    block_path=block_name,
                    provider_id=validator_provider.id,
                    provider_model=validator_provider.model,
                    evidence_ids=list(evidence_ids),
                    output={
                        "untrusted": True,
                        "decision": decision.decision,
                        "reason_code": decision.reason_code,
                        "unsupported_fragments": list(decision.unsupported_fragments),
                        "explanation": decision.explanation[:300],
                        "suggested_rewrite": decision.suggested_rewrite[:300],
                    },
                    retry_count=retry_state.total_retries,
                )
            except BriefSchemaError as exc:
                self._append_attempt_step(
                    phase="validation_result",
                    iteration=retry_state.total_retries,
                    block_path=block_name,
                    status="failed",
                    evidence_ids=list(evidence_ids),
                    output={
                        "untrusted": True,
                        "decision": "",
                        "issue_type": ISSUE_VALIDATOR_PARSE_FAILED,
                        "reason_code": ISSUE_VALIDATOR_PARSE_FAILED,
                        "explanation": exc.message[:300],
                    },
                    retry_count=retry_state.total_retries,
                    error_code=ISSUE_VALIDATOR_PARSE_FAILED,
                    error_message=exc.message[:500],
                )
                results.append(
                    {
                        "block": block_name,
                        # 解析失败不是 Evidence 不支持；保留空 decision，交由质量
                        # 汇总写入 validator_parse_failed，避免把模型协议错误伪装成
                        # 内容判定。
                        "decision": "",
                        "issue_type": ISSUE_VALIDATOR_PARSE_FAILED,
                        "reason": f"Validator 输出无法解析：{exc.message}",
                        "reason_code": ISSUE_VALIDATOR_PARSE_FAILED,
                        "unsupported_fragments": [],
                        "explanation": exc.message[:300],
                        "suggested_rewrite": "",
                        "evidence_ids": list(evidence_ids),
                    }
                )
                continue
            except BriefModelCallError as exc:
                results.append(
                    {
                        "block": block_name,
                        "decision": "",
                        "issue_type": "validator_call_failed",
                        "reason": f"Validator 调用失败：{exc.code}",
                        "reason_code": "validator_call_failed",
                        "unsupported_fragments": [],
                        "explanation": exc.message[:300],
                        "suggested_rewrite": "",
                        "evidence_ids": list(evidence_ids),
                    }
                )
                continue
            results.append(
                {
                    "block": block_name,
                    "decision": decision.decision,
                    # Finding 4：受限 reason（已限长）再做回显检测，仅供 repair 临时使用，不持久化。
                    "reason": redact_reason_echo(
                        decision.reason,
                        statement,
                        [e.get("excerpt", "") for e in cited if e.get("excerpt")],
                    ),
                    "reason_code": decision.reason_code,
                    "unsupported_fragments": list(decision.unsupported_fragments),
                    "explanation": redact_reason_echo(
                        decision.explanation,
                        statement,
                        [e.get("excerpt", "") for e in cited if e.get("excerpt")],
                    ),
                    "suggested_rewrite": redact_reason_echo(
                        decision.suggested_rewrite,
                        statement,
                        [e.get("excerpt", "") for e in cited if e.get("excerpt")],
                    ),
                    "evidence_ids": list(evidence_ids),
                }
            )
        return results

    def _evidence_for_prompt(self, evidence: Optional[EvidenceRecord]) -> dict[str, Any]:
        if evidence is None:
            return {"id": "", "section": "", "kind": "text", "excerpt": ""}
        heading = " / ".join(evidence.heading_path) if evidence.heading_path else "(文档顶层)"
        return {
            "id": evidence.id,
            "section": heading,
            "kind": evidence.kind,
            "excerpt": evidence.canonical_excerpt if evidence.kind != "asset" else "",
            "alt_text": evidence.search_text if evidence.kind == "asset" else "",
        }

    def _evaluate_brief_quality(
        self,
        *,
        brief: BriefPayload,
        evidence_rows: list[EvidenceRecord],
        coverage_plan: SectionCoveragePlan,
        primary_provider: AIProviderProfile,
        fallback_provider: Optional[AIProviderProfile],
        retry_state: "_RetryState",
        outer_job_id: Optional[int] = None,
        outer_attempt_token: str = "",
    ) -> BriefQualityOutcome:
        """KBR-05 汇总质量校验：citation（per-block）→ support（有效 block 逐条）→ coverage。

        三类问题合并成统一 ``issues``，不按首个失败返回。citation 无效的 block 不发起
        Validator，其问题按 missing/ownership 分类进入 report；citation 有效 block 继续
        逐条 support validation，以收集尽可能完整的反馈。coverage 用 KBR-04 实际 citation
        结果派生，与 citation/support 问题合并。
        """
        report = validate_brief_against_evidence(
            brief, evidence_rows=evidence_rows, expected_sections=coverage_plan
        )
        evidence_index = {row.id: row for row in evidence_rows}
        # coverage_missing issue 富化：每个 section 的文本 Evidence id 列表，供 repair prompt
        # 告知模型该 section 可引用哪些 Evidence（Finding 1 upsert_section_guide 的输入）。
        section_text_evidence: dict[str, list[str]] = {}
        for row in evidence_rows:
            if str(getattr(row, "kind", "text")) == "asset":
                continue
            sk = _section_key_for_heading(list(getattr(row, "heading_path", ()) or ()))
            section_text_evidence.setdefault(sk, []).append(str(row.id))

        issues: list[ValidationIssue] = []
        invalid_block_paths: set[str] = set()
        for block in report.citation_blocks:
            if not block.invalid_evidence_ids:
                continue
            invalid_block_paths.add(block.block_path)
            for evidence_id in block.invalid_evidence_ids:
                # 区分 citation_missing（编造）与 citation_ownership（跨 Source/Snapshot）。
                owner = self._repository.get_evidence(evidence_id)
                if owner is None:
                    code, summary = program_reason_for(ISSUE_CITATION_MISSING)
                    issues.append(
                        ValidationIssue(
                            block_path=block.block_path,
                            issue_type=ISSUE_CITATION_MISSING,
                            decision="",
                            reason=summary,
                            evidence_ids=[evidence_id],
                            reason_code=code,
                        )
                    )
                else:
                    code, summary = program_reason_for(ISSUE_CITATION_OWNERSHIP)
                    issues.append(
                        ValidationIssue(
                            block_path=block.block_path,
                            issue_type=ISSUE_CITATION_OWNERSHIP,
                            decision="",
                            reason=summary,
                            evidence_ids=[evidence_id],
                            reason_code=code,
                        )
                    )

        support_results = self._run_support_validation(
            brief=brief,
            evidence_index=evidence_index,
            provider=primary_provider,
            fallback=fallback_provider,
            retry_state=retry_state,
            outer_job_id=outer_job_id,
            outer_attempt_token=outer_attempt_token,
            skip_block_paths=invalid_block_paths,
        )
        for result in support_results:
            decision = str(result.get("decision") or "")
            if decision == "supported":
                continue
            # Validator 协议/调用失败使用显式 issue_type；不能通过缺省 decision
            # 伪装成 support_unsupported。旧报告没有 issue_type 时仍按 decision 兼容。
            issue_type = str(result.get("issue_type") or "")
            if not issue_type:
                issue_type = SUPPORT_DECISION_ISSUE_TYPE.get(
                    decision, ISSUE_VALIDATOR_PARSE_FAILED
                )
            code, summary = program_reason_for(issue_type)
            issues.append(
                ValidationIssue(
                    block_path=str(result.get("block", "")),
                    issue_type=issue_type,
                    decision=decision,
                    reason=(summary if issue_type != ISSUE_VALIDATOR_PARSE_FAILED else str(result.get("explanation") or summary)[:300]),
                    evidence_ids=list(result.get("evidence_ids", [])),
                    reason_code=str(result.get("reason_code") or code),
                    # repair_hint：受限 + 回显检测后的模型原始 reason，仅 repair 临时使用，不落库。
                    repair_hint=str(result.get("reason", "")),
                    unsupported_fragments=[
                        str(value) for value in result.get("unsupported_fragments", [])
                    ],
                    explanation=str(result.get("explanation", ""))[:300],
                    suggested_rewrite=str(result.get("suggested_rewrite", ""))[:300],
                )
            )

        for status in report.coverage_statuses:
            if status.status == "missing":
                code, summary = program_reason_for(ISSUE_COVERAGE_MISSING)
                issues.append(
                    ValidationIssue(
                        block_path=f"coverage[{status.section_key}]",
                        issue_type=ISSUE_COVERAGE_MISSING,
                        decision="",
                        reason=summary,
                        evidence_ids=list(section_text_evidence.get(status.section_key, [])),
                        reason_code=code,
                    )
                )

        return BriefQualityOutcome(
            issues=issues,
            support_results=support_results,
            coverage_statuses=list(report.coverage_statuses),
            programmatic_issues=list(report.issues),
        )

    def _build_structured_report(
        self, *, quality: BriefQualityOutcome, repair_count: int
    ) -> str:
        """KBR-05 结构化 validation report：全部失败项 + coverage 状态 + support 结果。

        Source 状态区只显示稳定 error_code + 失败总数 + 短摘要；Attempt/处理记录展示
        全部失败项（block_path + issue_type + decision + reason + evidence_ids），可定位
        到候选 Brief block 与已引用 Evidence。不复制 Evidence 正文（按 evidence_ids 读）。

        仅用于 Schema 合法后的质量失败（error_code 固定 brief_quality_failed）。Schema
        不可解析路径（brief_schema_invalid）因 error_code 语义不同且此时无 coverage/support
        数据，在调用处内联构造 report，不复用本函数。
        """
        has_failures = bool(quality.issues)
        issues_payload = [
            {
                "block_path": issue.block_path,
                "issue_type": issue.issue_type,
                "decision": issue.decision,
                "reason_code": issue.reason_code,
                "reason": issue.reason,
                "evidence_ids": list(issue.evidence_ids),
                "unsupported_fragments": list(issue.unsupported_fragments),
                "explanation": issue.explanation[:300],
                "suggested_rewrite": issue.suggested_rewrite[:300],
            }
            for issue in quality.issues
        ]
        # Finding 4：support_results 不持久化模型原始 reason（已 redact 的也不落库），
        # 仅保留 block/decision/evidence_ids 供诊断定位。
        support_results_payload = [
            {
                "block": r.get("block", ""),
                "decision": r.get("decision", ""),
                "evidence_ids": list(r.get("evidence_ids", [])),
                "reason_code": r.get("reason_code", ""),
                "unsupported_fragments": list(r.get("unsupported_fragments", [])),
                "explanation": str(r.get("explanation", ""))[:300],
                "suggested_rewrite": str(r.get("suggested_rewrite", ""))[:300],
            }
            for r in quality.support_results
        ]
        return json.dumps(
            {
                "stage": "quality_failed" if has_failures else "quality_passed",
                "error_code": "brief_quality_failed" if has_failures else "",
                "failure_count": len(quality.issues),
                "summary": (
                    self._format_quality_summary(quality.issues) if has_failures else ""
                ),
                "issues": issues_payload,
                "coverage_statuses": [
                    {
                        "section_key": status.section_key,
                        "status": status.status,
                        "skipped_reason": status.skipped_reason,
                    }
                    for status in quality.coverage_statuses
                ],
                "support_results": support_results_payload,
                "programmatic_issues": quality.programmatic_issues,
                "repair_count": repair_count,
            },
            ensure_ascii=False,
        )

    def _format_quality_summary(self, issues: list[ValidationIssue]) -> str:
        """稳定短摘要：失败总数 + 按分类（citation/support/coverage）计数。

        不半句截断；不复制 Evidence 正文或具体 issue reason。
        """
        counts: dict[str, int] = {}
        for issue in issues:
            category = issue.issue_type.split("_", 1)[0]
            counts[category] = counts.get(category, 0) + 1
        parts = [f"{label} ×{count}" for label, count in counts.items()]
        detail = "，".join(parts) if parts else "未分类"
        return f"Brief 质量校验失败：共 {len(issues)} 条（{detail}），详见处理记录"

    def _call_model_once(
        self,
        provider: AIProviderProfile,
        messages: list[dict[str, str]],
        *,
        retry_count: int = 0,
    ) -> tuple[str, int, int, int]:
        """Spec §11 / §18：单次调用 litellm.completion，返回 (text, in, out, ms)。

        不持久化原始响应；只保留稳定错误类别与 token/延时元数据。重试与 failover 由
        ``_call_model_with_retry`` / ``_call_model_with_failover`` 包装。

        错误归类（Spec §11.4）：
        - ``provider_auth_invalid``：401/403 鉴权失败（permanent，不重试不切换）。
        - ``provider_model_unavailable``：404 模型不存在 / 上下文超限（permanent）。
        - ``provider_transient_error``：超时 / 连接 / 限流 / 5xx（重试，可切换 fallback）。
        """
        started = time.monotonic()
        payload: dict[str, Any] = {
            "model": _litellm_model_name(provider),
            "messages": messages,
            "api_key": provider.api_key,
            "timeout": BRIEF_MODEL_TIMEOUT_SECONDS,
        }
        api_base = _litellm_api_base(provider)
        if api_base:
            payload["api_base"] = api_base
        try:
            response = self._model_client(**payload)
        except Exception as exc:  # noqa: BLE001 - litellm 错误族未导出统一基类
            code = _classify_model_error(exc)
            self._append_attempt_step(
                phase=self._trace_phase or "model_call",
                iteration=self._trace_iteration,
                block_path=self._trace_block_path,
                provider_id=provider.id,
                provider_model=provider.model,
                prompt_version=BRIEF_PROMPT_VERSION,
                schema_version=BRIEF_SCHEMA_VERSION,
                evidence_ids=self._trace_evidence_ids,
                status="failed",
                latency_ms=int((time.monotonic() - started) * 1000),
                retry_count=retry_count,
                error_code=code,
                error_message=f"Provider 调用失败：{type(exc).__name__}",
                output={"untrusted": True, "response_received": False},
            )
            raise BriefModelCallError(
                code,
                f"Provider 调用失败：{type(exc).__name__}",
                retry_after=_extract_retry_after_seconds(exc),
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        message = _first_choice_message(response)
        content = str(_get(message, "content") or "")
        token_in = int(_get(response, "usage.prompt_tokens") or 0)
        token_out = int(_get(response, "usage.completion_tokens") or 0)
        self._append_attempt_step(
            phase=self._trace_phase or "model_call",
            iteration=self._trace_iteration,
            block_path=self._trace_block_path,
            provider_id=provider.id,
            provider_model=provider.model,
            prompt_version=BRIEF_PROMPT_VERSION,
            schema_version=BRIEF_SCHEMA_VERSION,
            evidence_ids=self._trace_evidence_ids,
            status="completed",
            token_input_count=token_in,
            token_output_count=token_out,
            latency_ms=latency_ms,
            output={
                "untrusted": True,
                "response_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "response_chars": len(content),
            },
            retry_count=retry_count,
        )
        return content, token_in, token_out, latency_ms

    def _call_model_with_retry(
        self,
        provider: AIProviderProfile,
        messages: list[dict[str, str]],
        *,
        state: "_RetryState",
    ) -> tuple[str, int, int, int]:
        """Spec §11.4：单 Provider 最多 ``BRIEF_PROVIDER_MAX_ATTEMPTS`` 次调用。

        只重试 transient 错误；permanent 错误立即抛出。每次 transient 失败后：
        1. 通过 ``state.bump`` 持久化累计重试计数与 ``next_retry_at``（重启保留）。
        2. 按 Retry-After / 2s/10s 退避 sleep。
        """
        last_error: Optional[BriefModelCallError] = None
        for attempt_index in range(1, BRIEF_PROVIDER_MAX_ATTEMPTS + 1):
            try:
                return self._call_model_once(
                    provider, messages, retry_count=state.total_retries
                )
            except BriefModelCallError as exc:
                last_error = exc
                if not _is_transient(exc.code):
                    raise  # permanent：不重试
                if attempt_index >= BRIEF_PROVIDER_MAX_ATTEMPTS:
                    raise  # 该 Provider 重试预算耗尽
                delay = _retry_delay_seconds(exc, attempt_index)
                state.bump(
                    self._repository,
                    error_code=exc.code,
                    error_message=(
                        f"Provider {_provider_label(provider)} 第 {attempt_index} 次"
                        f"调用失败（{exc.code}），{delay:.1f}s 后重试"
                    ),
                    delay_seconds=delay,
                    sleeper=self._sleep,
                )
                self._append_attempt_step(
                    phase="retry",
                    iteration=self._trace_iteration,
                    status="scheduled",
                    block_path=self._trace_block_path,
                    provider_id=provider.id,
                    provider_model=provider.model,
                    evidence_ids=self._trace_evidence_ids,
                    output={
                        "provider": provider.id,
                        "attempt": attempt_index,
                        "next_attempt": attempt_index + 1,
                        "error_code": exc.code,
                    },
                    retry_count=state.total_retries,
                    error_code=exc.code,
                    error_message=f"Provider 重试序号 {state.total_retries}",
                )
        # 不可达：循环要么 return，要么 raise。
        assert last_error is not None
        raise last_error

    def _call_model_with_failover(
        self,
        primary: AIProviderProfile,
        fallback: Optional[AIProviderProfile],
        messages: list[dict[str, str]],
        *,
        state: "_RetryState",
    ) -> tuple[str, int, int, int, AIProviderProfile]:
        """Spec §11.3 / §11.4：primary 重试耗尽后切 fallback。

        - 只有 transient 错误切换 fallback；permanent（鉴权 / 模型 / 上下文超限）立即抛出。
        - fallback 必须已由 ``resolve_fallback_provider`` 校验 96K；此处不再重复。
        - 返回 ``(text, in, out, ms, actual_provider)``，actual 可能是 fallback。
        """
        try:
            text, tin, tout, ms = self._call_model_with_retry(
                primary, messages, state=state
            )
            return text, tin, tout, ms, primary
        except BriefModelCallError as exc:
            if not _is_transient(exc.code) or fallback is None:
                raise
            self._append_attempt_step(
                phase="provider_switch",
                iteration=self._trace_iteration,
                status="scheduled",
                block_path=self._trace_block_path,
                provider_id=fallback.id,
                provider_model=fallback.model,
                evidence_ids=self._trace_evidence_ids,
                output={
                    "from_provider": primary.id,
                    "to_provider": fallback.id,
                    "reason": exc.code,
                },
                retry_count=state.total_retries,
                error_code="provider_switch",
                error_message=f"Provider {primary.id} 重试耗尽后切换 {fallback.id}",
            )
            text, tin, tout, ms = self._call_model_with_retry(
                fallback, messages, state=state
            )
            return text, tin, tout, ms, fallback


class BriefModelCallError(Exception):
    """Spec §13 Brief Provider 调用失败的稳定错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after


def _classify_model_error(exc: BaseException) -> str:
    """Spec §11.4 错误分类：区分永久性 vs 暂时性失败。

    KI-10 会在此函数基础上实现自动重试；KI-09 仅给出稳定 code 便于记录。
    """
    name = type(exc).__name__.lower()
    message = str(exc).lower() if str(exc) else ""
    status_code = _provider_status_code(exc)
    if status_code in {401, 403}:
        return "provider_auth_invalid"
    if status_code == 404:
        return "provider_model_unavailable"
    if status_code in {408, 409, 425, 429} or status_code >= 500:
        return "provider_transient_error"
    if 400 <= status_code < 500:
        return "provider_request_invalid"
    if "authentication" in name or "auth" in name or "401" in message or "403" in message:
        return "provider_auth_invalid"
    context_error_markers = (
        "context window",
        "context length",
        "context_length",
        "maximum context",
        "max context",
        "context limit",
        "context exceeded",
        "token limit",
        "too many tokens",
        "input too long",
        "prompt too long",
        "上下文超限",
        "上下文窗口",
        "上下文长度",
        "token 超限",
        "输入过长",
    )
    if (
        "notfound" in name
        or "not_found" in name
        or "404" in message
        or any(marker in message for marker in context_error_markers)
    ):
        return "provider_model_unavailable"
    if "rate" in name or "rate_limit" in message or "429" in message:
        return "provider_transient_error"
    if "timeout" in name or "timeout" in message:
        return "provider_transient_error"
    return "provider_transient_error"


def _provider_status_code(exc: BaseException) -> int:
    """从常见 Provider 异常结构提取 HTTP 状态码。"""

    candidates: list[Any] = [
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ]
    for candidate in candidates:
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            continue
        if 100 <= value <= 599:
            return value
    match = re.search(r"\b([45]\d{2})\b", str(exc))
    return int(match.group(1)) if match else 0


def _litellm_model_name(provider: AIProviderProfile) -> str:
    if "/" in provider.model:
        return provider.model
    if provider.provider in {"openai", "openai_compatible", "litellm_proxy"}:
        return f"openai/{provider.model}"
    if provider.provider:
        return f"{provider.provider}/{provider.model}"
    return provider.model


def _litellm_api_base(provider: AIProviderProfile) -> str:
    if provider.provider == "anthropic":
        return ""
    return provider.base_url.rstrip("/")


def _first_choice_message(response: Any) -> Any:
    choices = _get(response, "choices") or []
    if not choices:
        return {}
    return _get(choices[0], "message") or {}


def _get(value: Any, key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


class KnowledgeJobRunner:
    """Spec §12 持久队列调度器。

    使用方式：调用方按需驱动 ``tick_*``；不启动后台线程。KI-07 测试通过多次 tick
    验证单并发、FIFO、取消与恢复。KI-09 Brief generation 由 Brief queue 接入。
    """

    def __init__(
        self,
        repository: KnowledgeRepository,
        worker: ExtractionWorker,
        brief_worker: Optional[BriefWorker] = None,
    ) -> None:
        self._repository = repository
        self._worker = worker
        self._brief_worker = brief_worker

    def tick_extraction(
        self,
        *,
        lease_owner: str = "extraction-runner",
        lease_duration_seconds: int = 30,
    ) -> list[JobExecutionResult]:
        """跑一次 Extraction queue，直到没有 pending Job。

        单并发由 ``claim_next_job`` 的 ``with_for_update(skip_locked=True)`` 与
        ``status='pending'`` 过滤共同保证：同一时刻只有一个 worker 能 claim 成功。
        """

        results: list[JobExecutionResult] = []
        while True:
            job = self._repository.claim_next_job(
                "extraction",
                lease_owner=lease_owner,
                lease_duration_seconds=lease_duration_seconds,
            )
            if job is None:
                return results
            result = self._worker.execute(
                job,
                attempt_token=job.attempt_token,
                lease_owner=lease_owner,
            )
            results.append(result)
            if not result.accepted:
                # lease 被其他 worker 抢走 / token 不匹配 -> 退出本 tick。
                return results

    def tick_brief(
        self,
        *,
        lease_owner: str = "brief-runner",
        lease_duration_seconds: int = 120,
    ) -> list[JobExecutionResult]:
        """Brief queue 调度入口。

        KI-09 实现：
        - 若配置了 ``BriefWorker``，claim 一个 brief Job 后委托给 worker.execute。
        - 否则保持 KI-07 兼容占位行为：claim 后标记 brief_not_implemented。

        Spec §12 单并发：claim_next_job 用乐观 UPDATE 守卫，调用方串行驱动。
        """
        results: list[JobExecutionResult] = []
        job = self._repository.claim_next_job(
            "brief",
            lease_owner=lease_owner,
            lease_duration_seconds=lease_duration_seconds,
        )
        if job is None:
            return results
        if self._brief_worker is None:
            ok, _ = self._repository.complete_job(
                job.id,
                attempt_token=job.attempt_token,
                status="failed",
                stage="brief_not_implemented",
                error_code="brief_not_implemented",
                error_message="Brief generation 由 KI-09 实现",
            )
            results.append(
                JobExecutionResult(
                    job_id=job.id,
                    accepted=ok,
                    status="failed",
                    error_code="brief_not_implemented",
                    error_message="Brief generation 由 KI-09 实现",
                )
            )
            return results
        result = self._brief_worker.execute(job)
        results.append(result)
        return results

    def retry_extract(
        self,
        source_id: int,
    ) -> Optional[JobRecord]:
        """Spec §12 "手动重试进入队尾"。

        创建新的 ``kind=extract, queue=extraction, status=pending`` Job，排到队尾。
        不影响已有 Job 状态（已 succeeded 的旧 Job 保留为历史）。
        """
        from offerpilot.knowledge.repository import JobCreateInput

        return self._repository.create_job(
            JobCreateInput(
                kind="extract",
                queue="extraction",
                source_id=source_id,
                stage="retry_pending",
            )
        )


class KnowledgeWorkerRuntime:
    """应用生命周期内的双队列 Worker 运行时。

    每个队列固定一个线程：Extraction 与 Brief 可以并行，但同一队列不会在本运行时
    内并发消费。运行时不拥有数据库连接，停止时只设置事件并等待当前安全点返回；模型
    调用无法主动中断时，Worker 的 lease/cancel 门禁会拒绝迟到结果。

    ``start`` / ``stop`` 由应用生命周期调用；测试和 CLI 可使用 ``run_once`` 或
    ``run_forever``，无需依赖 FastAPI。
    """

    def __init__(
        self,
        runner: KnowledgeJobRunner,
        repository: KnowledgeRepository,
        *,
        poll_interval_seconds: float = 0.5,
        extraction_lease_seconds: int = 30,
        brief_lease_seconds: int = 300,
        on_extraction_succeeded: Optional[Callable[[int], Any]] = None,
    ) -> None:
        self._runner = runner
        self._repository = repository
        self._poll_interval_seconds = max(0.05, poll_interval_seconds)
        self._extraction_lease_seconds = max(1, extraction_lease_seconds)
        self._brief_lease_seconds = max(1, brief_lease_seconds)
        self._on_extraction_succeeded = on_extraction_succeeded
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._running = False

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._running

    def start(self) -> None:
        """启动两个固定单并发消费者；重复调用不会创建额外线程。"""

        with self._state_lock:
            if self._running:
                return
            self._recover_stale_jobs()
            self._repair_missing_brief_jobs()
            self._stop_event.clear()
            self._threads = [
                threading.Thread(
                    target=self._queue_loop,
                    args=("extraction",),
                    name="knowledge-extraction-worker",
                    daemon=True,
                ),
                threading.Thread(
                    target=self._queue_loop,
                    args=("brief",),
                    name="knowledge-brief-worker",
                    daemon=True,
                ),
            ]
            self._running = True
            for thread in self._threads:
                thread.start()

    def stop(self, timeout: Optional[float] = 10.0) -> bool:
        """请求停止并等待队列线程在安全点退出。"""

        with self._state_lock:
            if not self._running:
                return True
            self._stop_event.set()
            threads = list(self._threads)
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        for thread in threads:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            thread.join(remaining)
        with self._state_lock:
            self._threads = [thread for thread in threads if thread.is_alive()]
            self._running = bool(self._threads)
            if not self._running:
                self._threads = []
            return not self._running

    close = stop

    def _recover_stale_jobs(self) -> list[int]:
        """恢复过期 Job 并重新入队；兼容仅用于测试的旧式替身。"""

        recover = self._repository.recover_stale_running_jobs
        try:
            parameters = list(inspect.signature(recover).parameters.values())
        except (TypeError, ValueError):
            parameters = []
        supports_requeue = any(
            parameter.name == "requeue"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        if supports_requeue:
            return recover(requeue=True)
        return recover()

    def _repair_missing_brief_jobs(self) -> None:
        """恢复 Extraction 提交后、Brief 回调前崩溃留下的 Source。"""
        if self._on_extraction_succeeded is None:
            return
        for source in self._repository.list_sources(include_archived=True):
            if source.extraction_status != "extracted" or source.brief_status not in (
                "not_started",
                "outdated",
            ):
                continue
            active_brief_job = any(
                job.kind == "brief"
                and job.status in ("pending", "running")
                and not job.canceled
                and job.snapshot_id == source.active_snapshot_id
                for job in self._repository.list_jobs_for_source(source.id)
            )
            if active_brief_job:
                continue
            try:
                self._on_extraction_succeeded(source.id)
            except Exception:
                _LOGGER.exception(
                    "Knowledge Brief recovery callback failed for source %s", source.id
                )

    def run_once(self) -> dict[str, list[JobExecutionResult]]:
        """同步驱动两条队列一次，供 CLI、smoke 和测试使用。"""

        self._recover_stale_jobs()
        self._repair_missing_brief_jobs()
        return {
            "extraction": self._runner.tick_extraction(
                lease_owner="knowledge-extraction-runtime",
                lease_duration_seconds=self._extraction_lease_seconds,
            ),
            "brief": self._runner.tick_brief(
                lease_owner="knowledge-brief-runtime",
                lease_duration_seconds=self._brief_lease_seconds,
            ),
        }

    def run_forever(self, stop_event: Optional[threading.Event] = None) -> None:
        """在当前线程同步运行双队列，适合外部进程管理器托管。"""

        event = stop_event or self._stop_event
        self._recover_stale_jobs()
        self._repair_missing_brief_jobs()
        while not event.is_set():
            try:
                self._recover_stale_jobs()
                self._repair_missing_brief_jobs()
                extraction = self._runner.tick_extraction(
                    lease_owner="knowledge-extraction-runtime",
                    lease_duration_seconds=self._extraction_lease_seconds,
                )
                brief = self._runner.tick_brief(
                    lease_owner="knowledge-brief-runtime",
                    lease_duration_seconds=self._brief_lease_seconds,
                )
                if not extraction and not brief:
                    event.wait(self._poll_interval_seconds)
            except Exception:  # noqa: BLE001 - 运行时必须保持队列消费者存活
                _LOGGER.exception("Knowledge worker runtime tick failed")
                event.wait(self._poll_interval_seconds)

    def _queue_loop(self, queue: str) -> None:
        lease_owner = f"knowledge-{queue}-runtime"
        lease_seconds = (
            self._extraction_lease_seconds
            if queue == "extraction"
            else self._brief_lease_seconds
        )
        while not self._stop_event.is_set():
            try:
                self._recover_stale_jobs()
                if queue == "extraction":
                    results = self._runner.tick_extraction(
                        lease_owner=lease_owner,
                        lease_duration_seconds=lease_seconds,
                    )
                else:
                    results = self._runner.tick_brief(
                        lease_owner=lease_owner,
                        lease_duration_seconds=lease_seconds,
                    )
                if not results:
                    self._stop_event.wait(self._poll_interval_seconds)
            except Exception:  # noqa: BLE001 - 单个 Job 失败不能杀死消费者
                _LOGGER.exception("Knowledge %s worker tick failed", queue)
                self._stop_event.wait(self._poll_interval_seconds)
