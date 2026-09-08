"""KI-10 Brief 重建与 Provider 故障语义测试。

覆盖 Spec §10.4 / §11.3 / §11.4：
- 无 AI / 小窗口：Source 保持 extracted，Brief pending + block reason，Evidence 可搜索。
- Provider 重试：transient（429/5xx/timeout）最多 3 次 + 退避；permanent（auth/model）不重试。
- fallback：primary transient 耗尽切 fallback；内容质量失败不切；记录实际成功 Provider。
- 重建失败保留旧 Brief；Provider/Schema/Snapshot 变化标记 outdated；不自动批量重建。
- 重试计数与 next_retry_at 持久化，重启后保留。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.config import AIProviderProfile, Config
from offerpilot.db import init_database, session_factory_for_data_dir
from offerpilot.knowledge.brief import (
    BRIEF_LANGUAGE,
    BRIEF_MIN_CONTEXT_WINDOW,
    BRIEF_PROMPT_VERSION,
    BRIEF_SCHEMA_VERSION,
    build_section_coverage_plan,
    parse_brief_payload,
    validate_brief_against_evidence,
)
from offerpilot.knowledge.brief import BriefSchemaError
from offerpilot.knowledge.repository import (
    BriefAttemptCreateInput,
    EvidenceRecord,
    KnowledgeRepository,
)
from offerpilot.knowledge.service import IngestRequest, KnowledgeIngestService
from offerpilot.knowledge.worker import (
    BRIEF_PROVIDER_MAX_ATTEMPTS,
    BriefWorker,
    ExtractionWorker,
    KnowledgeJobRunner,
)
from offerpilot.models import KnowledgeExtractionSnapshot

_CONTENT = (
    "# 概述\n\n"
    "Source 描述 OfferPilot 与 SQLite 单一事实源决策。\n\n"
    "## 第二段\n\n"
    "Evidence 是引用单位，Evidence 不重叠。\n"
)


def test_text_section_covered_only_when_actually_cited() -> None:
    """KBR-04：含文本 Evidence 的章节只有被实际引用才 covered；模型无法用状态绕过。"""
    evidence = [
        SimpleNamespace(
            id="ev_text",
            heading_path=["正文"],
            kind="text",
            canonical_excerpt="正文内容",
            search_text="正文内容",
        )
    ]
    plan = build_section_coverage_plan(evidence)
    payload = {
        "schema_version": 2,
        "language": "zh-CN",
        "overview": [
            {"statement": "概述一", "evidence_ids": ["ev_text"]},
            {"statement": "概述二", "evidence_ids": ["ev_text"]},
        ],
        "key_points": [{"statement": "要点", "evidence_ids": ["ev_text"]}],
        "section_guides": [
            {
                "section_key": "正文",
                "heading_path": ["正文"],
                "summary": "正文摘要",
                "evidence_ids": ["ev_text"],
            }
        ],
        "limitations": [],
    }
    brief = parse_brief_payload(json.dumps(payload, ensure_ascii=False))
    report = validate_brief_against_evidence(
        brief, evidence_rows=evidence, expected_sections=plan
    )
    assert report.coverage_ok
    # 模型若额外返回 coverage 字段声明 skipped，程序派生仍 covered（不受模型声明影响）。
    payload_with_coverage = dict(payload)
    payload_with_coverage["coverage"] = [
        {"section_key": "正文", "status": "skipped", "skipped_reason": "模型认为不重要"}
    ]
    brief_with_coverage = parse_brief_payload(
        json.dumps(payload_with_coverage, ensure_ascii=False)
    )
    report_with_coverage = validate_brief_against_evidence(
        brief_with_coverage, evidence_rows=evidence, expected_sections=plan
    )
    assert report_with_coverage.coverage_ok


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _valid_payload_dict() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "language": "zh-CN",
        "overview": [
            {"statement": "Source 描述 OfferPilot 架构。", "evidence_ids": []},
            {"statement": "Source 给出 SQLite SSOT 决策。", "evidence_ids": []},
        ],
        "key_points": [
            {"statement": "Evidence 是引用单位。", "evidence_ids": []},
        ],
        "section_guides": [
            {
                "section_key": "概述",
                "heading_path": ["概述"],
                "summary": "该章节介绍 OfferPilot 整体方向。",
                "evidence_ids": [],
            },
        ],
        "limitations": [
            {"statement": "未涉及 Pilot 对话细节。", "evidence_ids": []},
        ],
    }


def _supported_json() -> str:
    return json.dumps({"decision": "supported", "reason": "ok"}, ensure_ascii=False)


def _build_payload_for_evidence(items: list[EvidenceRecord]) -> dict[str, Any]:
    """根据真实 Evidence 列表构造合法 v2 Brief payload（每个文本章节被实际引用）。"""
    evidence_ids = [item.id for item in items]
    assert len(evidence_ids) >= 2
    plan = build_section_coverage_plan(items)
    section_eids: dict[str, list[str]] = {}
    for item in items:
        path = tuple(item.heading_path or ())
        key = "__document__" if not path else " / ".join(path)
        section_eids.setdefault(key, []).append(item.id)
    text_sections = [entry for entry in plan.sections.values() if not entry.must_skip]
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
    payload = _valid_payload_dict()
    payload["overview"][0]["evidence_ids"] = [ov1]
    payload["overview"][1]["evidence_ids"] = [ov2]
    payload["key_points"][0]["evidence_ids"] = [ov1, ov2]
    payload["section_guides"][0]["evidence_ids"] = [first_eid]
    payload["section_guides"][0]["section_key"] = first.section_key
    payload["section_guides"][0]["heading_path"] = list(first.heading_path)
    payload["limitations"][0]["evidence_ids"] = [ov2]
    cited = {ov1, ov2, first_eid}
    extra = [rep for rep in reps if rep not in cited]
    if extra:
        payload["key_points"][0]["evidence_ids"].extend(extra)
    return payload


def test_duplicate_section_guides_are_rejected() -> None:
    """Spec §10.1：同一实质章节最多生成一条 section guide。"""
    payload = _valid_payload_dict()
    for block_name in ("overview", "key_points", "section_guides", "limitations"):
        for item in payload[block_name]:
            item["evidence_ids"] = ["ev"]
    payload["section_guides"].append(dict(payload["section_guides"][0]))
    with pytest.raises(BriefSchemaError, match="section_guides section_key 重复"):
        parse_brief_payload(json.dumps(payload, ensure_ascii=False))


def _primary_config(
    *, context_window: int = BRIEF_MIN_CONTEXT_WINDOW, api_key: str = "sk-primary"
) -> Config:
    provider = AIProviderProfile(
        id="primary",
        label="Primary",
        provider="openai",
        api_key=api_key,
        base_url="https://primary.example.com",
        model="gpt-primary",
        enabled=True,
        context_window=context_window,
        max_output_tokens=4096,
    )
    return Config(providers=[provider], active_provider_id="primary", api_key=api_key)


def _primary_fallback_config() -> Config:
    primary = AIProviderProfile(
        id="primary",
        label="Primary",
        provider="openai",
        api_key="sk-primary",
        base_url="https://primary.example.com",
        model="gpt-primary",
        enabled=True,
        context_window=BRIEF_MIN_CONTEXT_WINDOW,
        max_output_tokens=4096,
    )
    fallback = AIProviderProfile(
        id="fallback",
        label="Fallback",
        provider="openai",
        api_key="sk-fallback",
        base_url="https://fallback.example.com",
        model="gpt-fallback",
        enabled=True,
        context_window=BRIEF_MIN_CONTEXT_WINDOW,
        max_output_tokens=4096,
    )
    return Config(
        providers=[primary, fallback],
        active_provider_id="primary",
        fallback_provider_id="fallback",
    )


def _setup(
    tmp_path: Path, *, config: Config | None = None
) -> tuple[KnowledgeRepository, sessionmaker[Session], int, int]:
    """走完一次完整 ingest + Extraction queue，得到一个 extracted Source。

    KBR-01：ingest 只入队 extract job；必须显式驱动 Extraction queue 才能让
    Source 达到 extracted、active Snapshot/Evidence 可见。``config`` 决定
    extraction 成功后 brief 是否入队：
    - 有合格 Provider → brief job 已入队。
    - 无合格 Provider → brief_status=pending + block_reason，无 job。
    """
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)
    service = KnowledgeIngestService(repository, tmp_path, session_factory, config=config)
    result = service.ingest(
        IngestRequest(
            filename="doc.md",
            content_bytes=_CONTENT.encode("utf-8"),
            title_hint="测试",
        )
    )
    source_id = result.source.id
    # 显式驱动 Extraction queue：extraction 成功后由 callback 触发 brief 入队。
    extraction_worker = ExtractionWorker(
        repository,
        tmp_path,
        session_factory,
        on_extraction_succeeded=service.enqueue_or_block_brief,
    )
    KnowledgeJobRunner(repository, extraction_worker).tick_extraction(lease_owner="test")
    source = repository.get_source(source_id)
    assert source is not None
    return (
        repository,
        session_factory,
        source_id,
        source.active_snapshot_id or 0,
    )


def _sequence_client(behaviors: list[Any]) -> tuple[Any, list[dict[str, Any]]]:
    """按调用顺序消费 behaviors 的 stub model_client。

    每个元素：
    - ``BaseException`` 实例 → raise（模拟 Provider 错误，由 ``_classify_model_error`` 分类）。
    - ``str`` → 成功响应，content 为该字符串。

    ``call_log`` 记录每次调用的 model 名（区分 primary/fallback）与是否为 validation。
    耗尽后再调用会抛 ``RuntimeError``，让测试显式提供足够 behaviors。
    """
    queue = list(behaviors)
    call_log: list[dict[str, Any]] = []

    def _client(**payload: Any) -> dict[str, Any]:
        model = str(payload.get("model") or "")
        has_key = bool(payload.get("api_key"))
        messages = payload.get("messages") or []
        system_text = ""
        for message in messages:
            if message.get("role") == "system":
                system_text = message.get("content") or ""
                break
        call_log.append(
            {
                "model": model,
                "has_key": has_key,
                "is_validation": "Validator" in system_text,
            }
        )
        if not queue:
            raise RuntimeError("sequence exhausted")
        event = queue.pop(0)
        if isinstance(event, BaseException):
            raise event
        return {
            "choices": [{"message": {"content": str(event)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200},
        }

    return _client, call_log


def _count_validations(payload: dict[str, Any]) -> int:
    """计算 Brief payload 需要的 validation 调用数（每条 statement 一次）。"""
    return (
        len(payload["overview"])
        + len(payload["key_points"])
        + len(payload["section_guides"])
        + len(payload["limitations"])
    )


def _tick_brief(
    repository: KnowledgeRepository,
    session_factory: sessionmaker[Session],
    config: Config,
    behaviors: list[Any],
    *,
    tmp_path: Path,
) -> tuple[Any, list[dict[str, Any]]]:
    """装配 BriefWorker（注入 no-op sleeper）并跑一次 tick_brief。"""
    model_client, call_log = _sequence_client(behaviors)
    worker = BriefWorker(repository, config, model_client=model_client, sleeper=lambda _d: None)
    runner = KnowledgeJobRunner(
        repository,
        ExtractionWorker(repository, tmp_path, session_factory),
        brief_worker=worker,
    )
    results = runner.tick_brief(lease_owner="test")
    return results, call_log


# ---------------------------------------------------------------------------
# Spec §11.2 无 AI / 小窗口
# ---------------------------------------------------------------------------


def test_no_provider_source_stays_extracted_evidence_searchable(tmp_path: Path) -> None:
    """Spec §11.2：无满足条件的 Provider 时 Source 保持 extracted，Evidence 可搜索。"""
    config = Config()  # 无 api_key，无 providers
    repository, _, source_id, _ = _setup(tmp_path, config=config)
    source = repository.get_source(source_id)
    assert source is not None
    assert source.extraction_status == "extracted"
    assert source.brief_status == "pending"
    assert source.brief_block_reason == "provider_unavailable"
    # Evidence FTS 仍可搜索。
    hits = repository.search_evidence("OfferPilot", limit=5)
    assert hits, "无 Provider 时 Evidence 必须仍可搜索"


def test_small_context_window_blocks_brief(tmp_path: Path) -> None:
    """Spec §11.2 / §4.2：context_window < 96K 时 block。"""
    config = _primary_config(context_window=32_000)
    repository, _, source_id, _ = _setup(tmp_path, config=config)
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "pending"
    assert source.brief_block_reason == "provider_context_too_small"


def test_small_primary_but_qualified_fallback_enqueues_brief(
    tmp_path: Path,
) -> None:
    """Spec §11.3：active 不满足 96K 但 fallback 满足时不 block，brief job 入队。"""
    primary = AIProviderProfile(
        id="primary",
        label="Primary",
        provider="openai",
        api_key="sk-primary",
        base_url="https://primary.example.com",
        model="gpt-primary",
        enabled=True,
        context_window=32_000,
        max_output_tokens=4096,
    )
    fallback = AIProviderProfile(
        id="fallback",
        label="Fallback",
        provider="openai",
        api_key="sk-fallback",
        base_url="https://fallback.example.com",
        model="gpt-fallback",
        enabled=True,
        context_window=BRIEF_MIN_CONTEXT_WINDOW,
        max_output_tokens=4096,
    )
    config = Config(
        providers=[primary, fallback],
        active_provider_id="primary",
        fallback_provider_id="fallback",
    )
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_block_reason == ""
    # brief job 已入队；tick 后用 fallback 生成成功。
    evidence_page = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50)
    payload = _build_payload_for_evidence(evidence_page.items)
    behaviors = [json.dumps(payload, ensure_ascii=False)] + [_supported_json()] * _count_validations(payload)
    results, call_log = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results
    assert results[0].status == "succeeded", results[0].error_message
    # 实际成功 Provider 是 fallback。
    attempt = repository.find_latest_brief_attempt(source_id)
    assert attempt is not None
    assert attempt.actual_provider_id == "fallback"


# ---------------------------------------------------------------------------
# Spec §11.4 Provider 重试
# ---------------------------------------------------------------------------


def test_transient_429_retries_then_succeeds(tmp_path: Path) -> None:
    """Spec §11.4：429 限流重试，最多 3 次；第 3 次成功。"""
    config = _primary_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    evidence_page = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50)
    payload = _build_payload_for_evidence(evidence_page.items)
    generation_json = json.dumps(payload, ensure_ascii=False)
    behaviors = [
        RuntimeError("429 Too Many Requests"),
        RuntimeError("429 Too Many Requests"),
        generation_json,
    ] + [_supported_json()] * _count_validations(payload)
    results, call_log = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "succeeded", results[0].error_message
    # 2 次 transient 失败 + 1 次成功 = 3 次 generation 调用 + N validation。
    generation_calls = [c for c in call_log if not c["is_validation"]]
    assert len(generation_calls) == 3
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "ready"
    attempt = repository.find_latest_brief_attempt(source_id)
    assert attempt is not None
    assert attempt.provider_retry_count == 2


def test_transient_exhausts_max_attempts_per_provider(tmp_path: Path) -> None:
    """Spec §11.4：transient 超过 MAX_ATTEMPTS 后 Attempt failed。"""
    config = _primary_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    behaviors = [RuntimeError("500 Internal Server Error")] * (
        BRIEF_PROVIDER_MAX_ATTEMPTS + 1
    )
    results, call_log = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "failed"
    assert results[0].error_code == "provider_transient_error"
    generation_calls = [c for c in call_log if not c["is_validation"]]
    assert len(generation_calls) == BRIEF_PROVIDER_MAX_ATTEMPTS
    attempt = repository.find_latest_brief_attempt(source_id)
    assert attempt is not None
    assert attempt.error_code == "provider_transient_error"
    assert attempt.provider_retry_count == BRIEF_PROVIDER_MAX_ATTEMPTS - 1


def test_auth_error_does_not_retry(tmp_path: Path) -> None:
    """Spec §11.4：鉴权失败（401）不重试，直接 Attempt failed。"""
    config = _primary_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    behaviors = [RuntimeError("401 Unauthorized")]
    results, call_log = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "failed"
    assert results[0].error_code == "provider_auth_invalid"
    generation_calls = [c for c in call_log if not c["is_validation"]]
    assert len(generation_calls) == 1  # 只调用一次，不重试
    attempt = repository.find_latest_brief_attempt(source_id)
    assert attempt is not None
    assert attempt.provider_retry_count == 0


def test_model_not_found_does_not_retry(tmp_path: Path) -> None:
    """Spec §11.4：模型不存在（404）不重试。"""
    config = _primary_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    behaviors = [RuntimeError("404 Model not found")]
    results, _ = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "failed"
    assert results[0].error_code == "provider_model_unavailable"


def test_context_window_error_does_not_retry_or_fallback(tmp_path: Path) -> None:
    """Spec §11.3/§11.4：上下文超限是确定性错误，不重试、不切 fallback。"""
    config = _primary_fallback_config()
    repository, session_factory, source_id, _ = _setup(tmp_path, config=config)
    behaviors = [RuntimeError("maximum context window exceeded")]
    results, call_log = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "failed"
    assert results[0].error_code == "provider_model_unavailable"
    generation_calls = [item for item in call_log if not item["is_validation"]]
    assert len(generation_calls) == 1
    assert "gpt-primary" in generation_calls[0]["model"]


def test_retry_delay_prefers_retry_after_header() -> None:
    """Spec §11.4：优先 Retry-After，否则 2s/10s 退避。"""
    from offerpilot.knowledge.worker import (
        BRIEF_RETRY_BACKOFF_SECONDS,
        BRIEF_RETRY_MAX_DELAY_SECONDS,
        _extract_retry_after_seconds,
        _retry_delay_seconds,
    )

    # 异常携带 retry_after 数值。
    exc_with_after = RuntimeError("429")
    exc_with_after.retry_after = 5  # type: ignore[attr-defined]
    assert _extract_retry_after_seconds(exc_with_after) == 5.0
    assert _retry_delay_seconds(exc_with_after, 1) == 5.0

    # Retry-After 超过上限被截断。
    exc_big = RuntimeError("429")
    exc_big.retry_after = 999  # type: ignore[attr-defined]
    assert _retry_delay_seconds(exc_big, 1) == BRIEF_RETRY_MAX_DELAY_SECONDS

    # 无 Retry-After：第 1 次重试用 backoff[0]，第 2 次用 backoff[1]。
    plain = RuntimeError("500 Server Error")
    d1 = _retry_delay_seconds(plain, 1)
    d2 = _retry_delay_seconds(plain, 2)
    assert BRIEF_RETRY_BACKOFF_SECONDS[0] <= d1 < BRIEF_RETRY_BACKOFF_SECONDS[0] + 1
    assert BRIEF_RETRY_BACKOFF_SECONDS[1] <= d2 < BRIEF_RETRY_BACKOFF_SECONDS[1] + 1


# ---------------------------------------------------------------------------
# Spec §11.3 fallback
# ---------------------------------------------------------------------------


def test_fallback_used_when_primary_transient_exhausted(tmp_path: Path) -> None:
    """Spec §11.3：primary transient 耗尽 → fallback 成功；记录 actual_provider。"""
    config = _primary_fallback_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    evidence_page = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50)
    payload = _build_payload_for_evidence(evidence_page.items)
    generation_json = json.dumps(payload, ensure_ascii=False)
    behaviors = (
        [RuntimeError("429 Rate Limit")] * BRIEF_PROVIDER_MAX_ATTEMPTS
        + [generation_json]
        + [_supported_json()] * _count_validations(payload)
    )
    results, call_log = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "succeeded", results[0].error_message
    generation_calls = [c for c in call_log if not c["is_validation"]]
    # primary 3 次 + fallback 1 次（成功）
    assert len(generation_calls) == BRIEF_PROVIDER_MAX_ATTEMPTS + 1
    primary_calls = [c for c in generation_calls if "gpt-primary" in c["model"]]
    fallback_calls = [c for c in generation_calls if "gpt-fallback" in c["model"]]
    assert len(primary_calls) == BRIEF_PROVIDER_MAX_ATTEMPTS
    assert len(fallback_calls) == 1
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "ready"
    attempt = repository.find_latest_brief_attempt(source_id)
    assert attempt is not None
    assert attempt.actual_provider_id == "fallback"
    assert attempt.actual_provider_model == "gpt-fallback"
    assert attempt.fallback_provider_id == "fallback"


def test_fallback_also_fails_marks_attempt_failed(tmp_path: Path) -> None:
    """Spec §11.3：primary 与 fallback 都 transient 耗尽 → Attempt failed。"""
    config = _primary_fallback_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    behaviors = [RuntimeError("503 Service Unavailable")] * (
        BRIEF_PROVIDER_MAX_ATTEMPTS * 2 + 1
    )
    results, call_log = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "failed"
    assert results[0].error_code == "provider_transient_error"
    generation_calls = [c for c in call_log if not c["is_validation"]]
    assert len(generation_calls) == BRIEF_PROVIDER_MAX_ATTEMPTS * 2
    attempt = repository.find_latest_brief_attempt(source_id)
    assert attempt is not None
    # primary 用 2 + fallback 用 2 = 4 次重试持久化。
    assert attempt.provider_retry_count == BRIEF_PROVIDER_MAX_ATTEMPTS * 2 - 2


def test_content_quality_failure_does_not_switch_provider(tmp_path: Path) -> None:
    """Spec §11.3：内容质量失败（schema/citation）不切换 Provider。"""
    config = _primary_fallback_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    # 第 1 次 generation 返回非法 JSON（schema_invalid → repair）；
    # 第 2 次 generation（repair）返回 schema 合法但 citation 全部无效的 payload，
    # 走 citation 校验失败 → brief_quality_failed（不切 provider）。
    # KBR-05：用真实 Evidence 构造 schema 合法 payload，再把所有 evidence_ids 替换为
    # 不存在的 id——schema 解析通过（evidence_ids 非空），但每条 citation 都 invalid，
    # 故无有效 block、不发起 Validator，确定命中 brief_quality_failed 路径（而非靠 schema
    # 失败侥幸通过）。
    evidence_page = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50)
    bad_citation_payload = _build_payload_for_evidence(evidence_page.items)
    nonexistent_eid = "ev_NONEXISTENT"
    for block_name in ("overview", "key_points", "section_guides", "limitations"):
        for item in bad_citation_payload[block_name]:
            item["evidence_ids"] = [nonexistent_eid]
    behaviors = [
        "not a json object {{{",
        json.dumps(bad_citation_payload, ensure_ascii=False),
    ]
    results, call_log = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "failed"
    assert results[0].error_code in (
        "brief_schema_invalid",
        "brief_quality_failed",
    )
    generation_calls = [c for c in call_log if not c["is_validation"]]
    # 全部调用都是 primary，没有 fallback。
    assert all("gpt-primary" in c["model"] for c in generation_calls)
    assert not any("gpt-fallback" in c["model"] for c in generation_calls)


def test_auth_failure_does_not_switch_to_fallback(tmp_path: Path) -> None:
    """Spec §11.3：primary 鉴权失败不切 fallback（permanent）。"""
    config = _primary_fallback_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    behaviors = [RuntimeError("403 Forbidden")]
    results, call_log = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "failed"
    assert results[0].error_code == "provider_auth_invalid"
    generation_calls = [c for c in call_log if not c["is_validation"]]
    assert all("gpt-primary" in c["model"] for c in generation_calls)


# ---------------------------------------------------------------------------
# Spec §10.4 重建失败保留旧 Brief + outdated
# ---------------------------------------------------------------------------


def _seed_ready_brief(
    repository: KnowledgeRepository,
    config: Config,
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    source_id: int,
    snapshot_id: int,
) -> dict[str, Any]:
    """先跑一次成功 generation，建立 ready Brief。"""
    evidence_page = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50)
    payload = _build_payload_for_evidence(evidence_page.items)
    behaviors = [json.dumps(payload, ensure_ascii=False)] + [
        _supported_json()
    ] * _count_validations(payload)
    results, _ = _tick_brief(repository, session_factory, config, behaviors, tmp_path=tmp_path)
    assert results[0].status == "succeeded", results[0].error_message
    return payload


def _create_alt_snapshot(
    session_factory: sessionmaker[Session], source_id: int, base_snapshot_id: int
) -> int:
    """创建第二个真实 Snapshot 行模拟 extractor 升级（不同 version/digest）。

    KBR-01：db trigger 强制 active_snapshot_id 指向真实存在的 Snapshot，
    不能再用 ``snapshot_id + 9999`` 模拟升级。
    """
    with session_factory() as session:
        base = session.get(KnowledgeExtractionSnapshot, base_snapshot_id)
        assert base is not None
        alt = KnowledgeExtractionSnapshot(
            source_id=source_id,
            extractor_version=base.extractor_version + "-alt",
            parser_version=base.parser_version,
            normalization_version=base.normalization_version,
            tokenizer_version=base.tokenizer_version,
            encoding=base.encoding,
            detection_method=base.detection_method,
            canonical_text=base.canonical_text,
            structure_manifest=base.structure_manifest,
            digest=base.digest + "-alt",
            token_count=base.token_count,
            char_count=base.char_count,
        )
        session.add(alt)
        session.flush()
        alt_id = alt.id
        session.commit()
    return alt_id


def test_rebuild_failure_preserves_existing_brief(tmp_path: Path) -> None:
    """Spec §10.4：有旧 ready Brief 时，重建失败保持 ready + 最近 Attempt 错误。"""
    config = _primary_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    _seed_ready_brief(
        repository, config, tmp_path, session_factory, source_id, snapshot_id
    )
    existing_brief = repository.get_source_brief(source_id)
    assert existing_brief is not None

    # 重建：创建新 brief job，然后 generation 鉴权失败。
    repository.create_job(
        _job_brief_input(source_id, snapshot_id, stage="brief_rebuild_pending")
    )
    behaviors = [RuntimeError("401 Unauthorized")]
    results, _ = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "failed"
    source = repository.get_source(source_id)
    assert source is not None
    # 旧 Brief 保留：brief_status 保持 ready，而非 failed。
    assert source.brief_status == "ready"
    assert source.brief_error_code == "provider_auth_invalid"
    preserved = repository.get_source_brief(source_id)
    assert preserved is not None
    assert preserved.payload_json == existing_brief.payload_json
    # 最近 Attempt 记录错误。
    latest = repository.find_latest_brief_attempt(source_id)
    assert latest is not None
    assert latest.status == "failed"
    assert latest.error_code == "provider_auth_invalid"


def test_first_failure_without_existing_brief_marks_failed(tmp_path: Path) -> None:
    """首次失败（无旧 Brief）时 Source brief_status=failed。"""
    config = _primary_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    behaviors = [RuntimeError("401 Unauthorized")]
    results, _ = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "failed"
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "failed"


def _job_brief_input(source_id: int, snapshot_id: int, *, stage: str = "brief_pending"):
    from offerpilot.knowledge.repository import JobCreateInput

    return JobCreateInput(
        kind="brief",
        queue="brief",
        source_id=source_id,
        snapshot_id=snapshot_id,
        stage=stage,
    )


def test_brief_marked_outdated_on_provider_change(tmp_path: Path) -> None:
    """Spec §10.4：Provider 变化后 brief.outdated=True，不自动重建。"""
    config = _primary_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    _seed_ready_brief(
        repository, config, tmp_path, session_factory, source_id, snapshot_id
    )
    # 用户在设置中切换 active provider（model 变化）。
    new_config = Config(
        providers=[
            AIProviderProfile(
                id="primary",
                label="Primary",
                provider="openai",
                api_key="sk-primary",
                base_url="https://primary.example.com",
                model="gpt-primary-v2",  # model 变化
                enabled=True,
                context_window=BRIEF_MIN_CONTEXT_WINDOW,
                max_output_tokens=4096,
            )
        ],
        active_provider_id="primary",
    )
    service = KnowledgeIngestService(
        repository, tmp_path, session_factory, config=new_config
    )
    service.refresh_brief_outdated(source_id)
    brief = repository.get_source_brief(source_id)
    assert brief is not None
    assert brief.outdated is True
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "outdated"
    # 没有自动创建新的 brief job。
    pending_jobs = repository.list_pending_jobs("brief")
    assert not any(job.source_id == source_id for job in pending_jobs)


def test_brief_marked_outdated_on_snapshot_change(tmp_path: Path) -> None:
    """Spec §10.4：active_snapshot_id 变化（extractor 升级）后 outdated。"""
    config = _primary_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    _seed_ready_brief(
        repository, config, tmp_path, session_factory, source_id, snapshot_id
    )
    # 模拟 extractor 升级：创建第二个真实 Snapshot 并切换 active_snapshot_id。
    alt_snapshot_id = _create_alt_snapshot(session_factory, source_id, snapshot_id)
    repository.update_source_state(source_id, active_snapshot_id=alt_snapshot_id)
    service = KnowledgeIngestService(
        repository, tmp_path, session_factory, config=config
    )
    service.refresh_brief_outdated(source_id)
    brief = repository.get_source_brief(source_id)
    assert brief is not None
    assert brief.outdated is True
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "outdated"


def test_rebuild_brief_marks_source_pending(tmp_path: Path) -> None:
    """用户显式 rebuild 入队后 Source 立即变为 pending，供前端展示排队状态。"""
    config = _primary_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    _seed_ready_brief(
        repository, config, tmp_path, session_factory, source_id, snapshot_id
    )
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "ready"

    service = KnowledgeIngestService(
        repository, tmp_path, session_factory, config=config
    )
    updated, message = service.rebuild_brief(source_id)
    assert message == "brief_rebuild_queued"
    assert updated is not None
    assert updated.brief_status == "pending"
    assert updated.brief_block_reason == ""
    assert updated.brief_error_code == ""
    # Job 已入队。
    pending_jobs = repository.list_pending_jobs("brief")
    assert any(job.source_id == source_id for job in pending_jobs)
    # 旧 Brief 仍可读。
    assert repository.get_source_brief(source_id) is not None
    # GET 路径的 outdated 检测不得覆盖 pending。
    service.refresh_brief_outdated(source_id)
    still = repository.get_source(source_id)
    assert still is not None
    assert still.brief_status == "pending"
    assert snapshot_id > 0


def test_outdated_cleared_after_successful_rebuild(tmp_path: Path) -> None:
    """Spec §10.4：rebuild 成功后新 Brief 匹配当前配置，outdated 清除。"""
    config = _primary_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    _seed_ready_brief(
        repository, config, tmp_path, session_factory, source_id, snapshot_id
    )
    # 标记 outdated。
    repository.mark_brief_outdated_if_stale(
        source_id,
        provider_id="stale-provider",
        provider_model="stale-model",
        prompt_version=BRIEF_PROMPT_VERSION,
        schema_version=BRIEF_SCHEMA_VERSION,
        snapshot_id=snapshot_id,
    )
    brief = repository.get_source_brief(source_id)
    assert brief is not None and brief.outdated

    # 用户显式 rebuild：新 brief job + 成功 generation。
    repository.create_job(
        _job_brief_input(source_id, snapshot_id, stage="brief_rebuild_pending")
    )
    evidence_page = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50)
    payload = _build_payload_for_evidence(evidence_page.items)
    behaviors = [json.dumps(payload, ensure_ascii=False)] + [
        _supported_json()
    ] * _count_validations(payload)
    results, _ = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "succeeded"
    # rebuild 后 refresh → outdated 与当前配置一致 → 清除。
    service = KnowledgeIngestService(
        repository, tmp_path, session_factory, config=config
    )
    service.refresh_brief_outdated(source_id)
    brief = repository.get_source_brief(source_id)
    assert brief is not None
    assert brief.outdated is False


# ---------------------------------------------------------------------------
# Spec §11.4 重启保留重试进度
# ---------------------------------------------------------------------------


def test_provider_retry_count_and_next_retry_persisted(tmp_path: Path) -> None:
    """Spec §11.4：transient 失败后 Attempt 持久化 retry_count 与 next_retry_at。"""
    config = _primary_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    behaviors = [RuntimeError("429 Rate Limit")] * BRIEF_PROVIDER_MAX_ATTEMPTS
    results, _ = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "failed"
    attempt = repository.find_latest_brief_attempt(source_id)
    assert attempt is not None
    assert attempt.provider_retry_count == BRIEF_PROVIDER_MAX_ATTEMPTS - 1
    assert attempt.next_retry_at is not None

    # 模拟"重启"：用新 repository/session 读取同一数据库。
    init_database(tmp_path / "data.db")
    new_session_factory = session_factory_for_data_dir(tmp_path)
    new_repository = KnowledgeRepository(new_session_factory)
    refreshed = new_repository.find_latest_brief_attempt(source_id)
    assert refreshed is not None
    assert refreshed.provider_retry_count == BRIEF_PROVIDER_MAX_ATTEMPTS - 1
    assert refreshed.next_retry_at is not None
    assert refreshed.error_code == "provider_transient_error"


def test_manual_rebuild_creates_new_attempt_with_fresh_retry_budget(
    tmp_path: Path,
) -> None:
    """Spec §11.4：用户重建创建新 Attempt 和独立重试预算。"""
    config = _primary_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    _seed_ready_brief(
        repository, config, tmp_path, session_factory, source_id, snapshot_id
    )
    # 第一次重建失败（transient 耗尽）。
    repository.create_job(
        _job_brief_input(source_id, snapshot_id, stage="brief_rebuild_pending")
    )
    behaviors = [RuntimeError("500 Server Error")] * BRIEF_PROVIDER_MAX_ATTEMPTS
    results, _ = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "failed"
    failed_attempt = repository.find_latest_brief_attempt(source_id)
    assert failed_attempt is not None
    assert failed_attempt.provider_retry_count > 0

    # 第二次重建：新 Attempt，retry_count 从 0 开始。
    repository.create_job(
        _job_brief_input(source_id, snapshot_id, stage="brief_rebuild_pending")
    )
    evidence_page = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50)
    payload = _build_payload_for_evidence(evidence_page.items)
    behaviors = [json.dumps(payload, ensure_ascii=False)] + [
        _supported_json()
    ] * _count_validations(payload)
    results, _ = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "succeeded"
    new_attempt = repository.find_latest_brief_attempt(source_id)
    assert new_attempt is not None
    assert new_attempt.id != failed_attempt.id
    assert new_attempt.provider_retry_count == 0


# ---------------------------------------------------------------------------
# Spec §11.1 / §18 Attempt 不保存敏感数据
# ---------------------------------------------------------------------------


def test_attempt_does_not_expose_api_key_in_payload(tmp_path: Path) -> None:
    """Spec §18：Attempt 不保存 API Key。API payload 不应含 api_key。"""
    config = _primary_config(api_key="sk-super-secret")
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    evidence_page = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50)
    payload = _build_payload_for_evidence(evidence_page.items)
    behaviors = [json.dumps(payload, ensure_ascii=False)] + [
        _supported_json()
    ] * _count_validations(payload)
    results, _ = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "succeeded"
    attempt = repository.find_latest_brief_attempt(source_id)
    assert attempt is not None
    # Attempt 行的 JSON 字段不应含 api_key 明文。
    assert "sk-super-secret" not in attempt.candidate_payload_json
    assert "sk-super-secret" not in attempt.validation_report_json
    assert "sk-super-secret" not in attempt.error_message


def test_attempt_fixed_with_fallback_candidate(tmp_path: Path) -> None:
    """Spec §11.1：Attempt 创建时固定 fallback 候选。"""
    config = _primary_fallback_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    # 手动创建 Attempt 检查 fallback 固定。
    attempt, _, _ = repository.create_brief_attempt(
        BriefAttemptCreateInput(
            source_id=source_id,
            snapshot_id=snapshot_id,
            provider_id="primary",
            provider_model="gpt-primary",
            provider_base_url="https://primary.example.com",
            context_window=BRIEF_MIN_CONTEXT_WINDOW,
            max_output_tokens=4096,
            prompt_version=BRIEF_PROMPT_VERSION,
            schema_version=BRIEF_SCHEMA_VERSION,
            language=BRIEF_LANGUAGE,
            fallback_provider_id="fallback",
            fallback_provider_model="gpt-fallback",
        )
    )
    assert attempt.fallback_provider_id == "fallback"
    assert attempt.fallback_provider_model == "gpt-fallback"


def test_validator_transient_uses_configured_fallback(tmp_path: Path) -> None:
    """Validator 的基础设施失败可切 fallback，内容质量失败不走该路径。"""

    config = _primary_fallback_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    evidence_page = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50)
    payload = _build_payload_for_evidence(evidence_page.items)
    generation_json = json.dumps(payload, ensure_ascii=False)
    validation_count = _count_validations(payload)
    behaviors = (
        [generation_json]
        # 首个 Validator 在 primary 上 3 次 transient，随后 fallback 成功。
        + [RuntimeError("503 Service Unavailable")] * BRIEF_PROVIDER_MAX_ATTEMPTS
        + [_supported_json()] * validation_count
    )
    results, call_log = _tick_brief(
        repository, session_factory, config, behaviors, tmp_path=tmp_path
    )
    assert results[0].status == "succeeded", results[0].error_message
    validation_calls = [call for call in call_log if call["is_validation"]]
    assert sum("gpt-primary" in call["model"] for call in validation_calls) == (
        BRIEF_PROVIDER_MAX_ATTEMPTS
    )
    assert any("gpt-fallback" in call["model"] for call in validation_calls)


# ---------------------------------------------------------------------------
# Spec §10.4 取消保留旧 Brief
# ---------------------------------------------------------------------------


def test_cancel_during_rebuild_preserves_existing_brief(tmp_path: Path) -> None:
    """Spec §10.4 / §12：重建取消（pending → canceled）保留旧 Brief，不调用模型。"""
    config = _primary_config()
    repository, session_factory, source_id, snapshot_id = _setup(
        tmp_path, config=config
    )
    _seed_ready_brief(
        repository, config, tmp_path, session_factory, source_id, snapshot_id
    )
    existing = repository.get_source_brief(source_id)
    assert existing is not None
    existing_payload = existing.payload_json

    from offerpilot.knowledge.repository import JobCreateInput

    job = repository.create_job(
        JobCreateInput(
            kind="brief",
            queue="brief",
            source_id=source_id,
            snapshot_id=snapshot_id,
            stage="brief_rebuild_pending",
        )
    )
    repository.mark_canceled(job.id)
    # canceled job 不被 claim_next_job 消费，tick_brief 无模型调用。
    results, call_log = _tick_brief(
        repository, session_factory, config, [], tmp_path=tmp_path
    )
    assert results == []
    assert call_log == []
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "ready"  # 旧 Brief 保留
    preserved = repository.get_source_brief(source_id)
    assert preserved is not None
    assert preserved.payload_json == existing_payload


# ---------------------------------------------------------------------------
# Spec §11.1 / §14 / §18 API payload 字段
# ---------------------------------------------------------------------------


def _attempt_namespace(**overrides: Any) -> SimpleNamespace:
    """构造 _knowledge_brief_attempt_payload 所需的最小 Attempt 视图。"""
    base: dict[str, Any] = dict(
        id=1,
        source_id=1,
        snapshot_id=1,
        status="succeeded",
        provider_id="primary",
        provider_model="gpt-primary",
        context_window=BRIEF_MIN_CONTEXT_WINDOW,
        max_output_tokens=4096,
        prompt_version=BRIEF_PROMPT_VERSION,
        schema_version=BRIEF_SCHEMA_VERSION,
        language=BRIEF_LANGUAGE,
        candidate_payload_json="",
        validation_report_json="{}",
        error_code="",
        error_message="",
        repair_count=0,
        fallback_provider_id="fallback",
        fallback_provider_model="gpt-fallback",
        actual_provider_id="fallback",
        actual_provider_model="gpt-fallback",
        provider_retry_count=2,
        next_retry_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        token_input_count=120,
        token_output_count=80,
        latency_ms=1500,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_api_attempt_payload_exposes_ki10_fields() -> None:
    """Spec §14：处理记录展示实际 Provider、token、耗时、重试。"""
    from offerpilot.api import _knowledge_brief_attempt_payload

    payload = _knowledge_brief_attempt_payload(_attempt_namespace())
    assert payload is not None
    assert payload["actual_provider_id"] == "fallback"
    assert payload["actual_provider_model"] == "gpt-fallback"
    assert payload["fallback_provider_id"] == "fallback"
    assert payload["fallback_provider_model"] == "gpt-fallback"
    assert payload["provider_retry_count"] == 2
    assert payload["next_retry_at"] is not None
    assert payload["token_input_count"] == 120
    assert payload["latency_ms"] == 1500


def test_api_attempt_payload_does_not_expose_api_key_or_prompt() -> None:
    """Spec §18：Attempt payload 不暴露 API Key、完整 Prompt 或原始响应。"""
    from offerpilot.api import _knowledge_brief_attempt_payload

    payload = _knowledge_brief_attempt_payload(_attempt_namespace())
    assert payload is not None
    # 不存在敏感字段键。
    for forbidden in ("api_key", "prompt", "raw_response", "chain_of_thought"):
        assert forbidden not in payload
    # payload 值里不应出现 key 明文（即使其他字段）。
    serialized = json.dumps(payload, ensure_ascii=False, default=str)
    assert "sk-" not in serialized
