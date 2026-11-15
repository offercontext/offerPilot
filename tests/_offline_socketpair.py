"""Allow only CPython's private, owned socketpair handshake in strict tests."""

from __future__ import annotations

import socket
import sys

_SOCKET = socket.socket
_FALLBACK = getattr(socket, "_fallback_socketpair", None)
_FALLBACK_CODE = getattr(_FALLBACK, "__code__", None)


def _is_owned_socketpair_connect(sock, address, caller):
    # A function name, a loopback address, or merely being inside socketpair is
    # insufficient. Require the captured stdlib implementation and its own pair.
    if (
        _FALLBACK_CODE is None
        or getattr(socket, "_fallback_socketpair", None) is not _FALLBACK
        or caller.f_code is not _FALLBACK_CODE
        or caller.f_globals is not vars(socket)
        or sock is not caller.f_locals.get("csock")
        or type(sock) is not _SOCKET
    ):
        return False
    listener = caller.f_locals.get("lsock")
    if (
        type(listener) is not _SOCKET
        or listener is sock
        or sock.family not in (socket.AF_INET, socket.AF_INET6)
        or listener.family != sock.family
        or sock.type != socket.SOCK_STREAM
        or listener.type != socket.SOCK_STREAM
        or type(address) is not tuple
    ):
        return False
    try:
        host, port = listener.getsockname()[:2]
        return (
            host in {"127.0.0.1", "::1"}
            and 0 < port <= 65535
            and address == (host, port)
            and bool(listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN))
        )
    except OSError:
        return False


def socketpair_only_connect(forbidden):
    # Preserve the already-installed startup network guard. Never reach past it
    # to a raw socket implementation or temporarily unpatch global methods.
    previous_connect = socket.socket.connect

    def connect(sock, address):
        if _is_owned_socketpair_connect(sock, address, sys._getframe(1)):
            return previous_connect(sock, address)
        return forbidden(sock, address)

    return connect
