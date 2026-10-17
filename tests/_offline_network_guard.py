"""Pytest startup guard, loaded by pyproject via -p tests._offline_network_guard.

Importing this plugin precedes conftest/collection and makes regression runs
loopback-only, including SDK retries, redirects and local outbound proxies.
It never changes product transport settings or grants network permissions.
This protects this Python process, not child processes or arbitrary native I/O.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import sys
from urllib.parse import urlsplit

_EARLY_INSTALL = not any(name in sys.modules for name in ("offerpilot.api", "litellm"))
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"


class OfflineNetworkDenied(PermissionError):
    pass


_denied: list[str] = []


def require_loopback(host: object) -> None:
    name = host.decode("ascii") if isinstance(host, bytes) else str(host)
    if name.lower() == "localhost":
        return
    try:
        if ipaddress.ip_address(name).is_loopback:
            return
    except ValueError:
        pass
    _denied.append(name)
    raise OfflineNetworkDenied(f"Offline regression blocked non-loopback host: {name}")


_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_getaddrinfo = socket.getaddrinfo
_sendto = socket.socket.sendto


def _local_address(family, address):
    if family not in (socket.AF_INET, socket.AF_INET6):
        return address
    require_loopback(address[0])
    name = address[0].decode("ascii") if isinstance(address[0], bytes) else address[0]
    if name.lower() == "localhost":
        return (("::1" if family == socket.AF_INET6 else "127.0.0.1"), *address[1:])
    return address


def guarded_connect(self, address):
    return _connect(self, _local_address(self.family, address))


def guarded_connect_ex(self, address):
    return _connect_ex(self, _local_address(self.family, address))


def guarded_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host is not None:
        require_loopback(host)
    elif not flags & socket.AI_PASSIVE:
        require_loopback(host)
    normalized = host
    name = host.decode("ascii") if isinstance(host, bytes) else host
    if isinstance(name, str) and name.lower() == "localhost":
        normalized = "::1" if family == socket.AF_INET6 else "127.0.0.1"
    rows = _getaddrinfo(normalized, port, family, type, proto, flags)
    if host is not None:
        for row in rows:
            require_loopback(row[4][0])
    return rows


def guarded_sendto(self, data, *args):
    return _sendto(self, data, *args[:-1], _local_address(self.family, args[-1]))


_gethostbyname = socket.gethostbyname
_gethostbyname_ex = socket.gethostbyname_ex
_gethostbyaddr = socket.gethostbyaddr
_getnameinfo = socket.getnameinfo


def guarded_gethostbyname(host):
    require_loopback(host)
    name = host.decode("ascii") if isinstance(host, bytes) else host
    return "127.0.0.1" if name.lower() == "localhost" else str(ipaddress.ip_address(name))


def guarded_gethostbyname_ex(host):
    address = guarded_gethostbyname(host)
    return ("localhost", [], [address])


def guarded_gethostbyaddr(host):
    address = guarded_gethostbyname(host)
    return ("localhost", [], [address])


def guarded_getnameinfo(sockaddr, flags):
    require_loopback(sockaddr[0])
    return (str(sockaddr[0]) if flags & socket.NI_NUMERICHOST else "localhost", str(sockaddr[1]))


socket.gethostbyname = guarded_gethostbyname
socket.gethostbyname_ex = guarded_gethostbyname_ex
socket.gethostbyaddr = guarded_gethostbyaddr
socket.getnameinfo = guarded_getnameinfo


socket.socket.connect = guarded_connect
socket.socket.connect_ex = guarded_connect_ex
socket.getaddrinfo = guarded_getaddrinfo
socket.socket.sendto = guarded_sendto

# HTTP-level checks are necessary even when a proxy itself is on loopback.
# The single-request boundary runs for every redirect/retry destination.
import httpx  # noqa: E402
import requests.sessions  # noqa: E402
from starlette.testclient import _TestClientTransport  # noqa: E402

_httpx_send = httpx.Client._send_single_request
_httpx_async_send = httpx.AsyncClient._send_single_request
_requests_send = requests.sessions.Session.send


def _in_memory(transport):
    return type(transport) in (
        httpx.MockTransport, httpx.ASGITransport, httpx.WSGITransport, _TestClientTransport,
    )


def guarded_httpx_send(self, request):
    if not _in_memory(self._transport_for_url(request.url)):
        require_loopback(request.url.host)
    return _httpx_send(self, request)


async def guarded_httpx_async_send(self, request):
    if not _in_memory(self._transport_for_url(request.url)):
        require_loopback(request.url.host)
    return await _httpx_async_send(self, request)


def guarded_requests_send(self, request, **kwargs):
    require_loopback(urlsplit(request.url).hostname)
    return _requests_send(self, request, **kwargs)


httpx.Client._send_single_request = guarded_httpx_send
httpx.AsyncClient._send_single_request = guarded_httpx_async_send
requests.sessions.Session.send = guarded_requests_send


# urllib follows redirects through OpenerDirector.open. aiohttp constructs a
# ClientRequest for every hop, including redirects internal to _request.
import urllib.request  # noqa: E402
import aiohttp  # noqa: E402

_urllib_open = urllib.request.OpenerDirector.open
_aiohttp_request = aiohttp.ClientSession._request
_aiohttp_request_init = aiohttp.ClientRequest.__init__


def guarded_urllib_open(self, fullurl, *args, **kwargs):
    url = fullurl.full_url if isinstance(fullurl, urllib.request.Request) else fullurl
    parsed = urlsplit(url)
    if parsed.scheme != "file" or parsed.hostname:
        require_loopback(parsed.hostname)
    return _urllib_open(self, fullurl, *args, **kwargs)


async def guarded_aiohttp_request(self, method, str_or_url, *args, **kwargs):
    require_loopback(self._build_url(str_or_url).host)
    return await _aiohttp_request(self, method, str_or_url, *args, **kwargs)


def guarded_aiohttp_request_init(self, method, url, *args, **kwargs):
    require_loopback(url.host)
    return _aiohttp_request_init(self, method, url, *args, **kwargs)


urllib.request.OpenerDirector.open = guarded_urllib_open
aiohttp.ClientSession._request = guarded_aiohttp_request
aiohttp.ClientRequest.__init__ = guarded_aiohttp_request_init


def pytest_configure(config):
    if not _EARLY_INSTALL:
        raise RuntimeError("Offline guard must load before product imports and collection")
