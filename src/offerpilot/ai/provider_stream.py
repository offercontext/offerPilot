"""Cancellation-aware async provider I/O beneath the synchronous Agent boundary.

Only transport runs on the shared event loop. Authority checks, delta callbacks,
response validation and persistence remain on the original Agent worker.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Any

from .control import AgentLoopControlError

_CHECK_ACTIVE: ContextVar[Callable[[], None] | None] = ContextVar("provider_check_active", default=None)
_LOOP_LOCK = Lock()
_LOOP: asyncio.AbstractEventLoop | None = None
_CLOSE_TIMEOUT_SECONDS = 5.0


@contextmanager
def provider_control_scope(check_active: Callable[[], None]) -> Iterator[None]:
    token = _CHECK_ACTIVE.set(check_active)
    try:
        yield
    finally:
        _CHECK_ACTIVE.reset(token)


def provider_control() -> Callable[[], None] | None:
    return _CHECK_ACTIVE.get()


def _io_loop() -> asyncio.AbstractEventLoop:
    global _LOOP
    with _LOOP_LOCK:
        if _LOOP is None:
            ready = Event()
            loop = asyncio.new_event_loop()

            def run() -> None:
                asyncio.set_event_loop(loop)
                ready.set()
                loop.run_forever()

            Thread(target=run, name="pilot-provider-io", daemon=True).start()
            ready.wait()
            _LOOP = loop
        return _LOOP


def interruptible_stream(
    create: Callable[[], Awaitable[Any]], check_active: Callable[[], None],
) -> Iterator[Any]:
    """Cancel actual asynchronous socket waits, including before response headers.

    The queue is bounded independently of provider output. Cancelling the async
    task unwinds the SDK's request/read and awaits stream closure before this
    iterator returns. This bridge does not put synchronous completion in a
    background thread and mistake Future.cancel() for a transport disconnect.
    """
    check_active()
    chunks: Queue[tuple[str, Any]] = Queue(maxsize=1)
    started, finished = Event(), Event()
    space = asyncio.Event()
    task: asyncio.Task[None] | None = None
    closing = False

    async def publish(kind: str, value: Any) -> None:
        while True:
            try:
                chunks.put_nowait((kind, value))
                return
            except Full:
                space.clear()
                await space.wait()

    async def consume() -> None:
        nonlocal task, closing
        stream = None
        task = asyncio.current_task()
        started.set()
        try:
            stream = await create()
            async for chunk in stream:
                await publish("chunk", chunk)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await publish("error", exc)
        finally:
            closing = True
            try:
                if stream is not None:
                    close = getattr(stream, "aclose", None)
                    if callable(close):
                        await close()
            finally:
                finished.set()
                # Wake an idle consumer immediately on EOF/cleanup, without
                # inserting a per-response polling delay or blocking close.
                try:
                    chunks.put_nowait(("done", None))
                except Full:
                    pass

    loop = _io_loop()
    future = asyncio.run_coroutine_threadsafe(consume(), loop)

    def cancel_read() -> None:
        # This check and task.cancel run on the I/O loop. A worker-side cancel
        # must never interrupt normal/error-path aclose already in progress.
        if task is not None and not closing:
            task.cancel()

    # Start before exposing cancellation: cancelling a not-yet-started coroutine
    # would skip its finally and could never prove closure.
    started.wait()
    try:
        while True:
            check_active()
            if finished.is_set() and chunks.empty():
                future.result()
                return
            try:
                kind, value = chunks.get(timeout=0.05)
                loop.call_soon_threadsafe(space.set)
            except Empty:
                if finished.is_set():
                    future.result()
                    return
                continue
            check_active()
            if kind == "error":
                raise value
            if kind == "done":
                future.result()
                return
            yield value
    finally:
        if not finished.is_set():
            loop.call_soon_threadsafe(cancel_read)
            # Cancellation is delivered to native async I/O, not a running
            # synchronous Future. Wait for the stream's finally/aclose proof.
            if not finished.wait(_CLOSE_TIMEOUT_SECONDS):
                raise AgentLoopControlError("provider stream closure is unconfirmed")
