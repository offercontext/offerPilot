"""Spec KBR-03：Evidence eligibility policy。

职责分离：本模块只决定一个已解析的正文块是否应当发射 Evidence，不修改 canonical
text，也不触碰 AST 的原始位置。规则用确定性结构信号匹配；无法确认的块默认保留为
Evidence（Spec Implementation Decisions）。

Spec 红线：
- 全局规则只覆盖低歧义结构（空链接壳、纯装饰图片壳、明确导出控件文本）。
- 平台/导出噪声由**明确适配器**承载，适配器按**确定性来源信号**选择（文档级结构语法、
  文件扩展名、ingest origin_url），绝不凭正文中出现品牌名或关键词启用。一个适配器的
  信号不得顺带启用另一个适配器；没有确定性信号时默认保留（即使残留少量元数据噪声）。
- 平台适配器：Obsidian ``![[...]]`` embed / ``%%...%%`` 注释、Evernote ``<en-*>`` 残片、
  web-article 作者署名 / 阅读数 / 翻页导航。
- Evidence 规则变化视为 Extraction 版本变化（见 extractor.EXTRACTOR_VERSION 升级）。

本模块对所有规则保持稳定 ``rule_id``，供 Snapshot 结构摘要按 rule_id 聚合命中数量。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional


# Spec Implementation Decisions：Evidence 规则带稳定版本号；规则变化 = Extraction
# 版本变化（由 extractor 升 EXTRACTOR_VERSION 体现）。本版本号写入 Snapshot 摘要，
# 使维护者可确定性重建 Snapshot 并区分 policy 代际。
#
# ⚠ 升级护栏：修改 EVIDENCE_POLICY_VERSION 必须同时升级 extractor.EXTRACTOR_VERSION。
# Spec 要求 Evidence 规则变化视为 Extraction 版本变化——policy 变化会改变
# structure_manifest（filtered_by_rule / evidence_policy_version）从而改变 digest；
# 若只升 policy 不升 extractor，同一 (source_id, extractor_version) 重提取会触发
# source_integrity_mismatch（digest drift）。这是正确的护栏，但耦合隐式，故在此显式声明。
#
# evidence-policy-2：把作者署名 / 阅读数 / 翻页导航 / Obsidian / Evernote 规则从全局
# 收敛进按确定性信号选择的平台适配器，消除普通 Markdown 中 ``作者：SQLite 是最佳选择``
# / ``by: Python 3`` / 单独「目录」被误删的缺陷（Spec 第 35/58/71/72 行）。
EVIDENCE_POLICY_VERSION = "evidence-policy-2"


@dataclass(frozen=True)
class EligibilityDecision:
    """``emit=True`` 表示保留为 Evidence（``rule_id`` 为 None）；``emit=False`` 表示过滤。"""

    emit: bool
    rule_id: Optional[str] = None


# ---------------------------------------------------------------------------
# 适配器：按确定性来源信号选择（不靠品牌关键词）
# ---------------------------------------------------------------------------

ADAPTER_OBSIDIAN = "obsidian"
ADAPTER_EVERNOTE = "evernote"
ADAPTER_WEB_ARTICLE = "web_article"

# 适配器 id 闭集；ExtractionContext 校验用。
ADAPTER_IDS: frozenset[str] = frozenset(
    {ADAPTER_OBSIDIAN, ADAPTER_EVERNOTE, ADAPTER_WEB_ARTICLE}
)


@dataclass(frozen=True)
class ExtractionContext:
    """Spec KBR-03：单次 Extraction 的平台适配器激活集合。

    适配器选择与块 eligibility 分离——``active_adapters`` 由调用方（extractor/worker）
    按**确定性来源信号**（文档级结构语法、文件扩展名、ingest origin_url）预先算好传入，
    ``evaluate_block`` 只负责在已激活的适配器范围内判定单块。空集 = 无平台适配器，
    仅全局低歧义规则生效，不确定块默认保留。
    """

    active_adapters: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        invalid = self.active_adapters - ADAPTER_IDS
        if invalid:
            raise ValueError(f"未知 adapter id：{invalid}")


EMPTY_CONTEXT = ExtractionContext()


# 文档级结构语法信号（仅用于适配器选择，匹配的是语法存在性，不是正文关键词）。
# Obsidian：``![[...]]`` embed 或 ``%%...%%`` 注释。Evernote：``<en-*>`` ENML 标签。
_OBSIDIAN_EMBED_SIGNAL_RE = re.compile(r"!\[\[")
_OBSIDIAN_COMMENT_SIGNAL_RE = re.compile(r"%%[\s\S]*?%%")
_EVERNOTE_TAG_SIGNAL_RE = re.compile(r"</?en-(?:note|media|todo|crypt)\b", re.IGNORECASE)


def _doc_has_obsidian_syntax(canonical: str) -> bool:
    return (
        _OBSIDIAN_EMBED_SIGNAL_RE.search(canonical) is not None
        or _OBSIDIAN_COMMENT_SIGNAL_RE.search(canonical) is not None
    )


def _doc_has_evernote_syntax(canonical: str) -> bool:
    return _EVERNOTE_TAG_SIGNAL_RE.search(canonical) is not None


def select_adapters(
    canonical: str, *, origin_url: str = "", filename: str = ""
) -> frozenset[str]:
    """Spec KBR-03：按确定性来源信号选择平台适配器（信号严格隔离、互不串扰）。

    - Obsidian：canonical 出现 ``![[...]]`` 或 ``%%...%%`` 结构语法 → 仅启用 Obsidian 规则。
    - Evernote：canonical 出现 ``<en-*>`` 标签，或文件扩展名 ``.enex`` → 仅启用 Evernote 规则。
    - web-article：ingest 时 ``origin_url`` 非空 → 仅启用作者署名 / 阅读数 / 翻页导航规则。
    - 一个信号**不得**顺带启用别的 adapter；品牌名 / 正文关键词 / 文件标题不作信号。
    - 无任何信号 → 空集，平台规则一律不执行（不确定块默认保留）。
    """

    adapters: set[str] = set()
    if _doc_has_obsidian_syntax(canonical):
        adapters.add(ADAPTER_OBSIDIAN)
    if _doc_has_evernote_syntax(canonical) or (
        bool(filename) and filename.lower().endswith(".enex")
    ):
        adapters.add(ADAPTER_EVERNOTE)
    if origin_url and origin_url.strip():
        adapters.add(ADAPTER_WEB_ARTICLE)
    return frozenset(adapters)


# 面向用户的稳定展示标签（不含正则/实现细节）。普通用户界面只展示 label，不展示 rule_id
# 或正则。新增规则必须同时在此登记 label。
RULE_LABELS: dict[str, str] = {
    "empty_link_shell": "空链接壳",
    "decorative_image_shell": "纯装饰图片壳",
    "obsidian_wiki_embed": "Obsidian 资源引用",
    "obsidian_comment": "Obsidian 注释",
    "evernote_resource_fragment": "Evernote 导出残片",
    "author_byline": "作者署名",
    "reading_count": "阅读数元信息",
    "navigation": "翻页导航",
}


# ---------------------------------------------------------------------------
# 低歧义全局结构规则（始终执行）
# ---------------------------------------------------------------------------

# 整块恰好是一个 markdown link ``[text](url)``。
_SOLE_LINK_RE = re.compile(r"^\[([^\]\n]*)\]\(([^)\n]*)\)$")
# 整块恰好是一个 markdown image ``![alt](src)``。
_SOLE_IMAGE_RE = re.compile(r"^!\[([^\]\n]*)\]\(([^)\n]*)\)$")

# Obsidian wiki embed：``![[<name>]]``（资源引用/嵌入）。整块结构，确定性。
_OBSIDIAN_EMBED_RE = re.compile(r"^!\[\[[^\]\n]+\]\]$")

# Evernote ENEX 导出残片：<en-note>/<en-media...>/<en-todo> 标签。html 已被 extractor
# 禁用，因此这类标签在 canonical 中是纯文本残片，整块匹配即为导出残片。
_EVERNOTE_FRAGMENT_RE = re.compile(r"^</?en-(?:note|media|todo|crypt)[^>]*>/?$")


def _is_empty_link_shell(text: str) -> bool:
    """空链接壳：整块是单个链接，且 url 为空或 link text 为空。"""

    match = _SOLE_LINK_RE.match(text)
    if match is None:
        return False
    link_text = match.group(1).strip()
    link_url = match.group(2).strip()
    return not link_url or not link_text


def _is_decorative_image_shell(text: str) -> bool:
    """纯装饰图片壳：整块是单个图片且 alt 为空（无法支撑知识陈述）。"""

    match = _SOLE_IMAGE_RE.match(text)
    if match is None:
        return False
    return not match.group(1).strip()


def _is_obsidian_comment(text: str) -> bool:
    """Obsidian 注释块：``%%...%%``。要求首尾均为 ``%%`` 且长度 >= 4。"""

    return text.startswith("%%") and text.endswith("%%") and len(text) >= 4


# ---------------------------------------------------------------------------
# 平台/导出适配器规则（仅对应 adapter 激活时执行）
# ---------------------------------------------------------------------------

# 作者署名：``作者：<name>`` / ``Author: <name>`` / ``by: <name>``（web-article adapter）。
# 匹配 1-2 个 name token（CJK、字母、数字、@ . _ - ·，按空格切分计 token）；3 个及
# 以上 token 的正文句子（如 ``作者：X 认为 Y 是 Z``）受保护。
#
# 该规则只在 web-article adapter（ingest origin_url 非空）激活时执行——普通 Markdown
# 中 ``作者：SQLite 是最佳选择`` / ``by: Python 3`` 这类「冒号 + 2 token」短句保留为
# Evidence（Spec 第 35 行 User Story：正文 key:value 必须可检索）。web-article 来源下，
# 冒号 + 短名仍按结构化署名过滤（确定性优先于覆盖面），这是该 adapter 的接受 trade-off。
_BYLINE_RE = re.compile(
    r"^(?:作者|文章作者|本文作者|author|by)[：:]\s*[@\w·._\-]+(?:\s+[@\w·._\-]+)?\s*$",
    re.IGNORECASE,
)

# 阅读数：``阅读：1234`` / ``阅读数：1.2万`` / ``1234 次阅读`` / ``views: 500``（web-article）。
_READING_COUNT_PREFIX_RE = re.compile(
    r"^(?:阅读数?|阅读量|浏览[量数]?|views?|reads?)[：:\s]*[\d.,]+\s*(?:万|次|篇)?\s*$",
    re.IGNORECASE,
)
_READING_COUNT_SUFFIX_RE = re.compile(
    r"^[\d.,]+\s*(?:次阅读|篇阅读|views?|reads?)\s*$",
    re.IGNORECASE,
)

# 翻页导航 anchor 词汇（中英文）（web-article）。
_NAV_ANCHOR_RE = re.compile(
    r"^(?:上一篇|下一篇|上一页|下一页|首页|末页|目录|索引|返回|prev|next|previous|home|toc)$",
    re.IGNORECASE,
)
_NAV_LINK_RE = re.compile(r"\[([^\]\n]*)\]\(([^)\n]*)\)")
# 导航块中 link 之外允许的分隔符（箭头/竖线/点/破折号/空白）。
_NAV_PUNCT_ONLY_RE = re.compile(r"^[\s←→|·•\-\—>]*$")
_NAV_PUNCT_SPLIT_RE = re.compile(r"[\s←→|·•\-\—>]+")


def _is_navigation(text: str) -> bool:
    """翻页导航：整块由导航链接或导航标签 + 导航分隔符构成。

    - 链接型：至少一个 markdown link，所有 link anchor 是导航词汇，link 外只剩分隔符。
    - 纯文本型：不含 link，按分隔符切分后全部 token 都是导航词汇。
    """

    links = list(_NAV_LINK_RE.finditer(text))
    if links:
        for match in links:
            if _NAV_ANCHOR_RE.match(match.group(1).strip()) is None:
                return False
        outside_chunks: list[str] = []
        pos = 0
        for match in links:
            outside_chunks.append(text[pos:match.start()])
            pos = match.end()
        outside_chunks.append(text[pos:])
        return _NAV_PUNCT_ONLY_RE.match("".join(outside_chunks)) is not None
    # 纯文本导航：整段全部由闭集导航词构成（如单独「目录」）。仅 web-article adapter 下过滤；
    # 普通 Markdown 中独立「目录」保留为 Evidence（避免误删章节标题样短词）。
    tokens = [token for token in _NAV_PUNCT_SPLIT_RE.split(text.strip()) if token]
    if not tokens:
        return False
    return all(_NAV_ANCHOR_RE.match(token) is not None for token in tokens)


# 规则分组：全局始终执行；平台适配器规则仅在对应 adapter 激活时执行。组内按优先级排序。
_GLOBAL_RULES: tuple[tuple[str, Callable[[str], bool]], ...] = (
    ("empty_link_shell", _is_empty_link_shell),
    ("decorative_image_shell", _is_decorative_image_shell),
)
_OBSIDIAN_RULES: tuple[tuple[str, Callable[[str], bool]], ...] = (
    ("obsidian_comment", _is_obsidian_comment),
    ("obsidian_wiki_embed", lambda text: _OBSIDIAN_EMBED_RE.match(text) is not None),
)
_EVERNOTE_RULES: tuple[tuple[str, Callable[[str], bool]], ...] = (
    (
        "evernote_resource_fragment",
        lambda text: _EVERNOTE_FRAGMENT_RE.match(text) is not None,
    ),
)
_WEB_ARTICLE_RULES: tuple[tuple[str, Callable[[str], bool]], ...] = (
    ("navigation", _is_navigation),
    ("author_byline", lambda text: _BYLINE_RE.match(text) is not None),
    (
        "reading_count",
        lambda text: _READING_COUNT_PREFIX_RE.match(text) is not None
        or _READING_COUNT_SUFFIX_RE.match(text) is not None,
    ),
)

# adapter id → 其规则组。evaluate_block 按此遍历已激活适配器。
_ADAPTER_RULES: dict[str, tuple[tuple[str, Callable[[str], bool]], ...]] = {
    ADAPTER_OBSIDIAN: _OBSIDIAN_RULES,
    ADAPTER_EVERNOTE: _EVERNOTE_RULES,
    ADAPTER_WEB_ARTICLE: _WEB_ARTICLE_RULES,
}

# 全量规则视图（全局 + 各适配器并集），供 registry 交叉校验与外部按 rule_id 统计。
_RULES: tuple[tuple[str, Callable[[str], bool]], ...] = (
    _GLOBAL_RULES + _OBSIDIAN_RULES + _EVERNOTE_RULES + _WEB_ARTICLE_RULES
)

# 模块加载时交叉校验：_RULES 中每个 rule_id 都必须在 RULE_LABELS 登记。否则 api 层
# ``RULE_LABELS.get(rule_id, rule_id)`` 会 fallback 把内部 rule_id 当 label 展示给
# 用户，违背 Spec「普通 UI 不展示内部正则/实现细节」。
assert all(rule_id in RULE_LABELS for rule_id, _ in _RULES), (
    "每个 _RULES 中的 rule_id 必须在 RULE_LABELS 登记"
)


def evaluate_block(
    text: str, ctx: ExtractionContext = EMPTY_CONTEXT
) -> EligibilityDecision:
    """Spec：评估单个正文块（纯文本）的 Evidence 资格。

    ``text`` 是该块在 canonical Source 中的原文（调用方负责取片，内部 strip）。policy
    只对 paragraph / list_item / blockquote 三类块的纯文本判定；fence 与 table 不经过
    policy（代码与结构化数据始终是 Evidence）。``EligibilityDecision(emit=True)`` 表示
    保留；``emit=False`` 携带稳定 ``rule_id`` 表示过滤。无法确认的块默认保留。

    全局低歧义规则始终执行；平台适配器规则仅在 ``ctx.active_adapters`` 包含对应 adapter
    时执行（适配器由调用方按确定性来源信号预先选择）。
    """

    stripped = text.strip()
    if not stripped:
        return EligibilityDecision(emit=True)
    for rule_id, matcher in _GLOBAL_RULES:
        if matcher(stripped):
            return EligibilityDecision(emit=False, rule_id=rule_id)
    for adapter_id in (ADAPTER_OBSIDIAN, ADAPTER_EVERNOTE, ADAPTER_WEB_ARTICLE):
        if adapter_id in ctx.active_adapters:
            for rule_id, matcher in _ADAPTER_RULES[adapter_id]:
                if matcher(stripped):
                    return EligibilityDecision(emit=False, rule_id=rule_id)
    return EligibilityDecision(emit=True)
