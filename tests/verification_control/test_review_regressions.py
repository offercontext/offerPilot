"""S05–S08 regression boundaries with disposable ledgers/source trees."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess

import pytest

from scripts.real_ai_control.account import AccountSession, read
from scripts.real_ai_control.archive import ArchiveIncomplete, EvidenceArchive
from scripts.real_ai_control.journal import ReservationJournal
from scripts.real_ai_control.protocol import sha
from scripts.real_ai_control.private import remove_owned_config_copies, work_path
from scripts.real_ai_control.source import implementation, product_source
from .test_account_archive import PLAN, account, closure_ref, proposal


@pytest.mark.parametrize("tamper", ["truncate", "replace"])
def test_verify_then_tamper_cannot_settle_as_zero_or_release_allowed_fees(tmp_path, tamper):
    path = account(tmp_path)
    with AccountSession(
        path,
        tmp_path / "tx",
        expected_sha256=sha(path.read_bytes()),
        authorized_cny="57.605790",
        cap_cny="12.39",
        prior_journals=(),
    ) as bank:
        with ReservationJournal(
            tmp_path / "ledger",
            plan_sha256=PLAN,
            max_requests=64,
            max_cny="12.39",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ) as ledger:
            payload, estimate, now = proposal()
            ledger.reserve(payload, estimate, seq=1, now=now)
            ref = closure_ref(tmp_path, ledger)
            if tamper == "truncate":
                ledger.path.write_bytes(b"")
            else:
                other = tmp_path / "replacement"
                other.write_bytes(b"")
                try:
                    other.replace(ledger.path)
                except PermissionError:
                    # Windows may enforce held-file identity at the OS boundary.
                    bank.close_allocation(ledger, plan_sha256=PLAN, closure=ref)
                    assert read(path)["allocated_cny"] == "0"
                    assert float(read(path)["retained_cny"]) > 0
                    assert read(path)["refund_cny"] == "0"
                    return
            with pytest.raises(ValueError):
                bank.close_allocation(ledger, plan_sha256=PLAN, closure=ref)
    assert read(path)["allocated_cny"] == "12.39" and read(path)["refund_cny"] == "0"


def test_release_intent_already_binds_fsynced_closure_before_account_write(tmp_path):
    path = account(tmp_path)
    seen = []
    with AccountSession(
        path,
        tmp_path / "tx",
        expected_sha256=sha(path.read_bytes()),
        authorized_cny="57.605790",
        cap_cny="12.39",
        prior_journals=(),
    ) as bank:
        with ReservationJournal(
            tmp_path / "ledger",
            plan_sha256=PLAN,
            max_requests=64,
            max_cny="12.39",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ) as ledger:
            payload, estimate, now = proposal()
            ledger.reserve(payload, estimate, seq=1, now=now)
            ref = closure_ref(tmp_path, ledger)

            def observe(phase):
                if phase == "intent":
                    value = read(tmp_path / "tx/0002.intent.json")
                    assert value["after"]["closed_runs"][-1]["closure_manifest"] == ref
                    assert sha(Path(ref["path"]).read_bytes()) == ref["sha256"]
                    seen.append(phase)

            bank.fault = observe
            bank.close_allocation(ledger, plan_sha256=PLAN, closure=ref)
    assert seen == ["intent"]
    assert read(path)["closed_runs"][0]["closure_manifest"] == ref


def test_first_metadata_failure_has_independent_prefix_free_fault_and_safe_summary(tmp_path):
    def fault(phase):
        if phase == "metadata":
            raise OSError("offline disk failure")

    archive = EvidenceArchive(tmp_path / "archive", secrets=(), fault=fault)
    statuses = []
    archive.failure_sink = statuses.append
    with pytest.raises(ArchiveIncomplete):
        archive.event("first")
    assert len(statuses) == 1 and statuses[0]["phase"] == "metadata"
    assert (
        statuses[0]["last_successful_ordinal"] == 0 and statuses[0]["last_event_sha256"] == "0" * 64
    )
    assert statuses[0]["raw_prefixes"] == [] and statuses[0]["continuation_forbidden"] is True
    assert archive.summary()["complete"] is False


def test_product_or_dependency_changes_do_not_hide_behind_unchanged_controller_hash(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for rel in (
        "src/offerpilot/cli.py",
        "scripts/evaluation_subject/process_tree.py",
        "scripts/real_ai_control/__init__.py",
        "pyproject.toml",
        "uv.lock",
        "web/package-lock.json",
    ):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("offline fixture\n")

    def git(*args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init")
    git("add", ".")
    git(
        "-c",
        "user.name=OfflineFixture",
        "-c",
        "user.email=offline@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "test: 离线源绑定夹具",
    )
    first = product_source(root)
    control = implementation(root)
    (root / "src/offerpilot/cli.py").write_text("changed CLI\n")
    assert product_source(root) != first and implementation(root) == control
    second = product_source(root)
    (root / "uv.lock").write_text("changed dependency\n")
    assert product_source(root) != second and implementation(root) == control


def test_unknown_ownership_cannot_remove_private_config_copies(tmp_path):
    output = tmp_path / "evidence"
    output.mkdir()
    root = work_path("offline-" + "a" * 16, output)
    # A unique test root is not created here; do not touch another run's files.
    assert not root.is_relative_to(output)
    result = remove_owned_config_copies(root, owned_tree_exited=None)
    assert result["held_for_audit"] is True and result["removed"] == []


def test_metadata_short_write_is_not_accepted_as_complete_evidence(tmp_path, monkeypatch):
    original = Path.open

    class Short:
        def __init__(self, inner):
            self.inner = inner

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

        def write(self, raw):
            return self.inner.write(raw[: len(raw) // 2])

        def flush(self):
            return self.inner.flush()

        def fileno(self):
            return self.inner.fileno()

    def opened(path, *args, **kwargs):
        inner = original(path, *args, **kwargs)
        return Short(inner) if path.name == "events.jsonl" and args and args[0] == "ab" else inner

    archive = EvidenceArchive(tmp_path / "archive", secrets=())
    statuses = []
    archive.failure_sink = statuses.append
    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(ArchiveIncomplete):
        archive.event("offline-partial-metadata")
    assert archive.summary()["complete"] is False
    assert statuses[0]["phase"] == "metadata"


def test_denied_attempts_also_have_a_bounded_journal_denominator(tmp_path):
    with ReservationJournal(
        tmp_path / "ledger",
        plan_sha256=PLAN,
        max_requests=64,
        max_cny="12.39",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ) as ledger:
        payload, estimate, now = proposal()
        for seq in range(1, 66):
            payload["nonce"] = format(seq, "032x")
            row = ledger.reserve(payload, estimate, seq=seq, now=now)
            assert row["allowed"] is (seq == 1)
        payload["nonce"] = "f" * 32
        with pytest.raises(ValueError):
            ledger.reserve(payload, estimate, seq=66, now=now)
        assert ledger.verify()["rows"] == 65 and ledger.requests == 1
