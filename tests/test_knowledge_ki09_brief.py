"""KI-09 Brief generation / validation / 提交 单元测试。"""

from __future__ import annotations

import json
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
    BRIEF_REPAIR_PATCH_VERSION,
    BRIEF_SCHEMA_VERSION,
    BriefSchemaError,
    build_generation_prompt,
    build_repair_prompt,
    build_section_coverage_plan,
    build_validation_prompt,
    collect_brief_statement_blocks,
    parse_brief_payload,
    parse_support_decision,
    validate_brief_against_evidence,
)
from offerpilot.knowledge.repository import (
    BriefAttemptCreateInput,
    EvidenceRecord,
    JobCreateInput,
    KnowledgeBriefAttemptError,
    KnowledgeRepository,
)
from offerpilot.knowledge.service import IngestRequest
from offerpilot.knowledge.worker import (
    ExtractionWorker,
    BriefWorker,
    KnowledgeJobRunner,
)


# ---------------------------------------------------------------------------
# Spec §10.1 Schema / parse
# ---------------------------------------------------------------------------


def _valid_payload_dict() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "language": "zh-CN",
        "overview": [
            {"statement": "Source 描述了 OfferPilot 架构。", "evidence_ids": ["ev_1"]},
            {"statement": "Source 给出 SQLite SSOT 决策。", "evidence_ids": ["ev_2"]},
        ],
        "key_points": [
            {"statement": "Evidence 是引用单位。", "evidence_ids": ["ev_1", "ev_2"]},
        ],
        "section_guides": [
            {
                "section_key": "概述",
                "heading_path": ["概述"],
                "summary": "该章节介绍 OfferPilot 整体方向。",
                "evidence_ids": ["ev_1"],
            },
        ],
        "limitations": [
            {"statement": "未涉及 Pilot 对话细节。", "evidence_ids": ["ev_2"]},
        ],
    }


def test_parse_brief_payload_accepts_strict_json() -> None:
    raw = json.dumps(_valid_payload_dict(), ensure_ascii=False)
    brief = parse_brief_payload(raw)
    assert brief.schema_version == 2
    assert brief.language == BRIEF_LANGUAGE
    assert len(brief.overview) == 2


def test_parse_brief_payload_accepts_extra_text_around_json() -> None:
    """Spec §10.1 模型可能输出 markdown fence，需要从中提取 JSON。"""
    raw = (
        "```json\n"
        + json.dumps(_valid_payload_dict(), ensure_ascii=False)
        + "\n```"
    )
    brief = parse_brief_payload(raw)
    assert brief.schema_version == 2


def test_parse_brief_payload_picks_final_over_reasoning_draft() -> None:
    """推理模型（如 deepseek-v4-flash）把思考草稿和最终答案都写进 content：草稿
    overview 超限（8 条），最终答案合法（2 条）。parse 必须从多个 JSON 候选中
    选中最终合法 brief，而非首个草稿——否则会误报 overview 超限。"""

    draft = _valid_payload_dict()
    draft["overview"] = [
        {"statement": f"草稿概述 {i}", "evidence_ids": ["ev_1"]} for i in range(8)
    ]
    final = _valid_payload_dict()  # 合法 2 条 overview
    raw = (
        "先分析文档结构。\n草稿："
        + json.dumps(draft, ensure_ascii=False)
        + "\n修正后的最终 Brief：\n"
        + json.dumps(final, ensure_ascii=False)
    )
    brief = parse_brief_payload(raw)
    assert len(brief.overview) == 2


def test_parse_brief_payload_allows_document_toplevel_empty_heading_path() -> None:
    """``__document__``（文档顶层）天然无标题，heading_path 允许为空。coverage_plan
    对空 heading_path 的 Evidence 归入 ``__document__`` 并给出空 heading_path，模型
    照填后必须通过 schema——否则每次 generation 都因 section_guides[0] 误触 repair。"""

    payload = _valid_payload_dict()
    payload["section_guides"][0] = {
        "section_key": "__document__",
        "heading_path": [],
        "summary": "文档顶层摘要。",
        "evidence_ids": ["ev_1"],
    }
    brief = parse_brief_payload(json.dumps(payload, ensure_ascii=False))
    assert brief.section_guides[0].heading_path == []


def test_parse_brief_payload_rejects_empty_heading_path_for_named_section() -> None:
    """非 ``__document__`` 章节的 heading_path 仍必须非空（model_validator 兜底）。"""

    payload = _valid_payload_dict()
    payload["section_guides"][0]["heading_path"] = []
    with pytest.raises(BriefSchemaError):
        parse_brief_payload(json.dumps(payload, ensure_ascii=False))


def test_parse_brief_payload_rejects_invalid_json() -> None:
    with pytest.raises(BriefSchemaError) as info:
        parse_brief_payload("not json")
    assert info.value.code == "brief_schema_invalid"


def test_parse_brief_payload_rejects_markdown_text() -> None:
    with pytest.raises(BriefSchemaError) as info:
        parse_brief_payload("# Heading\n\nSome narrative text only.")
    assert info.value.code == "brief_schema_invalid"


def test_parse_brief_payload_rejects_too_few_overview() -> None:
    payload = _valid_payload_dict()
    payload["overview"] = [
        {"statement": "只一条概述。", "evidence_ids": ["ev_1"]},
    ]
    with pytest.raises(BriefSchemaError):
        parse_brief_payload(json.dumps(payload, ensure_ascii=False))


def test_parse_brief_payload_rejects_long_statement() -> None:
    payload = _valid_payload_dict()
    payload["overview"][0]["statement"] = "字" * 400
    with pytest.raises(BriefSchemaError):
        parse_brief_payload(json.dumps(payload, ensure_ascii=False))


def test_parse_brief_payload_rejects_empty_evidence_ids() -> None:
    payload = _valid_payload_dict()
    payload["overview"][0]["evidence_ids"] = []
    with pytest.raises(BriefSchemaError):
        parse_brief_payload(json.dumps(payload, ensure_ascii=False))


def test_parse_brief_payload_rejects_duplicate_evidence_ids() -> None:
    payload = _valid_payload_dict()
    payload["overview"][0]["evidence_ids"] = ["ev_1", "ev_1"]
    with pytest.raises(BriefSchemaError):
        parse_brief_payload(json.dumps(payload, ensure_ascii=False))


def test_parse_brief_payload_rejects_unsupported_language() -> None:
    payload = _valid_payload_dict()
    payload["language"] = "en-US"
    with pytest.raises(BriefSchemaError):
        parse_brief_payload(json.dumps(payload, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Spec §10.3 programmatic validation
# ---------------------------------------------------------------------------


def _evidence_record(
    *,
    evidence_id: str,
    heading_path: tuple[str, ...] = (),
    kind: str = "text",
) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        source_id=1,
        snapshot_id=1,
        kind=kind,
        block_kind="paragraph",
        ordinal=0,
        heading_path=list(heading_path),
        char_start=0,
        char_end=10,
        line_start=1,
        line_end=1,
        canonical_excerpt="原文",
        search_text="",
        content_hash="hash",
        asset_id=None,
        previous_evidence_id=None,
        next_evidence_id=None,
    )


def test_validate_brief_catches_fabricated_citation() -> None:
    """Spec §10.3 citation 必须属于当前 Source/Snapshot。"""
    raw = json.dumps(_valid_payload_dict(), ensure_ascii=False)
    brief = parse_brief_payload(raw)
    evidence_rows = [
        _evidence_record(evidence_id="ev_1", heading_path=("概述",)),
        _evidence_record(evidence_id="ev_2", heading_path=("概述",)),
    ]
    coverage_plan = build_section_coverage_plan(evidence_rows)
    report = validate_brief_against_evidence(
        brief, evidence_rows=evidence_rows, expected_sections=coverage_plan
    )
    assert report.citation_ok
    assert report.coverage_ok

    # 注入伪造 citation
    brief.overview[0].evidence_ids.append("ev_FAKE")
    report = validate_brief_against_evidence(
        brief, evidence_rows=evidence_rows, expected_sections=coverage_plan
    )
    assert not report.citation_ok
    assert any("ev_FAKE" in issue for issue in report.issues)


def test_validate_brief_catches_missing_section_coverage() -> None:
    """KBR-04：含文本 Evidence 的章节未被实际引用 → coverage 失败。"""
    evidence_rows = [
        _evidence_record(evidence_id="ev_1", heading_path=("概述",)),
        _evidence_record(evidence_id="ev_2", heading_path=("另一章",)),
    ]
    coverage_plan = build_section_coverage_plan(evidence_rows)
    payload = _valid_payload_dict()
    for block_name in ("overview", "key_points", "section_guides", "limitations"):
        for item in payload[block_name]:
            item["evidence_ids"] = ["ev_1"]
    brief = parse_brief_payload(json.dumps(payload, ensure_ascii=False))
    report = validate_brief_against_evidence(
        brief,
        evidence_rows=evidence_rows,
        expected_sections=coverage_plan,
    )
    assert not report.coverage_ok
    assert any("另一章" in issue for issue in report.issues)


def test_validate_brief_accepts_assets_only_section_skipped_by_program() -> None:
    """KBR-04：assets-only 章节由程序标 skipped，不要求模型引用，coverage 仍通过。"""
    evidence_rows = [
        _evidence_record(evidence_id="ev_img", heading_path=("附图",), kind="asset"),
        _evidence_record(evidence_id="ev_1", heading_path=("概述",)),
        _evidence_record(evidence_id="ev_2", heading_path=("概述",)),
    ]
    coverage_plan = build_section_coverage_plan(evidence_rows)
    payload = _valid_payload_dict()
    brief = parse_brief_payload(json.dumps(payload, ensure_ascii=False))
    report = validate_brief_against_evidence(
        brief,
        evidence_rows=evidence_rows,
        expected_sections=coverage_plan,
    )
    assert report.coverage_ok


# ---------------------------------------------------------------------------
# Spec §10.3 support validation (parse)
# ---------------------------------------------------------------------------


def test_parse_support_decision_valid() -> None:
    decision = parse_support_decision(
        json.dumps(
            {"decision": "supported", "reason": "Evidence 直接说明"},
            ensure_ascii=False,
        )
    )
    assert decision.decision == "supported"


def test_parse_support_decision_rejects_unknown_decision() -> None:
    with pytest.raises(BriefSchemaError):
        parse_support_decision(
            json.dumps({"decision": "kinda_ok", "reason": "x"})
        )


def test_parse_support_decision_rejects_empty_reason() -> None:
    with pytest.raises(BriefSchemaError):
        parse_support_decision(
            json.dumps({"decision": "supported", "reason": ""})
        )


def test_parse_support_decision_preserves_structured_diagnostics() -> None:
    decision = parse_support_decision(
        json.dumps(
            {
                "decision": "partial",
                "reason": "Evidence 未说明默认行为",
                "reason_code": "unsupported_qualifier",
                "unsupported_fragments": ["默认"],
                "explanation": "Evidence 只描述 PROXY 模式，没有说明默认模式。",
                "suggested_rewrite": "该选择器在 PROXY 模式下引入配置类。",
            },
            ensure_ascii=False,
        ),
        statement="该选择器默认在 PROXY 模式下引入配置类。",
    )
    assert decision.reason_code == "unsupported_qualifier"
    assert decision.unsupported_fragments == ["默认"]
    assert decision.suggested_rewrite.startswith("该选择器")


def test_parse_support_decision_unknown_reason_code_is_safe() -> None:
    """未知 reason_code 不得注入报告，但不改变 Validator decision。"""
    decision = parse_support_decision(
        json.dumps(
            {
                "decision": "partial",
                "reason": "Evidence 只部分支持",
                "reason_code": "model_invented_code",
            },
            ensure_ascii=False,
        )
    )
    assert decision.decision == "partial"
    assert decision.reason_code == "validator_unknown_reason"


def test_parse_support_decision_rejects_fragment_not_in_statement() -> None:
    with pytest.raises(BriefSchemaError):
        parse_support_decision(
            json.dumps(
                {
                    "decision": "partial",
                    "reason": "外延",
                    "unsupported_fragments": ["读者评论"],
                },
                ensure_ascii=False,
            ),
            statement="文章使用了反向代理一词。",
        )


def test_collect_brief_statement_blocks_includes_all_sections() -> None:
    raw = json.dumps(_valid_payload_dict(), ensure_ascii=False)
    brief = parse_brief_payload(raw)
    blocks = collect_brief_statement_blocks(brief)
    block_names = [name for name, _, _ in blocks]
    assert "overview[0]" in block_names
    assert "key_points[0]" in block_names
    assert "section_guides[0]" in block_names
    assert "limitations[0]" in block_names


# ---------------------------------------------------------------------------
# Spec §10.2 generation prompt
# ---------------------------------------------------------------------------


def test_generation_prompt_injects_evidence_and_coverage() -> None:
    evidence_rows = [
        _evidence_record(evidence_id="ev_1", heading_path=("概述",)),
        _evidence_record(evidence_id="ev_2", heading_path=("概述",)),
    ]
    coverage_plan = build_section_coverage_plan(evidence_rows)
    messages = build_generation_prompt(
        source_title="Test",
        evidence_rows=evidence_rows,
        coverage_plan=coverage_plan,
    )
    user_text = messages[1]["content"]
    assert "ev_1" in user_text
    assert "ev_2" in user_text
    assert "概述" in user_text
    assert "Schema v2" in messages[0]["content"]


def test_generation_prompt_marks_assets_only_sections() -> None:
    """Spec §10.2 仅含图片的章节必须 skipped。"""
    evidence_rows = [
        _evidence_record(
            evidence_id="ev_img",
            heading_path=("附图",),
            kind="asset",
        ),
        _evidence_record(evidence_id="ev_1", heading_path=("正文",)),
    ]
    coverage_plan = build_section_coverage_plan(evidence_rows)
    payload = coverage_plan.to_payload()
    assets_only_sections = [
        item for item in payload if item["must_skip"]
    ]
    assert len(assets_only_sections) == 1
    assert assets_only_sections[0]["section_key"] == "附图"


def test_repair_prompt_restricts_to_fixes() -> None:
    """Spec §10.3 / KBR-06 repair 只能对失败 block 返回 replace/delete/split patch。"""
    from offerpilot.knowledge.brief import ISSUE_CITATION_MISSING, ValidationIssue

    evidence_row = _evidence_record(evidence_id="ev_1")
    messages = build_repair_prompt(
        source_title="T",
        evidence_rows=[evidence_row],
        coverage_plan=build_section_coverage_plan([evidence_row]),
        candidate=parse_brief_payload(json.dumps(_valid_payload_dict(), ensure_ascii=False)),
        failed_issues=[
            ValidationIssue(
                block_path="overview[0]",
                issue_type=ISSUE_CITATION_MISSING,
                decision="",
                reason="引用 ev_FAKE 不存在",
                evidence_ids=["ev_FAKE"],
            )
        ],
        failed_block_paths=["overview[0]"],
    )
    # patch prompt 只允许 replace/delete/split，禁止改已通过 block / 跨 Source / 新增主题。
    assert "replace" in messages[0]["content"]
    assert "delete" in messages[0]["content"]
    assert "split" in messages[0]["content"]
    assert "overview[0]" in messages[1]["content"]
    assert "引用 ev_FAKE 不存在" in messages[1]["content"]


def test_validation_prompt_isolates_statement_and_evidence() -> None:
    """Spec §10.3 Validator 只读取 statement + cited Evidence。"""
    messages = build_validation_prompt(
        statement="Source 描述 SQLite SSOT。",
        cited_evidence=[
            {"id": "ev_1", "section": "概述", "kind": "text", "excerpt": "..."}
        ],
    )
    assert "Validator" in messages[0]["content"]
    assert "ev_1" in messages[1]["content"]
    assert "Source 描述 SQLite SSOT" in messages[1]["content"]


# ---------------------------------------------------------------------------
# Repository: Attempt lifecycle
# ---------------------------------------------------------------------------


def _setup_repository(tmp_path: Path) -> tuple[KnowledgeRepository, sessionmaker[Session], int, int]:
    init_database(tmp_path / "data.db")
    session_factory = session_factory_for_data_dir(tmp_path)
    repository = KnowledgeRepository(session_factory)

    # 通过 service 走完一次完整 ingest，再显式驱动 Extraction queue，
    # 得到一个 extracted Source（ingest 只入队 extract job）。
    from offerpilot.knowledge.service import KnowledgeIngestService

    service = KnowledgeIngestService(repository, tmp_path, session_factory)
    result = service.ingest(
        IngestRequest(
            filename="doc.md",
            content_bytes="# 概述\n\nSource 描述 OfferPilot 与 SQLite。\n\n## 第二段\n\n另一条 Evidence。\n".encode("utf-8"),
            title_hint="测试",
        )
    )
    source_id = result.source.id
    KnowledgeJobRunner(
        repository,
        ExtractionWorker(
            repository,
            tmp_path,
            session_factory,
            on_extraction_succeeded=service.enqueue_or_block_brief,
        ),
    ).tick_extraction(lease_owner="test")
    source = repository.get_source(source_id)
    assert source is not None
    return repository, session_factory, source_id, source.active_snapshot_id or 0


def test_create_brief_attempt_locks_source_processing(tmp_path: Path) -> None:
    repository, _, source_id, snapshot_id = _setup_repository(tmp_path)
    attempt, job_id, token = repository.create_brief_attempt(
        BriefAttemptCreateInput(
            source_id=source_id,
            snapshot_id=snapshot_id,
            provider_id="default",
            provider_model="test-model",
            provider_base_url="https://example.com",
            context_window=BRIEF_MIN_CONTEXT_WINDOW,
            max_output_tokens=4096,
            prompt_version=BRIEF_PROMPT_VERSION,
            schema_version=BRIEF_SCHEMA_VERSION,
            language=BRIEF_LANGUAGE,
        )
    )
    assert attempt.status == "processing"
    assert job_id > 0
    assert token
    job = repository.get_job(job_id)
    assert job is not None
    assert job.attempt_id == attempt.id
    assert repository.find_brief_job_for_attempt(attempt.id).id == job_id  # type: ignore[union-attr]
    # Source 状态进入 processing
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "processing"


def test_create_brief_attempt_rejects_duplicate_active(tmp_path: Path) -> None:
    repository, _, source_id, snapshot_id = _setup_repository(tmp_path)
    repository.create_brief_attempt(
        BriefAttemptCreateInput(
            source_id=source_id,
            snapshot_id=snapshot_id,
            provider_id="default",
            provider_model="test-model",
            provider_base_url="https://example.com",
            context_window=BRIEF_MIN_CONTEXT_WINDOW,
            max_output_tokens=4096,
            prompt_version=BRIEF_PROMPT_VERSION,
            schema_version=BRIEF_SCHEMA_VERSION,
            language=BRIEF_LANGUAGE,
        )
    )
    with pytest.raises(KnowledgeBriefAttemptError) as info:
        repository.create_brief_attempt(
            BriefAttemptCreateInput(
                source_id=source_id,
                snapshot_id=snapshot_id,
                provider_id="default",
                provider_model="test-model",
                provider_base_url="https://example.com",
                context_window=BRIEF_MIN_CONTEXT_WINDOW,
                max_output_tokens=4096,
                prompt_version=BRIEF_PROMPT_VERSION,
                schema_version=BRIEF_SCHEMA_VERSION,
                language=BRIEF_LANGUAGE,
            )
        )
    assert info.value.code == "brief_attempt_conflict"


def test_find_brief_job_for_attempt_does_not_cross_old_attempts(tmp_path: Path) -> None:
    """同一 Source 的新旧 Attempt 必须各自回读自己的 Job。"""
    repository, _, source_id, snapshot_id = _setup_repository(tmp_path)
    data = BriefAttemptCreateInput(
        source_id=source_id,
        snapshot_id=snapshot_id,
        provider_id="default",
        provider_model="test-model",
        provider_base_url="https://example.com",
        context_window=BRIEF_MIN_CONTEXT_WINDOW,
        max_output_tokens=4096,
        prompt_version=BRIEF_PROMPT_VERSION,
        schema_version=BRIEF_SCHEMA_VERSION,
        language=BRIEF_LANGUAGE,
    )

    first, first_job_id, first_token = repository.create_brief_attempt(data)
    failed, _, _ = repository.fail_brief_attempt(
        first.id,
        job_id=first_job_id,
        attempt_token=first_token,
        error_code="brief_schema_invalid",
        error_message="first candidate rejected",
    )
    assert failed is True

    second, second_job_id, second_token = repository.create_brief_attempt(data)
    assert second_job_id != first_job_id
    assert second_token
    first_job = repository.find_brief_job_for_attempt(first.id)
    second_job = repository.find_brief_job_for_attempt(second.id)
    assert first_job is not None
    assert first_job.id == first_job_id
    assert first_job.status == "failed"
    assert second_job is not None
    assert second_job.id == second_job_id
    assert second_job.attempt_id == second.id


def test_commit_brief_attempt_success_replaces_current_brief(tmp_path: Path) -> None:
    repository, _, source_id, snapshot_id = _setup_repository(tmp_path)
    attempt, job_id, token = repository.create_brief_attempt(
        BriefAttemptCreateInput(
            source_id=source_id,
            snapshot_id=snapshot_id,
            provider_id="default",
            provider_model="test-model",
            provider_base_url="",
            context_window=BRIEF_MIN_CONTEXT_WINDOW,
            max_output_tokens=4096,
            prompt_version=BRIEF_PROMPT_VERSION,
            schema_version=BRIEF_SCHEMA_VERSION,
            language=BRIEF_LANGUAGE,
        )
    )
    payload_json = json.dumps(_valid_payload_dict(), ensure_ascii=False)
    ok, brief, _ = repository.commit_brief_attempt_success(
        attempt.id,
        job_id=job_id,
        attempt_token=token,
        payload_json=payload_json,
        validation_report_json="{}",
        token_input_count=1234,
        token_output_count=567,
        latency_ms=800,
    )
    assert ok
    assert brief is not None
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "ready"
    assert source.active_brief_id == brief.id

    fetched = repository.get_source_brief(source_id)
    assert fetched is not None
    assert fetched.payload_json == payload_json


def test_commit_brief_attempt_rejects_stale_token(tmp_path: Path) -> None:
    """Spec §12 迟到 lease 拒绝提交。"""
    repository, _, source_id, snapshot_id = _setup_repository(tmp_path)
    attempt, job_id, _ = repository.create_brief_attempt(
        BriefAttemptCreateInput(
            source_id=source_id,
            snapshot_id=snapshot_id,
            provider_id="default",
            provider_model="test-model",
            provider_base_url="",
            context_window=BRIEF_MIN_CONTEXT_WINDOW,
            max_output_tokens=4096,
            prompt_version=BRIEF_PROMPT_VERSION,
            schema_version=BRIEF_SCHEMA_VERSION,
            language=BRIEF_LANGUAGE,
        )
    )
    ok, brief, _ = repository.commit_brief_attempt_success(
        attempt.id,
        job_id=job_id,
        attempt_token="wrong-token",
        payload_json="{}",
        validation_report_json="{}",
    )
    assert not ok
    assert brief is None


def test_fail_brief_attempt_marks_source_failed(tmp_path: Path) -> None:
    repository, _, source_id, snapshot_id = _setup_repository(tmp_path)
    attempt, job_id, token = repository.create_brief_attempt(
        BriefAttemptCreateInput(
            source_id=source_id,
            snapshot_id=snapshot_id,
            provider_id="default",
            provider_model="test-model",
            provider_base_url="",
            context_window=BRIEF_MIN_CONTEXT_WINDOW,
            max_output_tokens=4096,
            prompt_version=BRIEF_PROMPT_VERSION,
            schema_version=BRIEF_SCHEMA_VERSION,
            language=BRIEF_LANGUAGE,
        )
    )
    ok, attempt_record, _ = repository.fail_brief_attempt(
        attempt.id,
        job_id=job_id,
        attempt_token=token,
        error_code="brief_quality_failed",
        error_message="unsupported x1",
        validation_report_json='{"stage":"support"}',
    )
    assert ok
    assert attempt_record is not None
    assert attempt_record.status == "failed"
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "failed"
    assert source.brief_error_code == "brief_quality_failed"


# ---------------------------------------------------------------------------
# BriefWorker end-to-end with stub model_client
# ---------------------------------------------------------------------------


def _stub_model_client_factory(
    *,
    generation_output: str,
    validation_outputs: list[str],
):
    """生成一个可控的 stub model_client，按调用顺序返回不同响应。"""

    call_log: list[dict[str, Any]] = []

    def _client(**payload: Any) -> dict[str, Any]:
        messages = payload.get("messages") or []
        system_text = ""
        user_text = ""
        for message in messages:
            role = message.get("role")
            if role == "system":
                system_text = message.get("content") or ""
            elif role == "user":
                user_text = message.get("content") or ""
        call_log.append({"system": system_text, "user": user_text})
        if "Validator" in system_text:
            text = validation_outputs.pop(0) if validation_outputs else (
                json.dumps({"decision": "supported", "reason": "stub supported"})
            )
        elif "Repair Agent" in system_text:
            # KBR-06：repair 必须返回结构化 patch；空 patch 应用后候选不变，复验仍失败。
            text = json.dumps({"version": BRIEF_REPAIR_PATCH_VERSION, "operations": []}, ensure_ascii=False)
        else:
            text = generation_output
        return {
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200},
        }

    return _client, call_log


def _provider_config(context_window: int = BRIEF_MIN_CONTEXT_WINDOW) -> Config:
    provider = AIProviderProfile(
        id="default",
        label="Default",
        provider="openai",
        api_key="sk-test",
        base_url="https://example.com",
        model="gpt-test",
        enabled=True,
        context_window=context_window,
        max_output_tokens=4096,
    )
    return Config(
        api_key="sk-test",
        providers=[provider],
        active_provider_id="default",
    )


def _build_valid_payload_from_evidence(evidence_page_items: list[EvidenceRecord]) -> dict[str, Any]:
    """根据真实 Evidence 列表构造合法 v2 Brief payload。

    KBR-04：不再输出 coverage；每个文本章节至少有一条自身 Evidence 被 statement
    实际引用，以保证程序派生 coverage 通过。
    """
    evidence_ids = [item.id for item in evidence_page_items]
    assert len(evidence_ids) >= 2
    from offerpilot.knowledge.brief import build_section_coverage_plan

    plan = build_section_coverage_plan(evidence_page_items)
    section_eids: dict[str, list[str]] = {}
    for item in evidence_page_items:
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
    valid_payload = _valid_payload_dict()
    valid_payload["overview"][0]["evidence_ids"] = [ov1]
    valid_payload["overview"][1]["evidence_ids"] = [ov2]
    valid_payload["key_points"][0]["evidence_ids"] = [ov1, ov2]
    valid_payload["section_guides"][0]["evidence_ids"] = [first_eid]
    valid_payload["section_guides"][0]["section_key"] = first.section_key
    valid_payload["section_guides"][0]["heading_path"] = list(first.heading_path)
    valid_payload["limitations"][0]["evidence_ids"] = [ov2]
    # 追加未引用文本章节的代表 Evidence，保证 coverage 全 covered。
    cited = {ov1, ov2, first_eid}
    extra = [rep for rep in reps if rep not in cited]
    if extra:
        valid_payload["key_points"][0]["evidence_ids"].extend(extra)
    return valid_payload


def test_brief_worker_generates_and_commits_brief(tmp_path: Path) -> None:
    repository, session_factory, source_id, snapshot_id = _setup_repository(tmp_path)
    config = _provider_config()
    # 使用真实 Evidence ID 构造合法 Brief payload
    evidence_page = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50)
    valid_payload = _build_valid_payload_from_evidence(evidence_page.items)
    generation_output = json.dumps(valid_payload, ensure_ascii=False)
    validation_outputs = [
        json.dumps({"decision": "supported", "reason": "ok"})
        for _ in range(10)
    ]
    model_client, _ = _stub_model_client_factory(
        generation_output=generation_output,
        validation_outputs=validation_outputs,
    )
    worker = BriefWorker(repository, config, model_client=model_client)
    repository.create_job(
        JobCreateInput(
            kind="brief",
            queue="brief",
            source_id=source_id,
            snapshot_id=snapshot_id,
            stage="brief_pending",
        )
    )
    runner = KnowledgeJobRunner(
        repository,
        ExtractionWorker(repository, tmp_path, session_factory),
        brief_worker=worker,
    )
    results = runner.tick_brief(lease_owner="test")
    assert results
    assert results[0].status == "succeeded", results[0].error_message
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "ready"
    brief = repository.get_source_brief(source_id)
    assert brief is not None


def test_brief_worker_fails_when_support_unsupported(tmp_path: Path) -> None:
    repository, session_factory, source_id, snapshot_id = _setup_repository(tmp_path)
    config = _provider_config()
    evidence_page = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50)
    valid_payload = _build_valid_payload_from_evidence(evidence_page.items)
    generation_output = json.dumps(valid_payload, ensure_ascii=False)
    validation_outputs = [
        json.dumps({"decision": "unsupported", "reason": "no link"})
        for _ in range(10)
    ]
    model_client, _ = _stub_model_client_factory(
        generation_output=generation_output,
        validation_outputs=validation_outputs,
    )
    worker = BriefWorker(repository, config, model_client=model_client)
    repository.create_job(
        JobCreateInput(
            kind="brief",
            queue="brief",
            source_id=source_id,
            snapshot_id=snapshot_id,
            stage="brief_pending",
        )
    )
    runner = KnowledgeJobRunner(
        repository,
        ExtractionWorker(repository, tmp_path, session_factory),
        brief_worker=worker,
    )
    results = runner.tick_brief(lease_owner="test")
    assert results
    assert results[0].status == "failed"
    # KBR-05：质量失败统一 error_code；support unsupported 进入结构化 report。
    assert results[0].error_code == "brief_quality_failed"
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "failed"
    # 未提交 Brief
    assert repository.get_source_brief(source_id) is None
    steps = repository.list_brief_attempt_steps(
        repository.find_latest_brief_attempt(source_id).id  # type: ignore[union-attr]
    )
    assert any(step.phase == "final" and step.status == "failed" for step in steps)
    assert all("response_preview" not in step.output_json for step in steps)


def test_validator_parse_failure_is_replayed_as_structured_step(tmp_path: Path) -> None:
    """Validator JSON 解析失败要保留 block_path 和结构化失败步骤。"""
    repository, session_factory, source_id, snapshot_id = _setup_repository(tmp_path)
    evidence_page = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50)
    generation_output = json.dumps(
        _build_valid_payload_from_evidence(evidence_page.items), ensure_ascii=False
    )
    model_client, _ = _stub_model_client_factory(
        generation_output=generation_output,
        validation_outputs=["not-json"] + [
            json.dumps({"decision": "supported", "reason": "ok"})
            for _ in range(20)
        ],
    )
    worker = BriefWorker(repository, _provider_config(), model_client=model_client)
    repository.create_job(
        JobCreateInput(
            kind="brief",
            queue="brief",
            source_id=source_id,
            snapshot_id=snapshot_id,
            stage="brief_pending",
        )
    )
    results = KnowledgeJobRunner(
        repository,
        ExtractionWorker(repository, tmp_path, session_factory),
        brief_worker=worker,
    ).tick_brief(lease_owner="test")
    assert results[0].status == "succeeded", results[0].error_message
    attempt = repository.find_latest_brief_attempt(source_id)
    assert attempt is not None
    steps = repository.list_brief_attempt_steps(attempt.id)
    parse_steps = [
        step
        for step in steps
        if step.phase == "validation_result"
        and step.error_code == "validator_parse_failed"
    ]
    assert parse_steps
    assert parse_steps[0].block_path
    assert json.loads(parse_steps[0].output_json)["issue_type"] == "validator_parse_failed"


def test_attempt_step_api_drops_legacy_preview() -> None:
    """旧库中截断 preview 也不得通过 API 暴露。"""
    from offerpilot.api import _knowledge_brief_attempt_step_payload

    payload = _knowledge_brief_attempt_step_payload(
        SimpleNamespace(
            id=1,
            attempt_id=2,
            sequence=1,
            iteration=0,
            phase="model_call",
            status="completed",
            block_path="",
            provider_id="p",
            provider_model="m",
            prompt_version="v",
            schema_version=2,
            evidence_ids=[],
            output_json=json.dumps(
                {"truncated": True, "preview": "secret", "response_preview": "secret"}
            ),
            token_input_count=0,
            token_output_count=0,
            latency_ms=0,
            retry_count=0,
            error_code="",
            error_message="",
            created_at=None,
        )
    )
    assert payload["output"] == {"truncated": True}


def test_brief_worker_repairs_once_then_succeeds(tmp_path: Path) -> None:
    """Spec §10.3 首次失败允许一次修复。"""
    repository, session_factory, source_id, snapshot_id = _setup_repository(tmp_path)
    config = _provider_config()

    evidence_page = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50)
    valid_payload = _build_valid_payload_from_evidence(evidence_page.items)

    # 「invalid」由 validation stub 模拟（overview[0] 首轮 unsupported）；citation 保持有效，
    # 故 Finding 5 下 repair replace 落在原块有效 citation 所属章节集合内。完全无效 citation
    # 的块只允许 delete，不在本 replace 修复用例覆盖。
    invalid_payload = json.loads(json.dumps(valid_payload))

    call_index = {"generation": 0}

    def _client(**payload: Any) -> dict[str, Any]:
        system_text = ""
        for message in payload.get("messages") or []:
            if message.get("role") == "system":
                system_text = message.get("content") or ""

        if "Validator" in system_text:
            # 首次 validation（overview[0]）unsupported，repair 后复验全 supported。
            call_index["validation"] = call_index.get("validation", 0) + 1
            decision = (
                "unsupported" if call_index["validation"] == 1 else "supported"
            )
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"decision": decision, "reason": "stub support"}
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }
        if "Repair Agent" in system_text:
            # KBR-06：repair 返回结构化 patch，replace overview[0] 为合法 citation。
            patch = {
                "version": BRIEF_REPAIR_PATCH_VERSION,
                "operations": [
                    {
                        "block_path": "overview[0]",
                        "action": "replace",
                        "payload": valid_payload["overview"][0],
                    }
                ],
            }
            return {
                "choices": [
                    {"message": {"content": json.dumps(patch, ensure_ascii=False)}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }

        call_index["generation"] += 1
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(invalid_payload, ensure_ascii=False)
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }

    worker = BriefWorker(repository, config, model_client=_client)
    repository.create_job(
        JobCreateInput(
            kind="brief",
            queue="brief",
            source_id=source_id,
            snapshot_id=snapshot_id,
            stage="brief_pending",
        )
    )
    runner = KnowledgeJobRunner(
        repository,
        ExtractionWorker(repository, tmp_path, session_factory),
        brief_worker=worker,
    )
    results = runner.tick_brief(lease_owner="test")
    assert results
    assert results[0].status == "succeeded", results[0].error_message
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "ready"


def test_brief_worker_fails_when_provider_context_too_small(tmp_path: Path) -> None:
    """Spec §4.2 / §11.2 context < 96K 时不发请求。"""
    repository, session_factory, source_id, snapshot_id = _setup_repository(tmp_path)
    config = _provider_config(context_window=32_000)
    worker = BriefWorker(repository, config, model_client=lambda **p: {})
    repository.create_job(
        JobCreateInput(
            kind="brief",
            queue="brief",
            source_id=source_id,
            snapshot_id=snapshot_id,
            stage="brief_pending",
        )
    )
    runner = KnowledgeJobRunner(
        repository,
        ExtractionWorker(repository, tmp_path, session_factory),
        brief_worker=worker,
    )
    results = runner.tick_brief(lease_owner="test")
    assert results
    assert results[0].status == "failed"
    assert results[0].error_code == "provider_context_too_small"
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "pending"
    assert source.brief_block_reason == "provider_context_too_small"


def test_brief_worker_handles_invalid_json_then_repairs_twice_fails(tmp_path: Path) -> None:
    """Spec §10.3 第二次仍失败则 Attempt failed。"""
    repository, session_factory, source_id, snapshot_id = _setup_repository(tmp_path)
    config = _provider_config()

    def _client(**payload: Any) -> dict[str, Any]:
        system_text = ""
        for message in payload.get("messages") or []:
            if message.get("role") == "system":
                system_text = message.get("content") or ""
        if "Validator" in system_text:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"decision": "supported", "reason": "ok"}
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }
        # 始终返回非 JSON 文本，触发 schema_invalid
        return {
            "choices": [{"message": {"content": "this is not json"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }

    worker = BriefWorker(repository, config, model_client=_client)
    repository.create_job(
        JobCreateInput(
            kind="brief",
            queue="brief",
            source_id=source_id,
            snapshot_id=snapshot_id,
            stage="brief_pending",
        )
    )
    runner = KnowledgeJobRunner(
        repository,
        ExtractionWorker(repository, tmp_path, session_factory),
        brief_worker=worker,
    )
    results = runner.tick_brief(lease_owner="test")
    assert results
    assert results[0].status == "failed"
    assert results[0].error_code == "brief_schema_invalid"
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "failed"


@pytest.mark.parametrize(
    "decision",
    ["partial", "unsupported", "contradicted"],
)
def test_brief_worker_fails_for_non_supported_decisions(
    tmp_path: Path, decision: str
) -> None:
    """Spec §10.3 仅 supported 可发布；partial/unsupported/contradicted 均判失败。"""
    repository, session_factory, source_id, snapshot_id = _setup_repository(tmp_path)
    config = _provider_config()

    evidence_page = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50)
    valid_payload = _build_valid_payload_from_evidence(evidence_page.items)

    def _client(**payload: Any) -> dict[str, Any]:
        system_text = ""
        for message in payload.get("messages") or []:
            if message.get("role") == "system":
                system_text = message.get("content") or ""
        if "Validator" in system_text:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"decision": decision, "reason": "stub non-supported"}
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }
        if "Repair Agent" in system_text:
            # KBR-06：repair 返回空 patch，复验仍产生同样非 supported decision → 失败。
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"version": BRIEF_REPAIR_PATCH_VERSION, "operations": []}, ensure_ascii=False
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(valid_payload, ensure_ascii=False)
                    }
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200},
        }

    worker = BriefWorker(repository, config, model_client=_client)
    repository.create_job(
        JobCreateInput(
            kind="brief",
            queue="brief",
            source_id=source_id,
            snapshot_id=snapshot_id,
            stage="brief_pending",
        )
    )
    runner = KnowledgeJobRunner(
        repository,
        ExtractionWorker(repository, tmp_path, session_factory),
        brief_worker=worker,
    )
    results = runner.tick_brief(lease_owner="test")
    assert results
    assert results[0].status == "failed"
    # KBR-05：partial/unsupported/contradicted 均为硬失败，统一 error_code；
    # 具体支持性判定在结构化 report 的 issue_type 中区分。
    assert results[0].error_code == "brief_quality_failed"
    attempt = repository.find_latest_brief_attempt(source_id)
    assert attempt is not None
    report = json.loads(attempt.validation_report_json or "{}")
    issue_types = {item.get("issue_type") for item in report.get("issues", [])}
    expected_issue_type = {
        "partial": "support_partial",
        "unsupported": "support_unsupported",
        "contradicted": "support_contradicted",
    }[decision]
    assert expected_issue_type in issue_types
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "failed"
    assert repository.get_source_brief(source_id) is None


def test_brief_worker_repairs_support_failure_then_succeeds(tmp_path: Path) -> None:
    """Spec §10.3 / KBR-06 support validation 首次失败允许一次结构化 patch repair。

    首轮 overview[0] partial → repair replace overview[0] 为原子陈述 → 复验全 supported
    → ready。KBR-06 把 repair 从「重写完整 Brief」改为「只对失败 block 返回 patch」。
    """
    repository, session_factory, source_id, snapshot_id = _setup_repository(tmp_path)
    config = _provider_config()

    evidence_page = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50)
    valid_payload = _build_valid_payload_from_evidence(evidence_page.items)

    call_state = {"generation_count": 0, "validation_calls": 0}

    def _client(**payload: Any) -> dict[str, Any]:
        system_text = ""
        for message in payload.get("messages") or []:
            if message.get("role") == "system":
                system_text = message.get("content") or ""
        if "Validator" in system_text:
            # 第 1 次 validation（overview[0]）partial，其余 supported。
            call_state["validation_calls"] += 1
            decision = (
                "partial" if call_state["validation_calls"] == 1 else "supported"
            )
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"decision": decision, "reason": "stub support"}
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }
        if "Repair Agent" in system_text:
            # KBR-06：repair replace overview[0]（失败 block）为原子陈述。
            call_state["generation_count"] += 1
            patch = {
                "version": BRIEF_REPAIR_PATCH_VERSION,
                "operations": [
                    {
                        "block_path": "overview[0]",
                        "action": "replace",
                        "payload": valid_payload["overview"][0],
                    }
                ],
            }
            return {
                "choices": [
                    {"message": {"content": json.dumps(patch, ensure_ascii=False)}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }
        call_state["generation_count"] += 1
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(valid_payload, ensure_ascii=False)
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }

    worker = BriefWorker(repository, config, model_client=_client)
    repository.create_job(
        JobCreateInput(
            kind="brief",
            queue="brief",
            source_id=source_id,
            snapshot_id=snapshot_id,
            stage="brief_pending",
        )
    )
    runner = KnowledgeJobRunner(
        repository,
        ExtractionWorker(repository, tmp_path, session_factory),
        brief_worker=worker,
    )
    results = runner.tick_brief(lease_owner="test")
    assert results
    assert results[0].status == "succeeded", results[0].error_message
    # generation 首次 + repair 第二次。
    assert call_state["generation_count"] == 2
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "ready"


def test_brief_worker_classifies_provider_auth_error(tmp_path: Path) -> None:
    """Spec §11.4：鉴权失败应归类为 provider_auth_invalid，便于 KI-10 不重试。"""
    repository, session_factory, source_id, snapshot_id = _setup_repository(tmp_path)
    config = _provider_config()

    class _AuthError(Exception):
        pass

    def _client(**payload: Any) -> dict[str, Any]:
        raise _AuthError("AuthenticationError: 401 invalid api key")

    worker = BriefWorker(repository, config, model_client=_client)
    repository.create_job(
        JobCreateInput(
            kind="brief",
            queue="brief",
            source_id=source_id,
            snapshot_id=snapshot_id,
            stage="brief_pending",
        )
    )
    runner = KnowledgeJobRunner(
        repository,
        ExtractionWorker(repository, tmp_path, session_factory),
        brief_worker=worker,
    )
    results = runner.tick_brief(lease_owner="test")
    assert results
    assert results[0].status == "failed"
    assert results[0].error_code == "provider_auth_invalid"
