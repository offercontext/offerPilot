"""Private loopback backend owned by the Electron desktop process.

Run as ``python -m offerpilot.desktop`` or freeze this module as a console
executable. The parent provides absolute data/static paths, a fresh 32-byte hex
token in OFFERPILOT_DESKTOP_TOKEN, and an open stdin pipe for its lifetime.
Stdout is reserved for newline-delimited versioned protocol messages.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import socket
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from secrets import compare_digest
from typing import BinaryIO, Callable, TextIO

import uvicorn
from fastapi import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

TOKEN_ENV = "OFFERPILOT_DESKTOP_TOKEN"
TOKEN_HEADER = "X-OfferPilot-Desktop-Token"
PROTOCOL_VERSION = 1
SHUTDOWN_TIMEOUT_SECONDS = 12.0


class DesktopStartupError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DesktopSecurity:
    """Validate the private session independently of all editable app settings."""

    token: str = field(repr=False)
    port: int

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", self.token) is None:
            raise DesktopStartupError(
                "invalid_token", "Desktop token must be a fresh 32-byte lowercase hex value."
            )
        if not 1 <= self.port <= 65535:
            raise ValueError("Desktop port must be a bound TCP port.")

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def rejection(self, scope: Scope) -> tuple[int, str] | None:
        headers = scope.get("headers", [])

        def values(name: bytes) -> list[bytes]:
            return [value for key, value in headers if key.lower() == name]

        # Reject duplicates too: different HTTP layers must not disagree about
        # which Host, Origin, or credential is authoritative.
        if values(b"host") != [f"127.0.0.1:{self.port}".encode("ascii")]:
            return 403, "desktop host forbidden"
        origins = values(b"origin")
        if origins and origins != [self.origin.encode("ascii")]:
            return 403, "desktop origin forbidden"
        tokens = values(TOKEN_HEADER.lower().encode("ascii"))
        if len(tokens) != 1 or not compare_digest(tokens[0], self.token.encode("ascii")):
            return 401, "desktop session required"
        return None

    def authenticates(self, request: Request) -> bool:
        return self.rejection(request.scope) is None


class DesktopSecurityMiddleware:
    """Outer ASGI boundary protecting every route, static file, and preflight."""

    def __init__(self, app: ASGIApp, security: DesktopSecurity) -> None:
        self.app = app
        self.security = security

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] == "http":
            rejection = self.security.rejection(scope)
            if rejection is not None:
                status, message = rejection
                response = JSONResponse({"error": message}, status_code=status)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_desktop_app(
    *, data_dir: Path, static_dir: Path, security: DesktopSecurity
) -> ASGIApp:
    """Compose the existing product with a mandatory desktop security boundary."""
    # Desktop startup must be offline-capable. LiteLLM otherwise downloads its
    # price map during import, before the user has requested any provider work.
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    from offerpilot.api import create_app

    app = create_app(
        data_dir=data_dir,
        static_dir=static_dir,
        transport_authenticator=security.authenticates,
    )
    return DesktopSecurityMiddleware(app, security)


class _DesktopServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, on_ready: Callable[[], None]) -> None:
        super().__init__(config)
        self.on_ready = on_ready
        self.ready_emitted = False

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        # Uvicorn has completed ASGI lifespan startup and attached the retained
        # listening socket. Never announce readiness merely after binding a port.
        if self.started and not self.should_exit:
            self.on_ready()
            self.ready_emitted = True


class _ParentLifetime:
    """A pipe watcher also covers parent exit during slow application startup."""

    def __init__(self, stream: BinaryIO) -> None:
        self.input_fd = stream.fileno()
        self.disconnected = threading.Event()
        self.finished = threading.Event()
        self.server: uvicorn.Server | None = None

    def start(self) -> None:
        threading.Thread(target=self._watch, name="desktop-parent-pipe", daemon=True).start()

    def attach(self, server: uvicorn.Server) -> None:
        self.server = server
        if self.disconnected.is_set():
            server.should_exit = True

    def _watch(self) -> None:
        try:
            # Never hold Python's buffered-stdin lock in a daemon thread. On a
            # startup error the parent may still hold its pipe open, and Python
            # finalization would otherwise abort waiting for that buffer lock.
            while os.read(self.input_fd, 4096):
                pass
        except (OSError, ValueError):
            # A broken/closed pipe has the same ownership meaning as EOF.
            pass
        self.disconnected.set()
        if self.server is not None:
            self.server.should_exit = True
        if not self.finished.wait(SHUTDOWN_TIMEOUT_SECONDS):
            # ASGI shutdown callbacks or model requests can otherwise strand a
            # backend after the owning app has exited. Give cleanup a bounded
            # opportunity first; this is a last-resort process exit, not a kill
            # of any other Python process.
            os._exit(1)


def _absolute_path(raw: str | None, name: str) -> Path:
    if not raw:
        raise DesktopStartupError("missing_path", f"An explicit {name} is required.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise DesktopStartupError("invalid_path", f"The {name} must be an absolute path.")
    return path.resolve()


def _emit(stream: TextIO, message: dict[str, object]) -> None:
    stream.write(json.dumps(message, ensure_ascii=True) + "\n")
    stream.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OfferPilot private desktop backend")
    parser.add_argument("--data-dir", default=os.environ.get("OFFERPILOT_DESKTOP_DATA_DIR"))
    parser.add_argument("--static-dir", default=os.environ.get("OFFERPILOT_DESKTOP_STATIC_DIR"))
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    protocol_stream = sys.stdout
    lifetime: _ParentLifetime | None = None
    try:
        if protocol_stream is None or sys.stdin is None:
            raise DesktopStartupError("missing_pipes", "Desktop backend requires stdio pipes.")
        data_dir = _absolute_path(args.data_dir, "data directory")
        static_dir = _absolute_path(args.static_dir, "static directory")
        if not 0 <= args.port <= 65535:
            raise DesktopStartupError("invalid_port", "Desktop port must be between 0 and 65535.")
        if not static_dir.is_dir() or not (static_dir / "index.html").is_file():
            raise DesktopStartupError(
                "missing_frontend", "Built frontend index.html is missing from the static directory."
            )
        # Read and remove the ephemeral token so subprocesses cannot accidentally
        # inherit it. It is never stored in config.json, URLs, or protocol output.
        token = os.environ.pop(TOKEN_ENV, "")
        lifetime = _ParentLifetime(sys.stdin.buffer)
        lifetime.start()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                # Reopen a saved origin after shutdown despite TIME_WAIT sockets.
                # Windows needs exclusive ownership instead of POSIX reuse flags.
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(("127.0.0.1", args.port))
            except OSError as exc:
                raise DesktopStartupError(
                    "port_unavailable", "The desktop loopback port could not be bound."
                ) from exc
            security = DesktopSecurity(token=token, port=listener.getsockname()[1])
            if lifetime.disconnected.is_set():
                return 0
            # Third-party stdout chatter must not corrupt the parent's protocol.
            with contextlib.redirect_stdout(sys.stderr):
                app = create_desktop_app(data_dir=data_dir, static_dir=static_dir, security=security)
                server = _DesktopServer(
                    uvicorn.Config(
                        app,
                        host="127.0.0.1",
                        port=security.port,
                        loop="asyncio",
                        http="h11",
                        ws="none",
                        lifespan="on",
                        workers=1,
                        proxy_headers=False,
                        server_header=False,
                        access_log=False,
                        timeout_graceful_shutdown=5,
                    ),
                    on_ready=lambda: _emit(
                        protocol_stream,
                        {
                            "type": "offerpilot.desktop.ready",
                            "protocol": PROTOCOL_VERSION,
                            "origin": security.origin,
                            "pid": os.getpid(),
                            "parent_pid": os.getppid(),
                        },
                    ),
                )
                lifetime.attach(server)
                server.run(sockets=[listener])
                if not server.ready_emitted and not lifetime.disconnected.is_set():
                    raise DesktopStartupError("startup_failed", "ASGI application startup failed.")
        return 0
    except (Exception, SystemExit) as exc:
        code = exc.code if isinstance(exc, DesktopStartupError) else "startup_failed"
        message = (
            str(exc) if isinstance(exc, DesktopStartupError)
            else f"Desktop backend failed ({type(exc).__name__})."
        )
        if protocol_stream is not None:
            _emit(protocol_stream, {
                "type": "offerpilot.desktop.error",
                "protocol": PROTOCOL_VERSION,
                "code": code,
                "message": message,
            })
        print(message, file=sys.stderr)
        return 1
    finally:
        if lifetime is not None:
            lifetime.finished.set()


if __name__ == "__main__":
    raise SystemExit(main())
