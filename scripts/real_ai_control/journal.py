"""Fresh durable physical-send reservations. Never resumes or refunds an allowance."""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from decimal import Context, Decimal, localcontext
import os
from pathlib import Path
import stat
import threading
from typing import Any, BinaryIO, Callable, Iterator

from offerpilot.evaluation.core_targeted_account import safe_path
from .protocol import canonical, hex_value, sha, unique_pairs, reject_constant
from .wire import WireEstimate

MAX_LEDGER_BYTES = 131072


def bounded_read(stream: BinaryIO, path: Path, identity: tuple[int, int]) -> bytes:
    safe_path(path)
    before, entry = os.fstat(stream.fileno()), path.stat()
    if (
        ReservationJournal._identity(before) != identity
        or ReservationJournal._identity(entry) != identity
        or before.st_size != entry.st_size
        or not 0 <= before.st_size <= MAX_LEDGER_BYTES
    ):
        raise ValueError("Journal identity/size invalid before bounded read")
    stream.seek(0)
    raw = stream.read(MAX_LEDGER_BYTES + 1)
    safe_path(path)
    after, entry_after = os.fstat(stream.fileno()), path.stat()
    if (
        len(raw) > MAX_LEDGER_BYTES
        or len(raw) != before.st_size
        or ReservationJournal._identity(after) != identity
        or ReservationJournal._identity(entry_after) != identity
        or after.st_size != before.st_size
        or entry_after.st_size != before.st_size
    ):
        raise ValueError("Journal identity/size changed during bounded read")
    return raw


class ReservationJournal:
    def __init__(
        self, path: Path, *, plan_sha256: str, max_requests: int, max_cny: str, expires_at: datetime
    ) -> None:
        if (
            not hex_value(plan_sha256, 64)
            or type(max_requests) is not int
            or not 0 < max_requests <= 64
            or type(max_cny) is not str
            or not isinstance(expires_at, datetime)
            or expires_at.utcoffset() is None
        ):
            raise ValueError("Bounded fresh journal scope required")
        cap = Decimal(max_cny)
        if not cap.is_finite() or not 0 < cap <= Decimal("57.605790"):
            raise ValueError(
                "Finite positive cap within original cumulative authorization required"
            )
        self.path = safe_path(path)
        self.plan_sha256 = plan_sha256
        self.max_requests, self.cap, self.expires_at = max_requests, cap, expires_at
        self._rows: list[dict[str, Any]] = []
        self.seen: set[str] = set()
        self.nonces: set[str] = set()
        self.requests, self.last_seq = 0, 0
        self.retained = Decimal("0")
        self.tail = "0" * 64
        self.poisoned = False
        self.settling = False
        self.lock = threading.Lock()
        self.stream = self.path.open("x+b")
        try:
            self.identity = self._identity(os.fstat(self.stream.fileno()))
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self._check_prefix()
        except BaseException:
            self.stream.close()
            raise

    @staticmethod
    def _identity(info: os.stat_result) -> tuple[int, int]:
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Journal must be a private regular file with one name")
        return info.st_dev, info.st_ino

    def _check_prefix(self) -> bytes:
        try:
            if self.poisoned or self.stream.closed:
                raise ValueError("Journal unavailable; audit required")
            safe_path(self.path)
            if (
                self._identity(self.path.stat()) != self.identity
                or self._identity(os.fstat(self.stream.fileno())) != self.identity
            ):
                raise ValueError("Journal path identity changed")
            raw = bounded_read(self.stream, self.path, self.identity)
            expected = b"".join(canonical(row) + b"\n" for row in self._rows)
            if raw != expected:
                raise ValueError("Partial, truncated, extended or changed journal; audit required")
            return raw
        except BaseException:
            self.poisoned = True
            raise

    def reserve(
        self, payload: dict[str, Any], estimate: WireEstimate, *, seq: int, now: datetime
    ) -> dict[str, Any]:
        with self.lock:
            self._check_prefix()  # Before every allowance, not only at settlement.
            if len(self._rows) >= 65:
                raise ValueError("Maximum total reservation attempts reached")
            if self.settling:
                raise ValueError("Journal is sealed for settlement; no more sending")
            if (
                type(seq) is not int
                or seq <= self.last_seq
                or not isinstance(now, datetime)
                or now.utcoffset() is None
                or not isinstance(estimate, WireEstimate)
                or estimate.request_sha256 != payload.get("request_sha256")
                or estimate.body_sha256 != payload.get("body_sha256")
                or not hex_value(payload.get("nonce"), 32)
                or payload["nonce"] in self.nonces
                or type(payload.get("logical_id")) is not str
                or not payload["logical_id"].startswith("completion-")
                or not hex_value(payload["logical_id"][11:], 32)
            ):
                raise ValueError("Fresh validated request/estimate identity required")
            amount = estimate.retained_bound_cny
            if not amount.is_finite() or amount <= 0:
                raise ValueError("Nonzero finite conservative reservation required")
            logical = payload["logical_id"]
            with localcontext(Context(prec=40)):
                total = self.retained + amount
            reason = (
                "logical_completion_already_attempted"
                if logical in self.seen
                else "grant_expired"
                if now.astimezone(timezone.utc) >= self.expires_at
                else "request_cap_exhausted"
                if self.requests >= self.max_requests
                else "money_cap_exhausted"
                if total > self.cap
                else "allowed"
            )
            row = dict(
                schema_version=3,
                kind="official-real-ai-smoke-physical-reservation",
                ordinal=len(self._rows) + 1,
                plan_sha256=self.plan_sha256,
                seq=seq,
                nonce=payload["nonce"],
                logical_id=logical,
                request_sha256=estimate.request_sha256,
                body_sha256=estimate.body_sha256,
                input_bytes=estimate.input_bytes,
                output_cap=estimate.output_cap,
                reserve_cny=str(amount),
                allowed=reason == "allowed",
                reason=reason,
                previous_hash=self.tail,
                at=now.astimezone(timezone.utc).isoformat(),
            )
            row["row_hash"] = sha(canonical(row))
            try:
                raw = canonical(row) + b"\n"
                if self.stream.seek(0, os.SEEK_END) + len(raw) > 131072:
                    raise ValueError("Maximum ledger byte limit reached")
                self.stream.seek(0, os.SEEK_END)
                if self.stream.write(raw) != len(raw):
                    raise OSError("Partial reservation write")
                self.stream.flush()
                os.fsync(self.stream.fileno())
                self._rows.append(row)
                self._check_prefix()  # Recheck both held fd and path before ACK.
            except BaseException:
                self.poisoned = True
                raise
            self.seen.add(logical)
            self.nonces.add(payload["nonce"])
            self.last_seq, self.tail = seq, row["row_hash"]
            if row["allowed"]:
                self.requests += 1
                self.retained = total
            return dict(row)

    def verify(self) -> dict[str, Any]:
        with self.lock:
            raw = self._check_prefix()
            return dict(
                rows=len(self._rows),
                allowed_requests=self.requests,
                retained_cny=str(self.retained),
                tail=self.tail,
                sha256=sha(raw),
                invoice_verified=False,
                dispatch_proven=False,
            )

    def close(self) -> None:
        self.stream.close()

    @contextmanager
    def settlement(self) -> Iterator[tuple[dict[str, Any], Callable[[], None]]]:
        with self.lock:
            self.settling = True
            raw = self._check_prefix()
            proof = verify_bytes(raw, plan_sha256=self.plan_sha256)
            if (
                proof["allowed_requests"] != self.requests
                or Decimal(proof["retained_cny"]) != self.retained
            ):
                self.poisoned = True
                raise ValueError("Held ledger totals differ")

            def check() -> None:
                if sha(self._check_prefix()) != proof["sha256"]:
                    raise ValueError("Held ledger changed during settlement")

            try:
                yield proof, check
            finally:
                check()

    def __enter__(self) -> ReservationJournal:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def verify_ledger(path: Path, *, plan_sha256: str) -> dict[str, Any]:
    """Independent readback, including chain/order/identity, not only its sum."""
    with safe_path(path).open("rb") as stream:
        identity = ReservationJournal._identity(os.fstat(stream.fileno()))
        return verify_bytes(bounded_read(stream, path, identity), plan_sha256=plan_sha256)


def verify_bytes(raw: bytes, *, plan_sha256: str) -> dict[str, Any]:
    import json

    if len(raw) > 131072 or len(raw.splitlines()) > 65:
        raise ValueError("Ledger exceeds bounded attempts/bytes")
    if raw and not raw.endswith(b"\n"):
        raise ValueError("Partial ledger tail; audit required")
    tail, seen, nonces, seq = "0" * 64, set(), set(), 0
    total, count = Decimal(0), 0
    lines = raw.splitlines()
    for ordinal, line in enumerate(lines, 1):
        row = json.loads(line, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
        hashed = row.pop("row_hash")
        if (
            row["ordinal"] != ordinal
            or row["previous_hash"] != tail
            or sha(canonical(row)) != hashed
            or row["plan_sha256"] != plan_sha256
            or type(row["seq"]) is not int
            or row["seq"] <= seq
            or not hex_value(row["nonce"], 32)
            or row["nonce"] in nonces
            or type(row["allowed"]) is not bool
        ):
            raise ValueError("Invalid reservation chain or identity")
        amount = Decimal(row["reserve_cny"])
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Invalid reservation amount")
        if row["allowed"]:
            if row["logical_id"] in seen or row["reason"] != "allowed":
                raise ValueError("Repeated logical dispatch")
            with localcontext(Context(prec=40)):
                total += amount
            count += 1
        seen.add(row["logical_id"])
        nonces.add(row["nonce"])
        tail, seq = hashed, row["seq"]
    return dict(
        rows=len(lines),
        allowed_requests=count,
        retained_cny=str(total),
        tail=tail,
        sha256=sha(raw),
        invoice_verified=False,
        dispatch_proven=False,
    )
