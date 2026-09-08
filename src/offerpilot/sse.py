from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


STREAM_VERSION = "pilot-sse-v1"


@dataclass
class SseRun:
    run_id: str
    conversation_id: int
    context_type: str
    context_ref: str
    mode: str
    seq: int = 0

    def envelope(self, event: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        self.seq += 1
        return {
            "run_id": self.run_id,
            "seq": self.seq,
            "conversation_id": self.conversation_id,
            "event": event,
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "context_type": self.context_type,
            "context_ref": self.context_ref,
            "mode": self.mode,
            "data": data or {},
        }


def format_sse(event: str, event_id: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\nid: {event_id}\ndata: {payload}\n\n"


def sse_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
