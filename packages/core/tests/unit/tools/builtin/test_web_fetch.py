"""Tests for the web_fetch built-in tool (T08)."""

# ruff: noqa: ANN401, ARG001, ARG002, ERA001
from __future__ import annotations

from typing import Any

import httpx
import pytest
from persona.tools.builtin.web_fetch import make_web_fetch_tool
from persona.tools.protocol import AsyncTool

# Section: helpers


def _make_mock_http(handler: Any) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0))


_SAMPLE_HTML = """\
<html>
<head><title>Norwegian Tenancy Law</title></head>
<body>
<nav>boilerplate nav</nav>
<article>
<h1>The Tenancy Act of 1999</h1>
<p>The Norwegian Tenancy Act provides the framework for residential leases.
Tenants have certain rights to security of tenure and protection against
unreasonable rent increases.</p>
<p>Rent disputes go to the Husleietvistutvalget (HTU), a specialised tribunal
operating in Oslo, Bergen, Trondheim, and northern Norway.</p>
</article>
<footer>boilerplate footer</footer>
</body>
</html>
"""


# Section: HTML extraction happy path


class TestHTMLExtraction:
    @pytest.mark.asyncio
    async def test_extracts_readable_content(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=_SAMPLE_HTML,
                headers={"content-type": "text/html; charset=utf-8"},
            )

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_fetch_tool(http=client)
            result = await tool_inst.execute(url="https://example.no/tenancy")

        assert result.is_error is False
        # The extracted text should contain article body but not nav/footer.
        assert "Tenancy Act" in result.content
        assert "Husleietvistutvalget" in result.content
        assert "boilerplate" not in result.content
        assert result.truncated is False
        assert result.data is not None
        assert result.data["url"] == "https://example.no/tenancy"
        assert result.data["extracted"] is True

    @pytest.mark.asyncio
    async def test_truncates_long_content(self) -> None:
        long_paragraph = "Tenancy law content. " * 1000  # ~21000 chars
        html = (
            "<html><body><article><h1>x</h1><p>" + long_paragraph + "</p></article></body></html>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_fetch_tool(http=client)
            result = await tool_inst.execute(url="https://x.com/", max_chars=500)

        assert result.is_error is False
        assert result.truncated is True
        assert len(result.content) == 500
        assert result.data is not None
        assert result.data["original_length"] > 500

    @pytest.mark.asyncio
    async def test_empty_extraction_returns_empty_content(self) -> None:
        # A page with no extractable content (e.g., JS-only or empty body).
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text="<html><body></body></html>",
                headers={"content-type": "text/html"},
            )

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_fetch_tool(http=client)
            result = await tool_inst.execute(url="https://x.com/empty")

        assert result.is_error is False
        assert result.content == ""
        assert result.data is not None
        assert result.data["extracted"] is False


# Section: non-HTML pass-through


class TestNonHTMLPassthrough:
    @pytest.mark.asyncio
    async def test_text_plain_passes_through(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text="Plain text response body.",
                headers={"content-type": "text/plain"},
            )

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_fetch_tool(http=client)
            result = await tool_inst.execute(url="https://x.com/raw.txt")

        assert result.is_error is False
        assert result.content == "Plain text response body."
        assert result.data is not None
        assert result.data["extracted"] is False

    @pytest.mark.asyncio
    async def test_json_passes_through(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text='{"key": "value"}',
                headers={"content-type": "application/json"},
            )

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_fetch_tool(http=client)
            result = await tool_inst.execute(url="https://x.com/api")

        assert result.is_error is False
        assert "value" in result.content

    @pytest.mark.asyncio
    async def test_non_html_truncates_too(self) -> None:
        big_text = "x" * 8000

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=big_text, headers={"content-type": "text/plain"})

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_fetch_tool(http=client)
            result = await tool_inst.execute(url="https://x.com/big", max_chars=100)

        assert result.is_error is False
        assert result.truncated is True
        assert len(result.content) == 100


# Section: scheme guard (D-03-11)


class TestSchemeGuard:
    @pytest.mark.asyncio
    async def test_rejects_ftp(self) -> None:
        tool_inst = make_web_fetch_tool()
        result = await tool_inst.execute(url="ftp://files.example/x")
        assert result.is_error is True
        assert "http/https" in result.content

    @pytest.mark.asyncio
    async def test_rejects_file(self) -> None:
        tool_inst = make_web_fetch_tool()
        result = await tool_inst.execute(url="file:///etc/passwd")
        assert result.is_error is True
        assert "http/https" in result.content

    @pytest.mark.asyncio
    async def test_rejects_gopher(self) -> None:
        tool_inst = make_web_fetch_tool()
        result = await tool_inst.execute(url="gopher://example.com/")
        assert result.is_error is True
        assert "http/https" in result.content

    @pytest.mark.asyncio
    async def test_rejects_missing_host(self) -> None:
        tool_inst = make_web_fetch_tool()
        result = await tool_inst.execute(url="https://")
        assert result.is_error is True
        assert "host" in result.content.lower()


# Section: SSRF guard (D-11-6, spec 11) — blocks non-public addresses


class TestSSRFGuard:
    """Resolved-IP block of non-public addresses (RFC-1918, loopback, link-local
    incl. the cloud-metadata 169.254.169.254, reserved, multicast). The HTTP
    layer must NEVER be reached — verified by injecting a transport that raises
    if invoked."""

    @staticmethod
    def _raising_transport() -> httpx.AsyncClient:
        def _explode(_request: httpx.Request) -> httpx.Response:
            msg = "SSRF guard failed — HTTP call should have been blocked"
            raise AssertionError(msg)

        return _make_mock_http(_explode)

    @pytest.mark.parametrize(
        ("url", "label"),
        [
            ("http://127.0.0.1/", "loopback v4"),
            ("http://10.0.0.5/", "rfc1918 10/8"),
            ("http://172.16.0.1/", "rfc1918 172.16/12"),
            ("http://192.168.1.1/", "rfc1918 192.168/16"),
            ("http://169.254.169.254/latest/meta-data/", "cloud metadata"),
            ("http://0.0.0.0/", "unspecified"),
            ("http://[::1]/", "loopback v6"),
            ("http://[fe80::1]/", "link-local v6"),
        ],
        ids=lambda v: v if isinstance(v, str) else "",
    )
    @pytest.mark.asyncio
    async def test_blocks_non_public_literal_ip(self, url: str, label: str) -> None:
        tool_inst = make_web_fetch_tool(http=self._raising_transport())
        result = await tool_inst.execute(url=url)
        assert result.is_error is True, f"{label}: not blocked"
        assert "non-public" in result.content or "SSRF" in result.content

    @pytest.mark.asyncio
    async def test_blocks_redirect_to_private_ip(self) -> None:
        # T07b security-review finding: a public server's 302 to a private IP
        # MUST NOT be followed transparently. We feed the tool a public-looking
        # initial URL, the mock transport 302s to http://10.0.0.1/, and the
        # SSRF guard MUST refuse the redirect (asserting on the URL the transport
        # was asked for, since the second fetch would expose the private host).
        calls: list[str] = []

        def _redirect_then_explode(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if str(request.url) == "http://1.1.1.1/start":
                return httpx.Response(
                    302, headers={"location": "http://10.0.0.1/internal"}
                )
            msg = f"SSRF re-check failed — second fetch reached {request.url}"
            raise AssertionError(msg)

        tool_inst = make_web_fetch_tool(http=_make_mock_http(_redirect_then_explode))
        result = await tool_inst.execute(url="http://1.1.1.1/start")
        assert result.is_error is True
        assert "non-public" in result.content or "10.0.0.1" in result.content
        # Only the FIRST hop was made; the redirect destination was never fetched.
        assert calls == ["http://1.1.1.1/start"]

    @pytest.mark.asyncio
    async def test_blocks_dns_rebind(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A public-looking hostname that resolves to a private IP must be blocked
        # by the RESOLVED-IP check (not the literal hostname).
        from persona.tools.builtin import web_fetch as wf

        async def _fake_block_reason(_host: str) -> str | None:
            return "URL resolves to a non-public address (10.0.0.42); blocked to prevent SSRF"

        monkeypatch.setattr(wf, "_ssrf_block_reason", _fake_block_reason)
        tool_inst = make_web_fetch_tool(http=self._raising_transport())
        result = await tool_inst.execute(url="http://rebind.example.com/")
        assert result.is_error is True
        assert "non-public" in result.content


# Section: HTTP error mapping


class TestHTTPErrorMapping:
    @pytest.mark.asyncio
    async def test_404(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="Not Found")

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_fetch_tool(http=client)
            result = await tool_inst.execute(url="https://x.com/missing")

        assert result.is_error is True
        assert "404" in result.content

    @pytest.mark.asyncio
    async def test_500(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_fetch_tool(http=client)
            result = await tool_inst.execute(url="https://x.com/broken")

        assert result.is_error is True
        assert "500" in result.content

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timeout")

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_fetch_tool(http=client)
            result = await tool_inst.execute(url="https://x.com/slow")

        assert result.is_error is True
        assert "Timeout" in result.content or "timeout" in result.content

    @pytest.mark.asyncio
    async def test_connection_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("DNS failed")

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_fetch_tool(http=client)
            result = await tool_inst.execute(url="https://nonexistent.example/")

        assert result.is_error is True
        assert "Network" in result.content or "ConnectError" in result.content


# Section: redirects


class TestRedirects:
    @pytest.mark.asyncio
    async def test_follows_redirects(self) -> None:
        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return httpx.Response(
                    301,
                    headers={"location": "https://example.com/dest"},
                )
            html = (
                "<html><body><article>"
                "<p>final destination text content here</p>"
                "</article></body></html>"
            )
            return httpx.Response(
                200,
                text=html,
                headers={"content-type": "text/html"},
            )

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_fetch_tool(http=client)
            result = await tool_inst.execute(url="https://example.com/start")

        assert result.is_error is False
        assert call_count["n"] == 2


# Section: AsyncTool conformance


class TestAsyncToolConformance:
    def test_satisfies_async_tool(self) -> None:
        tool_inst = make_web_fetch_tool()
        assert isinstance(tool_inst, AsyncTool)
        assert tool_inst.name == "web_fetch"
        assert "url" in tool_inst.parameters_schema["properties"]
        assert "max_chars" in tool_inst.parameters_schema["properties"]
