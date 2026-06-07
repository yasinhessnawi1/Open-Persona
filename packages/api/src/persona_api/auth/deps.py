"""FastAPI auth glue: the injectable verify_token seam + the RLS current-user dep.

The provider-agnostic JWT verification surface (``AuthenticatedUser``,
``JwtVerifierConfig``, ``make_jwt_verifier``) was relocated to persona-core at
spec V1 T03 (D-V1-X-jwt-verifier-extraction) so persona-voice can consume it
without taking a persona-api dependency. This module re-exports those names
for back-compat with the existing persona-api callers and keeps the FastAPI-
specific glue (``_bearer_token`` header parsing, ``get_verify_token``
dependency, ``get_current_user`` request-scoped contextvar binding) here —
those depend on FastAPI's ``Request`` / ``Depends`` and the RLS
:data:`persona_api.middleware.rls_context.current_user_id` contextvar, both of
which are framework concerns that don't belong in persona-core.

The verifier is an **injectable seam**: the default uses ``python-jose`` (HS256
for the test fake-JWT path, RS256 for the real JWKS path; fails closed on
expired/tampered/wrong-audience — verified research §4). Tests override the
``verify_token`` dependency with a fake verifier so no provider is called
(acceptance #14). Deferring the provider (S08-1) keeps the seam
provider-agnostic — both Clerk and Supabase issue JWTs.
"""

from __future__ import annotations

# NOTE: these collections.abc names are imported at RUNTIME (not under
# TYPE_CHECKING) because FastAPI resolves dependency signatures via
# get_type_hints at startup — with `from __future__ import annotations` every
# annotation is a string, so every name in a dependency's signature must be
# importable at runtime or FastAPI mis-reads the params as query params.
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import Depends, Request

# The provider-agnostic verification surface lives in persona-core
# (D-V1-X-jwt-verifier-extraction). Re-exported here for back-compat with the
# existing persona-api import sites (`from persona_api.auth import ...`).
from persona.auth.jwt_verifier import (
    AuthenticatedUser,
    JwtVerifierConfig,
    make_jwt_verifier,
)

from persona_api.errors import AuthenticationError
from persona_api.middleware.rls_context import current_user_id

__all__ = [
    "AuthenticatedUser",
    "JwtVerifierConfig",
    "get_current_user",
    "get_verify_token",
    "make_jwt_verifier",
]


def _bearer_token(request: Request) -> str:
    """Extract the bearer token from the Authorization header, or 401."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise AuthenticationError("missing or malformed Authorization header")
    token = header.removeprefix("Bearer ").strip()
    if not token:
        raise AuthenticationError("empty bearer token")
    return token


def get_verify_token(request: Request) -> Callable[[str], Awaitable[AuthenticatedUser]]:
    """Provide the active token verifier.

    The default is built from the app's :class:`APIConfig` (stored on
    ``app.state.config`` by the factory). Tests override THIS dependency to
    inject a fake-JWT verifier (no provider call). Kept as a dependency (not a
    bare import) precisely so it is overridable.
    """
    verifier = getattr(request.app.state, "verify_token", None)
    if verifier is not None:
        return verifier  # type: ignore[no-any-return]
    return make_jwt_verifier(request.app.state.config)


async def get_current_user(
    request: Request,
    verify: Callable[[str], Awaitable[AuthenticatedUser]] = Depends(get_verify_token),
) -> AsyncIterator[AuthenticatedUser]:
    """Authenticate the request and bind the RLS user-id for its duration (D-08-1).

    A ``yield`` dependency so the :data:`current_user_id` ``ContextVar`` lifetime
    is exactly the request scope: extract + verify the bearer token, **set** the
    contextvar (so the RLS pool listener scopes every connection this request
    touches — route queries AND the runtime store's), yield the user, then
    **reset** the contextvar on teardown so nothing leaks to a later request on
    the same worker. On auth failure the contextvar is never set (fail-closed —
    an unscoped connection sees zero rows).
    """
    token = _bearer_token(request)
    user = await verify(token)
    # JIT-provision the users row: the provider (Clerk) issues JWTs but the API
    # has no signup, and persona/conversation/run/credits all FK users.id
    # (webhook mirroring deferred in spec 08). A system action on the superuser
    # engine; idempotent. None in unit tests without a superuser DSN.
    admin_engine = getattr(request.app.state, "admin_engine", None)
    if admin_engine is not None:
        from persona_api.services.user_service import ensure_user

        ensure_user(admin_engine, user_id=user.id, email=user.email)
    reset_token = current_user_id.set(user.id)
    try:
        yield user
    finally:
        current_user_id.reset(reset_token)
