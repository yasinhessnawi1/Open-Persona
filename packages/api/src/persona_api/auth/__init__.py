"""Authentication: the injectable verify_token seam + the RLS current-user dep.

``AuthenticatedUser`` + ``JwtVerifierConfig`` + ``make_jwt_verifier`` are
re-exported from persona-core (D-V1-X-jwt-verifier-extraction). The FastAPI
dependencies (``get_current_user``, ``get_verify_token``) stay in persona-api.
"""

from __future__ import annotations

from persona_api.auth.deps import (
    AuthenticatedUser,
    JwtVerifierConfig,
    get_current_user,
    get_verify_token,
    make_jwt_verifier,
)

__all__ = [
    "AuthenticatedUser",
    "JwtVerifierConfig",
    "get_current_user",
    "get_verify_token",
    "make_jwt_verifier",
]
