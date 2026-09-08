"""KBR-03 过滤已知元数据样板并记录规则统计验收。

Spec（2026-07-15 Evidence 元数据过滤与 Brief 修复设计）Implementation Decisions：
- Evidence eligibility policy 与 Markdown 解析职责分离；规则只决定是否发射 Evidence，
  不修改 canonical text 或 AST 原始位置。
- 全局规则只覆盖低歧义结构（空链接壳、纯装饰图片壳、明确导出控件文本）。
- 平台/导出噪声由明确适配器处理：Obsidian `![[...]]` embed / `%%...%%` 注释、
  Evernote 导出残片、结构化作者署名、阅读信息、翻页导航。
- 不确定块默认生成 Evidence；每条规则有稳定 rule_id、正例和反例。
- Snapshot 结构摘要记录 filtered_block_total、按 rule_id 聚合数量、命中的 provenance
  字段名、metadata extraction version、evidence policy version；不重复保存被过滤正文。
- 被过滤块不进 Evidence/FTS/Brief Prompt；相邻保留 Evidence 的 line/char offsets 不偏移。
- 相同 Source 与 policy version 重跑得到相同 digest、Evidence ID、顺序和过滤统计。
- Evidence 规则变化视为 Extraction 版本变化（policy 变化 -> 新 Snapshot -> 旧 Brief outdated）。

本文件覆盖 policy 单元、extractor 集成、FTS 搜索回归、repository/API 摘要和 KBR-01 seam。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from offerpilot.config import AIProviderProfile, Config
from offerpilot.knowledge.brief import BRIEF_MIN_CONTEXT_WINDOW
from offerpilot.knowledge.evidence_policy import (
    ADAPTER_EVERNOTE,
    ADAPTER_OBSIDIAN,
    ADAPTER_WEB_ARTICLE,
    EVIDENCE_POLICY_VERSION,
    ExtractionContext,
    RULE_LABELS,
    _RULES,
    evaluate_block,
    select_adapters,
)
from offerpilot.knowledge.extractor import (
    EXTRACTOR_VERSION,
    METADATA_EXTRACTION_VERSION,
    MarkdownExtractor,
    _collect_adapter_signal_corpus,
)

from _knowledge_seam import (
    RoleAwareModelClient,
    build_supported_brief_json,
    drive_brief_queue,
    ingest_and_extract,
)


@pytest.fixture
def qualified_config() -> Config:  # type: ignore[no-untyped-def]
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


def _api_client(tmp_path: Path):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from offerpilot.api import create_app

    return TestClient(create_app(data_dir=tmp_path))


# ---------------------------------------------------------------------------
# Spec 验收：policy 与解析职责分离 + 版本号
# ---------------------------------------------------------------------------


def test_evidence_policy_version_stable() -> None:
    """Evidence policy 有稳定版本号；规则变化视为 Extraction 版本变化。"""
    assert EVIDENCE_POLICY_VERSION == "evidence-policy-2"


def test_extractor_version_bumped_for_evidence_policy() -> None:
    """KBR-03 Evidence 规则变化 -> 升级 EXTRACTOR_VERSION，新 Snapshot 身份。"""
    assert EXTRACTOR_VERSION == "md-kbr03-2"
    assert EXTRACTOR_VERSION != "md-kbr02-1"
    assert METADATA_EXTRACTION_VERSION == "provenance-1"


def test_rule_registry_has_stable_ids_and_labels() -> None:
    """每条规则有稳定 rule_id 与面向用户的稳定 label（不暴露正则/实现细节）。"""
    expected = {
        "empty_link_shell",
        "decorative_image_shell",
        "obsidian_wiki_embed",
        "obsidian_comment",
        "evernote_resource_fragment",
        "author_byline",
        "reading_count",
        "navigation",
    }
    assert expected <= set(RULE_LABELS.keys())
    # 反向闭包：_RULES 中每个 rule_id 都必须登记 label，否则 api 层
    # ``RULE_LABELS.get(rule_id, rule_id)`` 会 fallback 把内部 rule_id 当 label 展示。
    rule_ids_in_rules = {rule_id for rule_id, _ in _RULES}
    assert rule_ids_in_rules <= set(RULE_LABELS.keys()), (
        f"rule_id 缺 label 登记：{rule_ids_in_rules - set(RULE_LABELS.keys())}"
    )
    # label 非空、不含正则语法，供普通用户界面展示。
    for rule_id, label in RULE_LABELS.items():
        assert isinstance(label, str) and label
        assert "\\" not in label and "^" not in label and "[" not in label


# ---------------------------------------------------------------------------
# Spec 验收：每条规则正例 + 反例（不确定块默认生成 Evidence）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,rule_id",
    [
        ("[首页]()", "empty_link_shell"),
        ("[](https://example.com)", "empty_link_shell"),
        ("[link]( )", "empty_link_shell"),
        ("![](tracking-pixel.png)", "decorative_image_shell"),
        ("![]()", "decorative_image_shell"),
    ],
)
def test_global_rule_skips_with_empty_context(text: str, rule_id: str) -> None:
    """全局低歧义规则（空链接壳 / 纯装饰图片壳）在无 adapter 上下文下也过滤（Spec 第 71 行）。"""
    decision = evaluate_block(text)
    assert not decision.emit
    assert decision.rule_id == rule_id


@pytest.mark.parametrize(
    "text,rule_id,adapter",
    [
        ("![[Pasted image 1.png]]", "obsidian_wiki_embed", ADAPTER_OBSIDIAN),
        ("![[note.md]]", "obsidian_wiki_embed", ADAPTER_OBSIDIAN),
        ("%% TODO: rewrite later %%", "obsidian_comment", ADAPTER_OBSIDIAN),
        ("%%任意注释内容%%", "obsidian_comment", ADAPTER_OBSIDIAN),
        ("<en-media type=\"image/png\" hash=\"abc\"/>", "evernote_resource_fragment", ADAPTER_EVERNOTE),
        ("<en-note>", "evernote_resource_fragment", ADAPTER_EVERNOTE),
        ("</en-note>", "evernote_resource_fragment", ADAPTER_EVERNOTE),
        ("作者：诸葛孔明丰", "author_byline", ADAPTER_WEB_ARTICLE),
        ("Author: Jane Doe", "author_byline", ADAPTER_WEB_ARTICLE),
        ("阅读：8888", "reading_count", ADAPTER_WEB_ARTICLE),
        ("阅读数：1.2万", "reading_count", ADAPTER_WEB_ARTICLE),
        ("8888 次阅读", "reading_count", ADAPTER_WEB_ARTICLE),
        ("views: 500", "reading_count", ADAPTER_WEB_ARTICLE),
        ("[上一篇](/prev) [下一篇](/next)", "navigation", ADAPTER_WEB_ARTICLE),
        ("← 上一页 | 下一页 →", "navigation", ADAPTER_WEB_ARTICLE),
    ],
)
def test_adapter_rule_skips_only_when_adapter_active(
    text: str, rule_id: str, adapter: str
) -> None:
    """平台规则只在对应 adapter 激活时过滤；空上下文下保留（Spec 第 67/72 行：不确定默认保留）。

    信号隔离：其他 adapter 激活时不得过滤本规则（一个信号不顺带启用别的 adapter）。
    """
    # 空 ctx：不确定块默认保留。
    assert evaluate_block(text).emit
    # 对应 adapter 激活：过滤并返回稳定 rule_id。
    decision = evaluate_block(
        text, ExtractionContext(active_adapters=frozenset({adapter}))
    )
    assert not decision.emit
    assert decision.rule_id == rule_id
    # 其他 adapter 激活时不过滤该规则（信号隔离）。
    others = {ADAPTER_OBSIDIAN, ADAPTER_EVERNOTE, ADAPTER_WEB_ARTICLE} - {adapter}
    for other in others:
        assert evaluate_block(
            text, ExtractionContext(active_adapters=frozenset({other}))
        ).emit, f"adapter {other} 不应顺带过滤 {rule_id}"


def test_select_adapters_uses_deterministic_signals() -> None:
    """select_adapters 按确定性信号选择：结构语法 / 扩展名 / origin_url，互不串扰。"""
    # 无信号 → 空。
    assert select_adapters("普通 Markdown 正文 作者：SQLite 是最佳选择") == frozenset()
    # Obsidian 结构语法 → 仅 obsidian。
    assert select_adapters("参见 ![[note]] 与 %%注释%%") == frozenset({ADAPTER_OBSIDIAN})
    # Evernote 标签 → 仅 evernote。
    assert select_adapters("<en-media hash=\"a\"/>") == frozenset({ADAPTER_EVERNOTE})
    # .enex 扩展名 → 仅 evernote（内容无关）。
    assert select_adapters("plain", filename="note.enex") == frozenset({ADAPTER_EVERNOTE})
    # origin_url → 仅 web_article。
    assert select_adapters("plain", origin_url="https://example.com/a") == frozenset(
        {ADAPTER_WEB_ARTICLE}
    )
    # 三信号同时存在 → 三 adapter 叠加，互不串扰。
    assert select_adapters(
        "![[x]] <en-note/>", origin_url="https://example.com/a"
    ) == frozenset({ADAPTER_OBSIDIAN, ADAPTER_EVERNOTE, ADAPTER_WEB_ARTICLE})


def test_brand_name_and_keyword_are_not_signals() -> None:
    """品牌名 / 正文关键词 / 文件标题不得作为 adapter 信号（Spec KBR-03 第 104 行）。"""
    # 含「Obsidian」「Evernote」字样但无结构语法 / provenance URL → 无 adapter。
    assert select_adapters("本文讨论 Obsidian 与 Evernote 的导出格式") == frozenset()
    assert select_adapters("作者：张三", filename="Obsidian笔记.md") == frozenset()



@pytest.mark.parametrize(
    "text",
    [
        # 真实链接（url 与 text 均非空）保留。
        "[Google](https://google.com)",
        # 有 alt 的真实图片保留。
        "![架构图](arch.png)",
        # 正文里出现的 Obsidian embed（非整块）保留。
        "参见 ![[note]] 的结论",
        # 不以 %% 包裹的百分号文本保留。
        "完成 50% 的工作",
        # en-note 标签内含正文（非纯残片）保留。
        "<en-note>讨论内容</en-note>",
        # 作者出现在正文句子里（无冒号 byline 结构）保留。
        "作者认为 SQLite 是最佳选择",
        # “阅读”出现在正文里（非阅读数结构）保留。
        "阅读以下章节了解细节",
        # 真实外链单链接（非导航词汇）保留。
        "[MySQL 文档](https://dev.mysql.com)",
        # 正文里的 key: value 配置示例保留。
        "port: 8080 是默认端口",
        # 普通技术段落保留。
        "Evidence 是引用单位，Evidence 不重叠。",
    ],
)
def test_rule_negative_keeps_as_evidence(text: str) -> None:
    """反例：不确定或合法正文 -> 默认生成 Evidence（emit, rule_id=None）。"""
    decision = evaluate_block(text)
    assert decision.emit
    assert decision.rule_id is None


@pytest.mark.parametrize(
    "text",
    [
        "作者：SQLite 是最佳选择",
        "作者：张三 是专家",
        "by: Python 3",
        "目录",
    ],
)
def test_byline_and_nav_body_kept_in_plain_markdown(text: str) -> None:
    """Spec 第 35/67 行：普通 Markdown（无 web_article adapter）下，冒号 + 2 token 短句、
    ``by: Python 3`` 配置示例、单独「目录」一律保留为 Evidence——不确定默认保留，正文
    key:value 必须可检索。这些只在 web-article adapter（ingest origin_url）下过滤。"""
    # 无 adapter：保留（修正此前全局误删）。
    assert evaluate_block(text).emit
    # web_article adapter：作为结构化署名 / 阅读数 / 导航样板过滤。
    decision = evaluate_block(
        text, ExtractionContext(active_adapters=frozenset({ADAPTER_WEB_ARTICLE}))
    )
    assert not decision.emit
    assert decision.rule_id in {"author_byline", "reading_count", "navigation"}


def test_author_byline_protects_three_token_body() -> None:
    """对照：冒号 + 3 token 及以上正文句子即使 web_article adapter 激活也保留。"""
    assert evaluate_block("作者：张三 认为 Python 是最佳选择").emit
    assert evaluate_block(
        "作者：张三 认为 Python 是最佳选择",
        ExtractionContext(active_adapters=frozenset({ADAPTER_WEB_ARTICLE})),
    ).emit


def test_non_paragraph_block_kinds_evaluated() -> None:
    """policy 适用于 paragraph / list_item / blockquote；fence/table 不在此层。

    作者署名 / 阅读数在 web_article adapter 下过滤；空上下文下保留。
    """
    web = ExtractionContext(active_adapters=frozenset({ADAPTER_WEB_ARTICLE}))
    assert not evaluate_block("作者：张三", web).emit
    assert not evaluate_block("阅读：1234", web).emit
    assert evaluate_block("作者：张三").emit
    assert evaluate_block("阅读：1234").emit


def test_blockquote_and_list_item_noise_filtered_through_extractor() -> None:
    """extractor 对 blockquote/list_item 的 marker-stripped 文本应用 policy（web-article 来源）。"""
    extractor = MarkdownExtractor()
    content = (
        f"> 阅读：{_READING_NUMBER} 次\n\n"
        f"- 作者：{_BYLINE_TOKEN}\n"
        "- 真实要点保留 SQLite\n"
    )
    result = extractor.extract(content, origin_url="https://example.com/web-article")
    for draft in result.evidence_drafts:
        assert _READING_NUMBER not in draft.search_text
        assert _BYLINE_TOKEN not in draft.search_text
    assert any("真实要点保留 SQLite" in d.search_text for d in result.evidence_drafts)
    manifest = json.loads(result.structure_manifest)
    assert manifest["filtered_by_rule"]["reading_count"] == 1
    assert manifest["filtered_by_rule"]["author_byline"] == 1


# ---------------------------------------------------------------------------
# Spec 验收：代表性 @Async 样本 extractor 集成
# ---------------------------------------------------------------------------


# 噪声独有 token（只出现在被过滤块，验证零召回）。
_BYLINE_TOKEN = "诸葛孔明丰"
_READING_NUMBER = "8888"
_NAV_TOKEN = "prev-unique"
_IMAGE_SHELL_TOKEN = "tracking-pixel"
_OBSIDIAN_TOKEN = "Pasted-Image-Obs"
_EVERNOTE_TOKEN = "en-frag-unique"
# 正文独有 token（验证可召回）。
_BODY_TERM = "SQLiteSSOT决策独有"
_BODY_NUMBER = "8080"
_BODY_URL = "sqlite-unique.example.org"
_BODY_CONFIG = "preserved-in-code"


def _async_representative_source() -> str:
    """代表性 @Async 样本：frontmatter/tags + 作者卡 + 阅读数 + 导航 + 图片壳 +
    Obsidian embed + Evernote 残片 + 真实正文（含配置示例/URL/数字/真实链接/真实图片/代码块）。"""
    return (
        "---\n"
        "title: '@Async 源码笔记'\n"
        f"author: {_BYLINE_TOKEN}\n"
        "tags: [async, spring]\n"
        "url: https://example.com/async\n"
        "date: 2026-07-15\n"
        "---\n\n"
        f"作者：{_BYLINE_TOKEN}\n\n"
        f"阅读：{_READING_NUMBER} 次\n\n"
        f"[上一篇](/{_NAV_TOKEN}) [下一篇](/next-unique)\n\n"
        f"![]({_IMAGE_SHELL_TOKEN}.png)\n\n"
        f"![[{_OBSIDIAN_TOKEN}.png]]\n\n"
        f'<en-media type="image/png" hash="{_EVERNOTE_TOKEN}"/>\n\n'
        "# 概述\n\n"
        f"OfferPilot 使用 {_BODY_TERM} 作为单一事实源。\n\n"
        "## 配置\n\n"
        f"port: {_BODY_NUMBER} 是默认端口。详见 https://{_BODY_URL}/ssot 文档。\n\n"
        "阅读以下章节了解细节。\n\n"
        "[Google](https://google.com)\n\n"
        "![架构图](arch-real.png)\n\n"
        "```yaml\n"
        f"en-note: {_BODY_CONFIG}\n"
        "```\n"
    )


def test_filtered_blocks_excluded_from_evidence_drafts() -> None:
    """代表性样本：噪声块不进 evidence_drafts，正文块保留。"""
    extractor = MarkdownExtractor()
    result = extractor.extract(_async_representative_source(), origin_url="https://example.com/async")
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
    ):
        assert not any(noise in s for s in searches), f"噪声 {noise} 泄漏进 Evidence"
        assert not any(noise in e for e in excerpts)
    # 正文独有 token 仍可检索。
    assert any(_BODY_TERM in s for s in searches)
    assert any(_BODY_NUMBER in s for s in searches)
    assert any(_BODY_URL in s for s in searches)
    assert any(_BODY_CONFIG in s for s in searches)
    # 真实链接与真实图片保留。
    assert any("google.com" in s for s in searches)
    assert any("架构图" in s for s in searches)


def test_canonical_text_preserves_noise_verbatim() -> None:
    """canonical Source 完整保留 frontmatter 与噪声原文，未被改写或删除。"""
    extractor = MarkdownExtractor()
    content = _async_representative_source()
    result = extractor.extract(content)
    canonical = result.canonical_text
    assert canonical.startswith("---\n")
    for noise in (_BYLINE_TOKEN, _READING_NUMBER, _NAV_TOKEN, _IMAGE_SHELL_TOKEN,
                  _OBSIDIAN_TOKEN, _EVERNOTE_TOKEN):
        assert noise in canonical, f"噪声 {noise} 应保留在 canonical 原文"


def test_retained_evidence_offsets_read_back_from_full_canonical() -> None:
    """相邻保留 Evidence 的 line/char offsets 不因过滤偏移，可从完整 canonical 回读。"""
    extractor = MarkdownExtractor()
    result = extractor.extract(_async_representative_source(), origin_url="https://example.com/async")
    lines = result.canonical_text.split("\n")
    assert result.evidence_drafts
    for draft in result.evidence_drafts:
        # char range 严格对齐完整 canonical（含 frontmatter + 噪声原文）。
        assert result.canonical_text[draft.char_start:draft.char_end] == draft.canonical_excerpt
        assert 1 <= draft.line_start <= draft.line_end <= len(lines)


def test_structure_manifest_records_filter_stats() -> None:
    """Snapshot 结构摘要记录 filtered_block_total、按 rule_id 聚合数量、
    provenance 字段名、metadata extraction version、evidence policy version。"""
    extractor = MarkdownExtractor()
    result = extractor.extract(_async_representative_source(), origin_url="https://example.com/async")
    manifest = json.loads(result.structure_manifest)
    assert manifest["evidence_policy_version"] == EVIDENCE_POLICY_VERSION
    assert manifest["metadata_extraction_version"] == METADATA_EXTRACTION_VERSION
    assert manifest["provenance_fields"] == list(result.provenance.fields_hit)
    # 6 个噪声块被过滤：作者卡 / 阅读数 / 导航 / 图片壳 / Obsidian / Evernote。
    expected_by_rule = {
        "author_byline": 1,
        "reading_count": 1,
        "navigation": 1,
        "decorative_image_shell": 1,
        "obsidian_wiki_embed": 1,
        "evernote_resource_fragment": 1,
    }
    assert manifest["filtered_by_rule"] == expected_by_rule
    assert manifest["filtered_block_total"] == sum(expected_by_rule.values())
    # 摘要不重复保存被过滤正文 / URL / 作者名 / 本机路径。
    manifest_text = result.structure_manifest
    for leak in (_BYLINE_TOKEN, _IMAGE_SHELL_TOKEN, _OBSIDIAN_TOKEN, _EVERNOTE_TOKEN,
                 _NAV_TOKEN):
        assert leak not in manifest_text, f"摘要泄漏被过滤正文 {leak}"


def test_repeated_extraction_is_stable() -> None:
    """相同 Source 与 policy version 重跑：digest、Evidence ID、顺序、过滤统计稳定。"""
    extractor = MarkdownExtractor()
    first = extractor.extract(_async_representative_source(), origin_url="https://example.com/async")
    second = extractor.extract(_async_representative_source(), origin_url="https://example.com/async")
    assert first.digest == second.digest
    assert [d.locator for d in first.evidence_drafts] == [
        d.locator for d in second.evidence_drafts
    ]
    assert [d.content_hash for d in first.evidence_drafts] == [
        d.content_hash for d in second.evidence_drafts
    ]
    assert first.structure_manifest == second.structure_manifest


def test_policy_version_in_digest_identity() -> None:
    """evidence_policy_version 进入 structure_manifest/digest 身份；policy 变化 -> digest 变化。"""
    extractor = MarkdownExtractor()
    result = extractor.extract(_async_representative_source(), origin_url="https://example.com/async")
    manifest = json.loads(result.structure_manifest)
    # policy version 在摘要中；摘要参与 digest 计算（见 _snapshot_digest）。
    assert manifest["evidence_policy_version"] == EVIDENCE_POLICY_VERSION
    assert result.digest.startswith("sha256:")


def test_filtered_list_item_does_not_leak_into_nested_child_search_text() -> None:
    """被过滤的 list item 不把噪声文本泄漏到嵌套子项的 search_text（FTS 红线）。"""
    extractor = MarkdownExtractor()
    content = (
        "# 列表\n\n"
        f"- 作者：{_BYLINE_TOKEN}\n"
        "  - 真实子要点 SQLite\n"
        f"- 阅读：{_READING_NUMBER} 次\n"
        "  - 第二个真实子要点\n"
    )
    result = extractor.extract(content, origin_url="https://example.com/web-article")
    # 父级噪声 token 不进任何（子项）Evidence 的 search_text。
    for draft in result.evidence_drafts:
        assert _BYLINE_TOKEN not in draft.search_text
        assert _READING_NUMBER not in draft.search_text
    # 嵌套子项保留为 Evidence。
    assert any("真实子要点 SQLite" in d.search_text for d in result.evidence_drafts)
    assert any("第二个真实子要点" in d.search_text for d in result.evidence_drafts)
    # 两个父级噪声项被过滤。
    manifest = json.loads(result.structure_manifest)
    assert manifest["filtered_by_rule"]["author_byline"] == 1
    assert manifest["filtered_by_rule"]["reading_count"] == 1


# ---------------------------------------------------------------------------
# Spec 验收：repository 集成 -- 噪声零召回，正文可召回
# ---------------------------------------------------------------------------


def test_filtered_noise_not_recalled_but_body_searchable(tmp_path: Path) -> None:
    """搜索回归：被过滤噪声不可召回；正文术语/URL/数字/配置示例仍可召回。"""
    repository, _, source_id, _ = ingest_and_extract(
        tmp_path, _async_representative_source().encode("utf-8"),
        origin_url="https://example.com/async",
        import_method="paste",
    )
    # 噪声独有 token 零召回。
    for noise in (
        _BYLINE_TOKEN,
        _READING_NUMBER,
        _NAV_TOKEN,
        _IMAGE_SHELL_TOKEN,
        _OBSIDIAN_TOKEN,
        _EVERNOTE_TOKEN,
    ):
        assert not repository.search_evidence(noise, limit=5), (
            f"噪声 {noise} 不应被召回"
        )
    # 正文独有 token 可召回。
    assert repository.search_evidence(_BODY_TERM, limit=5)
    assert repository.search_evidence(_BODY_NUMBER, limit=5)
    assert repository.search_evidence(_BODY_URL, limit=5)
    assert repository.search_evidence(_BODY_CONFIG, limit=5)


def test_repository_filter_summary_from_snapshot(tmp_path: Path) -> None:
    """repository 从 active Snapshot 结构摘要读出过滤统计。"""
    repository, _, source_id, _ = ingest_and_extract(
        tmp_path, _async_representative_source().encode("utf-8"),
        origin_url="https://example.com/async",
        import_method="paste",
    )
    summary = repository.get_source_filter_summary(source_id)
    assert summary["filtered_block_total"] == 6
    assert summary["evidence_policy_version"] == EVIDENCE_POLICY_VERSION
    assert summary["filtered_by_rule"]["author_byline"] == 1
    assert summary["filtered_by_rule"]["navigation"] == 1


def test_source_detail_api_exposes_filter_summary(
    tmp_path: Path, qualified_config  # type: ignore[no-untyped-def]
) -> None:
    """Source 详情 API 展示过滤数量与规则摘要（不暴露正则）。"""
    _, _, source_id, _ = ingest_and_extract(
        tmp_path, _async_representative_source().encode("utf-8"),
        origin_url="https://example.com/async",
        import_method="paste",
        config=qualified_config,
    )
    client = _api_client(tmp_path)
    response = client.get(f"/api/knowledge/sources/{source_id}")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["evidence_policy_summary"]
    assert summary["filtered_block_total"] == 6
    assert summary["evidence_policy_version"] == EVIDENCE_POLICY_VERSION
    # 规则摘要含稳定 label（用户可读），不含 rule_id 正则。
    labels = {item["label"] for item in summary["rules"]}
    assert "作者署名" in labels or any("作者" in lab for lab in labels)
    assert all("\\" not in item["label"] for item in summary["rules"])


def test_source_detail_no_filter_summary_when_none(tmp_path: Path) -> None:
    """无过滤的纯正文 Source：不制造空占位噪声。"""
    content = "# 纯正文\n\n这是没有噪声的正文内容。\n"
    _, _, source_id, _ = ingest_and_extract(tmp_path, content.encode("utf-8"))
    client = _api_client(tmp_path)
    response = client.get(f"/api/knowledge/sources/{source_id}")
    assert response.status_code == 200
    payload = response.json()
    # 无过滤时 filtered_block_total 为 0；仍暴露 evidence_policy_version 但不制造规则占位。
    summary = payload.get("evidence_policy_summary")
    assert summary is not None
    assert summary["filtered_block_total"] == 0
    assert summary["rules"] == []


# ---------------------------------------------------------------------------
# Spec 验收：policy version 变化 -> 旧 Brief outdated（新 Snapshot 身份）
# ---------------------------------------------------------------------------


def test_policy_change_via_version_bump_invalidates_old_brief(
    tmp_path: Path, qualified_config  # type: ignore[no-untyped-def]
) -> None:
    """Evidence 规则变化视为 Extraction 版本变化：新 extractor_version 创建新 Snapshot，
    旧 Brief 被标记 outdated，新旧 Snapshot Evidence 不混用。"""
    from offerpilot.knowledge.repository import (
        SnapshotCreateInput,
        commit_extraction,
    )

    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, _async_representative_source().encode("utf-8"),
        origin_url="https://example.com/async",
        import_method="paste",
        config=qualified_config,
    )
    # 构造一个 ready Brief（绑定当前 Snapshot）。
    evidence = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50).items
    brief_json = build_supported_brief_json(evidence)
    client = RoleAwareModelClient(generation=[brief_json])
    outcome = drive_brief_queue(
        repository, session_factory, tmp_path,
        config=qualified_config, model_client=client, source_id=source_id,
    )
    assert outcome.brief is not None
    assert not outcome.brief.outdated
    # 模拟 policy 变化导致 extractor_version 升级：直接用新版本号提交 Extraction。
    new_snapshot_input = SnapshotCreateInput(
        source_id=source_id,
        extractor_version="md-kbr-future-1",
        parser_version="markdown-it-py-3",
        normalization_version="nl-1",
        tokenizer_version="none-1",
        encoding="utf-8",
        detection_method="strict-utf8",
        canonical_text=outcome.evidence[0].canonical_excerpt,
        structure_manifest=json.dumps({"evidence_policy_version": "evidence-policy-2"}),
        digest="sha256:future",
        token_count=10,
        char_count=10,
        metadata_extraction_version=METADATA_EXTRACTION_VERSION,
    )
    with session_factory() as session:
        with session.begin():
            commit_extraction(
                session,
                snapshot_input=new_snapshot_input,
                evidence_drafts=[],
                source_id=source_id,
                source_title="title",
                extractor_version="md-kbr-future-1",
            )
    refreshed = repository.get_source_brief(source_id)
    assert refreshed is not None
    # policy 变化 -> 新 Snapshot -> 旧 Brief outdated（不混用新旧 Evidence）。
    assert refreshed.outdated is True


# ---------------------------------------------------------------------------
# Spec 验收：最高层 seam -- 代表性样本字节 -> Extraction -> Brief
# ---------------------------------------------------------------------------


def test_seam_async_sample_filters_metadata_and_builds_brief(
    tmp_path: Path, qualified_config  # type: ignore[no-untyped-def]
) -> None:
    """KBR-01 seam：代表性 @Async 样本经正式 Extraction，tags/作者卡/图片壳/导航不成为
    Evidence，正文 Evidence 可回读，Brief 成功且只引用正文 Evidence。"""
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, _async_representative_source().encode("utf-8"),
        origin_url="https://example.com/async",
        import_method="paste",
        config=qualified_config,
    )
    evidence = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50).items
    assert evidence, "正文应产出 Evidence"
    # tags / 作者卡 / 阅读数 / 导航 / 图片壳 / embed / evernote 残片均不成为 Evidence。
    for item in evidence:
        for noise in (_BYLINE_TOKEN, _READING_NUMBER, _NAV_TOKEN, _IMAGE_SHELL_TOKEN,
                      _OBSIDIAN_TOKEN, _EVERNOTE_TOKEN):
            assert noise not in item.search_text, (
                f"噪声 {noise} 不应出现在 Evidence search_text"
            )
    # 正文 Evidence 可回读（offset 对齐）。
    source = repository.get_source(source_id)
    assert source is not None
    # canonical_text 由 Source 原文提供；经 /content 接口或 repository 可取。这里用 Evidence
    # excerpt 唯一性证明正文保留：至少存在一条含正文独有 token 的 Evidence。
    assert any(_BODY_TERM in item.search_text for item in evidence)
    # Brief 用正文 Evidence 构造并成功（filtered 块不进 Brief prompt / coverage）。
    brief_json = build_supported_brief_json(evidence)
    client = RoleAwareModelClient(generation=[brief_json])
    outcome = drive_brief_queue(
        repository, session_factory, tmp_path,
        config=qualified_config, model_client=client, source_id=source_id,
    )
    assert outcome.source is not None
    assert outcome.source.brief_status == "ready", outcome.source.brief_error_message
    assert outcome.brief is not None


# ===========================================================================
# 二轮 Review P1-D：教程语法（fence/table/inline code）不得激活平台 adapter
# ===========================================================================


def test_finding_d_signal_corpus_excludes_fence_table_code_inline() -> None:
    """信号语料显式排除 fenced code、table、行内 code——教程示例语法不进语料。"""
    doc = (
        "# 教程\n\n"
        "正文 `![[inline]]` 含行内 code。\n\n"
        "```markdown\n"
        "![[fenced]]\n"
        "%%comment%%\n"
        "```\n\n"
        "| 列 |\n"
        "|----|\n"
        "| <en-media> |\n"
    )
    tokens = MarkdownExtractor()._parser.parse(doc)
    corpus = _collect_adapter_signal_corpus(tokens)
    assert "![[fenced]]" not in corpus
    assert "%%comment%%" not in corpus
    assert "![[inline]]" not in corpus  # 行内 code 内容排除
    assert "<en-media>" not in corpus  # table 单元格排除
    assert "正文" in corpus  # 正文 text 保留


def test_finding_d_fence_syntax_does_not_activate_obsidian() -> None:
    """fenced code 里的 Obsidian 语法不激活 obsidian adapter。"""
    doc = (
        "# 教程\n\n"
        "Obsidian 嵌入示例：\n\n"
        "```markdown\n"
        "![[example.png]]\n"
        "%%这是注释%%\n"
        "```\n\n"
        "正文段落保持中性。\n"
    )
    tokens = MarkdownExtractor()._parser.parse(doc)
    adapters = select_adapters(_collect_adapter_signal_corpus(tokens))
    assert ADAPTER_OBSIDIAN not in adapters


def test_finding_d_table_syntax_does_not_activate_evernote() -> None:
    """table 单元格里的 Evernote 标签不激活 evernote adapter。"""
    doc = (
        "# 表格教程\n\n"
        "| 示例 |\n"
        "|------|\n"
        "| <en-media> |\n\n"
        "正文不含 Evernote 语法。\n"
    )
    tokens = MarkdownExtractor()._parser.parse(doc)
    adapters = select_adapters(_collect_adapter_signal_corpus(tokens))
    assert ADAPTER_EVERNOTE not in adapters


def test_finding_d_inline_code_syntax_does_not_activate_obsidian() -> None:
    """行内 code 里的 Obsidian 语法不激活 adapter。"""
    doc = "# 文档\n\n使用 `![[embed]]` 语法嵌入资源。\n"
    tokens = MarkdownExtractor()._parser.parse(doc)
    adapters = select_adapters(_collect_adapter_signal_corpus(tokens))
    assert ADAPTER_OBSIDIAN not in adapters


def test_finding_d_paragraph_syntax_still_activates_obsidian() -> None:
    """正文 paragraph 的真实 Obsidian 语法仍激活 adapter（反例：非教程语法不误杀）。"""
    doc = "# 笔记\n\n真实嵌入 ![[real.png]] 出现在正文。\n"
    tokens = MarkdownExtractor()._parser.parse(doc)
    adapters = select_adapters(_collect_adapter_signal_corpus(tokens))
    assert ADAPTER_OBSIDIAN in adapters
