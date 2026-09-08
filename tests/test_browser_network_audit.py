from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import websockets


ROOT = Path(__file__).parents[1]
AUDIT = ROOT / "scripts" / "browser-network-audit.py"


def _load_browser_audit():
    spec = importlib.util.spec_from_file_location("browser_network_audit", AUDIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeCdp:
    def __init__(
        self,
        *,
        reject_network: bool = False,
        wrong_target: bool = False,
        no_attach: bool = False,
        drop_after_navigation: bool = False,
        emit_unowned_request: bool = False,
        response_body_error: bool = False,
        streaming_response: bool = False,
        list_response: bool = False,
        hold_streaming_response: bool = False,
        destroy_target_after_navigation: bool = False,
        story_steps: tuple[str, ...] = (),
    ) -> None:
        self.reject_network = reject_network
        self.wrong_target = wrong_target
        self.no_attach = no_attach
        self.drop_after_navigation = drop_after_navigation
        self.emit_unowned_request = emit_unowned_request
        self.response_body_error = response_body_error
        self.streaming_response = streaming_response
        self.list_response = list_response
        self.hold_streaming_response = hold_streaming_response
        self.destroy_target_after_navigation = destroy_target_after_navigation
        self.methods: list[str] = []
        self.story_steps = story_steps
        self.injected_sources: list[str] = []
        self.expected_url = "http://127.0.0.1:18766/"
        self.ready = threading.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ws_server = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self.ready.wait(5)
        assert self.loop is not None and self.ws_server is not None
        ws_port = self.ws_server.sockets[0].getsockname()[1]

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # type: ignore[no-untyped-def]
                if self.path != "/json/version":
                    self.send_error(404)
                    return
                payload = json.dumps({
                    "webSocketDebuggerUrl": f"ws://127.0.0.1:{ws_port}/devtools/browser",
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):  # type: ignore[no-untyped-def]
                return

        self.http_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        self.http_thread.start()
        self.debugging_url = f"http://127.0.0.1:{self.http_server.server_port}"

    async def handler(self, websocket):  # type: ignore[no-untyped-def]
        async for raw in websocket:
            message = json.loads(raw)
            command_id = message["id"]
            method = message["method"]
            self.methods.append(method)
            session_id = message.get("sessionId")
            if method == "Target.createTarget":
                if not self.no_attach:
                    attached_id = "wrong-target" if self.wrong_target else "main-target"
                    await websocket.send(json.dumps({
                        "method": "Target.attachedToTarget",
                        "params": {
                            "sessionId": "main-session",
                            "targetInfo": {"targetId": attached_id, "type": "page", "url": "about:blank"},
                        },
                    }))
                await websocket.send(json.dumps({"id": command_id, "result": {"targetId": "main-target"}}))
            elif method == "Network.enable" and self.reject_network:
                await websocket.send(json.dumps({"id": command_id, "error": {"code": -1}}))
            elif method == "Network.getResponseBody" and self.response_body_error:
                await websocket.send(json.dumps({"id": command_id, "error": {"code": -32000}}))
            elif method == "Network.getResponseBody" and self.list_response:
                await websocket.send(json.dumps({
                    "id": command_id,
                    "result": {
                        "body": json.dumps([
                            {"id": 2, "source_kind": "pilot"},
                            {"id": 1, "source_kind": "ui"},
                        ]),
                    },
                }))
            elif method == "Page.addScriptToEvaluateOnNewDocument":
                params = message.get("params")
                source = params.get("source") if isinstance(params, dict) else None
                if isinstance(source, str):
                    self.injected_sources.append(source)
                await websocket.send(json.dumps({"id": command_id, "result": {}}))
            elif method == "Runtime.evaluate":
                steps = self.story_steps if self.injected_sources else ()
                await websocket.send(json.dumps({
                    "id": command_id,
                    "result": {"result": {"value": json.dumps(list(steps))}},
                }))
            else:
                await websocket.send(json.dumps({"id": command_id, "result": {}}))
                if method == "Page.navigate":
                    await websocket.send(json.dumps({
                        "method": "Network.requestWillBeSent",
                        "sessionId": session_id,
                        "params": {
                            "requestId": "api-request" if self.response_body_error else "page-request",
                            "request": {
                                "method": "GET",
                                "url": self.expected_url + ("api/health" if self.response_body_error else ""),
                            },
                        },
                    }))
                    if self.response_body_error:
                        await websocket.send(json.dumps({
                            "method": "Network.responseReceived",
                            "sessionId": session_id,
                            "params": {
                                "requestId": "api-request",
                                "response": {
                                    "status": 200,
                                    **({"mimeType": "text/event-stream"} if self.streaming_response else {}),
                                },
                            },
                        }))
                        if not (self.streaming_response and self.hold_streaming_response):
                            await websocket.send(json.dumps({
                                "method": "Network.loadingFinished",
                                "sessionId": session_id,
                                "params": {"requestId": "api-request"},
                            }))
                    if self.list_response:
                        await websocket.send(json.dumps({
                            "method": "Network.requestWillBeSent",
                            "sessionId": session_id,
                            "params": {
                                "requestId": "list-request",
                                "request": {
                                    "method": "GET",
                                    "url": self.expected_url + "api/applications/1/job-description/versions?offset=0&limit=50",
                                },
                            },
                        }))
                        await websocket.send(json.dumps({
                            "method": "Network.responseReceived",
                            "sessionId": session_id,
                            "params": {
                                "requestId": "list-request",
                                "response": {"status": 200, "mimeType": "application/json"},
                            },
                        }))
                        await websocket.send(json.dumps({
                            "method": "Network.loadingFinished",
                            "sessionId": session_id,
                            "params": {"requestId": "list-request"},
                        }))
                    if self.destroy_target_after_navigation:
                        await websocket.send(json.dumps({
                            "method": "Target.targetDestroyed",
                            "params": {"targetId": "main-target"},
                        }))
                    if self.emit_unowned_request:
                        unowned_flow = (
                            ("POST", "/api/applications/1/events/2/mock-interview/attempts"),
                            ("POST", "/api/applications/1/events/2/mock-interview/attempts/99/turns"),
                            ("POST", "/api/applications/1/events/2/mock-interview/attempts/99/turns/2/question"),
                            ("POST", "/api/applications/1/events/2/mock-interview/attempts/99/finish"),
                            ("POST", "/api/applications/1/events/2/mock-interview/attempts/99/review-drafts"),
                            ("GET", "/api/applications/1/events/2/mock-interview/attempts"),
                        )
                        for unowned_method, unowned_path in unowned_flow:
                            await websocket.send(json.dumps({
                                "method": "Network.requestWillBeSent",
                                "sessionId": "unowned-session",
                                "params": {"request": {"method": unowned_method, "url": self.expected_url.rstrip("/") + unowned_path}},
                            }))
                    if self.drop_after_navigation:
                        await asyncio.sleep(0.1)
                        await websocket.close()

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.ws_server = self.loop.run_until_complete(self._start_server())
        self.ready.set()
        self.loop.run_forever()

    async def _start_server(self):  # type: ignore[no-untyped-def]
        return await websockets.serve(self.handler, "127.0.0.1", 0)

    def close(self) -> None:
        self.http_server.shutdown()
        assert self.loop is not None and self.ws_server is not None
        async def stop_server() -> None:
            self.ws_server.close()
            await self.ws_server.wait_closed()
            self.loop.stop()

        self.loop.call_soon_threadsafe(lambda: asyncio.create_task(stop_server()))
        self.thread.join(timeout=5)


def _run_auditor(
    tmp_path: Path,
    fake: _FakeCdp,
    timeout: str = "2",
    stop_delay_seconds: float = 0,
) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).parents[1]
    audit = tmp_path / "browser.jsonl"
    stop = tmp_path / "stop"
    ready = tmp_path / "ready"
    diagnostic = tmp_path / "diagnostic.json"
    flush = tmp_path / "flush"
    flushed = tmp_path / "flushed"
    process = subprocess.Popen(
        [
            sys.executable,
            str(root / "scripts" / "browser-network-audit.py"),
            "--debugging-url", fake.debugging_url,
            "--expected-url", fake.expected_url,
            "--audit", str(audit),
            "--stop-file", str(stop),
            "--ready-file", str(ready),
            "--diagnostic-file", str(diagnostic),
            "--flush-file", str(flush),
            "--flushed-file", str(flushed),
            "--ready-timeout-seconds", timeout,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and process.poll() is None and not ready.exists():
        time.sleep(0.05)
    if ready.exists():
        if stop_delay_seconds:
            time.sleep(stop_delay_seconds)
        flush.touch()
        flush_deadline = time.monotonic() + 5
        while time.monotonic() < flush_deadline and not flushed.exists() and process.poll() is None:
            time.sleep(0.05)
        stop.touch()
    returncode = process.wait(timeout=10)
    return subprocess.CompletedProcess(process.args, returncode, process.stdout.read(), process.stderr.read())


def test_browser_network_audit_uses_browser_target_and_records_navigation(tmp_path):
    fake = _FakeCdp()
    try:
        result = _run_auditor(tmp_path, fake)
        assert result.returncode == 0, result.stderr
        records = [json.loads(line) for line in (tmp_path / "browser.jsonl").read_text(encoding="utf-8").splitlines()]
        assert "18766" in json.dumps(records)
        assert records[-1] == {
            "kind": "browser_story_interactions",
            "observed_at_ns": records[-1]["observed_at_ns"],
            "target_id": "main-target",
            "session_id": "main-session",
            "steps": [],
        }
    finally:
        fake.close()


def test_browser_network_audit_records_allowlisted_story_actions(tmp_path):
    expected_steps = (
        "ui-library",
        "ui-source-picker",
        "ui-generate",
        "ui-confirm",
        "pilot-entry",
        "pilot-source-picker",
        "pilot-generate",
        "pilot-confirm",
    )
    fake = _FakeCdp(story_steps=expected_steps)
    try:
        result = _run_auditor(tmp_path, fake)
        assert result.returncode == 0, result.stderr
        records = [json.loads(line) for line in (tmp_path / "browser.jsonl").read_text(encoding="utf-8").splitlines()]
        assert records[-1]["kind"] == "browser_story_interactions"
        assert records[-1]["steps"] == list(expected_steps)
        assert len(fake.injected_sources) == 1
        assert "window.__offerpilotStoryAuditSteps" in fake.injected_sources[0]
        assert "[data-story-audit]" in fake.injected_sources[0]
    finally:
        fake.close()


def test_browser_network_audit_rejects_unallowlisted_story_action(tmp_path):
    fake = _FakeCdp(story_steps=("ui-library", "forged-action"))
    try:
        result = _run_auditor(tmp_path, fake)
        assert result.returncode != 0
        assert "story interaction audit payload is invalid" in result.stderr
    finally:
        fake.close()


def test_browser_network_audit_fails_closed_for_wrong_target_and_network_rejection(tmp_path):
    for kwargs in ({"wrong_target": True}, {"reject_network": True}):
        fake = _FakeCdp(**kwargs)
        try:
            result = _run_auditor(tmp_path / str(len(kwargs)), fake, timeout="0.5")
            assert result.returncode != 0
        finally:
            fake.close()


def test_browser_network_audit_fails_closed_on_ready_timeout(tmp_path):
    fake = _FakeCdp(no_attach=True)
    try:
        result = _run_auditor(tmp_path / "timeout", fake, timeout="0.2")
        assert result.returncode != 0
        assert not (tmp_path / "timeout" / "ready").exists()
    finally:
        fake.close()


def test_browser_network_audit_propagates_post_ready_disconnect(tmp_path):
    fake = _FakeCdp(drop_after_navigation=True)
    try:
        result = _run_auditor(tmp_path / "disconnect", fake, stop_delay_seconds=0.5)
        assert result.returncode != 0
        diagnostic = json.loads((tmp_path / "disconnect" / "diagnostic.json").read_text(encoding="utf-8"))
        assert diagnostic["failure_category"] == "cdp_connection_closed"
        assert diagnostic["ready"] is True
        assert diagnostic["stop_requested"] is False
        assert diagnostic["main_target_id"] == "main-target"
        assert diagnostic["main_session_id"] == "main-session"
    finally:
        fake.close()


def test_browser_network_audit_records_clean_stop_diagnostic(tmp_path):
    fake = _FakeCdp()
    try:
        result = _run_auditor(tmp_path / "clean", fake, stop_delay_seconds=0.2)
        assert result.returncode == 0, result.stderr
        diagnostic = json.loads((tmp_path / "clean" / "diagnostic.json").read_text(encoding="utf-8"))
        assert diagnostic["status"] == "passed"
        assert diagnostic["failure_category"] is None
        assert diagnostic["ready"] is True
        assert diagnostic["stop_requested"] is True
        assert "Browser.getVersion" in fake.methods
    finally:
        fake.close()


def test_browser_network_audit_fails_closed_when_api_response_body_is_unavailable(tmp_path):
    fake = _FakeCdp(response_body_error=True)
    try:
        result = _run_auditor(tmp_path / "body-error", fake)
        assert result.returncode != 0
        diagnostic = json.loads((tmp_path / "body-error" / "diagnostic.json").read_text(encoding="utf-8"))
        assert diagnostic["failure_category"] == "response_body_unavailable"
    finally:
        fake.close()


def test_browser_network_audit_fails_closed_when_main_target_is_destroyed(tmp_path):
    fake = _FakeCdp(destroy_target_after_navigation=True)
    try:
        result = _run_auditor(tmp_path / "destroyed", fake)
        assert result.returncode != 0
        diagnostic = json.loads((tmp_path / "destroyed" / "diagnostic.json").read_text(encoding="utf-8"))
        assert diagnostic["failure_category"] == "dedicated_target_destroyed"
    finally:
        fake.close()


def test_browser_network_audit_ignores_requests_from_another_target(tmp_path):
    fake = _FakeCdp(emit_unowned_request=True)
    try:
        result = _run_auditor(tmp_path / "unowned", fake)
        assert result.returncode == 0, result.stderr
        records = [json.loads(line) for line in (tmp_path / "unowned" / "browser.jsonl").read_text(encoding="utf-8").splitlines()]
        requests = [record for record in records if record["kind"] == "browser_request"]
        assert len(requests) == 1
        assert requests[0]["target_id"] == "main-target"
        assert requests[0]["session_id"] == "main-session"
        assert requests[0]["method"] == "GET"
        assert all("mock-interview" not in record["url"] for record in requests)
        assert records[-1]["kind"] == "browser_story_interactions"
        assert records[-1]["steps"] == []
    finally:
        fake.close()


def test_browser_network_audit_accepts_completed_sse_without_response_body(tmp_path):
    fake = _FakeCdp(response_body_error=True, streaming_response=True)
    try:
        result = _run_auditor(tmp_path / "sse-body-error", fake, stop_delay_seconds=0.2)
        assert result.returncode == 0, result.stderr
        diagnostic = json.loads((tmp_path / "sse-body-error" / "diagnostic.json").read_text(encoding="utf-8"))
        assert diagnostic["status"] == "passed"
        assert diagnostic["failure_category"] is None
    finally:
        fake.close()


def test_browser_network_audit_records_list_response_metadata(tmp_path):
    fake = _FakeCdp(list_response=True)
    try:
        result = _run_auditor(tmp_path / "list-metadata", fake, stop_delay_seconds=0.2)
        assert result.returncode == 0, result.stderr
        records = [
            json.loads(line)
            for line in (tmp_path / "list-metadata" / "browser.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        history = next(
            record
            for record in records
            if record["kind"] == "browser_response"
            and "job-description/versions?" in record["url"]
        )
        assert history["response_jd_version_ids"] == [2, 1]
        assert history["response_source_kinds"] == ["pilot", "ui"]
    finally:
        fake.close()


def test_browser_network_audit_does_not_timeout_on_active_sse(tmp_path):
    fake = _FakeCdp(response_body_error=True, streaming_response=True, hold_streaming_response=True)
    try:
        result = _run_auditor(tmp_path / "active-sse", fake, stop_delay_seconds=0.2)
        assert result.returncode == 0, result.stderr
        diagnostic = json.loads((tmp_path / "active-sse" / "diagnostic.json").read_text(encoding="utf-8"))
        assert diagnostic["status"] == "passed"
        assert diagnostic["failure_category"] is None
    finally:
        fake.close()


def test_browser_audit_preserves_private_triage_context_before_provider_call(tmp_path):
    module = _load_browser_audit()
    private_context = tmp_path / "triage-replay-context.json"
    audit = module.BrowserAudit(
        None,
        tmp_path / "audit.jsonl",
        tmp_path / "stop",
        private_context_file=private_context,
    )
    audit.target_sessions["target"] = "session"
    audit.handle = io.StringIO()
    message = {
        "sessionId": "session",
        "params": {
            "requestId": "triage-request",
            "request": {
                "method": "POST",
                "url": "http://127.0.0.1:8000/api/applications/10/opportunity-fit-reviews",
                "postData": json.dumps(
                    {
                        "schema_version": 2,
                        "resume_id": 7,
                        "jd_version_id": 9,
                        "jd_source_label": "UI JD",
                        "candidate_assertions": ["我负责过迁移"],
                        "idempotency_key": "same-key",
                    },
                    ensure_ascii=False,
                ),
            },
        },
    }

    asyncio.run(audit.record_request(message))

    stored = json.loads(private_context.read_text(encoding="utf-8"))
    assert stored["payload"]["idempotency_key"] == "same-key"
    assert "我负责过迁移" not in audit.handle.getvalue()


def test_browser_audit_allows_application_cdp_frames_larger_than_one_megabyte():
    script = AUDIT.read_text(encoding="utf-8")
    assert "max_size=_CDP_MAX_MESSAGE_BYTES" in script
    assert "_CDP_MAX_MESSAGE_BYTES = 8 * 1024 * 1024" in script
