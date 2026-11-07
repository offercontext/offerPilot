"""Async SDK admission and metadata observation inside the existing subject."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timezone
from functools import partial
import hashlib
import inspect
import ipaddress
import json
import time
from typing import Any, Callable


class ObserverIO:
    """Keep bounded control ACKs and evidence writes off the provider I/O loop."""

    def __init__(self) -> None:
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eval-provider-observer")

    def defer(self, function: Callable[[], Any]) -> Any:
        context = copy_context()
        return self.pool.submit(context.run, function)

    async def call(self, function: Callable[[], Any]) -> Any:
        future = asyncio.wrap_future(self.defer(function))
        # Cancellation must not discard an already admitted control record.
        # The callback cannot send HTTP; the awaiting SDK task owns that action.
        future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(future)

    def close(self) -> None:
        # Subject shutdown first wakes pending ACKs under its original deadline.
        self.pool.shutdown(wait=True)


def install(channel: Any, client: Any, prepare: Callable[..., Any]) -> ObserverIO:
    from scripts.evaluation_subject.core_transport import SEND_PERMISSION
    from scripts.evaluation_subject.core_stream import observe_async_stream

    io = ObserverIO()
    original_send = client.send

    async def send(request: Any, *args: Any, **kwargs: Any) -> Any:
        # OpenAI builds a buffered JSON request. Freeze those exact wire bytes.
        body = await request.aread()
        prepared = await io.call(lambda: prepare(request, require_scope=True))
        if prepared.activity is None or not prepared.activity.is_set():
            raise PermissionError("provider invocation closed before HTTP send")
        if request.content != body:
            raise PermissionError("provider request changed after admission")
        attempt, private, payload = prepared.attempt, prepared.private, prepared.payload
        identity = prepared.identity
        kwargs["follow_redirects"] = False
        started = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        events: list[dict[str, Any]] = []
        previous_trace = request.extensions.get("trace")

        async def trace(name: str, info: Any) -> None:
            if len(events) < 64:
                events.append(dict(event=name, elapsed_seconds=round(time.monotonic() - started, 6)))
            if previous_trace is not None:
                result = previous_trace(name, info)
                if inspect.isawaitable(result):
                    await result

        request.extensions["trace"] = trace
        responded = False
        response: Any = None
        cleanup_error: str | None = None

        def error_record(error: BaseException) -> None:
            chain: list[dict[str, Any]] = []
            seen: set[int] = set()
            current: BaseException | None = error
            while current is not None and id(current) not in seen and len(chain) < 8:
                seen.add(id(current))
                chain.append(dict(type=type(current).__name__, module=type(current).__module__,
                                  errno=getattr(current, "errno", None),
                                  winerror=getattr(current, "winerror", None)))
                current = current.__cause__ or current.__context__
            channel.emit("model_observation", dict(category="provider_error", attempt=attempt,
                error_type=type(error).__name__, exception_chain=chain,
                http_response_received=responded,
                failure_stage="response_capture" if responded else
                events[-1]["event"] if events else "http_send", transport_events=list(events),
                unreturned_response_cleanup_error=cleanup_error), ack=True)

        try:
            SEND_PERMISSION.permitted = True
            SEND_PERMISSION.attempt = attempt
            try:
                # Async networking can skip DNS for a literal IP.
                address = str(ipaddress.ip_address(request.url.host))
            except ValueError:
                SEND_PERMISSION.addresses = set()
            else:
                SEND_PERMISSION.addresses = {(address, request.url.port or
                                               (443 if request.url.scheme == "https" else 80))}
            try:
                response = await original_send(request, *args, **kwargs)
            finally:
                SEND_PERMISSION.permitted = False
                if previous_trace is None:
                    request.extensions.pop("trace", None)
                else:
                    request.extensions["trace"] = previous_trace
            responded = True
            await io.call(lambda: channel.emit("model_observation", dict(
                category="provider_response", attempt=attempt, identity=identity,
                status=response.status_code,
                request_id_hash=hashlib.sha256(response.headers["x-request-id"].encode()).hexdigest()
                if response.headers.get("x-request-id") else None,
                transport_stream=bool(kwargs.get("stream")), wire_stream=bool(payload.get("stream")),
                content_encoding=response.headers.get("content-encoding"),
                content_type=response.headers.get("content-type"), transport_events=list(events)), ack=True))
            if payload.get("stream"):
                observe_async_stream(channel, response, attempt, identity, started, started_at, io)
            else:
                raw_bytes = await response.aread()
                if len(raw_bytes) > 1024 * 1024:
                    raise ValueError("oversized provider JSON response")
                raw = json.loads(raw_bytes)

                def capture_json() -> None:
                    if response.status_code == 200:
                        (private / (attempt + "-response.json")).write_bytes(raw_bytes)
                    channel.emit("model_observation", dict(category="usage", attempt=attempt,
                        actual_model=raw.get("model"), usage=raw.get("usage"),
                        completion_id_hash=hashlib.sha256(raw["id"].encode()).hexdigest()
                        if isinstance(raw.get("id"), str) else None), ack=True)

                await io.call(capture_json)
            return response
        except BaseException as error:
            # Until send returns, neither AsyncOpenAI nor consume() owns this
            # response. Close it here even if observation ACK is cancelled.
            if response is not None:
                try:
                    await response.aclose()
                except BaseException as close_error:
                    cleanup_error = type(close_error).__name__
            if isinstance(error, asyncio.CancelledError):
                try:
                    io.defer(partial(error_record, error))
                except RuntimeError:
                    pass  # Closed observation is a gap, never a replacement cancellation.
            else:
                try:
                    await io.call(partial(error_record, error))
                except Exception:
                    # Retain the SDK failure; missing diagnostics remain gaps.
                    pass
            raise

    client.send = send
    return io
