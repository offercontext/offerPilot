"""Strict mail guards allow only the stdlib-owned asyncio socketpair handshake."""

import importlib
import socket
from types import FunctionType, SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import _offline_socketpair as guard
from test_desktop_job_mail_integration import _offline_mail_guard


def test_stdlib_fallback_socketpair_remains_usable(monkeypatch):
    # Exercise the Windows implementation on every platform, not a fake pair.
    monkeypatch.setattr(socket, "socketpair", socket._fallback_socketpair)
    with _offline_mail_guard():
        left, right = socket.socketpair()
        with left, right:
            left.settimeout(1)
            right.settimeout(1)
            left.sendall(b"left")
            assert right.recv(4) == b"left"
            right.sendall(b"right")
            assert left.recv(5) == b"right"


def test_testclient_starts_with_real_fallback_socketpair(monkeypatch):
    monkeypatch.setattr(socket, "socketpair", socket._fallback_socketpair)
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"ok": True}

    with _offline_mail_guard(), TestClient(app) as client:
        assert client.get("/health").json() == {"ok": True}


def test_socketpair_preserves_preexisting_connect_guard(monkeypatch):
    previous_connect = socket.socket.connect
    allowed = []

    def observed(sock, address):
        allowed.append((sock, address))
        return previous_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", observed)
    with _offline_mail_guard():
        left, right = socket._fallback_socketpair()
        with left, right:
            assert allowed == [(right, left.getsockname())]


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "198.51.100.1"])
@pytest.mark.parametrize("operation", ["connect", "connect_ex", "sendto", "create_connection", "getaddrinfo"])
def test_arbitrary_loopback_and_external_network_are_denied(monkeypatch, host, operation):
    def unexpected_io(*args, **kwargs):
        pytest.fail("The previous network primitive must not be reached")

    # Also prove a rejected connect never reaches the preexisting startup guard.
    monkeypatch.setattr(socket.socket, "connect", unexpected_io)
    with pytest.raises(AssertionError, match="offline and credential-free"):
        with _offline_mail_guard(), socket.socket() as sock:
            address = (host, 443)
            if operation == "sendto":
                sock.sendto(b"synthetic", address)
            elif operation == "create_connection":
                socket.create_connection(address)
            elif operation == "getaddrinfo":
                socket.getaddrinfo(*address)
            else:
                getattr(sock, operation)(address)


@pytest.mark.parametrize("target", [
    "offerpilot.job_mail.transport.OSVault",
    "offerpilot.job_mail.transport.QQIMAPTransport",
    "litellm.completion", "litellm.acompletion",
])
def test_credentials_mail_transport_and_models_remain_denied(target):
    module, name = target.rsplit(".", 1)
    with pytest.raises(AssertionError, match="offline and credential-free"):
        with _offline_mail_guard():
            getattr(importlib.import_module(module), name)()


def test_caught_network_denial_still_fails_the_guard():
    with pytest.raises(AssertionError):
        with _offline_mail_guard(), socket.socket() as sock:
            with pytest.raises(AssertionError, match="offline and credential-free"):
                sock.connect(("127.0.0.1", 443))


def test_same_named_function_cannot_claim_socketpair_exception():
    def _fallback_socketpair():
        with socket.socket() as lsock, socket.socket() as csock:
            lsock.bind(("127.0.0.1", 0))
            lsock.listen()
            csock.connect(lsock.getsockname())

    with pytest.raises(AssertionError, match="offline and credential-free"):
        with _offline_mail_guard():
            _fallback_socketpair()


def test_copied_stdlib_code_with_different_globals_is_denied():
    copied = FunctionType(
        socket._fallback_socketpair.__code__, dict(vars(socket)),
        argdefs=socket._fallback_socketpair.__defaults__,
    )
    with pytest.raises(AssertionError, match="offline and credential-free"):
        with _offline_mail_guard():
            copied()


@pytest.mark.parametrize("replacement", [None, lambda: None])
def test_missing_or_replaced_stdlib_identity_fails_closed(monkeypatch, replacement):
    original = socket._fallback_socketpair
    monkeypatch.setattr(socket, "_fallback_socketpair", replacement)
    with pytest.raises(AssertionError, match="offline and credential-free"):
        with _offline_mail_guard():
            original()


@pytest.mark.parametrize("mismatch", [
    "client", "destination", "same_socket", "closed_listener", "not_listening",
    "wildcard_listener", "datagram_client",
])
def test_socketpair_exception_requires_its_exact_live_pair(mismatch):
    with socket.socket() as lsock, socket.socket() as csock, socket.socket() as other:
        lsock.bind(("0.0.0.0" if mismatch == "wildcard_listener" else "127.0.0.1", 0))
        if mismatch != "not_listening":
            lsock.listen()
        address = lsock.getsockname()
        caller = SimpleNamespace(
            f_code=guard._FALLBACK_CODE, f_globals=vars(socket),
            f_locals={"lsock": lsock, "csock": csock},
        )
        if mismatch == "client":
            csock = other
        elif mismatch == "destination":
            address = (address[0], 1 if address[1] != 1 else 2)
        elif mismatch == "same_socket":
            caller.f_locals["lsock"] = csock
        elif mismatch == "closed_listener":
            lsock.close()
        elif mismatch == "datagram_client":
            with socket.socket(type=socket.SOCK_DGRAM) as datagram:
                caller.f_locals["csock"] = datagram
                assert not guard._is_owned_socketpair_connect(datagram, address, caller)
            return
        assert not guard._is_owned_socketpair_connect(csock, address, caller)
