"""One local legacy request. Input stays in stdin, never command-line arguments."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import httpx


def sign_control(value: dict[str, Any], key: bytes, domain: str) -> dict[str, Any]:
    if len(key) != 32 or "control_mac" in value:
        raise ValueError("Fresh private control key and unsigned value required")
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode()
    return dict(value, control_mac=hmac.new(key, domain.encode() + b"\0" + raw,
                                          hashlib.sha256).hexdigest())


def verify_control(value: dict[str, Any], key: bytes, domain: str) -> dict[str, Any]:
    unsigned = {name: item for name, item in value.items() if name != "control_mac"}
    mac = value.get("control_mac")
    if not isinstance(mac, str) or not hmac.compare_digest(sign_control(unsigned, key, domain)["control_mac"], mac):
        raise ValueError("Local HTTP control authentication failed")
    return unsigned


class HeaderCancellationArbiter:
    """Order observed headers and cancellation, not physical packet arrival."""

    def __init__(self, emit: Any, lock: Any = None) -> None:
        self.emit = emit
        self.lock = lock if lock is not None else threading.RLock()
        self.headers_observed = False
        self.cancellation_accepted = False

    def headers(self, status: int) -> None:
        with self.lock:
            self.headers_observed = True
            self.emit(dict(stage="headers", status=status,
                arbitration="headers_after_cancellation" if self.cancellation_accepted
                else "headers_before_cancellation"))

    def cancel(self) -> bool:
        with self.lock:
            accepted = not self.headers_observed
            self.cancellation_accepted = accepted
            self.emit(dict(stage="cancel_accepted_before_headers" if accepted
                           else "cancel_refused_after_headers"))
            return accepted


def main() -> None:
    value = json.load(sys.stdin)
    control_key = bytes.fromhex(value["control_key"])
    if len(control_key) != 32:
        raise ValueError("Private per-request control key required")
    target = urlsplit(value["address"])
    if (target.scheme != "http" or target.hostname != "127.0.0.1" or not target.port
            or target.path not in {"", "/"} or target.query or target.fragment
            or target.username or target.password or value["method"] != "POST"
            or value["path"] not in {"/api/chat", "/api/chat/confirm"}):
        raise ValueError("local HTTP destination outside fixed routes")
    lock = threading.RLock()
    sequence = 0
    # Hold one exclusively created descriptor; never follow a replacement path
    # when appending observations in the target's data directory.
    progress_stream = (Path(value["progress_file"]).open("x", encoding="utf-8")
                       if value.get("progress_file") else None)
    def emit(result: dict[str, Any]) -> None:
        nonlocal sequence
        with lock:
            sequence += 1
            event = dict(identity=value["identity"], observation_seq=sequence,
                         elapsed_seconds=time.monotonic() - value["started"],
                         observed_at=datetime.now(timezone.utc).isoformat(), **result)
            signed = sign_control(event, control_key, "local-http-progress-v1")
            # Flush the private pipe first. A target-writable archive cannot
            # truncate authenticated observations out of the parent's pipe.
            print(json.dumps(signed), flush=True)
            if progress_stream is not None:
                progress_stream.write(json.dumps(signed) + "\n")
                progress_stream.flush()
                os.fsync(progress_stream.fileno())

    arbiter = HeaderCancellationArbiter(emit, lock)
    stop = threading.Event()
    watcher = None
    if value.get("cancel_file"):
        cancel_file = Path(value["cancel_file"])
        progress_file = Path(value["progress_file"])
        if (cancel_file.parent.resolve() != progress_file.parent.resolve()
                or cancel_file.name != "local-http-" + value["identity"]["observation_id"] + ".cancel.json"
                or cancel_file.is_symlink()):
            raise ValueError("local cancellation path is not bound to this request")

        def watch() -> None:
            try:
                while not stop.wait(.02):
                    if cancel_file.exists():
                        if cancel_file.is_symlink():
                            raise ValueError("local cancellation file is linked")
                        command = verify_control(json.loads(cancel_file.read_bytes()),
                                                 control_key, "local-http-cancel-v1")
                        if (set(command) != {"identity", "reason"}
                                or command["identity"] != value["identity"]
                                or command["reason"] != "offline_budget_denial"):
                            raise ValueError("local cancellation identity changed")
                        arbiter.cancel()
                        return
            except Exception as exc:
                emit(dict(stage="cancellation_error", error_type=type(exc).__name__))

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()

    try:
        remaining = value["deadline"] - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("local HTTP deadline before send")
        timeout = httpx.Timeout(remaining, connect=min(30, remaining),
                                write=min(30, remaining), pool=min(30, remaining))
        with httpx.Client(base_url=value["address"], trust_env=False, follow_redirects=False,
                          headers={"Authorization": "Bearer " + value["token"]}, timeout=timeout,
                          event_hooks={"response": [lambda response: arbiter.headers(response.status_code)]}) as client:
            emit(dict(stage="send_started"))
            with client.stream(value["method"], value["path"], json=value["body"]) as response:
                content = response.read()
        result = dict(ok=True, status=response.status_code,
                      headers={"content-type": response.headers["content-type"]}
                      if "content-type" in response.headers else {},
                      content=base64.b64encode(content).decode("ascii"))
    except Exception as exc:
        chain: list[str] = []
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen and len(chain) < 8:
            seen.add(id(current))
            chain.append(type(current).__name__)
            current = current.__cause__ if current.__cause__ is not None else current.__context__
        result = dict(ok=False, error_type=type(exc).__name__, exception_chain_types=chain)
    finally:
        stop.set()
        if watcher is not None:
            watcher.join(timeout=.2)
            if watcher.is_alive():
                raise RuntimeError("local cancellation observer did not stop")
        if progress_stream is not None:
            progress_stream.close()
    # Body is returned only over the private pipe, never the metadata archive.
    print(json.dumps(dict(stage="result", identity=value["identity"], **result)), flush=True)


if __name__ == "__main__":
    main()
