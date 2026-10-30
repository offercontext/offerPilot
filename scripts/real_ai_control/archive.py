"""Ordered HTTP-body tees and distinct SDK/product evidence, before cleanup.

Raw means HTTP entity bytes at the installed hook, never TLS wire bytes.
Known literal secrets are redacted with an explicit loss-of-raw-fidelity flag.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import threading
from typing import Any, Callable, Iterator, cast

import httpx

from offerpilot.evaluation.core_targeted_account import safe_path
from .protocol import canonical, sha


class ArchiveIncomplete(RuntimeError):
    pass


class EvidenceArchive:
    def __init__(
        self,
        root: Path,
        *,
        secrets: tuple[str, ...],
        entry_limit: int = 16 * 1024 * 1024,
        total_limit: int = 256 * 1024 * 1024,
        fault: Callable[[str], None] = lambda _: None,
    ) -> None:
        if not 0 < entry_limit <= total_limit <= 256 * 1024 * 1024:
            raise ValueError("Frozen archive byte limits required")
        self.root = safe_path(root)
        self.root.mkdir(exist_ok=False)
        self.secrets = tuple(x.encode("utf8") for x in secrets if x)
        self.entry_limit, self.total_limit, self.fault = entry_limit, total_limit, fault
        self.total, self.number = 0, 0
        self.incomplete = False
        self.lock = threading.RLock()
        self.events: list[dict[str, Any]] = []
        self.active = 0
        self.streams: list[Any] = []
        self.failure_sink: Callable[[dict[str, Any]], None] | None = None
        self.failure_reported = False
        self.prefixes: dict[int, dict[str, Any]] = {}

    def failed(self, phase: str, error_type: str) -> None:
        self.incomplete = True
        if self.failure_reported:
            return
        self.failure_reported = True
        if self.failure_sink is not None:
            self.failure_sink(
                dict(
                    error_type=error_type,
                    phase=phase,
                    last_successful_ordinal=self.events[-1]["ordinal"] if self.events else 0,
                    last_event_sha256=sha(canonical(self.events[-1])) if self.events else "0" * 64,
                    raw_prefixes=list(self.prefixes.values())[-4:],
                    continuation_forbidden=True,
                )
            )

    def event(self, kind: str, **data: Any) -> dict[str, Any]:
        with self.lock:
            self.number += 1
            row = cast(dict[str, Any], self.scrub(dict(ordinal=self.number, kind=kind, **data)))
            try:
                self.fault("metadata")
                raw = canonical(row) + b"\n"
                if len(raw) > self.entry_limit or self.total + len(raw) > self.total_limit:
                    raise ArchiveIncomplete("Total archive limit")
                with (self.root / "events.jsonl").open("ab") as stream:
                    if stream.write(raw) != len(raw):
                        raise OSError("Partial metadata write")
                    stream.flush()
                    os.fsync(stream.fileno())
                self.total += len(raw)
            except BaseException as error:
                self.failed("metadata", type(error).__name__)
                raise ArchiveIncomplete("Evidence metadata could not be archived") from None
            self.events.append(row)
            return row

    def scrub(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(k): self.scrub(v)
                for k, v in value.items()
                if str(k).lower()
                not in {"authorization", "api_key", "_hidden_params", "cookie", "set-cookie"}
            }
        if isinstance(value, (list, tuple)):
            return [self.scrub(v) for v in value]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret.decode("utf8"), "[REDACTED]")
            return value
        return value

    def exception(self, error: BaseException) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        seen: set[int] = set()
        current: BaseException | None = error
        while current is not None and id(current) not in seen and len(result) < 8:
            seen.add(id(current))
            result.append(
                dict(type=type(current).__name__, message=self.scrub(str(current))[:4096])
            )
            current = current.__cause__ or current.__context__
        return result

    def body(self, kind: str, *, links: dict[str, Any]) -> BodyCapture:
        return BodyCapture(self, kind, links)

    def summary(self) -> dict[str, Any]:
        with self.lock:
            path = self.root / "events.jsonl"
            raw = path.read_bytes() if path.exists() else b""
            expected = b"".join(canonical(row) + b"\n" for row in self.events)
            if raw != expected:
                self.failed("summary", "MetadataPrefixMismatch")
            return dict(
                events=len(self.events),
                attempted_events=self.number,
                stored_bytes=self.total,
                active_bodies=self.active,
                complete=not self.incomplete and self.active == 0,
                events_sha256=sha(raw),
                historical_unknowns_backfilled=False,
            )

    def __enter__(self) -> EvidenceArchive:
        return self

    def __exit__(self, *args: Any) -> None:
        if self.active:
            self.incomplete = True


class BodyCapture:
    def __init__(self, archive: EvidenceArchive, kind: str, links: dict[str, Any]) -> None:
        self.archive, self.kind, self.links = archive, kind, links
        self.pending = b""
        self.observed, self.stored = hashlib.sha256(), hashlib.sha256()
        self.count, self.saved = 0, 0
        self.redacted, self.finished, self.failed = False, False, False
        with archive.lock:
            self.start = archive.event(kind + "-start", links=links)["ordinal"]
            self.path = archive.root / f"body-{self.start:06d}.bin"
            try:
                self.stream = self.path.open("xb")
            except BaseException as error:
                archive.failed("body", type(error).__name__)
                raise ArchiveIncomplete("Body file could not be created") from None
            archive.prefixes[self.start] = dict(
                body_start=self.start,
                file=self.path.name,
                stored_bytes=0,
                stored_sha256=self.stored.hexdigest(),
                observed_bytes=0,
                persistence_unknown=False,
            )
            archive.active += 1

    def _save(self, raw: bytes) -> None:
        with self.archive.lock:
            room = min(
                self.archive.entry_limit - self.saved, self.archive.total_limit - self.archive.total
            )
            prefix = raw[: max(0, room)]
            self.archive.prefixes[self.start].update(
                observed_bytes=self.count, persistence_unknown=True
            )
            try:
                self.archive.fault("body")
                if self.stream.write(prefix) != len(prefix):
                    raise OSError("Partial archive write")
                self.stream.flush()
                os.fsync(self.stream.fileno())
                self.stored.update(prefix)
                self.saved += len(prefix)
                self.archive.total += len(prefix)
                self.archive.prefixes[self.start] = dict(
                    body_start=self.start,
                    file=self.path.name,
                    stored_bytes=self.saved,
                    stored_sha256=self.stored.hexdigest(),
                    observed_bytes=self.count,
                    persistence_unknown=False,
                )
                if len(prefix) != len(raw):
                    raise ArchiveIncomplete("Archive byte limit; prefix retained")
            except BaseException as error:
                self.failed = True
                self.archive.failed("body", type(error).__name__)
                raise ArchiveIncomplete("Body archive incomplete; prefix retained") from None

    def feed(self, raw: bytes) -> None:
        if self.finished or self.failed:
            raise ArchiveIncomplete("Body already ended or failed")
        self.observed.update(raw)
        self.count += len(raw)
        pending = self.pending + raw
        keep = max((len(secret) - 1 for secret in self.archive.secrets), default=0)
        # Retain a suffix so a secret split across arbitrary stream chunks is detected.
        cutoff = max(0, len(pending) - keep)
        for secret in self.archive.secrets:
            index = pending.find(secret)
            while index >= 0:
                pending = pending[:index] + b"[REDACTED]" + pending[index + len(secret) :]
                self.redacted = True
                cutoff = min(cutoff, index)  # Never split the replacement during this pass.
                index = pending.find(secret, index + len(b"[REDACTED]"))
        self._save(pending[:cutoff])
        self.pending = pending[cutoff:]

    def end(self, *, complete: bool, error_type: str | None = None) -> dict[str, Any]:
        if self.finished:
            raise ArchiveIncomplete("Duplicate body terminal")
        self.finished = True
        try:
            if not self.failed:
                self._save(self.pending)
        finally:
            try:
                self.stream.close()
            except BaseException as error:
                self.archive.prefixes[self.start]["persistence_unknown"] = True
                self.archive.failed("close", type(error).__name__)
                raise
            finally:
                with self.archive.lock:
                    self.archive.active -= 1
        if not complete or self.failed:
            self.archive.incomplete = True
        return self.archive.event(
            self.kind + "-end",
            links=self.links,
            body_start=self.start,
            file=self.path.name,
            bytes_observed=self.count,
            bytes_stored=self.saved,
            observed_sha256=self.observed.hexdigest(),
            stored_sha256=self.stored.hexdigest(),
            raw_fidelity=not self.redacted and not self.failed,
            literal_secret_redacted=self.redacted,
            complete=complete and not self.failed,
            error_type=error_type,
            layer=self.links.get("body_layer", "serialized HTTP request body; not TLS wire"),
        )


class TeeStream(httpx.SyncByteStream):
    def __init__(self, inner: httpx.SyncByteStream, capture: BodyCapture) -> None:
        self.inner, self.capture = inner, capture
        self.ended = False

    def __iter__(self) -> Iterator[bytes]:
        try:
            for chunk in self.inner:
                self.capture.feed(chunk)
                yield chunk
            self.ended = True
            self.capture.end(complete=True)
        except BaseException as error:
            if not self.capture.finished:
                self.capture.end(complete=False, error_type=type(error).__name__)
            raise

    def close(self) -> None:
        try:
            self.inner.close()
        except BaseException as error:
            self.capture.archive.failed("close", type(error).__name__)
            raise
        finally:
            if not self.capture.finished:
                self.capture.end(complete=False, error_type="StreamClosedBeforeEOF")


def captured_response(
    response: httpx.Response, archive: EvidenceArchive, *, kind: str, links: dict[str, Any]
) -> httpx.Response:
    archive.event(
        kind + "-headers",
        links=links,
        status=response.status_code,
        provider_request_id=response.headers.get("x-request-id"),
        content_type=response.headers.get("content-type"),
        content_encoding=response.headers.get("content-encoding"),
    )
    if response.is_stream_consumed:
        try:
            raw = response.content
        except httpx.ResponseNotRead:
            archive.incomplete = True
            raise ArchiveIncomplete(
                "Response consumed before archive hook; bytes unavailable"
            ) from None
        capture = archive.body(
            kind, links={**links, "body_layer": "HTTPX already buffered decoded body"}
        )
        capture.feed(raw)
        capture.end(complete=True)
        return response
    # HTTPX decoding occurs above the transport. The byte stream at this hook is
    # content-encoded HTTP entity bytes. Label that layer exactly in metadata.
    capture = archive.body(
        kind, links={**links, "body_layer": "HTTP entity stream before HTTPX decoding"}
    )
    if not isinstance(response.stream, httpx.SyncByteStream):
        archive.incomplete = True
        raise ArchiveIncomplete("Synchronous body stream required")
    response.stream = TeeStream(response.stream, capture)
    return response
