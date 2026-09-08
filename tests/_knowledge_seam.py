"""KBR-01 最高层集成测试 seam。

从 Imported Source 原始字节开始，经过正式 Ingest → 显式 Extraction queue →
Snapshot/Evidence/FTS 提交 → Brief queue → generation → 逐条 validation → repair →
最终持久化的可注入、可断言入口。

Spec Testing Decisions：
- 走正式 Ingest/Job/Worker 边界，不直接向 Snapshot/Evidence/FTS/Brief 表插伪造数据。
- 显式驱动 Extraction queue，等 Source 达到 extracted 且 active Snapshot/Evidence 可见后才进入 Brief。
- 支持注入按角色（generation/repair/validation）返回的确定性模型响应。
- 记录调用角色、顺序、输入摘要和调用次数；不把完整 Source 正文或 Prompt 写入 call_log。
- 返回可断言的 Source/Job/Attempt/current Brief/Evidence/validation report。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from sqlalchemy.orm import Session, sessionmaker

from offerpilot.config import Config
from offerpilot.db import init_database, session_factory_for_data_dir
from offerpilot.knowledge.brief import (
    BRIEF_LANGUAGE,
    BRIEF_REPAIR_PATCH_VERSION,
    BRIEF_SCHEMA_VERSION,
    build_section_coverage_plan,
)
from offerpilot.knowledge.repository import EvidenceRecord, KnowledgeRepository
from offerpilot.knowledge.service import IngestRequest, KnowledgeIngestService
from offerpilot.knowledge.worker import (
    BriefWorker,
    ExtractionWorker,
    JobExecutionResult,
    KnowledgeJobRunner,
)

if TYPE_CHECKING:
    from offerpilot.knowledge.repository import BriefAttemptRecord, SourceBriefRecord, SourceRecord


# KBR-06：空 repair patch。应用后候选不变，复验产生同样问题 → brief_quality_failed，
# 用于驱动「repair 后仍失败」路径而不需要构造具体 fix。repair 成功路径需构造真实 fix patch。
EMPTY_REPAIR_PATCH = json.dumps(
    {"version": BRIEF_REPAIR_PATCH_VERSION, "operations": []}, ensure_ascii=False
)


# ---------------------------------------------------------------------------
# 角色：generation / repair / validation
# ---------------------------------------------------------------------------


def _classify_role(system_text: str) -> str:
    """从模型 system prompt 文本判定调用角色。

    - validation：``Brief Validator``（build_validation_prompt）
    - repair：``Brief Repair Agent``（build_repair_prompt）
    - generation：``Brief Generator``（build_generation_prompt）
    """
    if "Validator" in system_text:
        return "validation"
    if "Repair Agent" in system_text:
        return "repair"
    return "generation"


_VALIDATION_STATEMENT_MARKER = "待校验 statement：\n"


def _validation_statement_digest(user_text: str) -> str:
    """从 validation prompt 提取 statement 前 40 字符作为输入摘要。

    只保留截断摘要，不记录完整 Prompt 或 Evidence 正文（Spec 隐私边界）。
    """
    if _VALIDATION_STATEMENT_MARKER not in user_text:
        return ""
    tail = user_text.split(_VALIDATION_STATEMENT_MARKER, 1)[1]
    statement = tail.split("\n", 1)[0]
    return statement[:40]


@dataclass(frozen=True)
class ModelCallRecord:
    """单次模型调用记录。不含完整 Prompt 或 Source 正文。"""

    order: int
    role: str  # "generation" | "repair" | "validation"
    input_digest: str  # validation: statement 前 40 字符；generation/repair: ""

    def __post_init__(self) -> None:
        # 防御性截断：任何摘要都不得超出隐私边界。
        if len(self.input_digest) > 80:
            object.__setattr__(self, "input_digest", self.input_digest[:80])


@dataclass
class RoleAwareModelClient:
    """按角色路由确定性响应、记录调用角色/顺序/摘要/次数的 stub model_client。

    每个角色维护一个按调用顺序消费的队列：

    - ``str`` → 成功响应，content 为该字符串。
    - ``BaseException`` 实例 → raise（模拟 Provider 错误，由 worker 分类）。

    validation 队列耗尽后默认返回 ``supported``，因为 statement 条数由候选 Brief
    结构决定、测试通常只需控制关键几条；generation/repair 队列耗尽则 raise，
    避免静默重复使用过期响应。
    """

    generation: list[Any] = field(default_factory=list)
    repair: list[Any] = field(default_factory=list)
    validation: list[Any] = field(default_factory=list)
    call_log: list[ModelCallRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        # 复制成内部可变队列，避免外部列表被消费。
        self._queues: dict[str, list[Any]] = {
            "generation": list(self.generation),
            "repair": list(self.repair),
            "validation": list(self.validation),
        }

    def __call__(self, **payload: Any) -> dict[str, Any]:
        messages = payload.get("messages") or []
        system_text = ""
        user_text = ""
        for message in messages:
            role = message.get("role")
            if role == "system":
                system_text = message.get("content") or ""
            elif role == "user":
                user_text = message.get("content") or ""
        call_role = _classify_role(system_text)
        digest = _validation_statement_digest(user_text) if call_role == "validation" else ""
        self.call_log.append(
            ModelCallRecord(
                order=len(self.call_log) + 1,
                role=call_role,
                input_digest=digest,
            )
        )
        queue = self._queues[call_role]
        if queue:
            event = queue.pop(0)
        elif call_role == "validation":
            event = json.dumps(
                {"decision": "supported", "reason": "seam default supported"},
                ensure_ascii=False,
            )
        else:
            raise RuntimeError(f"{call_role} 响应序列已耗尽")
        if isinstance(event, BaseException):
            raise event
        return {
            "choices": [{"message": {"content": str(event)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200},
        }

    def count(self, role: str) -> int:
        return sum(1 for record in self.call_log if record.role == role)

    def role_sequence(self) -> list[str]:
        return [record.role for record in self.call_log]


# ---------------------------------------------------------------------------
# Ingest + Extraction queue
# ---------------------------------------------------------------------------


def ingest_and_extract(
    tmp_path: Path,
    content_bytes: bytes,
    *,
    filename: str = "doc.md",
    title_hint: str = "",
    config: Optional[Config] = None,
    import_method: str = "file",
    origin_url: str = "",
) -> tuple[KnowledgeRepository, "sessionmaker[Session]", int, int]:
    """正式 Ingest → 显式驱动 Extraction queue → 返回 extracted Source。

    返回 ``(repository, session_factory, source_id, active_snapshot_id)``。
    Extraction 通过 ``KnowledgeJobRunner.tick_extraction`` 真实完成，Source 达到
    ``extracted``、active Snapshot/Evidence 可见后才返回；不直接写 Snapshot/Evidence 表。
    """
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    service = KnowledgeIngestService(repository, tmp_path, session_factory, config=config)
    result = service.ingest(
        IngestRequest(
            filename=filename,
            content_bytes=content_bytes,
            title_hint=title_hint,
            import_method=import_method,
            origin_url=origin_url,
        )
    )
    source_id = result.source.id
    # 显式驱动 Extraction queue，直到 Source 达到 extracted；extraction 成功后由
    # callback 触发 brief 入队（与生产 runtime 行为一致）。
    extraction_worker = ExtractionWorker(
        repository,
        tmp_path,
        session_factory,
        on_extraction_succeeded=service.enqueue_or_block_brief,
    )
    runner = KnowledgeJobRunner(repository, extraction_worker)
    runner.tick_extraction(lease_owner="seam-extraction")
    source = repository.get_source(source_id)
    assert source is not None, "ingest 后 Source 丢失"
    assert source.extraction_status == "extracted", (
        f"Extraction queue 未完成：status={source.extraction_status} "
        f"error={source.extraction_error_code}"
    )
    snapshot_id = source.active_snapshot_id or 0
    assert snapshot_id, "extracted Source 缺少 active_snapshot_id"
    return repository, session_factory, source_id, snapshot_id


# ---------------------------------------------------------------------------
# Brief queue
# ---------------------------------------------------------------------------


@dataclass
class BriefRunOutcome:
    """``drive_brief_queue`` 的可断言结果集。"""

    job_results: list[JobExecutionResult]
    source: Optional["SourceRecord"]
    attempt: Optional["BriefAttemptRecord"]
    brief: Optional["SourceBriefRecord"]
    evidence: list[EvidenceRecord]
    validation_report: dict[str, Any]
    call_log: list[ModelCallRecord]


def drive_brief_queue(
    repository: KnowledgeRepository,
    session_factory: "sessionmaker[Session]",
    tmp_path: Path,
    *,
    config: Config,
    model_client: RoleAwareModelClient,
    source_id: int,
) -> BriefRunOutcome:
    """装配 BriefWorker（注入 ``model_client`` 与 no-op sleeper），跑一次 ``tick_brief``。

    返回完整 outcome：Job 结果、Source、最新 Attempt、current Brief、Evidence 列表、
    validation report 与模型调用记录。不绕过任何 Brief worker 门禁。
    """
    worker = BriefWorker(repository, config, model_client=model_client, sleeper=lambda _: None)
    extraction_worker = ExtractionWorker(repository, tmp_path, session_factory)
    runner = KnowledgeJobRunner(repository, extraction_worker, brief_worker=worker)
    job_results = runner.tick_brief(lease_owner="seam-brief")
    return _collect_outcome(repository, source_id, job_results, model_client)


def _collect_outcome(
    repository: KnowledgeRepository,
    source_id: int,
    job_results: list[JobExecutionResult],
    model_client: RoleAwareModelClient,
) -> BriefRunOutcome:
    source = repository.get_source(source_id)
    snapshot_id = source.active_snapshot_id if source else None
    evidence: list[EvidenceRecord] = []
    if snapshot_id:
        evidence = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=200).items
    attempt = repository.find_latest_brief_attempt(source_id)
    brief = repository.get_source_brief(source_id)
    validation_report: dict[str, Any] = {}
    if attempt is not None and attempt.validation_report_json:
        try:
            validation_report = json.loads(attempt.validation_report_json)
        except (json.JSONDecodeError, TypeError):
            validation_report = {}
    return BriefRunOutcome(
        job_results=job_results,
        source=source,
        attempt=attempt,
        brief=brief,
        evidence=evidence,
        validation_report=validation_report,
        call_log=list(model_client.call_log),
    )


# ---------------------------------------------------------------------------
# Brief payload 构造辅助
# ---------------------------------------------------------------------------


def _section_key_of(heading_path: list[str]) -> str:
    path = tuple(heading_path or ())
    if not path:
        return "__document__"
    return " / ".join(path)


def build_supported_brief_json(evidence_items: list[EvidenceRecord]) -> str:
    """根据真实 Evidence 列表构造合法 Brief Schema v2 JSON。

    KBR-04：不再输出 coverage 字段；程序依据实际 citations 派生。为保证 coverage
    门禁通过，每个文本章节至少有一条自身 Evidence 被 key_points 实际引用。
    validation 默认 supported。
    """
    evidence_ids = [item.id for item in evidence_items]
    assert len(evidence_ids) >= 2, "至少需要 2 条 Evidence 构造合法 Brief"
    plan = build_section_coverage_plan(evidence_items)
    section_eids: dict[str, list[str]] = {}
    for item in evidence_items:
        section_eids.setdefault(_section_key_of(item.heading_path), []).append(item.id)
    text_sections = [entry for entry in plan.sections.values() if not entry.must_skip]
    # 每个文本章节选一条代表 Evidence，确保该章节被实际引用而 covered。
    reps = [
        section_eids[entry.section_key][0]
        for entry in text_sections
        if section_eids.get(entry.section_key)
    ]
    first = text_sections[0] if text_sections else plan.sections["__document__"]
    first_eid = section_eids.get(first.section_key, [evidence_ids[0]])[0]
    ov1 = reps[0] if reps else evidence_ids[0]
    ov2 = (
        reps[1]
        if len(reps) > 1
        else (evidence_ids[1] if len(evidence_ids) > 1 else ov1)
    )
    key_points = (
        [{"statement": "该章节提供可引用 Evidence。", "evidence_ids": [rep]} for rep in reps]
        or [{"statement": "要点引用 Evidence。", "evidence_ids": [evidence_ids[0]]}]
    )
    payload: dict[str, Any] = {
        "schema_version": BRIEF_SCHEMA_VERSION,
        "language": BRIEF_LANGUAGE,
        "overview": [
            {"statement": "Source 涉及 OfferPilot 架构。", "evidence_ids": [ov1]},
            {"statement": "Source 给出引用依据。", "evidence_ids": [ov2]},
        ],
        "key_points": key_points,
        "section_guides": [
            {
                "section_key": first.section_key,
                "heading_path": list(first.heading_path),
                "summary": "该章节介绍 OfferPilot 整体方向。",
                "evidence_ids": [first_eid],
            }
        ],
        "limitations": [
            {"statement": "未涉及 Pilot 对话细节。", "evidence_ids": [ov2]},
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def expected_validation_count(payload_json: str) -> int:
    """计算一个 Brief payload 需要的逐条 validation 调用数。"""
    payload = json.loads(payload_json)
    return (
        len(payload["overview"])
        + len(payload["key_points"])
        + len(payload["section_guides"])
        + len(payload["limitations"])
    )
