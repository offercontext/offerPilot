from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    args: str


@dataclass
class Message:
    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""
    provider_blocks: dict[str, Any] = field(default_factory=dict)
    # Transient projector routing metadata. Adapters and persistence omit it.
    surface_contributor: str = ""
    surface_signal: str = ""
    surface_revision: str = ""
    surface_page_kind: str = ""
    surface_attachment_kinds: str = ""


@dataclass
class Assistant:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider_blocks: dict[str, Any] = field(default_factory=dict)
