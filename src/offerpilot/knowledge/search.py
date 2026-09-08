"""KI-08：Spec §15 Evidence 检索的查询解析与错误契约。

实现要点：
- 中文长问句按 trigram 切分，不再作为强制精确短语。
- ASCII identifier / 英文词组安全引用，防 FTS5 语法字符注入。
- 短查询 (< 3 字符) 走 LIKE 子串回退，避免全库无界扫描。
- 解析失败、空查询与 FTS5 不可用通过 ``SearchError`` 显式抛出。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class SearchError(Exception):
    """Spec §13 错误码 ``fts_query_invalid`` / ``fts_unavailable``。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ParsedQuery:
    """``parse_query`` 的稳定输出。

    - ``mode == "empty"``：查询为空，调用方直接返回空结果。
    - ``mode == "fts"``：使用 ``match_expr`` 进入 FTS5 MATCH。
    - ``mode == "substring"``：使用 ``terms`` 走有上限的 LIKE 回退。
    """

    mode: str
    match_expr: str = ""
    terms: tuple[str, ...] = field(default_factory=tuple)
    original: str = ""


_FTS_SPECIAL_CHARS = re.compile(r'["\*\(\)\+:]+')
_ASCII_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+(?:[-./][A-Za-z0-9_]+)*")
_CJK_PATTERN = re.compile(
    "[㐀-鿿豈-﫿぀-ヿ가-힯]"
)
_SUBSTRING_MAX_LEN = 64


def parse_query(raw: str) -> ParsedQuery:
    stripped = (raw or "").strip()
    if not stripped:
        return ParsedQuery(mode="empty", original=raw or "")

    ascii_tokens = _extract_ascii_tokens(stripped)
    cjk_grams = _extract_cjk_trigrams(stripped)

    if not ascii_tokens and not cjk_grams:
        # 纯标点或不可识别字符：走有界 substring，长度被截断
        return ParsedQuery(
            mode="substring",
            terms=(stripped[:_SUBSTRING_MAX_LEN],),
            original=raw or "",
        )

    # Spec §15：少于 3 字符查询使用有上限的精确/子串回退，避免全库无界扫描。
    # ASCII 单 token < 3 字符也走 LIKE（如 "AI"、"Go"）。
    if len(stripped) < 3:
        return ParsedQuery(
            mode="substring",
            terms=(stripped[:_SUBSTRING_MAX_LEN],),
            original=raw or "",
        )
    if len(ascii_tokens) == 1 and len(ascii_tokens[0]) < 3 and not cjk_grams:
        return ParsedQuery(
            mode="substring",
            terms=(ascii_tokens[0],),
            original=raw or "",
        )

    match_expr = _build_match_expr(ascii_tokens, cjk_grams)
    if not match_expr:
        return ParsedQuery(
            mode="substring",
            terms=(stripped[:_SUBSTRING_MAX_LEN],),
            original=raw or "",
        )
    return ParsedQuery(
        mode="fts",
        match_expr=match_expr,
        terms=tuple(ascii_tokens + cjk_grams),
        original=raw or "",
    )


def _extract_ascii_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for match in _ASCII_TOKEN_PATTERN.finditer(text):
        token = match.group(0)
        if token.lower() in seen:
            continue
        seen.add(token.lower())
        tokens.append(token)
    return tokens


def _extract_cjk_trigrams(text: str) -> list[str]:
    cjk_text = "".join(_CJK_PATTERN.findall(text))
    if not cjk_text:
        return []
    if len(cjk_text) <= 3:
        return [cjk_text]
    grams: list[str] = []
    seen: set[str] = set()
    for i in range(len(cjk_text) - 2):
        gram = cjk_text[i : i + 3]
        if gram in seen:
            continue
        seen.add(gram)
        grams.append(gram)
    return grams


def _build_match_expr(ascii_tokens: list[str], cjk_grams: list[str]) -> str:
    """构造 FTS5 MATCH 表达式。

    每个 token 用双引号包裹（防 FTS5 语法字符注入），并通过 ``OR`` 连接。
    Spec §15：不再把无空格中文整句作为一个强制精确短语。
    """
    parts: list[str] = []
    for token in ascii_tokens:
        cleaned = _FTS_SPECIAL_CHARS.sub(" ", token).strip()
        if cleaned:
            parts.append(f'"{cleaned}"')
    for gram in cjk_grams:
        cleaned = _FTS_SPECIAL_CHARS.sub(" ", gram).strip()
        if cleaned:
            parts.append(f'"{cleaned}"')
    return " OR ".join(parts)
