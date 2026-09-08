"""KBR-08：固化真实 ``@Async`` Brief 失败为最高层回归案例。

Spec（2026-07-15 Evidence 元数据过滤与 Brief 修复设计）Testing Decisions / Further Notes：
- 最高测试 seam 从 Imported Source 原始字节经 Extraction → Evidence/FTS → Brief generation →
  逐条 validation → 单次 repair patch → 最终 Brief/Attempt 持久化；真实 ``@Async`` 失败案例
  必须在这个 seam 回放，而不是只测 prompt helper。
- 关键反例：「元数据 coverage 先消耗 repair，真实 support 问题随后无额度」——实现必须保留
  跨阶段视角，证明 KBR-02~06 闭环：元数据不生成 Evidence、coverage 不消耗 repair、repair
  收到完整问题、最终结果只在全部 supported 后发布。

本文件用一份**脱敏虚构**的 Spring ``@Async`` / ``@EnableAsync`` 技术笔记（保留案例关键结构：
frontmatter/tags、来源作者+图片壳、Obsidian/Evernote 资源残片、阅读信息、导航、多章节技术
正文含 ``@EnableAsync`` 等陈述、citation 选错、复合 partial statement），经 KBR-01 seam 注入
确定性模型响应，从字节到最终持久化完整回放，断言全部红线。不提交私有原文或无授权完整原文。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from offerpilot.config import AIProviderProfile, Config
from offerpilot.knowledge.brief import (
    BRIEF_LANGUAGE,
    BRIEF_REPAIR_PATCH_VERSION,
    BRIEF_SCHEMA_VERSION,
    BRIEF_MIN_CONTEXT_WINDOW,
    ISSUE_CITATION_OWNERSHIP,
    ISSUE_COVERAGE_MISSING,
    ISSUE_SUPPORT_PARTIAL,
)
from offerpilot.knowledge.extractor import (
    METADATA_EXTRACTION_VERSION,
    MarkdownExtractor,
)
from offerpilot.knowledge.evidence_policy import EVIDENCE_POLICY_VERSION

from _knowledge_seam import (
    BriefRunOutcome,
    RoleAwareModelClient,
    drive_brief_queue,
    ingest_and_extract,
)


# ---------------------------------------------------------------------------
# 安全 fixture：脱敏虚构 Spring @Async / @EnableAsync 技术笔记
# ---------------------------------------------------------------------------
#
# 保留真实 @Async 案例的关键结构（frontmatter/tags、来源作者+图片壳、Obsidian/Evernote 资源
# 残片、阅读信息、导航、多章节技术正文含 @EnableAsync 陈述），但全部为虚构脱敏内容，使用
# 独特占位 token 便于精确断言；不包含任何私有 secret 或无授权完整原文。

# 噪声 token（验证不进 Evidence / FTS）。
_BYLINE_TOKEN = "AsyncBylineUnique"
_READING_NUMBER = "7777"
_NAV_TOKEN = "async-nav-unique"
_IMAGE_SHELL_TOKEN = "async-pixel-unique"
_OBSIDIAN_TOKEN = "AsyncObsidianUnique"
_EVERNOTE_TOKEN = "AsyncEvernoteUnique"
_FRONTMATTER_TAG_TOKEN = "asyncprivatetag"  # frontmatter tags 字段值，不进领域模型

# 正文 token（每章一个独特 token，验证可召回 + 精确 citation 定位）。
_BODY_TERM = "AsyncSpringBodyUnique"  # 概述章节
_ENABLE_ASYNC_TERM = "EnableAsyncNoteUnique"  # 启用异步章节（@EnableAsync）
_ASYNC_METHOD_TERM = "AsyncMethodNoteUnique"  # 标注方法章节（@Async）
_THREADPOOL_TERM = "ThreadPoolNoteUnique"  # 线程池配置章节
_EXCEPTION_TERM = "ExceptionHandlerUnique"  # 异常处理章节

# frontmatter URL（provenance URL，不进 Evidence FTS）。
_PROVENANCE_URL = "https://example.com/async-spring"


def _async_source_bytes() -> bytes:
    """脱敏 @Async Source：frontmatter/tags + 作者卡 + 阅读数 + 导航 + 图片壳 +
    Obsidian embed + Evernote 残片 + 5 章技术正文（含 @EnableAsync / @Async 陈述）。"""

    return (
        "---\n"
        "title: '@Async 与 @EnableAsync 用法笔记'\n"
        f"author: {_BYLINE_TOKEN}\n"
        f"tags: [spring, async, {_FRONTMATTER_TAG_TOKEN}]\n"
        f"url: {_PROVENANCE_URL}\n"
        "date: 2026-07-15\n"
        "---\n\n"
        f"作者：{_BYLINE_TOKEN}\n\n"
        f"阅读：{_READING_NUMBER} 次\n\n"
        f"[上一篇](/{_NAV_TOKEN}) [下一篇](/async-next)\n\n"
        f"![]({_IMAGE_SHELL_TOKEN}.png)\n\n"
        f"![[{_OBSIDIAN_TOKEN}.png]]\n\n"
        f'<en-media type="image/png" hash="{_EVERNOTE_TOKEN}"/>\n\n'
        "# 概述\n\n"
        f"OfferPilot 使用 {_BODY_TERM} 作为单一事实源。\n\n"
        "## 启用异步\n\n"
        f"@EnableAsync 注解 {_ENABLE_ASYNC_TERM} 开启方法级异步支持。\n\n"
        "## 标注方法\n\n"
        f"@Async 注解 {_ASYNC_METHOD_TERM} 让 Bean 方法在线程池异步执行。\n\n"
        "## 线程池配置\n\n"
        f"ThreadPoolTaskExecutor {_THREADPOOL_TERM} 配置核心线程数。\n\n"
        "## 异常处理\n\n"
        f"AsyncUncaughtExceptionHandler {_EXCEPTION_TERM} 回调处理异步抛出。\n"
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _qualified_config() -> Config:
    """满足 Brief 96K context 的 Provider 配置，供 seam 复用。"""
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


def _ev_id_by_term(evidence: list[Any], term: str) -> str:
    """按 search_text 中的独特 token 唯一定位 evidence id。"""
    matches = [str(ev.id) for ev in evidence if term in (ev.search_text or "")]
    assert len(matches) == 1, f"term {term!r} 应唯一匹配一条 evidence，实际 {len(matches)}"
    return matches[0]


def _find_other_source_evidence_id(repository: Any, exclude_source_id: int) -> str:
    """找一条不属于 exclude_source_id 的文本 Evidence id（用于 citation ownership）。"""
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


# ===========================================================================
# 1. Extraction 层安全性：元数据不生成 Evidence，正文可回读，provenance 提取
# ===========================================================================


def test_async_fixture_filters_metadata_preserves_body_and_provenance() -> None:
    """脱敏 fixture：噪声块不进 evidence_drafts；正文 offset 可从完整 canonical 回读；
    canonical 保留噪声原文；provenance 白名单字段提取，tags 不进领域模型。"""
    extractor = MarkdownExtractor()
    result = extractor.extract(
        _async_source_bytes().decode("utf-8"), origin_url=_PROVENANCE_URL
    )
    searches = [d.search_text for d in result.evidence_drafts]
    excerpts = [d.canonical_excerpt for d in result.evidence_drafts]

    # 噪声独有 token 不进任何 Evidence 的 search_text / excerpt。
    for noise in (
        _BYLINE_TOKEN,
        _READING_NUMBER,
        _NAV_TOKEN,
        _IMAGE_SHELL_TOKEN,
        _OBSIDIAN_TOKEN,
        _EVERNOTE_TOKEN,
        _FRONTMATTER_TAG_TOKEN,
    ):
        assert not any(noise in s for s in searches), f"噪声 {noise} 泄漏进 Evidence search_text"
        assert not any(noise in e for e in excerpts)

    # 正文独有 token（含 @EnableAsync / @Async 章节术语）保留为 Evidence。
    for body_term in (
        _BODY_TERM,
        _ENABLE_ASYNC_TERM,
        _ASYNC_METHOD_TERM,
        _THREADPOOL_TERM,
        _EXCEPTION_TERM,
    ):
        assert any(body_term in s for s in searches), f"正文 token {body_term} 应保留"

    # canonical 保留 frontmatter 与噪声原文（过滤不改写原件）。
    canonical = result.canonical_text
    assert canonical.startswith("---\n")
    for noise in (_BYLINE_TOKEN, _READING_NUMBER, _NAV_TOKEN, _IMAGE_SHELL_TOKEN,
                  _OBSIDIAN_TOKEN, _EVERNOTE_TOKEN):
        assert noise in canonical

    # 正文 Evidence 的 char/line offset 严格对齐完整 canonical（含噪声原文），可精确回读。
    lines = canonical.split("\n")
    for draft in result.evidence_drafts:
        assert canonical[draft.char_start:draft.char_end] == draft.canonical_excerpt
        assert 1 <= draft.line_start <= draft.line_end <= len(lines)

    # provenance 白名单提取；tags / 未知字段不进领域模型。
    provenance = result.provenance
    assert provenance.title == "@Async 与 @EnableAsync 用法笔记"
    assert provenance.author == _BYLINE_TOKEN
    assert provenance.url == _PROVENANCE_URL
    assert "tags" not in provenance.fields_hit
    for field in ("title", "author", "url", "published_time"):
        assert field in provenance.fields_hit


def test_async_fixture_structure_manifest_records_filter_stats() -> None:
    """结构摘要记录 6 类噪声规则命中 + provenance 字段 + 元数据/policy 版本；不复制被过滤正文。"""
    extractor = MarkdownExtractor()
    result = extractor.extract(
        _async_source_bytes().decode("utf-8"), origin_url=_PROVENANCE_URL
    )
    manifest = json.loads(result.structure_manifest)
    assert manifest["evidence_policy_version"] == EVIDENCE_POLICY_VERSION
    assert manifest["metadata_extraction_version"] == METADATA_EXTRACTION_VERSION
    expected_by_rule = {
        "author_byline": 1,
        "reading_count": 1,
        "navigation": 1,
        "decorative_image_shell": 1,
        "obsidian_wiki_embed": 1,
        "evernote_resource_fragment": 1,
    }
    assert manifest["filtered_by_rule"] == expected_by_rule
    assert manifest["filtered_block_total"] == 6
    # 摘要不重复保存被过滤正文 / URL / 作者名。
    manifest_text = result.structure_manifest
    for leak in (_BYLINE_TOKEN, _IMAGE_SHELL_TOKEN, _OBSIDIAN_TOKEN, _EVERNOTE_TOKEN,
                 _NAV_TOKEN, _PROVENANCE_URL):
        assert leak not in manifest_text, f"摘要泄漏被过滤正文 {leak}"


# ===========================================================================
# 2. 最高层 seam（Extraction）：噪声不进 FTS，provenance 可见但不进 FTS/support
# ===========================================================================


def test_async_seam_metadata_excluded_provenance_visible_body_searchable(
    tmp_path: Path,
) -> None:
    """正式 Ingest → Extraction queue：tags/作者卡/阅读/导航/图片壳/embed/Evernote 不进 Evidence FTS；
    provenance 在 Source 详情可见但不参与普通召回；正文 Evidence 可召回。"""
    repository, _, source_id, snapshot_id = ingest_and_extract(
        tmp_path,
        _async_source_bytes(),
        config=_qualified_config(),
        origin_url=_PROVENANCE_URL,
        import_method="paste",
    )

    # 噪声独有 token 零召回（不进 Evidence FTS）。
    for noise in (
        _BYLINE_TOKEN,
        _READING_NUMBER,
        _NAV_TOKEN,
        _IMAGE_SHELL_TOKEN,
        _OBSIDIAN_TOKEN,
        _EVERNOTE_TOKEN,
        _FRONTMATTER_TAG_TOKEN,
    ):
        assert not repository.search_evidence(noise, limit=5), f"噪声 {noise} 不应被召回"

    # frontmatter URL 不进 Evidence FTS（provenance 不参与正文召回）。
    assert not repository.search_evidence(_PROVENANCE_URL, limit=5)

    # 正文独有 token（含 @EnableAsync / @Async 章节）可召回。
    for body_term in (_BODY_TERM, _ENABLE_ASYNC_TERM, _ASYNC_METHOD_TERM,
                      _THREADPOOL_TERM, _EXCEPTION_TERM):
        assert repository.search_evidence(body_term, limit=5), f"正文 {body_term} 应可召回"

    # provenance 在 Source 详情可见（沿 Source 所有权，不进 FTS/support）。
    source = repository.get_source(source_id)
    assert source is not None
    assert source.author == _BYLINE_TOKEN
    assert source.display_title == "@Async 与 @EnableAsync 用法笔记"
    origins = repository.list_origins(source_id)
    assert origins and origins[0].origin_url == _PROVENANCE_URL


# ===========================================================================
# 3. 核心回放：citation ownership + 复合 partial + 伪造 coverage + coverage missing
#    → 全部进同一 report → 最多一次 repair → patch 只修失败 block（为 @EnableAsync 选当前
#    Source 更直接 Evidence + 复合 split）→ repair 后逐条复验全 supported + coverage 完整 → ready
# ===========================================================================


def test_async_replay_aggregated_one_repair_picks_direct_evidence_then_ready(
    tmp_path: Path,
) -> None:
    """真实 @Async 失败回放（Spec 关键反例）：

    首轮合法候选同时含 citation ownership（overview[1] 误引其他 Source）+ 复合 partial
   （key_points[0]）+ 模型伪造 coverage 字段 + coverage missing（启用异步 / 线程池配置章节
    未被引用）。断言：

    - 模型伪造 coverage 字段被程序忽略，coverage 由实际 citation 派生（首轮 missing）。
    - 三类问题（citation_ownership / support_partial / coverage_missing）全部进入同一 report。
    - 整个质量流程最多一次 repair，repair 在汇总完成后发起（不抢占）。
    - repair patch 只修改失败 block：replace overview[1] 为 @EnableAsync 选当前 Source 更直接
      Evidence；split 复合 key_points[0] 为原子项。
    - repair 后逐条复验全 supported + coverage 完整 → ready，repair_count=1。
    """
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path,
        _async_source_bytes(),
        config=_qualified_config(),
        origin_url=_PROVENANCE_URL,
        import_method="paste",
    )
    evidence = repository.list_evidence(
        source_id, snapshot_id=snapshot_id, limit=50
    ).items
    # 按独特 token 定位各章节 evidence（不依赖数据库自增 id）。
    ev_overview = _ev_id_by_term(evidence, _BODY_TERM)
    ev_enable_async = _ev_id_by_term(evidence, _ENABLE_ASYNC_TERM)
    ev_async_method = _ev_id_by_term(evidence, _ASYNC_METHOD_TERM)
    ev_threadpool = _ev_id_by_term(evidence, _THREADPOOL_TERM)
    ev_exception = _ev_id_by_term(evidence, _EXCEPTION_TERM)

    # 第二个 Source：取得不属于主 Source 的 Evidence id（citation ownership）。
    ingest_and_extract(
        tmp_path,
        "# 其他资料\n\n其他 Source 的正文 Evidence unique-other。\n".encode("utf-8"),
        config=_qualified_config(),
    )
    other_ev = _find_other_source_evidence_id(repository, source_id)
    assert other_ev, "夹具需要至少一条属于其他 Source 的 Evidence"

    # 首轮候选：模型违反 v2 契约伪造 coverage 字段 + citation ownership + 复合 partial
    # + coverage missing（启用异步 / 线程池配置章节未被任何 block 引用）。
    first_payload: dict[str, Any] = {
        "schema_version": BRIEF_SCHEMA_VERSION,
        "language": BRIEF_LANGUAGE,
        "overview": [
            {"statement": "Source 涉及 OfferPilot 架构。", "evidence_ids": [ev_overview]},
            # overview[1] 陈述关于 @EnableAsync；citation 误引其他 Source（ownership），同时
            # 带本 Source 的 ev_enable_async（有效 citation，使原块可定章节「启用异步」）。
            # Finding 5：replace 须落回原块有效 citation 章节范围。
            {"statement": "@EnableAsync 开启异步但 citation 选错。", "evidence_ids": [other_ev, ev_enable_async]},
        ],
        "key_points": [
            # 复合陈述（含多事实），引用标注方法章节 Evidence → Validator 判 partial。
            {"statement": "复合陈述含多事实与推论。", "evidence_ids": [ev_async_method, ev_threadpool]},
        ],
        "section_guides": [
            {
                "section_key": "概述",
                "heading_path": ["概述"],
                "summary": "概述导读。",
                "evidence_ids": [ev_overview],
            }
        ],
        "limitations": [
            # limitations 引用概述 Evidence，使「异常处理」章节首轮无人引用 -> coverage_missing。
            {"statement": "与概述相关的限制。", "evidence_ids": [ev_overview]}
        ],
    }
    # 模型伪造 coverage 字段：谎称「异常处理」已 covered。程序必须忽略，按实际 citation 派生
    # （异常处理首轮 missing）。
    first_payload["coverage"] = [
        {"section_key": "概述 / 异常处理", "status": "covered", "skipped_reason": "模型谎称"}
    ]
    brief_json = json.dumps(first_payload, ensure_ascii=False)

    # repair patch：只修改失败 block（overview[1] 与 key_points[0]）。
    # - replace overview[1] → @EnableAsync 当前 Source 更直接 Evidence（修复 ownership +
    #   覆盖「启用异步」章节）。
    # - split 复合 key_points[0] → 两条原子（分别覆盖「标注方法」「线程池配置」章节）。
    patch_json = json.dumps(
        {
            "version": BRIEF_REPAIR_PATCH_VERSION,
            "operations": [
                {
                    "block_path": "overview[1]",
                    "action": "replace",
                    "payload": {
                        "statement": "@EnableAsync 注解开启方法级异步支持。",
                        "evidence_ids": [ev_enable_async],
                    },
                },
                {
                    "block_path": "key_points[0]",
                    "action": "split",
                    "payload": [
                        {"statement": "@Async 方法异步执行。", "evidence_ids": [ev_async_method]},
                        {"statement": "线程池配置核心线程数。", "evidence_ids": [ev_threadpool]},
                    ],
                },
                # coverage_missing（异常处理）：模型只提交摘要，程序派生 section 身份并
                # 引用 ev_exception（Finding 1）。section 已在 plan，非新增主题。
                {
                    "block_path": "coverage[概述 / 异常处理]",
                    "action": "upsert_section_guide",
                    "payload": {"summary": "异常处理章节导读。"},
                },
            ],
        },
        ensure_ascii=False,
    )

    # 首轮 validation（4 个有效 block：overview[0] / key_points[0] / section_guides[0] /
    # limitations[0]；overview[1] 因 citation ownership 不调 Validator）：
    # supported / partial / supported / supported。repair 后复验用 seam 默认 supported。
    supported = json.dumps({"decision": "supported", "reason": "ok"})
    partial = json.dumps({"decision": "partial", "reason": "复合陈述"})
    client = RoleAwareModelClient(
        generation=[brief_json],
        repair=[patch_json],
        validation=[supported, partial, supported, supported],
    )
    outcome: BriefRunOutcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )

    # --- 红线：首轮三类问题全进同一 report ---
    # ready 后 attempt.validation_report_json 是 winning（复验通过）的 report，首轮失败
    # report 不在此保留；首轮 report 的完整性（含 citation/support/coverage 三类 issue_type
    # 与伪造 coverage 被忽略）由 test_async_replay_first_round_report_contains_all_failure_types
    # 在失败路径单独固化。本测试用调用顺序证明汇总 repair 语义（见下方）。

    # --- 红线：最多一次 repair，repair 在汇总完成后发起（不抢占）---
    assert client.count("generation") == 1
    assert client.count("repair") == 1
    roles = client.role_sequence()
    repair_index = roles.index("repair")
    # repair 之前完成首批 validation（citation 无效 block 跳过 Validator）。
    assert roles[0] == "generation"
    assert "validation" in roles[:repair_index]
    # repair 之后是第二批 validation（patch 复验）。
    assert roles[repair_index + 1 :] == ["validation"] * (
        len(roles) - repair_index - 1
    )

    # --- 红线：repair 后逐条复验全 supported + coverage 完整 → ready ---
    assert outcome.source is not None
    assert outcome.source.brief_status == "ready", outcome.source.brief_error_message
    assert outcome.brief is not None
    assert outcome.attempt is not None
    assert outcome.attempt.status == "succeeded"
    assert outcome.attempt.repair_count == 1

    # --- 红线：repair patch 只修失败 block；为 @EnableAsync 选当前 Source 更直接 Evidence ---
    patched = json.loads(outcome.brief.payload_json)
    assert patched["overview"][1]["evidence_ids"] == [ev_enable_async]
    exception_guide = next(
        guide
        for guide in patched["section_guides"]
        if guide["section_key"] == "概述 / 异常处理"
    )
    assert exception_guide["evidence_ids"] == [ev_exception]
    # --- 红线：复合 statement 被 split 为原子项（key_points ≥ 2） ---
    assert len(patched["key_points"]) >= 2

    # --- 红线：repair 后 coverage 完整（5 章节全 covered，由实际 citation 派生）---
    covered_sections = set()
    cited_ids: set[str] = set()
    for field in ("overview", "key_points", "limitations"):
        for item in patched[field]:
            cited_ids.update(item["evidence_ids"])
    for guide in patched["section_guides"]:
        cited_ids.update(guide["evidence_ids"])
    # 每个章节代表 token 的 evidence 被 patch 后候选实际引用。
    for term in (_BODY_TERM, _ENABLE_ASYNC_TERM, _ASYNC_METHOD_TERM,
                 _THREADPOOL_TERM, _EXCEPTION_TERM):
        ev_id = _ev_id_by_term(evidence, term)
        assert ev_id in cited_ids, f"章节 {term} 的 Evidence 未被 patch 后候选引用（coverage 不完整）"
        covered_sections.add(term)
    assert len(covered_sections) == 5


def test_async_replay_first_round_report_contains_all_failure_types(
    tmp_path: Path,
) -> None:
    """首轮失败 report 含全部三类 issue_type（citation_ownership + support_partial +
    coverage_missing），证明汇总式单次 repair 收到完整反馈。

    与 ``test_async_replay_aggregated_one_repair_picks_direct_evidence_then_ready`` 配对：
    那条证明 repair 成功路径，本条用空 patch 固化「repair 后仍失败」时的首轮完整 report，
    避免首轮 report 被 winning report 覆盖后无法断言。
    """
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path,
        _async_source_bytes(),
        config=_qualified_config(),
        origin_url=_PROVENANCE_URL,
        import_method="paste",
    )
    evidence = repository.list_evidence(
        source_id, snapshot_id=snapshot_id, limit=50
    ).items
    ev_overview = _ev_id_by_term(evidence, _BODY_TERM)
    ev_async_method = _ev_id_by_term(evidence, _ASYNC_METHOD_TERM)
    ev_exception = _ev_id_by_term(evidence, _EXCEPTION_TERM)

    ingest_and_extract(
        tmp_path,
        "# 其他资料\n\n其他 Source 的正文 Evidence unique-other-2。\n".encode("utf-8"),
        config=_qualified_config(),
    )
    other_ev = _find_other_source_evidence_id(repository, source_id)
    assert other_ev

    first_payload: dict[str, Any] = {
        "schema_version": BRIEF_SCHEMA_VERSION,
        "language": BRIEF_LANGUAGE,
        "overview": [
            {"statement": "概述 OfferPilot 架构。", "evidence_ids": [ev_overview]},
            {"statement": "@EnableAsync citation 选错。", "evidence_ids": [other_ev]},
        ],
        "key_points": [
            {"statement": "复合陈述含多事实。", "evidence_ids": [ev_async_method]},
        ],
        "section_guides": [
            {
                "section_key": "概述",
                "heading_path": ["概述"],
                "summary": "概述导读。",
                "evidence_ids": [ev_overview],
            }
        ],
        "limitations": [
            {"statement": "异常处理限制。", "evidence_ids": [ev_exception]}
        ],
    }
    # 模型伪造 coverage 字段：谎称「启用异步」「线程池配置」已 covered。程序必须忽略，
    # 仍按实际 citation 派生 → coverage missing（这两个章节未被任何 block 引用）。
    first_payload["coverage"] = [
        {"section_key": "概述 / 启用异步", "status": "covered", "skipped_reason": "模型谎称"},
        {"section_key": "概述 / 线程池配置", "status": "covered", "skipped_reason": "模型谎称"},
    ]
    brief_json = json.dumps(first_payload, ensure_ascii=False)

    supported = json.dumps({"decision": "supported", "reason": "ok"})
    partial = json.dumps({"decision": "partial", "reason": "复合"})
    # 两轮 validation 共用 supported/partial 序列；空 patch 后候选不变，第二轮仍 partial + coverage missing。
    from _knowledge_seam import EMPTY_REPAIR_PATCH

    client = RoleAwareModelClient(
        generation=[brief_json],
        repair=[EMPTY_REPAIR_PATCH],
        validation=[supported, partial, supported, supported] * 2,
    )
    outcome: BriefRunOutcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )

    # Attempt failed（repair 后仍 partial + coverage missing）。
    assert outcome.attempt is not None
    assert outcome.attempt.status == "failed"
    assert outcome.attempt.repair_count == 1
    assert outcome.source is not None
    assert outcome.source.brief_status == "failed"
    assert outcome.brief is None  # 不能发布

    # 完整结构化 report 含全部三类 issue_type。
    report = outcome.validation_report
    issue_types = {item.get("issue_type") for item in report.get("issues", [])}
    assert ISSUE_CITATION_OWNERSHIP in issue_types
    assert ISSUE_SUPPORT_PARTIAL in issue_types
    assert ISSUE_COVERAGE_MISSING in issue_types
    assert report.get("error_code") == "brief_quality_failed"
    assert report.get("failure_count") == len(report.get("issues", []))
    assert report.get("repair_count") == 1
    # report 不复制 Evidence 正文。
    report_text = json.dumps(report, ensure_ascii=False)
    assert _BODY_TERM not in report_text


# ===========================================================================
# 4. 反例：repair 后仍 partial → Attempt failed + 完整详情可见 + Evidence 继续可搜索
# ===========================================================================


def test_async_replay_repair_still_partial_attempt_failed_evidence_searchable(
    tmp_path: Path,
) -> None:
    """repair 应用成功但复验仍 partial → Attempt failed；完整结构化详情可见；
    Evidence 继续可搜索（Brief 失败不破坏 Source 可检索性）。"""
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path,
        _async_source_bytes(),
        config=_qualified_config(),
        origin_url=_PROVENANCE_URL,
        import_method="paste",
    )
    evidence = repository.list_evidence(
        source_id, snapshot_id=snapshot_id, limit=50
    ).items
    ev_overview = _ev_id_by_term(evidence, _BODY_TERM)
    ev_enable_async = _ev_id_by_term(evidence, _ENABLE_ASYNC_TERM)
    ev_async_method = _ev_id_by_term(evidence, _ASYNC_METHOD_TERM)
    ev_exception = _ev_id_by_term(evidence, _EXCEPTION_TERM)

    # 用 build_supported_brief_json 构造合法全覆盖候选，再制造首轮 overview[0] partial。
    from _knowledge_seam import build_supported_brief_json, expected_validation_count

    base_payload = json.loads(build_supported_brief_json(evidence))
    # 用真实章节 evidence 填充，确保除被注入 partial 的 block 外其余有效。
    base_payload["overview"][0]["evidence_ids"] = [ev_overview]
    base_payload["overview"][1]["evidence_ids"] = [ev_enable_async]
    base_payload["key_points"][0]["evidence_ids"] = [ev_async_method]
    if base_payload.get("limitations"):
        base_payload["limitations"][0]["evidence_ids"] = [ev_exception]
    brief_json = json.dumps(base_payload, ensure_ascii=False)

    block_count = expected_validation_count(brief_json)
    # patch：replace overview[0] 收缩为原子陈述（引用原 evidence，coverage 不变）。
    patch_json = json.dumps(
        {
            "version": BRIEF_REPAIR_PATCH_VERSION,
            "operations": [
                {
                    "block_path": "overview[0]",
                    "action": "replace",
                    "payload": {
                        "statement": "收缩后的单一原子断言。",
                        "evidence_ids": [ev_overview],
                    },
                }
            ],
        },
        ensure_ascii=False,
    )
    # 两轮 validation 全 partial（repair 后仍失败）。
    partial = json.dumps({"decision": "partial", "reason": "仍复合"})
    client = RoleAwareModelClient(
        generation=[brief_json],
        repair=[patch_json],
        validation=[partial] * (block_count * 2),
    )
    outcome: BriefRunOutcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=_qualified_config(),
        model_client=client,
        source_id=source_id,
    )

    # Attempt failed + repair_count=1 + 完整结构化详情。
    assert outcome.attempt is not None
    assert outcome.attempt.status == "failed"
    assert outcome.attempt.repair_count == 1
    assert outcome.brief is None
    report = outcome.validation_report
    assert report.get("repair_count") == 1
    issue_types = {item.get("issue_type") for item in report.get("issues", [])}
    assert ISSUE_SUPPORT_PARTIAL in issue_types
    assert report.get("failure_count") == len(report.get("issues", []))

    # Evidence 继续可搜索（Brief 失败不破坏 Source 可检索性）。
    assert repository.search_evidence(_BODY_TERM, limit=5)
    assert repository.search_evidence(_ENABLE_ASYNC_TERM, limit=5)
    source = repository.get_source(source_id)
    assert source is not None
    # Source 仍 extracted（Evidence 可用），brief_failed 不影响 Extraction 状态。
    assert source.extraction_status == "extracted"
