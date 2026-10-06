"""Real frozen-process smoke; this is not Windows installation or GUI acceptance."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


HEADER = "X-OfferPilot-Desktop-Token"
HTTP = build_opener(ProxyHandler({}))


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def request(
    origin: str, path: str, token: str | None, *, method: str = "GET",
    payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    values = {HEADER: token} if token is not None else {}
    values.update(headers or {})
    data = None if payload is None else json.dumps(payload).encode()
    if data is not None:
        values["Content-Type"] = "application/json"
    req = Request(origin + path, data=data, headers=values, method=method)
    try:
        with HTTP.open(req, timeout=5) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def offline_environment(token: str) -> dict[str, str]:
    env = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "OFFERPILOT_DATA", "TIKTOKEN_CACHE_DIR",
                 "CUSTOM_TIKTOKEN_CACHE_DIR"):
        env.pop(name, None)
    env.update({
        "OFFERPILOT_DESKTOP_TOKEN": token,
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        # Catch accidental conventional HTTP downloads. This is not a network sandbox.
        "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost",
    })
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        env[name.lower()] = env[name]
    return env


def startup_failure(
    executable: Path, static: Path, data: Path, token: str, code: str, port: int = 0,
) -> None:
    process = subprocess.Popen(
        [str(executable), "--data-dir", str(data), "--static-dir", str(static),
         "--port", str(port)], cwd=data.parent, env=offline_environment(token),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    try:
        # Keep the parent pipe OPEN while waiting: communicate() would close it
        # and hide the fatal-exit deadlock this negative check must detect.
        check(process.wait(timeout=20) != 0, "Invalid startup unexpectedly succeeded")
        stdout, stderr = process.communicate(timeout=5)
        events = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
        check(any(event.get("type") == "offerpilot.desktop.error" and event.get("code") == code
                  for event in events), f"Missing structured startup failure {code}: {stderr[-2000:]}")
        check(not any(event.get("type") == "offerpilot.desktop.ready" for event in events),
              "Failed startup incorrectly reported readiness")
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


@contextmanager
def backend(
    executable: Path, static: Path, data: Path, token: str, port: int = 0,
) -> Iterator[str]:
    with tempfile.TemporaryFile(mode="w+b") as errors:
        process = subprocess.Popen(
            [str(executable), "--data-dir", str(data), "--static-dir", str(static),
             "--port", str(port)],
            cwd=data.parent, env=offline_environment(token), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8",
        )
        lines: queue.Queue[str | None] = queue.Queue()

        def read_stdout() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                lines.put(line)
            lines.put(None)

        reader = threading.Thread(target=read_stdout, daemon=True)
        reader.start()
        origin = ""
        graceful = False
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    line = lines.get(timeout=0.5)
                except queue.Empty:
                    continue
                check(line is not None, "Backend exited before reporting readiness")
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") != "offerpilot.desktop.ready":
                    check(event.get("type") != "offerpilot.desktop.error", str(event))
                    continue
                check(event.get("protocol") == 1, "Unexpected desktop protocol")
                check(event.get("pid") == process.pid, "Ready PID differs from child PID")
                origin = str(event["origin"])
                address = urlsplit(origin)
                check(address.scheme == "http" and address.hostname == "127.0.0.1",
                      "Backend did not bind to the expected loopback origin")
                check(address.port is not None and address.port > 0, "Missing runtime port")
                if port:
                    check(address.port == port, "Backend changed a persisted origin")
                break
            check(bool(origin), "Backend readiness timed out")
            while time.monotonic() < deadline:
                try:
                    status, body = request(origin, "/api/health", token)
                    if status == 200 and json.loads(body).get("status") == "ok":
                        break
                except (URLError, TimeoutError, ConnectionError):
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Backend health timed out")
            yield origin
            assert process.stdin is not None
            process.stdin.close()
            check(process.wait(timeout=15) == 0, "Backend failed graceful stdin-EOF shutdown")
            graceful = True
            address = urlsplit(origin)
            try:
                with socket.create_connection(("127.0.0.1", address.port), timeout=1):
                    raise RuntimeError("Backend port still accepts connections after exit")
            except (ConnectionRefusedError, TimeoutError, OSError):
                pass
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            reader.join(timeout=5)
            if process.stdout:
                process.stdout.close()
            if not graceful:
                errors.seek(0)
                details = errors.read().decode("utf-8", errors="replace")
                # Generated token must never appear in logs or uploaded diagnostics.
                print(details[-12000:].replace(token, "[redacted]"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, type=Path)
    parser.add_argument("--static-dir", required=True, type=Path)
    args = parser.parse_args()
    executable, static = args.backend.resolve(), args.static_dir.resolve()
    check(executable.is_file(), f"Missing frozen executable: {executable}")
    check((static / "index.html").is_file(), f"Missing built frontend: {static}")
    token = secrets.token_hex(32)
    # An isolated cwd and no PYTHONPATH ensure the executable cannot borrow source files.
    with tempfile.TemporaryDirectory(prefix="OfferPilot 桌面 smoke ") as root:
        data = Path(root) / "workspace with spaces"
        data.mkdir()
        self_check = subprocess.run(
            [str(executable), "--packaging-self-check"], cwd=root,
            env=offline_environment(token), capture_output=True, text=True,
            encoding="utf-8", timeout=90,
        )
        check(self_check.returncode == 0,
              "Frozen dependency self-check failed:\n" + self_check.stderr[-12000:])
        check('"packaging_self_check": "ok"' in self_check.stdout,
              "Frozen dependency self-check did not report success")
        print("Frozen dependency self-check passed", flush=True)
        startup_failure(executable, static, data, "invalid", "invalid_token")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            startup_failure(executable, static, data, token, "port_unavailable",
                            port=occupied.getsockname()[1])
        print("Structured startup failures and cleanup passed", flush=True)
        payload = {"company_name": "Desktop Validation 示例", "position_name": "Local only",
                   "notes": "Persisted across a frozen backend restart"}
        with backend(executable, static, data, token) as origin:
            print("Frozen backend startup passed", flush=True)
            port = urlsplit(origin).port
            for path in ("/api/health", "/api/applications", "/", "/applications/desktop-smoke"):
                status, _ = request(origin, path, None)
                check(status in (401, 403), f"Unauthenticated request accepted: {path} ({status})")
            for headers, credential in (({}, "wrong-token"), ({"Origin": "https://example.org"}, token),
                                        ({"Origin": "null"}, token),
                                        ({"Host": "example.org"}, token)):
                status, _ = request(origin, "/api/applications", credential, headers=headers)
                check(status in (400, 401, 403), f"Foreign request accepted ({status})")
            status, _ = request(origin, "/api/applications", None, method="POST", payload=payload)
            check(status in (401, 403), "Unauthenticated mutation accepted")
            status, html = request(origin, "/applications/desktop-smoke", token)
            check(status == 200 and b"root" in html, "Packaged SPA fallback failed")
            assets = re.findall(rb'(?:src|href)="(/assets/[^"]+)"', html)
            check(bool(assets), "Built frontend has no asset references")
            for asset in assets:
                path = asset.decode("utf-8")
                status, body = request(origin, path, token)
                check(status == 200 and bool(body) and not body.startswith(b"<!doctype html>"),
                      f"Built frontend asset missing: {path}")
                status, _ = request(origin, path, None)
                check(status in (401, 403), f"Unauthenticated asset accepted: {path}")
            status, body = request(origin, "/api/applications", token, method="POST",
                                   payload=payload, headers={"Origin": origin})
            check(status == 201, f"Application save failed ({status}): {body!r}")
            identifier = json.loads(body)["id"]
        print("Security, SPA/assets, save, and first shutdown passed", flush=True)
        check((data / "data.db").is_file(), "Database not written to the isolated data directory")
        check((data / "config.json").is_file(), "Configuration not persisted")
        next_token = secrets.token_hex(32)
        with backend(executable, static, data, next_token, port=port) as origin:
            status, _ = request(origin, "/api/applications", token)
            check(status in (401, 403), "Previous process token accepted after restart")
            status, body = request(origin, f"/api/applications/{identifier}", next_token)
            check(status == 200, "Saved application missing after restart")
            restored = json.loads(body)
            check(all(restored.get(key) == value for key, value in payload.items()),
                  "Saved application changed after restart")
            status, body = request(origin, "/api/applications", next_token)
            check(status == 200 and len(json.loads(body)) == 1, "Unexpected application count")
    print("Frozen backend smoke passed: dependency imports, cached tokenizers, loopback auth, "
          "SPA, SQLite save/restart, stable origin, token rotation, and child cleanup.")
    print("Windows installer, Electron renderer, and UI save/restart acceptance remain separate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
