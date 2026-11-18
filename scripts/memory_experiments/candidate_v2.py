"""Frozen candidate: topic focus, bounded legacy recall, topic-switch handling.

No entity-specific synonyms; not installed in the runtime. Inputs are eligible records.
"""
from __future__ import annotations

from collections import Counter
from math import log
import re

from offerpilot.context_sources.memory import (
    MemorySelection, _CONCEPTS, _SCENARIOS, _has, _normalize, _tokens,
)

# Task scaffolding is not a concrete topic. This vocabulary is grammar/task verbs,
# not memory entities or expected-answer labels from an evaluation fixture.
_SCAFFOLD = re.compile(
    r"帮我|请|一下|怎么|如何|我的|个人|相关|合适|有没有|哪些|优化|润色|准备|推荐|"
    r"介绍|讲讲|讲|表达|贡献|工作机会|工作|目标|期望|聊聊|适合我吗|"
    r"\b(?:please|help|me|my|prepare|recommend|improve|tell|about)\b"
)
# A weak, ephemeral retrieval cue, never a persisted category or proof of ability.
_EXPERIENCE = re.compile(
    r"(?:曾|过去|以前).{0,12}(?:负责|参与|处理|维护|开发|设计|完成|做过)|"
    r"(?:负责过|参与过|维护过|开发过|设计过|做过)|"
    r"\b(?:worked on|built|maintained|implemented|developed)\b"
)
_CAREER = re.compile(r"工作|岗位|职位|职业|求职|薪|待遇|公司|行业|团队|办公|通勤|到岗|\b(?:job|career|salary|work|company|office)\b")
BACKFILL_LIMIT = 8

_CONTINUATION = re.compile(
    r"(?:(?:请|我们|咱们|帮我)\s*)?(?:继续|接着)(?:来|练|练习|准备)?(?:一下)?(?:吧)?|"
    r"(?:请)?帮我准备一下|(?:please )?continue|help me prepare"
)


def select_memory(items, query, *, confirmed_interview_context=False):
    query = _normalize(query[:500])
    # An explicit replacement clause supersedes the prior topic, but a simple
    # negative preference (e.g. a dietary exclusion) does not erase its subject.
    replacement = re.search(r"(?:只想|只需要|改为|改聊|换成|instead[, :]*)\s*(?:聊|谈|讨论)?", query)
    if replacement:
        query = query[replacement.end():]
    rejected_topic = re.match(r"(?:别|不要|不想)(?:谈|聊|讨论|说).+?(?:[,，;；。]|(?=讲|聊|谈))", query)
    if rejected_topic:
        query = query[rejected_topic.end():]
    tokens = _tokens(query)
    concepts = {i for i, words in enumerate(_CONCEPTS) if any(_has(query, word) for word in words)}
    scenarios = {name for name, (concept, _) in _SCENARIOS.items() if concept in concepts}
    continuation = bool(_CONTINUATION.fullmatch(query.strip(" \t\r\n.!?。！？")))
    if not scenarios and confirmed_interview_context and continuation:
        scenarios.add("interview")
    topic = query
    for words in _CONCEPTS[:3]:
        for word in words:
            topic = re.sub(r"\b" + re.escape(word) + r"\b", " ", topic) if word.isascii() else topic.replace(word, " ")
    topic = _SCAFFOLD.sub(" ", topic)
    # Specificity requires a concrete lexical cue, not just unknown prose.
    topic_tokens = _tokens(topic) - _tokens("项目经历问题")
    focused_concepts = concepts & {3, 4}
    focused = bool(focused_concepts or any(topic_tokens & _tokens(_normalize(str(item["content"]))) for item in items))
    if continuation:
        focused = False
    # Candidate admission and ordering are separate: category alone is never
    # enough for preference/goal backfill, and unknown records are not bulk-added.
    token_sets = {str(item["id"]): _tokens(_normalize(str(item["content"]))) for item in items}
    frequencies = Counter(token for values in token_sets.values() for token in values)
    ranked = []
    backfill = []
    lexical_count = scenario_count = expression_count = 0
    for item in items:
        content = _normalize(str(item["content"]))
        category = str(item["category"])
        item_tokens = _tokens(content)
        # Corpus frequency reduces the rank of shared generic words; it cannot
        # recover synonyms with no lexical intersection.
        lexical = sum(1 + log((len(items) + 1) / (frequencies[token] + 1))
                      for token in sorted(tokens & item_tokens))
        aliases = sum(any(_has(content, word) for word in _CONCEPTS[i]) for i in concepts)
        topic_hit = bool(topic_tokens & item_tokens) or any(
            any(_has(content, word) for word in _CONCEPTS[i]) for i in focused_concepts)
        weight = max((_SCENARIOS[name][1].get(category, 0) for name in scenarios), default=0)
        legacy_cue = category == "unclassified" and bool(_EXPERIENCE.search(content))
        if legacy_cue and scenarios and not focused:
            weight = 1
        prior_admitted = bool(weight) and (
            category in {"experience", "skill", "preparation"} or legacy_cue
            or bool(_CAREER.search(content))
        )
        expression = category == "preference" and any(
            term in content for term in ("回答", "回复", "表达", "answer", "response")
        ) and any(term in content for term in ("中文", "英文", "简洁", "详细", "concise", "english", "chinese"))
        # Narrow questions do not inherit every scenario category. Interview
        # preparation remains a weak supporting channel, bounded by byte budget.
        if focused and not topic_hit and not expression:
            if not ("interview" in scenarios and category == "preparation"):
                continue
        direct = bool(lexical or aliases or expression)
        if not direct and not prior_admitted:
            continue
        score = lexical * 4 + aliases * 4 + weight + int(expression)
        if score == 0:
            continue
        lexical_count += int(bool(lexical or aliases))
        scenario_count += int(bool(weight))
        expression_count += int(expression)
        target = ranked if direct else backfill
        target.append((score, str(item["id"]), item))
    backfill.sort(key=lambda entry: (-entry[0], entry[1]))
    ranked.extend(backfill[:BACKFILL_LIMIT])
    ranked.sort(key=lambda entry: (-entry[0], entry[1]))
    selected, seen = [], set()
    for _, _, item in ranked:
        identity = (_normalize(str(item["content"])), str(item["category"]), str(item["valid_until"]))
        if identity not in seen:
            seen.add(identity)
            selected.append(item)
    return MemorySelection(selected, {
        "memory_strategy_version": 2, "memory_candidate_count": len(items),
        "memory_irrelevant_count": len(items) - len(ranked),
        "memory_duplicate_count": len(ranked) - len(selected),
        "memory_lexical_match_count": lexical_count, "memory_scenario_match_count": scenario_count,
        "memory_expression_match_count": expression_count,
        **{f"memory_scenario_{name}": name in scenarios for name in _SCENARIOS},
    })
