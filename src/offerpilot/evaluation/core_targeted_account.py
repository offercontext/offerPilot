"""File-backed authority, single-use claims and conservative shared allocations.

Trust starts at the OS-account-owned registry, never at a CLI-selected account.
Hashes bind evidence; they do not authenticate a hostile same-user administrator.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import json
import importlib
import os
from pathlib import Path
import re
from typing import Any, Callable, Iterator

from offerpilot.evaluation.core_targeted_evidence import read
from offerpilot.evaluation.core_targeted_trust import require_owned
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.reporting import atomic_json

ZERO = "0" * 64
SESSION_CEILING = Decimal("2.394210")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def slug(value: str) -> str:
    require(bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value)), "unsafe authority identifier")
    return value


def safe_path(path: Path) -> Path:
    """Refuse linked authority/account ancestors, including Windows junctions."""
    import stat

    absolute = path.absolute()
    for entry in [absolute, *absolute.parents]:
        if not entry.exists():
            continue
        info = entry.lstat()
        require(not entry.is_symlink() and not (
            getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        ), "linked authority/account path")
    require(absolute.resolve() == absolute, "noncanonical authority/account path")
    return absolute


def bound_file(ref: dict[str, Any]) -> Path:
    path = safe_path(Path(ref["path"]))
    require(path.is_file() and file_hash(path) == ref["sha256"], "bound authority file changed")
    return path


def write_once(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def paid_lock(path: Path) -> Iterator[None]:
    """The legacy shared account's exact byte lock, held before the session lock."""
    safe_path(path)
    with path.open("a+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl: Any = importlib.import_module("fcntl")
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def ledger_total(path: Path) -> tuple[Decimal, int]:
    safe_path(path)
    raw = path.read_bytes()
    require(not raw or raw.endswith(b"\n"), "partial ledger; reservation remains unknown")
    total, count = Decimal(0), 0
    for line in raw.splitlines():
        row = json.loads(line)
        amount = Decimal(row["reserve_cny"])
        require(type(row["allowed"]) is bool and amount.is_finite() and amount >= 0,
                "invalid historical ledger")
        if row["allowed"]:
            total += amount
            count += 1
    return total, count


class AuthorityRegistry:
    """Root is injected only by internal assembly; production uses authority_home()."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def check(self, path: Path) -> Path:
        root = safe_path(self.root)
        target = safe_path(path)
        require(target.is_relative_to(root), "authority file escapes registry")
        require_owned(root)
        for entry in [*reversed(target.relative_to(root).parents), target.relative_to(root)]:
            child = root / entry
            if child.exists():
                require_owned(child)
        return target

    def authority(self, identifier: str) -> dict[str, Any]:
        config_path = self.check(self.root / "targeted-authorities" / (slug(identifier) + ".json"))
        config = read(config_path)
        require(config.get("schema_version") == 3 and config.get("authority_id") == identifier,
                "authority registry binding invalid")
        original_account = Path(config["account_path"])
        require(original_account.is_absolute(), "account binding requires a canonical absolute path")
        account = safe_path(original_account)
        require(str(account) == config["account_path"], "account path spelling must be canonical")
        require_owned(account.parent)
        require_owned(account)
        require(account.is_absolute() and account.is_file(), "authorized account missing")
        require(Decimal(config["authorized_cny"]) == 10 and
                0 < Decimal(config["max_session_cny"]) <= SESSION_CEILING,
                "authority total/session cap differs")
        bound_file(config["authorization_evidence"])
        return config

    def approval(self, identifier: str) -> dict[str, Any]:
        path = self.check(self.root / "targeted-approvals" / (slug(identifier) + ".json"))
        value = read(path)
        require(value.get("schema_version") == 3 and value.get("authorization_id") == identifier,
                "targeted authorization missing or invalid")
        bound_file(value["authorization_evidence"])
        return value

    def claims(self, authorization_id: str, grant_id: str, binding: dict[str, Any]) -> Path:
        auth = self.root / "targeted-claims" / slug(authorization_id)
        grant = self.root / "grant-claims" / (grant_id + ".json")
        own = auth / "claim.json"
        for path in (own, grant, auth / "spend-anchor.json"):
            self.check(path)
        existing = own.exists() or grant.exists() or auth.exists()
        if existing:
            require(own.is_file() and grant.is_file() and read(own) == binding and read(grant) == binding,
                    "authorization/grant claimed elsewhere or partial claim; audit required")
            require((auth / "spend-anchor.json").is_file(), "trusted spend anchor missing")
        else:
            # Partial creation deliberately blocks recovery; never infer an unspent grant.
            auth.mkdir(parents=True)
            write_once(own, binding)
            write_once(grant, binding)
            write_once(auth / "spend-anchor.json", dict(claim_hash=digest(binding), ledger=None))
        return auth


class FileBudgetAccount:
    """Legacy history stays byte-for-byte unchanged; new transactions append receipts.

    Any intent lacking its matching receipt is a durable stop condition. No new
    session may clear it or reconstruct an allocation after an interrupted commit.
    Caller holds paid_lock for the entire controller/child lifetime.
    """

    def __init__(self, registry: AuthorityRegistry, config: dict[str, Any], *,
                 fault: Callable[[str], None] | None = None) -> None:
        self.registry, self.config, self.fault = registry, config, fault
        self.path = safe_path(Path(config["account_path"]))
        self.transactions = registry.root / "targeted-account-journal" / slug(config["authority_id"])

    @property
    def lock_path(self) -> Path:
        return self.path.with_name("task-budget-paid.lock")

    def checkpoint(self, name: str) -> None:
        if self.fault:
            self.fault(name)

    def inspect(self) -> tuple[dict[str, Any], dict[str, Any] | None, int, str]:
        self.registry.check(self.transactions)
        safe_path(self.path)
        require_owned(self.path.parent)
        require_owned(self.path)
        expected = self.config["genesis_account_sha256"]
        previous = ZERO
        last = None
        intents = sorted(self.transactions.glob("*.intent.json"))
        receipts = sorted(self.transactions.glob("*.receipt.json"))
        require(len(intents) == len(receipts), "uncommitted account transaction; audit required")
        for number, (intent_path, receipt_path) in enumerate(zip(intents, receipts), 1):
            self.registry.check(intent_path)
            self.registry.check(receipt_path)
            stem = f"{number:06d}"
            require(intent_path.name == stem + ".intent.json" and
                    receipt_path.name == stem + ".receipt.json", "account journal sequence missing")
            intent, receipt = read(intent_path), read(receipt_path)
            require(intent["sequence"] == number and intent["account_before"] == expected and
                    intent["previous_receipt"] == previous and
                    receipt == dict(intent_hash=file_hash(intent_path), account_after=intent["account_after"]),
                    "account journal binding changed")
            require(intent["account_path"] == str(self.path) and
                    intent["authority_hash"] == digest(self.config), "account authority changed")
            expected, previous, last = intent["account_after"], file_hash(receipt_path), intent
        require(file_hash(self.path) == expected, "account changed outside bound journal; audit required")
        saved = read(self.path)
        require(Decimal(saved["authorized_cny"]) == 10 and Decimal(saved["refund_cny"]) == 0,
                "total authorization/refund changed")
        total, seen = Decimal(0), set()
        for record in saved["closed_runs"]:
            ledger = safe_path(Path(record["path"]))
            require(record["state"] == "closed" and str(ledger) not in seen and
                    file_hash(ledger) == record["hash"], "duplicate/changed closed ledger")
            amount, count = ledger_total(ledger)
            require(amount == Decimal(record["retained_cny"]) and count == record["allowed_requests"],
                    "historical reservations differ")
            if "report" in record:
                bound_file(record["report"])
            total += amount
            seen.add(str(ledger))
        allocated = Decimal(saved["allocated_cny"])
        require(0 <= allocated <= SESSION_CEILING and Decimal(saved["retained_cny"]) == total and
                Decimal(saved["unallocated_cny"]) == Decimal(10) - total - allocated,
                "shared account conservation failed")
        if allocated:
            require(last is not None and last["kind"] == "allocate" and
                    Decimal(last["cap_cny"]) == allocated, "unbound open allocation")
        elif last:
            require(last["kind"] == "close", "allocation receipt/account differs")
        return saved, last, len(intents), previous

    def commit(self, saved: dict[str, Any], after: dict[str, Any], *, kind: str,
               binding: str, cap: Decimal) -> None:
        current, _, count, previous = self.inspect()
        require(current == saved, "account version changed before transaction")
        # atomic_json's bytes are frozen using the same serializer as the actual replacement.
        encoded = json.dumps(after, ensure_ascii=False, indent=2, sort_keys=True).replace("\n", os.linesep).encode()
        import hashlib
        intent = dict(sequence=count + 1, kind=kind, session_binding=binding, cap_cny=str(cap),
                      account_path=str(self.path), authority_hash=digest(self.config),
                      account_before=file_hash(self.path), account_after=hashlib.sha256(encoded).hexdigest(),
                      previous_receipt=previous)
        base = self.transactions / f"{count + 1:06d}"
        self.checkpoint("before_intent")
        write_once(base.with_suffix(".intent.json"), intent)
        self.checkpoint("after_intent")
        atomic_json(self.path, after)
        self.checkpoint("after_account_replace")
        require(file_hash(self.path) == intent["account_after"], "account serializer differs")
        write_once(base.with_suffix(".receipt.json"), dict(
            intent_hash=file_hash(base.with_suffix(".intent.json")), account_after=intent["account_after"]))
        self.checkpoint("after_receipt")

    def allocate(self, binding: str, cap: Decimal) -> str:
        saved, last, _, _ = self.inspect()
        if last and last["session_binding"] == binding:
            return str(last["kind"])
        require(Decimal(saved["allocated_cny"]) == 0, "another session allocation remains open")
        require(0 < cap <= min(Decimal(saved["unallocated_cny"]),
                             Decimal(self.config["max_session_cny"]), SESSION_CEILING),
                "insufficient latest shared balance/session cap")
        after = dict(saved, allocated_cny=str(cap),
                     unallocated_cny=str(Decimal(saved["unallocated_cny"]) - cap))
        self.commit(saved, after, kind="allocate", binding=binding, cap=cap)
        return "allocate"

    def assert_open(self, binding: str, cap: Decimal) -> None:
        saved, last, _, _ = self.inspect()
        require(last is not None and last["kind"] == "allocate" and
                last["session_binding"] == binding and Decimal(saved["allocated_cny"]) == cap,
                "session allocation not open")

    def close(self, binding: str, cap: Decimal, ledger: Path, *, exit_proven: bool,
              report: dict[str, str] | None = None) -> None:
        require(exit_proven, "owned process/control exit not proven; allocation remains open")
        self.assert_open(binding, cap)
        saved, _, _, _ = self.inspect()
        amount, count = ledger_total(ledger)
        require(amount <= cap and count <= 27, "close exceeds allocation")
        record = dict(path=str(ledger), hash=file_hash(ledger), retained_cny=str(amount),
                      allowed_requests=count, state="closed", session_binding=binding,
                      accounting="conservative-reservation-not-actual-bill")
        if report is not None:
            bound_file(report)
            record["report"] = report
        require(all(r["path"] != str(ledger) for r in saved["closed_runs"]), "duplicate close")
        after = dict(saved, allocated_cny="0", retained_cny=str(Decimal(saved["retained_cny"]) + amount),
                     unallocated_cny=str(Decimal(saved["unallocated_cny"]) + cap - amount),
                     closed_runs=[*saved["closed_runs"], record])
        self.commit(saved, after, kind="close", binding=binding, cap=cap)
