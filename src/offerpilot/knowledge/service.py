"""Knowledge Ingest 编排服务。

实现 Spec §6 上传协议：
1. Preflight（严格解码 + token 上限 + 5MiB 上限 + Bundle 限制）+ hash
2. 去重检查
3. staging 写入
4. final 目录创建 + 原子 rename
5. SQLite 单事务：create_source + origin + pending extraction job
6. 返回 202

KI-03 范围：
- 支持 ``.md``、``.txt`` 文件，以及粘贴正文（视为虚拟 ``main.md``）。
- 编码矩阵：UTF-8 / UTF-8 BOM / UTF-16LE BE BOM / 高置信 GBK·GB18030，禁止
  ``errors='ignore'`` 或 ``errors='replace'``。
- 固定 product tokenizer（cl100k_base）+ 64,000 token 上限与 5MiB 字节上限同时执行。
- 错误返回实际值与允许值。

KI-04 范围：
- 支持 Source Bundle（Markdown 主文件 + PNG/JPEG/WebP 附件）。
- 图片真实解码、媒体类型校验、扁平路径白名单、像素 / 字节 / 数量限制。
- Bundle source_hash 包含主文件 + 附件 + 逻辑路径 manifest。
- 图片引用映射为 Asset Evidence；不调用多模态，不让图片字节进入 FTS。

KI-06 范围：
- 同步 archive / unarchive 操作：只动 lifecycle 与 archived_at。
- 永久删除 ``purge_source``：只创建持久 Delete Job；quarantine、SQLite 清理和物理删除
  由 Extraction Worker 执行。

Extraction 由持久队列异步触发。事务失败时 final 目录可能残留无数据库记录的孤儿原件，
由启动恢复负责清理（KI-07 实现完整恢复）。
"""

from __future__ import annotations

import json
import re
import secrets
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable, Iterator
from typing import Optional
from urllib.parse import urlsplit

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.config import Config
from offerpilot.knowledge.assets import (
    AssetInput,
    AssetValidationError,
    VerifiedAsset,
    verify_bundle,
)
from offerpilot.knowledge.brief import (
    BRIEF_MIN_CONTEXT_WINDOW,
    BRIEF_PROMPT_VERSION,
    BRIEF_SCHEMA_VERSION,
)
from offerpilot.knowledge.encoding import (
    EncodingError,
    decode_source_bytes,
)
from offerpilot.knowledge.extractor import (
    EXTRACTOR_VERSION,
    MAX_FILE_BYTES,
    MarkdownExtractor,
    compute_bundle_source_hash,
    compute_source_hash,
)
from offerpilot.knowledge.repository import (
    AssetCreateInput,
    DeleteJobSnapshot,
    JobCreateInput,
    KnowledgeRepository,
    OriginCreateInput,
    SourceRecord,
)
from offerpilot.knowledge.tokenizer import (
    TOKENIZER_VERSION,
    TokenizerUnavailableError,
    count_tokens,
    max_token_limit,
)
from offerpilot.models import (
    KnowledgeJob,
    KnowledgeSource,
    KnowledgeSourceAsset,
    KnowledgeSourceOrigin,
)


_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Spec §4.1 支持的输入文件类型。粘贴正文统一作为虚拟 ``main.md``。
_SUPPORTED_TEXT_EXTENSIONS = (".md", ".txt")
_PASTE_DEFAULT_FILENAME = "main.md"


@dataclass(frozen=True)
class IngestRequest:
    filename: str
    content_bytes: bytes
    title_hint: str = ""
    import_method: str = "file"
    origin_url: str = ""
    # KI-04 Bundle 附件。空列表表示非 Bundle 上传。Service 会在 Bundle 模式下
    # 强制要求附件非空，并校验 Markdown 中所有图片引用都被覆盖。
    asset_inputs: tuple[AssetInput, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class IngestResult:
    source: SourceRecord
    job_id: int
    deduplicated: bool
    extraction_failed: bool
    extraction_error_code: str
    extraction_error_message: str


@dataclass(frozen=True)
class PurgeResult:
    """KI-06：永久删除返回的 Delete Job 快照。

    Spec §16.1：永久删除返回 202 与 pending Delete Job。Job 会由 Extraction Worker
    在完成 SQLite 与文件清理时一并删除；``occurred_at`` 是请求入队时间。
    """

    job_snapshot: DeleteJobSnapshot
    occurred_at: datetime


class IngestError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class KnowledgeIngestService:
    """Source 上传 + Extraction 编排。"""

    def __init__(
        self,
        repository: KnowledgeRepository,
        data_dir: Path,
        session_factory: "sessionmaker[Session]",
        extractor: Optional[MarkdownExtractor] = None,
        config: Optional[Config] = None,
    ) -> None:
        self._repository = repository
        self._data_dir = data_dir
        self._session_factory = session_factory
        self._extractor = extractor or MarkdownExtractor()
        self._config = config

    def update_config(self, config: Config) -> None:
        """KI-10：settings 更新后刷新内存 config，确保后续 rebuild / outdated 检测、
        enqueue block 判断使用最新 Provider 配置，而非启动时的快照。
        """
        self._config = config

    def ingest(self, request: IngestRequest) -> IngestResult:
        filename = request.filename.strip()
        if not filename:
            raise IngestError("unsupported_type", "缺少文件名")
        is_paste = request.import_method == "paste"
        if not is_paste and not _has_supported_extension(filename):
            raise IngestError(
                "unsupported_type",
                "当前只支持 .md / .txt 文件、粘贴正文或 Bundle 上传",
            )
        # Spec §5.1 / KI-05：粘贴正文可选 origin_url 仅作为 provenance 保存;
        # 系统绝不访问网络,且必须拒绝非 http/https 协议以防止 schema 注入或本地文件读取。
        # Spec §4.1 仅授权 paste 路径接受 origin_url,file / bundle 路径不允许携带。
        if request.origin_url:
            if not is_paste:
                raise IngestError(
                    "unsupported_type",
                    "origin_url 只能在粘贴正文场景下使用",
                )
            _validate_origin_url(request.origin_url)
        safe_filename = _safe_filename(filename if not is_paste else _PASTE_DEFAULT_FILENAME)
        if not safe_filename:
            raise IngestError("unsupported_type", "文件名仅含不支持的字符")

        size = len(request.content_bytes)
        if size == 0:
            raise IngestError("unsupported_type", "原文为空，无法解析")
        if size > MAX_FILE_BYTES:
            raise IngestError(
                "source_too_large",
                (
                    f"原文 {size} 字节超出上限 {MAX_FILE_BYTES} 字节；"
                    "请按主题拆分资料后再上传"
                ),
            )

        try:
            decoded = decode_source_bytes(request.content_bytes)
            if "\x00" in decoded.text:
                raise IngestError(
                    "encoding_unknown",
                    "原文中存在 NUL 控制字符，无法安全解析",
                )
            token_count_value = count_tokens(decoded.text).count
        except EncodingError as exc:
            raise IngestError(exc.code, exc.message) from exc
        except TokenizerUnavailableError as exc:
            raise IngestError("tokenizer_unavailable", str(exc)) from exc
        token_limit = max_token_limit()
        if token_count_value > token_limit:
            raise IngestError(
                "source_too_large",
                (
                    f"原文 {token_count_value} tokens 超出上限 {token_limit} tokens；"
                    "请按主题拆分资料后再上传"
                ),
            )

        # KI-04：Bundle 模式下校验附件字节 / 像素 / 路径；任何附件错误均按 bundle_invalid
        # 拒绝整个上传，Spec §4.4 不允许部分 Source。
        verified_assets: list[VerifiedAsset] = []
        is_bundle = bool(request.asset_inputs)
        extension = Path(safe_filename).suffix.lower()
        if is_bundle and extension != ".md":
            raise IngestError(
                "unsupported_type",
                "Bundle 主文件必须是 .md",
            )
        if is_bundle:
            try:
                verified_assets, _ = verify_bundle(
                    request.content_bytes, list(request.asset_inputs)
                )
            except AssetValidationError as exc:
                raise IngestError(exc.code, exc.message) from exc
            _validate_asset_final_names(verified_assets)
            _validate_image_references(
                self._extractor.image_references(decoded.text), verified_assets
            )

        if is_bundle:
            source_hash = compute_bundle_source_hash(
                request.content_bytes,
                [(va.logical_name, va.content_bytes) for va in verified_assets],
            )
        else:
            source_hash = compute_source_hash(request.content_bytes)

        existing = self._repository.get_source_by_hash(source_hash)
        if existing is not None:
            # Spec §5.1：命中已有 Source 必须追加 Origin 记录 provenance;
            # 但**不再**为 dedup 创建第二个 Extract Job,避免重复计权 / 重复排队。
            # 命中 processing/pending 时复用当前 active extract Job;
            # 命中 extracted/ready/brief_failed 时返回 Source 自身 (job_id 取 Source 最近一个 extract Job)。
            self._repository.append_origin(
                OriginCreateInput(
                    source_id=existing.id,
                    import_method=request.import_method,
                    original_filename=request.filename,
                    origin_url=request.origin_url,
                )
            )
            refreshed = self._repository.get_source(existing.id)
            if refreshed is None:
                raise IngestError(
                    "source_integrity_mismatch",
                    "已存在 Source 在重读时丢失",
                )
            active_job_id = self._repository.find_latest_extract_job_id(existing.id)
            if active_job_id is None:
                # Spec §5.1：dedup 必须复用已有 Extract Job,不创建第二个 Job。
                # Source 存在但没有任何 Extract Job 属于内部一致性破坏,fail-fast 暴露 bug,
                # 不掩盖。
                raise IngestError(
                    "source_integrity_mismatch",
                    "已存在 Source 缺少 Extract Job 历史",
                )
            return IngestResult(
                source=refreshed,
                job_id=active_job_id,
                deduplicated=True,
                extraction_failed=False,
                extraction_error_code="",
                extraction_error_message="",
            )

        deleting = self._repository.get_deleting_source_by_hash(source_hash)
        if deleting is not None:
            # deleting Source 仍暂时占用 source_hash 唯一约束；不能把这个可预期的
            # 并发窗口伪装成内部 IntegrityError。删除完成后客户端可安全重试。
            raise IngestError(
                "source_deleting",
                "相同内容的 Source 正在删除，请稍后重试",
                status_code=409,
            )

        knowledge_root = self._data_dir / "knowledge"
        knowledge_root.mkdir(parents=True, exist_ok=True)
        if knowledge_root.is_symlink():
            raise IngestError("source_integrity_mismatch", "Knowledge 根目录不能是符号链接")
        staging_dir = knowledge_root / "staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        if staging_dir.is_symlink():
            raise IngestError("source_integrity_mismatch", "staging 目录不能是符号链接")
        upload_id = secrets.token_urlsafe(12)
        staging_source_dir = staging_dir / upload_id
        staging_source_dir.mkdir(parents=True, exist_ok=True)
        staging_path = staging_source_dir / safe_filename
        staging_path.write_bytes(request.content_bytes)
        staging_asset_paths: list[tuple[VerifiedAsset, Path]] = []
        if is_bundle:
            for asset in verified_assets:
                asset_staging_path = staging_source_dir / asset.logical_name
                asset_staging_path.write_bytes(asset.content_bytes)
                staging_asset_paths.append((asset, asset_staging_path))

        manifest: dict[str, object] = {
            "kind": "text" if safe_filename.lower().endswith(".txt") else "markdown",
            "main_filename": safe_filename,
            "total_bytes": size,
            "source_hash": source_hash,
            "extractor_version": EXTRACTOR_VERSION,
            "encoding": decoded.encoding,
            "detection_method": decoded.detection_method,
            "tokenizer_version": TOKENIZER_VERSION,
            "token_count": token_count_value,
        }
        if is_bundle:
            manifest["bundle"] = {
                "asset_count": len(verified_assets),
                "asset_bytes": sum(va.bytes_size for va in verified_assets),
                "asset_logical_names": [va.logical_name for va in verified_assets],
            }
        manifest_json = json.dumps(manifest, ensure_ascii=False, sort_keys=True)

        title_hint = (
            request.title_hint
            or _derive_title_from_text(decoded.text)
            or _derive_title_from_filename(safe_filename)
        ).strip()

        source_kind = "markdown"
        main_media_type = "text/markdown"
        if safe_filename.lower().endswith(".txt"):
            source_kind = "text"
            main_media_type = "text/plain"
        if is_bundle:
            source_kind = "bundle"

        try:
            source_id, job_id = self._commit_new_source(
                source_hash=source_hash,
                source_kind=source_kind,
                main_media_type=main_media_type,
                safe_filename=safe_filename,
                title_hint=title_hint,
                manifest_json=manifest_json,
                size=size,
                token_count=token_count_value,
                request=request,
                staging_path=staging_path,
                staging_source_dir=staging_source_dir,
                verified_assets=verified_assets,
                staging_asset_paths=staging_asset_paths,
            )
        except IntegrityError as exc:
            # 并发兜底:两个 ingest 同时通过 get_source_by_hash 检查后,UNIQUE 约束
            # 拦截第二个插入。此时 staging 与 final 目录残留必须由本路径清理,
            # 然后回退到 dedup 路径,避免半个 Bundle / 孤儿文件。
            _safe_cleanup(staging_source_dir)
            if "source_hash" not in str(exc).lower():
                # 其他约束失败是实现或数据库一致性错误，不能误当成重复上传并
                # 静默降级为已有 Source。
                raise
            existing_after = self._repository.get_source_by_hash(source_hash)
            if existing_after is None:
                deleting_after = self._repository.get_deleting_source_by_hash(source_hash)
                if deleting_after is not None:
                    raise IngestError(
                        "source_deleting",
                        "相同内容的 Source 正在删除，请稍后重试",
                        status_code=409,
                    ) from exc
                raise IngestError(
                    "source_integrity_mismatch",
                    "并发冲突但未发现已有 Source",
                ) from exc
            self._repository.append_origin(
                OriginCreateInput(
                    source_id=existing_after.id,
                    import_method=request.import_method,
                    original_filename=request.filename,
                    origin_url=request.origin_url,
                )
            )
            refreshed_existing = self._repository.get_source(existing_after.id)
            if refreshed_existing is None:
                raise IngestError(
                    "source_integrity_mismatch",
                    "并发兜底重读 Source 失败",
                ) from exc
            existing_job_id = self._repository.find_latest_extract_job_id(existing_after.id)
            if existing_job_id is None:
                raise IngestError(
                    "source_integrity_mismatch",
                    "并发兜底未找到已有 Extract Job",
                ) from exc
            return IngestResult(
                source=refreshed_existing,
                job_id=existing_job_id,
                deduplicated=True,
                extraction_failed=False,
                extraction_error_code="",
                extraction_error_message="",
            )

        refreshed_source = self._repository.get_source(source_id)
        refreshed_job = self._repository.get_job(job_id)
        if refreshed_source is None or refreshed_job is None:
            raise IngestError(
                "source_integrity_mismatch",
                "事务提交后无法读取 Source / Job",
            )

        # KV1-01 / ADR-0003：V1 导入在 Evidence 提交后结束，不自动入队 Brief、不检查
        # Provider、不写 brief_enqueue_failed；Source 保持 brief_status=not_started。
        # 显式 rebuild_brief 走独立 create_job 路径，不受此处影响。
        return IngestResult(
            source=refreshed_source,
            job_id=refreshed_job.id,
            deduplicated=False,
            extraction_failed=False,
            extraction_error_code="",
            extraction_error_message="",
        )

    def archive_source(self, source_id: int) -> Optional[SourceRecord]:
        """KI-06：归档 Source。

        Spec §5.3：归档是同步 SQLite 操作,只动 lifecycle + archived_at;不删除文件、
        Evidence、Brief、Job 历史;归档默认不出现在列表和普通 Evidence 检索中。详情 /
        原文 / 附件仍可读;归档不会自动过期或后台清理。
        """
        return self._repository.archive_source(source_id)

    def enqueue_or_block_brief(self, source_id: int) -> Optional[SourceRecord]:
        """KI-09：Source 进入 ``extracted`` 后，根据 Provider 状态决定 enqueue 或 block。

        Spec §11.2：
        - 配置合格 Provider → 创建 ``kind=brief, queue=brief, status=pending`` Job。
        - 无合格 Provider → ``brief_status=pending`` + ``brief_block_reason=provider_unavailable``
          或 ``provider_context_too_small``。

        Spec §11.2 "配置变化不自动批量生成"：用户显式重建由 ``rebuild_brief`` 触发。
        KV1-01 / ADR-0003：V1 导入不再自动调用本方法；当前仅供 acceptance profile
        与 Brief 测试作为 callback 注册入口，V1.1 恢复自动 Brief 时重新启用。
        """
        source = self._repository.get_source(source_id)
        if source is None or source.lifecycle == "deleting":
            return None
        if source.extraction_status != "extracted":
            return source
        if source.brief_status in ("processing", "ready"):
            return source
        block_reason = self._brief_provider_block_reason()
        if block_reason:
            return self._repository.update_source_state(
                source_id,
                brief_status="pending",
                brief_block_reason=block_reason,
                brief_error_code=block_reason,
                brief_error_message=(
                    "未配置满足 Brief 96K context 的 Provider，请先在设置中配置"
                ),
            )
        self._repository.create_job(
            JobCreateInput(
                kind="brief",
                queue="brief",
                source_id=source_id,
                snapshot_id=source.active_snapshot_id,
                stage="brief_pending",
            )
        )
        return self._repository.update_source_state(
            source_id,
            brief_status="pending",
            brief_block_reason="",
            brief_error_code="",
            brief_error_message="",
        )

    def rebuild_brief(self, source_id: int) -> tuple[Optional[SourceRecord], str]:
        """KI-09：用户显式触发 Brief 重建（Spec §16.1 ``POST /sources/{id}/brief/rebuild``）。

        返回 ``(source, status_message)``。``source=None`` 表示 Source 不存在或处于
        不可重建状态，由 API 层映射 4xx 错误。

        Spec §10.4 "用户使用当前配置重建会创建新 Attempt 和独立重试预算"。
        Spec §11.2 无合格 Provider 时不创建 Attempt，仅返回 block reason。
        """
        source = self._repository.get_source(source_id)
        if source is None or source.lifecycle == "deleting":
            return None, "Source 不存在"
        if source.extraction_status != "extracted":
            return source, "Source 尚未完成 Extraction"
        block_reason = self._brief_provider_block_reason()
        if block_reason:
            updated = self._repository.update_source_state(
                source_id,
                brief_status="pending",
                brief_block_reason=block_reason,
                brief_error_code=block_reason,
                brief_error_message=(
                    "未配置满足 Brief 96K context 的 Provider，请先在设置中配置"
                ),
            )
            return updated, block_reason
        self._repository.create_job(
            JobCreateInput(
                kind="brief",
                queue="brief",
                source_id=source_id,
                snapshot_id=source.active_snapshot_id,
                stage="brief_rebuild_pending",
            )
        )
        # 入队后立即把 Source 标为 pending，让列表/详情无需等 worker 创建
        # Attempt 就能显示「排队中」。已是 processing 时不降级。
        if source.brief_status == "processing":
            updated = self._repository.get_source(source_id)
        else:
            updated = self._repository.update_source_state(
                source_id,
                brief_status="pending",
                brief_block_reason="",
                brief_error_code="",
                brief_error_message="",
            )
        return updated, "brief_rebuild_queued"

    def _brief_provider_block_reason(self) -> str:
        """Spec §11.2 / §11.3：active 与 fallback 都不满足 96K 时返回 block reason。

        只要任一满足即视为可用（fallback 可在 primary 不可用时承接）。两者均不可用时，
        区分 ``provider_unavailable``（无 api_key）与 ``provider_context_too_small``。
        没有传入 Config 时（旧调用方）默认认为 Provider 可用，便于早期测试在 worker
        端单独校验；正式启用 Brief 后 service 一定传入 Config。
        """
        if self._config is None:
            return ""
        primary = self._config.active_provider()
        fallback = self._config.fallback_provider()
        primary_ok = (
            primary is not None
            and primary.enabled
            and bool(primary.api_key)
            and primary.context_window >= BRIEF_MIN_CONTEXT_WINDOW
        )
        fallback_ok = (
            fallback is not None
            and fallback.enabled
            and bool(fallback.api_key)
            and fallback.context_window >= BRIEF_MIN_CONTEXT_WINDOW
        )
        if primary_ok or fallback_ok:
            return ""
        any_api_key = bool(primary and primary.api_key) or any(
            profile.api_key for profile in self._config.provider_profiles()
        )
        if not any_api_key:
            return "provider_unavailable"
        return "provider_context_too_small"

    def refresh_brief_outdated(self, source_id: int) -> Optional[SourceRecord]:
        """KI-10 / Spec §10.4：检测当前 Brief 是否相对活跃配置过期。

        比较 winning Attempt 的 Provider/Model/Prompt/Schema 与当前 active provider，
        以及 Brief.snapshot_id 与 Source.active_snapshot_id。任一不一致则标记
        ``outdated=True``；完全一致则清除标记（rebuild 成功后自然匹配）。

        Spec §10.4 "不自动批量调用模型"：本方法只更新标记，不创建 rebuild Job，
        也不通知任何 Provider。返回更新后的 Source（便于 API 反映最新 outdated 状态）。
        """
        source = self._repository.get_source(source_id)
        if source is None:
            return None
        provider = self._config.active_provider() if self._config is not None else None
        provider_id = provider.id if provider is not None else ""
        provider_model = provider.model if provider is not None else ""
        self._repository.mark_brief_outdated_if_stale(
            source_id,
            provider_id=provider_id,
            provider_model=provider_model,
            prompt_version=BRIEF_PROMPT_VERSION,
            schema_version=BRIEF_SCHEMA_VERSION,
            snapshot_id=source.active_snapshot_id or 0,
        )
        return self._repository.get_source(source_id)

    def unarchive_source(self, source_id: int) -> Optional[SourceRecord]:
        """KI-06：取消归档 Source。

        Spec §5.3：取消归档同样是同步 SQLite 操作,lifecycle 改回 ``active``,archived_at
        清空。不触发 Extraction / Brief / Evidence 重建。
        """
        return self._repository.unarchive_source(source_id)

    def purge_source(self, source_id: int) -> Optional[PurgeResult]:
        """KI-06：永久删除 Source。

        Spec §5.4 删除流程:
        1. ``begin_delete``:lifecycle=deleting,cancel pending/running jobs,create
           pending delete Job。
        2. Extraction Worker 原子移动 Source 目录到 quarantine。
        3. Worker 调用 ``complete_purge``，单 SQLite 事务清理 FTS / Evidence /
           Snapshot / Asset / Origin / Job / Source。
        4. Worker 在事务提交后物理删除 quarantine 目录并写 KnowledgeLog。

        返回 ``PurgeResult``，仅包含待处理 Delete Job 快照；请求线程不执行文件 IO 或
        永久删除事务。

        幂等:source_id 不存在或已删除 → 返回 ``None``,由 API 层返回 404。
        ``lifecycle=deleting`` 重复调用 → 返回 ``None``(避免重复扣费 / 重复 IO)。
        """
        begin_result = self._repository.begin_delete(source_id)
        if begin_result is None:
            return None
        _, delete_job_id = begin_result
        occurred_at = datetime.now(timezone.utc)
        snapshot = DeleteJobSnapshot(
            job_id=delete_job_id,
            source_id=source_id,
            # begin_delete 已在同一事务中创建 pending Job；不再回读 Job，避免 Worker
            # 在请求返回前极快完成删除时把成功请求误报为 500。
            status="pending",
            stage="delete_pending",
            created_at=occurred_at,
        )
        return PurgeResult(job_snapshot=snapshot, occurred_at=occurred_at)

    def _commit_new_source(
        self,
        *,
        source_hash: str,
        source_kind: str,
        main_media_type: str,
        safe_filename: str,
        title_hint: str,
        manifest_json: str,
        size: int,
        token_count: int,
        request: IngestRequest,
        staging_path: Path,
        staging_source_dir: Path,
        verified_assets: list[VerifiedAsset] | None = None,
        staging_asset_paths: list[tuple[VerifiedAsset, Path]] | None = None,
    ) -> tuple[int, int]:
        """单事务创建 Source/Origin/Job + rename + Asset 元数据。

        Spec §6 / §9：数据库提交前完成 final rename（主文件 + 附件）；事务失败时
        无任何 DB 行可见，final 目录残留由启动恢复清理。Bundle 模式下附件落到
        ``knowledge/sources/<source_id>/assets/`` 子目录，与 Spec §13 一致。
        """

        verified_assets_resolved = verified_assets or []
        staging_asset_paths_resolved = staging_asset_paths or []
        # ``session.begin`` 的退出阶段才真正提交事务；此时 final rename 已完成。
        # 用闭包让异常处理能够拿到事务体内创建的 final_dir，避免 IntegrityError/FTS
        # 提交失败后仅清理空 staging、却遗留正式目录。
        final_dir: Optional[Path] = None

        with self._session_factory() as session:
            with _knowledge_commit_transaction(
                session,
                staging_source_dir,
                lambda: final_dir,
            ):
                source_row = KnowledgeSource(
                    source_hash=source_hash,
                    source_kind=source_kind,
                    display_title="",
                    title_hint=title_hint,
                    main_filename=safe_filename,
                    main_media_type=main_media_type,
                    main_relative_path="",
                    manifest_json=manifest_json,
                    total_bytes=size,
                    token_count=token_count,
                    lifecycle="active",
                    extraction_status="pending",
                    extraction_error_code="",
                    extraction_error_message="",
                    brief_status="not_started",
                    brief_block_reason="",
                    brief_error_code="",
                    brief_error_message="",
                )
                session.add(source_row)
                session.flush()
                source_id = source_row.id

                final_relative_path = (
                    f"knowledge/sources/{source_id}/{safe_filename}"
                )
                source_row.main_relative_path = final_relative_path

                sources_root = self._data_dir / "knowledge" / "sources"
                sources_root.mkdir(parents=True, exist_ok=True)
                if sources_root.is_symlink():
                    raise OSError("正式 Source 根目录不能是符号链接")
                final_dir = sources_root / str(source_id)
                final_dir.mkdir(parents=True, exist_ok=True)
                if final_dir.is_symlink():
                    raise OSError("正式 Source 目录不能是符号链接")
                final_path = final_dir / safe_filename
                final_asset_dir = final_dir / "assets"
                if verified_assets_resolved:
                    final_asset_dir.mkdir(parents=True, exist_ok=True)
                    if final_asset_dir.is_symlink():
                        raise OSError("正式 Asset 目录不能是符号链接")
                moved_asset_files: list[Path] = []
                try:
                    staging_path.replace(final_path)
                    for asset, staging_asset_path in staging_asset_paths_resolved:
                        safe_asset_name = _safe_filename(asset.logical_name)
                        if not safe_asset_name:
                            raise OSError(
                                f"附件逻辑名 {asset.logical_name!r} 无法生成安全文件名"
                            )
                        final_asset_path = (
                            final_asset_dir
                            / f"{source_id}-{safe_asset_name}"
                        )
                        staging_asset_path.replace(final_asset_path)
                        moved_asset_files.append(final_asset_path)
                except OSError as exc:
                    _safe_cleanup(staging_source_dir)
                    for moved in moved_asset_files:
                        _safe_cleanup(moved)
                    _safe_cleanup(final_dir)
                    raise IngestError(
                        "source_integrity_mismatch",
                        "原件无法落到正式目录",
                    ) from exc
                try:
                    staging_source_dir.rmdir()
                except OSError:
                    pass

                session.add(
                    KnowledgeSourceOrigin(
                        source_id=source_id,
                        import_method=request.import_method,
                        original_filename=request.filename,
                        origin_url=request.origin_url,
                    )
                )

                job_row = KnowledgeJob(
                    kind="extract",
                    queue="extraction",
                    source_id=source_id,
                    stage="pending",
                    status="pending",
                )
                session.add(job_row)
                session.flush()
                job_id = job_row.id

                # Asset 元数据随 Source 一起提交，Evidence 在 Worker 中根据固定
                # Snapshot 生成并关联到这些不可变 Asset 行。
                asset_commit_inputs: list[AssetCreateInput] = []
                for asset, _ in staging_asset_paths_resolved:
                    safe_asset_name = _safe_filename(asset.logical_name)
                    asset_relative_path = (
                        f"knowledge/sources/{source_id}/assets/"
                        f"{source_id}-{safe_asset_name}"
                    )
                    asset_commit_inputs.append(
                        AssetCreateInput(
                            logical_name=asset.logical_name,
                            media_type=asset.media_type,
                            relative_path=asset_relative_path,
                            bytes_size=asset.bytes_size,
                            sha256=asset.sha256,
                            width=asset.width,
                            height=asset.height,
                        )
                    )

                for asset_input in asset_commit_inputs:
                    session.add(
                        # Asset 行只保存元数据；原始字节已在正式 Source 目录中。
                        KnowledgeSourceAsset(
                            source_id=source_id,
                            logical_name=asset_input.logical_name,
                            media_type=asset_input.media_type,
                            relative_path=asset_input.relative_path,
                            bytes=asset_input.bytes_size,
                            sha256=asset_input.sha256,
                            width=asset_input.width,
                            height=asset_input.height,
                        )
                    )

        return source_id, job_id


def _has_supported_extension(filename: str) -> bool:
    lowered = filename.lower()
    return any(lowered.endswith(ext) for ext in _SUPPORTED_TEXT_EXTENSIONS)


def _validate_image_references(
    references: tuple[str, ...],
    verified_assets: list[VerifiedAsset],
) -> None:
    """Spec §4.4：缺图、重复逻辑名、未使用附件、不支持的媒体类型必须整个 Bundle 失败。

    - 提取 Markdown 中所有 image reference 的 ``logical_name``；
    - 与上传附件比对：缺图、未使用附件均触发 ``bundle_invalid``。
    - 远程/绝对/父目录路径：``safe_logical_name`` 在 verify_bundle 阶段已经拒绝；
      此处只需对剩余的本地引用做完整性比对。
    """

    uploaded = {va.logical_name for va in verified_assets}
    referenced: set[str] = set()
    for logical_name in references:
        logical_name = str(logical_name or "")
        if not logical_name:
            continue
        referenced.add(logical_name)

    missing = referenced - uploaded
    if missing:
        names = ", ".join(sorted(missing))
        raise IngestError(
            "bundle_invalid",
            f"Markdown 引用的图片未在 Bundle 附件中提供：{names}",
        )
    unused = uploaded - referenced
    if unused:
        names = ", ".join(sorted(unused))
        raise IngestError(
            "bundle_invalid",
            f"Bundle 附件未被 Markdown 引用：{names}",
        )


def _validate_asset_final_names(verified_assets: list[VerifiedAsset]) -> None:
    """拒绝不同逻辑名被物理文件名规范化为同一路径。

    Bundle 的逻辑名允许空格和前导点，而正式目录使用 ``_safe_filename`` 生成
    ASCII 文件名；例如 ``.a.png`` 与 ``a.png`` 会发生碰撞。若不在 staging 阶段
    拦截，后续 ``Path.replace`` 会静默覆盖第一个附件，数据库却仍保留两条 Asset。
    """
    seen: dict[str, str] = {}
    for asset in verified_assets:
        safe_name = _safe_filename(asset.logical_name)
        if not safe_name:
            raise IngestError(
                "bundle_invalid",
                f"附件逻辑名 {asset.logical_name!r} 无法生成安全文件名",
            )
        previous = seen.get(safe_name)
        if previous is not None and previous != asset.logical_name:
            raise IngestError(
                "bundle_invalid",
                (
                    "附件逻辑名规范化后发生文件名冲突："
                    f"{previous!r} 与 {asset.logical_name!r} 都映射为 {safe_name!r}"
                ),
            )
        seen[safe_name] = asset.logical_name


def _safe_filename(name: str) -> str:
    collapsed = _SAFE_FILENAME_RE.sub("-", name).strip("-._")
    if not collapsed:
        return ""
    if len(collapsed.encode("utf-8")) > 255:
        collapse_base = collapsed.rsplit(".", 1)[0]
        suffix = collapsed.rsplit(".", 1)[1] if "." in collapsed else ""
        collapse_base = collapse_base[: 200]
        collapsed = f"{collapse_base}.{suffix}" if suffix else collapse_base
    return collapsed


def _derive_title_from_filename(filename: str) -> str:
    for ext in _SUPPORTED_TEXT_EXTENSIONS:
        if filename.lower().endswith(ext):
            return filename[: -len(ext)]
    return filename.removesuffix(".md")


def _derive_title_from_text(text: str) -> str:
    """从原文轻量推导展示标题，不运行 Extraction。"""
    for line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            candidate = match.group(1).strip()
            if candidate:
                return candidate[:120]
    for line in text.splitlines():
        candidate = line.strip()
        if candidate:
            return candidate[:120]
    return ""


def _safe_cleanup(target: Path) -> None:
    try:
        # 清理失败事务产生的目录时不能跟随外部注入的符号链接，否则会把
        # data_dir 之外的文件当作 staging/final 内容递归删除。
        if target.is_symlink():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            for child in target.iterdir():
                _safe_cleanup(child)
            target.rmdir()
        else:
            target.unlink(missing_ok=True)
    except OSError:
        pass


@contextmanager
def _knowledge_commit_transaction(
    session: Session,
    staging_source_dir: Path,
    final_dir_getter: Callable[[], Optional[Path]],
) -> Iterator[None]:
    """事务提交失败时清理上传原件，避免 final 目录成为孤儿。

    文件 rename 必须发生在 SQLite commit 之前。``session.begin`` 的上下文退出才
    会执行 commit，因此 IntegrityError 可能在业务代码已结束后才抛出；统一在这里
    清理 staging 和已移动的 final 目录，随后仍由调用方决定错误码/并发去重语义。
    """
    try:
        with session.begin():
            yield
    except Exception:
        _safe_cleanup(staging_source_dir)
        final_dir = final_dir_getter()
        if final_dir is not None:
            _safe_cleanup(final_dir)
        raise


def _validate_origin_url(origin_url: str) -> None:
    """Spec §5.1 / KI-05：``origin_url`` 仅作为 provenance 保存,系统绝不访问网络。

    只允许 http/https scheme + 非空 host;拒绝 file://、ftp://、data:、javascript:
    等协议,也拒绝空 host 或包含 NUL / CR / LF 的 URL,防止 schema 注入与日志注入。
    """
    if not origin_url.strip():
        return
    if any(ch in origin_url for ch in ("\x00", "\n", "\r")):
        raise IngestError("unsupported_type", "origin_url 不允许包含控制字符")
    try:
        parts = urlsplit(origin_url.strip())
    except ValueError as exc:
        raise IngestError("unsupported_type", "origin_url 格式无效") from exc
    if parts.scheme not in {"http", "https"}:
        raise IngestError(
            "unsupported_type",
            "origin_url 必须使用 http 或 https 协议",
        )
    if not parts.netloc or not parts.hostname:
        raise IngestError("unsupported_type", "origin_url 缺少有效的域名")
