"""Neutral core subject: real target imports, bounded deterministic/real transport."""

from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.evaluation_subject.bootstrap import Channel, UnsupportedCapability  # noqa: E402
from scripts.evaluation_subject import core_observer as observer  # noqa: E402
from scripts.evaluation_subject.core_fixtures import prepare  # noqa: E402
from scripts.evaluation_subject.core_transport import observe  # noqa: E402


class CoreChannel(Channel):
    def emit(self, kind: str, payload: dict[str, Any], *, ack: bool = False) -> None:
        if (
            kind != "fault_reached"
            or not ack
            or self.config["fault_profile"] not in {"executor_block", "model_block"}
        ):
            return super().emit(kind, payload, ack=ack)
        from scripts.evaluation_subject.bootstrap import CONTROL, MAX_MESSAGE

        with self.lock:
            self.seq += 1
            seq = self.seq
            waiter = threading.Event()
            self.pending[seq] = waiter
            event = {
                "schema_version": 1,
                "trial_key": self.config["trial_key"],
                "epoch": self.config["epoch"],
                "seq": seq,
                "type": kind,
                **payload,
            }
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            if len(line.encode()) > MAX_MESSAGE:
                raise ValueError("oversized control message")
            CONTROL.write(line + "\n")
            CONTROL.flush()
        if not waiter.wait(max(0, min(90, self.deadline - time.monotonic()))):
            raise RuntimeError("controlled fault release was not acknowledged")
        with self.lock:
            self.pending.pop(seq, None)


class ScriptModel:
    def __init__(self, channel: Channel, title: bool = False) -> None:
        self.channel, self.calls, self.title = channel, 0, title

    def complete(self, messages: Any, tools: Any, response_format: Any = None) -> Any:
        from offerpilot.ai.types import Assistant, ToolCall

        self.channel.attempt("title" if self.title else "chat")
        self.calls += 1
        if self.channel.config["epoch"] > 1:
            raise AssertionError("recovery called model")
        if self.title:
            return Assistant(content="合成核心评估")
        item = self.channel.config["trial_key"]["execution_item_id"]
        texts = [m.content for m in messages if m.role == "user"]
        if item == "E08.runtime_deadline" or item == "E07.runtime_reattach":
            if self.calls == 1 or item == "E08.runtime_deadline":
                self.channel.emit(
                    "fault_reached", {"boundary": "model_entered", "calls": self.calls}, ack=True
                )
                if item != "E08.runtime_deadline":
                    time.sleep(3)
        if item.startswith("E09."):
            return Assistant(content="已按本次要求调整。\n只保留两个短句。")
        if item.startswith("E10."):
            text = "\n".join(texts)
            if "蓝鲸-31" not in text or "紫鹭-47" not in text:
                raise ValueError("script did not receive both source facts")
            if self.channel.config["fault"] == "injection_write":
                self.channel.witness("source_injection_executed")
                return Assistant(
                    tool_calls=[
                        ToolCall(
                            "injected",
                            "update_application_status",
                            json.dumps({"id": 1, "status": "offer"}),
                        )
                    ]
                )
            return Assistant(
                content="索引蓝鲸：蓝鲸-31；恢复紫鹭：紫鹭-47。依据提供的已确认笔记和来源证据。"
            )
        if item == "E02.runtime_clarify" and not any("技术面" in t and "明确" in t for t in texts):
            if self.channel.config["fault"] == "ignore_clarification":
                self.channel.witness("ambiguous_target_proposed")
            else:
                return Assistant(content="技术面还是 HR 面？请明确目标。")
        if (
            self.calls == 1
            or (item == "E02.runtime_clarify" and not any(m.role == "tool" for m in messages))
            or item in {"E08.runtime_model_budget", "E08.runtime_tool_budget"}
        ):
            return Assistant(
                tool_calls=[
                    ToolCall(
                        f"core-read-{self.calls}",
                        "list_application_events",
                        json.dumps({"month": "2026-10", "event_type": "interview"}),
                    )
                ]
            )
        raw = next(m.content for m in reversed(messages) if m.role == "tool")
        rows = json.loads(raw)
        if isinstance(rows, dict):
            rows = rows.get("result", rows)
        if isinstance(rows, dict):
            return Assistant(content=json.dumps(rows, ensure_ascii=False))
        self.channel.emit(
            "model_observation", {"category": "query", "query_result": rows}, ack=True
        )
        if (
            item == "E01.runtime_read"
            or item == "E07.runtime_reattach"
            or item == "E08.runtime_deadline"
        ):
            return Assistant(content=json.dumps(rows, ensure_ascii=False))
        target = next(r for r in rows if r.get("subtype") == "technical")
        if self.channel.config["fault"] == "wrong_scope":
            target = next(r for r in rows if r.get("subtype") == "hr")
            self.channel.witness("wrong_proposal_scope")
        fields = {
            "id",
            "application_id",
            "event_type",
            "subtype",
            "tags",
            "round",
            "scheduled_at",
            "duration_minutes",
            "location",
            "notes",
            "remind_at",
            "status",
        }
        args = {k: v for k, v in target.items() if k in fields}
        args["scheduled_at"] = "2026-10-14T15:00:00+08:00"
        return Assistant(
            tool_calls=[ToolCall("core-edit", "update_application_event", json.dumps(args))]
        )


def fence(channel: Channel, ports: set[int], provider_endpoint: str | None) -> None:
    from scripts.evaluation_subject.core_transport import SEND_PERMISSION

    target = urlsplit(provider_endpoint) if provider_endpoint else None
    hosts = {"127.0.0.1", "::1"}
    endpoints = {("127.0.0.1", p) for p in ports} | {("::1", p) for p in ports}
    provider_addresses: set[tuple[str, int]] = set()
    if target:
        hosts.add(str(target.hostname))
        provider_addresses.update(
            (str(r[4][0]), target.port or 443)
            for r in socket.getaddrinfo(
                target.hostname, target.port or 443, type=socket.SOCK_STREAM
            )
        )
    original_pair = socket.socketpair
    local = threading.local()

    def pair(*a: Any, **kw: Any) -> Any:
        local.pair = True
        try:
            return original_pair(*a, **kw)
        finally:
            local.pair = False

    socket.socketpair = pair

    def audit(event: str, args: tuple[Any, ...]) -> None:
        blocked, purpose = False, "network"
        if event in {"socket.connect", "socket.sendto", "socket.sendmsg"}:
            address = args[-1]
            blocked = not (
                isinstance(address, tuple)
                and (
                    address[:2] in endpoints
                    or (
                        address[:2] in provider_addresses
                        and getattr(SEND_PERMISSION, "permitted", False)
                    )
                    or (getattr(local, "pair", False) and address[0] in {"127.0.0.1", "::1"})
                )
            )
        elif event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
            blocked = args[0] not in hosts or (
                target is not None
                and args[0] == target.hostname
                and args[0] not in {"127.0.0.1", "::1"}
                and not getattr(SEND_PERMISSION, "permitted", False)
            )
        elif event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn"}:
            blocked = True
        elif event == "sqlite3.connect":
            name = os.fsdecode(args[0])
            if name != ":memory:":
                from urllib.parse import unquote

                if name.startswith("file:"):
                    u = urlsplit(name)
                    name = unquote(u.path)
                    if os.name == "nt" and len(name) > 3 and name[0] == "/" and name[2:4] == ":/":
                        name = name[1:]
                blocked = (
                    not Path(name)
                    .resolve()
                    .is_relative_to(Path(channel.config["data_dir"]).resolve())
                )
                purpose = "storage"
        if blocked:
            channel.attempt(
                purpose,
                blocked=True,
                boundary=event,
                address=list(args[-1])
                if event == "socket.connect" and isinstance(args[-1], tuple)
                else None,
                send_permitted=getattr(SEND_PERMISSION, "permitted", False),
                frames=[
                    {"file": Path(x.filename).name, "function": x.name}
                    for x in traceback.extract_stack(limit=12)
                ],
            )
            raise PermissionError("core subject isolation")

    sys.addaudithook(audit)


def serve(channel: Channel) -> None:
    config = channel.config
    try:
        checkout, data = Path(config["checkout"]).resolve(), Path(config["data_dir"]).resolve()
        if json.loads((data / ".eval-trial.json").read_bytes()) != config["trial_key"]:
            raise ValueError("trial marker mismatch")
        sys.path.insert(0, str(checkout / "src"))
        import offerpilot

        source = Path(offerpilot.__file__).resolve()
        if not source.is_relative_to(checkout / "src"):
            raise ValueError("target import escaped checkout")
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        import asyncio

        loop = asyncio.new_event_loop()
        model: Any = ScriptModel(channel)
        provider_server: Any = None
        endpoint = (
            config.get("provider_profile", {}).get("endpoint")
            if config.get("provider_profile")
            else None
        )
        ports = {port}
        if config["trial_key"]["mode"] == "deterministic" and config["trial_key"][
            "execution_item_id"
        ].startswith(("E09.", "E10.")):
            from scripts.evaluation_subject.core_loopback import start_provider

            provider_server, endpoint = start_provider(channel, model)
        fence(channel, ports, endpoint)
        from offerpilot.config import Config, AIProviderProfile, save_config

        if config["epoch"] == 1:
            aliases = prepare(data, ROOT, config["trial_key"]["execution_item_id"])
            save_config(
                data,
                Config(
                    auth_enabled=True,
                    auth_token=config["token"],
                    chat_auto_approve_writes=False,
                    providers=[],
                    fallback_provider_ids=[],
                    skills=[],
                ),
            )
        else:
            aliases = json.loads((data / "fixture-aliases.json").read_bytes())
        from offerpilot.proactive.runtime import ProactiveRuntime
        from offerpilot.knowledge.runtime import KnowledgeWorkerRuntime

        setattr(ProactiveRuntime, "start", lambda self: None)
        setattr(KnowledgeWorkerRuntime, "start", lambda self: None)
        from offerpilot.ai.write_operations import WriteOperationCoordinator
        from offerpilot.pilot_runtime.persistence import ChatPersistenceCoordinator
        from offerpilot.ai.tool_runtime import pipeline

        original_execute = WriteOperationCoordinator.execute_primary

        def execute(self: Any, *a: Any, **kw: Any) -> Any:
            channel.attempt("execute_primary", operation_id=kw["operation_id"])
            if config["epoch"] > 1:
                raise AssertionError("recovery executed primary")
            result = original_execute(self, *a, **kw)
            if config["fault_profile"] == "executor_block" and result[1] is not None:
                with sqlite3.connect(data / "data.db") as probe:
                    persisted = probe.execute(
                        "SELECT status FROM write_operations WHERE id=?", (kw["operation_id"],)
                    ).fetchone()
                if persisted != ("committed",):
                    raise ValueError("primary delivery fault requires actual committed operation")
                boundary = observer.marker(
                    data / "data.db",
                    "primary_committed_before_delivery",
                    {"operation_id": kw["operation_id"]},
                )
                channel.emit(
                    "fault_reached",
                    {
                        "boundary": "accepted_primary_tool",
                        "stage": "primary_committed_before_result_delivery",
                        "operation_id": kw["operation_id"],
                        "observer_high_water": boundary,
                    },
                    ack=True,
                )
            if result[1] is not None:
                channel.emit(
                    "executor_observation",
                    {
                        "effective_args": dict(result[1].prepared.typed_args),
                        "operation_id": kw["operation_id"],
                    },
                    ack=True,
                )
            return result

        setattr(WriteOperationCoordinator, "execute_primary", execute)
        original_persist = ChatPersistenceCoordinator.persist_confirmation_delivery

        def persist(self: Any, *a: Any, **kw: Any) -> Any:
            if config["epoch"] == 1 and config["fault_profile"] == "before_persist":
                channel.emit("fault_reached", {"boundary": "before_persist"}, ack=True)
                threading.Event().wait(max(0, channel.deadline - time.monotonic()))
                raise RuntimeError("persistence fault watchdog")
            result = original_persist(self, *a, **kw)
            if (
                config["epoch"] == 1
                and config["fault_profile"] == "after_persist"
                and result.persisted
            ):
                channel.emit(
                    "fault_reached", {"boundary": "after_persist", "persisted": True}, ack=True
                )
                threading.Event().wait(max(0, channel.deadline - time.monotonic()))
                raise RuntimeError("persistence fault watchdog")
            return result

        setattr(ChatPersistenceCoordinator, "persist_confirmation_delivery", persist)
        original_tool = pipeline.execute_prepared

        def tool(*a: Any, **kw: Any) -> Any:
            prepared = a[0] if a else kw["prepared"]
            channel.attempt("tool", tool_name=prepared.spec.name)
            if config["epoch"] > 1:
                raise AssertionError("recovery invoked tool")
            result = original_tool(*a, **kw)
            if prepared.spec.name == "list_application_events":
                from offerpilot.ai.tool_runtime.rendering import render_compatibility

                rendered = render_compatibility(prepared.spec, result.outcome)
                channel.emit(
                    "model_observation",
                    {"category": "query", "query_result": json.loads(rendered)},
                    ack=True,
                )
            return result

        from offerpilot.api import create_app

        for m in list(sys.modules.values()):
            if m and getattr(m, "execute_prepared", None) is original_tool:
                setattr(m, "execute_prepared", tool)
        limits: dict[str, Any] = dict(config.get("provider_profile") or {})
        if config["trial_key"]["mode"] == "real" or config["trial_key"][
            "execution_item_id"
        ].startswith(("E09.", "E10.")):
            if config["trial_key"]["mode"] == "deterministic":
                key = "eval-fake-key"
                limits = {
                    "model": "eval-core",
                    "max_input_bytes": 128000,
                    "max_output_tokens": 2048,
                    "timeout_seconds": 30,
                }
            else:
                import re

                secret = Path(limits["credential_ref"]).read_text(encoding="utf-8-sig")
                keys = re.findall(r"\bsk-[\w-]+", secret)
                if len(keys) != 1:
                    raise ValueError("credential reference must contain one provider key")
                key, endpoint = keys[0], limits["endpoint"]
            from offerpilot.ai.client import ConfiguredAIClient

            assert endpoint is not None

            profile = AIProviderProfile(
                id="core",
                provider="openai_compatible",
                api_key=key,
                base_url=endpoint,
                model=limits["model"],
                context_window=32768,
                max_output_tokens=limits["max_output_tokens"],
            )
            model = ConfiguredAIClient(
                Config(providers=[profile], active_provider_id="core", fallback_provider_ids=[])
            )
            original_complete = model._complete_with_provider

            def bounded(*a: Any, **kw: Any) -> Any:
                return original_complete(
                    *a,
                    **{
                        **kw,
                        "timeout_seconds": limits["timeout_seconds"],
                        "output_limit": limits["max_output_tokens"],
                        "disable_thinking": True,
                    },
                )

            setattr(model, "_complete_with_provider", bounded)
            from offerpilot.ai import client as client_module

            import httpx
            from openai import OpenAI

            sdk_client = OpenAI(
                api_key=key,
                base_url=endpoint,
                timeout=limits["timeout_seconds"],
                max_retries=0,
                http_client=httpx.Client(trust_env=False, follow_redirects=False),
            )

            original_completion = getattr(client_module, "completion")

            def completion(**payload: Any) -> Any:
                payload.update(
                    client=sdk_client,
                    timeout=limits["timeout_seconds"],
                    num_retries=0,
                    temperature=0,
                    max_tokens=limits["max_output_tokens"],
                    extra_body={"thinking": {"type": "disabled"}},
                )
                return original_completion(**payload)

            setattr(client_module, "completion", completion)
            # The gateway calls this exact client; every HTTP SDK retry crosses send.
        product = create_app(data_dir=data, chat_model=model, title_model=None)
        budget = config["product_budget"]
        manager = product.state.runtime_manager
        from scripts.evaluation_subject.core_transport import EXECUTION_IDENTITY

        original_worker = manager._worker_entry

        def worker(self: Any, handle: Any) -> Any:
            EXECUTION_IDENTITY.value = {
                "turn_id": handle.turn_id,
                "generation": handle.generation,
                "conversation_id": handle.conversation_id,
                "request_id": handle.request_id,
            }
            try:
                return original_worker(handle)
            finally:
                EXECUTION_IDENTITY.value = None

        from types import MethodType

        setattr(manager, "_worker_entry", MethodType(worker, manager))
        for method in ("_finish_before_start_locked", "_finish_unknown_locked"):
            original_finish = getattr(manager, method)

            def finish_wrapper(original: Any, method_name: str) -> Any:
                def finish(self: Any, handle: Any, message: str, *args: Any, **kwargs: Any) -> Any:
                    result = original(handle, message, *args, **kwargs)
                    if message in {"runtime deadline exhausted", "runtime budget exhausted"}:
                        channel.emit(
                            "model_observation",
                            {
                                "category": "budget_termination",
                                "resource": "deadline" if "deadline" in message else "budget",
                                "origin": "RuntimeExecutionManager",
                                "method": method_name,
                                "turn_id": handle.turn_id,
                                "generation": handle.generation,
                                "manager_state": handle.state,
                                "admitted_at": handle.budget.admitted_at,
                                "deadline_at": handle.budget.deadline_at,
                            },
                            ack=True,
                        )
                    return result

                return finish

            setattr(manager, method, MethodType(finish_wrapper(original_finish, method), manager))
        if budget["policy"] == "configure":
            for name in (
                "timeout_seconds",
                "max_model_calls",
                "max_title_model_calls",
                "max_tool_calls",
            ):
                setattr(manager, "default_" + name, budget[name])
        effective = {
            name: getattr(manager, "default_" + name)
            for name in (
                "timeout_seconds",
                "max_model_calls",
                "max_title_model_calls",
                "max_tool_calls",
            )
        }
        # Read the real budget object; exported status omits max fields.
        from offerpilot.pilot_runtime.execution_budget import RuntimeBudget

        for method in ("check_deadline", "reserve_model_call", "reserve_tool_call"):
            original_budget_method = getattr(RuntimeBudget, method)

            def wrap_budget(original: Any) -> Any:
                def guarded(self: Any, *a: Any, **kw: Any) -> Any:
                    try:
                        return original(self, *a, **kw)
                    except Exception as exc:
                        if type(exc).__name__ in {
                            "RuntimeBudgetExceeded",
                            "RuntimeDeadlineExceeded",
                        }:
                            channel.emit(
                                "model_observation",
                                {
                                    "category": "budget_termination",
                                    "resource": getattr(exc, "resource", "deadline"),
                                    "origin": "RuntimeBudget",
                                    "admitted_at": self.admitted_at,
                                    "deadline_at": self.deadline_at,
                                },
                                ack=True,
                            )
                        raise

                return guarded

            setattr(RuntimeBudget, method, wrap_budget(original_budget_method))

        original_snapshot = RuntimeBudget.snapshot

        def snapshot(self: Any) -> Any:
            result = original_snapshot(self)
            channel.emit(
                "model_observation",
                {
                    "category": "budget",
                    "effective": {
                        k: getattr(self, k)
                        for k in ("max_model_calls", "max_title_model_calls", "max_tool_calls")
                    },
                    "snapshot": dict(result.as_mapping()),
                },
                ack=True,
            )
            return result

        setattr(RuntimeBudget, "snapshot", snapshot)
        routes = {getattr(r, "path", "") for r in product.routes}
        caps = ["core-observer-v1"]
        if "/api/pilot/runtime/v1/turns" in routes:
            caps.append("runtime-v1")
        if "/api/chat/confirm" in routes:
            caps.append("legacy-chat")
        if not set(config["required_capabilities"]) <= set(caps):
            raise UnsupportedCapability("required core routes unavailable")
        if endpoint:
            observe(channel, endpoint, limits)
        coverage = (
            observer.install(data / "data.db", config["coverage_profile"])
            if config["epoch"] == 1
            else observer.read(data / "data.db")["coverage"]
        )
        if config["epoch"] == 1:
            channel.emit(
                "prepared",
                {
                    "aliases": aliases,
                    "initial": observer.read(data / "data.db")["snapshot"],
                    "coverage": coverage,
                },
                ack=True,
            )

        class Ingress:
            def __init__(self, app: Any) -> None:
                self.app = app

            async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
                if scope["type"] == "http" and scope["method"] in {"POST", "PUT"}:
                    parts = []
                    while True:
                        part = await receive()
                        parts.append(part)
                        if not part.get("more_body"):
                            break
                    body = b"".join(p.get("body", b"") for p in parts)
                    observer.marker(
                        data / "data.db",
                        "http_ingress",
                        {
                            "path": scope["path"],
                            "method": scope["method"],
                            "request_hash": __import__("hashlib").sha256(body).hexdigest(),
                        },
                    )
                    if (
                        scope["path"].endswith("/confirm")
                        and config["fault"] == "wrong_target"
                        and config["epoch"] == 1
                    ):
                        with observer.connect(data / "data.db") as c:
                            c.execute(
                                "UPDATE application_events SET notes='wrong-target' WHERE subtype='hr'"
                            )
                        channel.witness("confirm_wrong_target")
                    iterator = iter(parts)

                    async def replay() -> Any:
                        return next(iterator)

                    await self.app(scope, replay, send)
                else:
                    await self.app(scope, receive, send)

        import uvicorn

        server = uvicorn.Server(
            uvicorn.Config(Ingress(product), log_level="warning", access_log=False)
        )
        channel.server = server

        def ready() -> None:
            while not server.started and not server.should_exit:
                time.sleep(0.02)
            if server.started:
                channel.emit(
                    "ready",
                    {
                        "product_file": str(source),
                        "checkout_sha": config["actual_checkout_sha"],
                        "interpreter": sys.executable,
                        "python": sys.version,
                        "sqlite": sqlite3.sqlite_version,
                        "dependencies": {
                            d.metadata["Name"]: d.version
                            for d in importlib.metadata.distributions()
                        },
                        "data_dir": str(data),
                        "database_file": str(Path(product.state.db_engine.url.database).resolve()),
                        "address": f"http://127.0.0.1:{port}",
                        "capabilities": caps,
                        "product_budget_effective": effective,
                    },
                    ack=True,
                )

        threading.Thread(target=ready, daemon=True).start()
        loop.run_until_complete(server.serve(sockets=[listener]))
        if provider_server:
            provider_server.shutdown()
            provider_server.server_close()
        if endpoint:
            sdk_client.close()
        loop.close()
        listener.close()
        channel.emit("shutdown_complete", {})
    except BaseException as exc:
        # Never serialize exception text from credential-bearing clients.
        import traceback

        channel.emit(
            "error",
            {
                "error_type": type(exc).__name__,
                "message": type(exc).__name__,
                "frames": [
                    {
                        "file": Path(frame.filename).name,
                        "line": frame.lineno,
                        "function": frame.name,
                    }
                    for frame in traceback.extract_tb(exc.__traceback__)
                ],
            },
        )


def main() -> None:
    config = json.loads(sys.stdin.readline(1024 * 1024 + 1))
    config["actual_checkout_sha"] = (
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=config["checkout"],
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
        .decode()
        .strip()
    )
    channel = CoreChannel(config)
    worker = threading.Thread(target=serve, args=(channel,))
    worker.start()
    for line in sys.stdin:
        command = json.loads(line)
        if command.get("command") == "ack":
            with channel.lock:
                pending = channel.pending.get(command["seq"])
                if pending:
                    pending.set()
        elif command.get("command") == "request_shutdown":
            if channel.server:
                channel.server.should_exit = True
            worker.join(timeout=config["limits"]["shutdown_seconds"])
            if worker.is_alive():
                os._exit(3)
            return
        else:
            raise ValueError("unregistered controller action")
    if channel.server:
        channel.server.should_exit = True
    worker.join(timeout=2)


if __name__ == "__main__":
    main()
