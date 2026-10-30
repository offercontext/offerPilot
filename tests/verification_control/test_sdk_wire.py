"""Offline SDK wire PoC with fake admission and MockTransport only.

These tests cannot authorize a real send or certify an installed CLI controller.
They exercise the actual SDK's serialized requests and automatic role repair.
"""

from datetime import datetime, timedelta, timezone
import json
import socket
import threading
from unittest.mock import patch

import httpx
from litellm import completion
from openai import OpenAI
import pytest

from scripts.real_ai_control.wire import WirePolicy, WirePolicyDenied

KEY = "sk-offline-wire-policy-fixture"
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


class OfflineAttempt:
    """Test double only: models a single logical call and a fake parent ACK."""

    def __init__(self, response_kind="ok", *, admit=True):
        self.response_kind = response_kind
        self.admit = admit
        self.policy = WirePolicy(64000, 1024, "2", "8", NOW + timedelta(hours=1))
        self.requests = []
        self.fake_reservations = []
        self.wire_attempts = 0
        self.used = False
        self.blocked_repeats = 0

    def dispatch_mock(self, request):
        self.wire_attempts += 1
        if self.used:
            self.blocked_repeats += 1
            raise PermissionError("Offline repeat fuse denied before fake admission")
        self.policy.validate(request, expected_key=KEY, now=NOW)
        self.used = True
        if not self.admit:
            raise PermissionError("Fake parent denied: no real budget authority")
        self.fake_reservations.append(request.content)
        self.requests.append(request)
        if self.response_kind == "timeout":
            raise httpx.ReadTimeout("Offline timeout fixture", request=request)
        if self.response_kind == "role":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Last message must have role `user`",
                        "type": "invalid_request_error",
                        "code": None,
                    }
                },
            )
        if self.response_kind == "server":
            return httpx.Response(500, json={"error": {"message": "Offline fixture"}})
        if self.response_kind == "stream":
            parts = [
                dict(
                    id="mock",
                    object="chat.completion.chunk",
                    created=1,
                    model="deepseek-flash",
                    choices=[
                        dict(
                            index=0,
                            delta=dict(role="assistant", content="offline success"),
                            finish_reason=None,
                        )
                    ],
                ),
                dict(
                    id="mock",
                    object="chat.completion.chunk",
                    created=1,
                    model="deepseek-flash",
                    choices=[dict(index=0, delta={}, finish_reason="stop")],
                    usage=dict(prompt_tokens=1, completion_tokens=2, total_tokens=3),
                ),
            ]
            raw = "".join("data: " + json.dumps(part) + "\n\n" for part in parts)
            return httpx.Response(
                200,
                content=(raw + "data: [DONE]\n\n").encode(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json=dict(
                id="mock",
                object="chat.completion",
                created=1,
                model="deepseek-flash",
                choices=[
                    dict(
                        index=0,
                        message=dict(role="assistant", content="offline success"),
                        finish_reason="stop",
                    )
                ],
                usage=dict(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            ),
        )


def invoke(attempt, *, stream=False):
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_pair = socket.socketpair
    state = threading.local()

    def forbidden(*args, **kwargs):
        raise AssertionError("External network forbidden in offline SDK PoC")

    def pair(*args, **kwargs):
        # Windows asyncio creates a private loopback socketpair. This is the
        # only real socket connection allowed; Provider traffic stays mocked.
        state.pair = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            state.pair = False

    def connect(sock, address):
        if (
            getattr(state, "pair", False)
            and isinstance(address, tuple)
            and address[0] in {"127.0.0.1", "::1"}
        ):
            return original_connect(sock, address)
        return forbidden()

    def connect_ex(sock, address):
        if (
            getattr(state, "pair", False)
            and isinstance(address, tuple)
            and address[0] in {"127.0.0.1", "::1"}
        ):
            return original_connect_ex(sock, address)
        return forbidden()

    # MockTransport must handle every physical request; socket fallback is a failure.
    with (
        patch.object(socket, "getaddrinfo", side_effect=forbidden),
        patch.object(socket.socket, "connect", new=connect),
        patch.object(socket.socket, "connect_ex", new=connect_ex),
        patch.object(socket, "socketpair", new=pair),
        httpx.Client(
            transport=httpx.MockTransport(attempt.dispatch_mock),
            trust_env=False,
            follow_redirects=False,
        ) as client,
    ):
        sdk = OpenAI(
            api_key=KEY, base_url="https://api.deepseek.com/v1", max_retries=0, http_client=client
        )
        options = dict(
            model="openai/deepseek-flash",
            messages=[dict(role="user", content="offline")],
            client=sdk,
            api_base="https://api.deepseek.com/v1",
            api_key=KEY,
            num_retries=0,
            temperature=0,
            max_tokens=1024,
            timeout=2,
            extra_body=dict(thinking=dict(type="disabled")),
            stream=stream,
        )
        if stream:
            options["stream_options"] = {"include_usage": True}
        result = completion(**options)
        return list(result) if stream else result


def test_actual_sdk_serialization_passes_policy_before_fake_admission():
    attempt = OfflineAttempt()
    result = invoke(attempt)
    assert result.choices[0].message.content == "offline success"
    assert len(attempt.requests) == len(attempt.fake_reservations) == 1
    actual = json.loads(attempt.requests[0].content)
    assert actual["max_tokens"] == 1024 and actual["thinking"] == {"type": "disabled"}


@pytest.mark.parametrize("kind", ["server", "timeout"])
def test_sdk_zero_retry_does_not_repeat_failed_or_unknown_billing_attempt(kind):
    attempt = OfflineAttempt(kind)
    with pytest.raises(Exception):
        invoke(attempt)
    assert attempt.wire_attempts == 1
    assert len(attempt.requests) == len(attempt.fake_reservations) == 1


def test_role_repair_cannot_dispatch_twice_in_the_same_logical_call():
    attempt = OfflineAttempt("role")
    with pytest.raises(Exception):
        invoke(attempt)
    # The original attempt observed four boundary entries in this SDK version,
    # not the guessed two. Every repeat must be denied before fake reservation.
    assert attempt.wire_attempts > 1
    assert attempt.blocked_repeats == attempt.wire_attempts - 1
    assert len(attempt.requests) == len(attempt.fake_reservations) == 1


def test_fake_parent_denial_prevents_even_first_mock_dispatch():
    attempt = OfflineAttempt(admit=False)
    with pytest.raises(Exception):
        invoke(attempt)
    assert attempt.wire_attempts == 1
    assert attempt.requests == attempt.fake_reservations == []


def test_actual_sdk_stream_bytes_and_usage_contract_match_wire_policy():
    attempt = OfflineAttempt("stream")
    chunks = invoke(attempt, stream=True)
    assert any(
        chunk.choices and chunk.choices[0].delta.content == "offline success" for chunk in chunks
    )
    assert len(attempt.requests) == 1
    assert json.loads(attempt.requests[0].content)["stream_options"] == {"include_usage": True}


def test_policy_denial_precedes_fake_reservation_and_dispatch():
    attempt = OfflineAttempt()
    attempt.policy = WirePolicy(1, 1024, "2", "8", NOW + timedelta(hours=1))
    with pytest.raises(Exception):
        invoke(attempt)
    assert attempt.wire_attempts == 1
    assert attempt.requests == attempt.fake_reservations == []


def test_scope_is_only_an_offline_double_not_an_authorization_capability():
    estimate = OfflineAttempt().policy.validate(
        httpx.Request(
            "POST",
            "https://api.deepseek.com/v1/chat/completions",
            json=dict(
                model="deepseek-flash",
                messages=[dict(role="user", content="offline")],
                max_tokens=1,
                temperature=0,
                thinking={"type": "disabled"},
            ),
            headers={"Authorization": "Bearer " + KEY},
        ),
        expected_key=KEY,
        now=NOW,
    )
    assert estimate.parent_reservation_required is True
    assert estimate.physical_dispatch_permitted is False
    assert issubclass(WirePolicyDenied, ValueError)
