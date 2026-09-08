"""Knowledge 持久化 Repository。

封装 SQLAlchemy 会话，提供：
- Source 创建/查询/状态更新（lifecycle/extraction/brief 独立）。
- Origin 追加（每次导入一条）。
- Snapshot 幂等 upsert（按 source_id+extractor_version 唯一）。
- Evidence 批量插入（含稳定 opaque ID 生成）。
- FTS 单事务重建。
- Evidence 搜索（FTS5 MATCH + bm25 加权 + Retrieval Trace）。
- Job 持久化与状态机。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.knowledge.search import ParsedQuery, SearchError, parse_query
from offerpilot.models import (
    KnowledgeBriefAttempt,
    KnowledgeBriefAttemptStep,
    KnowledgeEvidence,
    KnowledgeExtractionSnapshot,
    KnowledgeJob,
    KnowledgeLog,
    KnowledgeRetrievalTrace,
    KnowledgeSource,
    KnowledgeSourceAsset,
    KnowledgeSourceBrief,
    KnowledgeSourceOrigin,
)


_LOGGER = logging.getLogger(__name__)

# lease expired 的 running brief Job 最多自动重新入队次数；超过即转终态 failed，
# 避免慢/卡 provider 下无限 requeue 死循环。worker 的 Provider 重试属另一维度不计入。
BRIEF_LEASE_REQUEUE_MAX = 3


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite DateTime 不保留 tz；provenance 时间字段统一视为 UTC 读出。

    写入时是 tz-aware UTC，读回 naive 时补回 ``timezone.utc``，避免 Source 详情
    与 extractor 返回的 provenance 在 tz 语义上漂移。
    """

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class KnowledgeBriefAttemptError(Exception):
    """Spec §10.3 Brief Attempt 创建/提交时拒绝的稳定错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SourceRecord:
    id: int
    source_hash: str
    source_kind: str
    display_title: str
    title_hint: str
    author: str
    published_at: Optional[datetime]
    main_filename: str
    main_media_type: str
    main_relative_path: str
    manifest_json: str
    total_bytes: int
    token_count: int
    lifecycle: str
    extraction_status: str
    extraction_error_code: str
    extraction_error_message: str
    brief_status: str
    brief_block_reason: str
    brief_error_code: str
    brief_error_message: str
    active_snapshot_id: Optional[int]
    active_brief_id: Optional[int]
    archived_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SourceOriginRecord:
    id: int
    source_id: int
    import_method: str
    original_filename: str
    origin_url: str
    imported_at: datetime


@dataclass(frozen=True)
class SourceSnapshotRecord:
    id: int
    source_id: int
    extractor_version: str
    parser_version: str
    normalization_version: str
    tokenizer_version: str
    encoding: str
    detection_method: str
    digest: str
    canonical_text: str
    structure_manifest: str
    metadata_extraction_version: str
    token_count: int
    char_count: int
    created_at: datetime


@dataclass(frozen=True)
class SourceAssetRecord:
    id: int
    source_id: int
    logical_name: str
    media_type: str
    relative_path: str
    bytes_size: int
    sha256: str
    width: int
    height: int
    created_at: datetime


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    source_id: int
    snapshot_id: int
    kind: str
    block_kind: str
    ordinal: int
    heading_path: list[str]
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    canonical_excerpt: str
    search_text: str
    content_hash: str
    asset_id: Optional[int]
    previous_evidence_id: Optional[str]
    next_evidence_id: Optional[str]


@dataclass(frozen=True)
class JobRecord:
    id: int
    kind: str
    queue: str
    source_id: Optional[int]
    snapshot_id: Optional[int]
    stage: str
    status: str
    progress: int
    retry_count: int
    next_retry_at: Optional[datetime]
    error_code: str
    error_message: str
    canceled: bool
    lease_owner: str
    lease_expires_at: Optional[datetime]
    heartbeat_at: Optional[datetime]
    attempt_token: str
    created_at: datetime
    updated_at: datetime
    # 仅 Brief Job 使用；Extraction/Delete Job 为 None。
    attempt_id: Optional[int] = None


@dataclass(frozen=True)
class EvidenceSearchHit:
    evidence_id: str
    source_id: int
    snapshot_id: int
    block_kind: str
    heading_path: list[str]
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    canonical_excerpt: str
    snippet: str
    score: float
    previous_evidence_id: Optional[str]
    next_evidence_id: Optional[str]


@dataclass(frozen=True)
class DeleteJobSnapshot:
    """Spec §16.1: 永久删除返回 202 与 Delete Job。

    Job 在事务提交后已从 ``knowledge_jobs`` 表中清理,本快照用于 HTTP 响应,不重新
    查询数据库。
    """

    job_id: int
    source_id: int
    status: str
    stage: str
    created_at: datetime


@dataclass(frozen=True)
class RetrievalTraceRecord:
    """Spec §14.10 Retrieval Trace 读出视图。

    KI-08 验收点：每次搜索本地记录 query/filters/hits/duration_ms/label/error_code。
    ``hits`` 只保存稳定 ID + score，禁止保留 Evidence 原文。
    """

    id: int
    query: str
    filters: dict[str, Any]
    hits: list[dict[str, Any]]
    duration_ms: int
    evaluation_label: str
    error_code: str
    created_at: datetime


@dataclass(frozen=True)
class SourceBriefRecord:
    """Spec §10 / §14.7：Source 当前 Brief 读出视图。"""

    id: int
    source_id: int
    snapshot_id: int
    winning_attempt_id: int
    schema_version: int
    language: str
    payload_json: str
    outdated: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BriefAttemptRecord:
    """Spec §10 / §14.8：Brief Attempt 读出视图。

    Attempt 不暴露 API Key；``validation_report_json`` 与 ``candidate_payload_json``
    持久化便于 KI-11 评估，但 API 层只暴露脱敏后的字段。
    """

    id: int
    source_id: int
    snapshot_id: int
    status: str
    provider_id: str
    provider_model: str
    provider_base_url: str
    context_window: int
    max_output_tokens: int
    prompt_version: str
    schema_version: int
    language: str
    candidate_payload_json: str
    validation_report_json: str
    error_code: str
    error_message: str
    repair_count: int
    fallback_provider_id: str
    fallback_provider_model: str
    actual_provider_id: str
    actual_provider_model: str
    provider_retry_count: int
    next_retry_at: Optional[datetime]
    token_input_count: int
    token_output_count: int
    latency_ms: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BriefAttemptStepRecord:
    """Brief Attempt 单步过程记录。``output_json`` 仅含限长不可信摘要。"""

    id: int
    attempt_id: int
    sequence: int
    iteration: int
    phase: str
    status: str
    block_path: str
    provider_id: str
    provider_model: str
    prompt_version: str
    schema_version: int
    evidence_ids: list[str]
    output_json: str
    token_input_count: int
    token_output_count: int
    latency_ms: int
    retry_count: int
    error_code: str
    error_message: str
    created_at: datetime


@dataclass
class BriefAttemptCreateInput:
    """Spec §11.1 Attempt 创建时固定的 Provider/Prompt/Schema 快照。

    KI-10：同时固定 fallback 候选 Provider，运行途中设置变化不改变本 Attempt。
    """

    source_id: int
    snapshot_id: int
    provider_id: str
    provider_model: str
    provider_base_url: str
    context_window: int
    max_output_tokens: int
    prompt_version: str
    schema_version: int
    language: str = "zh-CN"
    status: str = "pending"
    fallback_provider_id: str = ""
    fallback_provider_model: str = ""


def _to_source_brief_record(row: KnowledgeSourceBrief) -> SourceBriefRecord:
    return SourceBriefRecord(
        id=row.id,
        source_id=row.source_id,
        snapshot_id=row.snapshot_id,
        winning_attempt_id=row.winning_attempt_id,
        schema_version=row.schema_version,
        language=row.language,
        payload_json=row.payload_json,
        outdated=bool(row.outdated),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_brief_attempt_record(row: KnowledgeBriefAttempt) -> BriefAttemptRecord:
    return BriefAttemptRecord(
        id=row.id,
        source_id=row.source_id,
        snapshot_id=row.snapshot_id,
        status=row.status,
        provider_id=row.provider_id,
        provider_model=row.provider_model,
        provider_base_url=row.provider_base_url,
        context_window=row.context_window,
        max_output_tokens=row.max_output_tokens,
        prompt_version=row.prompt_version,
        schema_version=row.schema_version,
        language=row.language,
        candidate_payload_json=row.candidate_payload_json or "",
        validation_report_json=row.validation_report_json or "{}",
        error_code=row.error_code,
        error_message=row.error_message,
        repair_count=row.repair_count,
        fallback_provider_id=row.fallback_provider_id or "",
        fallback_provider_model=row.fallback_provider_model or "",
        actual_provider_id=row.actual_provider_id or "",
        actual_provider_model=row.actual_provider_model or "",
        provider_retry_count=row.provider_retry_count or 0,
        next_retry_at=row.next_retry_at,
        token_input_count=row.token_input_count,
        token_output_count=row.token_output_count,
        latency_ms=row.latency_ms,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_brief_attempt_step_record(row: KnowledgeBriefAttemptStep) -> BriefAttemptStepRecord:
    try:
        evidence_ids = json.loads(row.evidence_ids_json or "[]")
    except (TypeError, json.JSONDecodeError):
        evidence_ids = []
    if not isinstance(evidence_ids, list):
        evidence_ids = []
    return BriefAttemptStepRecord(
        id=row.id,
        attempt_id=row.attempt_id,
        sequence=row.sequence,
        iteration=row.iteration,
        phase=row.phase,
        status=row.status,
        block_path=row.block_path or "",
        provider_id=row.provider_id or "",
        provider_model=row.provider_model or "",
        prompt_version=row.prompt_version or "",
        schema_version=row.schema_version or 0,
        evidence_ids=[str(item) for item in evidence_ids[:200]],
        output_json=row.output_json or "{}",
        token_input_count=row.token_input_count or 0,
        token_output_count=row.token_output_count or 0,
        latency_ms=row.latency_ms or 0,
        retry_count=row.retry_count or 0,
        error_code=row.error_code or "",
        error_message=(row.error_message or "")[:500],
        created_at=row.created_at,
    )


@dataclass
class SourceCreateInput:
    source_hash: str
    source_kind: str
    title_hint: str
    main_filename: str
    main_media_type: str
    main_relative_path: str
    manifest_json: str
    total_bytes: int
    token_count: int


@dataclass
class OriginCreateInput:
    source_id: int
    import_method: str
    original_filename: str = ""
    origin_url: str = ""


@dataclass
class SnapshotCreateInput:
    source_id: int
    extractor_version: str
    parser_version: str
    normalization_version: str
    tokenizer_version: str
    encoding: str
    detection_method: str
    canonical_text: str
    structure_manifest: str
    digest: str
    token_count: int
    char_count: int
    # KBR-02：Snapshot 沿自身所有权记录元数据提取版本。
    metadata_extraction_version: str = ""
    # KBR-02：frontmatter 白名单 provenance 沿 Source 所有权写入；Worker 从
    # Extraction 透传，commit 时更新 Source 行。display_title 为空时用 frontmatter
    # title 填充（使其进入 FTS source_title 可定位），origin_url 为空时用 frontmatter url。
    provenance_title: str = ""
    provenance_author: str = ""
    provenance_url: str = ""
    provenance_published_at: Optional[datetime] = None


@dataclass
class EvidenceDraftInput:
    block_kind: str
    heading_path: Sequence[str]
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    canonical_excerpt: str
    search_text: str
    content_hash: str
    locator: str
    kind: str = "text"
    logical_name: str = ""
    alt_text: str = ""


@dataclass
class AssetCreateInput:
    logical_name: str
    media_type: str
    relative_path: str
    bytes_size: int
    sha256: str
    width: int
    height: int


@dataclass
class JobCreateInput:
    kind: str
    queue: str
    source_id: Optional[int] = None
    attempt_id: Optional[int] = None
    snapshot_id: Optional[int] = None
    stage: str = ""


def _to_source_record(row: KnowledgeSource) -> SourceRecord:
    return SourceRecord(
        id=row.id,
        source_hash=row.source_hash,
        source_kind=row.source_kind,
        display_title=row.display_title,
        title_hint=row.title_hint,
        author=row.author,
        published_at=_as_utc(row.published_at),
        main_filename=row.main_filename,
        main_media_type=row.main_media_type,
        main_relative_path=row.main_relative_path,
        manifest_json=row.manifest_json,
        total_bytes=row.total_bytes,
        token_count=row.token_count,
        lifecycle=row.lifecycle,
        extraction_status=row.extraction_status,
        extraction_error_code=row.extraction_error_code,
        extraction_error_message=row.extraction_error_message,
        brief_status=row.brief_status,
        brief_block_reason=row.brief_block_reason,
        brief_error_code=row.brief_error_code,
        brief_error_message=row.brief_error_message,
        active_snapshot_id=row.active_snapshot_id,
        active_brief_id=row.active_brief_id,
        archived_at=row.archived_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_origin_record(row: KnowledgeSourceOrigin) -> SourceOriginRecord:
    return SourceOriginRecord(
        id=row.id,
        source_id=row.source_id,
        import_method=row.import_method,
        original_filename=row.original_filename,
        origin_url=row.origin_url,
        imported_at=row.imported_at,
    )


def _to_snapshot_record(row: KnowledgeExtractionSnapshot) -> SourceSnapshotRecord:
    return SourceSnapshotRecord(
        id=row.id,
        source_id=row.source_id,
        extractor_version=row.extractor_version,
        parser_version=row.parser_version,
        normalization_version=row.normalization_version,
        tokenizer_version=row.tokenizer_version,
        encoding=row.encoding,
        detection_method=row.detection_method,
        digest=row.digest,
        canonical_text=row.canonical_text,
        structure_manifest=row.structure_manifest,
        metadata_extraction_version=row.metadata_extraction_version,
        token_count=row.token_count,
        char_count=row.char_count,
        created_at=row.created_at,
    )


def _get_first_origin(
    session: Session, source_id: int
) -> Optional[KnowledgeSourceOrigin]:
    """按 id 升序取首条 Origin（provenance URL 与 frontmatter url 补全共用）。"""

    return (
        session.execute(
            select(KnowledgeSourceOrigin)
            .where(KnowledgeSourceOrigin.source_id == source_id)
            .order_by(KnowledgeSourceOrigin.id.asc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def _build_source_provenance(session: Session, source_id: int) -> dict[str, Any]:
    """从 Source / 首条 Origin / active Snapshot 组装非空 provenance 字段。

    空字段不进入字典（Spec：空字段不制造占位噪声）。``captured_at`` 与
    ``metadata_extraction_version`` 对正常 extracted Source 总存在。
    """

    source = session.get(KnowledgeSource, source_id)
    if source is None:
        return {}
    provenance: dict[str, Any] = {}
    if source.display_title:
        provenance["title"] = source.display_title
    if source.author:
        provenance["author"] = source.author
    origin_row = _get_first_origin(session, source_id)
    if origin_row is not None and origin_row.origin_url:
        provenance["url"] = origin_row.origin_url
    if source.published_at is not None:
        provenance["published_at"] = _as_utc(source.published_at)
    provenance["captured_at"] = _as_utc(source.created_at)
    if source.active_snapshot_id:
        snapshot = session.get(KnowledgeExtractionSnapshot, source.active_snapshot_id)
        if snapshot is not None and snapshot.metadata_extraction_version:
            provenance["metadata_extraction_version"] = (
                snapshot.metadata_extraction_version
            )
    return provenance


def _to_asset_record(row: KnowledgeSourceAsset) -> SourceAssetRecord:
    return SourceAssetRecord(
        id=row.id,
        source_id=row.source_id,
        logical_name=row.logical_name,
        media_type=row.media_type,
        relative_path=row.relative_path,
        bytes_size=row.bytes,
        sha256=row.sha256,
        width=row.width,
        height=row.height,
        created_at=row.created_at,
    )


def _to_evidence_record(row: KnowledgeEvidence) -> EvidenceRecord:
    heading_path_json = row.heading_path_json or "[]"
    try:
        heading_path_value: Any = json.loads(heading_path_json)
    except json.JSONDecodeError:
        heading_path_value = []
    heading_path = (
        [str(item) for item in heading_path_value]
        if isinstance(heading_path_value, list)
        else []
    )
    return EvidenceRecord(
        id=row.id,
        source_id=row.source_id,
        snapshot_id=row.snapshot_id,
        kind=row.kind,
        block_kind=row.block_kind,
        ordinal=row.ordinal,
        heading_path=heading_path,
        char_start=row.char_start,
        char_end=row.char_end,
        line_start=row.line_start,
        line_end=row.line_end,
        canonical_excerpt=row.canonical_excerpt,
        search_text=row.search_text,
        content_hash=row.content_hash,
        asset_id=row.asset_id,
        previous_evidence_id=row.previous_evidence_id,
        next_evidence_id=row.next_evidence_id,
    )


def _to_job_record(row: KnowledgeJob) -> JobRecord:
    return JobRecord(
        id=row.id,
        kind=row.kind,
        queue=row.queue,
        source_id=row.source_id,
        snapshot_id=row.snapshot_id,
        stage=row.stage,
        status=row.status,
        progress=row.progress,
        retry_count=row.retry_count,
        next_retry_at=row.next_retry_at,
        error_code=row.error_code,
        error_message=row.error_message,
        canceled=row.canceled,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        heartbeat_at=row.heartbeat_at,
        attempt_token=row.attempt_token,
        created_at=row.created_at,
        updated_at=row.updated_at,
        attempt_id=row.attempt_id,
    )


def _new_attempt_token() -> str:
    """Spec §12 "Job claim 使用 lease owner、expiry 和 heartbeat"。

    每次生成新 token；complete/heartbeat 必须验证 token 匹配，拒绝迟到 lease。
    """
    import secrets as _secrets

    return _secrets.token_hex(16)


def _as_utc_datetime(value: Optional[datetime]) -> Optional[datetime]:
    """将 SQLite 返回的 naive 时间按 UTC 解释，统一 lease 比较。"""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class EvidencePage:
    items: list[EvidenceRecord] = field(default_factory=list)
    next_cursor: Optional[int] = None


class KnowledgeRepository:
    """Knowledge 表族的 SQL 访问层。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _lock_active_job_for_write(
        session: Session,
        *,
        job_id: int,
        attempt_token: str,
        moment: datetime,
    ) -> bool:
        """原子获取一个仍在 lease 内且未取消的 running Job 写入资格。

        先读 Job 再更新会留下取消/过期竞态；这里用带完整门禁的 UPDATE 抢占
        SQLite 写锁，后续 Attempt、Brief、Job 更新都在同一事务中完成。
        """

        result = session.execute(
            text(
                """
                UPDATE knowledge_jobs
                SET updated_at = :moment
                WHERE id = :jid
                  AND status = 'running'
                  AND canceled = 0
                  AND attempt_token = :token
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at > :moment
                """
            ),
            {"jid": job_id, "token": attempt_token, "moment": moment},
        )
        return int(getattr(result, "rowcount", 0) or 0) == 1

    # Source

    def create_source(self, data: SourceCreateInput) -> SourceRecord:
        with self._session_factory() as session:
            model = KnowledgeSource(
                source_hash=data.source_hash,
                source_kind=data.source_kind,
                display_title="",
                title_hint=data.title_hint,
                main_filename=data.main_filename,
                main_media_type=data.main_media_type,
                main_relative_path=data.main_relative_path,
                manifest_json=data.manifest_json,
                total_bytes=data.total_bytes,
                token_count=data.token_count,
                lifecycle="active",
                extraction_status="pending",
                extraction_error_code="",
                extraction_error_message="",
                brief_status="not_started",
                brief_block_reason="",
                brief_error_code="",
                brief_error_message="",
            )
            session.add(model)
            session.commit()
            session.refresh(model)
            return _to_source_record(model)

    def get_source(self, source_id: int) -> Optional[SourceRecord]:
        with self._session_factory() as session:
            row = session.get(KnowledgeSource, source_id)
            # deleting 是永久删除的过渡态；在物理目录进入 quarantine 前后都不应
            # 再从详情、原文或附件下载路径暴露 Source。
            if (
                row is None
                or row.deleted_at is not None
                or row.lifecycle == "deleting"
            ):
                return None
            return _to_source_record(row)

    def get_deleting_source(self, source_id: int) -> Optional[SourceRecord]:
        """供删除 Worker 读取 ``deleting`` 过渡态，不对普通 API 暴露。"""

        with self._session_factory() as session:
            row = session.get(KnowledgeSource, source_id)
            if row is None or row.deleted_at is not None or row.lifecycle != "deleting":
                return None
            return _to_source_record(row)

    def get_source_by_hash(self, source_hash: str) -> Optional[SourceRecord]:
        """KI-06：去重查询必须排除 ``lifecycle=deleting`` 的 Source。

        Spec §5.4：删除不保留 source_hash 墓碑;再次上传相同内容必须创建新 Source。``deleting``
        期间的 Source 行仍存在,但语义上等价于已删除,dedup 不应命中。
        """
        with self._session_factory() as session:
            stmt = select(KnowledgeSource).where(
                KnowledgeSource.source_hash == source_hash,
                KnowledgeSource.deleted_at.is_(None),
                KnowledgeSource.lifecycle != "deleting",
            )
            row = session.scalars(stmt).first()
            return _to_source_record(row) if row is not None else None

    def get_deleting_source_by_hash(self, source_hash: str) -> Optional[SourceRecord]:
        """返回同 hash 的删除中过渡 Source，供上传冲突返回稳定错误。"""

        with self._session_factory() as session:
            stmt = select(KnowledgeSource).where(
                KnowledgeSource.source_hash == source_hash,
                KnowledgeSource.deleted_at.is_(None),
                KnowledgeSource.lifecycle == "deleting",
            )
            row = session.scalars(stmt).first()
            return _to_source_record(row) if row is not None else None

    def list_sources(self, *, include_archived: bool = False) -> list[SourceRecord]:
        """KI-06：默认只返回 ``active`` Source;显式筛选可查看归档资料。

        Spec §5.3：归档 Source 默认不出现在列表和普通 Evidence 检索中。``deleting``
        lifecycle 在 Spec §13 中是过渡态,正常运行时不可见,本方法始终排除。
        """
        with self._session_factory() as session:
            stmt = select(KnowledgeSource).where(
                KnowledgeSource.deleted_at.is_(None),
                KnowledgeSource.lifecycle != "deleting",
                KnowledgeSource.source_kind != "captured_interview_note",
            )
            if not include_archived:
                stmt = stmt.where(KnowledgeSource.lifecycle == "active")
            stmt = stmt.order_by(
                KnowledgeSource.created_at.desc(), KnowledgeSource.id.desc()
            )
            return [_to_source_record(row) for row in session.scalars(stmt)]

    def list_brief_enqueue_candidates(self, *, limit: int = 100) -> list[int]:
        """返回 Extraction 已完成但当前 Snapshot 尚无 Brief Job 的 Source。

        ``brief_status=pending`` 是 Extraction 提交事务留下的可恢复标记；只扫描
        该状态，避免把设置变更产生的 ``outdated`` 误当成自动批量重建。
        """
        clamped = max(1, min(500, limit))
        with self._session_factory() as session:
            active_job = (
                select(KnowledgeJob.id)
                .where(
                    KnowledgeJob.source_id == KnowledgeSource.id,
                    KnowledgeJob.kind == "brief",
                    KnowledgeJob.snapshot_id == KnowledgeSource.active_snapshot_id,
                    KnowledgeJob.status.in_(("pending", "running")),
                    KnowledgeJob.canceled.is_(False),
                )
                .correlate(KnowledgeSource)
            )
            stmt = (
                select(KnowledgeSource.id)
                .where(
                    KnowledgeSource.lifecycle == "active",
                    KnowledgeSource.deleted_at.is_(None),
                    KnowledgeSource.extraction_status == "extracted",
                    KnowledgeSource.brief_status == "pending",
                    KnowledgeSource.brief_block_reason == "",
                    KnowledgeSource.active_snapshot_id.is_not(None),
                    ~active_job.exists(),
                )
                .order_by(KnowledgeSource.updated_at.asc(), KnowledgeSource.id.asc())
                .limit(clamped)
            )
            return [int(source_id) for source_id in session.scalars(stmt)]

    def update_source_state(
        self,
        source_id: int,
        *,
        extraction_status: Optional[str] = None,
        extraction_error_code: Optional[str] = None,
        extraction_error_message: Optional[str] = None,
        brief_status: Optional[str] = None,
        brief_block_reason: Optional[str] = None,
        brief_error_code: Optional[str] = None,
        brief_error_message: Optional[str] = None,
        active_snapshot_id: Optional[int] = None,
    ) -> Optional[SourceRecord]:
        with self._session_factory() as session:
            row = session.get(KnowledgeSource, source_id)
            if row is None or row.deleted_at is not None:
                return None
            if extraction_status is not None:
                row.extraction_status = extraction_status
            if extraction_error_code is not None:
                row.extraction_error_code = extraction_error_code
            if extraction_error_message is not None:
                row.extraction_error_message = extraction_error_message
            if brief_status is not None:
                row.brief_status = brief_status
            if brief_block_reason is not None:
                row.brief_block_reason = brief_block_reason
            if brief_error_code is not None:
                row.brief_error_code = brief_error_code
            if brief_error_message is not None:
                row.brief_error_message = brief_error_message
            if active_snapshot_id is not None:
                row.active_snapshot_id = active_snapshot_id
            session.commit()
            session.refresh(row)
            return _to_source_record(row)

    def update_display_title(self, source_id: int, display_title: str) -> Optional[SourceRecord]:
        """KI-05：用户可编辑 display_title,不影响 title_hint、Evidence ID 与 Snapshot digest。

        Spec §5.2：``display_title`` 修改后列表 / 详情 / 搜索展示一致。display_title UPDATE
        与 FTS ``source_title`` UPDATE 在同一事务内提交,确保二者原子可见;任一失败回滚后
        Source 标题保持旧值,Spec §15 "FTS 失败必须显式报错" 通过向上层抛出实现。
        """
        cleaned = display_title.strip()
        with self._session_factory() as session:
            row = session.get(KnowledgeSource, source_id)
            if row is None or row.deleted_at is not None or row.lifecycle == "deleting":
                return None
            row.display_title = cleaned
            search_title = cleaned or row.title_hint or row.main_filename
            session.execute(
                text(
                    "UPDATE knowledge_evidence_fts "
                    "SET source_title = :title WHERE source_id = :sid"
                ),
                {"title": search_title, "sid": source_id},
            )
            session.commit()
            session.refresh(row)
            return _to_source_record(row)

    def archive_source(self, source_id: int) -> Optional[SourceRecord]:
        """KI-06：归档 Source。

        Spec §5.3：归档是同步 SQLite 操作,只改 lifecycle + archived_at,不删除文件、
        Evidence、Brief、Job 历史。归档 Source 默认不在列表与普通搜索中可见,但详情 /
        原文 / 附件仍可读。归档不会自动过期或后台清理。
        """
        with self._session_factory() as session:
            row = session.get(KnowledgeSource, source_id)
            if row is None or row.deleted_at is not None or row.lifecycle == "deleting":
                return None
            if row.lifecycle == "archived":
                return _to_source_record(row)
            row.lifecycle = "archived"
            row.archived_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(row)
            return _to_source_record(row)

    def unarchive_source(self, source_id: int) -> Optional[SourceRecord]:
        """KI-06：取消归档 Source。

        Spec §5.3：取消归档同样是同步 SQLite 操作,lifecycle 改回 ``active``,
        archived_at 清空。不触发 Extraction / Brief / Evidence 重建。
        """
        with self._session_factory() as session:
            row = session.get(KnowledgeSource, source_id)
            if row is None or row.deleted_at is not None or row.lifecycle == "deleting":
                return None
            if row.lifecycle == "active":
                return _to_source_record(row)
            row.lifecycle = "active"
            row.archived_at = None
            session.commit()
            session.refresh(row)
            return _to_source_record(row)

    def begin_delete(self, source_id: int) -> Optional[tuple[SourceRecord, int]]:
        """KI-06：开始永久删除流程。

        Spec §5.4：删除请求返回 Delete Job,Source 立即进入 ``deleting`` 并拒绝新 Job。
        本方法在单个事务中:
        1. 将 Source lifecycle 改为 ``deleting``;
        2. 取消该 Source 所有 pending / running extract / brief Job;
        3. 创建一个 ``kind=delete, queue=extraction, status=pending`` Delete Job；
           lease 由 Extraction Worker claim 时生成。

        返回 ``Source`` 当前快照与新建 Delete Job 的 ID。
        """
        with self._session_factory() as session:
            row = session.get(KnowledgeSource, source_id)
            if row is None or row.deleted_at is not None:
                return None
            if row.lifecycle == "deleting":
                return None
            row.lifecycle = "deleting"
            active_jobs = session.scalars(
                select(KnowledgeJob).where(
                    KnowledgeJob.source_id == source_id,
                    KnowledgeJob.kind.in_(("extract", "brief")),
                    KnowledgeJob.status.in_(("pending", "running")),
                    KnowledgeJob.canceled.is_(False),
                )
            ).all()
            now = datetime.now(timezone.utc)
            for job in active_jobs:
                job.status = "canceled"
                job.canceled = True
                job.stage = "canceled_by_delete"
                job.updated_at = now
            delete_job = KnowledgeJob(
                kind="delete",
                queue="extraction",
                source_id=source_id,
                stage="delete_pending",
                status="pending",
                progress=0,
            )
            session.add(delete_job)
            session.flush()
            delete_job_id = delete_job.id
            session.commit()
            session.refresh(row)
            return _to_source_record(row), delete_job_id

    def complete_purge(self, source_id: int) -> bool:
        """KI-06：在调用方提供的事务外执行完整删除事务。

        Spec §5.4：单 SQLite 事务删除 FTS、Evidence、Snapshot、Asset、Origin、Job 与
        Source 行。本方法不接触文件系统,Service 层负责目录 rename 与 quarantine 清理。
        成功提交返回 True;若 Source 不在 ``deleting`` 状态(已被并发清理)返回 False。

        同一事务内插入 ``knowledge_logs`` 行,确保删除结果与日志原子可见——Spec §5.4
        验收点 10 要求日志必须存在,不允许"Source 已删但日志丢失"的中间态。
        """
        with self._session_factory() as session:
            row = session.get(KnowledgeSource, source_id)
            if row is None or row.lifecycle != "deleting":
                return False
            # active_* 是当前 Source 的非 FK 引用。先清空再删目标行，既保持
            # 引用不悬空，也允许后续在 SQLite 中启用删除保护触发器。
            row.active_snapshot_id = None
            row.active_brief_id = None
            session.execute(
                text(
                    "DELETE FROM knowledge_evidence_fts WHERE source_id = :sid"
                ),
                {"sid": source_id},
            )
            session.execute(
                text("DELETE FROM knowledge_evidence WHERE source_id = :sid"),
                {"sid": source_id},
            )
            session.execute(
                text(
                    "DELETE FROM knowledge_extraction_snapshots WHERE source_id = :sid"
                ),
                {"sid": source_id},
            )
            session.execute(
                text("DELETE FROM knowledge_source_assets WHERE source_id = :sid"),
                {"sid": source_id},
            )
            session.execute(
                text("DELETE FROM knowledge_source_origins WHERE source_id = :sid"),
                {"sid": source_id},
            )
            # KI-09：Spec §5.4 删除会清理 Brief/Attempt；外键 CASCADE 也会自动处理，
            # 但显式 DELETE 保证表结构未启用外键时仍清理（SQLite 默认不开 PRAGMA）。
            session.execute(
                text("DELETE FROM knowledge_source_briefs WHERE source_id = :sid"),
                {"sid": source_id},
            )
            session.execute(
                text(
                    "DELETE FROM knowledge_brief_attempt_steps "
                    "WHERE attempt_id IN (SELECT id FROM knowledge_brief_attempts WHERE source_id = :sid)"
                ),
                {"sid": source_id},
            )
            session.execute(
                text("DELETE FROM knowledge_brief_attempts WHERE source_id = :sid"),
                {"sid": source_id},
            )
            _delete_retrieval_traces_for_source(session, source_id)
            session.execute(
                text(
                    "DELETE FROM knowledge_jobs WHERE source_id = :sid"
                ),
                {"sid": source_id},
            )
            session.delete(row)
            session.add(
                KnowledgeLog(
                    source_id=source_id,
                    action="source_deleted",
                    result="succeeded",
                    error_code="",
                )
            )
            session.commit()
            return True

    def find_latest_extract_job_id(self, source_id: int) -> Optional[int]:
        """KI-05：去重路径复用 Source 已有的 Extract Job,避免重复排队。

        优先返回状态为 ``pending/running`` 的活跃 Job;若不存在,返回最近一个 extract
        Job (通常是首次成功的 Job)。Spec §5.1：命中已有 Source 时不创建第二个 Job。
        """
        with self._session_factory() as session:
            active_stmt = (
                select(KnowledgeJob)
                .where(
                    KnowledgeJob.source_id == source_id,
                    KnowledgeJob.kind == "extract",
                    KnowledgeJob.queue == "extraction",
                    KnowledgeJob.status.in_(("pending", "running")),
                    KnowledgeJob.canceled.is_(False),
                )
                .order_by(KnowledgeJob.created_at.desc(), KnowledgeJob.id.desc())
                .limit(1)
            )
            active = session.scalars(active_stmt).first()
            if active is not None:
                return active.id
            latest_stmt = (
                select(KnowledgeJob)
                .where(
                    KnowledgeJob.source_id == source_id,
                    KnowledgeJob.kind == "extract",
                )
                .order_by(KnowledgeJob.created_at.desc(), KnowledgeJob.id.desc())
                .limit(1)
            )
            latest = session.scalars(latest_stmt).first()
            return latest.id if latest is not None else None

    def update_main_relative_path(
        self, source_id: int, main_relative_path: str
    ) -> Optional[SourceRecord]:
        with self._session_factory() as session:
            row = session.get(KnowledgeSource, source_id)
            if row is None or row.deleted_at is not None:
                return None
            row.main_relative_path = main_relative_path
            session.commit()
            session.refresh(row)
            return _to_source_record(row)

    # Origin

    def append_origin(self, data: OriginCreateInput) -> SourceOriginRecord:
        with self._session_factory() as session:
            model = KnowledgeSourceOrigin(
                source_id=data.source_id,
                import_method=data.import_method,
                original_filename=data.original_filename,
                origin_url=data.origin_url,
            )
            session.add(model)
            session.commit()
            session.refresh(model)
            return _to_origin_record(model)

    def list_origins(self, source_id: int) -> list[SourceOriginRecord]:
        with self._session_factory() as session:
            stmt = (
                select(KnowledgeSourceOrigin)
                .where(KnowledgeSourceOrigin.source_id == source_id)
                .order_by(KnowledgeSourceOrigin.imported_at.desc(), KnowledgeSourceOrigin.id.desc())
            )
            return [_to_origin_record(row) for row in session.scalars(stmt)]

    def get_source_provenance(self, source_id: int) -> dict[str, Any]:
        """Spec KBR-02：组装 Source 的最小 provenance（只含非空字段）。

        沿 Source / Source Origin / Snapshot 现有所有权边界：标题用 display_title
        （frontmatter title 填充或用户 PATCH），URL 用首条 Origin.origin_url，作者/
        发布时间用 Source 列，系统捕获时间用 Source.created_at，元数据提取版本用
        active Snapshot。空字段不进入字典，避免占位噪声。provenance 不参与 Evidence
        FTS 召回计权，仅用于出处展示。
        """

        with self._session_factory() as session:
            return _build_source_provenance(session, source_id)

    def get_source_provenance_map(
        self, source_ids: Sequence[int]
    ) -> dict[int, dict[str, Any]]:
        """批量组装多个 Source 的 provenance，供 Evidence 搜索结果出处展示。"""

        unique_ids = [sid for sid in dict.fromkeys(source_ids) if sid]
        if not unique_ids:
            return {}
        result: dict[int, dict[str, Any]] = {}
        with self._session_factory() as session:
            for sid in unique_ids:
                result[sid] = _build_source_provenance(session, sid)
        return result

    # Asset

    def list_assets(self, source_id: int) -> list[SourceAssetRecord]:
        with self._session_factory() as session:
            stmt = (
                select(KnowledgeSourceAsset)
                .where(KnowledgeSourceAsset.source_id == source_id)
                .order_by(KnowledgeSourceAsset.id.asc())
            )
            return [_to_asset_record(row) for row in session.scalars(stmt)]

    def get_asset(self, asset_id: int) -> Optional[SourceAssetRecord]:
        with self._session_factory() as session:
            row = session.get(KnowledgeSourceAsset, asset_id)
            return _to_asset_record(row) if row is not None else None

    # Snapshot

    def get_snapshot_by_version(
        self, source_id: int, extractor_version: str
    ) -> Optional[SourceSnapshotRecord]:
        with self._session_factory() as session:
            stmt = select(KnowledgeExtractionSnapshot).where(
                KnowledgeExtractionSnapshot.source_id == source_id,
                KnowledgeExtractionSnapshot.extractor_version == extractor_version,
            )
            row = session.scalars(stmt).first()
            return _to_snapshot_record(row) if row is not None else None

    def get_snapshot(self, snapshot_id: int) -> Optional[SourceSnapshotRecord]:
        with self._session_factory() as session:
            row = session.get(KnowledgeExtractionSnapshot, snapshot_id)
            return _to_snapshot_record(row) if row is not None else None

    def get_source_filter_summary(self, source_id: int) -> dict[str, Any]:
        """Spec KBR-03：从 active Snapshot 的 structure_manifest 读出 evidence policy 摘要。

        返回 ``filtered_block_total``、``filtered_by_rule``（rule_id→count）与
        ``evidence_policy_version``。摘要只含计数与稳定 rule_id，不重复保存被过滤正文、
        URL、作者名或本机路径。无 active Snapshot 或旧 Snapshot 缺字段时返回零值摘要。
        """

        empty = {
            "filtered_block_total": 0,
            "filtered_by_rule": {},
            "evidence_policy_version": "",
        }
        with self._session_factory() as session:
            source = session.get(KnowledgeSource, source_id)
            if source is None or source.active_snapshot_id is None:
                return empty
            snapshot = session.get(
                KnowledgeExtractionSnapshot, source.active_snapshot_id
            )
            if snapshot is None or not snapshot.structure_manifest:
                return empty
            try:
                manifest = json.loads(snapshot.structure_manifest)
            except json.JSONDecodeError:
                return empty
            if not isinstance(manifest, dict):
                return empty
            filtered_by_rule = manifest.get("filtered_by_rule", {})
            if not isinstance(filtered_by_rule, dict):
                filtered_by_rule = {}
            return {
                "filtered_block_total": int(manifest.get("filtered_block_total", 0) or 0),
                "filtered_by_rule": {
                    str(rule_id): int(count)
                    for rule_id, count in filtered_by_rule.items()
                    if isinstance(count, (int, float))
                },
                "evidence_policy_version": str(
                    manifest.get("evidence_policy_version", "") or ""
                ),
            }

    # Job

    def create_job(self, data: JobCreateInput) -> JobRecord:
        with self._session_factory() as session:
            existing = None
            if data.source_id is not None:
                active_job_conditions = [
                    KnowledgeJob.source_id == data.source_id,
                    KnowledgeJob.kind == data.kind,
                    KnowledgeJob.status.in_(("pending", "running")),
                    KnowledgeJob.canceled.is_(False),
                ]
                # Brief Job 必须绑定当前 Snapshot。否则旧 Snapshot 的 pending
                # Job 会被错误复用，新 Snapshot 永远没有自己的 Brief 候选。
                if data.kind == "brief":
                    if data.snapshot_id is None:
                        active_job_conditions.append(KnowledgeJob.snapshot_id.is_(None))
                    else:
                        active_job_conditions.append(
                            KnowledgeJob.snapshot_id == data.snapshot_id
                        )
                if data.attempt_id is not None:
                    active_job_conditions.append(
                        KnowledgeJob.attempt_id == data.attempt_id
                    )
                existing = session.execute(
                    select(KnowledgeJob)
                    .where(*active_job_conditions)
                    .order_by(KnowledgeJob.created_at.desc(), KnowledgeJob.id.desc())
                    .limit(1)
                ).scalars().first()
            if existing is not None:
                return _to_job_record(existing)
            model = KnowledgeJob(
                kind=data.kind,
                queue=data.queue,
                source_id=data.source_id,
                attempt_id=data.attempt_id,
                snapshot_id=data.snapshot_id,
                stage=data.stage,
                status="pending",
            )
            session.add(model)
            session.commit()
            session.refresh(model)
            return _to_job_record(model)

    def get_job(self, job_id: int) -> Optional[JobRecord]:
        with self._session_factory() as session:
            row = session.get(KnowledgeJob, job_id)
            return _to_job_record(row) if row is not None else None

    def list_jobs_for_source(self, source_id: int) -> list[JobRecord]:
        with self._session_factory() as session:
            stmt = (
                select(KnowledgeJob)
                .where(KnowledgeJob.source_id == source_id)
                .order_by(KnowledgeJob.created_at.desc(), KnowledgeJob.id.desc())
            )
            return [_to_job_record(row) for row in session.scalars(stmt)]

    def update_job(
        self,
        job_id: int,
        *,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        progress: Optional[int] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Optional[JobRecord]:
        with self._session_factory() as session:
            row = session.get(KnowledgeJob, job_id)
            if row is None:
                return None
            if status is not None:
                row.status = status
            if stage is not None:
                row.stage = stage
            if progress is not None:
                row.progress = progress
            if error_code is not None:
                row.error_code = error_code
            if error_message is not None:
                row.error_message = error_message
            session.commit()
            session.refresh(row)
            return _to_job_record(row)

    # KI-07：Spec §12 持久队列 / lease / 取消 / 恢复。

    def claim_next_job(
        self,
        queue: str,
        *,
        lease_owner: str,
        lease_duration_seconds: int = 30,
        now: Optional[datetime] = None,
    ) -> Optional[JobRecord]:
        """Spec §12：单并发 FIFO (created_at, id)。

        本方法在一个事务中：
        1. SELECT 队列里最早一个 ``pending`` Job（按 created_at, id）。
        2. UPDATE 设置 ``status=running, lease_owner, lease_expires_at=now+duration,
           heartbeat_at=now, attempt_token=new_uuid``。

        单并发由调用方驱动（每个 worker 串行调用本方法 + execute + complete）。
        并发安全通过乐观 UPDATE 守卫实现：UPDATE 子句带 ``status='pending'`` 条件，
        被并发抢走的 Job 因 status 已变 running 而 rowcount=0，第二个 caller 看不到。
        SQLite 不真正实现 ``SELECT FOR UPDATE``，因此乐观守卫是 lease 正确性的关键。
        """

        moment = now or datetime.now(timezone.utc)
        expires_at = moment + timedelta(seconds=max(1, lease_duration_seconds))
        token = _new_attempt_token()
        with self._session_factory() as session:
            stmt = (
                select(KnowledgeJob)
                .where(
                    KnowledgeJob.queue == queue,
                    KnowledgeJob.status == "pending",
                    KnowledgeJob.canceled.is_(False),
                )
                .order_by(KnowledgeJob.created_at.asc(), KnowledgeJob.id.asc())
                .limit(1)
            )
            row = session.scalars(stmt).first()
            if row is None:
                return None
            optimistic = session.execute(
                text(
                    """
                    UPDATE knowledge_jobs
                    SET status = 'running',
                        lease_owner = :owner,
                        lease_expires_at = :expires,
                        heartbeat_at = :moment,
                        attempt_token = :token,
                        updated_at = :moment
                    WHERE id = :jid
                      AND status = 'pending'
                      AND canceled = 0
                    """
                ),
                {
                    "owner": lease_owner,
                    "expires": expires_at,
                    "moment": moment,
                    "token": token,
                    "jid": row.id,
                },
            )
            affected = int(getattr(optimistic, "rowcount", 0) or 0)
            if affected == 0:
                # 被并发抢走；递归找下一个候选，避免漏掉队列。
                session.commit()
                return self.claim_next_job(
                    queue,
                    lease_owner=lease_owner,
                    lease_duration_seconds=lease_duration_seconds,
                    now=now,
                )
            # 同步 ORM 对象状态，避免 expire_on_commit=False 保留旧 status='pending'。
            row.status = "running"
            row.lease_owner = lease_owner
            row.lease_expires_at = expires_at
            row.heartbeat_at = moment
            row.attempt_token = token
            row.updated_at = moment
            session.commit()
            session.refresh(row)
            # 与 heartbeat_job 保持 UTC aware 返回快照；SQLite 重新读取时可能丢失
            # tzinfo，但调用方常直接比较同一 Job 的前后 lease 时间。
            row.lease_expires_at = expires_at
            row.heartbeat_at = moment
            row.updated_at = moment
            return _to_job_record(row)

    def heartbeat_job(
        self,
        job_id: int,
        *,
        attempt_token: str,
        lease_duration_seconds: int = 30,
        now: Optional[datetime] = None,
    ) -> Optional[JobRecord]:
        """更新 heartbeat_at + lease_expires_at。

        Spec §12 "Job claim 使用 lease owner、expiry 和 heartbeat"。token 不匹配
        返回 ``None``——可能是同一 job 已被另一个 lease 重 claim，旧 worker 应停止。
        """
        moment = now or datetime.now(timezone.utc)
        expires_at = moment + timedelta(seconds=max(1, lease_duration_seconds))
        with self._session_factory() as session:
            # 取消和续租必须在同一 UPDATE 门禁中判断。先读再写会允许
            # mark_canceled() 在两次操作之间提交，随后 heartbeat 又把已取消 Job
            # 重新续租，导致恢复/迟到结果语义失真。
            result = session.execute(
                text(
                    """
                    UPDATE knowledge_jobs
                    SET heartbeat_at = :moment,
                        lease_expires_at = :expires,
                        updated_at = :moment
                    WHERE id = :jid
                      AND status = 'running'
                      AND canceled = 0
                      AND attempt_token = :token
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at > :moment
                    """
                ),
                {
                    "jid": job_id,
                    "token": attempt_token,
                    "moment": moment,
                    "expires": expires_at,
                },
            )
            if int(getattr(result, "rowcount", 0) or 0) != 1:
                return None
            session.commit()
            row = session.get(KnowledgeJob, job_id)
            if row is None:
                return None
            # SQLite 的 DateTime 返回值通常是 naive；沿用本方法输入的 UTC aware
            # 值构造返回快照，避免调用方比较 lease 时触发 aware/naive TypeError。
            row.heartbeat_at = moment
            row.lease_expires_at = expires_at
            row.updated_at = moment
            return _to_job_record(row)

    def complete_job(
        self,
        job_id: int,
        *,
        attempt_token: str,
        status: str,
        stage: Optional[str] = None,
        progress: Optional[int] = None,
        error_code: str = "",
        error_message: str = "",
        next_retry_at: Optional[datetime] = None,
        increment_retry: bool = False,
        now: Optional[datetime] = None,
    ) -> tuple[bool, Optional[JobRecord]]:
        """提交 Job 结果；验证 attempt_token；不匹配返回 ``(False, None)``。

        Spec §12 "迟到的旧 lease 结果因 owner/Attempt 不匹配而拒绝提交"。

        ``status`` 应为 ``succeeded`` / ``failed`` / ``canceled``。``increment_retry``
        为 True 时 ``retry_count`` 加 1（用于 Brief 重试计数）。
        """

        moment = now or datetime.now(timezone.utc)
        with self._session_factory() as session:
            row = session.get(KnowledgeJob, job_id)
            if row is None:
                return False, None
            if row.attempt_token != attempt_token:
                return False, None
            # 一个已经终结为同一状态的 Job 允许幂等回读。BriefWorker 的
            # commit/fail 已经在同一事务终结内部 Job，外层调用方可能再次提交；
            # 这不应被误报为 lease 拒绝。
            if row.status in ("succeeded", "failed", "canceled") and row.status == status:
                if row.canceled and status != "canceled":
                    return False, None
                return True, _to_job_record(row)
            if row.status != "running":
                # Spec §12 "已发出的模型调用即使无法中止，其返回也不能在取消后提交"。
                # 只允许 running→终态；pending Job 必须先 claim 才能提交。
                return False, None
            if row.canceled:
                # running Job 被取消后只允许安全点写入 canceled；迟到的
                # succeeded/failed 结果一律拒绝。
                if status != "canceled":
                    return False, None
                eligible_where = (
                    "status = 'running' AND canceled = 1"
                )
            else:
                # lease 到期后即使旧 token 仍相同也不能提交，避免恢复线程尚未
                # 重置 Job 时迟到结果写入正式状态。
                eligible_where = (
                    "status = 'running' AND canceled = 0 "
                    "AND lease_expires_at IS NOT NULL "
                    "AND lease_expires_at > :moment"
                )
            retry_expression = "retry_count + 1" if increment_retry else "retry_count"
            result = session.execute(
                text(
                    f"""
                    UPDATE knowledge_jobs
                    SET status = :status,
                        stage = COALESCE(:stage, stage),
                        progress = COALESCE(:progress, progress),
                        error_code = :error_code,
                        error_message = :error_message,
                        next_retry_at = COALESCE(:next_retry_at, next_retry_at),
                        retry_count = {retry_expression},
                        lease_expires_at = NULL,
                        updated_at = :moment
                    WHERE id = :jid
                      AND attempt_token = :token
                      AND {eligible_where}
                    """
                ),
                {
                    "jid": job_id,
                    "token": attempt_token,
                    "status": status,
                    "stage": stage,
                    "progress": progress,
                    "error_code": error_code,
                    "error_message": error_message,
                    "next_retry_at": next_retry_at,
                    "moment": moment,
                },
            )
            if int(getattr(result, "rowcount", 0) or 0) != 1:
                return False, None
            session.commit()
            session.refresh(row)
            return True, _to_job_record(row)

    def mark_canceled(self, job_id: int) -> Optional[JobRecord]:
        """Spec §12 取消规则：
        - pending Job 直接标记 canceled。
        - running Job 设置 ``canceled=True`` 并清空 lease_expires_at，本地任务在
          安全点检查并停止；完整状态由 worker 在安全点写入（``status=canceled``）。
          清空 lease_expires_at 防止启动恢复把已 cancel 的 running Job 误判为
          过期失败。

        幂等：重复 cancel 不会复活 Job。
        """
        with self._session_factory() as session:
            row = session.get(KnowledgeJob, job_id)
            if row is None:
                return None
            if row.status in ("succeeded", "failed", "canceled"):
                return _to_job_record(row)
            if row.kind == "delete":
                # 删除 Job 不可取消：取消会让 Source 永久停留在 deleting，既不能
                # 恢复为可用 Source，也不能保证原件和 FTS 被清理。
                return _to_job_record(row)
            row.canceled = True
            if row.status == "pending":
                row.status = "canceled"
                row.stage = "canceled"
                row.lease_expires_at = None
                # pending brief Job 直接终结，没有 worker 会处理它，必须在此同步
                # 恢复 Source brief_status，否则 Source 永久卡在 pending/processing，
                # 前端 rebuild 按钮禁用、_repair_missing_brief_jobs 也不会重新入队。
                if row.kind == "brief" and row.source_id is not None:
                    pending_source = session.get(KnowledgeSource, row.source_id)
                    if (
                        pending_source is not None
                        and pending_source.brief_status in ("pending", "processing")
                    ):
                        pending_source.brief_status = "not_started"
                        pending_source.updated_at = datetime.now(timezone.utc)
            else:
                # running：worker 在安全点检查 canceled 标记并完成清理。Spec §12
                # "running 本地任务在安全点停止；已发出的模型调用即使无法中止，
                # 其返回也不能在取消后提交"。
                # 清空 lease_expires_at → recover_stale_running_jobs 不会触碰它。
                row.stage = "canceling"
                row.lease_expires_at = None
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(row)
            return _to_job_record(row)

    def is_job_canceled(self, job_id: int) -> bool:
        """供 worker 在安全点查询取消标记。"""
        with self._session_factory() as session:
            row = session.get(KnowledgeJob, job_id)
            if row is None:
                return True
            return bool(row.canceled) or row.status == "canceled"

    def list_pending_jobs(self, queue: str) -> list[JobRecord]:
        """列出队列所有 ``pending`` Job，按 FIFO 排序；测试与调度器使用。"""
        with self._session_factory() as session:
            stmt = (
                select(KnowledgeJob)
                .where(
                    KnowledgeJob.queue == queue,
                    KnowledgeJob.status == "pending",
                    KnowledgeJob.canceled.is_(False),
                )
                .order_by(KnowledgeJob.created_at.asc(), KnowledgeJob.id.asc())
            )
            return [_to_job_record(row) for row in session.scalars(stmt)]

    def recover_stale_running_jobs(
        self,
        now: Optional[datetime] = None,
        *,
        requeue: bool = False,
    ) -> list[int]:
        """KI-07 启动恢复过期 lease。

        默认保留旧的诊断行为（标记 failed），供历史诊断调用方使用；应用运行时传入
        ``requeue=True``，将未提交的 Extraction/Brief Job 原子放回 pending，并终结
        遗留 processing Attempt，保证重启后可以继续消费而不是永久卡住。

        返回被恢复的 job_id 列表，便于日志与测试。
        """
        moment = now or datetime.now(timezone.utc)
        recovered: list[int] = []
        with self._session_factory() as session:
            stale_conditions = [KnowledgeJob.status == "running"]
            if not requeue:
                # 旧的诊断调用仍不主动恢复删除 Job；应用运行时的 requeue 路径会
                # 将过期删除任务重新交给 Extraction Worker。
                stale_conditions.append(KnowledgeJob.kind != "delete")
            if requeue:
                # cancel running Job 会清空 lease，进程若此时崩溃不能再依赖
                # lease 过期发现；运行时恢复应把它安全终结为 canceled。
                stale_conditions.append(
                    or_(
                        and_(
                            KnowledgeJob.lease_expires_at.is_not(None),
                            KnowledgeJob.lease_expires_at < moment,
                        ),
                        KnowledgeJob.canceled.is_(True),
                    )
                )
            else:
                stale_conditions.extend(
                    (
                        KnowledgeJob.lease_expires_at.is_not(None),
                        KnowledgeJob.lease_expires_at < moment,
                    )
                )
            stale = (
                select(KnowledgeJob)
                .where(*stale_conditions)
                .order_by(KnowledgeJob.id.asc())
            )

            def _finish_brief_attempt(
                job_row: KnowledgeJob, *, error_code: str, error_message: str
            ) -> None:
                """只收口该 Job 绑定的 Attempt，禁止按 Source 误伤新候选。"""
                if job_row.kind != "brief":
                    return
                attempt: Optional[KnowledgeBriefAttempt] = None
                if job_row.attempt_id is not None:
                    attempt = session.get(KnowledgeBriefAttempt, job_row.attempt_id)
                elif job_row.source_id is not None and job_row.snapshot_id is not None:
                    attempt = session.execute(
                        select(KnowledgeBriefAttempt)
                        .where(
                            KnowledgeBriefAttempt.source_id == job_row.source_id,
                            KnowledgeBriefAttempt.snapshot_id == job_row.snapshot_id,
                            KnowledgeBriefAttempt.status == "processing",
                        )
                        .order_by(
                            KnowledgeBriefAttempt.created_at.desc(),
                            KnowledgeBriefAttempt.id.desc(),
                        )
                        .limit(1)
                    ).scalars().first()
                if attempt is not None and attempt.status == "processing":
                    attempt.status = "failed"
                    attempt.error_code = error_code
                    attempt.error_message = error_message
                    attempt.updated_at = moment
                # 终态（canceled 或 lease requeue 超限 failed）时同步恢复 Source
                # brief_status：worker 若阻塞在 LLM 调用里到不了安全点，或 Job 反复
                # lease 过期超过重试上限，Source 会永久卡在 processing，前端 rebuild
                # 按钮永久禁用。未超限的 lease expired requeue 保留 processing——Job 回
                # pending 会被 tick_brief 重新消费。
                if (
                    error_code in ("job_canceled", "job_lease_requeue_exhausted")
                    and job_row.source_id is not None
                ):
                    terminal_source = session.get(
                        KnowledgeSource, job_row.source_id
                    )
                    if (
                        terminal_source is not None
                        and terminal_source.brief_status == "processing"
                    ):
                        terminal_source.brief_status = "not_started"
                        terminal_source.updated_at = moment

            for row in session.scalars(stale):
                if requeue and row.canceled:
                    row.status = "canceled"
                    row.stage = "canceled"
                    row.error_code = "job_canceled"
                    row.error_message = "用户取消后进程退出，恢复为 canceled"
                    _finish_brief_attempt(
                        row,
                        error_code="job_canceled",
                        error_message="Brief Job 在取消后进程退出",
                    )
                elif requeue:
                    row.retry_count = (row.retry_count or 0) + 1
                    if row.retry_count > BRIEF_LEASE_REQUEUE_MAX:
                        row.status = "failed"
                        row.stage = "lease_requeue_exhausted"
                        row.error_code = "job_lease_requeue_exhausted"
                        row.error_message = (
                            f"lease 过期已自动重新入队 {row.retry_count} 次仍未完成"
                        )
                        _finish_brief_attempt(
                            row,
                            error_code="job_lease_requeue_exhausted",
                            error_message=row.error_message,
                        )
                    else:
                        row.status = "pending"
                        row.stage = "recovered_pending"
                        row.error_code = ""
                        row.error_message = ""
                        _finish_brief_attempt(
                            row,
                            error_code="job_lease_expired",
                            error_message="Brief Job lease expired during restart recovery",
                        )
                else:
                    row.status = "failed"
                    row.stage = "expired_lease"
                    row.error_code = "job_lease_expired"
                    row.error_message = (
                        "lease expired before completion; runtime marked job failed"
                    )
                row.lease_expires_at = None
                row.lease_owner = ""
                row.heartbeat_at = None
                row.updated_at = moment
                recovered.append(row.id)
            if recovered:
                session.commit()
        return recovered

    # Evidence

    def list_evidence(
        self,
        source_id: int,
        snapshot_id: Optional[int] = None,
        *,
        after_ordinal: Optional[int] = None,
        limit: int = 50,
    ) -> EvidencePage:
        with self._session_factory() as session:
            effective_snapshot = snapshot_id
            if effective_snapshot is None:
                source = session.get(KnowledgeSource, source_id)
                if source is None or source.active_snapshot_id is None:
                    return EvidencePage()
                effective_snapshot = source.active_snapshot_id
            stmt = select(KnowledgeEvidence).where(
                KnowledgeEvidence.source_id == source_id,
                KnowledgeEvidence.snapshot_id == effective_snapshot,
            )
            if after_ordinal is not None:
                stmt = stmt.where(KnowledgeEvidence.ordinal > after_ordinal)
            stmt = stmt.order_by(KnowledgeEvidence.ordinal.asc()).limit(limit + 1)
            rows = list(session.scalars(stmt))
            next_cursor: Optional[int] = None
            if len(rows) > limit:
                rows = rows[:limit]
                next_cursor = rows[-1].ordinal
            return EvidencePage(
                items=[_to_evidence_record(row) for row in rows],
                next_cursor=next_cursor,
            )

    def get_evidence(self, evidence_id: str) -> Optional[EvidenceRecord]:
        with self._session_factory() as session:
            row = session.get(KnowledgeEvidence, evidence_id)
            if row is None:
                return None
            source = session.get(KnowledgeSource, row.source_id)
            if (
                source is None
                or source.deleted_at is not None
                or source.lifecycle == "deleting"
            ):
                return None
            return _to_evidence_record(row)

    def search_evidence(
        self,
        query: str,
        *,
        source_ids: Optional[Sequence[int]] = None,
        include_archived: bool = False,
        limit: int = 10,
        evaluation_label: str = "",
    ) -> list[EvidenceSearchHit]:
        """Spec §15 Evidence 检索入口。

        实现：
        - 启动时校验 FTS5 + trigram（``db._ensure_knowledge_fts``）。
        - ``parse_query`` 区分 ``fts`` / ``substring`` / ``empty`` 三种模式，避免整句
          作为强制精确短语。
        - FTS 模式使用分列权重；正文是 Evidence 的主要相关性信号，标题和章节
          只作为辅助，避免同一 Source 的标题命中挤掉真正的正文 Evidence。
        - 短查询 (< 3 字符) 走 LIKE + LIMIT 有界回退。
        - 错误显式抛 ``SearchError``，禁止静默吞掉变成空结果。
        - 每次搜索写一条 Retrieval Trace（含失败路径）。
        """
        parsed = parse_query(query)
        if parsed.mode == "empty":
            return []
        clamped_limit = max(1, min(50, limit))
        started = time.monotonic()
        try:
            hits = self._execute_search(
                parsed,
                source_ids=source_ids,
                include_archived=include_archived,
                limit=clamped_limit,
            )
        except SearchError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            self._safe_record_trace(
                query=query,
                source_ids=source_ids,
                include_archived=include_archived,
                hits=[],
                duration_ms=duration_ms,
                evaluation_label=evaluation_label,
                error_code=exc.code,
            )
            raise
        duration_ms = int((time.monotonic() - started) * 1000)
        self._safe_record_trace(
            query=query,
            source_ids=source_ids,
            include_archived=include_archived,
            hits=hits,
            duration_ms=duration_ms,
            evaluation_label=evaluation_label,
            error_code="",
        )
        return hits

    def _execute_search(
        self,
        parsed: ParsedQuery,
        *,
        source_ids: Optional[Sequence[int]],
        include_archived: bool,
        limit: int,
    ) -> list[EvidenceSearchHit]:
        with self._session_factory() as session:
            if parsed.mode == "fts":
                raw_rows = self._run_fts_match(
                    session,
                    parsed.match_expr,
                    source_ids=source_ids,
                    include_archived=include_archived,
                    limit=limit,
                )
            elif parsed.mode == "substring":
                raw_rows = self._run_substring_match(
                    session,
                    parsed.terms,
                    source_ids=source_ids,
                    include_archived=include_archived,
                    limit=limit,
                )
            else:
                return []

            terms_for_snippet = parsed.terms + (parsed.original,)
            hits: list[EvidenceSearchHit] = []
            for row in raw_rows:
                mapping = row._mapping if hasattr(row, "_mapping") else row
                evidence_id_value = str(mapping["evidence_id"])
                score_value = float(mapping["score"])
                ev_row = session.get(KnowledgeEvidence, evidence_id_value)
                if ev_row is None:
                    continue
                hits.append(
                    EvidenceSearchHit(
                        evidence_id=ev_row.id,
                        source_id=ev_row.source_id,
                        snapshot_id=ev_row.snapshot_id,
                        block_kind=ev_row.block_kind,
                        heading_path=_decode_heading_path(ev_row.heading_path_json),
                        char_start=ev_row.char_start,
                        char_end=ev_row.char_end,
                        line_start=ev_row.line_start,
                        line_end=ev_row.line_end,
                        canonical_excerpt=ev_row.canonical_excerpt,
                        snippet=_build_snippet_from_terms(
                            terms_for_snippet, ev_row.canonical_excerpt
                        ),
                        score=score_value,
                        previous_evidence_id=ev_row.previous_evidence_id,
                        next_evidence_id=ev_row.next_evidence_id,
                    )
                )
            return hits

    def _run_fts_match(
        self,
        session: Session,
        match_expr: str,
        *,
        source_ids: Optional[Sequence[int]],
        include_archived: bool,
        limit: int,
    ) -> list[Any]:
        # bm25 列权重按 FTS5 表定义顺序：evidence_id(0), source_id(1), source_title(2),
        # heading_path(3), content(4)。UNINDEXED 列权重无效，但参数位置必须保留。
        # Spec §15 "source title、heading path 和 content 使用分列权重"。
        params: dict[str, Any] = {"query": match_expr, "limit": limit}
        sql = (
            "SELECT fts.evidence_id AS evidence_id, "
            "bm25(knowledge_evidence_fts, 0.0, 0.0, 1.0, 2.0, 8.0) AS score "
            "FROM knowledge_evidence_fts fts "
            "JOIN knowledge_sources ks ON ks.id = fts.source_id "
            "WHERE knowledge_evidence_fts MATCH :query "
        )
        if source_ids:
            placeholders = ",".join(f":sid_{i}" for i in range(len(source_ids)))
            sql += f"AND fts.source_id IN ({placeholders}) "
            for i, sid in enumerate(source_ids):
                params[f"sid_{i}"] = sid
        # ``include_archived`` 只放开 archived，不得重新暴露永久删除的 deleting
        # 过渡态；该状态的原件可能已经移入 quarantine，Evidence 也正在清理。
        sql += "AND ks.lifecycle != 'deleting' AND ks.deleted_at IS NULL "
        if not include_archived:
            sql += "AND ks.lifecycle = 'active' "
        # bm25 返回负数（越小越相关），ASC 即最相关在前
        sql += "ORDER BY score ASC LIMIT :limit"
        try:
            return list(session.execute(text(sql), params).fetchall())
        except OperationalError as exc:
            message = str(exc).lower()
            if (
                "fts5" in message
                or "syntax" in message
                or "match" in message
                or "tokenizer" in message
                or "no such" in message
            ):
                raise SearchError(
                    "fts_query_invalid",
                    "搜索表达式无法解析，请简化关键词后重试",
                ) from exc
            raise

    def _run_substring_match(
        self,
        session: Session,
        terms: tuple[str, ...],
        *,
        source_ids: Optional[Sequence[int]],
        include_archived: bool,
        limit: int,
    ) -> list[Any]:
        # Spec §15 "少于 3 字符查询使用有上限的精确/子串回退，避免全库无界扫描"。
        # 通过 source_title / content 双列 LIKE，并强制 LIMIT；不引入无界 LIKE 子查询。
        if not terms:
            return []
        where_parts: list[str] = []
        params: dict[str, Any] = {"limit": limit}
        for i, term in enumerate(terms):
            key = f"term_{i}"
            where_parts.append(
                "(fts.source_title LIKE :{k} ESCAPE '\\' OR fts.content LIKE :{k} ESCAPE '\\')".format(
                    k=key
                )
            )
            # Spec §15 "查询注入"：使用参数化绑定，term 内的 %/_ 需要转义避免通配符注入。
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params[key] = f"%{escaped}%"
        sql = (
            "SELECT fts.evidence_id AS evidence_id, "
            "-1.0 AS score "
            "FROM knowledge_evidence_fts fts "
            "JOIN knowledge_sources ks ON ks.id = fts.source_id "
            "WHERE (" + " OR ".join(where_parts) + ") "
        )
        if source_ids:
            placeholders = ",".join(f":sid_{i}" for i in range(len(source_ids)))
            sql += f"AND fts.source_id IN ({placeholders}) "
            for i, sid in enumerate(source_ids):
                params[f"sid_{i}"] = sid
        sql += "AND ks.lifecycle != 'deleting' AND ks.deleted_at IS NULL "
        if not include_archived:
            sql += "AND ks.lifecycle = 'active' "
        sql += "ORDER BY fts.rowid ASC LIMIT :limit"
        try:
            return list(session.execute(text(sql), params).fetchall())
        except OperationalError as exc:
            raise SearchError(
                "fts_query_invalid",
                "短查询执行失败，请稍后重试",
            ) from exc

    def _safe_record_trace(
        self,
        *,
        query: str,
        source_ids: Optional[Sequence[int]],
        include_archived: bool,
        hits: list[EvidenceSearchHit],
        duration_ms: int,
        evaluation_label: str,
        error_code: str,
    ) -> None:
        """Spec §14.10 / §18：Trace 只保存 ID/score/时长,不写 Evidence 原文。

        Spec §15 "Retrieval Trace 不参与 Knowledge 召回,也不写普通应用日志或外部 Trace"：
        trace 写入失败时不能阻塞 search 返回;warning 不携带 query/原文。
        """
        filters_payload: dict[str, Any] = {
            "source_ids": list(source_ids) if source_ids else [],
            "include_archived": bool(include_archived),
        }
        hits_payload = [
            {
                "evidence_id": hit.evidence_id,
                "source_id": hit.source_id,
                "score": float(hit.score),
            }
            for hit in hits
        ]
        try:
            with self._session_factory() as session:
                session.add(
                    KnowledgeRetrievalTrace(
                        query=query,
                        filters_json=json.dumps(filters_payload, ensure_ascii=False),
                        hits_json=json.dumps(hits_payload, ensure_ascii=False),
                        duration_ms=duration_ms,
                        evaluation_label=evaluation_label,
                        error_code=error_code,
                    )
                )
                session.commit()
        except (OperationalError, SQLAlchemyError):
            # Spec §15 "Retrieval Trace 不参与 Knowledge 召回,也不写普通应用日志或外部 Trace"：
            # 仅捕获 SQLAlchemy 错误族（连接 / 约束 / 死锁），其他异常（代码 bug）应上抛暴露。
            # warning 仅含 duration_ms，不携带 query / 原文，避免评估数据泄漏到日志。
            _LOGGER.warning(
                "knowledge_retrieval_trace_write_failed duration_ms=%d",
                duration_ms,
            )

    def list_retrieval_traces(
        self, *, limit: int = 100
    ) -> list[RetrievalTraceRecord]:
        """KI-11 评估工具使用,普通用户路径不暴露。"""
        clamped = max(1, min(500, limit))
        with self._session_factory() as session:
            stmt = (
                select(KnowledgeRetrievalTrace)
                .order_by(
                    KnowledgeRetrievalTrace.created_at.desc(),
                    KnowledgeRetrievalTrace.id.desc(),
                )
                .limit(clamped)
            )
            return [_to_retrieval_trace_record(row) for row in session.scalars(stmt)]

    # KI-09：Brief Attempt / Source Brief 持久化。

    def create_brief_attempt(
        self, data: BriefAttemptCreateInput
    ) -> tuple[BriefAttemptRecord, int, str]:
        """Spec §11.1 / §10.3：创建 Brief Attempt，固定 Provider/Schema/Snapshot。

        返回 ``(attempt_record, job_id, attempt_token)``。``attempt_token`` 是 lease
        凭证，调用方必须在 ``commit_*`` 时原样传回；持久化或日志中不得保留。
        """
        moment = datetime.now(timezone.utc)
        attempt_token = _new_attempt_token()
        with self._session_factory() as session:
            with session.begin():
                source_row = session.get(KnowledgeSource, data.source_id)
                if source_row is None or source_row.deleted_at is not None:
                    raise KnowledgeBriefAttemptError(
                        "source_integrity_mismatch",
                        "Source 不存在或已被删除",
                    )
                if source_row.lifecycle == "deleting":
                    raise KnowledgeBriefAttemptError(
                        "source_integrity_mismatch",
                        "Source 处于 deleting 状态",
                    )
                if source_row.active_snapshot_id != data.snapshot_id:
                    raise KnowledgeBriefAttemptError(
                        "brief_snapshot_stale",
                        "Brief Attempt 必须绑定 Source 当前 active Snapshot",
                    )
                _fail_stale_brief_jobs_for_snapshot(
                    session,
                    source_id=data.source_id,
                    snapshot_id=data.snapshot_id,
                    moment=moment,
                )
                existing_active = session.execute(
                    select(KnowledgeBriefAttempt).where(
                        KnowledgeBriefAttempt.source_id == data.source_id,
                        KnowledgeBriefAttempt.status.in_(("pending", "processing")),
                    )
                ).scalars().first()
                if existing_active is not None:
                    raise KnowledgeBriefAttemptError(
                        "brief_attempt_conflict",
                        "Source 已有进行中 Brief Attempt",
                    )
                # 首次 ingest / rebuild 会先排入一个 pending Brief Job，Worker claim
                # 后再创建 Attempt。复用该 Job，避免同一 Source 出现两个活动 Job；
                # 直接调用 Repository 时也兼容已有 pending Job。
                existing_job = session.execute(
                    select(KnowledgeJob)
                    .where(
                        KnowledgeJob.source_id == data.source_id,
                        KnowledgeJob.kind == "brief",
                        KnowledgeJob.queue == "brief",
                        KnowledgeJob.status.in_(("pending", "running")),
                        KnowledgeJob.canceled.is_(False),
                        or_(
                            KnowledgeJob.snapshot_id == data.snapshot_id,
                            KnowledgeJob.snapshot_id.is_(None),
                        ),
                    )
                    .order_by(KnowledgeJob.created_at.desc(), KnowledgeJob.id.desc())
                    .limit(1)
                ).scalars().first()
                if (
                    existing_job is not None
                    and existing_job.snapshot_id is not None
                    and existing_job.snapshot_id != data.snapshot_id
                ):
                    raise KnowledgeBriefAttemptError(
                        "source_integrity_mismatch",
                        "Brief Job 与当前 Snapshot 不一致",
                    )

                attempt_row = KnowledgeBriefAttempt(
                    source_id=data.source_id,
                    snapshot_id=data.snapshot_id,
                    status="processing",
                    provider_id=data.provider_id,
                    provider_model=data.provider_model,
                    provider_base_url=data.provider_base_url,
                    context_window=data.context_window,
                    max_output_tokens=data.max_output_tokens,
                    prompt_version=data.prompt_version,
                    schema_version=data.schema_version,
                    language=data.language,
                    candidate_payload_json="",
                    validation_report_json="{}",
                    error_code="",
                    error_message="",
                    fallback_provider_id=data.fallback_provider_id,
                    fallback_provider_model=data.fallback_provider_model,
                )
                session.add(attempt_row)
                try:
                    session.flush()
                except IntegrityError as exc:
                    if "uq_knowledge_active_attempt_source" in str(exc):
                        raise KnowledgeBriefAttemptError(
                            "brief_attempt_conflict",
                            "Source 已有进行中 Brief Attempt",
                        ) from exc
                    raise
                if existing_job is None:
                    job_row = KnowledgeJob(
                        kind="brief",
                        queue="brief",
                        source_id=data.source_id,
                        attempt_id=attempt_row.id,
                        snapshot_id=data.snapshot_id,
                        stage="brief_processing",
                        status="running",
                    )
                    job_row.lease_owner = "brief-attempt"
                    job_row.lease_expires_at = moment + timedelta(seconds=600)
                    job_row.heartbeat_at = moment
                    job_row.attempt_token = attempt_token
                    session.add(job_row)
                else:
                    job_row = existing_job
                    if job_row.snapshot_id is None:
                        job_row.snapshot_id = data.snapshot_id
                    if job_row.status == "pending":
                        job_row.status = "running"
                        job_row.stage = "brief_processing"
                        job_row.lease_owner = "brief-attempt"
                        job_row.lease_expires_at = moment + timedelta(seconds=600)
                        job_row.heartbeat_at = moment
                        job_row.attempt_token = attempt_token
                    elif not job_row.attempt_token:
                        # 兼容旧库中已经 running 但没有 attempt_token 的 Job。
                        job_row.attempt_token = attempt_token
                    # Attempt 创建后才写入关联，确保不会再按 Source 猜测 Job。
                    job_row.attempt_id = attempt_row.id
                try:
                    session.flush()
                except IntegrityError as exc:
                    if "uq_knowledge_active_job_source_kind" in str(exc):
                        raise KnowledgeBriefAttemptError(
                            "brief_attempt_conflict",
                            "Source 已有进行中 Brief Job",
                        ) from exc
                    raise
                source_row.brief_status = "processing"
                source_row.brief_block_reason = ""
                source_row.brief_error_code = ""
                source_row.brief_error_message = ""
                attempt_id_value = attempt_row.id
                job_id_value = job_row.id
                job_attempt_token = job_row.attempt_token
            refreshed = session.get(KnowledgeBriefAttempt, attempt_id_value)
            assert refreshed is not None
            return (
                _to_brief_attempt_record(refreshed),
                job_id_value,
                job_attempt_token,
            )

    def get_brief_attempt(self, attempt_id: int) -> Optional[BriefAttemptRecord]:
        with self._session_factory() as session:
            row = session.get(KnowledgeBriefAttempt, attempt_id)
            return _to_brief_attempt_record(row) if row is not None else None

    def find_active_brief_attempt(
        self, source_id: int
    ) -> Optional[BriefAttemptRecord]:
        """Spec §10.4：返回当前 Source 未完成 Attempt。

        重建期间旧 Brief 继续可见，新候选 Attempt 独立写入 validation_report。
        本方法返回最近一个 pending/processing Attempt，避免重复创建。
        """
        with self._session_factory() as session:
            stmt = (
                select(KnowledgeBriefAttempt)
                .where(
                    KnowledgeBriefAttempt.source_id == source_id,
                    KnowledgeBriefAttempt.status.in_(("pending", "processing")),
                )
                .order_by(
                    KnowledgeBriefAttempt.created_at.desc(),
                    KnowledgeBriefAttempt.id.desc(),
                )
                .limit(1)
            )
            row = session.scalars(stmt).first()
            return _to_brief_attempt_record(row) if row is not None else None

    def find_latest_brief_attempt(
        self, source_id: int
    ) -> Optional[BriefAttemptRecord]:
        """Spec §10.4：返回最近一次 Brief Attempt（无论状态）。

        供 API 展示最近错误与诊断信息，与 active_brief_id 区分。
        """
        with self._session_factory() as session:
            stmt = (
                select(KnowledgeBriefAttempt)
                .where(KnowledgeBriefAttempt.source_id == source_id)
                .order_by(
                    KnowledgeBriefAttempt.created_at.desc(),
                    KnowledgeBriefAttempt.id.desc(),
                )
                .limit(1)
            )
            row = session.scalars(stmt).first()
            return _to_brief_attempt_record(row) if row is not None else None

    def find_brief_job_for_attempt(
        self, attempt_id: int
    ) -> Optional[JobRecord]:
        """Spec §12：Brief Attempt 与 brief Job 关联查询。

        KI-09 创建 Attempt 时同时创建 Job；必须按持久化的 ``attempt_id`` 精确查询，
        不能按 Source + ``running`` 猜测（同一 Source 的旧 Attempt 可能与新 Job
        并存）。终态 Job 也保留关联，供失败/恢复诊断回读。
        """
        with self._session_factory() as session:
            stmt = (
                select(KnowledgeJob)
                .where(
                    KnowledgeJob.attempt_id == attempt_id,
                    KnowledgeJob.kind == "brief",
                )
                .order_by(KnowledgeJob.created_at.desc(), KnowledgeJob.id.desc())
                .limit(1)
            )
            row = session.scalars(stmt).first()
            return _to_job_record(row) if row is not None else None

    def update_brief_attempt_progress(
        self,
        attempt_id: int,
        *,
        candidate_payload_json: str = "",
        validation_report_json: str = "",
        error_code: str = "",
        error_message: str = "",
        repair_count: Optional[int] = None,
        token_input_count: Optional[int] = None,
        token_output_count: Optional[int] = None,
        latency_ms: Optional[int] = None,
    ) -> Optional[BriefAttemptRecord]:
        """Spec §10.3：更新 Attempt 候选 payload / 校验报告 / 错误。"""
        with self._session_factory() as session:
            row = session.get(KnowledgeBriefAttempt, attempt_id)
            if row is None:
                return None
            if candidate_payload_json:
                row.candidate_payload_json = candidate_payload_json
            if validation_report_json:
                row.validation_report_json = validation_report_json
            if error_code:
                row.error_code = error_code
            if error_message:
                row.error_message = error_message
            if repair_count is not None:
                row.repair_count = repair_count
            if token_input_count is not None:
                row.token_input_count = token_input_count
            if token_output_count is not None:
                row.token_output_count = token_output_count
            if latency_ms is not None:
                row.latency_ms = latency_ms
            session.commit()
            session.refresh(row)
            return _to_brief_attempt_record(row)

    def fail_brief_attempt(
        self,
        attempt_id: int,
        *,
        job_id: int,
        attempt_token: str,
        error_code: str,
        error_message: str,
        validation_report_json: str = "",
        candidate_payload_json: str = "",
        token_input_count: int = 0,
        token_output_count: int = 0,
        latency_ms: int = 0,
        repair_count: Optional[int] = None,
        actual_provider_id: str = "",
        actual_provider_model: str = "",
        provider_retry_count: Optional[int] = None,
    ) -> tuple[bool, Optional[BriefAttemptRecord], Optional[JobRecord]]:
        """Spec §10.3 / §10.4：Brief Attempt 失败，Source brief_status=failed。

        事务内：
        1. 原子验证 Brief Job lease 未过期且未取消；迟到结果直接拒绝。
        2. Brief Attempt 标记 failed + 错误码 + validation 报告，Job 也终结为 failed。
        3. Source 已有有效当前 Brief 时保持 ``ready``（旧 Brief 继续可见），仅记录
           最近 Attempt 错误；首次失败（无旧 Brief）才 ``failed``。
        4. KI-10：actual_provider_* / provider_retry_count 持久化；next_retry_at 保留
           ``bump_brief_attempt_retry`` 写入的最近一次预计重试时间，便于诊断与
           Spec §11.4 "重启后保留"，不在失败时清空。
        """
        moment = datetime.now(timezone.utc)
        with self._session_factory() as session:
            with session.begin():
                if not self._lock_active_job_for_write(
                    session,
                    job_id=job_id,
                    attempt_token=attempt_token,
                    moment=moment,
                ):
                    return False, None, None
                attempt_row = session.get(KnowledgeBriefAttempt, attempt_id)
                if attempt_row is None:
                    return False, None, None
                job_row = session.get(KnowledgeJob, job_id)
                if job_row is None:
                    return False, None, None
                if job_row.attempt_id not in (None, attempt_id):
                    # 一个 Job 只能提交其创建时绑定的 Attempt；拒绝迟到或串线结果。
                    return False, None, None
                if job_row.attempt_id is None:
                    # 旧库迁移期间允许首次安全提交补齐关联；新建 Job 始终在
                    # create_brief_attempt 事务中写入该字段。
                    job_row.attempt_id = attempt_id
                source_row = session.get(KnowledgeSource, attempt_row.source_id)
                attempt_row.status = "failed"
                attempt_row.error_code = error_code
                attempt_row.error_message = error_message[:500]
                if validation_report_json:
                    attempt_row.validation_report_json = validation_report_json
                if candidate_payload_json:
                    attempt_row.candidate_payload_json = candidate_payload_json
                if repair_count is not None:
                    attempt_row.repair_count = repair_count
                attempt_row.token_input_count = token_input_count
                attempt_row.token_output_count = token_output_count
                attempt_row.latency_ms = latency_ms
                if actual_provider_id:
                    attempt_row.actual_provider_id = actual_provider_id
                if actual_provider_model:
                    attempt_row.actual_provider_model = actual_provider_model
                if provider_retry_count is not None:
                    attempt_row.provider_retry_count = provider_retry_count
                attempt_row.updated_at = moment
                job_row.status = "failed"
                job_row.stage = "brief_attempt_failed"
                job_row.progress = 100
                job_row.error_code = error_code
                job_row.error_message = error_message[:500]
                job_row.lease_expires_at = None
                job_row.updated_at = moment
                source_matches_snapshot = (
                    source_row is not None
                    and source_row.lifecycle != "deleting"
                    and source_row.active_snapshot_id == attempt_row.snapshot_id
                )
                if source_matches_snapshot and source_row is not None:
                    # Spec §10.4：新候选失败时保留旧 Brief。如果 Source 已有有效当前
                    # Brief，保持 ``ready`` 让旧 Brief 继续可见，仅记录最近 Attempt
                    # 错误；首次失败（无旧 Brief）才进入 ``failed``。
                    existing_brief = session.execute(
                        select(KnowledgeSourceBrief).where(
                            KnowledgeSourceBrief.source_id == attempt_row.source_id
                        )
                    ).scalars().first()
                    if existing_brief is not None:
                        source_row.brief_status = "ready"
                    else:
                        source_row.brief_status = "failed"
                    source_row.brief_error_code = error_code
                    source_row.brief_error_message = error_message[:500]
                    source_row.brief_block_reason = ""
                attempt_id_value = attempt_row.id
                job_id_value = job_row.id
            refreshed_attempt = session.get(KnowledgeBriefAttempt, attempt_id_value)
            refreshed_job = session.get(KnowledgeJob, job_id_value)
            return (
                True,
                _to_brief_attempt_record(refreshed_attempt)
                if refreshed_attempt is not None
                else None,
                _to_job_record(refreshed_job) if refreshed_job is not None else None,
            )

    def commit_brief_attempt_success(
        self,
        attempt_id: int,
        *,
        job_id: int,
        attempt_token: str,
        payload_json: str,
        validation_report_json: str,
        token_input_count: int = 0,
        token_output_count: int = 0,
        latency_ms: int = 0,
        actual_provider_id: str = "",
        actual_provider_model: str = "",
        provider_retry_count: int = 0,
        repair_count: Optional[int] = None,
    ) -> tuple[bool, Optional[SourceBriefRecord], Optional[JobRecord]]:
        """Spec §10.3 / §10.4：成功 Brief 与 winning Attempt 在同一事务中提交。

        事务步骤：
        1. 原子验证 brief Job lease 未过期且未取消；迟到 lease 拒绝。
        2. upsert ``knowledge_source_briefs`` 单行（Source UNIQUE）：
           - 替换 payload / winning_attempt_id / schema_version / language / snapshot_id。
        3. Brief Attempt 标记 succeeded + validation 报告。
        4. Brief Job complete_job(succeeded)。
        5. Source ``brief_status=ready``、``active_brief_id=brief.id``、清空 error 字段。
        """
        moment = datetime.now(timezone.utc)
        with self._session_factory() as session:
            with session.begin():
                if not self._lock_active_job_for_write(
                    session,
                    job_id=job_id,
                    attempt_token=attempt_token,
                    moment=moment,
                ):
                    return False, None, None
                attempt_row = session.get(KnowledgeBriefAttempt, attempt_id)
                if attempt_row is None:
                    return False, None, None
                job_row = session.get(KnowledgeJob, job_id)
                if job_row is None:
                    return False, None, None
                if job_row.attempt_id not in (None, attempt_id):
                    # 防止使用同一 Source 的另一个 Brief Job 覆盖当前候选。
                    return False, None, None
                if job_row.attempt_id is None:
                    job_row.attempt_id = attempt_id
                source_row = session.get(KnowledgeSource, attempt_row.source_id)
                if (
                    source_row is None
                    or source_row.lifecycle == "deleting"
                    or source_row.active_snapshot_id != attempt_row.snapshot_id
                ):
                    # 旧 Snapshot 的迟到模型结果不得成为当前 Brief；同时终结
                    # Attempt/Job，避免 processing Attempt 永久阻塞后续重建。
                    attempt_row.status = "failed"
                    attempt_row.error_code = "brief_snapshot_stale"
                    attempt_row.error_message = "Source active Snapshot 已更新"
                    attempt_row.updated_at = moment
                    job_row.status = "failed"
                    job_row.stage = "brief_snapshot_stale"
                    job_row.progress = 100
                    job_row.error_code = "brief_snapshot_stale"
                    job_row.error_message = "Source active Snapshot 已更新"
                    job_row.lease_expires_at = None
                    job_row.updated_at = moment
                    return False, None, None
                source_id_value = attempt_row.source_id
                snapshot_id_value = attempt_row.snapshot_id
                brief_row = session.execute(
                    select(KnowledgeSourceBrief).where(
                        KnowledgeSourceBrief.source_id == source_id_value
                    )
                ).scalars().first()
                if brief_row is None:
                    brief_row = KnowledgeSourceBrief(
                        source_id=source_id_value,
                        snapshot_id=snapshot_id_value,
                        winning_attempt_id=attempt_id,
                        schema_version=attempt_row.schema_version,
                        language=attempt_row.language,
                        payload_json=payload_json,
                        outdated=False,
                    )
                    session.add(brief_row)
                else:
                    brief_row.snapshot_id = snapshot_id_value
                    brief_row.winning_attempt_id = attempt_id
                    brief_row.schema_version = attempt_row.schema_version
                    brief_row.language = attempt_row.language
                    brief_row.payload_json = payload_json
                    brief_row.outdated = False
                    brief_row.updated_at = moment
                session.flush()
                attempt_row.status = "succeeded"
                attempt_row.validation_report_json = validation_report_json
                attempt_row.error_code = ""
                attempt_row.error_message = ""
                attempt_row.token_input_count = token_input_count
                attempt_row.token_output_count = token_output_count
                attempt_row.latency_ms = latency_ms
                # KI-10 / Spec §11.3：记录实际成功 Provider（可能为 fallback）与 Provider
                # 层重试总次数，供诊断与评估展示。
                attempt_row.actual_provider_id = actual_provider_id
                attempt_row.actual_provider_model = actual_provider_model
                attempt_row.provider_retry_count = provider_retry_count
                # KBR-06：repair 成功时持久化 repair_count（与 winning Attempt 同事务）。
                if repair_count is not None:
                    attempt_row.repair_count = repair_count
                attempt_row.next_retry_at = None
                attempt_row.updated_at = moment
                job_row.status = "succeeded"
                job_row.stage = "brief_ready"
                job_row.progress = 100
                job_row.lease_expires_at = None
                job_row.updated_at = moment
                if source_row is not None and source_row.lifecycle != "deleting":
                    source_row.brief_status = "ready"
                    source_row.brief_error_code = ""
                    source_row.brief_error_message = ""
                    source_row.brief_block_reason = ""
                    source_row.active_brief_id = brief_row.id
                    source_row.updated_at = moment
                brief_id_value = brief_row.id
                job_id_value = job_row.id
            refreshed_brief = session.get(KnowledgeSourceBrief, brief_id_value)
            refreshed_job = session.get(KnowledgeJob, job_id_value)
            return (
                True,
                _to_source_brief_record(refreshed_brief)
                if refreshed_brief is not None
                else None,
                _to_job_record(refreshed_job) if refreshed_job is not None else None,
            )

    def get_source_brief(self, source_id: int) -> Optional[SourceBriefRecord]:
        """Spec §10.4：读取当前 Brief（每 Source 至多一行）。"""
        with self._session_factory() as session:
            stmt = select(KnowledgeSourceBrief).where(
                KnowledgeSourceBrief.source_id == source_id
            )
            row = session.scalars(stmt).first()
            return _to_source_brief_record(row) if row is not None else None

    def list_brief_attempts(
        self, source_id: int, *, limit: int = 20
    ) -> list[BriefAttemptRecord]:
        """Spec §10.4：列出 Source 最近 Attempt 历史。

        API 层默认不暴露 candidate_payload / validation_report；KI-11 评估工具可读取。
        """
        clamped = max(1, min(50, limit))
        with self._session_factory() as session:
            stmt = (
                select(KnowledgeBriefAttempt)
                .where(KnowledgeBriefAttempt.source_id == source_id)
                .order_by(
                    KnowledgeBriefAttempt.created_at.desc(),
                    KnowledgeBriefAttempt.id.desc(),
                )
                .limit(clamped)
            )
            return [_to_brief_attempt_record(row) for row in session.scalars(stmt)]

    def append_brief_attempt_step(
        self,
        attempt_id: int,
        *,
        phase: str,
        status: str = "completed",
        iteration: int = 0,
        block_path: str = "",
        provider_id: str = "",
        provider_model: str = "",
        prompt_version: str = "",
        schema_version: int = 0,
        evidence_ids: Optional[Sequence[str]] = None,
        output: Any = None,
        token_input_count: int = 0,
        token_output_count: int = 0,
        latency_ms: int = 0,
        retry_count: int = 0,
        error_code: str = "",
        error_message: str = "",
    ) -> Optional[BriefAttemptStepRecord]:
        """追加一条 Attempt 步骤；不更新或覆盖已有步骤。

        ``output`` 仅接受 JSON 可序列化对象，序列化后截断到 12KB；调用方应把原始
        模型响应标为不可信摘要，不应传入 Evidence 正文或 Prompt。
        """
        safe_phase = str(phase or "unknown")[:64]
        safe_status = str(status or "completed")[:32]
        safe_output: Any = output if output is not None else {}
        try:
            output_json = json.dumps(safe_output, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            output_json = json.dumps({"unserializable": True}, ensure_ascii=False)
        if len(output_json) > 12_000:
            output_json = json.dumps(
                {"truncated": True, "preview": output_json[:11_500]},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        ids = [str(value)[:128] for value in list(evidence_ids or ())[:200]]
        moment = datetime.now(timezone.utc)
        with self._session_factory() as session:
            with session.begin():
                if session.get(KnowledgeBriefAttempt, attempt_id) is None:
                    return None
                max_sequence = session.scalar(
                    select(func.max(KnowledgeBriefAttemptStep.sequence)).where(
                        KnowledgeBriefAttemptStep.attempt_id == attempt_id
                    )
                )
                row = KnowledgeBriefAttemptStep(
                    attempt_id=attempt_id,
                    sequence=int(max_sequence or 0) + 1,
                    iteration=max(0, int(iteration)),
                    phase=safe_phase,
                    status=safe_status,
                    block_path=str(block_path or "")[:160],
                    provider_id=str(provider_id or "")[:128],
                    provider_model=str(provider_model or "")[:256],
                    prompt_version=str(prompt_version or "")[:128],
                    schema_version=max(0, int(schema_version)),
                    evidence_ids_json=json.dumps(ids, ensure_ascii=False),
                    output_json=output_json,
                    token_input_count=max(0, int(token_input_count)),
                    token_output_count=max(0, int(token_output_count)),
                    latency_ms=max(0, int(latency_ms)),
                    retry_count=max(0, int(retry_count)),
                    error_code=str(error_code or "")[:128],
                    error_message=str(error_message or "")[:500],
                    created_at=moment,
                )
                session.add(row)
                session.flush()
                row_id = row.id
            refreshed = session.get(KnowledgeBriefAttemptStep, row_id)
            return _to_brief_attempt_step_record(refreshed) if refreshed is not None else None

    def list_brief_attempt_steps(
        self, attempt_id: int, *, limit: int = 200
    ) -> list[BriefAttemptStepRecord]:
        """按追加顺序读取 Attempt 过程记录。"""
        clamped = max(1, min(500, int(limit)))
        with self._session_factory() as session:
            stmt = (
                select(KnowledgeBriefAttemptStep)
                .where(KnowledgeBriefAttemptStep.attempt_id == attempt_id)
                .order_by(KnowledgeBriefAttemptStep.sequence.asc(), KnowledgeBriefAttemptStep.id.asc())
                .limit(clamped)
            )
            return [_to_brief_attempt_step_record(row) for row in session.scalars(stmt)]

    def count_brief_attempt_steps(self, attempt_id: int) -> int:
        """返回 Attempt 的步骤总数，供 API 标注分页截断状态。"""
        with self._session_factory() as session:
            count = session.scalar(
                select(func.count(KnowledgeBriefAttemptStep.id)).where(
                    KnowledgeBriefAttemptStep.attempt_id == attempt_id
                )
            )
            return int(count or 0)

    def bump_brief_attempt_retry(
        self,
        attempt_id: int,
        *,
        provider_retry_count: int,
        next_retry_at: Optional[datetime],
        error_code: str,
        error_message: str = "",
    ) -> Optional[BriefAttemptRecord]:
        """Spec §11.4：持久化 Provider 层重试进度。

        worker 在两次 Provider 调用之间调用本方法写入重试计数与预计下次重试时间，
        保证进程崩溃或重启后 Attempt 仍保留进度，不从零开始。``status`` 不变
        （仍 ``processing``），由 ``fail_brief_attempt`` / ``commit_brief_attempt_success``
        在终态写入。
        """
        moment = datetime.now(timezone.utc)
        with self._session_factory() as session:
            row = session.get(KnowledgeBriefAttempt, attempt_id)
            if row is None:
                return None
            if row.status not in ("pending", "processing"):
                return _to_brief_attempt_record(row)
            row.provider_retry_count = provider_retry_count
            row.next_retry_at = next_retry_at
            row.error_code = error_code
            if error_message:
                row.error_message = error_message[:500]
            row.updated_at = moment
            session.commit()
            session.refresh(row)
            return _to_brief_attempt_record(row)

    def mark_brief_outdated_if_stale(
        self,
        source_id: int,
        *,
        provider_id: str,
        provider_model: str,
        prompt_version: str,
        schema_version: int,
        snapshot_id: int,
    ) -> Optional[SourceBriefRecord]:
        """Spec §10.4：检测当前 Brief 是否相对活跃配置过期。

        比较 winning Attempt 的 Provider/Model/Prompt/Schema 与当前 active provider，
        以及 Brief.snapshot_id 与 Source.active_snapshot_id。任一不一致则
        ``outdated=True``；完全一致则清除 ``outdated``（rebuild 成功后新 Brief 自然匹配）。

        Spec §10.4 "不自动批量调用模型"：本方法只更新标记，不创建 rebuild Job。
        """
        moment = datetime.now(timezone.utc)
        with self._session_factory() as session:
            brief_row = session.execute(
                select(KnowledgeSourceBrief).where(
                    KnowledgeSourceBrief.source_id == source_id
                )
            ).scalars().first()
            if brief_row is None:
                return None
            attempt_row = session.get(KnowledgeBriefAttempt, brief_row.winning_attempt_id)
            stale = (
                attempt_row is None
                or attempt_row.provider_id != provider_id
                or attempt_row.provider_model != provider_model
                or attempt_row.prompt_version != prompt_version
                or attempt_row.schema_version != schema_version
                or brief_row.snapshot_id != snapshot_id
            )
            previous = bool(brief_row.outdated)
            source_row = session.get(KnowledgeSource, source_id)
            target_status = "outdated" if stale else "ready"
            # pending/processing 表示 Brief Job 已在途；读取路径的 outdated 检测
            # 不得覆盖，否则 rebuild 入队后的「排队中/生成中」会被轮询 GET 打回 ready。
            source_needs_update = (
                source_row is not None
                and source_row.lifecycle != "deleting"
                and source_row.brief_status in ("ready", "outdated")
                and source_row.brief_status != target_status
            )
            if stale != previous or source_needs_update:
                brief_row.outdated = stale
                brief_row.updated_at = moment
                if source_needs_update and source_row is not None:
                    source_row.brief_status = target_status
                    source_row.updated_at = moment
                session.commit()
                session.refresh(brief_row)
            return _to_source_brief_record(brief_row)


def _decode_heading_path(heading_path_json: Optional[str]) -> list[str]:
    if not heading_path_json:
        return []
    try:
        value = json.loads(heading_path_json)
    except json.JSONDecodeError:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _build_snippet_from_terms(
    terms: Sequence[str], content: str, *, window: int = 80
) -> str:
    """Spec §15：snippet 围绕首个命中 term 截取。

    terms 通常是 ``parse_query`` 输出的 ascii_tokens + cjk_grams + 原始 query。
    对 ASCII token 使用大小写不敏感匹配;对 CJK trigram 直接子串匹配。
    """
    if not content:
        return ""
    if not terms:
        return content[:window]
    lowered = content.lower()
    needle_pos = -1
    needle_len = 0
    for term in terms:
        if not term:
            continue
        candidate = lowered.find(term.lower())
        if candidate == -1:
            continue
        if needle_pos == -1 or candidate < needle_pos:
            needle_pos = candidate
            needle_len = len(term)
    if needle_pos == -1:
        return content[:window]
    start = max(0, needle_pos - window // 4)
    end = min(len(content), needle_pos + needle_len + window)
    snippet = content[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(content):
        snippet = snippet + "…"
    return snippet


def _delete_retrieval_traces_for_source(session: Session, source_id: int) -> None:
    """删除引用指定 Source 的 Retrieval Trace。

    Trace 表刻意不建立 Source 外键，只保存评估所需的稳定命中 ID。永久删除
    Source 时按受控 JSON 结构清理相关 Trace，避免用 ``LIKE`` 匹配正文，
    也避免误删其他 Source 的评估记录。
    """
    rows = session.execute(
        select(
            KnowledgeRetrievalTrace.id,
            KnowledgeRetrievalTrace.filters_json,
            KnowledgeRetrievalTrace.hits_json,
        )
    ).all()
    trace_ids: list[int] = []
    for trace_id, filters_json, hits_json in rows:
        try:
            filters = json.loads(filters_json or "{}")
        except (TypeError, json.JSONDecodeError):
            filters = {}
        try:
            hits = json.loads(hits_json or "[]")
        except (TypeError, json.JSONDecodeError):
            hits = []

        source_ids = filters.get("source_ids") if isinstance(filters, dict) else None
        if isinstance(source_ids, list) and any(
            str(value) == str(source_id) for value in source_ids
        ):
            trace_ids.append(int(trace_id))
            continue
        if isinstance(hits, list) and any(
            isinstance(hit, dict)
            and str(hit.get("source_id")) == str(source_id)
            for hit in hits
        ):
            trace_ids.append(int(trace_id))

    if trace_ids:
        session.execute(
            delete(KnowledgeRetrievalTrace).where(
                KnowledgeRetrievalTrace.id.in_(trace_ids)
            )
        )


def _to_retrieval_trace_record(row: KnowledgeRetrievalTrace) -> RetrievalTraceRecord:
    try:
        filters = json.loads(row.filters_json or "{}")
    except json.JSONDecodeError:
        filters = {}
    try:
        hits = json.loads(row.hits_json or "[]")
    except json.JSONDecodeError:
        hits = []
    return RetrievalTraceRecord(
        id=row.id,
        query=row.query,
        filters=filters if isinstance(filters, dict) else {},
        hits=hits if isinstance(hits, list) else [],
        duration_ms=row.duration_ms,
        evaluation_label=row.evaluation_label,
        error_code=row.error_code,
        created_at=row.created_at,
    )


def make_evidence_id(
    *,
    snapshot_digest: str,
    extractor_version: str,
    locator: str,
    content_hash: str,
) -> str:
    payload = f"{snapshot_digest}|{extractor_version}|{locator}|{content_hash}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"ev_{digest}"


def _fail_stale_brief_jobs_for_snapshot(
    session: Session,
    *,
    source_id: int,
    snapshot_id: int,
    moment: datetime,
) -> None:
    """终结不再属于当前 Snapshot 的 Brief Job/Attempt。

    Extraction 切换 active Snapshot 时，旧 Job 可能仍在 pending 或已经被 worker
    claim。它们不能继续读取新 Snapshot，也不能让旧模型结果覆盖当前 Brief；统一以
    ``brief_snapshot_stale`` 失败终态收口，迟到提交自然会被 Job 状态门禁拒绝。
    """
    stale_jobs = session.execute(
        select(KnowledgeJob).where(
            KnowledgeJob.source_id == source_id,
            KnowledgeJob.kind == "brief",
            KnowledgeJob.status.in_(("pending", "running")),
            or_(
                KnowledgeJob.snapshot_id.is_(None),
                KnowledgeJob.snapshot_id != snapshot_id,
            ),
        )
    ).scalars().all()
    for job_row in stale_jobs:
        if job_row.attempt_id is not None:
            attempt_row = session.get(KnowledgeBriefAttempt, job_row.attempt_id)
            if (
                attempt_row is not None
                and attempt_row.source_id == source_id
                and attempt_row.snapshot_id != snapshot_id
                and attempt_row.status in ("pending", "processing")
            ):
                attempt_row.status = "failed"
                attempt_row.error_code = "brief_snapshot_stale"
                attempt_row.error_message = "Source active Snapshot 已更新"
                attempt_row.updated_at = moment
        job_row.status = "failed"
        job_row.stage = "brief_snapshot_stale"
        job_row.progress = 100
        job_row.error_code = "brief_snapshot_stale"
        job_row.error_message = "Source active Snapshot 已更新"
        job_row.lease_expires_at = None
        job_row.heartbeat_at = None
        job_row.updated_at = moment


def _mark_brief_outdated_for_snapshot(
    session: Session,
    source_row: Optional[KnowledgeSource],
    snapshot_id: int,
) -> None:
    """Snapshot 代际切换时保留旧 Brief，并在同一事务内标记过期。"""
    if source_row is None:
        return
    _fail_stale_brief_jobs_for_snapshot(
        session,
        source_id=source_row.id,
        snapshot_id=snapshot_id,
        moment=datetime.now(timezone.utc),
    )
    brief_row = session.execute(
        select(KnowledgeSourceBrief).where(
            KnowledgeSourceBrief.source_id == source_row.id
        )
    ).scalars().first()
    if brief_row is None or brief_row.snapshot_id == snapshot_id:
        return
    brief_row.outdated = True
    brief_row.updated_at = datetime.now(timezone.utc)
    source_row.brief_status = "outdated"


def _mark_brief_enqueue_pending_for_snapshot(
    session: Session,
    source_row: KnowledgeSource,
    snapshot_id: int,
) -> None:
    """为 Extraction 提交与 Brief 入队之间的崩溃窗口留下可恢复标记。

    KV1-01 / ADR-0003：V1 导入不自动触发 Brief，``commit_extraction`` 不再调用本方法，
    Source 在 Extraction 提交后保持 ``brief_status=not_started``。方法保留以备 V1.1
    恢复自动 Brief 入队时重新启用崩溃恢复标记；此处不删除 Brief 基础设施代码。
    """
    brief_row = session.execute(
        select(KnowledgeSourceBrief).where(
            KnowledgeSourceBrief.source_id == source_row.id
        )
    ).scalars().first()
    if brief_row is not None and brief_row.snapshot_id == snapshot_id:
        return
    active_job = session.execute(
        select(KnowledgeJob.id)
        .where(
            KnowledgeJob.source_id == source_row.id,
            KnowledgeJob.kind == "brief",
            KnowledgeJob.snapshot_id == snapshot_id,
            KnowledgeJob.status.in_(("pending", "running")),
            KnowledgeJob.canceled.is_(False),
        )
        .limit(1)
    ).first()
    active_attempt = session.execute(
        select(KnowledgeBriefAttempt.id)
        .where(
            KnowledgeBriefAttempt.source_id == source_row.id,
            KnowledgeBriefAttempt.snapshot_id == snapshot_id,
            KnowledgeBriefAttempt.status.in_(("pending", "processing")),
        )
        .limit(1)
    ).first()
    if active_job is not None or active_attempt is not None:
        return
    # 旧 Brief 已被 ``_mark_brief_outdated_for_snapshot`` 标记过期时保留 outdated
    # 状态（Spec §10.4 新 Snapshot 保留旧 Brief + 标记过期），不覆盖为 pending。
    if brief_row is not None and brief_row.outdated:
        return
    source_row.brief_status = "pending"
    source_row.brief_block_reason = ""
    source_row.brief_error_code = ""
    source_row.brief_error_message = ""


def commit_extraction(
    session: Session,
    *,
    snapshot_input: SnapshotCreateInput,
    evidence_drafts: Iterable[EvidenceDraftInput],
    source_id: int,
    source_title: str,
    extractor_version: str,
    asset_inputs: Iterable[AssetCreateInput] = (),
) -> tuple[KnowledgeExtractionSnapshot, list[KnowledgeEvidence]]:
    """单事务提交：Snapshot + Evidence + FTS + Asset + Source 状态切换。

    调用方负责包在 Begin/commit 中。Spec §9 要求 Snapshot/Evidence/FTS/extracted 状态
    在同一事务中可见，任一失败回滚后旧 Snapshot 仍可用，首次失败时 Source 不可搜索。

    幂等：Spec §7.2 同版本重跑 upsert，不覆盖、不立即删除。若 Snapshot 已存在且 digest
    一致，直接复用现有 Evidence 行（包括 FTS），不重复写入。

    KI-04：``asset_inputs`` 用于写入 ``knowledge_source_assets`` 行；``evidence_drafts``
    中 ``kind == 'asset'`` 的草稿会通过 ``logical_name`` 关联到本批 Asset 行，写入
    ``asset_id``，但 **不** 进入 FTS——Spec §4.4 明确图片字节不进 FTS。
    """
    existing_snapshot = session.execute(
        select(KnowledgeExtractionSnapshot).where(
            KnowledgeExtractionSnapshot.source_id == snapshot_input.source_id,
            KnowledgeExtractionSnapshot.extractor_version == snapshot_input.extractor_version,
        )
    ).scalars().first()

    if existing_snapshot is not None:
        if existing_snapshot.digest == snapshot_input.digest:
            existing_evidence = (
                session.execute(
                    select(KnowledgeEvidence).where(
                        KnowledgeEvidence.snapshot_id == existing_snapshot.id
                    ).order_by(KnowledgeEvidence.ordinal.asc())
                )
                .scalars()
                .all()
            )
            source_row = session.get(KnowledgeSource, source_id)
            if source_row is not None:
                source_row.active_snapshot_id = existing_snapshot.id
                source_row.extraction_status = "extracted"
                source_row.extraction_error_code = ""
                source_row.extraction_error_message = ""
                _mark_brief_outdated_for_snapshot(
                    session, source_row, existing_snapshot.id
                )
            return existing_snapshot, list(existing_evidence)
        # extractor 版本相同但 digest 不同：内部不一致，应当创建新版本而非覆盖。
        raise RuntimeError(
            "source_integrity_mismatch: snapshot digest drift within same extractor version"
        )

    snapshot_row = KnowledgeExtractionSnapshot(
        source_id=snapshot_input.source_id,
        extractor_version=snapshot_input.extractor_version,
        parser_version=snapshot_input.parser_version,
        normalization_version=snapshot_input.normalization_version,
        tokenizer_version=snapshot_input.tokenizer_version,
        encoding=snapshot_input.encoding,
        detection_method=snapshot_input.detection_method,
        canonical_text=snapshot_input.canonical_text,
        structure_manifest=snapshot_input.structure_manifest,
        metadata_extraction_version=snapshot_input.metadata_extraction_version,
        digest=snapshot_input.digest,
        token_count=snapshot_input.token_count,
        char_count=snapshot_input.char_count,
    )
    session.add(snapshot_row)
    session.flush()

    # Spec §14.3 先写 Asset 行，再用 logical_name → asset_id 字典关联 Evidence。
    asset_id_by_logical_name: dict[str, int] = {}
    for asset_input in asset_inputs:
        existing_asset = session.execute(
            select(KnowledgeSourceAsset).where(
                KnowledgeSourceAsset.source_id == source_id,
                KnowledgeSourceAsset.logical_name == asset_input.logical_name,
            )
        ).scalars().first()
        if existing_asset is not None:
            asset_id_by_logical_name[asset_input.logical_name] = existing_asset.id
            continue
        asset_row = KnowledgeSourceAsset(
            source_id=source_id,
            logical_name=asset_input.logical_name,
            media_type=asset_input.media_type,
            relative_path=asset_input.relative_path,
            bytes=asset_input.bytes_size,
            sha256=asset_input.sha256,
            width=asset_input.width,
            height=asset_input.height,
        )
        session.add(asset_row)
        session.flush()
        asset_id_by_logical_name[asset_input.logical_name] = asset_row.id

    drafts = list(evidence_drafts)
    created: list[KnowledgeEvidence] = []
    previous_id: Optional[str] = None
    for index, draft in enumerate(drafts):
        evidence_id = make_evidence_id(
            snapshot_digest=snapshot_input.digest,
            extractor_version=extractor_version,
            locator=draft.locator,
            content_hash=draft.content_hash,
        )
        heading_path_json = json.dumps(list(draft.heading_path), ensure_ascii=False)
        evidence_kind = draft.kind
        asset_id_value: Optional[int] = None
        if evidence_kind == "asset":
            asset_id_value = asset_id_by_logical_name.get(draft.logical_name)
        evidence_row = KnowledgeEvidence(
            id=evidence_id,
            source_id=source_id,
            snapshot_id=snapshot_row.id,
            kind=evidence_kind,
            block_kind=draft.block_kind,
            ordinal=index + 1,
            heading_path_json=heading_path_json,
            char_start=draft.char_start,
            char_end=draft.char_end,
            line_start=draft.line_start,
            line_end=draft.line_end,
            canonical_excerpt=draft.canonical_excerpt,
            search_text=draft.search_text,
            content_hash=draft.content_hash,
            asset_id=asset_id_value,
            previous_evidence_id=previous_id,
            next_evidence_id=None,
        )
        session.add(evidence_row)
        session.flush()
        created.append(evidence_row)
        if previous_id is not None:
            prev = session.get(KnowledgeEvidence, previous_id)
            if prev is not None:
                prev.next_evidence_id = evidence_id
        previous_id = evidence_id

    # Spec §4.4：图片字节不进 FTS。asset Evidence 的 search_text 仅含 alt text，
    # 用 alt text 进入 FTS 以支持 "alt 命中" 查询；canonical_excerpt 是 image literal，
    # 不写 FTS 以避免 ![](url) 噪音。
    # FTS 不保存 snapshot_id，切换 active Snapshot 前必须移除旧 Snapshot
    # 的行，避免同一 Source 的历史 Evidence 继续参与检索。
    session.execute(
        text("DELETE FROM knowledge_evidence_fts WHERE source_id = :sid"),
        {"sid": source_id},
    )

    for evidence_row in created:
        if evidence_row.kind == "asset":
            fts_content = evidence_row.search_text
            if not fts_content:
                continue
        else:
            fts_content = evidence_row.search_text or evidence_row.canonical_excerpt
        session.execute(
            text(
                """
                INSERT INTO knowledge_evidence_fts (
                    evidence_id, source_id, source_title, heading_path, content
                ) VALUES (:eid, :sid, :stitle, :hpath, :content)
                """
            ),
            {
                "eid": evidence_row.id,
                "sid": evidence_row.source_id,
                "stitle": source_title,
                "hpath": evidence_row.heading_path_json,
                "content": fts_content,
            },
        )

    source_row = session.get(KnowledgeSource, source_id)
    if source_row is not None:
        source_row.active_snapshot_id = snapshot_row.id
        source_row.extraction_status = "extracted"
        source_row.extraction_error_code = ""
        source_row.extraction_error_message = ""
        # KBR-02：frontmatter 白名单 provenance 沿 Source 所有权写入。display_title
        # 为空时用 frontmatter title 填充（使其进入 FTS source_title 可定位，且不覆盖
        # 用户已 PATCH 的标题）；author/published_at 是确定性提取的派生 provenance。
        if not source_row.display_title and snapshot_input.provenance_title:
            source_row.display_title = snapshot_input.provenance_title
            # INSERT 阶段 FTS source_title 用的是调用方传入的 title_hint，frontmatter
            # title 填充 display_title 后需回写 source_title，使标题可定位 Source Evidence。
            session.execute(
                text(
                    "UPDATE knowledge_evidence_fts "
                    "SET source_title = :title WHERE source_id = :sid"
                ),
                {"title": snapshot_input.provenance_title, "sid": source_id},
            )
        if snapshot_input.provenance_author:
            source_row.author = snapshot_input.provenance_author
        if snapshot_input.provenance_published_at is not None:
            source_row.published_at = snapshot_input.provenance_published_at
        # frontmatter url 沿 Origin 所有权补全：首条 Origin 若无 origin_url 则填入，
        # 不覆盖 paste 场景已提供的 url。
        if snapshot_input.provenance_url:
            origin_row = _get_first_origin(session, source_id)
            if origin_row is not None and not origin_row.origin_url:
                origin_row.origin_url = snapshot_input.provenance_url
        _mark_brief_outdated_for_snapshot(session, source_row, snapshot_row.id)

    return snapshot_row, created
