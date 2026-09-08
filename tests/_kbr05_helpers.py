"""KBR-05 测试夹具 helper。

构造同时含 citation missing + support partial + coverage missing 的候选 Brief，
用于验证汇总式单次 repair 与结构化 validation report。

设计约束：
- 不依赖固定 evidence id（真实 Extraction 的 id 是数据库自增），从 evidence 列表按
  heading_path 动态取代表 Evidence。
- 至少 3 个文本章节：章节 A/B 的代表被引用（A 多处、B 一处），章节 C 的代表不被任何
  block 引用 → 触发 coverage missing。
- key_points[0] 故意引用不存在的 ``ev_FAKE_MISSING`` → citation missing，且不调 Validator。
"""

from __future__ import annotations

import json
from typing import Any

MIXED_FAILURE_CONTENT = (
    "# 章节 A\n\n"
    "Evidence A 说明 OfferPilot 使用 SQLite 作为单一事实源。\n\n"
    "# 章节 B\n\n"
    "Evidence B 描述 Evidence 不重叠且可回读。\n\n"
    "# 章节 C\n\n"
    "Evidence C 涉及 Knowledge 检索与 Pilot 协作。\n"
)

_FAKE_MISSING_EVIDENCE_ID = "ev_FAKE_MISSING"


def _section_key_of(heading_path: tuple[str, ...] | list[str]) -> str:
    path = tuple(heading_path or ())
    if not path:
        return "__document__"
    return " / ".join(path)


def _group_text_evidence_by_section(
    evidence_items: list[Any],
) -> list[tuple[tuple[str, ...], str]]:
    """按 heading_path 聚合文本 Evidence，返回 (heading_path, 代表 evidence_id) 列表。

    顺序遵循 Evidence 在列表中的出现次序（即 Snapshot ordinal）。
    """
    seen: dict[tuple[str, ...], str] = {}
    order: list[tuple[str, ...]] = []
    for item in evidence_items:
        if getattr(item, "kind", "text") == "asset":
            continue
        heading = tuple(getattr(item, "heading_path", ()) or ())
        if heading not in seen:
            seen[heading] = str(item.id)
            order.append(heading)
    return [(heading, seen[heading]) for heading in order]


def build_mixed_failure_payload(evidence_items: list[Any]) -> dict[str, Any]:
    """构造同时含 citation missing + support partial + coverage missing 的 v2 payload。

    - overview[0] → 章节 A 代表（有效），Validator 预期 supported。
    - overview[1] → 章节 B 代表（有效），Validator 预期 partial。
    - key_points[0] → ``ev_FAKE_MISSING``（citation missing，不调 Validator）。
    - section_guides[0] → 章节 A 代表（有效）。
    - limitations[0] → 章节 A 代表（有效）。
    - 章节 C 代表不被任何 block 引用 → coverage missing。
    """
    sections = _group_text_evidence_by_section(evidence_items)
    assert len(sections) >= 3, (
        f"mixed-failure 夹具需要至少 3 个文本章节，实际 {len(sections)}"
    )
    ev_a = sections[0][1]
    ev_b = sections[1][1]
    heading_a = list(sections[0][0])
    section_key_a = _section_key_of(sections[0][0])
    return {
        "schema_version": 2,
        "language": "zh-CN",
        "overview": [
            {"statement": "章节 A 概述。", "evidence_ids": [ev_a]},
            {"statement": "章节 B 概述。", "evidence_ids": [ev_b]},
        ],
        "key_points": [
            {"statement": "越界引用占位。", "evidence_ids": [_FAKE_MISSING_EVIDENCE_ID]},
        ],
        "section_guides": [
            {
                "section_key": section_key_a,
                "heading_path": heading_a,
                "summary": "章节 A 导读摘要。",
                "evidence_ids": [ev_a],
            }
        ],
        "limitations": [
            {"statement": "章节 A 限制。", "evidence_ids": [ev_a]},
        ],
    }


def build_mixed_failure_brief_json(evidence_items: list[Any]) -> str:
    return json.dumps(build_mixed_failure_payload(evidence_items), ensure_ascii=False)


def mixed_failure_valid_block_count(evidence_items: list[Any]) -> int:
    """返回 mixed-failure 候选中 citation 有效的 block 数（= 会被 Validator 调用的次数）。

    key_points[0] 引用 ``ev_FAKE_MISSING`` → citation 无效，不计入。
    """
    payload = build_mixed_failure_payload(evidence_items)
    valid_evidence_ids = {str(item.id) for item in evidence_items}
    valid = 0
    for field in ("overview", "key_points", "section_guides", "limitations"):
        for item in payload[field]:
            if all(eid in valid_evidence_ids for eid in item["evidence_ids"]):
                valid += 1
    return valid


def extract_repair_issues(user_text: str) -> list[str]:
    """从 repair prompt user_text 提取「结构化失败报告」段的 issue_type 列表。

    KBR-06 ``build_repair_prompt`` 把失败 issues 渲染为 JSON 数组：
    ```
    结构化失败报告（每项含 block_path/issue_type/...）：
    [
      {"block_path": "overview[1]", "issue_type": "support_partial", ...},
      ...
    ]
    ```
    返回 issue_type 字符串列表（不含 Evidence 正文）。
    """
    marker = "结构化失败报告"
    if marker not in user_text:
        return []
    tail = user_text.split(marker, 1)[1]
    start = tail.find("[")
    if start == -1:
        return []
    # brace/bracket 计数提取首个完整 JSON 数组。
    depth = 0
    end = -1
    for index in range(start, len(tail)):
        ch = tail[index]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end == -1:
        return []
    try:
        items = json.loads(tail[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [str(item.get("issue_type", "")) for item in items if isinstance(item, dict)]
