"""Unit tests for the Spec 22 T13 OpenRouter subscription resolver.

Covers the four-branch resolution order of
:func:`persona_runtime.openrouter_subscription.resolve_openrouter_subscription`
+ the D-22-3 fail-open / D-22-9 fail-loud policy. A fake catalog client is
injected via ``client_factory`` so no test hits the network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from persona.backends.errors import (
    AuthenticationError,
    OpenRouterBalanceProbeError,
)
from persona.backends.openrouter_catalog import (
    OpenRouterKeyInfo,
    OpenRouterSubscriptionState,
)
from persona_runtime.openrouter_subscription import resolve_openrouter_subscription

_NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
_API_KEY = "sk-or-v1-test"


class _FakeCatalogClient:
    """Stub catalog client: returns a stub key-info or raises a probe error.

    Records whether :meth:`close` ran so tests can assert the resolver always
    closes the client (even on the fail-loud auth path).
    """

    def __init__(
        self,
        *,
        key_info: OpenRouterKeyInfo | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._key_info = key_info
        self._raises = raises
        self.closed = False

    def get_key_info(self) -> OpenRouterKeyInfo:
        if self._raises is not None:
            raise self._raises
        assert self._key_info is not None
        return self._key_info

    def close(self) -> None:
        self.closed = True


def _factory_for(client: _FakeCatalogClient):  # noqa: ANN202 — local test helper
    """Build a ``client_factory`` that yields ``client`` regardless of key."""

    def _factory(_api_key: str) -> _FakeCatalogClient:
        return client

    return _factory


def test_no_api_key_returns_none() -> None:
    """No PERSONA_OPENROUTER_API_KEY → None (opt-in; backward compat)."""
    assert resolve_openrouter_subscription({}, now=_NOW) is None


def test_blank_api_key_returns_none() -> None:
    """A whitespace-only key is treated as absent."""
    env = {"PERSONA_OPENROUTER_API_KEY": "   "}
    assert resolve_openrouter_subscription(env, now=_NOW) is None


def test_forced_free_skips_probe() -> None:
    """SUBSCRIPTION_MODE=free → forced free state, no probe (D-22-7)."""
    env = {
        "PERSONA_OPENROUTER_API_KEY": _API_KEY,
        "PERSONA_OPENROUTER_SUBSCRIPTION_MODE": "Free",
    }
    client = _FakeCatalogClient(raises=AssertionError("probe must not run"))
    state = resolve_openrouter_subscription(env, now=_NOW, client_factory=_factory_for(client))
    assert state == OpenRouterSubscriptionState(
        mode="free",
        is_free_tier=True,
        limit_remaining=None,
        last_checked_at=_NOW,
        probe_failed=False,
    )
    assert client.closed is False


def test_forced_paid_skips_probe() -> None:
    """SUBSCRIPTION_MODE=paid → forced paid state, no probe (D-22-7)."""
    env = {
        "PERSONA_OPENROUTER_API_KEY": _API_KEY,
        "PERSONA_OPENROUTER_SUBSCRIPTION_MODE": "  PAID  ",
    }
    client = _FakeCatalogClient(raises=AssertionError("probe must not run"))
    state = resolve_openrouter_subscription(env, now=_NOW, client_factory=_factory_for(client))
    assert state is not None
    assert state.mode == "paid"
    assert state.is_free_tier is False
    assert state.limit_remaining is None
    assert state.last_checked_at == _NOW
    assert state.probe_failed is False


def test_forced_invalid_mode_raises() -> None:
    """An invalid SUBSCRIPTION_MODE value is an operator typo → ValueError."""
    env = {
        "PERSONA_OPENROUTER_API_KEY": _API_KEY,
        "PERSONA_OPENROUTER_SUBSCRIPTION_MODE": "premium",
    }
    with pytest.raises(ValueError, match="must be 'free' or 'paid'"):
        resolve_openrouter_subscription(env, now=_NOW)


def test_empty_forced_mode_falls_through_to_probe() -> None:
    """A blank SUBSCRIPTION_MODE is ignored; resolution probes instead."""
    env = {
        "PERSONA_OPENROUTER_API_KEY": _API_KEY,
        "PERSONA_OPENROUTER_SUBSCRIPTION_MODE": "   ",
    }
    client = _FakeCatalogClient(key_info=OpenRouterKeyInfo(is_free_tier=False))
    state = resolve_openrouter_subscription(env, now=_NOW, client_factory=_factory_for(client))
    assert state is not None
    assert state.mode == "paid"
    assert client.closed is True


def test_probe_paid_key_returns_paid_state() -> None:
    """Probe of a paid key (is_free_tier=False) → paid state with limit."""
    env = {"PERSONA_OPENROUTER_API_KEY": _API_KEY}
    client = _FakeCatalogClient(
        key_info=OpenRouterKeyInfo(is_free_tier=False, limit_remaining=Decimal("4.50"))
    )
    state = resolve_openrouter_subscription(env, now=_NOW, client_factory=_factory_for(client))
    assert state == OpenRouterSubscriptionState(
        mode="paid",
        is_free_tier=False,
        limit_remaining=Decimal("4.50"),
        last_checked_at=_NOW,
        probe_failed=False,
    )
    assert client.closed is True


def test_probe_free_key_returns_free_state() -> None:
    """Probe of a free key (is_free_tier=True) → free state, probe_failed=False."""
    env = {"PERSONA_OPENROUTER_API_KEY": _API_KEY}
    client = _FakeCatalogClient(key_info=OpenRouterKeyInfo(is_free_tier=True))
    state = resolve_openrouter_subscription(env, now=_NOW, client_factory=_factory_for(client))
    assert state is not None
    assert state.mode == "free"
    assert state.is_free_tier is True
    assert state.probe_failed is False
    assert state.last_checked_at == _NOW
    assert client.closed is True


def test_probe_balance_error_falls_back_to_free() -> None:
    """OpenRouterBalanceProbeError → conservative free fallback (D-22-3)."""
    env = {"PERSONA_OPENROUTER_API_KEY": _API_KEY}
    client = _FakeCatalogClient(
        raises=OpenRouterBalanceProbeError(
            "timed out",
            context={"provider": "openrouter", "reason": "timeout"},
        )
    )
    state = resolve_openrouter_subscription(env, now=_NOW, client_factory=_factory_for(client))
    assert state == OpenRouterSubscriptionState(
        mode="free",
        is_free_tier=True,
        limit_remaining=None,
        last_checked_at=_NOW,
        probe_failed=True,
    )
    assert client.closed is True


def test_probe_auth_error_propagates() -> None:
    """AuthenticationError (401) fails loud and propagates (D-22-9)."""
    env = {"PERSONA_OPENROUTER_API_KEY": _API_KEY}
    client = _FakeCatalogClient(
        raises=AuthenticationError(
            "rejected",
            context={"provider": "openrouter", "status_code": "401"},
        )
    )
    with pytest.raises(AuthenticationError):
        resolve_openrouter_subscription(env, now=_NOW, client_factory=_factory_for(client))
    # close() runs even on the fail-loud path (finally block).
    assert client.closed is True


def test_default_now_is_tz_aware_utc() -> None:
    """When ``now`` is omitted, last_checked_at is tz-aware UTC."""
    env = {"PERSONA_OPENROUTER_API_KEY": _API_KEY}
    client = _FakeCatalogClient(key_info=OpenRouterKeyInfo(is_free_tier=True))
    state = resolve_openrouter_subscription(env, client_factory=_factory_for(client))
    assert state is not None
    assert state.last_checked_at.tzinfo is UTC


def test_reads_os_environ_when_env_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default env source is os.environ (snapshot pattern); no key → None."""
    monkeypatch.delenv("PERSONA_OPENROUTER_API_KEY", raising=False)
    assert resolve_openrouter_subscription(now=_NOW) is None
