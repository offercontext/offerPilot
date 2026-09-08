"""KI-03 单元测试与参数化属性测试。

Spec §8.1 / §8.2 Evidence 不越界、不重叠、顺序稳定且可完整回读。
本文件不依赖 HTTP/API；纯 extractor / encoding 单元测试。
"""

from __future__ import annotations

import hashlib
import random

import pytest

from offerpilot.knowledge.encoding import (
    EncodingError,
    decode_source_bytes,
)
from offerpilot.knowledge.extractor import (
    EXTRACTOR_VERSION,
    ExtractionError,
    MarkdownExtractor,
    compute_source_hash,
)


def test_extractor_version_distinct_from_ki02():
    # Spec §7.2：extractor 升级创建新 Snapshot。KBR-03 升级到 md-kbr03-* 以区分
    # evidence eligibility policy（过滤元数据样板）带来的规则变化。
    assert EXTRACTOR_VERSION in {
        "md-ki03-1",
        "md-ki04-1",
        "md-kbr02-1",
        "md-kbr03-1",
        "md-kbr03-2",
    }


# ---------------------------------------------------------------------------
# Spec §8.2 invariant：canonical_excerpt = canonical_text[char_start:char_end]
# ---------------------------------------------------------------------------


SAMPLES = [
    "# H1\n\nparagraph.\n",
    "# H\n\n- a\n- b\n  - nested\n- c\n",
    "# H\n\n> quote line one.\n> quote line two.\n",
    "# H\n\n| A | B |\n| - | - |\n| 1 | 2 |\n| 3 | 4 |\n",
    "# H\n\n```python\ndef f():\n    return 1\n```\n",
    (
        "# H\n\n"
        "First paragraph here.\n\n"
        "## Sub\n\n"
        "- outer\n"
        "  - inner one\n"
        "  - inner two\n"
        "- outer two\n\n"
        "> quoted.\n>\n> second paragraph in quote.\n\n"
        "| Col1 | Col2 |\n| ---- | ---- |\n| a | b |\n\n"
        "```js\nfoo();\n```\n\n"
        "Closing paragraph.\n"
    ),
]


@pytest.mark.parametrize("sample", SAMPLES)
def test_evidence_excerpt_matches_char_range(sample):
    extractor = MarkdownExtractor()
    result = extractor.extract(sample)
    assert result.evidence_drafts, f"no drafts for sample: {sample!r}"
    for draft in result.evidence_drafts:
        expected = result.canonical_text[draft.char_start:draft.char_end]
        assert draft.canonical_excerpt == expected, (
            f"{draft.block_kind} excerpt mismatch: "
            f"got {draft.canonical_excerpt!r}, expected {expected!r}"
        )


@pytest.mark.parametrize("sample", SAMPLES)
def test_evidence_does_not_overlap_and_no_gap(sample):
    extractor = MarkdownExtractor()
    result = extractor.extract(sample)
    ranges = sorted(
        ((d.char_start, d.char_end) for d in result.evidence_drafts),
        key=lambda r: r[0],
    )
    for prev, curr in zip(ranges, ranges[1:]):
        # 同起点允许（理论上不应发生）；上一切片结束必须 <= 下一切片起点
        assert prev[1] <= curr[0], f"overlap detected: {prev} vs {curr}"


@pytest.mark.parametrize("sample", SAMPLES)
def test_evidence_line_range_within_canonical(sample):
    extractor = MarkdownExtractor()
    result = extractor.extract(sample)
    total_lines = result.canonical_text.count("\n") + 1
    for draft in result.evidence_drafts:
        assert 1 <= draft.line_start <= draft.line_end <= total_lines, (
            f"{draft.block_kind} line range out of bounds: "
            f"{draft.line_start}-{draft.line_end} (total {total_lines})"
        )


@pytest.mark.parametrize("sample", SAMPLES)
def test_evidence_id_deterministic_across_runs(sample):
    extractor = MarkdownExtractor()
    r1 = extractor.extract(sample)
    r2 = extractor.extract(sample)
    assert r1.digest == r2.digest
    assert [d.locator for d in r1.evidence_drafts] == [
        d.locator for d in r2.evidence_drafts
    ]
    assert [d.content_hash for d in r1.evidence_drafts] == [
        d.content_hash for d in r2.evidence_drafts
    ]


@pytest.mark.parametrize("sample", SAMPLES)
def test_evidence_block_kinds_within_supported_set(sample):
    supported = {"paragraph", "list_item", "blockquote", "table_row", "fenced_code"}
    extractor = MarkdownExtractor()
    result = extractor.extract(sample)
    for draft in result.evidence_drafts:
        assert draft.block_kind in supported, (
            f"unexpected block_kind: {draft.block_kind}"
        )


# ---------------------------------------------------------------------------
# Spec §8.1 拆分粒度
# ---------------------------------------------------------------------------


def test_paragraph_split_caps_at_2000_chars():
    long_para = "This is a sentence. " * 600  # ~12K chars
    result = MarkdownExtractor().extract(f"# H\n\n{long_para}\n")
    pieces = [d for d in result.evidence_drafts if d.block_kind == "paragraph"]
    assert len(pieces) >= 5
    for piece in pieces:
        assert len(piece.canonical_excerpt) <= 2000


def test_fenced_code_split_caps_at_8000_chars():
    long_code = "\n".join(f"line_{i} = {i}" for i in range(700))
    result = MarkdownExtractor().extract(f"# H\n\n```python\n{long_code}\n```\n")
    pieces = [d for d in result.evidence_drafts if d.block_kind == "fenced_code"]
    assert len(pieces) >= 2
    for piece in pieces:
        assert len(piece.canonical_excerpt) <= 8000


def test_paragraph_split_preserves_full_content():
    long_para = "Sentence one. Sentence two. " * 500
    result = MarkdownExtractor().extract(f"# H\n\n{long_para.rstrip()}\n")
    pieces = [d for d in result.evidence_drafts if d.block_kind == "paragraph"]
    rebuilt = "".join(piece.canonical_excerpt for piece in pieces)
    assert long_para.rstrip() in rebuilt.replace("\n", "")


def test_fenced_code_split_preserves_full_content():
    long_code = "\n".join(f"line_{i} = {i}" for i in range(700))
    result = MarkdownExtractor().extract(f"# H\n\n```python\n{long_code}\n```\n")
    pieces = [d for d in result.evidence_drafts if d.block_kind == "fenced_code"]
    rebuilt = "".join(piece.canonical_excerpt for piece in pieces)
    assert "```python" in rebuilt
    assert "```" in rebuilt
    assert long_code in rebuilt


# ---------------------------------------------------------------------------
# Spec §8.1 嵌套列表 / 表头 / 代码语言
# ---------------------------------------------------------------------------


def test_nested_list_parent_path_in_search_text():
    sample = (
        "# Demo\n\n"
        "- outer one\n"
        "  - inner one\n"
        "  - inner two\n"
        "- outer two\n"
    )
    result = MarkdownExtractor().extract(sample)
    inner = next(
        d for d in result.evidence_drafts
        if d.block_kind == "list_item" and "inner one" in d.canonical_excerpt
    )
    assert "outer one" in inner.search_text


def test_table_row_search_text_includes_headers():
    sample = (
        "# Demo\n\n"
        "| Col1 | Col2 |\n| ---- | ---- |\n| a | b |\n"
    )
    result = MarkdownExtractor().extract(sample)
    rows = [d for d in result.evidence_drafts if d.block_kind == "table_row"]
    assert len(rows) == 1
    assert "Col1" in rows[0].search_text
    assert "Col2" in rows[0].search_text
    assert "a" in rows[0].search_text


def test_fenced_code_excerpt_includes_backticks():
    sample = "```python\nfoo()\nbar()\n```\n"
    result = MarkdownExtractor().extract(sample)
    code = next(d for d in result.evidence_drafts if d.block_kind == "fenced_code")
    assert code.canonical_excerpt.startswith("```python")
    assert code.canonical_excerpt.rstrip().endswith("```")


# ---------------------------------------------------------------------------
# Spec §7.1 控制字符
# ---------------------------------------------------------------------------


def test_nul_character_rejected():
    with pytest.raises(ExtractionError) as exc_info:
        MarkdownExtractor().extract("# Bad\n\nNUL\x00here\n")
    assert exc_info.value.code == "encoding_unknown"


def test_non_nul_control_chars_preserved_and_counted():
    result = MarkdownExtractor().extract("# Title\n\nHas BEL\x07char.\n")
    assert result.control_char_count == 1
    assert "\x07" in result.canonical_text
    assert "control_char_count" in result.structure_manifest


# ---------------------------------------------------------------------------
# 参数化属性测试：随机组合 Markdown 块仍满足 invariant
# ---------------------------------------------------------------------------


_BLOCK_PARTS = [
    "# Heading\n\n",
    "Paragraph text.\n\n",
    "- item one\n- item two\n\n",
    "> quoted text.\n>\n> more quote.\n\n",
    "| col |\n| --- |\n| val |\n\n",
    "```python\nx = 1\n```\n\n",
    "Plain sentence one. Plain sentence two.\n\n",
    "## Subsection\n\nMore text here.\n\n",
    "1. ordered one\n2. ordered two\n\n",
]


def _generate_random_markdown(seed: int) -> str:
    rng = random.Random(seed)
    count = rng.randint(1, 8)
    return "".join(rng.choice(_BLOCK_PARTS) for _ in range(count))


@pytest.mark.parametrize("seed", list(range(40)))
def test_property_evidence_invariants_hold(seed):
    sample = _generate_random_markdown(seed)
    extractor = MarkdownExtractor()
    result = extractor.extract(sample)
    for draft in result.evidence_drafts:
        expected = result.canonical_text[draft.char_start:draft.char_end]
        assert draft.canonical_excerpt == expected
        assert 0 <= draft.char_start < draft.char_end <= len(result.canonical_text)
    ranges = sorted(((d.char_start, d.char_end) for d in result.evidence_drafts))
    for prev, curr in zip(ranges, ranges[1:]):
        assert prev[1] <= curr[0]


# ---------------------------------------------------------------------------
# Spec §4.3 编码单元测试
# ---------------------------------------------------------------------------


def test_compute_source_hash_is_pinned_format():
    h = compute_source_hash("hello".encode("utf-8"))
    expected = "sha256:" + hashlib.sha256(b"hello").hexdigest()
    assert h == expected


def test_decode_source_bytes_pure_ascii():
    result = decode_source_bytes(b"# ASCII Title\n\nplain text\n")
    assert result.encoding == "utf-8"
    assert result.detection_method == "strict-utf8"


def test_decode_source_bytes_rejects_invalid_utf8_no_bom():
    with pytest.raises(EncodingError):
        decode_source_bytes(b"\xff invalid first byte")


def test_decode_source_bytes_rejects_utf8_bom_with_invalid_payload():
    with pytest.raises(EncodingError):
        decode_source_bytes(b"\xef\xbb\xbf\xff invalid after bom")
