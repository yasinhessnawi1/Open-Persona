"""``web_fetch`` built-in tool — fetch a URL and extract readable content.

Uses :mod:`httpx` (already a core dep) for the HTTP request and
:mod:`trafilatura` for HTML content extraction (D-03-12). Non-HTML
responses pass through with light cleanup via ``httpx.Response.text``.

Truncation: if extracted text exceeds ``max_chars``, truncate to
``max_chars`` and set ``ToolResult.truncated=True`` (D-03-3).

Scheme allow-list (``http``/``https`` only) is enforced, plus an **SSRF guard**
(D-03-11 / D-11-6, spec 11): the target hostname is resolved and the request is
rejected if any resolved IP is non-public (RFC-1918 private, loopback,
link-local — including the cloud-metadata ``169.254.169.254`` — reserved,
multicast, or unspecified). Resolving and checking the *resolved* IP defends
against DNS-rebinding (a public hostname pointing at an internal address).

`trafilatura.extract()` is called with explicit kwargs locked in by D-03-24
to keep the tool's behaviour stable across upstream version changes.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx
import trafilatura

from persona.logging import get_logger
from persona.schema.tools import ToolResult
from persona.tools.protocol import AsyncTool, tool

__all__ = ["make_web_fetch_tool"]

_logger = get_logger("tools.web_fetch")

_DEFAULT_TIMEOUT_S = 30.0
_ALLOWED_SCHEMES = ("http", "https")
# Bound the redirect chain — the SSRF guard re-checks every hop's resolved IP.
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


async def _ssrf_block_reason(hostname: str) -> str | None:
    """Return a reason string if ``hostname`` resolves to a non-public address.

    SSRF guard (D-11-6): resolve the host and reject if ANY resolved IP is
    private / loopback / link-local (incl. the cloud-metadata 169.254.169.254) /
    reserved / multicast / unspecified. Checking the *resolved* IP (not the
    literal hostname) blocks DNS-rebinding. Returns ``None`` when every resolved
    address is public, or when resolution fails (the HTTP layer then surfaces a
    normal network error rather than this guard masking it).
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, None)
    except (socket.gaierror, UnicodeError, OSError):
        return None
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str.split("%", 1)[0])  # strip any zone id
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return f"URL resolves to a non-public address ({ip_str}); blocked to prevent SSRF"
    return None


def _extract_readable(html: str) -> str:
    """Run trafilatura with the kwargs locked in by D-03-24.

    Returns the extracted text, or empty string if nothing extractable.
    """
    extracted = trafilatura.extract(
        html,
        output_format="txt",
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    return extracted or ""


def make_web_fetch_tool(
    *,
    http: httpx.AsyncClient | None = None,
) -> AsyncTool:
    """Build the ``web_fetch`` :class:`AsyncTool`.

    Args:
        http: Optional pre-built :class:`httpx.AsyncClient`. If ``None``, a
            client is constructed per call with a 30s timeout. Tests inject
            a mock client; callers (e.g., the runtime) may inject a shared
            client to amortise connection setup.

    Returns:
        An :class:`AsyncTool` named ``web_fetch``. Failures are returned as
        ``ToolResult(is_error=True, content=...)`` — never raised.
    """

    @tool(
        name="web_fetch",
        description="Fetch a URL and extract its readable text content.",
    )
    async def web_fetch(url: str, max_chars: int = 4000) -> ToolResult:
        # Scheme guard (D-03-11). Full SSRF defense lives in spec 11.
        try:
            parsed = urlparse(url)
        except ValueError as e:
            return ToolResult(
                tool_name="web_fetch",
                content=f"Invalid URL: {e}",
                is_error=True,
            )
        if parsed.scheme not in _ALLOWED_SCHEMES:
            return ToolResult(
                tool_name="web_fetch",
                content=f"Only http/https URLs allowed; got scheme {parsed.scheme!r}",
                is_error=True,
            )
        if not parsed.netloc:
            return ToolResult(
                tool_name="web_fetch",
                content="URL missing host component",
                is_error=True,
            )

        # SSRF guard (D-11-6, spec 11) — resolved-IP check defends against DNS-rebind.
        hostname = parsed.hostname or ""
        if hostname:
            block_reason = await _ssrf_block_reason(hostname)
            if block_reason is not None:
                _logger.warning("web_fetch ssrf blocked", url=url, host=hostname)
                return ToolResult(
                    tool_name="web_fetch",
                    content=block_reason,
                    is_error=True,
                )

        owns_client = http is None
        client = (
            http
            if http is not None
            else httpx.AsyncClient(timeout=httpx.Timeout(_DEFAULT_TIMEOUT_S))
        )

        try:
            # Manual redirect following with a per-hop SSRF re-check (spec 11 T07b
            # security review). httpx's transparent ``follow_redirects=True`` would
            # silently chase a public→private redirect (e.g. an attacker server
            # 302-ing to 169.254.169.254) and bypass the initial guard entirely.
            current_url = url
            visited: set[str] = set()
            response: httpx.Response | None = None
            for _hop in range(_MAX_REDIRECTS + 1):
                try:
                    response = await client.get(current_url, follow_redirects=False)
                except httpx.TimeoutException as e:
                    _logger.warning("web_fetch timeout", url=current_url, error=type(e).__name__)
                    return ToolResult(
                        tool_name="web_fetch",
                        content=f"Timeout fetching {current_url}: {type(e).__name__}",
                        is_error=True,
                    )
                except httpx.HTTPError as e:
                    _logger.warning(
                        "web_fetch network error", url=current_url, error=type(e).__name__
                    )
                    return ToolResult(
                        tool_name="web_fetch",
                        content=f"Network error fetching {current_url}: {type(e).__name__}: {e}",
                        is_error=True,
                    )
                if response.status_code not in _REDIRECT_STATUSES:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as e:
                        status = e.response.status_code
                        _logger.warning("web_fetch http error", url=current_url, status=status)
                        return ToolResult(
                            tool_name="web_fetch",
                            content=f"HTTP {status}: {e.response.reason_phrase or 'error'}",
                            is_error=True,
                        )
                    break
                # 3xx — re-check SSRF on the redirect target before following.
                if current_url in visited:
                    return ToolResult(
                        tool_name="web_fetch", content="Redirect loop detected", is_error=True
                    )
                visited.add(current_url)
                location = response.headers.get("location") or ""
                if not location:
                    return ToolResult(
                        tool_name="web_fetch",
                        content="3xx response missing Location header",
                        is_error=True,
                    )
                cur_parsed = urlparse(current_url)
                if location.startswith(("http://", "https://")):
                    next_url = location
                elif location.startswith("/"):
                    next_url = f"{cur_parsed.scheme}://{cur_parsed.netloc}{location}"
                else:
                    return ToolResult(
                        tool_name="web_fetch",
                        content=f"Unsupported redirect target: {location!r}",
                        is_error=True,
                    )
                next_parsed = urlparse(next_url)
                if next_parsed.scheme not in _ALLOWED_SCHEMES:
                    return ToolResult(
                        tool_name="web_fetch",
                        content=f"Redirect to disallowed scheme: {next_parsed.scheme!r}",
                        is_error=True,
                    )
                next_host = next_parsed.hostname or ""
                if next_host:
                    block_reason = await _ssrf_block_reason(next_host)
                    if block_reason is not None:
                        _logger.warning(
                            "web_fetch ssrf blocked on redirect",
                            origin=url,
                            redirect_to=next_url,
                            host=next_host,
                        )
                        return ToolResult(
                            tool_name="web_fetch",
                            content=f"{block_reason} (via redirect from {url})",
                            is_error=True,
                        )
                current_url = next_url
            else:
                return ToolResult(
                    tool_name="web_fetch",
                    content=f"Too many redirects (> {_MAX_REDIRECTS})",
                    is_error=True,
                )
            assert response is not None  # for type checker — loop always assigns

            # Decide whether to run trafilatura or pass through.
            content_type = response.headers.get("content-type", "").lower()
            if "html" in content_type:
                text = _extract_readable(response.text)
                if not text:
                    # Empty extraction — usually JavaScript-heavy page.
                    _logger.debug("web_fetch empty extraction", url=url)
                    return ToolResult(
                        tool_name="web_fetch",
                        content="",
                        truncated=False,
                        data={"url": url, "content_type": content_type, "extracted": False},
                    )
            else:
                text = response.text

        finally:
            if owns_client:
                await client.aclose()

        # Truncation per D-03-3 / D-03-16 pattern.
        if len(text) > max_chars:
            return ToolResult(
                tool_name="web_fetch",
                content=text[:max_chars],
                truncated=True,
                data={
                    "url": url,
                    "content_type": content_type,
                    "extracted": "html" in content_type,
                    "original_length": len(text),
                },
            )

        return ToolResult(
            tool_name="web_fetch",
            content=text,
            truncated=False,
            data={
                "url": url,
                "content_type": content_type,
                "extracted": "html" in content_type,
            },
        )

    return web_fetch
