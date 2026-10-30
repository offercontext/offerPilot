"""A deliberately one-shot, fail-closed budget boundary for one reviewed run.

No SDK, proxy environment, redirects, fallback, or retry at the upstream boundary.
A journal is never reopened: restart needs a new authorization, not a new budget.
Amounts are integer micro-CNY. No prompt, response, credential or config is logged.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import secrets
import ssl
import stat
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterator

from scripts.companion_ai_validation import continuation

MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"
ENDPOINT = "/chat/completions"
CONTEXT_TOKENS = 1_048_576
OUTPUT_TOKENS = 512
BUDGET = 20_000_000
RESERVATION = 3_000_000
MAX_REQUESTS = 12
MAX_BYTES = 128 * 1024
REQUEST_SECONDS = 90
SESSION_SECONDS = 20 * 60
# Official public sources rechecked 2026-10-24 03:39 UTC, never account /models:
# https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
# https://api-docs.deepseek.com/zh-cn/api/list-models/
# Peak cache-miss input 2 / output 8 CNY per million; context_window 1048576.
# This reviewed quote expires, and is never silently extended on a rerun.
PRICE_VALID_UNTIL = datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp()
OPERATIONS = continuation.OPERATIONS


class GateError(RuntimeError):
    """Safe error codes only: never stringify upstream errors."""


def profile_lineage(*, shadow_review: bool = False, shadow_quote_v2: bool = False) -> ModuleType:
    if type(shadow_review) is not bool or type(shadow_quote_v2) is not bool or (shadow_review and shadow_quote_v2):
        raise GateError("experiment_mode")
    if shadow_quote_v2:
        from scripts.companion_ai_validation import shadow_quote_continuation
        return shadow_quote_continuation
    if shadow_review:
        from scripts.companion_ai_validation import shadow_continuation
        return shadow_continuation
    return continuation


@dataclass(frozen=True)
class RunWindow:
    """One non-extending clock anchor, captured before admission or source I/O."""
    shadow_review: bool = False
    shadow_quote_v2: bool = False
    price_valid_until: float = field(init=False)
    started_monotonic: float = field(init=False)
    started_wall: float = field(init=False)
    session_deadline: float = field(init=False)
    price_deadline: float = field(init=False)
    _last_monotonic: float = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        lineage = profile_lineage(shadow_review=self.shadow_review, shadow_quote_v2=self.shadow_quote_v2)
        quote = lineage.PRICE_VALID_UNTIL if (self.shadow_review or self.shadow_quote_v2) else PRICE_VALID_UNTIL
        object.__setattr__(self, "price_valid_until", quote)
        monotonic, wall = time.monotonic(), time.time()
        if not math.isfinite(monotonic) or not math.isfinite(wall):
            raise GateError("clock_anomaly")
        object.__setattr__(self, "started_monotonic", monotonic)
        object.__setattr__(self, "started_wall", wall)
        object.__setattr__(self, "session_deadline", monotonic + SESSION_SECONDS)
        object.__setattr__(self, "price_deadline", monotonic + (quote - wall))
        object.__setattr__(self, "_last_monotonic", monotonic)

    def effective_wall(self) -> float:
        # A backward wall jump must never rejuvenate admission age or the quote.
        monotonic, wall = time.monotonic(), time.time()
        if (not math.isfinite(monotonic) or not math.isfinite(wall)
                or monotonic < self._last_monotonic):
            raise GateError("clock_anomaly")
        object.__setattr__(self, "_last_monotonic", monotonic)
        return max(wall, self.started_wall + (monotonic - self.started_monotonic))

    def restrict_quote_deadline(self, deadline_ns: int) -> None:
        """A same-job preflight handoff may only shorten this process's quote."""
        if type(deadline_ns) is not int or not 0 <= deadline_ns < 2**63:
            raise GateError("admission_required")
        # Integer-nanosecond conversion can round upward. Move one float step
        # down before narrowing so even conversion cannot extend the quote.
        carried = math.nextafter(deadline_ns / 1e9, -math.inf)
        object.__setattr__(self, "price_deadline", min(self.price_deadline, carried))


def require_linux() -> None:
    """This one-shot acceptance boundary requires Linux durability primitives."""
    if (sys.platform != "linux" or os.name != "posix"
            or not all(hasattr(os, name) for name in ("pread", "O_DIRECTORY", "fsync"))):
        raise GateError("unsupported_platform")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GateError("duplicate_json_key")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise GateError("nonfinite_json")


def decode(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_BYTES:
        raise GateError("body_limit")
    try:
        result = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise GateError("invalid_json") from exc
    if not isinstance(result, dict):
        raise GateError("object_required")
    return result


def encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _integer(value: Any, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise GateError("untrusted_usage")
    return value


def usage_cost(response: dict[str, Any], *, shadow_review: bool = False, shadow_quote_v2: bool = False) -> tuple[int, int, int]:
    profile_lineage(shadow_review=shadow_review, shadow_quote_v2=shadow_quote_v2)
    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise GateError("missing_usage")
    if set(usage) - {
        "prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens", "completion_tokens_details", "prompt_tokens_details",
    }:
        raise GateError("untrusted_usage")
    prompt = _integer(usage.get("prompt_tokens"), CONTEXT_TOKENS)
    completion = _integer(usage.get("completion_tokens"), 768 if (shadow_review or shadow_quote_v2) else OUTPUT_TOKENS)
    total = _integer(usage.get("total_tokens"), CONTEXT_TOKENS)
    hit = _integer(usage.get("prompt_cache_hit_tokens"), CONTEXT_TOKENS)
    miss = _integer(usage.get("prompt_cache_miss_tokens"), CONTEXT_TOKENS)
    if prompt + completion != total or hit + miss != prompt:
        raise GateError("conflicting_usage")
    details = usage.get("completion_tokens_details")
    if details is not None:
        if not isinstance(details, dict):
            raise GateError("untrusted_usage")
        if set(details) - {"reasoning_tokens"}:
            raise GateError("untrusted_usage")
        if "reasoning_tokens" in details:
            _integer(details["reasoning_tokens"], completion)
    prompt_details = usage.get("prompt_tokens_details")
    if prompt_details is not None:
        if not isinstance(prompt_details, dict):
            raise GateError("untrusted_usage")
        if set(prompt_details) - {"cached_tokens"}:
            raise GateError("untrusted_usage")
        if "cached_tokens" in prompt_details:
            if _integer(prompt_details["cached_tokens"], prompt) != hit:
                raise GateError("conflicting_usage")
    cost = prompt * 2 + completion * 8
    if cost > RESERVATION:
        raise GateError("reservation_exceeded")
    return cost, prompt, completion


def response_content(response: dict[str, Any]) -> str:
    if response.get("model") != MODEL:
        # No guessed concrete-version slug: an undocumented alias blocks this run.
        raise GateError("unexpected_response_model")
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise GateError("invalid_response")
    choice = choices[0]
    if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
        raise GateError("incomplete_response")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise GateError("invalid_response")
    if message.get("tool_calls") or message.get("function_call"):
        raise GateError("unexpected_tool")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise GateError("empty_response")
    return content


def local_sse(response: dict[str, Any]) -> bytes:
    """Expose no partial text until full upstream/usage/tool checks have passed."""
    content = response_content(response)
    base = {"id": "chatcmpl-companion-validation", "object": "chat.completion.chunk",
            "created": 0, "model": MODEL}
    first = {**base, "choices": [{"index": 0, "delta": {
        "role": "assistant", "content": content}, "finish_reason": None}]}
    last = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": response["usage"]}
    return b"data: " + encode(first) + b"\n\ndata: " + encode(last) + b"\n\ndata: [DONE]\n\n"


class HTTPSUpstream:
    """One direct verified TLS connection per attempt; never an SDK/proxy opener."""
    def __init__(self, key: str, *, window: RunWindow | None = None):
        require_linux()
        window = window if window is not None else RunWindow()
        if not key or any(c.isspace() for c in key):
            raise GateError("credential_missing")
        self._key = key
        self._deadline = window.session_deadline
        self._price_deadline = window.price_deadline
        self._price_valid_until = window.price_valid_until

    def _check_window(self, request_deadline: float) -> None:
        if (time.time() + REQUEST_SECONDS > self._price_valid_until
                or time.monotonic() + REQUEST_SECONDS > self._price_deadline):
            raise GateError("price_review_expired")
        if time.monotonic() + REQUEST_SECONDS > self._deadline:
            raise GateError("session_deadline")
        if time.monotonic() >= request_deadline:
            raise GateError("upstream_timeout")

    def __call__(self, body: bytes) -> tuple[int, bytes]:
        if any(name in os.environ for name in ("SSLKEYLOGFILE", "SSL_CERT_FILE", "SSL_CERT_DIR")):
            raise GateError("unsafe_tls_environment")
        deadline = time.monotonic() + REQUEST_SECONDS
        self._check_window(deadline)
        connection = http.client.HTTPSConnection(
            "api.deepseek.com", 443, timeout=REQUEST_SECONDS, context=ssl.create_default_context(),
        )
        try:
            self._check_window(deadline)
            connection.timeout = deadline - time.monotonic()
            # Resolve DNS and complete TLS first. No HTTP/body/key has been sent.
            connection.connect()
            self._check_window(deadline)
            if connection.sock is None:
                raise GateError("upstream_connection")
            connection.sock.settimeout(deadline - time.monotonic())
            connection.request("POST", ENDPOINT, body=body, headers={
                "Authorization": "Bearer " + self._key,
                "Content-Type": "application/json", "Accept": "application/json",
            })
            sock = connection.sock
            if sock is None or deadline <= time.monotonic():
                raise GateError("upstream_timeout")
            sock.settimeout(deadline - time.monotonic())
            response = connection.getresponse()
            # http.client does not follow redirects or retry requests.
            chunks: list[bytes] = []
            size = 0
            while size <= MAX_BYTES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GateError("upstream_timeout")
                sock.settimeout(remaining)
                chunk = response.read1(min(8192, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            return response.status, b"".join(chunks)
        finally:
            connection.close()


class Broker:
    def __init__(self, journal_path: Path, send: Callable[[bytes], tuple[int, bytes]],
                 source_sha: str, *, window: RunWindow | None = None, shadow_review: bool = False,
                 shadow_quote_v2: bool = False):
        require_linux()
        window = window if window is not None else RunWindow(shadow_review=shadow_review, shadow_quote_v2=shadow_quote_v2)
        if len(source_sha) != 40 or any(c not in "0123456789abcdef" for c in source_sha):
            raise GateError("source_sha")
        self._shadow_review = shadow_review
        self._shadow_quote_v2 = shadow_quote_v2
        lineage_module = profile_lineage(shadow_review=shadow_review, shadow_quote_v2=shadow_quote_v2)
        self._operations = lineage_module.OPERATIONS
        self._max_requests = 6 if (shadow_review or shadow_quote_v2) else MAX_REQUESTS
        # There is no caller-, CLI- or environment-supplied opening balance. The
        # only continuation inherits the exact fully replayed original ledger.
        try:
            lineage = lineage_module.lineage_fields()
        except continuation.ContinuationError as exc:
            raise GateError("predecessor_evidence_invalid") from exc
        if (lineage["budget_micro_cny"] != BUDGET
                or lineage["price_valid_until"] != window.price_valid_until):
            raise GateError("continuation_contract_mismatch")
        self._lock = threading.Lock()
        self._deadline = window.session_deadline
        self._price_deadline = window.price_deadline
        self._price_valid_until = window.price_valid_until
        self._halt = threading.Event()
        self._send = send
        self._token = ""
        self._operation = ""
        self._calls = 0
        self._next = 0
        self._state: dict[str, Any] = {
            **lineage, "schema": continuation.ADMISSION_SCHEMA,
            "source_sha": source_sha, "request_count": 0,
            "settled_micro_cny": lineage["prior_settled_micro_cny"],
            "reserved_micro_cny": lineage["prior_reserved_micro_cny"], "halted": False,
        }
        # Never truncate, reopen, resume or silently reset a previous budget.
        try:
            self._fd = os.open(journal_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            self._journal_path = journal_path
            self._journal_bytes = b""
            directory = os.open(journal_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self._record("created")
        except OSError as exc:
            self._halt.set()
            raise GateError("journal_unavailable") from exc

    def _accounting(self) -> dict[str, Any]:
        """Unambiguous cumulative and batch evidence at every durable transition."""
        settled = self._state["settled_micro_cny"]
        reserved = self._state["reserved_micro_cny"]
        return {
            **self._state,
            "batch_request_count": self._state["request_count"],
            "lifetime_request_count": (self._state["prior_request_count"]
                                       + self._state["request_count"]),
            "batch_settled_micro_cny": settled - self._state["prior_settled_micro_cny"],
            "batch_reserved_micro_cny": reserved - self._state["prior_reserved_micro_cny"],
            "lifetime_settled_micro_cny": settled,
            "lifetime_reserved_micro_cny": reserved,
            "lifetime_liability_micro_cny": settled + reserved,
            "lifetime_remaining_micro_cny": BUDGET - settled - reserved,
        }

    def _record(self, event: str, **values: Any) -> None:
        try:
            current = os.fstat(self._fd)
            linked = self._journal_path.lstat()
            if (not stat.S_ISREG(linked.st_mode) or current.st_ino != linked.st_ino
                    or current.st_dev != linked.st_dev or current.st_nlink != 1
                    or current.st_size != len(self._journal_bytes)
                    or os.pread(self._fd, current.st_size, 0) != self._journal_bytes):
                raise OSError("journal changed")
            line = encode({**self._accounting(), "event": event, **values}) + b"\n"
            view = memoryview(line)
            while view:
                count = os.write(self._fd, view)
                if count <= 0:
                    raise OSError("short write")
                view = view[count:]
            os.fsync(self._fd)
            self._journal_bytes += line
        except (OSError, ValueError) as exc:
            self._halt.set()
            raise GateError("journal_failure") from exc

    def begin_stage(self, operation: str) -> str:
        with self._lock:
            if self._halt.is_set() or self._operation or self._next >= len(self._operations):
                raise GateError("gate_closed")
            if operation != self._operations[self._next]:
                raise GateError("operation_order")
            self._operation = operation
            self._calls = 0
            self._token = secrets.token_urlsafe(32)
            return self._token

    def close_stage(self) -> None:
        with self._lock:
            self._token = ""
            if self._halt.is_set() or not self._operation or not self._calls:
                self._halt.set()
                raise GateError("incomplete_stage")
            self._record("stage_complete", operation=self._operation, calls=self._calls)
            self._operation = ""
            self._next += 1

    def _validate(self, raw: bytes) -> dict[str, Any]:
        if self._calls:
            # No SDK retry, structural repair, follow-up or second completion.
            raise GateError("ticket_consumed")
        if self._shadow_review or self._shadow_quote_v2:
            if self._shadow_quote_v2:
                from scripts.companion_ai_validation.shadow_quote_driver import frozen_request
            else:
                from scripts.companion_ai_validation.shadow_driver import frozen_request
            if raw != frozen_request(self._operation):
                raise GateError("shadow_request_mismatch")
            return decode(raw)
        payload = decode(raw)
        if set(payload) - {"model", "messages", "max_tokens", "stream", "stream_options", "tools", "tool_choice"}:
            raise GateError("unexpected_request_field")
        if payload.get("model") != MODEL or type(payload.get("max_tokens")) is not int:
            raise GateError("model_or_output_limit")
        if payload["max_tokens"] != OUTPUT_TOKENS or type(payload.get("stream", False)) is not bool:
            raise GateError("model_or_output_limit")
        if "stream_options" in payload:
            if (payload.get("stream") is not True
                    or payload["stream_options"] != {"include_usage": True}):
                raise GateError("stream_options")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not 2 <= len(messages) <= 64:
            raise GateError("message_shape")
        for message in messages:
            if not isinstance(message, dict) or set(message) != {"role", "content"}:
                raise GateError("message_shape")
            if message["role"] not in {"system", "user", "assistant"}:
                raise GateError("message_shape")
            if not isinstance(message["content"], str) or not message["content"].strip():
                raise GateError("message_shape")
        if messages[0]["role"] != "system" or messages[-1]["role"] != "user":
            raise GateError("message_shape")
        tools = payload.get("tools")
        if tools is not None:
            if not isinstance(tools, list) or not 1 <= len(tools) <= 64:
                raise GateError("tool_shape")
            names: set[str] = set()
            for tool in tools:
                if not isinstance(tool, dict) or set(tool) != {"type", "function"}:
                    raise GateError("tool_shape")
                function = tool["function"]
                if tool["type"] != "function" or not isinstance(function, dict):
                    raise GateError("tool_shape")
                if set(function) - {"name", "description", "parameters", "strict"}:
                    raise GateError("tool_shape")
                name = function.get("name")
                if (not isinstance(name, str) or not name or len(name) > 128
                        or name in names or not isinstance(function.get("parameters"), dict)
                        or not isinstance(function.get("description", ""), str)
                        or type(function.get("strict", False)) is not bool):
                    raise GateError("tool_shape")
                names.add(name)
            if payload.get("tool_choice") not in {"auto", "none"}:
                raise GateError("tool_shape")
        elif "tool_choice" in payload:
            raise GateError("tool_shape")
        # Keep the real product prompt/history/tools. Only the reviewed provider
        # execution mode is fixed; any actual tool reply is rejected below.
        payload["thinking"] = {"type": "disabled"}
        return payload

    def _check_window(self) -> None:
        if (time.time() + REQUEST_SECONDS > self._price_valid_until
                or time.monotonic() + REQUEST_SECONDS > self._price_deadline):
            raise GateError("price_review_expired")
        if time.monotonic() + REQUEST_SECONDS > self._deadline:
            raise GateError("session_deadline")

    def request(self, token: str, raw: bytes) -> bytes:
        if not self._lock.acquire(blocking=False):
            self._halt.set()
            raise GateError("concurrent_request")
        reserved = False
        try:
            if self._halt.is_set() or not self._token or not secrets.compare_digest(token, self._token):
                raise GateError("gate_closed")
            payload = self._validate(raw)
            local_stream = payload.get("stream", False)
            if self._shadow_review or self._shadow_quote_v2:
                upstream_raw = raw
            else:
                payload["stream"] = False
                payload.pop("stream_options", None)
                upstream_raw = encode(payload)
            self._check_window()
            if self._state["request_count"] >= self._max_requests:
                raise GateError("request_limit")
            liability = self._state["settled_micro_cny"] + self._state["reserved_micro_cny"]
            if liability + RESERVATION > BUDGET:
                raise GateError("budget_exhausted")
            self._state["request_count"] += 1
            self._state["reserved_micro_cny"] += RESERVATION
            self._calls += 1
            self._record("reserved", operation=self._operation,
                         body_sha256=hashlib.sha256(upstream_raw).hexdigest())
            reserved = True
            # Persistence can stall. Revalidate after fsync, before any network.
            self._check_window()
            status, response_raw = self._send(upstream_raw)
            if status != 200:
                raise GateError("upstream_status")
            response = decode(response_raw)
            cost, prompt, completion = usage_cost(response, shadow_review=self._shadow_review, shadow_quote_v2=self._shadow_quote_v2)
            response_content(response)
            pending_state = dict(self._state)
            self._state["settled_micro_cny"] += cost
            self._state["reserved_micro_cny"] -= RESERVATION
            self._state["halted"] = self._halt.is_set()
            try:
                self._record("settled", operation=self._operation, cost_micro_cny=cost,
                             prompt_tokens=prompt, completion_tokens=completion)
            except GateError:
                # An uncertain journal commit cannot release the reservation.
                self._state = pending_state
                raise
            if self._halt.is_set():
                raise GateError("gate_closed")
            return local_sse(response) if local_stream else response_raw
        except BaseException as exc:
            self._halt.set()
            self._state["halted"] = True
            # Persist unknown liability; a write failure must never permit a send.
            try:
                self._record("halted", operation=self._operation, after_reservation=reserved)
            except GateError:
                pass
            if isinstance(exc, GateError):
                raise
            raise GateError("upstream_unknown") from None
        finally:
            self._lock.release()

    def reject(self) -> None:
        self._halt.set()

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {**self._accounting(), "halted": self._halt.is_set(),
                    "stages_complete": self._next,
                    "accounting_basis": "conservative_usage_not_provider_invoice"}

    def close(self) -> None:
        self._halt.set()
        with self._lock:
            self._state["halted"] = True
            try:
                self._record("closed")
            finally:
                os.close(self._fd)


@contextmanager
def serve(broker: Broker) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_: Any) -> None:
            pass

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(5)

        def do_POST(self) -> None:
            self.close_connection = True
            try:
                lengths = self.headers.get_all("Content-Length", [])
                auth = self.headers.get_all("Authorization", [])
                if self.path != "/v1/chat/completions" or len(lengths) != 1 or len(auth) != 1:
                    raise GateError("request_target")
                if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Encoding"):
                    raise GateError("request_encoding")
                if self.headers.get_content_type() != "application/json":
                    raise GateError("request_type")
                length = int(lengths[0])
                if not 0 < length <= MAX_BYTES or not auth[0].startswith("Bearer "):
                    raise GateError("request_limit")
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise GateError("incomplete_request")
                output = broker.request(auth[0][7:], raw)
                status = 200
            except BaseException:
                broker.reject()
                output, status = b'{"error":{"message":"validation gate closed"}}', 400
            try:
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream"
                                 if output.startswith(b"data: ") else "application/json")
                self.send_header("Content-Length", str(len(output)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(output)
            except OSError:
                broker.reject()

        def do_GET(self) -> None:
            broker.reject()
            self.send_error(405)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
