"""Knowledge Markdown/Text Extractor。

KI-03 范围：
- 支持 Markdown（heading、paragraph、list item、blockquote、table row、fenced code）。
- 超长 paragraph 按句子边界拆分；超长 fenced code 按行边界拆分。
- 嵌套 list item 保留父路径；table row 携带表头；fenced code 携带语言和行范围。
- 固定 product tokenizer（cl100k_base）和 64,000 token 上限。
- 升级 EXTRACTOR_VERSION 以区分 KI-02 的 paragraph-only 解析。

Spec §7.1 规范化顺序：严格解码 → 换行 \\n → NUL 拒绝 → 固定版本 AST → canonical text +
结构节点 + 行/字符位置 → Snapshot digest。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token

from offerpilot.knowledge.evidence_policy import (
    EVIDENCE_POLICY_VERSION,
    ExtractionContext,
    evaluate_block,
    select_adapters,
)
from offerpilot.knowledge.tokenizer import (
    TOKENIZER_VERSION,
    TokenizerUnavailableError,
    count_tokens,
    max_token_limit,
)


# Spec §7.2：(source_id, extractor_version) 唯一。KBR-03 升级 extractor 版本：新增正文块
# Evidence eligibility policy（过滤作者卡/阅读数/导航/图片壳/Obsidian/Evernote 残片等元数据
# 样板），Evidence 规则变化视为 Extraction 版本变化。旧 Snapshot（md-kbr02-1）与新版本不
# 混用；测试期切换由 KBR-07 破坏性 reset 收尾。
#
# md-kbr03-2：平台/导出噪声规则收敛进按确定性来源信号选择的适配器（Obsidian / Evernote /
# web-article），不再全局应用；普通 Markdown 中作者署名 / 阅读数 / 翻页导航不再误删
# （Spec 第 35/58/71/72 行）。policy 规则变化同步体现为 evidence_policy_version=evidence-policy-2。
EXTRACTOR_VERSION = "md-kbr03-2"
PARSER_VERSION = "markdown-it-py-3"
NORMALIZATION_VERSION = "nl-1"
# Spec Implementation Decisions：最小 provenance 含“元数据提取版本”，确定性规则带版本号，
# 同一规则可以确定性重建 Snapshot。
METADATA_EXTRACTION_VERSION = "provenance-1"

# Spec §4.2 主 Markdown/Text 5 MiB；普通文本 Evidence ≤ 2000 Unicode chars，
# 超长时按句子边界拆分；fenced code / table cell 允许到 8000 chars，超过按行边界拆分。
MAX_FILE_BYTES = 5 * 1024 * 1024
PARAGRAPH_TARGET_CHARS = 2_000
BLOCK_TARGET_CHARS = 8_000


_SOURCE_HASH_PREFIX = "sha256:"
_TRAILING_WS_RE = re.compile(r"[ \t]+\n")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")

# Spec §7.1 控制字符：禁止 NUL，其他 C0/C1 控制字符保留在 canonical text 中，
# 但 Snapshot structure_manifest 记录它们的总数。
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Spec §8.1 paragraph 拆分按句子边界。中文/英文句号、问号、感叹号都视为句子结束。
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?\.])\s+|(?<=\n)")


def compute_source_hash(content_bytes: bytes) -> str:
    """单文件 Source hash（KI-02/KI-03 行为）。Bundle 使用 ``compute_bundle_source_hash``。"""

    digest = hashlib.sha256(content_bytes).hexdigest()
    return f"{_SOURCE_HASH_PREFIX}{digest}"


def compute_bundle_source_hash(
    main_bytes: bytes,
    assets: list[tuple[str, bytes]],
) -> str:
    """Spec §5.1 Bundle source_hash：主文件字节 + 附件字节 + 附件逻辑路径 manifest。

    ``assets`` 中每项为 ``(logical_name, content_bytes)``。计算顺序：
    1. 主文件 sha256。
    2. 附件按 logical_name 字典序排序；逐项取 ``sha256(content_bytes)`` 与 logical_name。
    3. 组装为带固定字段的 JSON manifest，序列化后再 sha256 得到最终 hash。

    Spec 要求 source_hash 不依赖展示标题、本机路径或 origin_url；本实现只接受原始字节
    和 logical_name，与 Spec 一致。
    """

    main_digest = hashlib.sha256(main_bytes).hexdigest()
    asset_manifest: list[dict[str, str]] = []
    for logical_name, content in sorted(assets, key=lambda item: item[0]):
        asset_manifest.append(
            {
                "logical_name": logical_name,
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": str(len(content)),
            }
        )
    payload = json.dumps(
        {
            "main_sha256": main_digest,
            "main_bytes": str(len(main_bytes)),
            "assets": asset_manifest,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    bundle_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{_SOURCE_HASH_PREFIX}{bundle_digest}"


@dataclass(frozen=True)
class EvidenceDraft:
    """Extractor 产出的 Evidence 草稿，由 Worker 写入数据库。"""

    block_kind: str
    heading_path: tuple[str, ...]
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    canonical_excerpt: str
    search_text: str
    content_hash: str
    locator: str
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProvenanceDraft:
    """从文档头部 frontmatter 白名单提取的最小 provenance（Spec Implementation Decisions）。

    只含 6 项 provenance 中的 4 个文档来源字段；系统捕获时间和元数据提取版本由
    Worker/Repository 沿 Source/Snapshot 所有权写入。空字符串/None 表示未命中或被
    安全忽略。``fields_hit`` 记录成功命中的白名单字段名，供 Snapshot 摘要记录。
    ``warnings`` 记录被忽略字段的稳定安全警告，不包含字段原值。
    """

    title: str = ""
    author: str = ""
    url: str = ""
    published_at: Optional[datetime] = None
    fields_hit: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarkdownExtraction:
    """Extractor 输出：canonical_text + digest + Evidence 草稿列表 + 元数据。"""

    canonical_text: str
    digest: str
    encoding: str
    detection_method: str
    evidence_drafts: list[EvidenceDraft]
    token_count: int
    char_count: int
    control_char_count: int
    tokenizer_version: str
    structure_manifest: str
    provenance: ProvenanceDraft = field(default_factory=ProvenanceDraft)


class ExtractionError(Exception):
    """非暂时性 Extraction 失败，携带稳定 error_code。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _normalize_text(raw: str) -> tuple[str, int]:
    """Spec §7.1 规范化：换行统一为 \\n，去除行尾空白，压缩多余空行。

    返回 (canonical_text, control_char_count)。NUL 触发 ExtractionError；其他控制
    字符保留并计数，由 structure_manifest 记录，不静默删除。
    """

    if "\x00" in raw:
        raise ExtractionError("encoding_unknown", "原文中存在 NUL 控制字符，无法安全解析")
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _TRAILING_WS_RE.sub("\n", normalized)
    normalized = _MULTI_NEWLINE_RE.sub("\n\n", normalized)
    control_count = len(_CONTROL_CHAR_RE.findall(normalized))
    return normalized, control_count


# Spec Implementation Decisions：frontmatter 边界只认文档开头成对的 ``---``。
# 单个白名单字段格式非法只忽略该字段并记录安全警告（不含原值）；未闭合边界按普通
# Markdown 保守处理，不静默吞后续正文。
_FRONTMATTER_DELIMITER = "---"

# provenance 白名单字段别名。tags 与未知字段不进入领域模型。
_PROVENANCE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("title", ("title",)),
    ("author", ("author", "authors")),
    ("url", ("url", "source_url", "source", "link")),
    (
        "published_time",
        ("date", "published", "published_at", "published_time", "publish_date"),
    ),
)


@dataclass(frozen=True)
class _FrontmatterBoundary:
    """frontmatter 边界识别结果。

    ``body_text`` 含两个边界行原文（供白名单解析，不进 Evidence）；``end_line_exclusive``
    是闭合边界下一行号，token.map[0] 小于该值表示落在 frontmatter 内，应跳过 Evidence 发射。
    """

    body_text: str
    end_line_exclusive: int


def _detect_frontmatter(
    canonical: str, offsets: list[int]
) -> Optional[_FrontmatterBoundary]:
    """Spec：文档开头存在成对 ``---`` 边界时识别为 frontmatter。

    第一行 ``rstrip`` 后必须严格等于 ``---``（容忍尾部空白，不允许前导空白，符合
    YAML 约定）；从第二行起第一个整行 ``---`` 作为闭合边界。无闭合返回 None。
    """

    lines = canonical.split("\n")
    if not lines or lines[0].rstrip() != _FRONTMATTER_DELIMITER:
        return None
    close_line = -1
    for index in range(1, len(lines)):
        if lines[index].rstrip() == _FRONTMATTER_DELIMITER:
            close_line = index
            break
    if close_line == -1:
        return None
    end_line_exclusive = close_line + 1
    body_end_char = (
        offsets[end_line_exclusive]
        if end_line_exclusive < len(offsets)
        else len(canonical)
    )
    return _FrontmatterBoundary(
        body_text=canonical[:body_end_char],
        end_line_exclusive=end_line_exclusive,
    )


def _strip_yaml_value(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    return text


def _clean_scalar(value: str, *, limit: int) -> str:
    text = _strip_yaml_value(value)
    if not text:
        return ""
    return text[:limit]


def _clean_author(value: str) -> str:
    """author 字段：YAML list 形式 ``[a, b]`` 取首个元素，否则按标量清理。"""

    text = _strip_yaml_value(value)
    if not text:
        return ""
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1]
        parts = [part.strip().strip("'\"") for part in inner.split(",")]
        text = parts[0] if parts and parts[0] else ""
    if not text:
        return ""
    return text[:200]


def _clean_url(value: str) -> tuple[str, str]:
    """url 字段：必须 http/https + 非空 host。非法返回 ("", 安全警告)。"""

    text = _strip_yaml_value(value)
    if not text:
        return "", ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return "", "published url 格式非法，已忽略该字段"
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return "", "published url 非 http/https 或缺少域名，已忽略该字段"
    return text, ""


def _parse_published_time(value: str) -> tuple[Optional[datetime], str]:
    """published_time 字段：ISO 8601 / YYYY-MM-DD。非法返回 (None, 安全警告)。"""

    text = _strip_yaml_value(value)
    if not text:
        return None, ""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None, "published_time 格式非法，已忽略该字段"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed, ""


def _parse_provenance(body_text: str) -> ProvenanceDraft:
    """从 frontmatter body 解析白名单 provenance 字段。

    逐行 ``key: value``（partition 第一冒号）；tags 等未知字段忽略。单字段非法只
    忽略该字段并记录安全警告，不影响其他字段与 Extraction 成功。
    """

    raw_values: dict[str, str] = {}
    for line in body_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped == _FRONTMATTER_DELIMITER:
            continue
        if stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip().lower()
        if not key:
            continue
        for field_name, aliases in _PROVENANCE_ALIASES:
            if key in aliases and field_name not in raw_values:
                raw_values[field_name] = value
                break

    title = _clean_scalar(raw_values.get("title", ""), limit=200)
    author = _clean_author(raw_values.get("author", ""))
    url_value, url_warning = _clean_url(raw_values.get("url", ""))
    published_at, published_warning = _parse_published_time(
        raw_values.get("published_time", "")
    )

    fields_hit: list[str] = []
    warnings: list[str] = []
    if title:
        fields_hit.append("title")
    if author:
        fields_hit.append("author")
    if url_value:
        fields_hit.append("url")
    elif "url" in raw_values and url_warning:
        warnings.append(url_warning)
    if published_at is not None:
        fields_hit.append("published_time")
    elif "published_time" in raw_values and published_warning:
        warnings.append(published_warning)

    return ProvenanceDraft(
        title=title,
        author=author,
        url=url_value,
        published_at=published_at,
        fields_hit=tuple(fields_hit),
        warnings=tuple(warnings),
    )


def _heading_depth(token: Token) -> int:
    tag = token.tag or ""
    if tag.startswith("h") and tag[1:].isdigit():
        return int(tag[1:])
    return 0


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _snapshot_digest(canonical_text: str, structure_summary: str) -> str:
    payload = (
        f"{EXTRACTOR_VERSION}|{NORMALIZATION_VERSION}|{PARSER_VERSION}|"
        f"{TOKENIZER_VERSION}|{canonical_text}|{structure_summary}"
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _line_offset_table(canonical_text: str) -> list[int]:
    """预计算每行起始 char offset，加速 char↔line 转换。"""

    offsets = [0]
    for index, char in enumerate(canonical_text):
        if char == "\n":
            offsets.append(index + 1)
    return offsets


def _char_to_line(offsets: list[int], char_pos: int) -> int:
    """Spec §8.2 line range 1-indexed。"""

    import bisect

    return bisect.bisect_right(offsets, char_pos)


def _line_range_from_char(
    offsets: list[int], char_start: int, char_end: int
) -> tuple[int, int]:
    line_start = _char_to_line(offsets, char_start)
    # char_end 是 exclusive end；前一字符所在行是真正的最后一行。
    last_char_pos = max(0, char_end - 1)
    line_end = _char_to_line(offsets, last_char_pos)
    return line_start, max(line_end, line_start)


def _inline_text(token: Optional[Token]) -> str:
    if token is None or token.type != "inline":
        return ""
    return token.content.strip()


def _collect_adapter_signal_corpus(tokens: list[Token]) -> str:
    """二轮 Review P1-D：收集允许的独立结构块行内文本作为 adapter 信号语料。

    仅取 heading/paragraph/list/list_item/blockquote 内的行内 ``text``；显式排除 fenced code
    （``fence``）、缩进 code block（``code_block``）、整段 table、行内 code（``code_inline``）——
    教程示例中的 ``![[...]]`` / ``%%...%%`` / ``<en-*>`` 不得激活平台 adapter。
    """
    parts: list[str] = []
    in_table = 0
    for token in tokens:
        if token.type == "table_open":
            in_table += 1
            continue
        if token.type == "table_close":
            in_table = max(0, in_table - 1)
            continue
        if in_table > 0:
            continue
        if token.type in ("fence", "code_block"):
            continue
        if token.type == "inline" and token.children:
            for child in token.children:
                if child.type == "text":
                    parts.append(child.content)
    return "\n".join(parts)


class MarkdownExtractor:
    """Spec §7.1/§8.1：固定版本 Markdown AST 解析 + 结构感知 Evidence 生成。"""

    def __init__(self) -> None:
        # Spec §4.4：HTML 作为不可信原文处理。我们禁用 html_block/html_inline，
        # 这样 markdown-it 会把 HTML 标签当作纯文本保留，不解析、不执行脚本、不加载资源。
        self._parser = MarkdownIt("commonmark", {"html": False}).enable("table")

    def image_references(self, raw_content: str) -> tuple[str, ...]:
        """只读取 Markdown 图片引用，不生成 Evidence。

        Bundle 的 Preflight 需要确认附件与引用完整匹配，但上传请求不能执行
        Extraction。该方法复用固定 Markdown parser，仅返回引用逻辑名。
        """
        canonical, _ = _normalize_text(raw_content)
        references: list[str] = []
        for token in self._parser.parse(canonical):
            if token.type != "inline" or not token.children:
                continue
            for child in token.children:
                if child.type != "image":
                    continue
                source = str(child.attrs.get("src") or "") if child.attrs else ""
                if source:
                    references.append(source)
        return tuple(references)

    def extract(
        self,
        raw_content: str,
        *,
        encoding: str = "utf-8",
        detection_method: str = "strict-utf8",
        origin_url: str = "",
        filename: str = "",
    ) -> MarkdownExtraction:
        canonical, control_char_count = _normalize_text(raw_content)
        tokens = self._parser.parse(canonical)
        # Spec KBR-03 + 二轮 Review P1-D：adapter 信号只从允许的独立结构块提取（排除
        # fence/table/code_block/行内 code），教程示例语法不得激活平台 adapter。适配器激活
        # 集合贯穿 _emit_block → _consume_* → evaluate_block；无信号时仅全局低歧义规则。
        ctx = ExtractionContext(
            active_adapters=select_adapters(
                _collect_adapter_signal_corpus(tokens),
                origin_url=origin_url,
                filename=filename,
            )
        )

        offsets = _line_offset_table(canonical)
        navigator = _StructureNavigator()
        drafts: list[EvidenceDraft] = []
        # Spec KBR-03：按稳定 rule_id 聚合被 evidence policy 过滤的正文块数量；只记计数，
        # 不保存被过滤正文/URL/作者名/本机路径。
        filter_counts: dict[str, int] = {}
        # Spec KBR-02：确定性识别文档头部 frontmatter 边界。canonical text 不改写，
        # frontmatter 原文保留用于回读；只跳过落在 frontmatter 行范围内的 token 的
        # Evidence 发射，避免 frontmatter 键值污染 heading_path / search_text / FTS。
        frontmatter = _detect_frontmatter(canonical, offsets)
        fm_end_line_exclusive = (
            frontmatter.end_line_exclusive if frontmatter is not None else 0
        )
        provenance = (
            _parse_provenance(frontmatter.body_text)
            if frontmatter is not None
            else ProvenanceDraft()
        )
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if (
                fm_end_line_exclusive > 0
                and token.map is not None
                and token.map[0] < fm_end_line_exclusive
            ):
                # frontmatter 块内 token 不发射 Evidence、不更新 navigator。
                index += 1
                continue
            advanced = self._emit_block(
                tokens=tokens,
                index=index,
                canonical=canonical,
                offsets=offsets,
                navigator=navigator,
                drafts=drafts,
                filter_counts=filter_counts,
                ctx=ctx,
            )
            if advanced <= 0:
                index += 1
            else:
                index += advanced

        try:
            token_count_value = count_tokens(canonical)
        except TokenizerUnavailableError as exc:
            # Service 层统一把 ExtractionError 映射为稳定 API 错误码；不能让
            # tokenizer 缺失退化成字符估算或未分类的 500。
            raise ExtractionError("tokenizer_unavailable", str(exc)) from exc
        if token_count_value.count > max_token_limit():
            raise ExtractionError(
                "source_too_large",
                (
                    f"原文约 {token_count_value.count} tokens，"
                    f"超出 {max_token_limit()} token 上限；请按主题拆分资料"
                ),
            )

        structure_summary = json.dumps(
            {
                "draft_count": len(drafts),
                "block_kinds": _count_block_kinds(drafts),
                "control_char_count": control_char_count,
                "headings": navigator.top_headings(),
                "metadata_extraction_version": METADATA_EXTRACTION_VERSION,
                "provenance_fields": list(provenance.fields_hit),
                "frontmatter_detected": frontmatter is not None,
                # Spec KBR-03：Snapshot 结构摘要记录 evidence policy version 与按稳定
                # rule_id 聚合的过滤统计。不重复保存被过滤正文。
                "evidence_policy_version": EVIDENCE_POLICY_VERSION,
                "filtered_block_total": sum(filter_counts.values()),
                "filtered_by_rule": dict(sorted(filter_counts.items())),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = _snapshot_digest(canonical, structure_summary)
        return MarkdownExtraction(
            canonical_text=canonical,
            digest=digest,
            encoding=encoding,
            detection_method=detection_method,
            evidence_drafts=drafts,
            token_count=token_count_value.count,
            char_count=len(canonical),
            control_char_count=control_char_count,
            tokenizer_version=token_count_value.tokenizer_version,
            structure_manifest=structure_summary,
            provenance=provenance,
        )

    def _emit_block(
        self,
        *,
        tokens: list[Token],
        index: int,
        canonical: str,
        offsets: list[int],
        navigator: "_StructureNavigator",
        drafts: list[EvidenceDraft],
        filter_counts: dict[str, int],
        ctx: ExtractionContext,
    ) -> int:
        token = tokens[index]
        if token.type == "heading_open":
            return self._consume_heading(tokens, index, navigator)
        if token.type == "paragraph_open":
            return self._consume_paragraph(
                tokens, index, canonical, offsets, navigator, drafts, filter_counts, ctx
            )
        if token.type == "bullet_list_open" or token.type == "ordered_list_open":
            return self._consume_list(
                tokens, index, canonical, offsets, navigator, drafts, filter_counts, ctx
            )
        if token.type == "blockquote_open":
            return self._consume_blockquote(
                tokens, index, canonical, offsets, navigator, drafts, filter_counts, ctx
            )
        if token.type == "table_open":
            return self._consume_table(tokens, index, canonical, offsets, navigator, drafts)
        if token.type == "fence":
            self._emit_fence(token, canonical, offsets, navigator, drafts)
            return 1
        if token.type == "hr":
            return 1
        return 0

    def _consume_heading(
        self,
        tokens: list[Token],
        index: int,
        navigator: "_StructureNavigator",
    ) -> int:
        token = tokens[index]
        depth = _heading_depth(token)
        inline = tokens[index + 1] if index + 1 < len(tokens) else None
        title = _inline_text(inline)
        if depth > 0 and title:
            navigator.push_heading(depth, title)
        # heading_open + inline + heading_close
        return 3

    def _consume_paragraph(
        self,
        tokens: list[Token],
        index: int,
        canonical: str,
        offsets: list[int],
        navigator: "_StructureNavigator",
        drafts: list[EvidenceDraft],
        filter_counts: dict[str, int],
        ctx: ExtractionContext,
    ) -> int:
        inline = tokens[index + 1] if index + 1 < len(tokens) else None
        if inline is None or not inline.content.strip():
            return 3
        char_start, char_end = _inline_char_range(inline, canonical)
        if char_end <= char_start:
            return 3
        text = canonical[char_start:char_end]
        # Spec KBR-03：正文块 Evidence eligibility policy。规则只决定是否发射，不改 canonical
        # text 与 char range；被过滤块不产生 paragraph / image Evidence，相邻保留 Evidence 的
        # line/char offsets 不受影响。平台规则只在 ctx 激活的 adapter 内执行。
        decision = evaluate_block(text, ctx)
        if not decision.emit:
            # evaluate_block 契约保证 emit=False 时 rule_id 非 None；``or "unknown"``
            # 是 Optional 类型的防御性兜底，运行时不会触发（list_item/blockquote 同）。
            rule_id = decision.rule_id or "unknown"
            filter_counts[rule_id] = filter_counts.get(rule_id, 0) + 1
            return 3
        for piece_index, (piece_start, piece_end, piece_text) in enumerate(
            _split_paragraph(text, char_start)
        ):
            line_start, line_end = _line_range_from_char(offsets, piece_start, piece_end)
            drafts.append(
                _make_paragraph_draft(
                    piece_text,
                    heading_path=navigator.heading_path_tuple(),
                    list_path=tuple(navigator.list_path()),
                    char_start=piece_start,
                    char_end=piece_end,
                    line_start=line_start,
                    line_end=line_end,
                    ordinal_hint=piece_index,
                )
            )
        # Spec §8.1 image reference：扫描 inline 中的 image token，每张图额外产出
        # 一条 Asset Evidence，记录该次引用的位置。同一 logical name 多次引用会
        # 产生多条 Evidence，由 Service/Repository 通过 logical_name 关联到 Asset 行。
        self._emit_inline_images(
            inline=inline,
            parent_char_range=(char_start, char_end),
            canonical=canonical,
            offsets=offsets,
            navigator=navigator,
            drafts=drafts,
        )
        return 3

    def _emit_inline_images(
        self,
        *,
        inline: Token,
        parent_char_range: tuple[int, int],
        canonical: str,
        offsets: list[int],
        navigator: "_StructureNavigator",
        drafts: list[EvidenceDraft],
    ) -> None:
        """Spec §8.1：每个 image reference 产出一条 Asset Evidence。

        定位策略：先尝试匹配完整 ``![alt](src)`` 字面值；若失败（alt 包含 markdown
        转义、emoji 等导致 ``child.content`` 与 canonical 字面值不一致），则回退到
        按 ``(src)`` 子串定位 URL，再向左查找 ``![``、向右查找 ``)`` 边界。
        """

        parent_start, parent_end = parent_char_range
        if not inline.children:
            return
        cursor = parent_start
        for child in inline.children:
            if child.type != "image":
                continue
            src = str(child.attrs.get("src") or "") if child.attrs else ""
            alt = child.content or ""
            char_start, char_end, excerpt = _locate_image_token(
                canonical=canonical,
                cursor=cursor,
                parent_end=parent_end,
                alt=alt,
                src=src,
            )
            if char_end <= char_start:
                continue
            line_start, line_end = _line_range_from_char(offsets, char_start, char_end)
            drafts.append(
                EvidenceDraft(
                    block_kind="image",
                    heading_path=navigator.heading_path_tuple(),
                    char_start=char_start,
                    char_end=char_end,
                    line_start=line_start,
                    line_end=line_end,
                    canonical_excerpt=excerpt,
                    search_text=alt,
                    content_hash=_content_hash(excerpt),
                    locator=f"image:{src}:{line_start}:{char_start}",
                    extra={
                        "logical_name": src,
                        "alt_text": alt,
                    },
                )
            )
            cursor = char_end

    def _consume_list(
        self,
        tokens: list[Token],
        index: int,
        canonical: str,
        offsets: list[int],
        navigator: "_StructureNavigator",
        drafts: list[EvidenceDraft],
        filter_counts: dict[str, int],
        ctx: ExtractionContext,
    ) -> int:
        open_token = tokens[index]
        is_ordered = open_token.type == "ordered_list_open"
        navigator.enter_list_level(open_token.level)
        # 顺序消费整个 list，遇到对应的 list_X_close 退出。
        consumed = 1
        cursor = index + 1
        while cursor < len(tokens):
            current = tokens[cursor]
            if (
                current.type == ("ordered_list_close" if is_ordered else "bullet_list_close")
                and current.level == open_token.level
            ):
                consumed += 1
                navigator.exit_list_level(open_token.level)
                return consumed
            if current.type == "list_item_open":
                item_consumed = self._consume_list_item(
                    tokens, cursor, canonical, offsets, navigator, drafts, filter_counts, ctx
                )
                cursor += item_consumed
                consumed += item_consumed
                continue
            # 跳过 list 内的非 item token（如 paragraph_open 等已被 item 处理消费）。
            cursor += 1
            consumed += 1
        navigator.exit_list_level(open_token.level)
        return consumed

    def _consume_list_item(
        self,
        tokens: list[Token],
        index: int,
        canonical: str,
        offsets: list[int],
        navigator: "_StructureNavigator",
        drafts: list[EvidenceDraft],
        filter_counts: dict[str, int],
        ctx: ExtractionContext,
    ) -> int:
        open_token = tokens[index]
        inline_text, inline_token, consumed = _collect_list_item_inline(
            tokens, index, canonical
        )
        if inline_token is not None and inline_text.strip():
            char_start, char_end = _inline_char_range(inline_token, canonical)
            text = canonical[char_start:char_end] if char_end > char_start else inline_text
            # Spec KBR-03：list item 也走 evidence policy（如 ``- 作者：张三`` byline）。
            # canonical slice 含 ``- `` list marker，因此用 marker-stripped 的 inline_text
            # 作为 policy 输入；被过滤项 push 空占位保持 push/pop 平衡，不污染嵌套子项。
            decision = evaluate_block(inline_text, ctx)
            if decision.emit:
                navigator.push_list_item(open_token.level, inline_text.strip())
                line_start, line_end = _line_range_from_char(offsets, char_start, char_end)
                drafts.append(
                    EvidenceDraft(
                        block_kind="list_item",
                        heading_path=navigator.heading_path_tuple(),
                        char_start=char_start,
                        char_end=char_end,
                        line_start=line_start,
                        line_end=line_end,
                        canonical_excerpt=text,
                        search_text=_build_search_text(
                            navigator.heading_path_tuple(),
                            navigator.list_path(),
                            text,
                        ),
                        content_hash=_content_hash(text),
                        locator=(
                            f"list_item:{navigator.list_locator()}:{line_start}:{char_start}"
                        ),
                        extra={
                            "list_path": list(navigator.list_path()),
                            "list_level": open_token.level,
                        },
                    )
                )
            else:
                # 占位 push 保持 push/pop 平衡；list_path 跳过空串，不泄漏噪声文本。
                navigator.push_list_item(open_token.level, "")
                rule_id = decision.rule_id or "unknown"
                filter_counts[rule_id] = filter_counts.get(rule_id, 0) + 1
        # 消费掉 list_item 内的 nested list（递归在 _consume_list 中处理）
        cursor = index + consumed
        while cursor < len(tokens):
            current = tokens[cursor]
            if current.type == "list_item_close" and current.level == open_token.level:
                navigator.pop_list_item(open_token.level)
                return consumed + 1
            if current.type in {"bullet_list_open", "ordered_list_open"}:
                nested = self._consume_list(
                    tokens, cursor, canonical, offsets, navigator, drafts, filter_counts, ctx
                )
                cursor += nested
                consumed += nested
                continue
            cursor += 1
            consumed += 1
        navigator.pop_list_item(open_token.level)
        return consumed

    def _consume_blockquote(
        self,
        tokens: list[Token],
        index: int,
        canonical: str,
        offsets: list[int],
        navigator: "_StructureNavigator",
        drafts: list[EvidenceDraft],
        filter_counts: dict[str, int],
        ctx: ExtractionContext,
    ) -> int:
        open_token = tokens[index]
        navigator.enter_blockquote()
        parts: list[tuple[int, int, str]] = []
        # blockquote 的 canonical slice 含 ``> `` 引用标记；policy 用 marker-stripped 的
        # inline content 判定，与 paragraph/list item 行为一致。
        clean_parts: list[str] = []
        consumed = 1
        cursor = index + 1
        while cursor < len(tokens):
            current = tokens[cursor]
            if current.type == "blockquote_close" and current.level == open_token.level:
                consumed += 1
                break
            if current.type == "paragraph_open":
                inline = tokens[cursor + 1] if cursor + 1 < len(tokens) else None
                if inline is not None and inline.content:
                    start, end = _inline_char_range(inline, canonical)
                    if end > start:
                        parts.append((start, end, canonical[start:end]))
                        clean_parts.append(inline.content)
                cursor += 3
                consumed += 3
                continue
            cursor += 1
            consumed += 1
        navigator.exit_blockquote()
        if parts:
            merged_start = parts[0][0]
            merged_end = parts[-1][1]
            text = canonical[merged_start:merged_end]
            # Spec KBR-03：blockquote 块也走 evidence policy；被过滤则整块不发射 Evidence。
            decision = evaluate_block("\n".join(clean_parts), ctx)
            if not decision.emit:
                rule_id = decision.rule_id or "unknown"
                filter_counts[rule_id] = filter_counts.get(rule_id, 0) + 1
                return consumed
            # Spec §8.1 blockquote：连续引用块合并为一条 Evidence，超长时按句子拆分。
            for piece_start, piece_end, _ in _split_paragraph(text, merged_start):
                excerpt = canonical[piece_start:piece_end]
                line_start, line_end = _line_range_from_char(offsets, piece_start, piece_end)
                drafts.append(
                    EvidenceDraft(
                        block_kind="blockquote",
                        heading_path=navigator.heading_path_tuple(),
                        char_start=piece_start,
                        char_end=piece_end,
                        line_start=line_start,
                        line_end=line_end,
                        canonical_excerpt=excerpt,
                        search_text=_build_search_text(
                            navigator.heading_path_tuple(),
                            navigator.list_path(),
                            excerpt,
                        ),
                        content_hash=_content_hash(excerpt),
                        locator=(
                            f"blockquote:{navigator.blockquote_depth()}:{line_start}:{piece_start}"
                        ),
                    )
                )
        return consumed

    def _consume_table(
        self,
        tokens: list[Token],
        index: int,
        canonical: str,
        offsets: list[int],
        navigator: "_StructureNavigator",
        drafts: list[EvidenceDraft],
    ) -> int:
        open_token = tokens[index]
        headers: list[str] = []
        rows: list[tuple[list[str], tuple[int, int]]] = []
        consumed = 1
        cursor = index + 1
        # 状态机：thead/tr/th/td
        current_row_cells: list[str] = []
        current_row_range: Optional[tuple[int, int]] = None
        in_thead = False
        in_tbody = False
        while cursor < len(tokens):
            current = tokens[cursor]
            if current.type == "table_close" and current.level == open_token.level:
                consumed += 1
                break
            if current.type == "thead_open":
                in_thead = True
                cursor += 1
                consumed += 1
                continue
            if current.type == "thead_close":
                in_thead = False
                cursor += 1
                consumed += 1
                continue
            if current.type == "tbody_open":
                in_tbody = True
                cursor += 1
                consumed += 1
                continue
            if current.type == "tbody_close":
                in_tbody = False
                cursor += 1
                consumed += 1
                continue
            if current.type == "tr_open":
                current_row_cells = []
                current_row_range = (
                    (current.map[0], current.map[1]) if current.map else None
                )
                cursor += 1
                consumed += 1
                continue
            if current.type == "tr_close":
                if in_thead:
                    headers = list(current_row_cells)
                elif in_tbody and current_row_range is not None:
                    rows.append((list(current_row_cells), current_row_range))
                cursor += 1
                consumed += 1
                continue
            if current.type in {"th_open", "td_open"}:
                inline = tokens[cursor + 1] if cursor + 1 < len(tokens) else None
                current_row_cells.append(_inline_text(inline))
                cursor += 3
                consumed += 3
                continue
            cursor += 1
            consumed += 1

        for row_index, (cells, (line_start_idx, line_end_idx)) in enumerate(rows):
            if not cells:
                continue
            char_start = _line_offset(offsets, line_start_idx)
            char_end = _line_offset(offsets, line_end_idx)
            char_end = min(char_end, len(canonical))
            # Spec §8.2 canonical_excerpt 与 char range 严格对齐；表头单独存放在 extra
            # 和 search_text 中，避免重复原文。
            excerpt = canonical[char_start:char_end]
            search = _build_search_text(
                navigator.heading_path_tuple(),
                navigator.list_path(),
                " | ".join([*headers, *cells]) if headers else " | ".join(cells),
            )
            line_start, line_end = _line_range_from_char(offsets, char_start, char_end)
            drafts.append(
                EvidenceDraft(
                    block_kind="table_row",
                    heading_path=navigator.heading_path_tuple(),
                    char_start=char_start,
                    char_end=char_end,
                    line_start=line_start,
                    line_end=line_end,
                    canonical_excerpt=excerpt,
                    search_text=search,
                    content_hash=_content_hash(excerpt),
                    locator=f"table_row:{row_index}:{line_start}:{char_start}",
                    extra={
                        "row_index": row_index,
                        "headers": list(headers),
                        "cells": list(cells),
                    },
                )
            )
        return consumed

    def _emit_fence(
        self,
        token: Token,
        canonical: str,
        offsets: list[int],
        navigator: "_StructureNavigator",
        drafts: list[EvidenceDraft],
    ) -> None:
        info = (token.info or "").strip()
        line_start_idx, line_end_idx = (
            (token.map[0], token.map[1]) if token.map else (0, 0)
        )
        char_start = _line_offset(offsets, line_start_idx)
        char_end = _line_offset(offsets, line_end_idx)
        char_end = min(char_end, len(canonical))
        # Spec §8.2 canonical_excerpt 与 char range 严格对齐；token.content 是去掉
        # ``` 行的纯代码，便于拆分与 search_text 使用。
        full_excerpt = canonical[char_start:char_end]
        code_content = token.content
        for piece_index, (piece_start, piece_end, _) in enumerate(
            _split_code_block(full_excerpt, char_start)
        ):
            excerpt = canonical[piece_start:piece_end]
            # 拆分时 piece_index 对应代码片段索引，用于 locator 区分。
            search_text = code_content if piece_index == 0 else excerpt
            line_start, line_end = _line_range_from_char(offsets, piece_start, piece_end)
            drafts.append(
                EvidenceDraft(
                    block_kind="fenced_code",
                    heading_path=navigator.heading_path_tuple(),
                    char_start=piece_start,
                    char_end=piece_end,
                    line_start=line_start,
                    line_end=line_end,
                    canonical_excerpt=excerpt,
                    search_text=search_text,
                    content_hash=_content_hash(excerpt),
                    locator=(
                        f"fenced_code:{info or 'text'}:{piece_index}:{line_start}:{piece_start}"
                    ),
                    extra={
                        "language": info,
                        "piece_index": piece_index,
                    },
                )
            )


def _make_paragraph_draft(
    text: str,
    *,
    heading_path: tuple[str, ...],
    list_path: tuple[str, ...],
    char_start: int,
    char_end: int,
    line_start: int,
    line_end: int,
    ordinal_hint: int,
) -> EvidenceDraft:
    return EvidenceDraft(
        block_kind="paragraph",
        heading_path=heading_path,
        char_start=char_start,
        char_end=char_end,
        line_start=line_start,
        line_end=line_end,
        canonical_excerpt=text,
        search_text=_build_search_text(heading_path, list_path, text),
        content_hash=_content_hash(text),
        locator=f"paragraph:{ordinal_hint}:{line_start}:{char_start}",
        extra={"list_path": list(list_path)},
    )


def _locate_image_token(
    *,
    canonical: str,
    cursor: int,
    parent_end: int,
    alt: str,
    src: str,
) -> tuple[int, int, str]:
    """Spec §8.1 image Evidence 定位：优先精确匹配字面值，回退到 URL 边界扫描。

    返回 ``(char_start, char_end, excerpt)``；未找到返回 ``(0, 0, "")``。 ``excerpt``
    永远等于 ``canonical[char_start:char_end]``，保证与 canonical text 对齐。
    """

    literal = f"![{alt}]({src})"
    pos = canonical.find(literal, cursor, parent_end)
    if pos != -1:
        return pos, pos + len(literal), literal

    if not src:
        return 0, 0, ""

    # 回退：以 URL 子串为锚点，向左查找最近的 ``![``，向右查找 ``)``。alt 中
    # 含转义字符或 emoji 时，``child.content`` 与 canonical 字面值可能不同。
    url_pos = canonical.find(f"]({src})", cursor, parent_end)
    if url_pos == -1:
        return 0, 0, ""
    url_end = url_pos + len(f"]({src})") + 1  # 含右括号
    if url_end > parent_end:
        url_end = parent_end
    # 向左查找最近的 ``![``
    bang_bracket = canonical.rfind("![", cursor, url_pos)
    if bang_bracket == -1:
        return 0, 0, ""
    excerpt = canonical[bang_bracket:url_end]
    return bang_bracket, url_end, excerpt


def _build_search_text(
    heading_path: tuple[str, ...],
    list_path: tuple[str, ...],
    text: str,
) -> str:
    prefix_parts: list[str] = []
    if heading_path:
        prefix_parts.extend(heading_path)
    if list_path:
        prefix_parts.extend(list_path)
    if prefix_parts:
        return " | ".join(prefix_parts) + " | " + text
    return text


def _inline_char_range(token: Optional[Token], canonical_text: str) -> tuple[int, int]:
    if token is None or not token.map:
        return (0, 0)
    start_line = token.map[0]
    end_line = token.map[1] if len(token.map) > 1 else start_line + 1
    char_start = _line_offset_for(canonical_text, start_line)
    char_end = _line_offset_for(canonical_text, end_line)
    return char_start, min(char_end, len(canonical_text))


def _line_offset_for(canonical_text: str, line_index: int) -> int:
    if line_index <= 0:
        return 0
    offset = 0
    for _ in range(line_index):
        next_newline = canonical_text.find("\n", offset)
        if next_newline == -1:
            return len(canonical_text)
        offset = next_newline + 1
    return offset


def _line_offset(offsets: list[int], line_index: int) -> int:
    if line_index < 0:
        return 0
    if line_index >= len(offsets):
        return offsets[-1] if offsets else 0
    return offsets[line_index]


def _count_block_kinds(drafts: list[EvidenceDraft]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for draft in drafts:
        summary[draft.block_kind] = summary.get(draft.block_kind, 0) + 1
    return summary


def _collect_list_item_inline(
    tokens: list[Token], index: int, canonical: str
) -> tuple[str, Optional[Token], int]:
    """返回 (inline_text, inline_token, consumed_tokens)。

    consumed_tokens 包含 list_item_open 到 list_item 内首个 paragraph_close（或等价位置）
    的 token 数，但不包含 nested list——nested list 由外层 _consume_list_item 处理。
    """

    cursor = index + 1
    end = index + 1
    while cursor < len(tokens):
        current = tokens[cursor]
        if current.type == "list_item_close":
            end = cursor
            break
        if current.type in {"bullet_list_open", "ordered_list_open"}:
            end = cursor
            break
        if current.type == "inline":
            inline = current
            # 继续找到 paragraph_close 或 list_item_close
            scan = cursor + 1
            while scan < len(tokens):
                nxt = tokens[scan]
                if nxt.type in {"paragraph_close", "list_item_close"} or nxt.type in {
                    "bullet_list_open",
                    "ordered_list_open",
                }:
                    return inline.content.strip(), inline, scan - index
                scan += 1
            return inline.content.strip(), inline, scan - index
        cursor += 1
    return "", None, max(1, end - index)


def _split_paragraph(
    text: str, base_offset: int
) -> Iterable[tuple[int, int, str]]:
    """Spec §8.1：普通文本 Evidence ≤ 2000 chars，超长按句子边界拆分。

    返回 (piece_start, piece_end, piece_text)；piece_text = text[start:end]，
    保证与 char range 严格对齐。pieces 之间无重叠且无空隙。
    """

    if len(text) <= PARAGRAPH_TARGET_CHARS:
        yield (base_offset, base_offset + len(text), text)
        return
    cursor = 0
    while cursor < len(text):
        remainder = text[cursor:]
        if len(remainder) <= PARAGRAPH_TARGET_CHARS:
            yield (base_offset + cursor, base_offset + len(text), remainder)
            return
        boundary = _last_sentence_boundary(remainder, PARAGRAPH_TARGET_CHARS)
        if boundary <= 0:
            boundary = PARAGRAPH_TARGET_CHARS
        piece_text = remainder[:boundary]
        yield (
            base_offset + cursor,
            base_offset + cursor + boundary,
            piece_text,
        )
        cursor += boundary


def _last_sentence_boundary(text: str, window: int) -> int:
    """在 text[:window] 中找最后一个句子边界位置（inclusive）。"""

    search_window = min(len(text), window)
    last = -1
    for match in _SENTENCE_BOUNDARY_RE.finditer(text, 0, search_window):
        last = match.end()
    if last > 0:
        return last
    # 找不到句子边界时退化为换行或空格
    fallback_newline = text.rfind("\n", 0, search_window)
    if fallback_newline > 0:
        return fallback_newline + 1
    fallback_space = text.rfind(" ", 0, search_window)
    if fallback_space > 0:
        return fallback_space + 1
    return search_window


def _split_code_block(
    text: str, base_offset: int
) -> Iterable[tuple[int, int, str]]:
    """Spec §8.1：fenced code / table cell 允许到 8000 chars，超长按行边界拆分。

    返回 (piece_start, piece_end, piece_text)；piece_text = text[start:end]，
    保证与 char range 严格对齐。pieces 之间无重叠且无空隙。
    """

    if len(text) <= BLOCK_TARGET_CHARS:
        yield (base_offset, base_offset + len(text), text)
        return
    cursor = 0
    while cursor < len(text):
        remainder = text[cursor:]
        if len(remainder) <= BLOCK_TARGET_CHARS:
            yield (base_offset + cursor, base_offset + len(text), remainder)
            return
        last_newline = remainder.rfind("\n", 0, BLOCK_TARGET_CHARS)
        if last_newline <= 0:
            cut = BLOCK_TARGET_CHARS
        else:
            cut = last_newline + 1
        piece_text = remainder[:cut]
        yield (
            base_offset + cursor,
            base_offset + cursor + cut,
            piece_text,
        )
        cursor += cut


class _StructureNavigator:
    """跟踪 heading 与 list 嵌套状态，为 Evidence 提供 heading_path 与 list_path。"""

    def __init__(self) -> None:
        self._headings: list[tuple[int, str]] = []
        self._list_stack: list[tuple[int, list[str]]] = []
        self._blockquote_depth = 0

    def push_heading(self, depth: int, title: str) -> None:
        self._headings = [(d, t) for d, t in self._headings if d < depth]
        self._headings.append((depth, title))

    def heading_path_tuple(self) -> tuple[str, ...]:
        return tuple(text for _, text in self._headings)

    def top_headings(self) -> list[str]:
        seen: list[str] = []
        for _, text in self._headings:
            if text and text not in seen:
                seen.append(text)
            if len(seen) >= 10:
                break
        return seen

    def enter_list_level(self, level: int) -> None:
        self._list_stack.append((level, []))

    def exit_list_level(self, level: int) -> None:
        self._list_stack = [
            (lvl, items)
            for lvl, items in self._list_stack
            if lvl != level
        ]

    def push_list_item(self, level: int, text: str) -> None:
        for idx in range(len(self._list_stack)):
            lvl, items = self._list_stack[idx]
            if lvl == level:
                items.append(text)
                return
        if self._list_stack:
            self._list_stack[-1][1].append(text)

    def pop_list_item(self, level: int) -> None:
        for idx in range(len(self._list_stack)):
            lvl, items = self._list_stack[idx]
            if lvl == level and items:
                items.pop()
                return

    def list_path(self) -> tuple[str, ...]:
        path: list[str] = []
        for _, items in self._list_stack:
            # 跳过空串占位（被 evidence policy 过滤的 list item），避免噪声文本
            # 污染嵌套子项的 list_path -> search_text -> FTS。
            if items and items[-1]:
                path.append(items[-1])
        return tuple(path)

    def list_locator(self) -> str:
        return ".".join(str(idx + 1) for idx, _ in enumerate(self._list_stack))

    def enter_blockquote(self) -> None:
        self._blockquote_depth += 1

    def exit_blockquote(self) -> None:
        if self._blockquote_depth > 0:
            self._blockquote_depth -= 1

    def blockquote_depth(self) -> int:
        return self._blockquote_depth
