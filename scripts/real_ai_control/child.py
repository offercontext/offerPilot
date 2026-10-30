"""Actual CLI bootstrap for offline MockTransport qualification only.

There is deliberately no paid activation command in this implementation version.
"""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sys
from typing import Any

import httpx

from .archive import EvidenceArchive
from .bootstrap import Isolation, STAGES
from .channel import Binding, PipeClient
from .protocol import canonical, read_packet, sha
from .transport import ControlledTransport, completion_wrapper
from .wire import WirePolicy


def main() -> int:
    if sys.argv[1:] != ["--offline"]:
        raise PermissionError(
            "Paid execution is disabled; only explicit offline qualification exists"
        )
    reader, writer = sys.stdin.buffer, sys.stdout.buffer
    packet = read_packet(reader)
    data = packet.get("data")
    if (
        set(packet) != {"protocol", "kind", "seq", "data"}
        or packet["kind"] != "bootstrap"
        or packet["seq"] != 1
        or not isinstance(data, dict)
        or data.get("mode") != "offline-mock"
    ):
        raise ValueError("Private offline bootstrap required")
    key = data["fake_key"]
    if not isinstance(key, str) or not key.startswith("sk-offline-"):
        raise PermissionError("Only fake credentials accepted by offline bootstrap")
    fault = data.get("fault", "first_model_failure")
    if fault not in {
        "first_model_failure",
        "invalid_model_result",
        "archive_disk",
        "worker_hang",
        "startup_failure",
        "storage_escape",
        "extra_bind",
        "metadata_initial",
        "diagnostic_secret",
    }:
        raise ValueError("Unknown offline fault")
    # Third-party text on Python stdout cannot be mistaken for protocol. Native
    # writes to fd1 remain visible as malformed frames and fail parent control.
    binding = Binding(**data["binding"])
    policy = WirePolicy(64000, 8192, "2", "8", datetime.fromisoformat(data["price_valid_until"]))
    channel = PipeClient(reader, writer, binding=binding, policy=policy, key=key)
    archive = EvidenceArchive(Path(data["archive"]), secrets=(key,))
    archive.failure_sink = channel.failure
    if fault == "metadata_initial":

        def broken_metadata(stage: str) -> None:
            if stage == "metadata":
                raise OSError("Offline first metadata write failure")

        archive.fault = broken_metadata
    trace = archive.body("python-diagnostics", links={"body_layer": "UTF-8 Python diagnostic text"})
    import threading

    trace_lock = threading.RLock()

    class SafeText:
        encoding = "utf8"

        def write(self, text: str) -> int:
            with trace_lock:
                if not trace.finished:
                    trace.feed(text.encode("utf8", errors="replace"))
            return len(text)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return False

    sys.stdout = SafeText()
    sys.stderr = SafeText()
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["LITELLM_TELEMETRY"] = "False"
    import platform

    platform.platform()
    from openai import OpenAI
    from offerpilot import cli
    from offerpilot.ai import client as client_module
    from offerpilot.config import AIProviderProfile, Config, save_config
    from .account import once

    isolation = Isolation(Path(data["work"]), archive)
    config_dir = isolation.work / "source-config"
    save_config(
        config_dir,
        Config(
            providers=[
                AIProviderProfile(
                    id="bounded",
                    provider="openai_compatible",
                    api_key=key,
                    base_url="https://api.deepseek.com/v1",
                    model="deepseek-flash",
                    max_output_tokens=8192,
                    supports_json_schema=False,
                )
            ],
            active_provider_id="bounded",
            fallback_provider_id="",
            fallback_provider_ids=[],
            multimodal_provider_id="",
            chat_auto_approve_writes=False,
        ),
    )
    os.environ["OFFERPILOT_DATA"] = str(config_dir)

    def no_generation(request: httpx.Request) -> httpx.Response:
        if fault == "invalid_model_result":
            return httpx.Response(
                200,
                json={
                    "id": "offline-native-model-result",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "deepseek-flash",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": '{"offline":"intentionally invalid feature schema"}',
                                "reasoning_content": "offline provider block",
                                "tool_calls": [
                                    {
                                        "id": "offline-tool-call",
                                        "type": "function",
                                        "function": {
                                            "name": "get_application",
                                            "arguments": '{"application_id":1}',
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
            )
        return httpx.Response(
            503,
            json={
                "error": {
                    "message": "Offline first-generation failure fixture",
                    "type": "offline_fixture",
                }
            },
            headers={"x-request-id": "offline-provider-failure"},
        )

    provider_http = httpx.Client(
        transport=ControlledTransport(httpx.MockTransport(no_generation), channel, archive),
        trust_env=False,
        follow_redirects=False,
    )
    sdk = OpenAI(
        api_key=key,
        base_url="https://api.deepseek.com/v1",
        max_retries=0,
        timeout=45,
        http_client=provider_http,
    )
    client_module.completion = completion_wrapper(client_module.completion, archive, sdk)
    isolation.install(provider_client=provider_http)
    if fault == "archive_disk":

        def disk(stage: str) -> None:
            if stage == "body" and any(
                row["kind"] == "provider-dispatch" for row in archive.events
            ):
                raise OSError("Offline response archive disk failure")

        archive.fault = disk
    if fault == "startup_failure":
        import uvicorn

        setattr(uvicorn.Server, "run", lambda *a, **kw: None)
    if fault == "worker_hang":
        from offerpilot import smoke

        original_dispose = smoke._dispose_smoke_app_database

        def dispose(app: Any) -> None:
            runtime = app.state.knowledge_runtime
            original_stop = runtime.stop

            def stop(*, timeout: float | None) -> bool:
                archive.event("injected-knowledge-stop", timeout=timeout)
                if timeout is None:
                    import threading

                    threading.Event().wait()
                return False

            runtime.stop = stop
            original_dispose(app)
            runtime.stop = original_stop

        smoke._dispose_smoke_app_database = dispose
    isolation.fence("https://api.deepseek.com/v1")
    native, error_type = 1, None
    try:
        if fault == "diagnostic_secret":
            sys.stdout.write(key[:9])
            sys.stdout.write(key[9:])
            os.write(2, key[:7].encode())
            os.write(2, key[7:].encode())
            raise RuntimeError("Offline split diagnostic secret fixture")
        if fault == "storage_escape":
            import sqlite3

            sqlite3.connect(isolation.work / "outside-registered-roots.db")
        if fault == "extra_bind":
            import socket

            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
        # Typer executes the unchanged official verify callback and all seven
        # original stage operations if they succeed. No FakeChatModel is passed.
        cli.app(args=["verify", "--profile", "real-ai"], prog_name="oc", standalone_mode=False)
        native = 0
    except BaseException as error:
        error_type = type(error).__name__
        if isinstance(error, SystemExit):
            native = int(error.code or 0)
        archive.event("original-cli-error", error_type=error_type)
    finally:
        sdk.close()
        provider_http.close()
    with trace_lock:
        trace.end(complete=True)
    rows = {x["stage"]: x for x in isolation.stages}
    report = dict(
        schema_version=3,
        scope="offline actual official CLI with SDK MockTransport",
        official_entry=["oc", "verify", "--profile", "real-ai"],
        native_exit=native,
        error_type=error_type,
        planned_stages=list(STAGES),
        stages=[rows.get(name, dict(stage=name, status="not_run")) for name in STAGES],
        factory_count=isolation.factory_index,
        server_count=isolation.server_index,
        active_roots=[str(p) for p in isolation.active],
        archive=archive.summary(),
        offline_fault=fault,
        unconfigured_503_observed=any(
            row["kind"] == "unconfigured-original-503-assertion-passed" for row in archive.events
        ),
        real_provider_requests=0,
        real_judge_requests=0,
        real_seven_stage_pass=False,
        execution_ready=False,
        release_eligible=False,
    )
    once(Path(data["report"]), report)
    channel.finish(sha(canonical(report)), native_exit=native)
    return native


if __name__ == "__main__":
    raise SystemExit(main())
