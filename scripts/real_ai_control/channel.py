"""Private inherited pipe routing and durable request-bound single-use permissions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import queue
import secrets
import threading
import time
from typing import Any, BinaryIO, Callable

import httpx

from .journal import ReservationJournal
from .protocol import (
    PROTOCOL,
    InvalidControlFrame,
    SendPermissionDenied,
    canonical,
    check_permission,
    hex_value,
    read_packet,
    reservation_payload,
    sha,
    validate_request,
    write_packet,
)
from .wire import WirePolicy


@dataclass(frozen=True)
class Binding:
    run_id: str
    plan_sha256: str
    source_sha256: str
    account_sha256: str
    quote_sha256: str
    implementation_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.run_id) is not str
            or not self.run_id
            or len(self.run_id) > 80
            or any(not hex_value(v, 64) for k, v in asdict(self).items() if k != "run_id")
        ):
            raise ValueError("Frozen run/plan/source/account/quote/implementation required")

    @property
    def digest(self) -> str:
        return sha(canonical(asdict(self)))


class Permit:
    def __init__(
        self, request_sha256: str, row_hash: str, *, live: Callable[[], bool], expires_at: float
    ) -> None:
        self.request_sha256, self.row_hash = request_sha256, row_hash
        self.live, self.expires_at = live, expires_at
        self.used = False
        self.lock = threading.Lock()

    def consume(self, request_sha256: str) -> str:
        with self.lock:
            if (
                self.used
                or not self.live()
                or time.monotonic() >= self.expires_at
                or self.request_sha256 != request_sha256
            ):
                raise PermissionError(
                    "Expired, consumed, disconnected or different send permission"
                )
            self.used = True
            return self.row_hash


class PipeClient:
    """One reader, bounded frames, per-sequence waiters; no lock held across ACK wait."""

    def __init__(
        self,
        reader: BinaryIO,
        writer: BinaryIO,
        *,
        binding: Binding,
        policy: WirePolicy,
        key: str,
        ack_timeout: float = 5.0,
    ) -> None:
        if not 0 < ack_timeout <= 5:
            raise ValueError("Frozen ACK control deadline must be at most five seconds")
        self.reader, self.writer, self.binding = reader, writer, binding
        self.policy, self.key, self.ack_timeout = policy, key, ack_timeout
        self.pending: dict[int, queue.Queue[Any]] = {}
        self.dead = threading.Event()
        self.write_lock, self.route_lock = threading.Lock(), threading.Lock()
        self.seq = 0
        self.finishing = False
        self.sending_closed = False
        self.thread = threading.Thread(target=self._receive, daemon=True)
        self.thread.start()

    def _fail(self) -> None:
        self.dead.set()
        with self.route_lock:
            for waiter in self.pending.values():
                waiter.put_nowait(EOFError("Private parent control ended or became invalid"))

    def _receive(self) -> None:
        try:
            while True:
                packet = read_packet(self.reader)
                if (
                    set(packet) != {"protocol", "kind", "seq", "binding_sha256", "data"}
                    or packet["kind"] not in {"permission", "finished"}
                    or packet["binding_sha256"] != self.binding.digest
                ):
                    raise InvalidControlFrame("Parent ACK binding differs")
                with self.route_lock:
                    waiter = self.pending.pop(packet["seq"], None)
                if waiter is None:
                    raise InvalidControlFrame("Duplicate, unsolicited or late ACK")
                waiter.put_nowait(packet["data"])
                if packet["kind"] == "finished":
                    self.dead.set()
                    return
        except BaseException:
            self._fail()

    def permission(self, request: httpx.Request, logical_id: str) -> Permit:
        estimate = self.policy.validate(
            request, expected_key=self.key, now=datetime.now(timezone.utc)
        )
        payload = reservation_payload(
            request,
            estimate,
            expected_key=self.key,
            logical_id=logical_id,
            nonce=secrets.token_hex(16),
        )
        waiter: queue.Queue[Any] = queue.Queue()
        with self.write_lock:
            if self.dead.is_set() or self.finishing or self.sending_closed:
                raise PermissionError("Parent control is unavailable")
            self.seq += 1
            seq = self.seq
            with self.route_lock:
                self.pending[seq] = waiter
            try:
                write_packet(
                    self.writer,
                    dict(
                        protocol=PROTOCOL,
                        kind="reserve",
                        seq=seq,
                        binding_sha256=self.binding.digest,
                        data=payload,
                    ),
                )
            except BaseException:
                self._fail()
                raise
        try:
            result = waiter.get(timeout=self.ack_timeout)
        except queue.Empty as exc:
            self._fail()  # No retry or new waiter after an ambiguous allowance.
            raise TimeoutError("Parent ACK control deadline exhausted") from exc
        if isinstance(result, BaseException):
            raise result
        try:
            row = check_permission(
                result,
                seq=seq,
                payload=payload,
                plan_sha256=self.binding.plan_sha256,
                estimate=estimate,
            )
        except SendPermissionDenied:
            self.sending_closed = True
            raise
        except BaseException:
            self._fail()
            raise
        return Permit(
            estimate.request_sha256,
            row,
            live=lambda: not self.dead.is_set() and not self.sending_closed,
            expires_at=time.monotonic() + self.ack_timeout,
        )

    def finish(self, evidence_sha256: str, *, native_exit: int) -> None:
        if not hex_value(evidence_sha256, 64) or type(native_exit) is not int:
            raise ValueError("Explicit evidence/CLI terminal required")
        with self.write_lock:
            with self.route_lock:
                if self.pending or self.dead.is_set():
                    raise ValueError("Cannot finish ambiguous private control")
            self.seq += 1
            self.finishing = True
            waiter: queue.Queue[Any] = queue.Queue()
            with self.route_lock:
                self.pending[self.seq] = waiter
            write_packet(
                self.writer,
                dict(
                    protocol=PROTOCOL,
                    kind="done",
                    seq=self.seq,
                    binding_sha256=self.binding.digest,
                    data=dict(evidence_sha256=evidence_sha256, native_exit=native_exit),
                ),
            )
            self.writer.close()
        try:
            terminal = waiter.get(timeout=self.ack_timeout)
        except queue.Empty as error:
            self._fail()
            raise TimeoutError("Parent terminal ACK not observed") from error
        if terminal != dict(evidence_sha256=evidence_sha256, native_exit=native_exit):
            raise InvalidControlFrame("Parent terminal ACK differs")
        self.thread.join(timeout=self.ack_timeout)
        if self.thread.is_alive():
            raise TimeoutError("Parent control EOF not observed after terminal")
        self.reader.close()

    def failure(self, details: dict[str, Any]) -> None:
        with self.write_lock:
            if self.finishing:
                return
            self._fail()
            self.seq += 1
            write_packet(
                self.writer,
                dict(
                    protocol=PROTOCOL,
                    kind="fault",
                    seq=self.seq,
                    binding_sha256=self.binding.digest,
                    data=details,
                ),
            )


class ParentBroker:
    def __init__(
        self,
        reader: BinaryIO,
        writer: BinaryIO,
        *,
        binding: Binding,
        policy: WirePolicy,
        key: str,
        journal: ReservationJournal,
        before_allow: Callable[[], None] = lambda: None,
    ) -> None:
        if journal.plan_sha256 != binding.plan_sha256:
            raise ValueError("Parent reservation plan differs")
        self.reader, self.writer, self.binding = reader, writer, binding
        self.policy, self.key, self.journal = policy, key, journal
        self.before_allow = before_allow
        self.last_seq = 0
        self.complete = False
        self.admission_stopped = False
        self.done: dict[str, Any] | None = None
        self.fault: dict[str, Any] | None = None

    def serve(self) -> None:
        while True:
            try:
                packet = read_packet(self.reader)
            except EOFError:
                self.complete = self.done is not None
                return
            if (
                set(packet) != {"protocol", "kind", "seq", "binding_sha256", "data"}
                or packet["kind"] not in {"reserve", "done", "fault"}
                or packet["seq"] != self.last_seq + 1
                or packet["binding_sha256"] != self.binding.digest
            ):
                raise InvalidControlFrame("Child scope/order differs")
            self.last_seq = packet["seq"]
            if self.done is not None:
                raise InvalidControlFrame("Control traffic after terminal")
            if packet["kind"] == "fault":
                details = packet["data"]
                expected = {
                    "error_type",
                    "phase",
                    "last_successful_ordinal",
                    "last_event_sha256",
                    "raw_prefixes",
                    "continuation_forbidden",
                }
                if (
                    not isinstance(details, dict)
                    or set(details) != expected
                    or type(details["error_type"]) is not str
                    or len(details["error_type"]) > 80
                    or details["phase"] not in {"metadata", "body", "summary", "close"}
                    or type(details["last_successful_ordinal"]) is not int
                    or details["last_successful_ordinal"] < 0
                    or not hex_value(details["last_event_sha256"], 64)
                    or not isinstance(details["raw_prefixes"], list)
                    or len(details["raw_prefixes"]) > 4
                    or details["continuation_forbidden"] is not True
                ):
                    raise InvalidControlFrame("Invalid independent archive fault frame")
                self.fault = details
                for prefix in details["raw_prefixes"]:
                    if (
                        not isinstance(prefix, dict)
                        or set(prefix)
                        != {
                            "body_start",
                            "file",
                            "stored_bytes",
                            "stored_sha256",
                            "observed_bytes",
                            "persistence_unknown",
                        }
                        or type(prefix["body_start"]) is not int
                        or prefix["body_start"] <= 0
                        or type(prefix["stored_bytes"]) is not int
                        or prefix["stored_bytes"] < 0
                        or not hex_value(prefix["stored_sha256"], 64)
                        or type(prefix["observed_bytes"]) is not int
                        or prefix["observed_bytes"] < 0
                        or type(prefix["persistence_unknown"]) is not bool
                        or prefix["file"] != f"body-{prefix['body_start']:06d}.bin"
                    ):
                        self.fault = None
                        raise InvalidControlFrame("Invalid archive prefix observation")
                raise RuntimeError("Child archive failed: no further allowance")
            if packet["kind"] == "done":
                data = packet["data"]
                if (
                    not isinstance(data, dict)
                    or set(data) != {"evidence_sha256", "native_exit"}
                    or not hex_value(data["evidence_sha256"], 64)
                    or type(data["native_exit"]) is not int
                ):
                    raise InvalidControlFrame("Invalid child control terminal")
                self.done = data
                write_packet(
                    self.writer,
                    dict(
                        protocol=PROTOCOL,
                        kind="finished",
                        seq=packet["seq"],
                        binding_sha256=self.binding.digest,
                        data=data,
                    ),
                )
                continue
            if self.admission_stopped:
                raise InvalidControlFrame("Reservation after a final budget denial")
            _, estimate = validate_request(
                packet["data"], self.policy, expected_key=self.key, now=datetime.now(timezone.utc)
            )
            self.before_allow()
            row = self.journal.reserve(
                packet["data"], estimate, seq=packet["seq"], now=datetime.now(timezone.utc)
            )
            if not row["allowed"]:
                self.admission_stopped = True
            reply = dict(
                protocol=PROTOCOL,
                kind="permission",
                seq=packet["seq"],
                nonce=packet["data"]["nonce"],
                request_sha256=estimate.request_sha256,
                plan_sha256=self.binding.plan_sha256,
                allowed=row["allowed"],
                reservation_row_hash=row["row_hash"],
                reserve_cny=row["reserve_cny"],
                reason=row["reason"],
            )
            write_packet(
                self.writer,
                dict(
                    protocol=PROTOCOL,
                    kind="permission",
                    seq=packet["seq"],
                    binding_sha256=self.binding.digest,
                    data=reply,
                ),
            )
