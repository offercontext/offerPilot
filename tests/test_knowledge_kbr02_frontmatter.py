"""KBR-02 结构化 provenance 与 frontmatter Evidence 排除验收。

Spec（2026-07-15 Evidence 元数据过滤与 Brief 修复设计）Implementation Decisions：
- canonical Source 完整保留；元数据过滤只影响 EvidenceDraft 发射，不重写 canonical text。
- 最小 provenance = Source 标题、Source URL、作者、发布时间、系统捕获时间、元数据提取版本。
- 文档开头成对 frontmatter 边界整块不生成 Evidence；单字段非法只忽略该字段+警告；
  边界不完整按普通 Markdown 保守处理。
- 正文 ``key: value``、YAML 示例、代码块保持可检索 Evidence。
- frontmatter 不进 Evidence FTS；provenance 不参与正文事实 support。
- Snapshot 记录 metadata extraction version；相同输入和版本重跑结果稳定。

本文件覆盖 extractor 单元、repository/API 集成和 KBR-01 seam 最高层路径。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from offerpilot.config import AIProviderProfile, Config
from offerpilot.knowledge.brief import BRIEF_MIN_CONTEXT_WINDOW
from offerpilot.knowledge.extractor import (
    EXTRACTOR_VERSION,
    METADATA_EXTRACTION_VERSION,
    MarkdownExtractor,
    compute_source_hash,
)

from _knowledge_seam import (
    RoleAwareModelClient,
    build_supported_brief_json,
    drive_brief_queue,
    ingest_and_extract,
)


@pytest.fixture
def qualified_config() -> Config:  # type: ignore[no-untyped-def]
    """满足 Brief 96K context 的 Provider 配置，供 seam 与 API 测试复用。"""
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
    """``create_app`` 复用 seam 已初始化的 ``tmp_path/data.db``。"""
    from fastapi.testclient import TestClient

    from offerpilot.api import create_app

    return TestClient(create_app(data_dir=tmp_path))


# ---------------------------------------------------------------------------
# Spec 验收：成对 frontmatter 整块不生成 Evidence
# ---------------------------------------------------------------------------


def test_paired_frontmatter_block_emits_no_evidence() -> None:
    """成对 ``---`` 边界：frontmatter 内容不进 Evidence search_text。"""
    extractor = MarkdownExtractor()
    content = (
        "---\n"
        "title: OfferPilot 架构笔记\n"
        "author: 张三\n"
        "tags: [arch, sqlite]\n"
        "url: https://example.com/offer\n"
        "date: 2026-07-15\n"
        "---\n\n"
        "# 正文\n\n"
        "OfferPilot 使用 SQLite 作为单一事实源。\n"
    )
    result = extractor.extract(content)
    # frontmatter 独有词不进任何 Evidence 的 search_text / excerpt。
    for draft in result.evidence_drafts:
        assert "张三" not in draft.search_text
        assert "arch" not in draft.search_text
        assert "example.com/offer" not in draft.search_text
        assert "tags" not in draft.search_text.lower()
    # 正文 Evidence 仍存在。
    assert any("SQLite" in d.search_text for d in result.evidence_drafts)


def test_paired_frontmatter_provenance_whitelist_extracted() -> None:
    """白名单字段（title/author/url/published_time）从 frontmatter 提取；tags 不进领域模型。"""
    extractor = MarkdownExtractor()
    content = (
        "---\n"
        "title: 笔记标题\n"
        "author: 李四\n"
        "url: https://example.com/note\n"
        "date: 2026-07-15\n"
        "tags: [a, b]\n"
        "category: misc\n"
        "---\n\n正文。\n"
    )
    result = extractor.extract(content)
    provenance = result.provenance
    assert provenance.title == "笔记标题"
    assert provenance.author == "李四"
    assert provenance.url == "https://example.com/note"
    assert provenance.published_at == datetime(2026, 7, 15, tzinfo=timezone.utc)
    # tags / category 是未知字段，不进领域模型。
    assert "tags" not in provenance.fields_hit
    assert "category" not in provenance.fields_hit
    # 命中字段名记录用于 Snapshot 摘要。
    for field in ("title", "author", "url", "published_time"):
        assert field in provenance.fields_hit


# ---------------------------------------------------------------------------
# Spec 验收：单字段非法只忽略该字段 + 安全警告，Extraction 仍成功
# ---------------------------------------------------------------------------


def test_invalid_single_field_ignored_with_warning() -> None:
    """非法日期/作者只忽略该字段并记录安全警告，Source Extraction 仍成功。"""
    extractor = MarkdownExtractor()
    content = (
        "---\n"
        "title: 合法标题\n"
        "author: 张三\n"
        "date: not-a-date\n"
        "url: not-a-url\n"
        "---\n\n正文内容。\n"
    )
    result = extractor.extract(content)
    provenance = result.provenance
    # 合法字段保留。
    assert provenance.title == "合法标题"
    assert provenance.author == "张三"
    # 非法字段忽略。
    assert provenance.published_at is None
    assert provenance.url == ""
    assert "published_time" not in provenance.fields_hit
    assert "url" not in provenance.fields_hit
    # 安全警告记录被忽略的字段。
    assert provenance.warnings
    assert any("published_time" in w for w in provenance.warnings)
    assert any("url" in w for w in provenance.warnings)
    # Extraction 仍成功，正文 Evidence 存在。
    assert any("正文内容" in d.search_text for d in result.evidence_drafts)


# ---------------------------------------------------------------------------
# Spec 验收：未闭合 frontmatter 按普通 Markdown 保守处理
# ---------------------------------------------------------------------------


def test_unclosed_frontmatter_treated_as_plain_markdown() -> None:
    """只有起始 ``---`` 无闭合边界：内容按普通 Markdown 处理，正文不被静默吞掉。"""
    extractor = MarkdownExtractor()
    content = "---\ntitle: 测试\n\n正文段落必须保留。\n"
    result = extractor.extract(content)
    # 未闭合 -> 不识别为 frontmatter，provenance 为空。
    assert result.provenance.title == ""
    assert result.provenance.fields_hit == ()
    # 正文保留（不静默吞掉后续正文）。
    assert any("正文段落必须保留" in d.search_text for d in result.evidence_drafts)


def test_frontmatter_must_start_at_document_head() -> None:
    """frontmatter 边界必须在文档开头；正文后出现的 ``---`` 不是 frontmatter。"""
    extractor = MarkdownExtractor()
    content = "# 标题\n\n正文。\n\n---\n\ntitle: 伪 frontmatter\nauthor: 不应提取\n"
    result = extractor.extract(content)
    # 不在开头，不识别为 frontmatter。
    assert result.provenance.author == ""
    assert result.provenance.fields_hit == ()


# ---------------------------------------------------------------------------
# Spec 验收：正文 key:value / YAML 示例 / 代码块保持可检索
# ---------------------------------------------------------------------------


def test_body_key_value_yaml_and_code_remain_searchable() -> None:
    """正文中的 ``key: value``、YAML 示例和代码块保持可检索 Evidence。"""
    extractor = MarkdownExtractor()
    content = (
        "# 配置说明\n\n"
        "key: value 是配置示例，应可检索。\n\n"
        "```yaml\n"
        "foo: bar\n"
        "baz: qux\n"
        "```\n\n"
        "普通段落收尾。\n"
    )
    result = extractor.extract(content)
    searches = [d.search_text for d in result.evidence_drafts]
    assert any("key: value" in s for s in searches)
    assert any("foo: bar" in s for s in searches)
    assert any("普通段落收尾" in s for s in searches)


# ---------------------------------------------------------------------------
# Spec 验收：canonical Source 字节/hash 不变 + Evidence offset 回读
# ---------------------------------------------------------------------------


def test_canonical_text_preserves_frontmatter_verbatim() -> None:
    """canonical Source 含完整 frontmatter 原文，未被改写或删除。"""
    extractor = MarkdownExtractor()
    content = "---\ntitle: 测试\nauthor: 张三\n---\n\n正文。\n"
    result = extractor.extract(content)
    # canonical 保留 frontmatter 原文。
    assert result.canonical_text.startswith("---\n")
    assert "title: 测试" in result.canonical_text
    assert "author: 张三" in result.canonical_text
    # 原始字节 hash 不依赖 frontmatter 过滤。
    assert compute_source_hash(content.encode("utf-8"))


def test_evidence_offsets_read_back_from_full_canonical() -> None:
    """保留 Evidence 的 char/line offsets 能从完整 canonical Source 精确回读。"""
    extractor = MarkdownExtractor()
    content = (
        "---\ntitle: 测试\nauthor: 张三\n---\n\n"
        "# 正文标题\n\n"
        "正文第一段。\n\n"
        "正文第二段。\n"
    )
    result = extractor.extract(content)
    assert result.evidence_drafts
    lines = result.canonical_text.split("\n")
    for draft in result.evidence_drafts:
        # char range 严格对齐 canonical 原文（含 frontmatter 的完整 canonical）。
        assert result.canonical_text[draft.char_start:draft.char_end] == draft.canonical_excerpt
        # line range 落在 canonical 行表内，且首行内容能回读。
        assert 1 <= draft.line_start <= draft.line_end <= len(lines)
        assert lines[draft.line_start - 1] in result.canonical_text


# ---------------------------------------------------------------------------
# Spec 验收：Snapshot 记录 metadata extraction version，重跑稳定
# ---------------------------------------------------------------------------


def test_metadata_extraction_version_recorded_and_stable() -> None:
    """Snapshot structure_manifest 记录 metadata extraction version；相同输入重跑稳定。"""
    extractor = MarkdownExtractor()
    content = "---\ntitle: 测试\nauthor: 张三\n---\n\n正文。\n"
    first = extractor.extract(content)
    second = extractor.extract(content)
    manifest = json.loads(first.structure_manifest)
    assert manifest["metadata_extraction_version"] == METADATA_EXTRACTION_VERSION
    assert manifest["provenance_fields"] == list(first.provenance.fields_hit)
    # 相同输入重跑：digest、Evidence 顺序与 content hash 稳定。
    assert first.digest == second.digest
    assert [d.content_hash for d in first.evidence_drafts] == [
        d.content_hash for d in second.evidence_drafts
    ]
    assert [d.locator for d in first.evidence_drafts] == [
        d.locator for d in second.evidence_drafts
    ]


def test_extractor_version_upgraded_for_evidence_policy_change() -> None:
    """frontmatter Evidence 规则变化视为 Extraction 版本变化。"""
    assert EXTRACTOR_VERSION != "md-ki04-1"
    assert METADATA_EXTRACTION_VERSION == "provenance-1"


# ---------------------------------------------------------------------------
# Spec 验收：repository 集成——frontmatter 不进 FTS，provenance 持久化
# ---------------------------------------------------------------------------


_FRONTMATTER_BODY_TOKEN = "OfferPilotSQLiteSingleSource"
_FRONTMATTER_TAG_TOKEN = "archmetaunique"
_FRONTMATTER_AUTHOR_TOKEN = "张三丰"


def _frontmatter_source_bytes() -> bytes:
    return (
        "---\n"
        "title: 架构笔记\n"
        f"author: {_FRONTMATTER_AUTHOR_TOKEN}\n"
        "url: https://example.com/arch\n"
        "date: 2026-07-15\n"
        f"tags: [{_FRONTMATTER_TAG_TOKEN}]\n"
        "---\n\n"
        "# 正文\n\n"
        f"{_FRONTMATTER_BODY_TOKEN} 是核心决策。\n"
    ).encode("utf-8")


def test_frontmatter_excluded_from_evidence_fts(tmp_path: Path) -> None:
    """frontmatter 内容不进入 Evidence FTS；搜索 tags/作者字段不召回。"""
    repository, _, source_id, snapshot_id = ingest_and_extract(
        tmp_path, _frontmatter_source_bytes()
    )
    # 正文独有词可召回。
    body_hits = repository.search_evidence(_FRONTMATTER_BODY_TOKEN, limit=5)
    assert body_hits, "正文独有词应可召回"
    # frontmatter 独有的 tag / 作者词不召回。
    assert not repository.search_evidence(_FRONTMATTER_TAG_TOKEN, limit=5)
    assert not repository.search_evidence(_FRONTMATTER_AUTHOR_TOKEN, limit=5)
    # frontmatter URL 不进 Evidence FTS。
    assert not repository.search_evidence("example.com/arch", limit=5)


def test_frontmatter_title_locates_source_via_display_title(tmp_path: Path) -> None:
    """frontmatter 派生标题经 display_title -> FTS source_title 仍可定位 Source Evidence。

    与 ``test_frontmatter_excluded_from_evidence_fts`` 的负向断言配对：frontmatter
    内容不进 Evidence content，但其白名单 title 填充 display_title 并经由
    ``knowledge_evidence_fts.source_title`` 让该 Source 的 Evidence 可被标题召回。
    """
    repository, _, source_id, _ = ingest_and_extract(
        tmp_path, _frontmatter_source_bytes()
    )
    source = repository.get_source(source_id)
    assert source is not None
    # frontmatter title 进入 display_title（可定位）。
    assert source.display_title == "架构笔记"
    # 经由 source_title 召回该 Source 的 Evidence（标题可定位资料）。
    hits = repository.search_evidence("架构笔记", limit=5)
    assert hits, "frontmatter 派生标题应经由 source_title 召回 Evidence"
    assert all(hit.source_id == source_id for hit in hits)


def test_source_provenance_persisted(tmp_path: Path) -> None:
    """Source/Snapshot 持久化 provenance：作者、发布时间、URL、元数据版本。"""
    repository, _, source_id, snapshot_id = ingest_and_extract(
        tmp_path, _frontmatter_source_bytes()
    )
    source = repository.get_source(source_id)
    assert source is not None
    # frontmatter author / published_at 沿 Source 所有权持久化。
    assert source.author == _FRONTMATTER_AUTHOR_TOKEN
    assert source.published_at == datetime(2026, 7, 15, tzinfo=timezone.utc)
    # frontmatter title 进入 display_title（可定位）。
    assert source.display_title == "架构笔记"
    # Origin 沿现有所有权记录 URL。
    origins = repository.list_origins(source_id)
    assert origins
    assert origins[0].origin_url == "https://example.com/arch"
    # Snapshot 记录 metadata extraction version。
    snapshot = repository.get_snapshot(snapshot_id)
    assert snapshot is not None
    assert snapshot.metadata_extraction_version == METADATA_EXTRACTION_VERSION


def test_source_provenance_only_nonempty_in_api(tmp_path: Path, qualified_config) -> None:  # type: ignore[no-untyped-def]
    """Source 详情 API 返回非空 provenance 字段，空字段不制造占位噪声。"""
    _, _, source_id, _ = ingest_and_extract(
        tmp_path, _frontmatter_source_bytes(), config=qualified_config
    )
    client = _api_client(tmp_path)
    response = client.get(f"/api/knowledge/sources/{source_id}")
    assert response.status_code == 200
    payload = response.json()
    provenance = payload["provenance"]
    # 非空字段展示。
    assert provenance["title"] == "架构笔记"
    assert provenance["author"] == _FRONTMATTER_AUTHOR_TOKEN
    assert provenance["url"] == "https://example.com/arch"
    assert provenance["published_at"].startswith("2026-07-15")
    assert provenance["metadata_extraction_version"] == METADATA_EXTRACTION_VERSION
    # captured_at 来自 Source.created_at（非空）。
    assert provenance["captured_at"]
    # 空字段不制造占位（无未知字段、无 tags）。
    assert "tags" not in provenance


def test_source_detail_no_provenance_noise_when_empty(tmp_path: Path, qualified_config) -> None:  # type: ignore[no-untyped-def]
    """无 frontmatter 的 Source：provenance 块只含 captured_at + 版本，不制造空占位。"""
    content = "# 纯正文\n\n这是没有 frontmatter 的正文。\n".encode("utf-8")
    _, _, source_id, _ = ingest_and_extract(
        tmp_path, content, config=qualified_config
    )
    client = _api_client(tmp_path)
    response = client.get(f"/api/knowledge/sources/{source_id}")
    assert response.status_code == 200
    provenance = response.json()["provenance"]
    # 没有 frontmatter -> title/author/url/published_at 不出现（空字段不占位）。
    assert "title" not in provenance
    assert "author" not in provenance
    assert "url" not in provenance
    assert "published_at" not in provenance
    # 仍含 captured_at 和元数据版本。
    assert provenance["captured_at"]
    assert provenance["metadata_extraction_version"] == METADATA_EXTRACTION_VERSION


def test_evidence_search_hit_carries_source_provenance(
    tmp_path: Path, qualified_config  # type: ignore[no-untyped-def]
) -> None:
    """Evidence 搜索响应附带所属 Source 的 provenance，用于出处展示而非召回计权。"""
    _, _, source_id, _ = ingest_and_extract(
        tmp_path, _frontmatter_source_bytes(), config=qualified_config
    )
    client = _api_client(tmp_path)
    response = client.post(
        "/api/knowledge/evidence/search",
        json={"query": _FRONTMATTER_BODY_TOKEN},
    )
    assert response.status_code == 200
    hits = response.json()["hits"]
    assert hits
    provenance = hits[0]["source_provenance"]
    assert provenance["author"] == _FRONTMATTER_AUTHOR_TOKEN
    assert provenance["url"] == "https://example.com/arch"


# ---------------------------------------------------------------------------
# Spec 验收：最高层 seam——frontmatter 导入后 Brief 不含 frontmatter Evidence
# ---------------------------------------------------------------------------


def test_seam_frontmatter_import_brief_excludes_frontmatter_evidence(
    tmp_path: Path, qualified_config  # type: ignore[no-untyped-def]
) -> None:
    """KBR-01 seam：frontmatter 导入后 Evidence 不含 frontmatter，Brief 成功且只引用正文 Evidence。"""
    content = (
        "---\n"
        "title: 架构笔记\n"
        f"author: {_FRONTMATTER_AUTHOR_TOKEN}\n"
        f"tags: [{_FRONTMATTER_TAG_TOKEN}]\n"
        "---\n\n"
        "# 概述\n\n"
        "Source 描述 OfferPilot 与 SQLite 单一事实源决策。\n\n"
        "## 第二段\n\n"
        "Evidence 是引用单位，Evidence 不重叠。\n"
    ).encode("utf-8")
    repository, session_factory, source_id, snapshot_id = ingest_and_extract(
        tmp_path, content, config=qualified_config
    )
    evidence = repository.list_evidence(source_id, snapshot_id=snapshot_id, limit=50).items
    # frontmatter 不在 Evidence。
    assert evidence, "正文应产出 Evidence"
    for item in evidence:
        assert _FRONTMATTER_TAG_TOKEN not in item.search_text
        assert _FRONTMATTER_AUTHOR_TOKEN not in item.search_text
    # Brief 用正文 Evidence 构造并成功。
    brief_json = build_supported_brief_json(evidence)
    client = RoleAwareModelClient(generation=[brief_json])
    outcome = drive_brief_queue(
        repository,
        session_factory,
        tmp_path,
        config=qualified_config,
        model_client=client,
        source_id=source_id,
    )
    assert outcome.source is not None
    assert outcome.source.brief_status == "ready", outcome.source.brief_error_message
    assert outcome.brief is not None
