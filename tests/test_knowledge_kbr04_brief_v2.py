"""KBR-04 Brief Schema v2 与确定性 citation coverage 单元测试。

Spec Implementation Decisions：
- Brief Schema v2，移除模型输出的 coverage；API/UI coverage 由程序派生。
- 预期 coverage 章节只来自 post-filter 的当前 Snapshot 合格正文 Evidence。
- 某章节至少有一条 Evidence 被 overview/key point/section guide/limitation 实际引用才 covered。
- 引用其他章节 Evidence 不能让当前章节通过；assets-only 章节程序标 skipped。
- key point/limitation/section guide summary 单一可验证核心断言；overview 禁止无 citation 支持事实。
- 不保留 v1 模型响应兼容分支。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from offerpilot.knowledge.brief import (
    BRIEF_LANGUAGE,
    BRIEF_PROMPT_VERSION,
    BRIEF_SCHEMA_VERSION,
    BriefPayload,
    BriefSchemaError,
    ISSUE_CITATION_MISSING,
    ValidationIssue,
    build_generation_prompt,
    build_repair_prompt,
    build_section_coverage_plan,
    derive_section_coverage,
    parse_brief_payload,
    validate_brief_against_evidence,
)


def _ev(
    evidence_id: str,
    heading_path: tuple[str, ...] = (),
    kind: str = "text",
) -> Any:
    return SimpleNamespace(
        id=evidence_id,
        heading_path=list(heading_path),
        kind=kind,
        canonical_excerpt="原文片段",
        search_text="alt 文本" if kind == "asset" else "",
    )


def _v2_payload(
    *,
    overview_ids: list[str] | None = None,
    key_ids: list[str] | None = None,
    guide_ids: list[str] | None = None,
    limit_ids: list[str] | None = None,
    guide_section: str = "概述",
    guide_heading: tuple[str, ...] = ("概述",),
    with_model_coverage: bool = False,
) -> dict[str, Any]:
    """构造合法 v2 payload（默认无 coverage 字段）。

    ``with_model_coverage=True`` 模拟模型违反 v2 契约仍输出 coverage 字段，用于
    证明程序派生不被模型声明影响。
    """
    payload: dict[str, Any] = {
        "schema_version": 2,
        "language": "zh-CN",
        "overview": [
            {"statement": "概述一。", "evidence_ids": overview_ids or ["ev_1"]},
            {"statement": "概述二。", "evidence_ids": overview_ids or ["ev_2"]},
        ],
        "key_points": [
            {"statement": "要点陈述。", "evidence_ids": key_ids or ["ev_1"]},
        ],
        "section_guides": [
            {
                "section_key": guide_section,
                "heading_path": list(guide_heading),
                "summary": "章节导读摘要。",
                "evidence_ids": guide_ids or ["ev_1"],
            }
        ],
        "limitations": [
            {"statement": "限制条目。", "evidence_ids": limit_ids or ["ev_2"]},
        ],
    }
    if with_model_coverage:
        payload["coverage"] = [
            {"section_key": guide_section, "status": "skipped", "skipped_reason": "模型谎称"}
        ]
    return payload


# ---------------------------------------------------------------------------
# Spec: Schema v2，移除模型 coverage，不保留 v1 分支
# ---------------------------------------------------------------------------


def test_brief_schema_v2_and_prompt_v4() -> None:
    """Schema 仍为 v2；prompt 已随 repair patch v4 升到 brief-prompt-v4。"""
    assert BRIEF_SCHEMA_VERSION == 2
    assert BRIEF_PROMPT_VERSION == "brief-prompt-v4"


def test_v2_payload_has_no_coverage_model_field() -> None:
    brief = parse_brief_payload(json.dumps(_v2_payload(), ensure_ascii=False))
    assert isinstance(brief, BriefPayload)
    assert "coverage" not in BriefPayload.model_fields


def test_parse_ignores_model_returned_coverage_field() -> None:
    """模型若仍返回 coverage 字段，v2 解析必须忽略；派生结果只认实际 citation。"""
    brief = parse_brief_payload(
        json.dumps(_v2_payload(with_model_coverage=True), ensure_ascii=False)
    )
    statuses = derive_section_coverage(
        brief,
        [_ev("ev_1", ("概述",)), _ev("ev_2", ("概述",))],
    )
    assert {s.section_key: s.status for s in statuses} == {"概述": "covered"}


def test_parse_rejects_v1_schema_version() -> None:
    payload = _v2_payload()
    payload["schema_version"] = 1
    with pytest.raises(BriefSchemaError):
        parse_brief_payload(json.dumps(payload, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Spec: derive_section_coverage 基于实际 citation + 章节归属
# ---------------------------------------------------------------------------


def test_derive_all_text_sections_covered() -> None:
    brief = parse_brief_payload(
        json.dumps(
            _v2_payload(
                overview_ids=["ev_1"],
                key_ids=["ev_2"],
                guide_ids=["ev_1"],
                limit_ids=["ev_2"],
            ),
            ensure_ascii=False,
        )
    )
    statuses = derive_section_coverage(
        brief,
        [_ev("ev_1", ("概述",)), _ev("ev_2", ("概述",))],
    )
    assert {s.section_key: s.status for s in statuses} == {"概述": "covered"}


def test_derive_citing_other_section_does_not_cover_current() -> None:
    """引用其他章节 Evidence 不能让当前章节 covered。"""
    brief = parse_brief_payload(
        json.dumps(
            _v2_payload(
                overview_ids=["ev_a1"],
                key_ids=["ev_a1"],
                guide_ids=["ev_a1"],
                limit_ids=["ev_a1"],
                guide_section="A",
                guide_heading=("A",),
            ),
            ensure_ascii=False,
        )
    )
    statuses = derive_section_coverage(
        brief,
        [_ev("ev_a1", ("A",)), _ev("ev_b1", ("B",))],
    )
    status_map = {s.section_key: s.status for s in statuses}
    assert status_map["A"] == "covered"
    assert status_map["B"] == "missing"


def test_derive_assets_only_section_is_skipped() -> None:
    """纯 asset 章节由程序标 skipped，不要求模型生成事实。"""
    brief = parse_brief_payload(
        json.dumps(
            _v2_payload(
                overview_ids=["ev_t"],
                key_ids=["ev_t"],
                guide_ids=["ev_t"],
                limit_ids=["ev_t"],
            ),
            ensure_ascii=False,
        )
    )
    statuses = derive_section_coverage(
        brief,
        [_ev("ev_t", ("正文",)), _ev("ev_img", ("附图",), kind="asset")],
    )
    status_map = {s.section_key: s.status for s in statuses}
    assert status_map["附图"] == "skipped"
    assert status_map["正文"] == "covered"


def test_derive_text_section_without_actual_citation_is_missing() -> None:
    """含文本 Evidence 的章节缺少实际 citation → missing。"""
    brief = parse_brief_payload(
        json.dumps(
            _v2_payload(
                overview_ids=["ev_a1"],
                key_ids=["ev_a1"],
                guide_ids=["ev_a1"],
                limit_ids=["ev_a1"],
                guide_section="A",
                guide_heading=("A",),
            ),
            ensure_ascii=False,
        )
    )
    statuses = derive_section_coverage(
        brief,
        [_ev("ev_a1", ("A",)), _ev("ev_c1", ("C",))],
    )
    assert {s.section_key: s.status for s in statuses}["C"] == "missing"


def test_derive_section_guide_key_alone_does_not_cover_other_section() -> None:
    """section guide 声明 section_key=X 但 evidence 来自 Y → X 仍 missing。

    覆盖红线"只声明 section guide key 也不能通过"。
    """
    payload = _v2_payload(
        overview_ids=["ev_y"],
        key_ids=["ev_y"],
        guide_ids=["ev_y"],
        limit_ids=["ev_y"],
        guide_section="X",
        guide_heading=("X",),
    )
    brief = parse_brief_payload(json.dumps(payload, ensure_ascii=False))
    statuses = derive_section_coverage(
        brief,
        [_ev("ev_x", ("X",)), _ev("ev_y", ("Y",))],
    )
    status_map = {s.section_key: s.status for s in statuses}
    # guide 的 evidence 属于 Y，Y covered；X 的 evidence 没被引用 → missing
    assert status_map["Y"] == "covered"
    assert status_map["X"] == "missing"


# ---------------------------------------------------------------------------
# Spec: validate_brief_against_evidence 程序门禁
# ---------------------------------------------------------------------------


def test_validate_fails_when_text_section_missing_citation() -> None:
    brief = parse_brief_payload(
        json.dumps(
            _v2_payload(
                overview_ids=["ev_a1"],
                key_ids=["ev_a1"],
                guide_ids=["ev_a1"],
                limit_ids=["ev_a1"],
                guide_section="A",
                guide_heading=("A",),
            ),
            ensure_ascii=False,
        )
    )
    evidence = [_ev("ev_a1", ("A",)), _ev("ev_b1", ("B",))]
    plan = build_section_coverage_plan(evidence)
    report = validate_brief_against_evidence(
        brief, evidence_rows=evidence, expected_sections=plan
    )
    assert not report.coverage_ok
    assert any("B" in issue for issue in report.issues)


def test_validate_passes_when_all_text_sections_actually_cited() -> None:
    brief = parse_brief_payload(
        json.dumps(
            _v2_payload(
                overview_ids=["ev_a1"],
                key_ids=["ev_b1"],
                guide_ids=["ev_a1"],
                limit_ids=["ev_b1"],
                guide_section="A",
                guide_heading=("A",),
            ),
            ensure_ascii=False,
        )
    )
    evidence = [_ev("ev_a1", ("A",)), _ev("ev_b1", ("B",))]
    plan = build_section_coverage_plan(evidence)
    report = validate_brief_against_evidence(
        brief, evidence_rows=evidence, expected_sections=plan
    )
    assert report.citation_ok
    assert report.coverage_ok


def test_validate_catches_fabricated_citation() -> None:
    brief = parse_brief_payload(json.dumps(_v2_payload(), ensure_ascii=False))
    evidence = [_ev("ev_1", ("概述",)), _ev("ev_2", ("概述",))]
    plan = build_section_coverage_plan(evidence)
    report = validate_brief_against_evidence(
        brief, evidence_rows=evidence, expected_sections=plan
    )
    assert report.citation_ok
    brief.overview[0].evidence_ids.append("ev_FAKE")
    report = validate_brief_against_evidence(
        brief, evidence_rows=evidence, expected_sections=plan
    )
    assert not report.citation_ok
    assert any("ev_FAKE" in issue for issue in report.issues)


def test_validate_ignores_model_coverage_payload_when_present() -> None:
    """模型返回 coverage 字段（v2 不允许）不影响程序派生 coverage 判定。"""
    brief = parse_brief_payload(
        json.dumps(_v2_payload(with_model_coverage=True), ensure_ascii=False)
    )
    evidence = [_ev("ev_1", ("概述",)), _ev("ev_2", ("概述",))]
    plan = build_section_coverage_plan(evidence)
    report = validate_brief_against_evidence(
        brief, evidence_rows=evidence, expected_sections=plan
    )
    assert report.coverage_ok


# ---------------------------------------------------------------------------
# Spec: generation / repair prompt 移除 coverage，要求 atomic statement
# ---------------------------------------------------------------------------


def test_generation_prompt_drops_coverage_output_and_asks_atomic_statements() -> None:
    evidence = [_ev("ev_1", ("概述",)), _ev("ev_2", ("概述",))]
    plan = build_section_coverage_plan(evidence)
    messages = build_generation_prompt(
        source_title="测试 Source",
        evidence_rows=evidence,
        coverage_plan=plan,
    )
    system = messages[0]["content"]
    user = messages[1]["content"]
    # 不再要求模型输出 coverage 字段
    assert '"coverage"' not in system
    # overview 禁止无 citation 支持事实
    assert "overview" in system.lower() or "概述" in system
    # atomic statement：key point/limitation/section guide summary 单一核心断言
    assert "单一" in system or "一个核心断言" in system or "原子" in system
    # Schema v2
    assert "2" in system
    # Evidence 仍注入
    assert "ev_1" in user


def test_generation_prompt_overview_forbids_unsupported_facts() -> None:
    evidence = [_ev("ev_1", ("概述",))]
    plan = build_section_coverage_plan(evidence)
    messages = build_generation_prompt(
        source_title="T", evidence_rows=evidence, coverage_plan=plan
    )
    system = messages[0]["content"]
    # overview 禁止加入 citations 未直接支持的事实/因果/建议
    assert "概述" in system or "overview" in system.lower()
    assert "禁止" in system or "不得" in system


def test_repair_prompt_requests_structured_patch_without_coverage() -> None:
    """KBR-06：repair prompt 要求结构化 patch（replace/delete/split），不输出 coverage。"""
    evidence = [_ev("ev_1", ("概述",))]
    plan = build_section_coverage_plan(evidence)
    candidate = parse_brief_payload(json.dumps(_v2_payload(), ensure_ascii=False))
    issue = ValidationIssue(
        block_path="overview[0]",
        issue_type=ISSUE_CITATION_MISSING,
        decision="",
        reason="引用 ev_FAKE 不存在",
        evidence_ids=["ev_FAKE"],
    )
    messages = build_repair_prompt(
        source_title="T",
        evidence_rows=evidence,
        coverage_plan=plan,
        candidate=candidate,
        failed_issues=[issue],
        failed_block_paths=["overview[0]"],
    )
    system = messages[0]["content"]
    user = messages[1]["content"]
    # patch prompt 要求结构化 patch，不输出 coverage 字段。
    assert '"coverage"' not in system
    assert "patch" in system
    assert "replace" in system and "split" in system and "delete" in system
    # 失败 block 与原因进入结构化失败报告。
    assert "overview[0]" in user
    assert "引用 ev_FAKE 不存在" in user


# ---------------------------------------------------------------------------
# Spec: __document__ 顶层章节与 coverage plan 一致
# ---------------------------------------------------------------------------


def test_derive_document_toplevel_section_covered() -> None:
    """文档顶层（空 heading_path → __document__）章节被实际引用时 covered。"""
    brief = parse_brief_payload(
        json.dumps(
            _v2_payload(
                overview_ids=["ev_1"],
                key_ids=["ev_2"],
                guide_ids=["ev_1"],
                limit_ids=["ev_2"],
                guide_section="__document__",
                guide_heading=(),
            ),
            ensure_ascii=False,
        )
    )
    statuses = derive_section_coverage(
        brief,
        [_ev("ev_1", ()), _ev("ev_2", ())],
    )
    status_map = {s.section_key: s.status for s in statuses}
    assert status_map["__document__"] == "covered"


# ---------------------------------------------------------------------------
# 集成：worker 无正文 block / schema outdated / seam 伪造 coverage 不能绕过门禁
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

from offerpilot.config import AIProviderProfile, Config  # noqa: E402
from offerpilot.knowledge.brief import BRIEF_MIN_CONTEXT_WINDOW  # noqa: E402
from offerpilot.knowledge.repository import BriefAttemptCreateInput  # noqa: E402
from offerpilot.knowledge.service import KnowledgeIngestService  # noqa: E402

from _knowledge_seam import (  # noqa: E402
    RoleAwareModelClient,
    build_supported_brief_json,
    drive_brief_queue,
    ingest_and_extract,
)


def _qualified_config() -> Config:
    provider = AIProviderProfile(
        id="default",
        label="Default",
        provider="openai",
        api_key="sk-test",
        base_url="https://example.com",
        model="gpt-test",
        enabled=True,
        context_window=BRIEF_MIN_CONTEXT_WINDOW,
        max_output_tokens=4096,
    )
    return Config(api_key="sk-test", providers=[provider], active_provider_id="default")


def test_worker_blocks_brief_when_no_text_evidence(tmp_path: Path) -> None:
    """KBR-04：无文本 Evidence 的 Source 不发 generation，使用稳定 block 语义。"""
    # frontmatter-only：KBR-02 排除 frontmatter，无正文 text Evidence。
    content = b"---\ntitle: only meta\nauthor: t\n---\n"
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, content, config=_qualified_config()
    )
    evidence = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50).items
    assert all(item.kind != "text" for item in evidence), "测试前置：无 text evidence"

    client = RoleAwareModelClient(generation=[])
    outcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )
    # 不发 generation 请求
    assert client.count("generation") == 0
    source = outcome.source
    assert source is not None
    # 稳定 block 语义（非 failed），便于用户识别"需补充正文"。
    assert source.brief_status == "pending"
    assert source.brief_block_reason == "brief_no_text_evidence"
    assert outcome.brief is None
    # Source 保持 extracted/Evidence 可用。
    assert source.extraction_status == "extracted"


def test_schema_version_v2_marks_v1_brief_outdated(tmp_path: Path) -> None:
    """KBR-04：schema_version 升级使旧 v1 Brief 正确标记 outdated。"""
    content = "# 概述\n\nSource 描述 OfferPilot。\n\n## 第二段\n\n另一段 Evidence。\n".encode(
        "utf-8"
    )
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, content, config=_qualified_config()
    )
    evidence = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50).items
    # 手动 commit 一个 v1 Attempt/Brief（模拟旧版本产物）。
    attempt, job_id, token = repository.create_brief_attempt(
        BriefAttemptCreateInput(
            source_id=source_id,
            snapshot_id=snapshot_id,
            provider_id="default",
            provider_model="m",
            provider_base_url="",
            context_window=BRIEF_MIN_CONTEXT_WINDOW,
            max_output_tokens=4096,
            prompt_version="brief-prompt-v1",
            schema_version=1,
            language=BRIEF_LANGUAGE,
        )
    )
    repository.commit_brief_attempt_success(
        attempt.id,
        job_id=job_id,
        attempt_token=token,
        payload_json=build_supported_brief_json(evidence),
        validation_report_json="{}",
    )
    service = KnowledgeIngestService(
        repository, tmp_path, session_factory, config=_qualified_config()
    )
    source_after = service.refresh_brief_outdated(source_id)
    assert source_after is not None
    # 当前 BRIEF_SCHEMA_VERSION=2，旧 v1 Brief 标 outdated。
    assert source_after.brief_status == "outdated"
    brief = repository.get_source_brief(source_id)
    assert brief is not None
    assert brief.outdated is True


def test_prompt_version_change_alone_marks_brief_outdated(tmp_path: Path) -> None:
    """KBR-04 nit #2：仅 prompt_version 变化（schema_version 保持当前）也标记 outdated。

    隔离单维度：``test_schema_version_v2_marks_v1_brief_outdated`` 同时改了 prompt_version
    与 schema_version，本用例固定 schema_version 为当前值，只把 prompt_version 设成旧值，
    证明 outdated 判定不依赖 schema_version 同时变化。
    """
    content = "# 概述\n\nSource 描述 OfferPilot。\n\n## 第二段\n\n另一段 Evidence。\n".encode(
        "utf-8"
    )
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, content, config=_qualified_config()
    )
    evidence = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50).items
    attempt, job_id, token = repository.create_brief_attempt(
        BriefAttemptCreateInput(
            source_id=source_id,
            snapshot_id=snapshot_id,
            provider_id="default",
            provider_model="m",
            provider_base_url="",
            context_window=BRIEF_MIN_CONTEXT_WINDOW,
            max_output_tokens=4096,
            prompt_version="brief-prompt-v1",  # 旧 prompt；schema_version 保持当前
            schema_version=BRIEF_SCHEMA_VERSION,
            language=BRIEF_LANGUAGE,
        )
    )
    repository.commit_brief_attempt_success(
        attempt.id,
        job_id=job_id,
        attempt_token=token,
        payload_json=build_supported_brief_json(evidence),
        validation_report_json="{}",
    )
    service = KnowledgeIngestService(
        repository, tmp_path, session_factory, config=_qualified_config()
    )
    source_after = service.refresh_brief_outdated(source_id)
    assert source_after is not None
    assert source_after.brief_status == "outdated"
    brief = repository.get_source_brief(source_id)
    assert brief is not None
    assert brief.outdated is True


def test_seam_model_coverage_field_cannot_bypass_coverage_gate(tmp_path: Path) -> None:
    """KBR-04：模型返回 coverage 字段谎称 covered，但缺实际 citation 仍被门禁拒。"""
    content = (
        "# 概述\n\nSource 描述 OfferPilot 架构。\n\n"
        "## 第二段\n\n另一章节的独立 Evidence。\n"
    ).encode("utf-8")
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, content, config=_qualified_config()
    )
    evidence = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50).items
    section_eids: dict[str, list[str]] = {}
    for item in evidence:
        path = tuple(item.heading_path or ())
        key = "__document__" if not path else " / ".join(path)
        section_eids.setdefault(key, []).append(item.id)
    sections = list(section_eids.keys())
    if len(sections) < 2:
        pytest.skip("需要多 section source 验证 coverage 门禁")
    first_section_eid = section_eids[sections[0]][0]
    # 只引用第一个 section 的 Evidence，其余 section 有文本 Evidence 但未被引用；
    # 同时附带模型 coverage 字段谎称所有章节 covered。
    lying_payload: dict[str, Any] = {
        "schema_version": 2,
        "language": "zh-CN",
        "overview": [
            {"statement": "概述一。", "evidence_ids": [first_section_eid]},
            {"statement": "概述二。", "evidence_ids": [first_section_eid]},
        ],
        "key_points": [{"statement": "要点。", "evidence_ids": [first_section_eid]}],
        "section_guides": [
            {
                "section_key": sections[0],
                "heading_path": sections[0].split(" / "),
                "summary": "导读。",
                "evidence_ids": [first_section_eid],
            }
        ],
        "limitations": [
            {"statement": "限制。", "evidence_ids": [first_section_eid]}
        ],
        "coverage": [
            {"section_key": s, "status": "covered", "skipped_reason": ""}
            for s in sections
        ],
    }
    lying_json = json.dumps(lying_payload, ensure_ascii=False)
    client = RoleAwareModelClient(generation=[lying_json], repair=[lying_json])
    outcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )
    # 程序按实际 citation 派生 coverage：未被引用的章节判 missing → coverage 门禁拒绝。
    assert outcome.attempt is not None
    assert outcome.attempt.status == "failed"
    assert outcome.brief is None
