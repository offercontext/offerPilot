"""KBR-05 汇总质量失败 + 完整 Attempt 报告测试。

Spec Implementation Decisions（核心）：
- Schema 合法时不按首个失败抢占 repair；系统先运行所有可执行的 citation、support 和
  coverage 检查，再统一生成 repair 输入。
- citation 无效的 block 不调用 support Validator，但其 citation 问题进入统一 repair report；
  其他引用有效的 block 继续完成 support validation。
- validation report 结构化保存全部 block、decision、reason 和 evidence IDs；Source 状态
  只显示稳定 error code、总数和短摘要。
- 同一候选同时含 citation、coverage 和 support 问题时只发起一次 repair，repair 输入包含
  全部已发现问题。

本文件覆盖 tickets.md KBR-05 全部验收复选框，分三层：
1. 纯函数（brief.py）：per-block citation 检查、结构化 issue、coverage 入 report。
2. seam 集成（worker 全流程）：汇总 repair、调用顺序、单次 repair、Source 摘要、Attempt 详情。
3. citation ownership：跨 Source 引用被识别为 citation_ownership 而非 missing。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from offerpilot.knowledge.brief import (
    ISSUE_CITATION_MISSING,
    ISSUE_CITATION_OWNERSHIP,
    ISSUE_COVERAGE_MISSING,
    ISSUE_SCHEMA_INVALID,
    ISSUE_SUPPORT_CONTRADICTED,
    ISSUE_SUPPORT_PARTIAL,
    ISSUE_SUPPORT_UNSUPPORTED,
    BRIEF_LANGUAGE,
    BRIEF_SCHEMA_VERSION,
    ValidationIssue,
    build_section_coverage_plan,
    parse_brief_payload,
    validate_brief_against_evidence,
)

from _knowledge_seam import (
    BriefRunOutcome,
    EMPTY_REPAIR_PATCH,
    RoleAwareModelClient,
    drive_brief_queue,
    ingest_and_extract,
)

from _kbr05_helpers import (
    MIXED_FAILURE_CONTENT,
    build_mixed_failure_brief_json,
    extract_repair_issues,
    mixed_failure_valid_block_count,
)


# ---------------------------------------------------------------------------
# 1. 纯函数：per-block citation 检查 + 结构化 issue + coverage 入 report
# ---------------------------------------------------------------------------


def _ev(
    evidence_id: str,
    heading_path: tuple[str, ...] = (),
    kind: str = "text",
) -> Any:
    return SimpleNamespace(
        id=evidence_id,
        heading_path=list(heading_path),
        kind=kind,
        canonical_excerpt="原文片段-不可泄露",
        search_text="alt 文本" if kind == "asset" else "",
    )


def _payload(
    *,
    overview_ids: list[str] | None = None,
    key_ids: list[str] | None = None,
    guide_ids: list[str] | None = None,
    limit_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
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
                "section_key": "概述",
                "heading_path": ["概述"],
                "summary": "章节导读摘要。",
                "evidence_ids": guide_ids or ["ev_1"],
            }
        ],
        "limitations": [
            {"statement": "限制条目。", "evidence_ids": limit_ids or ["ev_2"]},
        ],
    }


def test_check_citations_returns_per_block_valid_invalid_evidence() -> None:
    """Schema 合法时程序先算全部 citation 问题，per-block 标出 valid/invalid evidence。"""
    brief = parse_brief_payload(
        json.dumps(
            _payload(
                overview_ids=["ev_1"],
                key_ids=["ev_FAKE"],  # citation missing
                guide_ids=["ev_1"],
                limit_ids=["ev_2"],
            ),
            ensure_ascii=False,
        )
    )
    evidence = [_ev("ev_1", ("概述",)), _ev("ev_2", ("概述",))]
    plan = build_section_coverage_plan(evidence)
    report = validate_brief_against_evidence(
        brief, evidence_rows=evidence, expected_sections=plan
    )
    blocks = {block.block_path: block for block in report.citation_blocks}
    # overview[0] / section_guides[0] 引用 ev_1 有效。
    assert "ev_1" in blocks["overview[0]"].valid_evidence_ids
    assert not blocks["overview[0]"].invalid_evidence_ids
    # key_points[0] 引用 ev_FAKE 无效。
    assert "ev_FAKE" in blocks["key_points[0]"].invalid_evidence_ids
    assert not blocks["key_points[0]"].valid_evidence_ids
    assert not report.citation_ok


def test_validation_report_includes_structured_coverage_statuses() -> None:
    """KBR-04 nit #1：report 结构化保存 per-section coverage status（covered/skipped/missing）。"""
    # 章节 A 的 evidence 被引用，章节 B 的 evidence 不被任何 block 引用。
    brief = parse_brief_payload(
        json.dumps(
            _payload(
                overview_ids=["ev_a"],
                key_ids=["ev_a"],
                guide_ids=["ev_a"],
                limit_ids=["ev_a"],
                # guide 默认 section_key="概述"；下面单独构造让 section 归属章节 A
            ),
            ensure_ascii=False,
        )
    )
    # 用章节 A 的 guide 覆盖默认，确保 ev_a 属于章节 A。
    brief.section_guides[0].section_key = "A"
    brief.section_guides[0].heading_path = ["A"]
    evidence = [_ev("ev_a", ("A",)), _ev("ev_b", ("B",))]
    plan = build_section_coverage_plan(evidence)
    report = validate_brief_against_evidence(
        brief, evidence_rows=evidence, expected_sections=plan
    )
    statuses = {s.section_key: s.status for s in report.coverage_statuses}
    assert statuses.get("A") == "covered"
    assert statuses.get("B") == "missing"  # 含文本 Evidence 但未被引用
    assert not report.coverage_ok


def test_validation_issue_type_taxonomy_covers_all_categories() -> None:
    """issue_type 枚举能区分 Schema、citation missing/ownership、support 系列与 coverage missing。"""
    all_types = {
        ISSUE_SCHEMA_INVALID,
        ISSUE_CITATION_MISSING,
        ISSUE_CITATION_OWNERSHIP,
        ISSUE_SUPPORT_PARTIAL,
        ISSUE_SUPPORT_UNSUPPORTED,
        # contradicted 也属于 support 失败枚举（与 partial/unsupported 同级）。
    }
    assert ISSUE_COVERAGE_MISSING not in all_types  # 独立分类
    assert all_types  # 非空
    # 每种 issue_type 都能构造合法 ValidationIssue。
    for issue_type in (
        ISSUE_CITATION_MISSING,
        ISSUE_CITATION_OWNERSHIP,
        ISSUE_SUPPORT_PARTIAL,
        ISSUE_SUPPORT_UNSUPPORTED,
        ISSUE_COVERAGE_MISSING,
    ):
        issue = ValidationIssue(
            block_path="overview[0]",
            issue_type=issue_type,
            decision="partial" if issue_type == ISSUE_SUPPORT_PARTIAL else "",
            reason="测试原因",
            evidence_ids=["ev_1"],
        )
        assert issue.issue_type == issue_type


def test_validation_issue_carries_block_path_decision_reason_evidence_ids() -> None:
    """每条报告项至少包含 block path、issue type、decision、reason 和 evidence IDs。"""
    issue = ValidationIssue(
        block_path="key_points[1]",
        issue_type=ISSUE_CITATION_MISSING,
        decision="",
        reason="引用了未知 Evidence ev_FAKE",
        evidence_ids=["ev_FAKE"],
    )
    assert issue.block_path == "key_points[1]"
    assert issue.evidence_ids == ["ev_FAKE"]


# ---------------------------------------------------------------------------
# 2. seam 集成：汇总 repair + 调用顺序 + Source 摘要 + Attempt 详情
# ---------------------------------------------------------------------------


def _qualified_config() -> Any:
    from offerpilot.config import AIProviderProfile, Config
    from offerpilot.knowledge.brief import BRIEF_MIN_CONTEXT_WINDOW

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


class _RepairCapturingClient(RoleAwareModelClient):
    """:class:`RoleAwareModelClient` 子类，额外捕获每次 repair 调用收到的 issue 行。

    只记录 ``build_repair_prompt`` 的「校验失败原因」段（issue_type 摘要），不含
    Evidence 正文或完整 Prompt（Spec 隐私边界）。
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.repair_issue_batches: list[list[str]] = []

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
        if "Repair Agent" in system_text:
            self.repair_issue_batches.append(extract_repair_issues(user_text))
        return super().__call__(**payload)


def test_mixed_failures_aggregated_into_single_repair(tmp_path: Path) -> None:
    """同一候选同时含 citation missing + support partial + coverage missing：

    - 报告完整（三类失败项全部进入 validation_report_json）。
    - 只发起一次 repair（汇总后单次）。
    - repair 输入含全部已发现问题。
    - citation 无效的 block 不调 Validator（validation 调用数 = 有效 block 数 × 轮次）。
    """
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, MIXED_FAILURE_CONTENT.encode("utf-8"), config=_qualified_config()
    )
    evidence = repository.list_evidence(
        source_id, snapshot_id=snapshot_id, limit=50
    ).items
    mixed_brief_json = build_mixed_failure_brief_json(evidence)
    valid_block_count = mixed_failure_valid_block_count(evidence)
    # 有效 block 逐条 support：首轮 supported/partial/supported/supported（最后一 block 视构造而定）。
    # 这里只保证「恰有一条 partial、其余 supported」用于触发 support_partial。
    validation_batch: list[Any] = []
    for index in range(valid_block_count):
        decision = "partial" if index == 1 else "supported"
        validation_batch.append(json.dumps({"decision": decision, "reason": "测试"}))
    # 两轮（repair 前后）共用同样序列。
    client = _RepairCapturingClient(
        generation=[mixed_brief_json],
        repair=[EMPTY_REPAIR_PATCH],  # KBR-06：空 patch 应用后候选不变，第二轮仍失败
        validation=validation_batch * 2,
    )
    outcome: BriefRunOutcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )

    # 只发起一次 repair（即使三类问题并存）。
    assert client.count("generation") == 1
    assert client.count("repair") == 1
    # citation 无效的 block 不调 Validator：validation 次数 = 有效 block 数 × 2 轮。
    assert client.count("validation") == valid_block_count * 2

    # repair 在首批 validation 之后（汇总完成后才 repair，不抢占）。
    roles = client.role_sequence()
    repair_index = roles.index("repair")
    assert roles[:repair_index] == ["generation"] + ["validation"] * valid_block_count
    assert roles[repair_index + 1 :] == ["validation"] * valid_block_count

    # repair 输入含全部三类问题（KBR-06 patch prompt 以 JSON 结构化失败报告呈现）。
    assert len(client.repair_issue_batches) == 1
    repair_issues = client.repair_issue_batches[0]
    issue_type_set = set(repair_issues)
    assert ISSUE_CITATION_MISSING in issue_type_set
    assert ISSUE_SUPPORT_PARTIAL in issue_type_set
    assert ISSUE_COVERAGE_MISSING in issue_type_set

    # Attempt failed、Source brief_failed、旧 Brief 不存在（首次构建）。
    assert outcome.attempt is not None
    assert outcome.attempt.status == "failed"
    assert outcome.source is not None
    assert outcome.source.brief_status == "failed"

    # 结构化 report 含全部失败项 + 稳定 error code + 失败总数。
    report = outcome.validation_report
    assert report.get("error_code") == "brief_quality_failed"
    issues = report.get("issues", [])
    issue_types = {item.get("issue_type") for item in issues}
    assert ISSUE_CITATION_MISSING in issue_types
    assert ISSUE_SUPPORT_PARTIAL in issue_types
    assert ISSUE_COVERAGE_MISSING in issue_types
    failure_count = report.get("failure_count")
    assert failure_count is not None
    assert failure_count == len(issues)
    assert failure_count >= 3


def test_citation_invalid_block_skips_validator_valid_blocks_continue(
    tmp_path: Path,
) -> None:
    """citation 无效的 block 不发起 Validator；其余有效 block 仍逐条 support validation。"""
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, MIXED_FAILURE_CONTENT.encode("utf-8"), config=_qualified_config()
    )
    evidence = repository.list_evidence(
        source_id, snapshot_id=snapshot_id, limit=50
    ).items
    mixed_brief_json = build_mixed_failure_brief_json(evidence)
    valid_block_count = mixed_failure_valid_block_count(evidence)
    # 全部 supported，仅 citation missing + coverage missing 触发 repair；repair 后仍失败。
    supported = json.dumps({"decision": "supported", "reason": "ok"})
    client = _RepairCapturingClient(
        generation=[mixed_brief_json],
        repair=[EMPTY_REPAIR_PATCH],
        validation=[supported] * (valid_block_count * 2),
    )
    outcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )
    # citation 无效 block 不调 Validator：次数正好 = 有效 block × 2。
    assert client.count("validation") == valid_block_count * 2
    report = outcome.validation_report
    issue_types = {item.get("issue_type") for item in report.get("issues", [])}
    assert ISSUE_CITATION_MISSING in issue_types
    assert ISSUE_COVERAGE_MISSING in issue_types
    # citation 无效 block 不会产生 support_xxx issue（没调 Validator）。
    assert not any(
        t.startswith("support_") for t in issue_types if t is not None
    )


def test_schema_unparseable_still_immediate_repair(tmp_path: Path) -> None:
    """JSON/Schema 完全无法解析时保留立即 repair（后续门禁无法安全运行）。"""
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, MIXED_FAILURE_CONTENT.encode("utf-8"), config=_qualified_config()
    )
    invalid_json = "this is not json"
    good_evidence = repository.list_evidence(
        source_id,
        snapshot_id=snapshot_id,
        limit=50,
    ).items
    from _knowledge_seam import build_supported_brief_json

    good_brief = build_supported_brief_json(good_evidence)
    client = RoleAwareModelClient(
        generation=[invalid_json],  # 首轮 Schema 不可解析 → 立即 repair
        repair=[good_brief],  # repair 后合法
    )
    outcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )
    # Schema 不可解析立即 repair：repair 之前没有任何 validation 调用。
    roles = client.role_sequence()
    assert roles[0] == "generation"
    assert "repair" in roles
    repair_index = roles.index("repair")
    assert "validation" not in roles[:repair_index]
    # repair 后合法 brief 走完整汇总校验（含 support validation），最终 ready。
    assert outcome.source is not None
    assert outcome.source.brief_status == "ready"


def test_support_partial_is_hard_failure(tmp_path: Path) -> None:
    """support 结果只有 supported 才通过；partial 是硬失败，不能发布。"""
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, MIXED_FAILURE_CONTENT.encode("utf-8"), config=_qualified_config()
    )
    evidence = repository.list_evidence(
        source_id, snapshot_id=snapshot_id, limit=50
    ).items
    from _knowledge_seam import build_supported_brief_json

    brief_json = build_supported_brief_json(evidence)
    val_count = _count_statement_blocks(brief_json)
    partial = json.dumps({"decision": "partial", "reason": "部分推断"})
    client = RoleAwareModelClient(
        generation=[brief_json],
        repair=[EMPTY_REPAIR_PATCH],
        validation=[partial] * (val_count * 2),
    )
    outcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )
    assert outcome.source is not None
    assert outcome.source.brief_status == "failed"
    assert outcome.brief is None  # 不能发布
    report = outcome.validation_report
    issue_types = {item.get("issue_type") for item in report.get("issues", [])}
    assert ISSUE_SUPPORT_PARTIAL in issue_types


def test_support_contradicted_is_hard_failure(tmp_path: Path) -> None:
    """support 返回 contradicted 是硬失败：所引 Evidence 直接否定 statement 核心。

    与 partial 对称（Spec §10.3 仅 supported 可发布）；issue_type 区分为
    support_contradicted，decision='contradicted'，且候选 Brief 不发布。
    """
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, MIXED_FAILURE_CONTENT.encode("utf-8"), config=_qualified_config()
    )
    evidence = repository.list_evidence(
        source_id, snapshot_id=snapshot_id, limit=50
    ).items
    from _knowledge_seam import build_supported_brief_json

    brief_json = build_supported_brief_json(evidence)
    val_count = _count_statement_blocks(brief_json)
    contradicted = json.dumps(
        {"decision": "contradicted", "reason": "所引 Evidence 直接否定该 statement"}
    )
    client = RoleAwareModelClient(
        generation=[brief_json],
        repair=[EMPTY_REPAIR_PATCH],
        validation=[contradicted] * (val_count * 2),
    )
    outcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )
    assert outcome.source is not None
    assert outcome.source.brief_status == "failed"
    assert outcome.brief is None  # 不能发布
    report = outcome.validation_report
    issues = report.get("issues", [])
    contradicted_issues = [
        item for item in issues if item.get("issue_type") == ISSUE_SUPPORT_CONTRADICTED
    ]
    assert contradicted_issues, "contradicted 必须产生 support_contradicted issue"
    assert all(item.get("decision") == "contradicted" for item in contradicted_issues)


def test_source_status_summary_stable_code_count_short_text(tmp_path: Path) -> None:
    """Source 状态区显示稳定 error code + 失败总数 + 不半句截断的短摘要。"""
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, MIXED_FAILURE_CONTENT.encode("utf-8"), config=_qualified_config()
    )
    evidence = repository.list_evidence(
        source_id, snapshot_id=snapshot_id, limit=50
    ).items
    mixed_brief_json = build_mixed_failure_brief_json(evidence)
    valid_block_count = mixed_failure_valid_block_count(evidence)
    supported = json.dumps({"decision": "supported", "reason": "ok"})
    client = _RepairCapturingClient(
        generation=[mixed_brief_json],
        repair=[EMPTY_REPAIR_PATCH],
        validation=[supported] * (valid_block_count * 2),
    )
    outcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )
    source = outcome.source
    assert source is not None
    assert source.brief_error_code == "brief_quality_failed"
    message = source.brief_error_message
    # 短摘要含失败总数，且是完整句子（不以「；」「、」等连接符结尾）。
    assert message
    assert "条" in message or "失败" in message
    assert not message.endswith(("；", "、", "，", ", ", "; "))
    # 摘要不应把完整 Evidence 正文或具体 issue reason 全文复制进来。
    assert "原文片段-不可泄露" not in message


def test_attempt_report_locates_block_and_evidence(tmp_path: Path) -> None:
    """Attempt/处理记录展示全部失败项，能定位到候选 Brief block 和已引用 Evidence。"""
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, MIXED_FAILURE_CONTENT.encode("utf-8"), config=_qualified_config()
    )
    evidence = repository.list_evidence(
        source_id, snapshot_id=snapshot_id, limit=50
    ).items
    mixed_brief_json = build_mixed_failure_brief_json(evidence)
    valid_block_count = mixed_failure_valid_block_count(evidence)
    supported = json.dumps({"decision": "supported", "reason": "ok"})
    client = _RepairCapturingClient(
        generation=[mixed_brief_json],
        repair=[EMPTY_REPAIR_PATCH],
        validation=[supported] * (valid_block_count * 2),
    )
    outcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )
    report = outcome.validation_report
    issues = report.get("issues", [])
    assert issues, "失败 Attempt 必须保留全部失败项"
    for item in issues:
        assert item.get("block_path")
        assert item.get("issue_type")
        assert isinstance(item.get("evidence_ids"), list)
    # 至少一项指向候选 Brief block path（形如 overview[0]）。
    assert any(re.search(r"\[\d+\]$", item["block_path"]) for item in issues)
    # candidate_payload 与 Attempt 关联（UI 可跳转到 block）。
    attempt = outcome.attempt
    assert attempt is not None
    assert attempt.candidate_payload_json


def test_report_excludes_evidence_body(tmp_path: Path) -> None:
    """失败详情不把完整 Evidence 正文复制进 report 或普通日志。"""
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, MIXED_FAILURE_CONTENT.encode("utf-8"), config=_qualified_config()
    )
    evidence = repository.list_evidence(
        source_id, snapshot_id=snapshot_id, limit=50
    ).items
    mixed_brief_json = build_mixed_failure_brief_json(evidence)
    valid_block_count = mixed_failure_valid_block_count(evidence)
    supported = json.dumps({"decision": "supported", "reason": "ok"})
    client = _RepairCapturingClient(
        generation=[mixed_brief_json],
        repair=[EMPTY_REPAIR_PATCH],
        validation=[supported] * (valid_block_count * 2),
    )
    outcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )
    report_json = json.dumps(outcome.validation_report, ensure_ascii=False)
    # Evidence 正文不进 report。
    for ev in evidence:
        if ev.canonical_excerpt:
            assert ev.canonical_excerpt not in report_json
    # call_log 不含完整 Prompt（seam 已保证），也不含 Evidence 正文。
    for record in client.call_log:
        for ev in evidence:
            if ev.canonical_excerpt:
                assert ev.canonical_excerpt not in record.input_digest


def test_rebuild_failure_preserves_old_brief_and_attaches_report_to_new_attempt(
    tmp_path: Path,
) -> None:
    """重建失败时旧 current Brief 继续可见；失败候选 + 完整 report 归属新 Attempt。"""
    from offerpilot.knowledge.service import KnowledgeIngestService

    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, MIXED_FAILURE_CONTENT.encode("utf-8"), config=_qualified_config()
    )
    evidence = repository.list_evidence(
        source_id, snapshot_id=snapshot_id, limit=50
    ).items
    from _knowledge_seam import build_supported_brief_json

    good_brief = build_supported_brief_json(evidence)
    # 首轮成功：建立 current Brief。
    first = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=RoleAwareModelClient(generation=[good_brief]),
        source_id=source_id,
    )
    assert first.brief is not None
    winning_attempt_id = first.attempt.id if first.attempt else 0

    # 触发显式重建。
    service = KnowledgeIngestService(
        repository, tmp_path, session_factory, config=_qualified_config()
    )
    source_state, message = service.rebuild_brief(source_id)
    assert source_state is not None
    assert message == "brief_rebuild_queued"

    # 重建候选失败：mixed failure。
    mixed_brief_json = build_mixed_failure_brief_json(evidence)
    valid_block_count = mixed_failure_valid_block_count(evidence)
    supported = json.dumps({"decision": "supported", "reason": "ok"})
    rebuild = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=_RepairCapturingClient(
            generation=[mixed_brief_json],
            repair=[EMPTY_REPAIR_PATCH],
            validation=[supported] * (valid_block_count * 2),
        ),
        source_id=source_id,
    )
    # 旧 current Brief 仍可见、ready 保留。
    assert rebuild.attempt is not None
    assert rebuild.attempt.status == "failed"
    assert rebuild.attempt.id != winning_attempt_id  # 归属新 Attempt
    preserved = repository.get_source_brief(source_id)
    assert preserved is not None
    assert preserved.winning_attempt_id == winning_attempt_id
    source = repository.get_source(source_id)
    assert source is not None
    assert source.brief_status == "ready"  # 旧 Brief 保留
    # 失败候选 + 完整 report 归属新 Attempt。
    assert rebuild.attempt.candidate_payload_json
    assert rebuild.attempt.validation_report_json
    assert rebuild.attempt.validation_report_json != "{}"


# ---------------------------------------------------------------------------
# 3. citation ownership：跨 Source 引用识别
# ---------------------------------------------------------------------------


def test_cross_source_citation_classified_as_ownership(tmp_path: Path) -> None:
    """引用其他 Source/Snapshot 的 Evidence 被标为 citation_ownership，而非 missing。"""
    # Source 1：正文含 1 章节，作为主 Source。
    content_primary = "# 主章节\n\n主 Source 正文 Evidence。\n"
    repository, session_factory, source_id_primary, snapshot_primary = (
        ingest_and_extract(
            tmp_path, content_primary.encode("utf-8"), config=_qualified_config()
        )
    )
    primary_evidence = repository.list_evidence(
        source_id_primary, snapshot_id=snapshot_primary, limit=50
    ).items
    assert primary_evidence

    # Source 2：另一 Source，其 Evidence 不属于 Source 1 的 Snapshot。
    content_other = "# 其他章节\n\n其他 Source 正文 Evidence。\n"
    ingest_and_extract(
        tmp_path, content_other.encode("utf-8"), config=_qualified_config()
    )
    # 取 Source 2 的 evidence id。
    # list_evidence 只针对某 source；用 repository.get_evidence 全局查需要 id。
    # 改为直接查数据库找非 Source 1 的文本 evidence。
    other_evidence_id = _find_other_source_evidence_id(repository, source_id_primary)
    assert other_evidence_id, "测试夹具需要至少一条属于其他 Source 的 Evidence"

    # 候选 brief：引用 Source 1 的 evidence（有效）+ Source 2 的 evidence（ownership）。
    primary_ev_id = primary_evidence[0].id
    payload = {
        "schema_version": BRIEF_SCHEMA_VERSION,
        "language": BRIEF_LANGUAGE,
        "overview": [
            {"statement": "主概述。", "evidence_ids": [primary_ev_id]},
            {"statement": "越界引用。", "evidence_ids": [other_evidence_id]},
        ],
        "key_points": [
            {"statement": "主要点。", "evidence_ids": [primary_ev_id]},
        ],
        "section_guides": [
            {
                "section_key": "主章节",
                "heading_path": ["主章节"],
                "summary": "主章节导读。",
                "evidence_ids": [primary_ev_id],
            }
        ],
        "limitations": [
            {"statement": "主限制。", "evidence_ids": [primary_ev_id]},
        ],
    }
    brief_json = json.dumps(payload, ensure_ascii=False)
    supported = json.dumps({"decision": "supported", "reason": "ok"})
    # brief 有 5 个 block，全部 citation 有效（primary_ev_id 在 Source 1；other 在 Source 2
    # 但 get_evidence 能查到 → ownership）。validation 队列给足够 supported。
    client = _RepairCapturingClient(
        generation=[brief_json],
        repair=[EMPTY_REPAIR_PATCH],
        validation=[supported] * 20,
    )
    outcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id_primary,
    )
    report = outcome.validation_report
    issues = report.get("issues", [])
    ownership_issues = [
        item for item in issues if item.get("issue_type") == ISSUE_CITATION_OWNERSHIP
    ]
    assert ownership_issues, "跨 Source 引用必须被识别为 citation_ownership"
    # 越界引用的 evidence id 出现在该项的 evidence_ids。
    assert any(
        other_evidence_id in item.get("evidence_ids", [])
        for item in ownership_issues
    )


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _count_statement_blocks(payload_json: str) -> int:
    payload = json.loads(payload_json)
    return (
        len(payload["overview"])
        + len(payload["key_points"])
        + len(payload["section_guides"])
        + len(payload["limitations"])
    )


def _find_other_source_evidence_id(
    repository: Any, exclude_source_id: int
) -> str:
    """找一条不属于 ``exclude_source_id`` 的文本 Evidence id（用于 ownership 测试）。"""
    # repository 没有跨 source list；用直接 SQL 查询。
    session_factory = repository._session_factory  # type: ignore[attr-defined]
    from offerpilot.models import KnowledgeEvidence

    with session_factory() as session:
        rows = (
            session.query(KnowledgeEvidence)
            .filter(KnowledgeEvidence.source_id != exclude_source_id)
            .filter(KnowledgeEvidence.kind == "text")
            .all()
        )
        if not rows:
            return ""
        return str(rows[0].id)
