"""``web_search`` built-in tool — searches the web via a pluggable provider.

Provider selected via ``PERSONA_WEB_SEARCH_PROVIDER`` (default ``brave``).
API key read from ``PERSONA_WEB_SEARCH_API_KEY`` (D-03-10 — single env var).

Failures are returned as ``ToolResult(is_error=True, content=...)`` per the
``@tool`` decorator's no-raise contract (D-03-5). HTTP errors, missing keys,
and unknown providers all surface as informative ``ToolResult``s — the model
sees a string it can adapt to.

Structured results live in ``ToolResult.data["results"]`` (per D-03-3); the
human-readable summary lives in ``ToolResult.content``.
"""

from __future__ import annotations

import os

import httpx

from persona.logging import get_logger
from persona.schema.tools import ToolResult
from persona.tools.builtin._search_providers import (
    BraveSearchProvider,
    SearchResult,
    SerpAPISearchProvider,
    TavilySearchProvider,
    _SearchProvider,
)
from persona.tools.protocol import AsyncTool, tool

__all__ = ["make_web_search_tool"]

_logger = get_logger("tools.web_search")


_PROVIDER_CLASSES: dict[str, type[_SearchProvider]] = {
    "brave": BraveSearchProvider,
    "tavily": TavilySearchProvider,
    "serpapi": SerpAPISearchProvider,
}


def _format_results(results: list[SearchResult]) -> str:
    """Human-readable bullet list for ``ToolResult.content``."""
    if not results:
        return "No results."
    lines = []
    for i, r in enumerate(results, start=1):
        lines.append(f"{i}. {r.title}")
        lines.append(f"   {r.url}")
        if r.snippet:
            lines.append(f"   {r.snippet}")
    return "\n".join(lines)


def make_web_search_tool(
    *,
    provider_name: str | None = None,
    api_key: str | None = None,
    http: httpx.AsyncClient | None = None,
) -> AsyncTool:
    """Build the ``web_search`` :class:`AsyncTool`.

    Args:
        provider_name: Provider id; defaults to ``PERSONA_WEB_SEARCH_PROVIDER``
            env var, then ``"brave"``.
        api_key: API key; defaults to ``PERSONA_WEB_SEARCH_API_KEY`` env var.
        http: Optional pre-built :class:`httpx.AsyncClient`. If ``None``, a
            client is constructed per call with a sensible timeout. (Tests
            inject a mock client.)

    Returns:
        An :class:`AsyncTool` named ``web_search``. Failures during search
        are returned as ``ToolResult(is_error=True, content=...)`` — never
        raised.
    """
    selected_provider = provider_name or os.environ.get("PERSONA_WEB_SEARCH_PROVIDER", "brave")
    selected_key = api_key if api_key is not None else os.environ.get("PERSONA_WEB_SEARCH_API_KEY")

    @tool(
        name="web_search",
        description=(
            "YOU CAN search the web. Use this tool whenever the user asks for "
            "current information or anything you should look up — do not say you "
            "lack internet access: call this tool. Returns results with titles, "
            "URLs, and snippets."
        ),
    )
    async def web_search(query: str, max_results: int = 5) -> ToolResult:
        if selected_provider not in _PROVIDER_CLASSES:
            return ToolResult(
                tool_name="web_search",
                content=(
                    f"Unknown search provider: {selected_provider!r}. "
                    f"Supported: {sorted(_PROVIDER_CLASSES)}"
                ),
                is_error=True,
            )
        if not selected_key:
            return ToolResult(
                tool_name="web_search",
                content=(
                    "Missing PERSONA_WEB_SEARCH_API_KEY (or api_key argument). "
                    "Set the env var to your search provider's API key."
                ),
                is_error=True,
            )

        # Build (or reuse) the httpx client.
        owns_client = http is None
        client = http if http is not None else httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        try:
            provider_cls = _PROVIDER_CLASSES[selected_provider]
            provider = provider_cls(selected_key, client)  # type: ignore[call-arg]
            try:
                results = await provider.search(query, max_results)
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in (401, 403):
                    msg = (
                        f"Authentication failed (HTTP {status}); check PERSONA_WEB_SEARCH_API_KEY."
                    )
                elif status == 429:
                    msg = f"Rate limit exceeded (HTTP {status}); slow down or upgrade plan."
                else:
                    msg = f"Search provider returned HTTP {status}: {e.response.text[:200]}"
                _logger.warning(
                    "web_search http error",
                    provider=selected_provider,
                    status=status,
                )
                return ToolResult(tool_name="web_search", content=msg, is_error=True)
            except httpx.HTTPError as e:
                _logger.warning(
                    "web_search network error",
                    provider=selected_provider,
                    error=type(e).__name__,
                )
                return ToolResult(
                    tool_name="web_search",
                    content=(
                        f"Network error contacting {selected_provider}: {type(e).__name__}: {e}"
                    ),
                    is_error=True,
                )
        finally:
            if owns_client:
                await client.aclose()

        return ToolResult(
            tool_name="web_search",
            content=_format_results(results),
            data={
                "results": [
                    {"title": r.title, "url": r.url, "snippet": r.snippet} for r in results
                ],
                "provider": selected_provider,
            },
        )

    return web_search
