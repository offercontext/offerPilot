"""SDK-bound one-send scopes spanning lazy streams, with exact request admission."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import secrets
import threading
import time
from typing import Any, Callable, Iterator

import httpx

from .archive import EvidenceArchive, captured_response
from .channel import PipeClient
from .payload import bounded_payload


@dataclass
class LogicalCall:
    identity: str = field(default_factory=lambda: "completion-" + secrets.token_hex(16))
    attempted: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    started: float = field(default_factory=time.monotonic)
    deadline: float = field(default_factory=lambda: time.monotonic() + 45)
    responses: list[httpx.Response] = field(default_factory=list)

    def begin_send(self) -> None:
        with self.lock:
            if self.attempted:
                raise PermissionError("A logical SDK completion may physically send only once")
            self.attempted = True  # Even a denied/failed/timeout attempt consumes this scope.


CURRENT: ContextVar[LogicalCall | None] = ContextVar("official-smoke-logical-call", default=None)
PRODUCT_CALL: ContextVar[str | None] = ContextVar("official-smoke-product-call", default=None)
API_REQUEST: ContextVar[int | None] = ContextVar("official-smoke-api-request", default=None)
RUNTIME_IDENTITY: ContextVar[dict[str, Any] | None] = ContextVar(
    "official-smoke-runtime-identity", default=None
)


@contextmanager
def logical_scope(scope: LogicalCall) -> Iterator[None]:
    token = CURRENT.set(scope)
    try:
        yield
    finally:
        CURRENT.reset(token)


def close_responses(responses: list[httpx.Response], archive: EvidenceArchive) -> None:
    errors: list[BaseException] = []
    for response in responses:
        try:
            response.close()
        except BaseException as error:
            errors.append(error)
    if errors:
        try:
            archive.failed("close", type(errors[0]).__name__)
        except BaseException:
            pass
        raise errors[0]


def completion_wrapper(
    original: Callable[..., Any], archive: EvidenceArchive, sdk_client: Any
) -> Callable[..., Any]:
    def completion(**payload: Any) -> Any:
        scope = LogicalCall()
        links = dict(
            logical_call_id=scope.identity,
            product_call_id=PRODUCT_CALL.get(),
            api_request_id=API_REQUEST.get(),
            runtime_identity=RUNTIME_IDENTITY.get(),
        )
        archive.event("sdk-completion-start", links=links, stream=payload.get("stream") is True)
        payload, differences = bounded_payload(
            payload,
            dict(
                model="deepseek-flash",
                endpoint="https://api.deepseek.com/v1",
                fallback=[],
                max_output_tokens=8192,
                timeout_seconds=45,
            ),
            key=sdk_client.api_key,
            sdk_client=sdk_client,
        )
        scope.deadline = scope.started + float(payload["timeout"])
        archive.event("bounded-verification-configuration", links=links, differences=differences)
        with logical_scope(scope):
            try:
                result = original(**payload)
            except BaseException as error:
                try:
                    close_responses(scope.responses, archive)
                except BaseException as cleanup:
                    try:
                        archive.event(
                            "sdk-cleanup-error", links=links, error_type=type(cleanup).__name__
                        )
                    except BaseException:
                        pass
                archive.event(
                    "sdk-completion-error",
                    links=links,
                    error_type=type(error).__name__,
                    exception_chain=archive.exception(error),
                )
                raise
        if payload.get("stream"):
            return _stream(result, scope, links, archive)
        # Avoid SDK internals/_hidden_params which may retain credentials.
        try:
            archive.event("sdk-parsed-result", links=links, value=_safe_sdk(result))
            return result
        finally:
            close_responses(scope.responses, archive)

    return completion


def _safe_sdk(value: Any) -> dict[str, Any]:
    def get(obj: Any, key: str) -> Any:
        return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)

    def dump(obj: Any) -> Any:
        return obj.model_dump() if hasattr(obj, "model_dump") else obj

    return dict(
        id=get(value, "id"),
        model=get(value, "model"),
        choices=[dump(x) for x in (get(value, "choices") or [])],
        usage=dump(get(value, "usage")),
    )


class _stream:
    """Close works even before the first next(); a generator's finally does not."""

    def __init__(
        self, result: Any, scope: LogicalCall, links: dict[str, Any], archive: EvidenceArchive
    ) -> None:
        self.result, self.scope, self.links, self.archive = result, scope, links, archive
        self.iterator: Iterator[Any] | None = None
        self.finished = False
        archive.streams.append(self)

    def __iter__(self) -> _stream:
        return self

    def __next__(self) -> Any:
        if self.finished:
            raise StopIteration
        try:
            with logical_scope(self.scope):
                if time.monotonic() >= self.scope.deadline:
                    raise TimeoutError("Logical SDK call budget exhausted before stream iteration")
                if self.iterator is None:
                    self.iterator = iter(self.result)
                item = next(self.iterator)
                if time.monotonic() >= self.scope.deadline:
                    raise TimeoutError("Logical SDK call budget exhausted during stream iteration")
                self.archive.event("sdk-parsed-chunk", links=self.links, value=_safe_sdk(item))
                return item
        except StopIteration:
            self._end(True)
            raise
        except BaseException as error:
            self.archive.event(
                "sdk-stream-error", links=self.links, error_type=type(error).__name__
            )
            self._end(False)
            raise

    def _end(self, complete: bool) -> None:
        if self.finished:
            return
        self.finished = True
        close_error: BaseException | None = None
        try:
            with logical_scope(self.scope):
                if hasattr(self.result, "close"):
                    self.result.close()
        except BaseException as error:
            close_error = error
            self.archive.failed("close", type(error).__name__)
        finally:
            try:
                close_responses(self.scope.responses, self.archive)
            except BaseException as error:
                close_error = error
            raw = [
                dict(
                    body_start=row["body_start"],
                    raw_complete=row["complete"],
                    observed_sha256=row["observed_sha256"],
                )
                for row in self.archive.events
                if row["kind"] == "provider-response-end"
                and row["links"].get("logical_call_id") == self.scope.identity
            ]
            self.archive.event(
                "sdk-stream-end",
                links=self.links,
                sdk_semantic_complete=complete,
                raw_bodies=raw,
                raw_eof_proven=bool(raw) and all(r["raw_complete"] for r in raw),
            )
            if not complete:
                self.archive.incomplete = True
        if close_error is not None:
            raise close_error

    def close(self) -> None:
        self._end(False)


class ControlledTransport(httpx.BaseTransport):
    def __init__(
        self, inner: httpx.BaseTransport, channel: PipeClient, archive: EvidenceArchive
    ) -> None:
        self.inner, self.channel, self.archive = inner, channel, archive
        self.responses: list[httpx.Response] = []
        self.closed = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self.archive.incomplete or self.closed:
            raise PermissionError("Evidence failed; no further model send")
        scope = CURRENT.get()
        if scope is None:
            raise PermissionError("Unscoped SDK/repair send refused")
        scope.begin_send()
        permit = self.channel.permission(request, scope.identity)
        # Revalidate the actual immutable serialized bytes and complete headers
        # after ACK, immediately before atomic one-use permission consumption.
        estimate = self.channel.policy.validate(
            request, expected_key=self.channel.key, now=datetime.now(timezone.utc)
        )
        reservation = permit.consume(estimate.request_sha256)
        links = dict(
            logical_call_id=scope.identity,
            product_call_id=PRODUCT_CALL.get(),
            api_request_id=API_REQUEST.get(),
            runtime_identity=RUNTIME_IDENTITY.get(),
            reservation_row_hash=reservation,
            request_sha256=estimate.request_sha256,
        )
        capture = self.archive.body("provider-request", links=links)
        capture.feed(request.content)
        capture.end(complete=True)
        remaining = scope.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Original logical call budget exhausted before dispatch")
        original_timeout = request.extensions.get("timeout")
        if (
            not isinstance(original_timeout, dict)
            or set(original_timeout) != {"connect", "read", "write", "pool"}
            or any(
                type(value) not in (int, float) or not 0 < value <= 45
                for value in original_timeout.values()
            )
        ):
            raise PermissionError("Explicit bounded SDK network timeouts required")
        request.extensions["timeout"] = {
            name: min(value, remaining) for name, value in original_timeout.items()
        }
        from scripts.evaluation_subject.core_transport import SEND_PERMISSION

        self.archive.event("provider-dispatch", links=links, external_receipt_confirmed=None)
        SEND_PERMISSION.permitted, SEND_PERMISSION.addresses = True, set()
        SEND_PERMISSION.attempt = scope.identity
        response: httpx.Response | None = None
        try:
            response = self.inner.handle_request(request)
            scope.responses.append(response)
            self.responses.append(response)
            return captured_response(response, self.archive, kind="provider-response", links=links)
        except BaseException as error:
            if response is not None:
                try:
                    close_responses([response], self.archive)
                except BaseException:
                    pass
            self.archive.event(
                "provider-transport-error",
                links=links,
                error_type=type(error).__name__,
                failure_stage="transport" if response is None else "capture",
                response_received=None if response is None else True,
                status=None if response is None else response.status_code,
                body_complete=None,
            )
            raise
        finally:
            SEND_PERMISSION.permitted, SEND_PERMISSION.addresses = False, set()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        errors: list[BaseException] = []
        try:
            try:
                close_responses(self.responses, self.archive)
            except BaseException as error:
                errors.append(error)
        finally:
            try:
                self.inner.close()
            except BaseException as error:
                errors.append(error)
        if errors:
            try:
                self.archive.failed("close", type(errors[0]).__name__)
            except BaseException:
                pass
            raise errors[0]
