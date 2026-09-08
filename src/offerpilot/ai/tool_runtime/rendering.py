from __future__ import annotations

from typing import Any

from offerpilot.ai.tool_runtime.contracts import ToolFailure, ToolSpec, ToolSuccess


_FAILURE_TEXT = {
    "validation_error": "工具参数验证失败，请检查后重试。",
    "permission_denied": "权限不足，无法执行该操作。",
    "confirmation_rejected": "操作已取消。",
    "stale_state": "当前状态已变化，请刷新后重试。",
    "conflict": "操作冲突，请刷新后重试。",
    "not_found": "记录不存在。",
    "provider_error": "服务暂时不可用，请稍后重试。",
    "internal_error": "工具执行失败，请稍后重试。",
}


def render_compatibility(
    spec: ToolSpec[Any, Any],
    outcome: ToolSuccess[Any] | ToolFailure,
) -> str:
    if isinstance(outcome, ToolFailure):
        detail = outcome.compatibility_detail or _FAILURE_TEXT[outcome.category]
        return detail if detail.startswith("错误：") else "错误：" + detail
    if spec.success_renderer is None:
        return str(outcome.result)
    return spec.success_renderer(outcome.result)
