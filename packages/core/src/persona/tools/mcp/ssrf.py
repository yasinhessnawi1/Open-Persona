"""SSRF guard for bring-your-own MCP server URLs (Spec 30 T08, D-30-4).

A user supplies an MCP-server URL the runtime connects to **outbound** — the
server-side request forgery surface that is a live, exploited class for MCP
(Azure MCP CVE-2026-26118; mcp-atlassian CVE-2026-27826 → ``169.254.169.254``
IAM-token theft). This module is the single guard, used in two places so a
rebind between check and use cannot slip through (the TOCTOU the spec warns of):

1. **Eager, at add/test-connection** — :func:`assert_url_allowed` validates the
   scheme + resolves the host + checks every resolved IP.
2. **On every live connect** — :func:`pinned_httpx_client_factory` builds the
   httpx client the MCP SDK uses; its transport **resolve-then-pins**: it
   re-resolves + re-validates on *every* request (so a redirect or a DNS rebind
   is caught), then connects to the validated IP (preserving the original Host +
   TLS SNI/cert verification) so httpcore never independently re-resolves.

Defense: ``https`` only; block any host resolving to a private / loopback /
link-local (incl. cloud-metadata ``169.254.169.254``) / CGNAT / reserved /
multicast / unspecified address, including IPv4-mapped (``::ffff:``) and NAT64
(``64:ff9b::/96``) embedded-IPv4 forms. If **any** resolved address is blocked,
the whole host is rejected. Stdlib only (``ipaddress`` + ``socket``) — no
SSRF dependency.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx

from persona.errors import MCPUrlNotAllowedError

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "assert_url_allowed",
    "is_ip_blocked",
    "pinned_httpx_client_factory",
    "resolve_and_pin",
]

_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")
_ALLOWED_SCHEME = "https"


def _unwrap(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Unwrap IPv4-mapped (``::ffff:a.b.c.d``) and NAT64 (``64:ff9b::``) to the embedded v4.

    The stdlib classification flags do not see through these IPv6 wrappers, so a
    ``::ffff:169.254.169.254`` would otherwise read as a benign IPv6 address.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip in _NAT64_PREFIX:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip


def is_ip_blocked(ip_str: str) -> bool:
    """Return whether ``ip_str`` is a non-public address the guard must refuse.

    Unparseable input is blocked (fail-closed). Blocks loopback, RFC1918 private,
    link-local (incl. ``169.254.169.254``), ULA, CGNAT, reserved, multicast, and
    unspecified — anything not a globally-routable public address — after
    unwrapping IPv4-mapped / NAT64 forms.
    """
    try:
        ip = _unwrap(ipaddress.ip_address(ip_str))
    except ValueError:
        return True
    # Explicit flags + the is_global backstop (each is True only for non-public
    # ranges; is_global is False for the same set plus CGNAT/doc ranges).
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    return not ip.is_global


def _resolve(host: str, port: int | None) -> list[str]:
    """Resolve ``host`` to its IP strings (A + AAAA). Empty on failure."""
    try:
        infos = socket.getaddrinfo(host, port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    return [str(info[4][0]) for info in infos]


def _parse_https_host(url: str) -> tuple[str, int | None]:
    """Validate the scheme is ``https`` and return ``(host, port)``.

    Raises:
        MCPUrlNotAllowedError: scheme is not https, or no host.
    """
    parts = urlsplit(url)
    if parts.scheme != _ALLOWED_SCHEME:
        raise MCPUrlNotAllowedError(
            "MCP server URL must use https", context={"reason": "scheme_not_https"}
        )
    host = parts.hostname
    if not host:
        raise MCPUrlNotAllowedError("MCP server URL has no host", context={"reason": "no_host"})
    return host, parts.port


def assert_url_allowed(url: str) -> None:
    """Eager pre-flight: validate the scheme and that the host resolves to public IPs.

    Used at add / test-connection. The live-connect path re-validates per request
    (:func:`pinned_httpx_client_factory`) — this is the early, friendly rejection.

    Raises:
        MCPUrlNotAllowedError: bad scheme, no host, unresolvable, or any resolved
            address is non-public.
    """
    host, port = _parse_https_host(url)
    ips = _resolve(host, port)
    if not ips:
        raise MCPUrlNotAllowedError(
            "MCP server host could not be resolved", context={"reason": "unresolvable"}
        )
    if any(is_ip_blocked(ip) for ip in ips):
        raise MCPUrlNotAllowedError(
            "MCP server resolves to a non-public address",
            context={"reason": "blocked_target"},
        )


def resolve_and_pin(host: str, port: int | None, scheme: str) -> str:
    """Resolve + validate every IP for ``host`` and return one validated IP to pin to.

    Rejects if the scheme is not https, the host is unresolvable, or **any**
    resolved address is non-public (a partially-internal round-robin is refused
    whole). The returned IP is connected to directly so the HTTP client never
    re-resolves (defeats DNS rebinding).

    Raises:
        MCPUrlNotAllowedError: scheme/resolution/target failure.
    """
    if scheme != _ALLOWED_SCHEME:
        raise MCPUrlNotAllowedError(
            "MCP server URL must use https", context={"reason": "scheme_not_https"}
        )
    ips = _resolve(host, port)
    if not ips:
        raise MCPUrlNotAllowedError(
            "MCP server host could not be resolved", context={"reason": "unresolvable"}
        )
    if any(is_ip_blocked(ip) for ip in ips):
        raise MCPUrlNotAllowedError(
            "MCP server resolves to a non-public address",
            context={"reason": "blocked_target"},
        )
    return ips[0]


class _PinnedSSRFTransport(httpx.AsyncBaseTransport):
    """An httpx transport that resolve-then-pins every request (redirect-safe).

    On each request it re-resolves + re-validates the host (so a redirect hop or
    a DNS rebind between requests is caught), then rewrites the connect target to
    the validated IP while preserving the original ``Host`` header and TLS SNI
    (``sni_hostname`` extension) so certificate verification still matches the
    hostname. httpcore therefore connects to exactly the pinned IP — never its
    own re-resolution.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        port = request.url.port
        pinned = resolve_and_pin(host, port, request.url.scheme)
        # Preserve the SNI/cert hostname + Host header; connect to the pinned IP.
        request.extensions = {**request.extensions, "sni_hostname": host}
        host_header = host if port is None or port == 443 else f"{host}:{port}"
        request.headers["Host"] = host_header
        request.url = request.url.copy_with(host=pinned)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def pinned_httpx_client_factory(
    headers: Mapping[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """Build the SSRF-pinned httpx client the MCP SDK uses (the ``httpx_client_factory``).

    Matches ``mcp.shared._httpx_utils.McpHttpClientFactory``'s call shape
    ``(headers, timeout, auth)``. Redirects are followed but every hop re-enters
    the pinning transport, so a redirect to an internal host is refused too.
    """
    inner = httpx.AsyncHTTPTransport()
    return httpx.AsyncClient(
        transport=_PinnedSSRFTransport(inner),
        headers=dict(headers) if headers else None,
        timeout=timeout if timeout is not None else httpx.Timeout(30.0),
        auth=auth,
        follow_redirects=True,
    )
