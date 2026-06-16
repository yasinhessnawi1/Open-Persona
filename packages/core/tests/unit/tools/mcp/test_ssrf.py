"""SSRF guard for bring-your-own MCP URLs (Spec 30 T08, D-30-4).

Covers the block matrix (incl. IPv4-mapped + NAT64 + cloud-metadata), the
https-only + resolution checks, resolve-then-pin (any-blocked → reject whole),
and the pinned transport rewrite (connect to the validated IP, preserve Host +
SNI). Resolution-dependent tests monkeypatch ``socket.getaddrinfo`` — no network.
"""

from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest
from persona.errors import MCPUrlNotAllowedError
from persona.tools.mcp import ssrf


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # RFC1918
        "172.16.3.4",  # RFC1918
        "192.168.1.1",  # RFC1918
        "169.254.169.254",  # link-local — cloud metadata
        "0.0.0.0",  # unspecified
        "100.64.0.1",  # CGNAT
        "224.0.0.1",  # multicast
        "240.0.0.1",  # reserved
        "::1",  # IPv6 loopback
        "fc00::1",  # IPv6 ULA
        "fe80::1",  # IPv6 link-local
        "::ffff:169.254.169.254",  # IPv4-mapped metadata
        "64:ff9b::a9fe:a9fe",  # NAT64-embedded 169.254.169.254
        "not-an-ip",  # unparseable → fail-closed
    ],
)
def test_blocked_ips(ip: str) -> None:
    assert ssrf.is_ip_blocked(ip) is True


@pytest.mark.parametrize("ip", ["1.1.1.1", "8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"])
def test_public_ips_allowed(ip: str) -> None:
    assert ssrf.is_ip_blocked(ip) is False


def _patch_resolve(monkeypatch: pytest.MonkeyPatch, ips: list[str]) -> None:
    def fake_getaddrinfo(_host: str, port: int | None, *_a: object, **_k: object) -> list[Any]:
        if not ips:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 443)) for ip in ips]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def test_assert_url_allowed_rejects_non_https(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolve(monkeypatch, ["1.1.1.1"])
    with pytest.raises(MCPUrlNotAllowedError) as e:
        ssrf.assert_url_allowed("http://example.com/mcp")
    assert e.value.context["reason"] == "scheme_not_https"


def test_assert_url_allowed_rejects_loopback_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolve(monkeypatch, ["127.0.0.1"])
    with pytest.raises(MCPUrlNotAllowedError) as e:
        ssrf.assert_url_allowed("https://sneaky.example.com/mcp")
    assert e.value.context["reason"] == "blocked_target"


def test_assert_url_allowed_rejects_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolve(monkeypatch, [])
    with pytest.raises(MCPUrlNotAllowedError) as e:
        ssrf.assert_url_allowed("https://nope.invalid/mcp")
    assert e.value.context["reason"] == "unresolvable"


def test_assert_url_allowed_accepts_public(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolve(monkeypatch, ["93.184.216.34"])
    ssrf.assert_url_allowed("https://example.com/mcp")  # no raise


def test_resolve_and_pin_rejects_if_any_ip_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    # Round-robin where one record is internal → reject the whole host.
    _patch_resolve(monkeypatch, ["93.184.216.34", "169.254.169.254"])
    with pytest.raises(MCPUrlNotAllowedError):
        ssrf.resolve_and_pin("example.com", 443, "https")


def test_resolve_and_pin_returns_validated_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolve(monkeypatch, ["93.184.216.34"])
    assert ssrf.resolve_and_pin("example.com", 443, "https") == "93.184.216.34"


class _FakeInner(httpx.AsyncBaseTransport):
    """Captures the request the pinned transport hands down."""

    def __init__(self) -> None:
        self.seen: httpx.Request | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen = request
        return httpx.Response(200, content=b"ok")

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_pinned_transport_rewrites_to_validated_ip_and_preserves_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolve(monkeypatch, ["93.184.216.34"])
    inner = _FakeInner()
    transport = ssrf._PinnedSSRFTransport(inner)
    req = httpx.Request("POST", "https://example.com/mcp")
    await transport.handle_async_request(req)
    assert inner.seen is not None
    # Connected to the pinned IP, with the original Host + SNI preserved.
    assert inner.seen.url.host == "93.184.216.34"
    assert inner.seen.headers["Host"] == "example.com"
    assert inner.seen.extensions.get("sni_hostname") == "example.com"


@pytest.mark.asyncio
async def test_pinned_transport_blocks_rebind_to_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The host now resolves to metadata (a rebind between check and connect).
    _patch_resolve(monkeypatch, ["169.254.169.254"])
    transport = ssrf._PinnedSSRFTransport(_FakeInner())
    req = httpx.Request("POST", "https://rebind.example.com/mcp")
    with pytest.raises(MCPUrlNotAllowedError):
        await transport.handle_async_request(req)


def test_pinned_factory_builds_client_with_pinning_transport() -> None:
    client = ssrf.pinned_httpx_client_factory(headers={"x": "y"}, timeout=None, auth=None)
    assert isinstance(client._transport, ssrf._PinnedSSRFTransport)


class _RedirectingInner(httpx.AsyncBaseTransport):
    """Returns a 302 to an internal IP on the first hop; records hop count.

    Reached only on a hop that passed validation — the redirect target's hop is
    refused inside the pinning transport BEFORE it reaches here.
    """

    def __init__(self, location: str) -> None:
        self._location = location
        self.hops = 0

    async def handle_async_request(self, _request: httpx.Request) -> httpx.Response:
        self.hops += 1
        return httpx.Response(302, headers={"Location": self._location})

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_redirect_to_internal_ip_is_refused_through_real_httpx_redirect() -> None:
    """A 3xx → internal IP is blocked on the redirect hop (redirect-SSRF bypass).

    Distinct from DNS rebinding: here the initial URL is public + valid, but the
    server returns ``302 Location: https://169.254.169.254/``. httpx follows the
    redirect by re-invoking the transport, which resolve-then-pins (and thus
    re-validates) the new target → refused. IP-literal hosts resolve to
    themselves, so this needs no DNS mock — it exercises the real httpx
    follow-redirects machinery end to end.
    """
    inner = _RedirectingInner("https://169.254.169.254/")
    transport = ssrf._PinnedSSRFTransport(inner)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        with pytest.raises(MCPUrlNotAllowedError) as exc:
            await client.get("https://93.184.216.34/")  # public initial target
    assert exc.value.context["reason"] == "blocked_target"
    # The first (public) hop reached the server; the redirect hop was refused in
    # the transport before any connection to the internal target.
    assert inner.hops == 1
