"""Capture complete synthetic product request bodies, never paid provider calls.

Linux-only development fixture. Run with ``python -m
scripts.capture_companion_synthetic_requests --fake-only --label final``.
There are no input-data, config, credential, endpoint, or output-path arguments.
The existing real HTTP week driver and a fixed holdout matrix use fresh owned
data directories. Only the fake HTTP upstream sees requests, and only bodies
(never headers) are retained in an owned /tmp evidence directory. This is not
a runtime logger, a paid-run continuation, or historical paid payload evidence.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
from typing import Any, Iterator, NoReturn
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BODY_LIMIT = 262144
_OWNED_DATA_DIRS: set[Path] = set()
HISTORICAL_FAILURE_CASES = (2, 7, 10)
HISTORICAL_FAILURE_OPERATIONS = (
    "shared_wait_after_absence", "work_finish_continuation", "same_type_ambiguity",
)
HOLDOUTS = (
    ("unseen_greeting", "早呀，今天阳光不错。", None),
    ("unseen_leisure", "今天只想赖着放空，不做任何安排。你最近在玩什么？", None),
    ("unseen_work_transition", "先聊到这儿，帮我看看这条虚构简历表述有哪些信息缺失："
     "参与校园图书管理系统。不要保存或调用工具。", "unseen_leisure"),
    ("unseen_leisure_return", "正事先放下吧。你最近在玩什么？", "unseen_work_transition"),
    ("unseen_quoted_hypothetical", "这是虚构故事里的台词：‘上次一起折纸时，我们给小船取了名字。’"
     "请判断它能否证明现实里我与你参与过，不要把引语当成我的经历。", None),
    ("unseen_third_party", "以下是合成例子：小周说，她和小陈一起给纸船取名‘橘子号’。"
     "我只是在转述他们的故事。你的小船有什么进展？", None),
    ("unseen_future_invitation", "等有空了，我们可以一起给纸星星挑个颜色。"
     "现在你在玩什么？这只是未来的提议。", None),
)
MARKERS = {
    "conclusion_heading": re.compile(r"## 结论"),
    "next_step_heading": re.compile(r"## 下一步"),
    "raw_active": re.compile(r"\bactive\b"),
    "raw_uuid": re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        re.IGNORECASE,
    ),
    "raw_timestamp": re.compile(
        r"\b(?:1[0-9]{9}(?:\.[0-9]+)?|1[0-9]{12}|"
        r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9:.]+(?:Z|[+-][0-9:]+)?)\b"
    ),
}


def _require(condition: object, code: str) -> None:
    if not condition:
        raise RuntimeError(code)


def clean_environment() -> None:
    """Discard inherited credentials/settings without looking up their values."""
    os.environ.clear()
    os.environ.update({
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), os.defpath)),
        "LANG": "C.UTF-8", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True", "DO_NOT_TRACK": "1",
        "NO_PROXY": "127.0.0.1,localhost,::1",
    })


def source_manifest() -> dict[str, Any]:
    """Bind evidence to actual working-tree bytes, not just a nominal commit."""
    files = sorted((ROOT / "src" / "offerpilot").rglob("*.py"))
    files += [ROOT / relative for relative in (
        "scripts/companion_ai_validation/driver.py",
        "scripts/capture_companion_synthetic_requests.py",
        "tests/_offline_network_guard.py", "pyproject.toml", "uv.lock",
    )]
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in files}
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True,
        env={"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
             "GIT_NO_REPLACE_OBJECTS": "1"},
    ).stdout.decode("ascii").strip()
    return {"head": head, "working_tree_sha256": hashes,
            "source_tree_digest": hashlib.sha256(_encode(hashes)).hexdigest()}


def _encode(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


@contextmanager
def owned_data_dirs() -> Iterator[dict[str, Path]]:
    """Create data internally; no caller-controlled filesystem/config input."""
    with tempfile.TemporaryDirectory(prefix="offerpilot-capture-owned-", dir="/tmp") as owned:
        paths = {suite: Path(owned) / suite for suite in ("synthetic_week", "unseen_holdouts")}
        for path in paths.values():
            path.mkdir(mode=0o700)
            _OWNED_DATA_DIRS.add(path)
        try:
            yield paths
        finally:
            _OWNED_DATA_DIRS.difference_update(paths.values())


def capture_body(raw: bytes, operation: str, capability: str) -> dict[str, Any]:
    """Accept body bytes only; retain every field and redact only our own token."""
    _require(0 < len(raw) <= BODY_LIMIT, "request_body_bound")
    _require(capability.startswith("synthetic-capture-"), "synthetic_capability_required")
    body = raw.decode("utf-8")
    count = body.count(capability)
    safe = body.replace(capability, "[REDACTED_SYNTHETIC_CAPABILITY]")
    payload = json.loads(safe)
    _require(isinstance(payload, dict) and isinstance(payload.get("messages"), list),
             "request_body_shape")
    return {"operation": operation, "body_utf8": safe, "payload": payload,
            "received_body_bytes": len(raw),
            "received_body_sha256": hashlib.sha256(raw).hexdigest(),
            "stored_body_sha256": hashlib.sha256(safe.encode("utf-8")).hexdigest(),
            "synthetic_capability_redactions": count}


def summarize_request(record: dict[str, Any]) -> dict[str, Any]:
    payload = record["payload"]
    locations: dict[str, list[dict[str, Any]]] = {name: [] for name in MARKERS}
    messages = payload["messages"]
    for index, message in enumerate(messages):
        content = message.get("content", "")
        if not isinstance(content, str):
            continue
        surface = ("system_policy" if message.get("role") == "system" else
                   "current_user" if index == len(messages) - 1 else
                   "companion_reference" if "角色日常" in content else "history_or_context")
        for name, pattern in MARKERS.items():
            matches = list(pattern.finditer(content))
            if matches:
                locations[name].append({
                    "path": f"messages/{index}/content", "role": message.get("role"),
                    "surface": surface,
                    "matches": [{"offset": match.start(), "text": match.group()}
                                for match in matches],
                })
    return {"operation": record["operation"], "message_count": len(messages),
            "tool_count": len(payload.get("tools", [])),
            "request_fields": sorted(payload), "markers": locations}


class SyntheticUpstream:
    """Fixed fake-only HTTP boundary; no upstream endpoint or sender can be supplied."""

    def __init__(self, data_dir: Path, operations: tuple[str, ...], *, fake_only: bool):
        _require(fake_only is True, "fake_only_required")
        _require(data_dir in _OWNED_DATA_DIRS and data_dir.resolve() == data_dir
                 and data_dir.is_dir() and not any(data_dir.iterdir()), "owned_empty_data_required")
        self.data_dir, self.operations = data_dir, operations
        self.requests: list[dict[str, Any]] = []
        self.active: tuple[str, str] | None = None
        self.rejections: list[str] = []
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def do_POST(self) -> None:
                with owner._lock:
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        _require(self.path == "/v1/chat/completions" and
                                 0 < length <= BODY_LIMIT, "request_path_or_bound")
                        _require(owner.active is not None, "stage_closed")
                        assert owner.active is not None
                        operation, capability = owner.active
                        _require(self.headers.get("Authorization") == f"Bearer {capability}",
                                 "invalid_synthetic_capability")
                        _require(not any(r["operation"] == operation for r in owner.requests),
                                 "second_request_forbidden")
                        record = capture_body(self.rfile.read(length), operation, capability)
                        owner.requests.append(record)
                        # A fixed plain-text answer cannot invoke any product tool.
                        chunks = [
                            {"role": "assistant", "content": f"合成普通回复：{operation}。"}, {},
                        ]
                        body = b"".join(b"data: " + _encode({
                            "id": "synthetic-request-capture", "object": "chat.completion.chunk",
                            "created": 0, "model": "offline-model", "choices": [{
                                "index": 0, "delta": delta,
                                "finish_reason": "stop" if not delta else None,
                            }],
                        }) + b"\n\n" for delta in chunks) + b"data: [DONE]\n\n"
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                    except (RuntimeError, ValueError, UnicodeError):
                        owner.rejections.append("fake_boundary_rejected")
                        body = b'{"error":{"message":"synthetic fixture rejected"}}'
                        self.send_response(400)
                        self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @contextmanager
    def stage(self, operation: str) -> Iterator[None]:
        from offerpilot.config import AIProviderProfile, Config, save_config

        _require(self.active is None and len(self.requests) < len(self.operations)
                 and operation == self.operations[len(self.requests)], "fixed_operation_order")
        capability = "synthetic-capture-" + secrets.token_urlsafe(24)
        self.active = operation, capability

        def configure(token: str) -> None:
            save_config(self.data_dir, Config(providers=[AIProviderProfile(
                id="offline-fixture", label="Synthetic body capture", provider="openai_compatible",
                api_key=token, base_url=f"http://127.0.0.1:{self.server.server_port}/v1",
                model="offline-model", context_window=65536, max_output_tokens=512,
            )], active_provider_id="offline-fixture", fallback_provider_ids=[],
                chat_auto_approve_writes=False,
                confirmation_secret="synthetic-capture-confirmation-only"))

        configure(capability)
        try:
            yield
        finally:
            self.active = None
            configure("synthetic-capture-revoked")
        _require(bool(self.requests) and self.requests[-1]["operation"] == operation
                 and not self.rejections, "one_completed_fake_request_required")

    def close(self) -> None:
        self.active = None
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _holdout_matrix(data_dir: Path, fake: SyntheticUpstream) -> dict[str, Any]:
    from scripts.companion_ai_validation import driver
    from sqlalchemy import func, select
    from offerpilot.models import ChatMessage, PilotTurnRecord, WriteOperation

    (data_dir / driver.CLOCK_FILE).write_text(str(driver.CLOCK_START))
    conversations: dict[str, int] = {}
    with driver._api(data_dir) as client:
        matrix = driver._Matrix(data_dir, client, fake.stage)
        try:
            matrix.event("app_open")
            for operation, text, previous in HOLDOUTS:
                before = matrix.view()
                with fake.stage(operation):
                    response = driver._request(client, operation, "POST", "/api/chat", {
                        "request_id": str(uuid4()),
                        "conversation_id": conversations[previous] if previous is not None else 0,
                        "message": text, "context_type": "workspace", "mode": "general",
                    })
                _require(not response.get("pending_action") and
                         not response.get("pending_clarification"), "holdout_no_pending_action")
                conversations[operation] = response["conversation_id"]
                after = matrix.view()
                _require(all(after[key] == before[key] for key in ("identity", "revision", "state")),
                         "holdout_companion_read_only")
                _require(fake.requests[-1]["payload"]["messages"][-1]["content"] == text,
                         "actual_current_message_captured")
                with matrix.sessions() as session:
                    turn = session.get(PilotTurnRecord, response["turn_id"])
                    _require(turn is not None and turn.state == "completed", "holdout_completed")
            with matrix.sessions() as session:
                tool_count = session.scalar(select(func.count()).select_from(ChatMessage).where(
                    (ChatMessage.role == "tool") | (ChatMessage.tool_calls != "")))
                writes = session.scalar(select(func.count()).select_from(WriteOperation))
                _require(tool_count == 0 and writes == 0, "holdout_no_tool_execution")
            return {"passed": True, "no_tool_records": True, "no_write_operations": True,
                    "ordinary_count": len(HOLDOUTS), "companion_read_only": True}
        finally:
            matrix.sessions.kw["bind"].dispose()


def run_capture(*, fake_only: bool, label: str) -> Path:
    _require(fake_only is True, "fake_only_required")
    _require(label in {"baseline", "final", "snapshot"}, "fixed_label_required")
    _require(sys.platform == "linux" and os.name == "posix", "unsupported_platform")
    clean_environment()
    for path in (ROOT, ROOT / "src"):
        sys.path.insert(0, str(path))
    # Install before any product import; the API child installs this guard too.
    import tests._offline_network_guard as guard

    _require(guard._EARLY_INSTALL, "early_network_guard_required")
    from scripts.companion_ai_validation import driver

    sources = source_manifest()
    evidence = Path(tempfile.mkdtemp(prefix=f"offerpilot-request-{label}-", dir="/tmp"))
    os.chmod(evidence, 0o700)
    records: list[dict[str, Any]] = []
    with owned_data_dirs() as owned:
        for suite, operations in (
            ("synthetic_week", driver.OPERATIONS),
            ("unseen_holdouts", tuple(case[0] for case in HOLDOUTS)),
        ):
            data_dir = owned[suite]
            fake = SyntheticUpstream(data_dir, operations, fake_only=True)
            try:
                result = (driver.run_matrix(data_dir, fake.stage) if suite == "synthetic_week"
                          else _holdout_matrix(data_dir, fake))
                _require(result["passed"] is True and len(fake.requests) == len(operations),
                         "complete_fixed_matrix_required")
                records.extend({"suite": suite, **record} for record in fake.requests)
                (evidence / f"{suite}.json").write_bytes(_encode(result) + b"\n")
            finally:
                fake.close()
    _require(source_manifest() == sources, "source_changed_during_capture")
    _require(not guard._denied, "non_loopback_attempted")
    summaries = [summarize_request(record) for record in records]
    report = {
        "schema_version": 1, "label": label, "synthetic_only": True, "fake_upstream_only": True,
        "historical_paid_payloads": False, "provider_calls": 0, "tool_executions": 0,
        "capture_boundary": "complete product HTTP request body at fake upstream, before SSE reply",
        "execution_mode": "offline-model; no provider broker, paid ledger, admission, or continuation",
        "source": sources, "historical_failure_case_numbers": list(HISTORICAL_FAILURE_CASES),
        "historical_failure_operations": list(HISTORICAL_FAILURE_OPERATIONS),
        "request_count": len(records), "requests": records,
    }
    (evidence / "requests.json").write_bytes(_encode(report) + b"\n")
    (evidence / "summary.json").write_bytes(_encode({
        "schema_version": 1, "label": label, "source": sources,
        "interpretation": "Prompt exposure only; fixed fake replies do not test model behavior.",
        "request_count": len(records), "requests": summaries,
        "marker_request_counts": {name: sum(bool(row["markers"][name]) for row in summaries)
                                  for name in MARKERS},
    }) + b"\n")
    for path in evidence.iterdir():
        path.chmod(0o600)
    return evidence


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # A mistakenly supplied credential must not be echoed with an unknown flag.
        self.exit(2, "synthetic capture: unsupported or missing argument\n")


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--fake-only", action="store_true", required=True)
    parser.add_argument("--label", choices=("baseline", "final", "snapshot"), default="snapshot")
    args = parser.parse_args(argv)
    try:
        path = run_capture(fake_only=args.fake_only, label=args.label)
    except Exception:
        # Never echo process logs, config, headers, environment, or exception bodies.
        print('{"passed":false,"error_code":"synthetic_capture_blocked"}')
        return 1
    print(json.dumps({"passed": True, "evidence_dir": str(path), "provider_calls": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
