"""OpenAI-compatible local provider; scripted answers consume received messages."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from typing import Any
from uuid import uuid4


def start_provider(channel: Any, model: Any) -> tuple[Any, str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            from offerpilot.ai.types import Message, ToolCall

            size = int(self.headers.get("Content-Length", "0"))
            if self.path != "/v1/chat/completions" or size > 128000:
                self.send_error(400)
                return
            payload = json.loads(self.rfile.read(size))
            messages = [
                Message(
                    role=m["role"],
                    content=m.get("content") or "",
                    tool_call_id=m.get("tool_call_id"),
                    tool_calls=[
                        ToolCall(c["id"], c["function"]["name"], c["function"]["arguments"])
                        for c in m.get("tool_calls", [])
                    ],
                    provider_blocks={"reasoning_content": m["reasoning_content"]}
                    if "reasoning_content" in m
                    else {},
                )
                for m in payload["messages"]
            ]
            assistant = model.complete(messages, payload.get("tools", []))
            message = {
                "role": "assistant",
                "content": assistant.content,
                "reasoning_content": "synthetic-provider-block",
            }
            if assistant.tool_calls:
                message["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": c.args},
                    }
                    for c in assistant.tool_calls
                ]
            result: dict[str, Any] = {
                "id": "loopback-" + str(uuid4()),
                "object": "chat.completion",
                "created": 1,
                "model": "eval-core",
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if assistant.tool_calls else "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": size,
                    "completion_tokens": len(assistant.content),
                    "total_tokens": size + len(assistant.content),
                },
            }
            self.send_response(200)
            if payload.get("stream"):
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                chunk: dict[str, Any] = {
                    "id": result["id"],
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "eval-core",
                    "choices": [{"index": 0, "delta": message, "finish_reason": None}],
                }
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                chunk["choices"][0] = {
                    "index": 0,
                    "delta": {},
                    "finish_reason": result["choices"][0]["finish_reason"],
                }
                chunk["usage"] = result["usage"]
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
                self.wfile.flush()
            else:
                body = json.dumps(result).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/v1"
