"""Route-local runtime guards: a keyless/unconfigured model backend → 503 (R1-D-2).

These are applied ONLY at the model-required write paths (authoring / chat /
run). They are deliberately NOT a global exception handler: a cloud bad-key must
still surface through its normal provider path (401/mid-stream), so the global
``AuthenticationError`` mapping stays untouched (R1-D-5 is a tracked follow-up).

Two keyless shapes are collapsed to one ``ModelBackendUnavailableError`` (R-R1-9):
  * the runtime was never wired (no model configured at all) — ``tier_registry``
    / the loop builder is absent on ``app.state``;
  * a tier is configured but cannot construct a backend (no API key) — the
    backend constructor raises ``AuthenticationError`` at ``get()`` time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.backends.errors import AuthenticationError as _BackendAuthenticationError
from persona_runtime.errors import TierNotConfiguredError

from persona_api.errors import ModelBackendUnavailableError

if TYPE_CHECKING:
    from fastapi import Request
    from persona.backends import ChatBackend
    from persona_runtime.tier import TierRegistry

_NOT_CONFIGURED = "model backend is not configured"


def require_model_backend(request: Request, tier: str) -> ChatBackend:
    """Return the backend for ``tier`` or raise ``ModelBackendUnavailableError``.

    Used by the authoring routes (which need a live model synchronously). Raises
    the 503 error when the registry is absent (runtime unwired) or the tier
    cannot construct a backend keyless — never lets either leak as a 500.
    """
    registry: TierRegistry | None = getattr(request.app.state, "tier_registry", None)
    if registry is None:
        raise ModelBackendUnavailableError(_NOT_CONFIGURED, context={"reason": "no_model_backend"})
    try:
        return registry.get(tier)
    except (_BackendAuthenticationError, TierNotConfiguredError) as exc:
        raise ModelBackendUnavailableError(
            _NOT_CONFIGURED, context={"reason": "no_model_key"}
        ) from exc


def require_runtime_wired(request: Request, attr: str) -> None:
    """Pre-stream presence check for chat/run loop builders (R1-D-2).

    Raises ``ModelBackendUnavailableError`` (503) before streaming begins when
    the runtime was never wired (``app.state.<attr>`` absent) — the common
    keyless "no model configured" boot — instead of an ``AttributeError`` 500.
    The built-but-keyless mid-stream failure stays the tracked follow-up
    (R1-D-5); this guard only covers the unwired shape.
    """
    if getattr(request.app.state, attr, None) is None:
        raise ModelBackendUnavailableError(_NOT_CONFIGURED, context={"reason": "no_runtime"})
