"""``currency_convert`` built-in tool — cross-currency conversion (spec 26 T04).

Default provider is **Frankfurter** (``api.frankfurter.dev``): ECB-class
reference rates, **no API key**, no quota, HTTPS, and itself open-source /
self-hostable — so the tool works out of the box on ``pip install`` with zero
configuration (D-26-6 / D-26-X-currency-no-key-default-rationale).
``exchangerate_api`` (``open.er-api.com``) is a no-key alternate.

Provider + optional key come from :class:`PersonaCoreConfig`
(``PERSONA_CURRENCY_PROVIDER`` / ``PERSONA_CURRENCY_API_KEY``). The missing-key
guard is **provider-conditional** — only providers in :data:`_KEYED_PROVIDERS`
require a key; the no-key defaults proceed without one (unlike ``web_search``,
which hard-errors on any missing key).

Every failure — unknown provider, missing key for a keyed provider, unknown
currency code, HTTP error, network error, malformed JSON — is returned as
``ToolResult(is_error=True, content=...)`` and never raised (D-03-5). Daily
reference rates (not intraday) are adequate for a conversational utility.
"""

from __future__ import annotations

import os
import re

import httpx

from persona.logging import get_logger
from persona.schema.tools import ToolResult
from persona.tools.protocol import AsyncTool, tool

__all__ = ["make_currency_convert_tool"]

_logger = get_logger("tools.currency_convert")

_DEFAULT_TIMEOUT_S = 15.0
_CODE_RE = re.compile(r"^[A-Za-z]{3}$")
# Providers whose free tier requires an API key (provider-conditional guard,
# D-26-6). None are bundled yet; the set keeps the guard correct as future
# keyed providers (fixer / openexchangerates) are added.
_KEYED_PROVIDERS: frozenset[str] = frozenset()
_SUPPORTED_PROVIDERS = ("frankfurter", "exchangerate_api")


def _err(content: str) -> ToolResult:
    return ToolResult(tool_name="currency_convert", content=content, is_error=True)


async def _rates_from_frankfurter(
    client: httpx.AsyncClient, base: str, target: str
) -> tuple[float, str]:
    """Return ``(rate, date)`` for ``base``→``target`` from Frankfurter."""
    resp = await client.get(
        "https://api.frankfurter.dev/v1/latest",
        params={"base": base, "symbols": target},
    )
    resp.raise_for_status()
    payload = resp.json()
    rate = float(payload["rates"][target])
    return rate, str(payload.get("date", ""))


async def _rates_from_exchangerate_api(
    client: httpx.AsyncClient, base: str, target: str
) -> tuple[float, str]:
    """Return ``(rate, date)`` for ``base``→``target`` from open.er-api.com."""
    resp = await client.get(f"https://open.er-api.com/v6/latest/{base}")
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("result") != "success":
        raise KeyError("result")
    rate = float(payload["rates"][target])
    return rate, str(payload.get("time_last_update_utc", ""))


_PROVIDERS = {
    "frankfurter": _rates_from_frankfurter,
    "exchangerate_api": _rates_from_exchangerate_api,
}


def make_currency_convert_tool(
    *,
    provider_name: str | None = None,
    api_key: str | None = None,
    http: httpx.AsyncClient | None = None,
) -> AsyncTool:
    """Build the ``currency_convert`` :class:`AsyncTool`.

    Args:
        provider_name: Provider id; defaults to ``PERSONA_CURRENCY_PROVIDER``
            env var, then ``"frankfurter"``.
        api_key: API key for keyed providers; defaults to
            ``PERSONA_CURRENCY_API_KEY``. Unused by the no-key defaults.
        http: Optional pre-built :class:`httpx.AsyncClient` (tests inject a
            mock). If ``None``, a client is constructed per call.

    Returns:
        An :class:`AsyncTool` named ``currency_convert``. Failures are returned
        as ``ToolResult(is_error=True, content=...)`` — never raised.
    """
    selected_provider = provider_name or os.environ.get("PERSONA_CURRENCY_PROVIDER", "frankfurter")
    selected_key = api_key if api_key is not None else os.environ.get("PERSONA_CURRENCY_API_KEY")

    @tool(
        name="currency_convert",
        description=(
            "YOU CAN convert money between currencies at current exchange rates. "
            "Use this tool instead of guessing rates. Provide amount, "
            "from_currency, and to_currency as ISO 4217 codes (e.g. amount=50, "
            "from_currency='EUR', to_currency='NOK'). Rates are daily reference "
            "rates."
        ),
    )
    async def currency_convert(amount: float, from_currency: str, to_currency: str) -> ToolResult:
        if selected_provider not in _PROVIDERS:
            return _err(
                f"Unknown currency provider: {selected_provider!r}. "
                f"Supported: {list(_SUPPORTED_PROVIDERS)}."
            )
        # Provider-conditional key guard (D-26-6): only keyed providers need one.
        if selected_provider in _KEYED_PROVIDERS and not selected_key:
            return _err(f"Provider {selected_provider} requires PERSONA_CURRENCY_API_KEY.")
        base = from_currency.strip().upper()
        target = to_currency.strip().upper()
        if not _CODE_RE.match(base) or not _CODE_RE.match(target):
            return _err(
                "Currency codes must be ISO 4217 (three letters, e.g. 'USD', 'EUR', 'NOK')."
            )
        if base == target:
            return ToolResult(
                tool_name="currency_convert",
                content=f"{amount:g} {base} = {amount:g} {target} (rate 1.0)",
                data={"amount": amount, "from": base, "to": target, "rate": 1.0},
            )

        owns_client = http is None
        client = http if http is not None else httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S)
        try:
            try:
                rate, date = await _PROVIDERS[selected_provider](client, base, target)
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status == 429:
                    return _err(
                        "Rate limit exceeded (HTTP 429); try again shortly or "
                        "configure a keyed provider."
                    )
                if status in (401, 403):
                    return _err(
                        f"Authentication failed (HTTP {status}); check PERSONA_CURRENCY_API_KEY."
                    )
                if status == 404:
                    return _err(f"Unknown currency code: {base!r}. Use ISO 4217 codes.")
                return _err(f"Currency provider returned HTTP {status}.")
            except httpx.HTTPError as e:
                _logger.warning(
                    "currency_convert network error",
                    provider=selected_provider,
                    error=type(e).__name__,
                )
                return _err(f"Network error contacting {selected_provider}: {type(e).__name__}.")
            except (KeyError, ValueError, TypeError) as e:
                _logger.warning(
                    "currency_convert bad response",
                    provider=selected_provider,
                    error=type(e).__name__,
                )
                return _err(
                    f"Could not find a rate for {base}->{target} (unknown code or "
                    "unexpected provider response)."
                )
        finally:
            if owns_client:
                await client.aclose()

        converted = amount * rate
        return ToolResult(
            tool_name="currency_convert",
            content=(
                f"{amount:g} {base} = {converted:.2f} {target} "
                f"(rate {rate:g}{f', {date}' if date else ''}, {selected_provider})"
            ),
            data={
                "amount": amount,
                "from": base,
                "to": target,
                "rate": rate,
                "converted": converted,
                "date": date,
                "provider": selected_provider,
            },
        )

    return currency_convert
