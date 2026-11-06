from datetime import datetime, timedelta, timezone
import pytest
from scripts.real_ai_control.journal import ReservationJournal


class Spy:
    def __init__(self, inner, calls):
        self.inner, self.calls = inner, calls

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def read(self, size=-1):
        self.calls.append(size)
        return self.inner.read(size)


def subject(path):
    return ReservationJournal(
        path,
        plan_sha256="1" * 64,
        max_requests=64,
        max_cny="12.39",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def test_normal_verification_never_uses_an_unbounded_read(tmp_path):
    with subject(tmp_path / "ledger") as ledger:
        calls = []
        ledger.stream = Spy(ledger.stream, calls)
        assert ledger.verify()["rows"] == 0
        assert calls and all(0 <= n <= 131073 for n in calls)


def test_oversized_sparse_extension_is_rejected_before_body_allocation(tmp_path):
    with subject(tmp_path / "ledger") as ledger:
        with ledger.path.open("r+b") as f:
            f.truncate(131073)
        calls = []
        ledger.stream = Spy(ledger.stream, calls)
        with pytest.raises(ValueError):
            ledger.verify()
        assert calls == [] and ledger.poisoned is True
