"""Bounded, deterministic lexical retrieval over already eligible personal memory.

No inferred classifications are persisted. See the Knowledge/Memory code map.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

STRATEGY_VERSION = 1
# Small, explicit vocabulary: this is lexical matching, not semantic retrieval.
_CONCEPTS = (
    ("面试", "面谈", "interview"),
    ("岗位", "职位", "求职", "找工作", "job", "position"),
    ("简历", "履历", "resume", "cv"),
    ("远程", "居家办公", "remote"),
    ("薪资", "薪酬", "工资", "salary"),
    ("项目", "经历", "project", "experience"),
)
_SCENARIOS = {
    "interview": (0, {"experience": 3, "preparation": 3, "skill": 2, "goal": 1}),
    "job": (1, {"goal": 3, "preference": 3, "skill": 1, "experience": 1}),
    "resume": (2, {"experience": 3, "skill": 3, "goal": 1}),
}
_STOP = frozenset("帮我 请帮 一下 怎么 如何 可以 什么 当前 问题 我想 我的 我们 这个 那个 的是 一个".split())
_ASCII_STOP = frozenset("a an the is are to of and or for me my i please help with this".split())


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _has(text: str, word: str) -> bool:
    if word.isascii():
        return re.search(r"(?<![a-z0-9_])" + re.escape(word) + r"(?![a-z0-9_])", text) is not None
    return word in text


def _tokens(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9][a-z0-9_+#.-]*", text)) - _ASCII_STOP
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        tokens.update(run[index:index + 2] for index in range(len(run) - 1))
    return tokens - _STOP


@dataclass(frozen=True)
class MemorySelection:
    items: list[dict[str, object]]
    diagnostics: dict[str, bool | int]


def select_memory(
    items: list[dict[str, object]], query: str, *, confirmed_interview_context: bool = False,
) -> MemorySelection:
    """Select from a validated snapshot; never load or fall back to unfiltered rows.

    The current request supplies relevance hints only, never scope/authorization.
    All candidates (at most the repository's 100) are ranked before byte budgeting.
    """
    query = _normalize(query[:500])
    query_tokens = _tokens(query)
    concepts = {index for index, words in enumerate(_CONCEPTS) if any(_has(query, word) for word in words)}
    scenarios = {name for name, (concept, _) in _SCENARIOS.items() if concept in concepts}
    # Only use a validated, enabled Readiness contributor as a fallback hint.
    # An explicit current request wins over a previously bound interview.
    if not scenarios and confirmed_interview_context:
        scenarios.add("interview")
    ranked: list[tuple[int, str, dict[str, object]]] = []
    lexical_count = 0
    scenario_count = 0
    expression_count = 0
    for item in items:
        content = _normalize(str(item["content"]))
        lexical = min(6, len(query_tokens & _tokens(content)))
        aliases = sum(any(_has(content, word) for word in _CONCEPTS[index]) for index in concepts)
        weight = max((_SCENARIOS[name][1].get(str(item["category"]), 0) for name in scenarios), default=0)
        expression = str(item["category"]) == "preference" and any(
            term in content for term in ("回答", "回复", "表达", "answer", "response")
        ) and any(term in content for term in ("中文", "英文", "简洁", "详细", "concise", "english", "chinese"))
        score = lexical * 4 + aliases * 4 + weight + int(expression)
        if score == 0:
            continue
        expression_count += int(expression)
        lexical_count += int(bool(lexical or aliases))
        scenario_count += int(bool(weight))
        ranked.append((score, str(item["id"]), item))
    ranked.sort(key=lambda entry: (-entry[0], entry[1]))
    selected: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for _, _, item in ranked:
        # Metadata changes meaning: never collapse differing categories/deadlines.
        identity = (_normalize(str(item["content"])), str(item["category"]), str(item["valid_until"]))
        if identity not in seen:
            seen.add(identity)
            selected.append(item)
    return MemorySelection(selected, {
        "memory_strategy_version": STRATEGY_VERSION,
        "memory_candidate_count": len(items),
        "memory_irrelevant_count": len(items) - len(ranked),
        "memory_duplicate_count": len(ranked) - len(selected),
        "memory_lexical_match_count": lexical_count,
        "memory_scenario_match_count": scenario_count,
        "memory_expression_match_count": expression_count,
        **{f"memory_scenario_{name}": name in scenarios for name in _SCENARIOS},
    })
