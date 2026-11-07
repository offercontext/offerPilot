"""Offline private-frame and durable-reservation boundaries; disposable files only."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
from pathlib import Path
import tempfile
from unittest.mock import patch

import httpx
import pytest

from scripts.real_ai_control.protocol import (
    PROTOCOL,
    InvalidControlFrame,
    SendPermissionDenied,
    canonical,
    check_permission,
    decode,
    read_packet,
    reservation_payload,
    validate_request,
    write_packet,
)
from scripts.real_ai_control.journal import ReservationJournal
from scripts.real_ai_control.wire import WirePolicy

JOURNALS = []


@contextmanager
def folder_scope():
    with tempfile.TemporaryDirectory() as folder:
        try:
            yield folder
        finally:
            for item in JOURNALS:
                item.close()
            JOURNALS.clear()


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
KEY = "fake-private-control-test-key"
PLAN = "1" * 64


def request():
    return httpx.Request(
        "POST",
        "https://api.deepseek.com/v1/chat/completions",
        json=dict(
            model="deepseek-flash",
            messages=[dict(role="user", content="offline")],
            max_tokens=1024,
            temperature=0,
            thinking={"type": "disabled"},
        ),
        headers={"Authorization": "Bearer " + KEY},
    )


def proposal(index=1):
    policy = WirePolicy(64000, 8192, "2", "8", NOW + timedelta(hours=1))
    req = request()
    estimate = policy.validate(req, expected_key=KEY, now=NOW)
    payload = reservation_payload(
        req,
        estimate,
        expected_key=KEY,
        logical_id="completion-" + format(index, "032x"),
        nonce=format(index + 1000, "032x"),
    )
    return payload, estimate, policy


def journal(path, *, limit=2, cap="1"):
    subject = ReservationJournal(
        path, plan_sha256=PLAN, max_requests=limit, max_cny=cap, expires_at=NOW + timedelta(hours=1)
    )
    JOURNALS.append(subject)
    return subject


def test_private_frame_roundtrip_has_no_plain_authorization_or_key():
    payload, estimate, policy = proposal()
    wire = io.BytesIO()
    write_packet(wire, dict(protocol=PROTOCOL, kind="reserve", seq=1, data=payload))
    assert KEY.encode() not in wire.getvalue()
    assert b"Bearer " not in wire.getvalue()
    packet = read_packet(io.BytesIO(wire.getvalue()))
    actual, rebuilt = validate_request(packet["data"], policy, expected_key=KEY, now=NOW)
    assert actual.content == request().content
    assert rebuilt == estimate
    assert actual.headers["authorization"] == "Bearer " + KEY


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{}",
        b"{}\n\n",
        b"{}\n{}\n",
        b"x" * 131073 + b"\n",
        b'{"protocol":"x","kind":"reserve","seq":1}\n',
        ('{"protocol":"' + PROTOCOL + '","kind":"reserve","seq":true}\n').encode(),
        ('{"protocol":"' + PROTOCOL + '","kind":"reserve","seq":1,"seq":2}\n').encode(),
    ],
    ids=[
        "empty",
        "missing-newline",
        "double-newline",
        "two-frames",
        "oversized",
        "wrong-protocol",
        "bool-seq",
        "duplicate-seq",
    ],
)
def test_truncated_oversized_duplicate_or_unscoped_frames_are_denied(raw):
    with pytest.raises(InvalidControlFrame):
        decode(raw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("method", "GET"),
        ("url", "https://outside.invalid/"),
        ("body_sha256", "2" * 64),
        ("request_sha256", "2" * 64),
        ("authorization_sha256", "2" * 64),
        ("body_base64", "not-base64"),
        ("nonce", "x"),
        ("logical_id", "completion-1"),
    ],
)
def test_mutation_cannot_borrow_a_different_envelope_permission(field, value):
    payload, _, policy = proposal()
    payload[field] = value
    with pytest.raises(ValueError):
        validate_request(payload, policy, expected_key=KEY, now=NOW)


def test_alternate_auth_in_control_headers_is_denied_without_disclosing_key():
    payload, _, policy = proposal()
    payload["headers"].append(("Authorization", "Bearer " + KEY))
    with pytest.raises(InvalidControlFrame) as caught:
        validate_request(payload, policy, expected_key=KEY, now=NOW)
    assert KEY not in str(caught.value)


def permission(payload, estimate):
    return dict(
        protocol=PROTOCOL,
        kind="permission",
        seq=7,
        nonce=payload["nonce"],
        request_sha256=estimate.request_sha256,
        plan_sha256=PLAN,
        allowed=True,
        reservation_row_hash="a" * 64,
        reserve_cny=str(estimate.retained_bound_cny),
        reason="allowed",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("seq", 8),
        ("seq", True),
        ("nonce", "b" * 32),
        ("request_sha256", "b" * 64),
        ("plan_sha256", "b" * 64),
        ("reservation_row_hash", "bad"),
        ("reserve_cny", "0"),
        ("allowed", 1),
    ],
)
def test_stale_forged_or_different_request_ack_is_not_permission(field, value):
    payload, estimate, _ = proposal()
    reply = permission(payload, estimate)
    reply[field] = value
    with pytest.raises(InvalidControlFrame):
        check_permission(reply, seq=7, payload=payload, plan_sha256=PLAN, estimate=estimate)


def test_explicit_denial_does_not_turn_into_a_dispatch_token():
    payload, estimate, _ = proposal()
    reply = permission(payload, estimate)
    reply["allowed"] = False
    with pytest.raises(SendPermissionDenied):
        check_permission(reply, seq=7, payload=payload, plan_sha256=PLAN, estimate=estimate)


def test_allowance_is_durable_before_caller_can_issue_permission():
    with folder_scope() as folder:
        path = Path(folder) / "budget.jsonl"
        subject = journal(path)
        payload, estimate, _ = proposal()
        fsyncs = []
        import os

        original = os.fsync

        def fsync(fd):
            fsyncs.append(path.read_bytes())
            return original(fd)

        with patch("scripts.real_ai_control.journal.os.fsync", side_effect=fsync):
            row = subject.reserve(payload, estimate, seq=2, now=NOW)
        assert row["allowed"] is True
        assert len(fsyncs) == 1 and fsyncs[0] == canonical(row) + b"\n"
        assert subject.verify()["retained_cny"] == str(estimate.retained_bound_cny)


def test_one_logical_completion_has_at_most_one_reserved_attempt_even_if_new_nonce():
    with folder_scope() as folder:
        subject = journal(Path(folder) / "budget.jsonl")
        payload, estimate, _ = proposal()
        first = subject.reserve(payload, estimate, seq=2, now=NOW)
        payload["nonce"] = "c" * 32
        repeated = subject.reserve(payload, estimate, seq=3, now=NOW)
        assert first["allowed"] is True and repeated["allowed"] is False
        assert repeated["reason"] == "logical_completion_already_attempted"
        assert subject.requests == 1 and subject.retained == estimate.retained_bound_cny


@pytest.mark.parametrize("reason", ["requests", "money", "expiry"])
def test_parent_caps_deny_before_an_additional_dispatch_permission(reason):
    with folder_scope() as folder:
        payload, estimate, _ = proposal()
        subject = journal(
            Path(folder) / "budget.jsonl",
            limit=1 if reason == "requests" else 2,
            cap=str(estimate.retained_bound_cny) if reason == "money" else "1",
        )
        subject.reserve(payload, estimate, seq=2, now=NOW)
        second, _, _ = proposal(2)
        row = subject.reserve(
            second, estimate, seq=3, now=NOW + timedelta(hours=1) if reason == "expiry" else NOW
        )
        assert row["allowed"] is False and subject.requests == 1
        assert subject.verify()["allowed_requests"] == 1


def test_unknown_billing_is_not_refunded_or_subtracted_from_allowed_rows():
    with folder_scope() as folder:
        subject = journal(Path(folder) / "budget.jsonl")
        payload, estimate, _ = proposal()
        subject.reserve(payload, estimate, seq=2, now=NOW)
        # Transport may now fail/timeout: no operation can undo this allowance.
        result = subject.verify()
        assert Decimal(result["retained_cny"]) == estimate.retained_bound_cny
        assert result["invoice_verified"] is False and result["dispatch_proven"] is False


def test_fsync_failure_poisoning_never_returns_an_allowed_row_for_ack():
    with folder_scope() as folder:
        subject = journal(Path(folder) / "budget.jsonl")
        payload, estimate, _ = proposal()
        with patch(
            "scripts.real_ai_control.journal.os.fsync", side_effect=OSError("offline disk fault")
        ):
            with pytest.raises(OSError):
                subject.reserve(payload, estimate, seq=2, now=NOW)
        assert subject.poisoned is True
        with pytest.raises(ValueError):
            subject.verify()


def test_external_truncation_is_rejected_before_the_next_allowance():
    with folder_scope() as folder:
        path = Path(folder) / "budget.jsonl"
        subject = journal(path)
        payload, estimate, _ = proposal()
        subject.reserve(payload, estimate, seq=2, now=NOW)
        path.write_bytes(b"")
        other, _, _ = proposal(2)
        with pytest.raises(ValueError):
            subject.reserve(other, estimate, seq=3, now=NOW)
        assert subject.requests == 1


def test_existing_file_is_never_reset_on_constructor():
    with folder_scope() as folder:
        path = Path(folder) / "budget.jsonl"
        path.write_bytes(b"prior failed evidence\n")
        with pytest.raises(FileExistsError):
            journal(path)
        assert path.read_bytes() == b"prior failed evidence\n"


def test_inconsistent_price_estimate_identity_is_not_a_new_allowance():
    with folder_scope() as folder:
        subject = journal(Path(folder) / "budget.jsonl")
        payload, estimate, _ = proposal()
        with pytest.raises(ValueError):
            subject.reserve(payload, replace(estimate, body_sha256="c" * 64), seq=2, now=NOW)
        assert subject.requests == 0
