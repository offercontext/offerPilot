"""Real local HTTP workers; no Provider, async cancellation, or silent resends."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import base64
import json
import subprocess

import httpx
import pytest

from offerpilot.evaluation.core_actions import CoreHTTPDriver
from offerpilot.evaluation.adapters import DriverError


@contextlib.contextmanager
def local_server(mode):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append((self.path, self.headers["Authorization"]))
            if mode == "no-headers":
                time.sleep(12)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=gbk" if mode == "charset"
                             else "application/json")
            self.end_headers()
            if mode == "charset":
                self.wfile.write("中文".encode("gbk"))
                return
            if mode == "stalled-body":
                time.sleep(12)
                return
            parts = [b'{"message": "', b'synthetic', b'"}']
            try:
                for part in parts:
                    if mode == "trickle":
                        time.sleep(3)
                    self.wfile.write(part)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def driver_for(address, tmp_path, *, remaining=10, wait=120, reserve=0):
    return CoreHTTPDriver(address, "synthetic", time.monotonic() + remaining, [], 8,
        blocking_waits={"/api/chat": wait, "/api/chat/confirm": wait},
        cleanup_reserve=reserve, diagnostic_dir=tmp_path,
        request_context={"epoch": 1, "trial_key": {"fixture": "local-only"}})


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/confirm"])
def test_actual_owned_local_request_preserves_response_and_bearer(tmp_path, path):
    with local_server("success") as (address, requests):
        driver = driver_for(address, tmp_path, remaining=600, reserve=30)
        try:
            assert driver.request("POST", path) == {"message": "synthetic"}
            assert requests == [(path, "Bearer synthetic")]
            assert driver.reply_counter[0] == 1
            record = driver.local_http_observations[0]
            assert record["owned_tree_exited"] and record["child_exit"] == 0
            assert record["response_received"] and record["response_status"] == 200
            assert record["body_complete"] is True
            assert record["timeout_seconds"] == 121
            assert "synthetic" not in Path(record["progress_file"]).read_text(encoding="utf-8")
        finally:
            driver.close()


@pytest.mark.parametrize("mode,headers", [("no-headers", None),
    ("stalled-body", True), ("trickle", True)])
def test_absolute_deadline_stops_owned_worker_and_retains_header_scope(tmp_path, mode, headers):
    with local_server(mode) as (address, requests):
        driver = driver_for(address, tmp_path, remaining=10, reserve=2)
        started = time.monotonic()
        try:
            with pytest.raises(TimeoutError, match="deadline"):
                driver.request("POST", "/api/chat")
            assert time.monotonic() - started < 10
            assert len(requests) == driver.reply_counter[0] == 1
            assert not driver.observations
            record = driver.local_http_observations[0]
            assert record["owned_tree_exited"] and record["timed_out"]
            assert record["response_received"] is headers
            assert record["body_complete"] is None
            assert driver.request_errors[0]["response_received"] is headers
        finally:
            driver.close()


def test_late_child_result_is_rejected_without_accepting_or_resending(tmp_path, monkeypatch):
    from offerpilot.evaluation import core_actions
    driver = driver_for("http://127.0.0.1:1", tmp_path)
    clock = [100.0]
    driver.deadline = 700
    calls = []

    def late(*args):
        calls.append(args)
        clock[0] = 222
        return httpx.Response(200, json={"message": "late"})

    monkeypatch.setattr(core_actions.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(driver, "_blocking_request", late)
    try:
        with pytest.raises(TimeoutError, match="deadline"):
            driver.request("POST", "/api/chat")
        assert len(calls) == driver.reply_counter[0] == 1
        assert not driver.observations
    finally:
        driver.close()


def test_insufficient_cleanup_reserve_sends_nothing(tmp_path):
    with local_server("success") as (address, requests):
        driver = driver_for(address, tmp_path, remaining=10, reserve=30)
        try:
            with pytest.raises(TimeoutError, match="deadline"):
                driver.request("POST", "/api/chat")
            assert requests == [] and driver.reply_counter[0] == 0
        finally:
            driver.close()


def test_non_json_charset_metadata_survives_worker_without_sensitive_headers(tmp_path):
    with local_server("charset") as (address, requests):
        driver = driver_for(address, tmp_path)
        try:
            with pytest.raises(DriverError, match="non-JSON"):
                driver.request("POST", "/api/chat")
            assert len(requests) == 1
            assert driver.observations[0]["response"] == "中文"
        finally:
            driver.close()


@pytest.mark.parametrize("request_timeout", [False, True])
def test_watcher_close_failure_stays_unverified_and_preserves_request_error(tmp_path, monkeypatch,
                                                                        request_timeout):
    from offerpilot.evaluation import core_actions
    pipes_closed = []
    calls = []

    def communicate(payload=None, timeout=None):
        calls.append(payload)
        if request_timeout and len(calls) == 1:
            raise subprocess.TimeoutExpired("safe-worker", timeout)
        if payload is None:
            return b"", None
        value = json.loads(payload)
        return json.dumps(dict(stage="result", identity=value["identity"], ok=True, status=200,
            headers={"content-type": "application/json"},
            content=base64.b64encode(b'{"ok":true}').decode())).encode(), None

    process = SimpleNamespace(communicate=communicate, returncode=0, poll=lambda: 0,
        stdin=SimpleNamespace(close=lambda: pipes_closed.append("stdin")),
        stdout=SimpleNamespace(close=lambda: pipes_closed.append("stdout")))

    def close(**kwargs):
        raise TimeoutError("watcher timeout")

    tree = SimpleNamespace(process=process, wait=lambda **kw: 0, kill=lambda: None, close=close)
    monkeypatch.setattr(core_actions, "process_tree", lambda *args, **kw: tree)
    driver = driver_for("http://127.0.0.1:1", tmp_path, remaining=10, reserve=2)
    with pytest.raises(TimeoutError, match="deadline" if request_timeout else "watcher"):
        driver.request("POST", "/api/chat")
    record = driver.local_http_observations[0]
    assert record["owned_process_tree_exited"] and not record["owned_tree_exited"]
    assert not record["watcher_closed"] and record["cleanup_error_type"] == "TimeoutError"
    assert pipes_closed == ["stdin", "stdout"] and not driver.observations
    with pytest.raises(DriverError, match="unverified"):
        driver.close()


@pytest.mark.parametrize("method,path", [("GET", "/api/chat"),
    ("POST", "/api/pilot/runtime/v1/turns"), ("POST", "/other")])
def test_fast_routes_keep_original_thirty_second_cap(tmp_path, method, path):
    driver = driver_for("http://127.0.0.1:1", tmp_path, remaining=600)
    driver.client.close()
    observed = []

    def respond(request):
        observed.append(request)
        return httpx.Response(200, json={"ok": True})

    driver.client = httpx.Client(base_url="http://127.0.0.1", transport=httpx.MockTransport(respond))
    try:
        assert driver.request(method, path) == {"ok": True}
        assert len(observed) == 1 and not driver.local_http_observations
        assert observed[0].extensions["timeout"]["read"] == 30
    finally:
        driver.close()
