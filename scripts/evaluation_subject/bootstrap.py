"""Trusted deterministic subject bridge. stdout is an ACKed JSONL control channel."""
from __future__ import annotations

import importlib.util
from contextlib import ExitStack
import json
import os
from pathlib import Path
import platform
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import unquote, urlsplit

CONTROL = sys.stdout
sys.stdout = sys.stderr
BRIDGE_ROOT = Path(__file__).resolve().parents[2]
MAX_MESSAGE = 1024 * 1024


class UnsupportedCapability(RuntimeError):
    pass


class SubjectStopping(RuntimeError):
    """Control cancellation is never an acknowledgement or business success."""


class PendingAck:
    def __init__(self) -> None:
        self.wakeup = threading.Event()
        self.acknowledged = False


def load_observer() -> Any:
    path = BRIDGE_ROOT / "src/offerpilot/evaluation/evidence/observer.py"
    spec = importlib.util.spec_from_file_location("fixed_evaluation_observer", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Channel:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.seq = 0
        self.lock = threading.RLock()
        self.pending: dict[int, PendingAck] = {}
        self.closed = False
        self._server: Any = None
        self.shutdown_requested = False
        self.shutdown_deadline: float | None = None
        self.deadline = time.monotonic() + config["limits"]["wall_seconds"]

    @property
    def server(self) -> Any:
        with self.lock:
            return self._server

    @server.setter
    def server(self, server: Any) -> None:
        with self.lock:
            self._server = server
            if server is not None and self.shutdown_requested:
                server.should_exit = True

    def bind_server(self, server: Any) -> bool:
        with self.lock:
            self.server = server
            return not self.shutdown_requested

    def request_shutdown(self) -> None:
        with self.lock:
            if not self.shutdown_requested:
                self.shutdown_requested = True
                self.shutdown_deadline = min(
                    self.deadline, time.monotonic() + self.config["limits"]["shutdown_seconds"])
            if self._server is not None:
                self._server.should_exit = True
            for pending in self.pending.values():
                pending.wakeup.set()

    def acknowledge(self, seq: int) -> None:
        with self.lock:
            pending = self.pending.get(seq)
            if pending is not None and not self.shutdown_requested:
                pending.acknowledged = True
                pending.wakeup.set()

    def emit(self, kind: str, payload: dict[str, Any], *, ack: bool = False) -> None:
        self._emit_sequence(kind, payload, ack=ack)

    def _emit_sequence(self, kind: str, payload: dict[str, Any], *, ack: bool = False,
                       ack_seconds: int = 30) -> int:
        with self.lock:
            if self.shutdown_requested and kind not in {"error", "shutdown_complete"}:
                raise SubjectStopping("subject shutdown requested")
            self.seq += 1
            seq = self.seq
            waiter = PendingAck()
            event = {"schema_version": 1, "trial_key": self.config["trial_key"],
                     "epoch": self.config["epoch"], "seq": seq, "type": kind, **payload}
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            if len(line.encode()) > MAX_MESSAGE:
                raise ValueError("oversized control message")
            if ack:
                self.pending[seq] = waiter
            try:
                CONTROL.write(line + "\n")
                CONTROL.flush()
            except BaseException:
                self.pending.pop(seq, None)
                raise
        if ack:
            try:
                received = waiter.wakeup.wait(max(0, min(ack_seconds, self.deadline - time.monotonic())))
                with self.lock:
                    if self.shutdown_requested:
                        raise SubjectStopping("subject shutdown requested")
                    if not received or not waiter.acknowledged:
                        raise RuntimeError("control ACK missing; call was blocked")
            finally:
                with self.lock:
                    self.pending.pop(seq, None)
        return seq

    def attempt(self, purpose: str, **metadata: Any) -> None:
        self.emit("call_attempt", {"purpose": purpose, **metadata}, ack=True)

    def witness(self, boundary: str, *, durable: bool = False) -> None:
        payload = {"fault_id": self.config["fault"], "boundary": boundary,
                   "trial_key": self.config["trial_key"], "epoch": self.config["epoch"]}
        if durable:
            load_observer().marker(Path(self.config["data_dir"]) / "data.db", "negative_control_reached", payload)
        else:
            self.emit("negative_control_reached", payload, ack=True)


def run_ready(function: Any) -> None:
    try:
        function()
    except SubjectStopping:
        pass


def finish_subject(channel: Channel, resources: ExitStack,
                   ready_thread: threading.Thread | None) -> None:
    failures: list[BaseException] = []
    try:
        channel.request_shutdown()
        if ready_thread is not None:
            assert channel.shutdown_deadline is not None
            ready_thread.join(timeout=max(0, channel.shutdown_deadline - time.monotonic()))
            if ready_thread.is_alive():
                raise RuntimeError("ready helper did not stop")
    except BaseException as exc:
        failures.append(exc)
    try:
        resources.close()
    except BaseException as exc:
        failures.append(exc)
    if failures:
        if len(failures) > 1:
            raise failures[0] from failures[1]
        raise failures[0]
    with channel.lock:
        channel.closed = True
    channel.emit("shutdown_complete", {})


def network_guard(channel: Channel, port: int) -> None:
    # Prepare full neutral metadata for SDK headers, not only uname's cache.
    # Windows version reads can fall back to a local `ver` subprocess.
    # Later product process launches remain denied by the unchanged audit hook.
    platform.platform()
    original = socket.socket.connect
    original_ex = socket.socket.connect_ex
    original_pair = socket.socketpair
    internal = threading.local()
    def pair(*args: Any, **kwargs: Any) -> Any:
        # Windows implements socketpair with a private loopback connection.
        # Permit only the stdlib's construction scope, including cleanup loops.
        internal.pair = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            internal.pair = False
    def allowed(address: Any) -> bool:
        return (isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}
                and (address[1] == port or getattr(internal, "pair", False)))
    def guarded(self: socket.socket, address: Any) -> Any:
        if not allowed(address):
            channel.attempt("network", blocked=True, host=str(address[0]) if isinstance(address, tuple) else None)
            raise PermissionError("subject network isolation")
        return original(self, address)
    def guarded_ex(self: socket.socket, address: Any) -> Any:
        if not allowed(address):
            channel.attempt("network", blocked=True)
            raise PermissionError("subject network isolation")
        return original_ex(self, address)
    socket.socket.connect = guarded  # type: ignore[assignment]
    socket.socket.connect_ex = guarded_ex  # type: ignore[assignment]
    socket.socketpair = pair
    def audit(event: str, args: tuple[Any, ...]) -> None:
        blocked = False
        purpose = "network"
        if event in {"socket.connect", "socket.sendto", "socket.sendmsg"}:
            blocked = not allowed(args[-1])
        elif event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
            blocked = args[0] not in {"127.0.0.1", "::1"}
        elif event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn"}:
            # The subject has no external-tool capability. Deny process launch
            # before native children can bypass the Python socket guard.
            blocked = True
        elif event == "sqlite3.connect":
            name = os.fsdecode(args[0])
            if name != ":memory:":
                if name.startswith("file:"):
                    parsed = urlsplit(name)
                    name = unquote(parsed.path)
                    if os.name == "nt" and len(name) > 3 and name[0] == "/" and name[2:4] == ":/":
                        name = name[1:]
                    if parsed.netloc:
                        name = "//" + parsed.netloc + name
                blocked = not Path(name).resolve().is_relative_to(Path(channel.config["data_dir"]).resolve())
                purpose = "storage"
        if blocked:
            channel.attempt(purpose, blocked=True, boundary=event)
            raise PermissionError("subject external network/tool isolation")
    sys.addaudithook(audit)


class FixedModel:
    def __init__(self, channel: Channel, *, title: bool = False) -> None:
        self.channel, self.title = channel, title
        self.calls = 0

    def complete(self, messages: Any, tools: Any, response_format: Any = None) -> Any:
        from offerpilot.ai.types import Assistant, ToolCall
        self.channel.attempt("title" if self.title else "chat")
        self.calls += 1
        if self.channel.config["epoch"] > 1:
            raise AssertionError("recovery provider sentinel called")
        if self.title:
            return Assistant(content="合成面试评估")
        item = self.channel.config["trial_key"]["execution_item_id"]
        if self.calls == 1:
            return Assistant(tool_calls=[ToolCall("eval-read", "list_application_events",
                json.dumps({"month": "2026-10", "event_type": "interview"}))])
        if self.calls != 2:
            raise AssertionError("unexpected fixed model continuation")
        raw = next(m.content for m in reversed(messages) if m.role == "tool")
        rows = json.loads(raw)
        if isinstance(rows, dict):
            rows = rows.get("result", rows)
        if not isinstance(rows, list):
            raise ValueError("fixture cannot decode actual list result")
        self.channel.emit("model_observation", {"query_result": rows}, ack=True)
        if item == "E01.runtime_read":
            if self.channel.config["fault"] == "late_write":
                def tail() -> None:
                    observer = load_observer()
                    db = Path(self.channel.config["data_dir"]) / "data.db"
                    while time.monotonic() < self.channel.deadline:
                        if any(m.get("name") == "driver_finished" for m in observer.read(db)["markers"]):
                            time.sleep(0.1)
                            with observer.connect(db) as conn:
                                conn.execute("UPDATE applications SET notes='late-tail' WHERE id=?",
                                             (rows[0]["application_id"],))
                            self.channel.witness("after_driver_finished_dml", durable=True)
                            return
                        time.sleep(0.02)
                threading.Thread(target=tail, daemon=False).start()
            return Assistant(content=json.dumps(rows, ensure_ascii=False))
        target = [r for r in rows if r.get("subtype") == "technical"]
        if len(target) != 1:
            raise ValueError("actual tool result did not identify technical interview")
        allowed = {"id", "application_id", "event_type", "subtype", "tags", "round",
                   "scheduled_at", "duration_minutes", "location", "notes", "remind_at", "status"}
        args = {k: v for k, v in target[0].items() if k in allowed}
        args["scheduled_at"] = "2026-10-14T15:00:00+08:00"
        return Assistant(tool_calls=[ToolCall("eval-edit", "update_application_event",
                                              json.dumps(args, ensure_ascii=False))])


def serve(channel: Channel) -> None:
    config = channel.config
    resources = ExitStack()
    ready_thread = None
    try:
        policy_proof = sys.modules["fixed_evaluation_source_launcher"].policy_proof
        policy_proof()
        checkout = Path(config["checkout"]).resolve(strict=True)
        data = Path(config["data_dir"]).resolve(strict=True)
        if not (data / ".eval-trial.json").is_file():
            raise ValueError("trial marker missing")
        marker_data = json.loads((data / ".eval-trial.json").read_text(encoding="utf-8"))
        if marker_data != config["trial_key"]:
            raise ValueError("trial data identity mismatch")
        sys.path.insert(0, str(checkout / "src"))
        import offerpilot
        source = Path(offerpilot.__file__).resolve()
        if not source.is_relative_to(checkout / "src"):
            raise ValueError("wrong product import source")
        observer = load_observer()
        listener = socket.socket()
        resources.callback(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        # Windows asyncio creates a private loopback socketpair. Construct it
        # before fencing product networking; the product never receives its port.
        import asyncio
        event_loop = asyncio.new_event_loop()
        resources.callback(event_loop.close)
        network_guard(channel, port)
        from offerpilot.config import Config, load_config, save_config
        prepared_data = None
        if config["epoch"] == 1:
            from datetime import datetime
            from offerpilot.db import init_database
            from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
            from offerpilot.repositories.application_events import (
                ApplicationEventCreate, ApplicationEventsRepository,
            )
            sessions = init_database(data / "data.db")
            fixture = json.loads((BRIDGE_ROOT / "evals/fixtures/synthetic-interviews-v1.json").read_text(
                encoding="utf-8"))
            aliases = {}
            for row in fixture["applications"]:
                item = ApplicationsRepository(sessions).create(ApplicationCreate(
                    company_name=row["company_name"], position_name=row["position_name"]))
                aliases[row["alias"]] = item.id
            for row in fixture["events"]:
                event = ApplicationEventsRepository(sessions).create(ApplicationEventCreate(
                    application_id=aliases[row["application"]], event_type=row["event_type"],
                    subtype=row["subtype"], scheduled_at=datetime.fromisoformat(row["scheduled_at"]),
                    duration_minutes=row["duration_minutes"]))
                aliases[row["alias"]] = event.id
            sessions.kw["bind"].dispose()
            save_config(data, Config(auth_enabled=True, auth_token=config["token"],
                                    chat_auto_approve_writes=False, providers=[],
                                    fallback_provider_ids=[], skills=[]))
            coverage = observer.install(data / "data.db")
            prepared_data = {"aliases": aliases, "coverage": coverage,
                             "initial": observer.read(data / "data.db")["snapshot"]}
        current = load_config(data)
        if current.chat_auto_approve_writes or not current.auth_enabled or current.api_key:
            raise ValueError("unsafe subject configuration")
        try:
            from offerpilot.ai.write_operations import WriteOperationCoordinator
            from offerpilot.pilot_runtime.persistence import ChatPersistenceCoordinator
        except (ImportError, AttributeError) as exc:
            raise UnsupportedCapability("target write/persistence seam unavailable") from exc
        if (not hasattr(WriteOperationCoordinator, "execute_primary")
                or not hasattr(ChatPersistenceCoordinator, "persist_confirmation_delivery")):
            raise UnsupportedCapability("target write/persistence capability unavailable")
        original_execute = WriteOperationCoordinator.execute_primary
        def counted(self: Any, *args: Any, **kwargs: Any) -> Any:
            channel.attempt("execute_primary", operation_id=kwargs["operation_id"])
            if config["epoch"] > 1:
                raise AssertionError("recovery executor sentinel called")
            outcome = original_execute(self, *args, **kwargs)
            record = outcome[1]
            if record is not None:
                channel.emit("executor_observation", {"operation_id": kwargs["operation_id"],
                    "effective_args": dict(record.prepared.typed_args)}, ack=True)
            return outcome
        WriteOperationCoordinator.execute_primary = counted  # type: ignore[method-assign]
        original_persist = ChatPersistenceCoordinator.persist_confirmation_delivery
        def persistence(self: Any, *args: Any, **kwargs: Any) -> Any:
            outcome = original_persist(self, *args, **kwargs)
            if (config["trial_key"]["execution_item_id"] == "E06.legacy_saved"
                    and config["epoch"] == 1 and outcome.persisted):
                channel.emit("fault_reached", {"boundary": "after_persist", "persisted": True}, ack=True)
                # Parent seals the premise, then kills this process. No response escapes.
                threading.Event().wait(max(0, channel.deadline - time.monotonic()))
                raise RuntimeError("fault barrier watchdog")
            return outcome
        ChatPersistenceCoordinator.persist_confirmation_delivery = persistence  # type: ignore[method-assign]
        from offerpilot.proactive.runtime import ProactiveRuntime
        from offerpilot.knowledge.runtime import KnowledgeWorkerRuntime
        ProactiveRuntime.start = lambda self: None  # type: ignore[method-assign]
        KnowledgeWorkerRuntime.start = lambda self: None  # type: ignore[method-assign]
        from offerpilot.api import create_app
        if config["fault"] == "bad_receipt":
            from offerpilot.ai.confirmation_receipt import edited_confirmation_receipt
            def wrong_receipt(*args: Any, **kwargs: Any) -> str:
                channel.witness("edited_receipt_renderer")
                return "已按用户最终确认值保存。保存结果：时间=2026-10-14T07:00:00Z。本次确认后的自动续答已结束。"
            for module in list(sys.modules.values()):
                if module and getattr(module, "edited_confirmation_receipt", None) is edited_confirmation_receipt:
                    setattr(module, "edited_confirmation_receipt", wrong_receipt)
        from offerpilot.ai.tool_runtime import pipeline
        original_tool = pipeline.execute_prepared
        def counted_tool(*args: Any, **kwargs: Any) -> Any:
            prepared = args[0] if args else kwargs["prepared"]
            channel.attempt("tool", tool_name=prepared.spec.name)
            if config["epoch"] > 1:
                raise AssertionError("recovery tool sentinel called")
            return original_tool(*args, **kwargs)
        # Only aliases of the exact function are wrapped in this test process.
        for module in list(sys.modules.values()):
            if module and getattr(module, "execute_prepared", None) is original_tool:
                setattr(module, "execute_prepared", counted_tool)
        product = create_app(data_dir=data, chat_model=FixedModel(channel),
                             title_model=FixedModel(channel, title=True))
        database_file = Path(product.state.db_engine.url.database).resolve()
        if database_file != (data / "data.db").resolve():
            raise ValueError("product opened an unexpected business database")
        paths = {getattr(route, "path", "") for route in product.routes}
        capabilities = ["after_persist"]
        if "/api/chat" in paths and "/api/chat/confirm" in paths:
            capabilities.append("legacy-chat")
        if "/api/pilot/runtime/v1/turns" in paths:
            capabilities.append("runtime-v1")
        required = "legacy-chat" if config["trial_key"]["execution_item_id"] == "E06.legacy_saved" else "runtime-v1"
        if required not in capabilities:
            raise UnsupportedCapability("target HTTP protocol unavailable")

        class Ingress:
            def __init__(self, app: Any) -> None:
                self.app = app
            async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
                if scope["type"] == "http" and scope["method"] == "POST" and scope["path"].endswith("/confirm"):
                    pieces = []
                    while True:
                        part = await receive()
                        pieces.append(part)
                        if not part.get("more_body"):
                            break
                    body = json.loads(b"".join(p.get("body", b"") for p in pieces))
                    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode()
                    import hashlib
                    if config["epoch"] == 1 and config["fault"] == "unapproved_compensation":
                        with observer.connect(data / "data.db") as c:
                            c.execute("UPDATE application_events SET scheduled_at=? WHERE id=?",
                                      ("2026-10-14 07:00:00.000000", aliases["technical_interview"]))
                        with observer.connect(data / "data.db") as c:
                            c.execute("UPDATE application_events SET scheduled_at=? WHERE id=?",
                                      ("2026-10-14 02:00:00.000000", aliases["technical_interview"]))
                        channel.witness("before_confirm_compensated")
                    observer.marker(data / "data.db", "confirmation_received", {
                        "request_hash": hashlib.sha256(encoded).hexdigest(),
                        "operation_id": body.get("operation_id"),
                        "conversation_id": body.get("conversation_id"),
                    })
                    # Negative controls change actual business rows, while preserving
                    # the original HTTP approval and product pipeline.
                    if config["epoch"] == 1 and config["fault"] != "none":
                        with observer.connect(data / "data.db") as c:
                            if config["fault"] == "wrong_target":
                                c.execute("UPDATE application_events SET scheduled_at=? WHERE subtype='hr'",
                                          ("2026-10-14 08:00:00.000000",))
                            elif config["fault"] == "duplicate_effect":
                                c.execute("UPDATE application_events SET scheduled_at=? WHERE id=?",
                                          ("2026-10-14 07:00:00.000000", aliases["technical_interview"]))
                        if config["fault"] == "wrong_payload":
                            body["edited_args"]["scheduled_at"] = "2026-10-14T17:00:00+08:00"
                            pieces = [{"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}]
                        if config["fault"] in {"wrong_target", "duplicate_effect", "wrong_payload"}:
                            channel.witness("confirmation_ingress_injected")
                    queue = iter(pieces)
                    async def replay_receive() -> Any:
                        return next(queue)
                    await self.app(scope, replay_receive, send)
                else:
                    await self.app(scope, receive, send)

        import uvicorn
        server = uvicorn.Server(uvicorn.Config(Ingress(product), host="127.0.0.1", port=port,
                                               log_level="warning", access_log=False))
        if not channel.bind_server(server):
            return
        if prepared_data:
            channel.emit("prepared", prepared_data, ack=True)
        def ready() -> None:
            while not server.started and not server.should_exit:
                time.sleep(0.02)
            if server.started:
                sha = config["actual_checkout_sha"]
                import importlib.metadata
                with product.state.db_engine.connect() as connection:
                    sqlite_settings = {name: connection.exec_driver_sql(f"PRAGMA {name}").scalar()
                        for name in ("journal_mode", "synchronous", "foreign_keys", "recursive_triggers")}
                channel.emit("ready", {"bridge_version": "p0-v1", "product_file": str(source),
                    "source_loading": policy_proof(),
                    "checkout_sha": sha, "python": sys.version, "interpreter": sys.executable,
                    "sqlite": sqlite3.sqlite_version, "sqlite_settings": sqlite_settings,
                    "dependencies": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
                    "data_dir": str(data), "address": f"http://127.0.0.1:{port}",
                    "database_file": str(database_file),
                    "capabilities": capabilities,
                    "config": {"chat_auto_approve_writes": current.chat_auto_approve_writes,
                               "auth_enabled": current.auth_enabled}}, ack=True)
        ready_thread = threading.Thread(target=run_ready, args=(ready,), daemon=True)
        ready_thread.start()
        event_loop.run_until_complete(server.serve(sockets=[listener]))
        if config["fault"] == "shutdown_timeout":
            channel.witness("shutdown_stalled", durable=True)
            time.sleep(60)
    except SubjectStopping:
        pass
    except BaseException as exc:
        channel.emit("error", {"error_type": type(exc).__name__, "message": str(exc)[:500]})
    finally:
        try:
            finish_subject(channel, resources, ready_thread)
        except BaseException as exc:
            channel.emit("error", {"error_type": type(exc).__name__, "message": str(exc)[:500]})


def main() -> None:
    first = sys.stdin.readline(MAX_MESSAGE + 1)
    config = json.loads(first)
    if config.get("command") != "prepare" or len(first.encode()) > MAX_MESSAGE:
        raise ValueError("invalid prepare command")
    config["actual_checkout_sha"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=config["checkout"], stdin=subprocess.DEVNULL,
        timeout=10).decode().strip()
    channel = Channel(config)
    worker = threading.Thread(target=serve, args=(channel,))
    worker.start()
    for line in sys.stdin:
        command = json.loads(line)
        if command.get("command") == "ack":
            channel.acknowledge(command["seq"])
        elif command.get("command") == "request_shutdown":
            channel.request_shutdown()
            worker.join(timeout=config["limits"]["shutdown_seconds"])
            if worker.is_alive():
                os._exit(3)
            return
        else:
            raise ValueError("unknown control command")
    channel.request_shutdown()
    worker.join(timeout=2)


if __name__ == "__main__":
    main()
