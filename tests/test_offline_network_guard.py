from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
import requests

from tests import _offline_network_guard as guard


def test_guard_installs_before_collection(pytestconfig):
    assert pytestconfig.pluginmanager.hasplugin("tests._offline_network_guard")
    assert guard._EARLY_INSTALL is True
    assert socket.socket.connect is guard.guarded_connect
    assert socket.socket.connect_ex is guard.guarded_connect_ex
    assert socket.getaddrinfo is guard.guarded_getaddrinfo
    assert httpx.Client._send_single_request is guard.guarded_httpx_send
    assert httpx.AsyncClient._send_single_request is guard.guarded_httpx_async_send


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "LoCaLhOsT", b"LOCALHOST"])
def test_guard_normalizes_dns_localhost_without_native_hostname_lookup(host, monkeypatch):
    def resolve(name, port, family=0, type=0, proto=0, flags=0):
        assert name == "127.0.0.1", "native resolver must receive a literal loopback address"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (name, port))]

    monkeypatch.setattr(guard, "_getaddrinfo", resolve)
    assert socket.getaddrinfo(host, 443)[0][4] == ("127.0.0.1", 443)


def test_guard_covers_alternate_name_and_reverse_resolvers(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("native DNS resolver must never be reached")

    for name in ("_gethostbyname", "_gethostbyname_ex", "_gethostbyaddr", "_getnameinfo"):
        monkeypatch.setattr(guard, name, forbidden)
    for resolver in (socket.gethostbyname, socket.gethostbyname_ex, socket.gethostbyaddr):
        with pytest.raises(guard.OfflineNetworkDenied):
            resolver("8.8.8.8")
        with pytest.raises(guard.OfflineNetworkDenied):
            resolver("api.openai.com")
        assert resolver("LOCALHOST")
        assert resolver("127.0.0.1")
    with pytest.raises(guard.OfflineNetworkDenied):
        socket.getnameinfo(("8.8.8.8", 443), 0)
    assert socket.getnameinfo(("127.0.0.1", 443), 0) == ("localhost", "443")


@pytest.mark.parametrize("host", ["api.openai.com", "8.8.8.8", "::ffff:8.8.8.8", "provider.example"])
def test_guard_denies_before_socket_or_dns(host, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("network primitive must never be called")

    for name in ("_connect", "_connect_ex", "_getaddrinfo", "_sendto"):
        monkeypatch.setattr(guard, name, forbidden)
    with socket.socket() as sock:
        with pytest.raises(guard.OfflineNetworkDenied):
            sock.connect((host, 443))
        with pytest.raises(guard.OfflineNetworkDenied):
            sock.connect_ex((host, 443))
        with pytest.raises(guard.OfflineNetworkDenied):
            sock.sendto(b"synthetic", (host, 443))
    with pytest.raises(guard.OfflineNetworkDenied):
        socket.getaddrinfo(host, 443)


def test_guard_denies_http_before_transport_even_through_local_proxy(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("HTTP transport must never be reached")

    monkeypatch.setattr(guard, "_httpx_send", forbidden)
    monkeypatch.setattr(guard, "_httpx_async_send", forbidden)
    monkeypatch.setattr(guard, "_requests_send", forbidden)
    with httpx.Client(proxy="http://127.0.0.1:12345", trust_env=False) as client:
        with pytest.raises(guard.OfflineNetworkDenied):
            client.get("https://api.openai.com/v1/models")

    async def run():
        async with httpx.AsyncClient(proxy="http://127.0.0.1:12345", trust_env=False) as client:
            with pytest.raises(guard.OfflineNetworkDenied):
                await client.get("https://api.openai.com/v1/models")
    asyncio.run(run())
    with pytest.raises(guard.OfflineNetworkDenied):
        requests.get("https://api.openai.com/v1/models", proxies={"https": "http://127.0.0.1:12345"})


def test_guard_allows_only_explicit_memory_transports_or_loopback():
    for host in ("localhost", "127.0.0.1", "::1"):
        guard.require_loopback(host)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="memory"))) as client:
        assert client.get("https://memory.example").text == "memory"


@pytest.mark.parametrize("host", [
    "0.0.0.0", "169.254.169.254", "localhost.example", "127.0.0.1.example",
    "127.1", "2130706433", "0x7f000001", b"provider.example", "2001:db8::1",
])
def test_guard_rejects_address_aliases_before_any_primitive(host, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("network primitive must never be called")

    for name in ("_connect", "_connect_ex", "_getaddrinfo", "_sendto"):
        monkeypatch.setattr(guard, name, forbidden)
    with socket.socket(socket.AF_INET6) as sock:
        with pytest.raises(guard.OfflineNetworkDenied):
            sock.connect((host, 443, 0, 0))
        with pytest.raises(guard.OfflineNetworkDenied):
            sock.connect_ex((host, 443, 0, 0))
        with pytest.raises(guard.OfflineNetworkDenied):
            sock.sendto(b"synthetic", (host, 443, 0, 0))
    with pytest.raises(guard.OfflineNetworkDenied):
        socket.getaddrinfo(host, 443)


@pytest.mark.parametrize("family, literal", [
    (socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1"),
])
def test_guard_normalizes_localhost_before_native_resolution(family, literal, monkeypatch):
    observed = []

    def connect(_self, address):
        observed.append(address)
        assert address[0] == literal, "C socket call must not resolve a hostname"
        return 0

    def sendto(_self, _data, *args):
        return connect(_self, args[-1])

    monkeypatch.setattr(guard, "_connect", connect)
    monkeypatch.setattr(guard, "_connect_ex", connect)
    monkeypatch.setattr(guard, "_sendto", sendto)
    with socket.socket(family) as sock:
        address = ("localhost", 12345) if family == socket.AF_INET else ("localhost", 12345, 0, 0)
        sock.connect(address)
        sock.connect_ex(address)
        sock.sendto(b"synthetic", address)
    assert len(observed) == 3


def test_guard_rejects_nonloopback_dns_result_for_localhost(monkeypatch):
    def poisoned_resolution(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

    monkeypatch.setattr(guard, "_getaddrinfo", poisoned_resolution)
    with pytest.raises(guard.OfflineNetworkDenied):
        socket.getaddrinfo("localhost", 443)


def test_guard_never_exempts_custom_or_subclassed_transports(monkeypatch):
    class CustomTransport(httpx.BaseTransport):
        def handle_request(self, request):
            raise AssertionError("custom transport must never be reached")

    class SubclassedMock(httpx.MockTransport):
        pass

    def forbidden(*_args, **_kwargs):
        raise AssertionError("HTTP transport must never be reached")

    monkeypatch.setattr(guard, "_httpx_send", forbidden)
    for transport in (CustomTransport(), SubclassedMock(forbidden)):
        assert guard._in_memory(transport) is False
        with httpx.Client(transport=transport, trust_env=False) as client:
            for _ in range(3):
                with pytest.raises(guard.OfflineNetworkDenied):
                    client.get("https://api.openai.com/v1/models")


@pytest.mark.parametrize("asynchronous", [False, True])
def test_guard_checks_every_httpx_redirect_destination(asynchronous):
    visited = []

    def response(request):
        visited.append(str(request.url))
        assert request.url.host == "127.0.0.1", "external redirect must not reach transport"
        return httpx.Response(302, headers={"location": "https://api.openai.com/v1/models"})

    class Transport(httpx.BaseTransport):
        def handle_request(self, request):
            return response(request)

    class AsyncTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return response(request)

    async def run():
        async with httpx.AsyncClient(transport=AsyncTransport(), follow_redirects=True, trust_env=False) as client:
            with pytest.raises(guard.OfflineNetworkDenied):
                await client.get("http://127.0.0.1/start")

    if asynchronous:
        asyncio.run(run())
    else:
        with httpx.Client(transport=Transport(), follow_redirects=True, trust_env=False) as client:
            with pytest.raises(guard.OfflineNetworkDenied):
                client.get("http://127.0.0.1/start")
    assert visited == ["http://127.0.0.1/start"]


def test_guard_checks_requests_redirect_before_adapter():
    visited = []

    class Adapter(requests.adapters.BaseAdapter):
        def send(self, request, **kwargs):
            visited.append(request.url)
            assert request.url == "http://127.0.0.1/start", "external redirect must not reach adapter"
            response = requests.Response()
            response.status_code = 302
            response.url = request.url
            response.request = request
            response.headers["location"] = "https://api.openai.com/v1/models"
            response._content = b""
            response._content_consumed = True
            return response

        def close(self):
            pass

    with requests.Session() as session:
        session.trust_env = False
        session.mount("http://", Adapter())
        session.mount("https://", Adapter())
        with pytest.raises(guard.OfflineNetworkDenied):
            session.get("http://127.0.0.1/start")
    assert visited == ["http://127.0.0.1/start"]


def test_guard_denies_urllib_proxy_destination_before_open(monkeypatch):
    import urllib.request

    def forbidden(*_args, **_kwargs):
        raise AssertionError("urllib opener must never be reached")

    monkeypatch.setattr(guard, "_urllib_open", forbidden)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": "http://127.0.0.1:12345"}))
    for target in ("https://api.openai.com/v1/models", urllib.request.Request("https://8.8.8.8/")):
        with pytest.raises(guard.OfflineNetworkDenied):
            opener.open(target)


def test_guard_denies_aiohttp_proxy_and_redirect_before_request(monkeypatch):
    import aiohttp
    from yarl import URL

    def forbidden(*_args, **_kwargs):
        raise AssertionError("aiohttp request must never be reached")

    monkeypatch.setattr(guard, "_aiohttp_request", forbidden)
    monkeypatch.setattr(guard, "_aiohttp_request_init", forbidden)

    async def run():
        async with aiohttp.ClientSession(trust_env=False) as session:
            with pytest.raises(guard.OfflineNetworkDenied):
                await session.get("https://api.openai.com/v1/models", proxy="http://127.0.0.1:12345")
        with pytest.raises(guard.OfflineNetworkDenied):
            aiohttp.ClientRequest("GET", URL("https://api.openai.com/v1/models"))

    asyncio.run(run())
