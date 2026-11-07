"""Metadata-only observation of decoded SSE consumption; bytes remain untouched."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import time
from typing import Any


def observe_stream(channel: Any, response: Any, attempt: str, identity: Any,
                   started: float, started_at: str) -> None:
    original_iterator, original_close = response.iter_bytes, response.close
    pending = b""
    finished = False
    active = False
    pulling = False
    usage_seen = False
    done_seen = False
    eof = False
    byte_count = 0
    first_byte = last_byte = None
    capture_errors: list[str] = []
    parsing = True

    def emit(payload: dict[str, Any]) -> None:
        try:
            channel.emit("model_observation", payload, ack=True)
        except Exception as exc:
            # Diagnostics cannot replace an original stream error or cause a resend.
            if len(capture_errors) < 8:
                capture_errors.append(type(exc).__name__)

    def finish(reason: str, error: BaseException | None = None) -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        chain: list[str] = []
        seen: set[int] = set()
        while error is not None and id(error) not in seen and len(chain) < 8:
            seen.add(id(error))
            chain.append(type(error).__name__)
            error = error.__cause__ if error.__cause__ is not None else error.__context__
        emit(dict(category="provider_stream_terminal", attempt=attempt, identity=identity,
            started_at=started_at, finished_at=datetime.now(timezone.utc).isoformat(),
            elapsed_seconds=time.monotonic() - started, first_byte_seconds=first_byte,
            last_byte_seconds=last_byte, decoded_bytes=byte_count, http_response_received=True,
            status=response.status_code, terminal_reason=reason, natural_eof=eof,
            sse_done_seen=done_seen, usage_seen=usage_seen,
            partial_line_bytes=len(pending), exception_chain_types=chain,
            consumer_early_close=reason in {"consumer_closed", "iterator_closed"} and not done_seen,
            capture_errors=list(capture_errors), observation_complete=not capture_errors,
            raw_content_archived=False))

    def iterate(*args: Any, **kwargs: Any) -> Any:
        nonlocal pending, active, pulling, usage_seen, done_seen, eof, byte_count, first_byte, last_byte, parsing
        active = True
        reason = "iterator_closed"
        error = None
        try:
            iterator = iter(original_iterator(*args, **kwargs))
            while True:
                pulling = True
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                finally:
                    pulling = False
                now = time.monotonic() - started
                if first_byte is None:
                    first_byte = now
                last_byte = now
                byte_count += len(chunk)
                if parsing:
                    pending += chunk
                if len(pending) > 1024 * 1024:
                    # Bound observation memory, never truncate a valid SDK response.
                    capture_errors.append("sse_line_capture_limit")
                    parsing, pending = False, b""
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if data == b"[DONE]":
                        done_seen = True
                        continue
                    try:
                        value = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(value, dict) and value.get("usage"):
                        usage_seen = True
                        emit(dict(category="usage", attempt=attempt, identity=identity,
                            actual_model=value.get("model"), usage=value["usage"], stream=True,
                            completion_id_hash=hashlib.sha256(value["id"].encode()).hexdigest()
                            if isinstance(value.get("id"), str) else None))
                yield chunk
            eof = True
            reason = "natural_eof"
        except GeneratorExit:
            raise
        except BaseException as exc:
            reason, error = "read_error", exc
            raise
        finally:
            active = False
            finish(reason, error)

    def close() -> None:
        try:
            original_close()
        finally:
            # HTTPX automatically closes at EOF inside its iterator. Let that
            # iterator record EOF; SDK closing after [DONE] is a separate signal.
            if not active or not pulling:
                finish("consumer_closed")

    response.iter_bytes = iterate
    response.close = close


def observe_async_stream(channel: Any, response: Any, attempt: str, identity: Any,
                         started: float, started_at: str, io: Any) -> None:
    """Feed actual decoded async bytes through the same metadata-only parser.

    The proxy is only an iterator adapter. The original async response owns all
    network reads and closure, and the SDK receives the original bytes unchanged.
    """
    from collections import deque
    from types import SimpleNamespace

    original_iterator, original_close = response.aiter_bytes, response.aclose
    pending: Any = deque()
    end, failed = object(), object()
    active = pulling = False

    def source() -> Any:
        while True:
            value = pending.popleft()
            if value is end:
                return
            if isinstance(value, BaseException):
                raise value
            yield value

    proxy = SimpleNamespace(status_code=response.status_code, iter_bytes=lambda: source(),
                            close=lambda: None)
    observe_stream(channel, proxy, attempt, identity, started, started_at)
    observed = proxy.iter_bytes()

    def advance() -> Any:
        try:
            return next(observed)
        except StopIteration:
            return end

    def record_error(error: BaseException) -> Any:
        pending.append(error)
        try:
            return advance()
        except BaseException:
            return failed  # The actual async iterator already propagates it.

    def close_observer() -> None:
        try:
            proxy.close()
        finally:
            observed.close()

    async def iterate(*args: Any, **kwargs: Any) -> Any:
        nonlocal active, pulling
        active = True
        try:
            iterator = original_iterator(*args, **kwargs).__aiter__()
            while True:
                pulling = True
                try:
                    chunk = await iterator.__anext__()
                except StopAsyncIteration:
                    break
                finally:
                    pulling = False
                pending.append(chunk)
                value = await io.call(advance)
                if value is not chunk:
                    raise RuntimeError("metadata iterator changed provider bytes")
                yield chunk
            pending.append(end)
            if await io.call(advance) is not end:
                raise RuntimeError("metadata iterator did not observe actual EOF")
        except GeneratorExit:
            raise
        except BaseException as error:
            io.defer(lambda error=error: record_error(error))
            raise
        finally:
            active = False
            io.defer(close_observer)

    async def close() -> None:
        try:
            await original_close()
        except BaseException as error:
            io.defer(lambda error=error: record_error(error))
            raise
        finally:
            # Closing transport must not wait for a control ACK on the I/O loop.
            if not active or not pulling:
                io.defer(close_observer)

    response.aiter_bytes = iterate
    response.aclose = close
