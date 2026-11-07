"""Disposable-budget and actual-byte capture failures; never opens the shared account."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from scripts.real_ai_control.account import AccountSession, check_account, once, read
from scripts.real_ai_control.archive import ArchiveIncomplete, EvidenceArchive, TeeStream
from scripts.real_ai_control.archive import captured_response
from scripts.real_ai_control.journal import ReservationJournal, verify_ledger
from scripts.real_ai_control.protocol import reservation_payload, sha
from scripts.real_ai_control.wire import WirePolicy

PLAN = "1" * 64
KEY = "sk-offline-test"
CLOSURE = dict(
    owned_tree_exited=True,
    control_complete=True,
    ledger_complete=True,
    evidence_complete=True,
    forced_kill=False,
)


def account(root: Path) -> Path:
    path = root / "fake-account.json"
    once(
        path,
        dict(
            authorized_cny="57.605790",
            retained_cny="0",
            allocated_cny="0",
            unallocated_cny="57.605790",
            refund_cny="0",
            closed_runs=[],
            authorization_history=["unchanged"],
        ),
    )
    return path


def proposal(index=1):
    now = datetime.now(timezone.utc)
    policy = WirePolicy(64000, 8192, "2", "8", now + timedelta(hours=1))
    request = httpx.Request(
        "POST",
        "https://api.deepseek.com/v1/chat/completions",
        json=dict(
            model="deepseek-flash",
            messages=[dict(role="user", content="offline")],
            max_tokens=8192,
            temperature=0,
            thinking={"type": "disabled"},
        ),
        headers={"Authorization": "Bearer " + KEY},
    )
    estimate = policy.validate(request, expected_key=KEY, now=now)
    payload = reservation_payload(
        request,
        estimate,
        expected_key=KEY,
        logical_id="completion-" + format(index, "032x"),
        nonce=format(index, "032x"),
    )
    return payload, estimate, now


def closure_ref(root, journal, changes=None):
    proof = dict(schema_version=3, plan_sha256=PLAN, held_ledger=journal.verify(), **CLOSURE)
    proof.update(changes or {})
    path = root / f"closure-{len(list(root.glob('closure-*.json')))}.json"
    once(path, proof)
    return dict(path=str(path), sha256=sha(path.read_bytes()))


def test_sixty_four_budgeted_attempts_and_sixty_fifth_denial_are_durable(tmp_path):
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
            for number in range(1, 66):
                payload, estimate, now = proposal(number)
                row = ledger.reserve(payload, estimate, seq=number, now=now)
                assert row["allowed"] is (number <= 64)
            verified = verify_ledger(ledger.path, plan_sha256=PLAN)
            assert verified["allowed_requests"] == 64
            assert verified == ledger.verify()
            settled = bank.close_allocation(
                ledger, plan_sha256=PLAN, closure=closure_ref(tmp_path, ledger)
            )
            assert settled["allowed_requests"] == 64
            with pytest.raises(ValueError):
                bank.close_allocation(
                    ledger, plan_sha256=PLAN, closure=closure_ref(tmp_path, ledger)
                )
    saved = read(path)
    assert saved["authorization_history"] == ["unchanged"] and saved["refund_cny"] == "0"
    assert Decimal(saved["retained_cny"]) > 0 and saved["allocated_cny"] == "0"
    check_account(saved, authorized_cny="57.605790")


@pytest.mark.parametrize("phase", ["intent", "account", "receipt"])
def test_transaction_interruption_is_not_replayed_or_refunded(tmp_path, phase):
    path = account(tmp_path)

    def fault(stage):
        if stage == phase:
            raise OSError("injected transaction interruption")

    with pytest.raises(OSError):
        with AccountSession(
            path,
            tmp_path / "tx",
            expected_sha256=sha(path.read_bytes()),
            authorized_cny="57.605790",
            cap_cny="12.39",
            prior_journals=(),
            fault=fault,
        ):
            pytest.fail("Fault must prevent launch")
    old = path.read_bytes()
    with pytest.raises(ValueError):
        with AccountSession(
            path,
            tmp_path / "new",
            expected_sha256=sha(old),
            authorized_cny="57.605790",
            cap_cny="12.39",
            prior_journals=(tmp_path / "tx",),
        ):
            pytest.fail("Interrupted allocation must block")
    assert path.read_bytes() == old and read(path)["refund_cny"] == "0"


@pytest.mark.parametrize(
    "signal",
    [
        "owned_tree_exited",
        "control_complete",
        "ledger_complete",
        "evidence_complete",
        "forced_kill",
    ],
)
def test_incomplete_exit_or_capture_never_releases_allocation(tmp_path, signal):
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
            closure = {**CLOSURE, signal: True if signal == "forced_kill" else None}
            with pytest.raises(ValueError):
                bank.close_allocation(
                    ledger, plan_sha256=PLAN, closure=closure_ref(tmp_path, ledger, closure)
                )
    assert read(path)["allocated_cny"] == "12.39"


def test_changed_historical_ledger_and_old_authority_are_rejected_without_mutation(tmp_path):
    path = account(tmp_path)
    original = path.read_bytes()
    for authority in ("10", "60"):
        with pytest.raises(ValueError):
            with AccountSession(
                path,
                tmp_path / "tx",
                expected_sha256=sha(original),
                authorized_cny=authority,
                cap_cny="12.39",
                prior_journals=(),
            ):
                pass
        assert path.read_bytes() == original
    old = tmp_path / "history.jsonl"
    old.write_text('{"allowed":true,"reserve_cny":"2"}\n')
    saved = read(path)
    saved.update(
        retained_cny="2",
        unallocated_cny="55.605790",
        closed_runs=[
            dict(
                path=str(old),
                hash=sha(old.read_bytes()),
                state="closed",
                retained_cny="2",
                allowed_requests=1,
            )
        ],
    )
    path.write_text(json.dumps(saved))
    old.write_text('{"allowed":true,"reserve_cny":"0"}\n')
    frozen = path.read_bytes()
    with pytest.raises(ValueError):
        with AccountSession(
            path,
            tmp_path / "tx",
            expected_sha256=sha(frozen),
            authorized_cny="57.605790",
            cap_cny="12.39",
            prior_journals=(),
        ):
            pass
    assert path.read_bytes() == frozen


def test_archived_stream_preserves_order_hash_and_split_secret_protection(tmp_path):
    archive = EvidenceArchive(tmp_path / "archive", secrets=(KEY,))
    capture = archive.body("provider", links={"physical": "p1"})
    chunks = [b"a", KEY[:5].encode(), KEY[5:].encode(), b"z"]
    for chunk in chunks:
        capture.feed(chunk)
    final = capture.end(complete=True)
    assert final["observed_sha256"] == hashlib.sha256(b"".join(chunks)).hexdigest()
    assert capture.path.read_bytes() == b"a[REDACTED]z"
    assert final["raw_fidelity"] is False and final["literal_secret_redacted"] is True
    assert archive.summary()["complete"] is True
    assert all(KEY.encode() not in p.read_bytes() for p in archive.root.iterdir())


@pytest.mark.parametrize("failure", ["cap", "disk", "partial"])
def test_failed_capture_keeps_prefix_and_denies_complete_claim(tmp_path, failure):
    def fault(stage):
        if stage == "body" and failure == "disk":
            raise OSError("injected disk failure")

    archive = EvidenceArchive(
        tmp_path / "archive",
        secrets=(),
        entry_limit=1024 if failure == "cap" else 10000,
        total_limit=20000,
        fault=fault,
    )
    capture = archive.body("response", links={})
    if failure == "partial":
        capture.feed(b"prefix")
        capture.end(complete=False, error_type="ReadTimeout")
    else:
        with pytest.raises(ArchiveIncomplete):
            capture.feed(b"prefix followed by data" + b"x" * 1024)
        capture.end(complete=False, error_type="ArchiveIncomplete")
    assert archive.summary()["complete"] is False
    assert capture.path.exists()
    if failure == "cap":
        assert capture.path.read_bytes() == (b"prefix followed by data" + b"x" * 1024)[:1024]


def test_stream_close_before_eof_keeps_bytes_and_unknown_terminal(tmp_path):
    archive = EvidenceArchive(tmp_path / "archive", secrets=())

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"part1"
            yield b"part2"

        def close(self):
            pass

    capture = archive.body("response", links={})
    stream = TeeStream(Stream(), capture)
    iterator = iter(stream)
    assert next(iterator) == b"part1"
    stream.close()
    assert capture.path.read_bytes() == b"part1"
    assert archive.summary()["complete"] is False


def test_metadata_excludes_sdk_hidden_credentials_and_keeps_actual_assistant_fields(tmp_path):
    archive = EvidenceArchive(tmp_path / "archive", secrets=(KEY,))
    value = dict(
        content="reply",
        tool_calls=[dict(id="t1", name="write", args="{}")],
        provider_blocks={"request_id": "provider1"},
        _hidden_params={"api_key": KEY},
    )
    row = archive.event("product-assistant-result", value=value)
    assert (
        row["value"]["content"] == "reply"
        and row["value"]["provider_blocks"]["request_id"] == "provider1"
    )
    assert row["value"]["tool_calls"][0]["id"] == "t1"
    assert "_hidden_params" not in row["value"]


def test_already_buffered_mock_response_is_captured_without_an_unfinishable_body(tmp_path):
    archive = EvidenceArchive(tmp_path / "archive", secrets=())
    response = httpx.Response(503, json={"error": "offline fixture"})
    original = response.content
    captured_response(response, archive, kind="provider-response", links={})
    assert response.content == original
    assert archive.summary()["active_bodies"] == 0
    assert archive.summary()["complete"] is True


def test_actual_httpx_entity_eof_and_close_failure_are_separate_observations(tmp_path):
    archive = EvidenceArchive(tmp_path / "archive", secrets=())

    class Source(httpx.SyncByteStream):
        def __iter__(self):
            yield b"first"
            yield b"second"

        def close(self):
            raise OSError("offline response close failure")

    response = captured_response(
        httpx.Response(200, stream=Source()), archive, kind="response", links={}
    )
    iterator = iter(response.stream)
    assert b"".join(iterator) == b"firstsecond"
    assert [row for row in archive.events if row["kind"] == "response-end"][-1]["complete"] is True
    with pytest.raises(OSError):
        response.close()
    assert archive.summary()["complete"] is False
