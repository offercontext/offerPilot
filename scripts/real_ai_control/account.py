"""Shared-account transactions without legacy fixed caps or authority expansion.

Reuses only safe_path, paid_lock, ledger_total and atomic_json. No SharedAccount,
TargetedLedger, authorization migration, recovery replay or historical import.
"""

from __future__ import annotations

import copy
from decimal import Context, Decimal, localcontext
import json
import os
from pathlib import Path
from typing import Any, Callable

from offerpilot.evaluation.core_targeted_account import ledger_total, paid_lock, safe_path
from offerpilot.evaluation.reporting import atomic_json
from .journal import ReservationJournal
from .protocol import canonical, sha


def read(path: Path) -> dict[str, Any]:
    return dict(json.loads(safe_path(path).read_bytes()))


def money(value: Any) -> Decimal:
    if type(value) is not str or len(value) > 32:
        raise ValueError("Bounded decimal string required")
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0:
        raise ValueError("Nonnegative finite budget required")
    return amount


def once(path: Path, value: dict[str, Any]) -> None:
    safe_path(path)
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def check_account(account: dict[str, Any], *, authorized_cny: str) -> None:
    """All historical allowed rows remain charged; old hash-bound formats stay intact."""
    if money(account["authorized_cny"]) != money(authorized_cny) or money(account["refund_cny"]):
        raise ValueError("Frozen original authority differs; expansion/refund forbidden")
    seen: set[Path] = set()
    total = Decimal(0)
    with localcontext(Context(prec=40)):
        for ref in account["closed_runs"]:
            path = safe_path(Path(ref["path"]))
            if ref["state"] != "closed" or path in seen or sha(path.read_bytes()) != ref["hash"]:
                raise ValueError("Historical ledger missing, duplicated, changed or open")
            amount, requests = ledger_total(path)
            if amount != money(ref["retained_cny"]) or requests != ref["allowed_requests"]:
                raise ValueError("Historical allowance totals differ")
            if ref.get("kind") == "bounded-official-smoke":
                closure = ref["closure_manifest"]
                proof_path = safe_path(Path(closure["path"]))
                if sha(proof_path.read_bytes()) != closure["sha256"]:
                    raise ValueError("Historical closure proof changed")
                proof = read(proof_path)
                if proof["held_ledger"]["sha256"] != ref["hash"]:
                    raise ValueError("Historical closure ledger differs")
            seen.add(path)
            total += amount
        if total != money(account["retained_cny"]):
            raise ValueError("Historical allowance sum differs")
        if money(authorized_cny) != total + money(account["allocated_cny"]) + money(
            account["unallocated_cny"]
        ):
            raise ValueError("Shared account conservation failed")


def check_transactions(roots: tuple[Path, ...]) -> None:
    """Roots are frozen in the execution binding, including all prior session journals."""
    seen: set[Path] = set()
    for root in roots:
        root = safe_path(root)
        if root in seen or not root.is_dir():
            raise ValueError("Duplicate transaction root")
        seen.add(root)
        intents = sorted(root.glob("*.intent.json"))
        receipts = sorted(root.glob("*.receipt.json"))
        if len(intents) != len(receipts):
            raise ValueError("Unclosed transaction: preserve allocation for audit")
        for intent, receipt in zip(intents, receipts):
            if intent.name.replace(".intent.", ".receipt.") != receipt.name:
                raise ValueError("Transaction sequence differs")
            a, b = read(intent), read(receipt)
            if b["intent_sha256"] != sha(intent.read_bytes()):
                raise ValueError("Transaction receipt differs")
            for ref_path, ref_sha in (
                (a["before_path"], a["before_sha256"]),
                (b["after_path"], b["after_sha256"]),
            ):
                if sha(safe_path(Path(ref_path)).read_bytes()) != ref_sha:
                    raise ValueError("Transaction snapshot changed")
            if read(Path(b["after_path"])) != a["after"]:
                raise ValueError("Transaction after image differs")


class AccountSession:
    """One allocation under the exact existing account byte lock.

    An exceptional exit releases the OS handle but leaves allocation/intent on disk.
    A later controller refuses that state. There is no automatic rollback/resume.
    """

    def __init__(
        self,
        account: Path,
        journal: Path,
        *,
        expected_sha256: str,
        authorized_cny: str,
        cap_cny: str,
        prior_journals: tuple[Path, ...],
        fault: Callable[[str], None] = lambda _: None,
    ) -> None:
        self.path, self.directory = safe_path(account), safe_path(journal)
        self.lock_path = self.path.with_name("task-budget-paid.lock")
        self.expected = expected_sha256
        self.authorized, self.cap = authorized_cny, money(cap_cny)
        if not 0 < self.cap <= money(authorized_cny):
            raise ValueError("Bounded stage allocation required")
        self.prior, self.fault = prior_journals, fault
        self.entered, self.closed = False, False
        self.transaction_number = 0

    def __enter__(self) -> AccountSession:
        self.lock = paid_lock(self.lock_path)
        self.lock.__enter__()
        try:
            if sha(self.path.read_bytes()) != self.expected:
                raise ValueError("Latest frozen account hash differs")
            check_transactions(self.prior)
            current = read(self.path)
            check_account(current, authorized_cny=self.authorized)
            if money(current["allocated_cny"]) != 0 or money(current["unallocated_cny"]) < self.cap:
                raise ValueError("Open allocation or insufficient balance; no new sending")
            self.directory.mkdir(exist_ok=False)
            after = copy.deepcopy(current)
            after["allocated_cny"] = str(self.cap)
            with localcontext(Context(prec=40)):
                after["unallocated_cny"] = str(money(current["unallocated_cny"]) - self.cap)
            self._transaction("allocate", after)
            self.entered = True
            return self
        except BaseException:
            self.lock.__exit__(None, None, None)
            raise

    def _transaction(
        self, kind: str, after: dict[str, Any], *, guard: Callable[[], None] = lambda: None
    ) -> None:
        if sha(self.path.read_bytes()) != self.expected:
            raise ValueError("Account changed while locked")
        guard()
        self.transaction_number += 1
        prefix = self.directory / f"{self.transaction_number:04d}"
        before, saved = Path(str(prefix) + ".before.json"), Path(str(prefix) + ".after.json")
        intent, receipt = Path(str(prefix) + ".intent.json"), Path(str(prefix) + ".receipt.json")
        with before.open("xb") as stream:
            stream.write(self.path.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        once(
            intent,
            dict(
                kind=kind,
                before_path=str(before),
                before_sha256=sha(before.read_bytes()),
                after=after,
            ),
        )
        self.fault("intent")
        guard()
        atomic_json(self.path, after)
        self.fault("account")
        guard()
        with saved.open("xb") as stream:
            stream.write(self.path.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        once(
            receipt,
            dict(
                intent_sha256=sha(intent.read_bytes()),
                after_path=str(saved),
                after_sha256=sha(saved.read_bytes()),
            ),
        )
        self.expected = sha(self.path.read_bytes())
        self.fault("receipt")

    def close_allocation(
        self, journal: ReservationJournal, *, plan_sha256: str, closure: dict[str, str]
    ) -> dict[str, Any]:
        if not self.entered or self.closed or not isinstance(journal, ReservationJournal):
            raise ValueError("An open allocation and its held journal are required")
        if set(closure) != {"path", "sha256"}:
            raise ValueError("Durable closure manifest reference required")
        path = safe_path(Path(closure["path"]))
        if sha(path.read_bytes()) != closure["sha256"]:
            raise ValueError("Closure proof changed")
        proof = read(path)
        required = ("owned_tree_exited", "control_complete", "ledger_complete", "evidence_complete")
        if (
            proof.get("schema_version") != 3
            or any(proof.get(k) is not True for k in required)
            or proof.get("forced_kill") is not False
            or proof.get("plan_sha256") != plan_sha256
            or journal.plan_sha256 != plan_sha256
        ):
            raise ValueError("Incomplete durable closure; allocation remains held")
        with journal.settlement() as (result, guard):
            if proof.get("held_ledger") != result:
                raise ValueError("Closure proof does not match held fd/tail/fees")
            check_transactions((*self.prior, self.directory))
            current = read(self.path)
            check_account(current, authorized_cny=self.authorized)
            amount = money(result["retained_cny"])
            if (
                amount > self.cap
                or result["allowed_requests"] > 64
                or money(current["allocated_cny"]) != self.cap
            ):
                raise ValueError("Reservation exceeds allocation")
            if any(
                Path(ref["path"]).resolve() == journal.path.resolve()
                for ref in current["closed_runs"]
            ):
                raise ValueError("Ledger already settled")
            after = copy.deepcopy(current)
            with localcontext(Context(prec=40)):
                after["retained_cny"] = str(money(current["retained_cny"]) + amount)
                after["unallocated_cny"] = str(
                    money(current["unallocated_cny"]) + self.cap - amount
                )
            after["allocated_cny"] = "0"
            after["closed_runs"].append(
                dict(
                    path=str(journal.path),
                    hash=result["sha256"],
                    retained_cny=str(amount),
                    allowed_requests=result["allowed_requests"],
                    state="closed",
                    kind="bounded-official-smoke",
                    closure_manifest=dict(closure),
                )
            )
            check_account(after, authorized_cny=self.authorized)

            def before_commit() -> None:
                guard()
                if sha(path.read_bytes()) != closure["sha256"]:
                    raise ValueError("Durable closure changed before account commit")

            self._transaction("close", after, guard=before_commit)
            self.closed = True
            return result

    def __exit__(self, *args: Any) -> None:
        self.lock.__exit__(*args)
