"""Process-local adapters around the unchanged official CLI and product Runtime.

Only the temporary-directory factory, prebound listeners, metered SDK transport
and evidence hooks change. Product stage operations, validators and HITL stay real.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from typing import Any, Iterator, cast

import httpx

from .archive import EvidenceArchive, captured_response
from .transport import API_REQUEST, PRODUCT_CALL

STAGES = (
    "interview_preparation",
    "material_proposal",
    "opportunity_fit",
    "interview_review",
    "interview_knowledge_capture",
    "mock_interview",
    "write_smoke",
)


class Isolation:
    def __init__(self, work: Path, archive: EvidenceArchive) -> None:
        if work.resolve() == Path(tempfile.gettempdir()).resolve():
            raise ValueError("System TEMP is not an owned isolation root")
        self.work, self.archive = work.resolve(), archive
        self.work.mkdir(exist_ok=False)
        self.directories = [
            tempfile.TemporaryDirectory(prefix=prefix, dir=self.work, ignore_cleanup_errors=False)
            for prefix in ("main-", "unconfigured-")
        ]
        self.roots = tuple(Path(item.name).resolve() for item in self.directories)
        self.apps: dict[Path, Any] = {}
        self.active: set[Path] = set()
        self.listeners: list[socket.socket] = []
        for _ in range(2):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if sys.platform == "win32":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            self.listeners.append(listener)
        self.ports = {int(item.getsockname()[1]) for item in self.listeners}
        self.factory_index, self.server_index = 0, 0
        self.stages: list[dict[str, Any]] = []
        self.api_ids: set[int] = set()
        archive.event(
            "isolation-registration",
            roots=[str(p) for p in self.roots],
            ports=sorted(self.ports),
            registered_before_app=True,
        )

    def factory(self, *args: Any, **kwargs: Any) -> Any:
        expected = ("offerpilot-real-ai-verify-", "offerpilot-smoke-unconfigured-")
        index = self.factory_index
        if index >= 2 or kwargs.get("prefix") != expected[index]:
            raise PermissionError("Unexpected smoke temporary directory")
        self.factory_index += 1
        owner = self

        class Directory:
            def __enter__(self) -> str:
                return str(owner.roots[index])

            def __exit__(self, *exc: Any) -> None:
                root = owner.roots[index]
                if root in owner.active:
                    owner.archive.event(
                        "directory-retained",
                        root=str(root),
                        reason="server-or-worker-not-proven-exited",
                    )
                    # Detach stdlib finalizer too: it must not delete a live database.
                    getattr(owner.directories[index], "_finalizer").detach()
                    return
                owner.snapshot(root, "before-temporary-cleanup", closed=True)
                owner.directories[index].cleanup()
                owner.archive.event("temporary-cleanup", root=str(root), removed=not root.exists())

        return Directory()

    def snapshot(self, root: Path, reason: str, *, closed: bool) -> None:
        for path in sorted(root.glob("*.db*")):
            if path.is_file():
                capture = self.archive.body(
                    "database-file-snapshot",
                    links=dict(
                        root=str(root),
                        file=path.name,
                        reason=reason,
                        consistency="closed-workers" if closed else "live-files-unknown",
                        body_layer="database file bytes",
                    ),
                )
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(65536), b""):
                        capture.feed(chunk)
                capture.end(complete=True)

    def register_app(self, original: Any, **kwargs: Any) -> Any:
        from offerpilot.config import load_config

        root = Path(kwargs["data_dir"]).resolve()
        if root not in self.roots or root in self.apps:
            raise PermissionError("Unexpected app database root")
        config = load_config(root)
        if root == self.roots[1]:
            if any(p.api_key for p in config.provider_profiles()):
                raise PermissionError("Unconfigured service unexpectedly has credentials")
        elif (
            config.chat_auto_approve_writes
            or config.fallback_provider_ids
            or config.fallback_provider_id
            or config.multimodal_provider_id
            or len(config.providers) != 1
            or config.active_provider().supports_json_schema
        ):
            raise PermissionError("Bounded single-provider/HITL configuration differs")
        app = original(**kwargs)
        owner = self

        class Association:
            def __init__(self, app: Any) -> None:
                self.app = app

            async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
                request_id = None
                if scope["type"] == "http":
                    raw = dict(scope.get("headers", ())).get(
                        b"x-offerpilot-verification-request", b""
                    )
                    if raw.isdigit() and int(raw) in owner.api_ids:
                        request_id = int(raw)
                token = API_REQUEST.set(request_id)
                try:
                    await self.app(scope, receive, send)
                finally:
                    API_REQUEST.reset(token)

        app.add_middleware(Association)
        self.apps[root] = app
        self.archive.event(
            "app-created",
            root=str(root),
            unconfigured=root == self.roots[1],
            chat_auto_approve_writes=False,
        )
        return app

    @contextmanager
    def running_server(self, app: Any) -> Iterator[str]:
        import uvicorn
        from offerpilot import smoke

        expected_root = self.roots[1] if self.server_index == 0 else self.roots[0]
        if self.server_index >= 2 or self.apps.get(expected_root) is not app:
            raise PermissionError("App/listener consumption order differs")
        listener = self.listeners[self.server_index]
        self.server_index += 1
        port = int(listener.getsockname()[1])
        config = uvicorn.Config(
            cast(Any, app), host="127.0.0.1", port=port, log_level="warning", access_log=False
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        self.active.add(expected_root)
        thread.start()
        base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 15
        try:
            while time.monotonic() < deadline:
                try:
                    response = httpx.get(base_url + "/api/health", timeout=1.0)
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("http smoke server did not become ready")
            self.archive.event(
                "server-ready", root=str(expected_root), port=port, startup_deadline_seconds=15
            )
            yield base_url
        finally:
            # Original shutdown 30, then original Knowledge stop(10)/stop(None).
            # An outer owned watchdog may terminate the process; this is not an
            # excuse to delete a still-active directory or close its allocation.
            smoke._stop_smoke_server(server, thread, app)
            self.active.discard(expected_root)
            self.archive.event("server-and-workers-exited", root=str(expected_root), port=port)

    def install(self, *, provider_client: httpx.Client) -> None:
        from offerpilot import smoke
        from offerpilot.ai.client import ConfiguredAIClient

        original_app = getattr(smoke, "create_app")
        from .context import install_runtime_association

        self.restore_runtime_context = install_runtime_association(self.archive)
        setattr(smoke, "create_app", lambda **kw: self.register_app(original_app, **kw))
        setattr(smoke, "_running_server", self.running_server)
        setattr(smoke, "tempfile", SimpleNamespace(TemporaryDirectory=self.factory))
        original_cleanup = smoke._cleanup_real_ai_smoke_records

        def cleanup(root: Path, *args: Any, **kwargs: Any) -> Any:
            self.snapshot(root, "before-original-business-cleanup", closed=False)
            return original_cleanup(root, *args, **kwargs)

        setattr(smoke, "_cleanup_real_ai_smoke_records", cleanup)
        original_stage = smoke._run_named_real_ai_smoke_stage
        original_unconfigured = smoke._run_unconfigured_chat_smoke

        def unconfigured(*args: Any, **kwargs: Any) -> Any:
            result = original_unconfigured(*args, **kwargs)
            self.archive.event("unconfigured-original-503-assertion-passed")
            return result

        setattr(smoke, "_run_unconfigured_chat_smoke", unconfigured)

        def stage(name: str, operation: Any) -> None:
            if len(self.stages) >= len(STAGES) or name != STAGES[len(self.stages)]:
                raise ValueError("Official stage duplicated or unknown")
            self.archive.event("stage-start", stage=name)
            try:
                original_stage(name, operation)
            except BaseException as error:
                self.stages.append(
                    dict(stage=name, status="failed", error_type=type(error).__name__)
                )
                self.archive.event("stage-failed", stage=name, error_type=type(error).__name__)
                raise
            else:
                self.stages.append(dict(stage=name, status="passed"))
                self.archive.event("stage-passed", stage=name)

        setattr(smoke, "_run_named_real_ai_smoke_stage", stage)
        for method in ("_complete_with_provider", "_stream_with_provider"):
            original = getattr(ConfiguredAIClient, method)

            def wrapped(model: Any, *args: Any, _original: Any = original, **kwargs: Any) -> Any:
                import secrets

                identity = "product-" + secrets.token_hex(16)
                token = PRODUCT_CALL.set(identity)
                self.archive.event(
                    "product-call-start",
                    product_call_id=identity,
                    api_request_id=API_REQUEST.get(),
                    method=_original.__name__,
                    input_messages=[asdict(m) for m in args[1]] if len(args) > 1 else None,
                )
                try:
                    result = _original(model, *args, **kwargs)
                    self.archive.event(
                        "product-assistant-result",
                        product_call_id=identity,
                        api_request_id=API_REQUEST.get(),
                        value=asdict(result),
                    )
                    return result
                except BaseException as error:
                    self.archive.event(
                        "product-call-error",
                        product_call_id=identity,
                        error_type=type(error).__name__,
                    )
                    raise
                finally:
                    for stream in self.archive.streams:
                        if stream.links.get("product_call_id") == identity:
                            stream.close()
                    PRODUCT_CALL.reset(token)

            setattr(ConfiguredAIClient, method, wrapped)
        self._install_http(provider_client)

    def _install_http(self, provider_client: httpx.Client) -> None:
        original_init = httpx.Client.__init__

        def init(client: httpx.Client, *args: Any, **kwargs: Any) -> None:
            # Windows registry proxies also exist without proxy env vars. The
            # isolated actor must route its owned loopback directly.
            original_init(
                client, *args, **{**kwargs, "trust_env": False, "follow_redirects": False}
            )

        setattr(httpx.Client, "__init__", init)
        original = httpx.Client.send
        archive, ports = self.archive, self.ports

        def send(
            client: httpx.Client, request: httpx.Request, *args: Any, **kwargs: Any
        ) -> httpx.Response:
            if client is provider_client:
                return original(client, request, *args, **kwargs)
            if (
                request.url.scheme != "http"
                or request.url.host != "127.0.0.1"
                or request.url.port not in ports
            ):
                raise PermissionError("Only registered loopback and owned SDK clients allowed")
            request.read()  # Preserve the exact serialized body, including multipart.
            start = archive.event(
                "api-request",
                method=request.method,
                url=str(request.url),
                stage=__import__("os").environ.get("OFFERPILOT_FULL_VERIFY_ACTIVE_STAGE"),
                operation=__import__("os").environ.get("OFFERPILOT_FULL_VERIFY_OPERATION"),
            )
            links = dict(api_request_id=start["ordinal"])
            self.api_ids.add(start["ordinal"])
            request.headers["x-offerpilot-verification-request"] = str(start["ordinal"])
            body = archive.body("api-request-body", links=links)
            body.feed(request.content)
            body.end(complete=True)
            stream = kwargs.get("stream", False)
            try:
                response = original(client, request, *args, **{**kwargs, "stream": True})
                captured_response(response, archive, kind="api-response", links=links)
                if not stream:
                    try:
                        response.read()
                    finally:
                        response.close()
                return response
            except BaseException as error:
                archive.event("api-request-error", links=links, error_type=type(error).__name__)
                raise

        setattr(httpx.Client, "send", send)

    def fence(self, provider_endpoint: str) -> None:
        from scripts.evaluation_subject.core_bootstrap import fence

        # The existing fence limits network/subprocess access. Its common-root
        # SQLite permission is intersected with the exact two roots below.
        channel = SimpleNamespace(
            config={"data_dir": str(self.work)},
            attempt=lambda purpose, **data: self.archive.event(
                "isolation-observation", purpose=purpose, **data
            ),
        )
        fence(cast(Any, channel), self.ports, provider_endpoint)
        local = threading.local()
        pair = socket.socketpair

        def socketpair(*args: Any, **kwargs: Any) -> Any:
            local.pair = True
            try:
                return pair(*args, **kwargs)
            finally:
                local.pair = False

        socket.socketpair = socketpair

        def audit(event: str, args: tuple[Any, ...]) -> None:
            if event == "sqlite3.connect":
                name = str(args[0])
                if name == ":memory:":
                    return
                from urllib.parse import unquote, urlsplit

                if name.startswith("file:"):
                    name = unquote(urlsplit(name).path)
                    if sys.platform == "win32" and name.startswith("/") and name[2:4] == ":/":
                        name = name[1:]
                path = Path(name).resolve()
                if not any(path.is_relative_to(root) for root in self.roots):
                    raise PermissionError("SQLite is limited to the exact two registered roots")
            if event == "socket.bind" and not getattr(local, "pair", False):
                raise PermissionError("Additional listener binding refused")

        sys.addaudithook(audit)
