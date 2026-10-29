"""One local legacy request. Input stays in stdin, never command-line arguments."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
from urllib.parse import urlsplit

import httpx


def main() -> None:
    value = json.load(sys.stdin)
    target = urlsplit(value["address"])
    if (target.scheme != "http" or target.hostname != "127.0.0.1" or not target.port
            or target.path not in {"", "/"} or target.query or target.fragment
            or target.username or target.password or value["method"] != "POST"
            or value["path"] not in {"/api/chat", "/api/chat/confirm"}):
        raise ValueError("local HTTP destination outside fixed routes")
    def emit(result: dict[str, Any]) -> None:
        event = dict(identity=value["identity"], elapsed_seconds=time.monotonic() - value["started"],
                     observed_at=datetime.now(timezone.utc).isoformat(),
                     **result)
        if value.get("progress_file"):
            with Path(value["progress_file"]).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        print(json.dumps(event), flush=True)

    try:
        remaining = value["deadline"] - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("local HTTP deadline before send")
        timeout = httpx.Timeout(remaining, connect=min(30, remaining),
                                write=min(30, remaining), pool=min(30, remaining))
        with httpx.Client(base_url=value["address"], trust_env=False, follow_redirects=False,
                          headers={"Authorization": "Bearer " + value["token"]}, timeout=timeout) as client:
            emit(dict(stage="send_started"))
            with client.stream(value["method"], value["path"], json=value["body"]) as response:
                emit(dict(stage="headers", status=response.status_code))
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
    # Body is returned only over the private pipe, never the metadata archive.
    print(json.dumps(dict(stage="result", identity=value["identity"], **result)), flush=True)


if __name__ == "__main__":
    main()
