"""A deliberately one-shot, fail-closed budget boundary for one reviewed run.

No SDK, proxy environment, redirects, fallback, or retry at the upstream boundary.
A journal is never reopened: restart needs a new authorization, not a new budget.
Amounts are integer micro-CNY. No prompt, response, credential or config is logged.
"""
from __future__ import annotations

import copy
import hashlib
import http.client
import json
import math
import os
import re
import secrets
import ssl
import stat
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator

MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"
ENDPOINT = "/chat/completions"
CONTEXT_TOKENS = 1_048_576
OUTPUT_TOKENS = 4096
BUDGET = 10_000_000
RESERVATION = 3_000_000
MAX_REQUESTS = 18
MAX_BYTES = 128 * 1024
REQUEST_SECONDS = 90
SESSION_SECONDS = 20 * 60
# Official public sources rechecked 2026-10-22 00:01 UTC, never account /models:
# https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
# https://api-docs.deepseek.com/zh-cn/api/list-models/
# deepseek-flash maps to DeepSeek-V4.1-Flash-1017; context_window 1048576.
# Peak cache-miss input 2 / output 8 CNY per million.
# Conservative 4096-output upper bound: 2.129920 CNY; reservation stays 3 CNY.
# This reviewed quote expires, and is never silently extended on a rerun.
PRICE_VALID_UNTIL = datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp()
OPERATIONS = (
    "cli_analyze", "http_analyze", "cli_match", "http_match",
    "cli_questions", "http_questions", "quick_start", "quick_next",
    "quick_feedback", "event_start", "event_next", "event_feedback",
)


class GateError(RuntimeError):
    """Safe error codes only: never stringify upstream errors."""


def require_linux() -> None:
    """This helper requires real Linux journal durability; never emulate it."""
    if sys.platform != "linux" or os.name != "posix":
        raise GateError("linux_only_helper")


def clock_number(value: Any) -> float:
    if type(value) not in (int, float):
        raise GateError("invalid_clock")
    try:
        number = float(value)
    except OverflowError:
        raise GateError("invalid_clock") from None
    if not math.isfinite(number) or number < 0:
        raise GateError("invalid_clock")
    return number


def boot_identity() -> str:
    """Linux monotonic timestamps may only cross processes on this same boot."""
    require_linux()
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        raise GateError("clock_identity_unavailable") from None
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value):
        raise GateError("clock_identity_unavailable")
    return value


class PriceWindow:
    """One fixed quote anchor, carried through the same Linux paid job."""

    def __init__(self, wall: float, monotonic: float):
        self.wall = clock_number(wall)
        self.monotonic = clock_number(monotonic)
        self.deadline = self.monotonic + (PRICE_VALID_UNTIL - self.wall)
        if not math.isfinite(self.deadline):
            raise GateError("invalid_clock")
        self._last_monotonic = self.monotonic

    @classmethod
    def capture(cls) -> PriceWindow:
        require_linux()
        # A pause between reads only shortens this anchor's usable window.
        monotonic = time.monotonic()
        return cls(time.time(), monotonic)

    def record(self) -> dict[str, float]:
        return {"wall": self.wall, "monotonic": self.monotonic, "deadline": self.deadline}

    @classmethod
    def restore(cls, record: Any) -> PriceWindow:
        if not isinstance(record, dict) or set(record) != {"wall", "monotonic", "deadline"}:
            raise GateError("invalid_clock")
        window = cls(record["wall"], record["monotonic"])
        if clock_number(record["deadline"]) != window.deadline:
            raise GateError("invalid_clock")
        return window

    def tighten(self, entry: PriceWindow) -> None:
        if self.monotonic > entry.monotonic:
            raise GateError("invalid_clock")
        self.deadline = min(self.deadline, entry.deadline)
        self._last_monotonic = max(self._last_monotonic, entry._last_monotonic)

    def check(self, seconds: int) -> tuple[float, float]:
        before = clock_number(time.monotonic())
        wall = clock_number(time.time())
        monotonic = clock_number(time.monotonic())
        if before < self._last_monotonic or monotonic < before:
            raise GateError("invalid_clock")
        self._last_monotonic = monotonic
        if (wall + seconds > PRICE_VALID_UNTIL
                or monotonic + seconds > self.deadline):
            raise GateError("price_review_expired")
        return wall, monotonic


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


def usage_cost(response: dict[str, Any]) -> tuple[int, int, int]:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise GateError("missing_usage")
    if set(usage) - {
        "prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens", "completion_tokens_details", "prompt_tokens_details",
    }:
        raise GateError("untrusted_usage")
    prompt = _integer(usage.get("prompt_tokens"), CONTEXT_TOKENS)
    completion = _integer(usage.get("completion_tokens"), OUTPUT_TOKENS)
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


def _format_failure(operation: str, payload: dict[str, Any], content: str) -> str:
    """Authorize only the actual product's one structural repair, not SDK retry."""
    from offerpilot.ai.mock_interview import (
        MockInterviewContractError, _expand_evidence_ids, _normalize_question_text,
        parse_mock_interview_json, should_retry_mock_interview_format, validate_feedback,
    )
    data = decode(payload["messages"][1]["content"].encode())
    try:
        parsed = parse_mock_interview_json(content)
        if operation.endswith("feedback"):
            validate_feedback(parsed, data["evidence_catalog"])
        else:
            if set(parsed) != {"question", "evidence_ids"}:
                raise MockInterviewContractError("unexpected_field")
            question = parsed["question"]
            if not isinstance(question, str) or not question.strip() or len(question) > 1000:
                raise MockInterviewContractError("blank_value")
            _expand_evidence_ids(parsed["evidence_ids"], data["evidence_catalog"])
            answered = [t for t in data["turns"] if isinstance(t.get("answer"), str)
                        and t["answer"].strip()]
            if any(_normalize_question_text(question) ==
                   _normalize_question_text(str(t.get("question", ""))) for t in answered):
                return ""
            if answered:
                latest = max(int(t["turn_no"]) for t in answered)
                latest_ids = {e["id"] for e in data["evidence_catalog"]
                              if e["source"] == "turn" and
                              e["path"] == f"/turns/{latest:03d}/answer"}
                if not latest_ids.intersection(parsed["evidence_ids"]):
                    return ""
    except MockInterviewContractError as exc:
        return exc.category if should_retry_mock_interview_format(exc.category) else ""
    return ""


def _expected_repair(operation: str, original: dict[str, Any], category: str) -> dict[str, Any]:
    from offerpilot.ai.mock_interview import _format_repair_instruction
    expected = copy.deepcopy(original)
    data = decode(expected["messages"][1]["content"].encode())
    data["repair_failure_category"] = category
    if operation.endswith("feedback"):
        data["repair_instruction"] = _format_repair_instruction(category)
    else:
        expected["messages"][0]["content"] += (
            " Format repair: the previous response failed the structural check "
            f"{category!r}. Return only one raw JSON object with exactly "
            "question and evidence_ids. evidence_ids must be a non-empty array "
            "containing one to four IDs copied exactly from evidence_catalog."
        )
    expected["messages"][1]["content"] = encode(data).decode()
    return expected


class HTTPSUpstream:
    """One direct verified TLS connection per attempt; never an SDK/proxy opener."""
    def __init__(self, key: str, price_window: PriceWindow | None = None):
        require_linux()
        if not key or any(c.isspace() for c in key):
            raise GateError("credential_missing")
        self._key = key
        self._deadline = time.monotonic() + SESSION_SECONDS
        self._price_window = price_window if price_window is not None else PriceWindow.capture()
        self._price_deadline = self._price_window.deadline

    def _check_window(self, request_deadline: float) -> None:
        self._price_window.check(REQUEST_SECONDS)
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
                 source_sha: str, price_window: PriceWindow | None = None):
        require_linux()
        if len(source_sha) != 40 or any(c not in "0123456789abcdef" for c in source_sha):
            raise GateError("source_sha")
        self._lock = threading.Lock()
        self._deadline = time.monotonic() + SESSION_SECONDS
        self._price_window = price_window if price_window is not None else PriceWindow.capture()
        self._price_deadline = self._price_window.deadline
        self._halt = threading.Event()
        self._send = send
        self._token = ""
        self._operation = ""
        self._calls = 0
        self._initial: dict[str, Any] | None = None
        self._repair = ""
        self._next = 0
        self._state: dict[str, Any] = {
            "schema": 1, "source_sha": source_sha, "request_count": 0,
            "settled_micro_cny": 0, "reserved_micro_cny": 0,
            "budget_micro_cny": BUDGET, "halted": False,
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

    def _record(self, event: str, **values: Any) -> None:
        try:
            current = os.fstat(self._fd)
            linked = self._journal_path.lstat()
            if (not stat.S_ISREG(linked.st_mode) or current.st_ino != linked.st_ino
                    or current.st_dev != linked.st_dev or current.st_nlink != 1
                    or current.st_size != len(self._journal_bytes)
                    or os.pread(self._fd, current.st_size, 0) != self._journal_bytes):
                raise OSError("journal changed")
            line = encode({**self._state, "event": event, **values}) + b"\n"
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
            if self._halt.is_set() or self._operation or self._next >= len(OPERATIONS):
                raise GateError("gate_closed")
            if operation != OPERATIONS[self._next]:
                raise GateError("operation_order")
            self._operation = operation
            self._calls, self._initial, self._repair = 0, None, ""
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
        payload = decode(raw)
        if set(payload) - {"model", "messages", "max_tokens", "stream"}:
            raise GateError("unexpected_request_field")
        if payload.get("model") != MODEL or type(payload.get("max_tokens")) is not int:
            raise GateError("model_or_output_limit")
        if payload["max_tokens"] != OUTPUT_TOKENS or payload.get("stream", False) is not False:
            raise GateError("model_or_output_limit")
        messages = payload.get("messages")
        if not isinstance(messages, list) or len(messages) != 2:
            raise GateError("message_shape")
        for message, role in zip(messages, ("system", "user")):
            if not isinstance(message, dict) or set(message) != {"role", "content"}:
                raise GateError("message_shape")
            if message["role"] != role or not isinstance(message["content"], str):
                raise GateError("message_shape")
            if not message["content"].strip():
                raise GateError("message_shape")
        if self._calls:
            if not self._repair or self._calls != 1 or self._initial is None:
                raise GateError("ticket_consumed")
            if payload != _expected_repair(self._operation, self._initial, self._repair):
                raise GateError("not_structural_repair")
        return payload

    def _check_window(self) -> None:
        self._price_window.check(REQUEST_SECONDS)
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
            self._check_window()
            if self._state["request_count"] >= MAX_REQUESTS:
                raise GateError("request_limit")
            liability = self._state["settled_micro_cny"] + self._state["reserved_micro_cny"]
            if liability + RESERVATION > BUDGET:
                raise GateError("budget_exhausted")
            self._state["request_count"] += 1
            self._state["reserved_micro_cny"] += RESERVATION
            self._calls += 1
            self._record("reserved", operation=self._operation,
                         body_sha256=hashlib.sha256(raw).hexdigest())
            reserved = True
            # Persistence can stall. Revalidate after fsync, before any network.
            self._check_window()
            status, response_raw = self._send(raw)
            if status != 200:
                raise GateError("upstream_status")
            response = decode(response_raw)
            cost, prompt, completion = usage_cost(response)
            content = response_content(response)
            # Compute any repair permission before releasing the reservation.
            repair = ""
            if self._operation.startswith(("quick_", "event_")) and self._calls == 1:
                repair = _format_failure(self._operation, payload, content)
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
            self._initial, self._repair = payload, repair
            if self._halt.is_set():
                raise GateError("gate_closed")
            return response_raw
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
            return {**self._state, "halted": self._halt.is_set(), "stages_complete": self._next}

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
                self.send_header("Content-Type", "application/json")
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
