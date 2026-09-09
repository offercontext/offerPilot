"""Deterministic receipts for approved, meaningfully edited writes.

The receipt is deliberately built from the immutable terminal result stored by
the write Ledger.  It must never use the request's edited arguments or reload
the current business row: either value can describe something other than the
operation that actually committed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence


EDITED_CONFIRMATION_RECEIPT_STRATEGY = "edited_confirmation_receipt_v1"

_FIELD_LABELS = {
    "company_name": "公司",
    "position_name": "职位",
    "status": "状态",
    "base_monthly": "月薪",
    "months_per_year": "薪资月数",
    "signing_bonus": "签字费",
    "equity": "股权",
    "perks": "福利",
    "deadline": "截止时间",
    "notes": "备注",
    "assessment": "评估",
    "event_type": "事件类型",
    "subtype": "子类型",
    "scheduled_at": "时间",
    "remind_at": "提醒时间",
    "duration_minutes": "时长",
    "round": "轮次",
    "location": "地点",
    "allow_placeholder_date": "允许日期占位",
    "questions": "问题",
    "self_reflection": "自我反思",
    "difficulty_points": "难点",
    "mood": "情绪",
    "company": "公司",
    "position": "岗位",
    "date": "日期",
    "job_url": "岗位链接",
    "closed_reason": "结束原因",
    "jd_text": "JD 原文",
    "source_url": "来源链接",
    "text": "改写正文",
    "submitted_at": "投递时间",
    "note": "投递备注",
    "stage": "阶段",
    "result": "结果",
    "feedback_text": "原始反馈",
    "reflection_text": "我的复盘",
    "next_action_text": "下次行动",
    "occurred_at": "发生时间",
}


def _display_value(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "空"
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return "不可显示"


def _terminal_result_details(
    result_json: str | None,
    changed_fields: Sequence[str],
) -> str | None:
    if type(result_json) is not str:
        return None
    try:
        result = json.loads(result_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(result, Mapping):
        return None
    details: list[str] = []
    for field in changed_fields:
        # A strategy field is part of the contract only when the terminal
        # result proves its value.  Rendering the fields that happen to be
        # present would turn a partial payload into a misleading receipt.
        if type(field) is not str or not field or field not in result:
            return None
        label = _FIELD_LABELS.get(field, field)
        details.append(f"{label}={_display_value(result[field])}")
    return "；".join(details) if details else None


def edited_confirmation_receipt(
    *,
    result_json: str | None,
    changed_fields: Sequence[str],
) -> str:
    """Render one stable assistant receipt from a trusted terminal payload."""

    details = _terminal_result_details(result_json, changed_fields)
    if details:
        return (
            f"已按用户最终确认值保存。保存结果：{details}。"
            "本次确认后的自动续答已结束。"
        )
    return "已按用户最终确认值保存，部分明细暂不可用。本次确认后的自动续答已结束。"


__all__ = ["EDITED_CONFIRMATION_RECEIPT_STRATEGY", "edited_confirmation_receipt"]
