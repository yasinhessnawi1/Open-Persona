"""API-domain exceptions and FastAPI exception handlers (spec 08, T02).

Every API-domain failure is a subclass of ``persona.errors.PersonaError`` so it
carries the project's structured ``context: dict[str, str]`` (D-01-12) and the
existing log idioms apply. The handlers translate each exception — ours, the
re-used core/runtime ones, and Pydantic ``ValidationError`` — into the right HTTP
status with a structured JSON body. Stack traces and internal messages are never
leaked to the client (security-first, ENGINEERING_STANDARDS §1.1); a generic 500
hides unexpected errors while the real detail goes to the logs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from persona.errors import (
    PersonaError,
    PersonaNotFoundError,
    RuntimeWriteForbiddenError,
    SchemaVersionMismatchError,
    ToolNotAllowedError,
)
from persona.logging import get_logger
from persona.sandbox.errors import (
    SandboxQuotaExceededError,
    SandboxUnavailableError,
)
from pydantic import ValidationError

if TYPE_CHECKING:
    from fastapi import FastAPI

_log = get_logger("api.errors")

__all__ = [
    "AuthenticationError",
    "ConversationNotFoundError",
    "CreditsExhaustedError",
    "RateLimitExceededError",
    "RefinementLimitError",
    "RunNotFoundError",
    "register_exception_handlers",
]


class AuthenticationError(PersonaError):
    """Raised when a request has no valid bearer token (→ 401)."""


class CreditsExhaustedError(PersonaError):
    """Raised when a user's credit balance cannot cover an operation (→ 402)."""


class ConversationNotFoundError(PersonaError):
    """Raised when a conversation is not visible to the current user (→ 404)."""


class RunNotFoundError(PersonaError):
    """Raised when a run is not visible to the current user (→ 404)."""


class RateLimitExceededError(PersonaError):
    """Raised when a user exceeds an endpoint's per-minute limit (→ 429).

    ``context`` carries the rate-limit metadata the handler turns into
    ``X-RateLimit-*`` / ``Retry-After`` headers: ``limit``, ``remaining`` (0),
    ``reset`` (epoch seconds when the window resets).
    """


class RefinementLimitError(PersonaError):
    """Raised when an authoring-refinement request exceeds the 3-round cap (→ 422; D-10-5)."""


def _body(error: str, detail: str, context: dict[str, str]) -> dict[str, Any]:
    """The structured error body shape every handler returns."""
    payload: dict[str, Any] = {"error": error, "detail": detail}
    if context:
        payload["context"] = context
    return payload


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the exception → HTTP-response handlers to ``app``."""

    @app.exception_handler(AuthenticationError)
    async def _auth(_: Request, exc: AuthenticationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=_body(
                "authentication_error", exc.message or "authentication required", exc.context
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(CreditsExhaustedError)
    async def _credits(_: Request, exc: CreditsExhaustedError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            content=_body("credits_exhausted", exc.message or "insufficient credits", exc.context),
        )

    @app.exception_handler(ToolNotAllowedError)
    async def _forbidden_tool(_: Request, exc: ToolNotAllowedError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=_body("tool_not_allowed", exc.message or "tool not permitted", exc.context),
        )

    @app.exception_handler(PersonaNotFoundError)
    async def _persona_404(_: Request, exc: PersonaNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_body("persona_not_found", exc.message or "persona not found", exc.context),
        )

    @app.exception_handler(ConversationNotFoundError)
    async def _conv_404(_: Request, exc: ConversationNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_body(
                "conversation_not_found", exc.message or "conversation not found", exc.context
            ),
        )

    @app.exception_handler(RunNotFoundError)
    async def _run_404(_: Request, exc: RunNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_body("run_not_found", exc.message or "run not found", exc.context),
        )

    @app.exception_handler(RateLimitExceededError)
    async def _rate(_: Request, exc: RateLimitExceededError) -> JSONResponse:
        headers = {}
        if "limit" in exc.context:
            headers["X-RateLimit-Limit"] = exc.context["limit"]
            headers["X-RateLimit-Remaining"] = exc.context.get("remaining", "0")
        if "reset" in exc.context:
            headers["X-RateLimit-Reset"] = exc.context["reset"]
            headers["Retry-After"] = exc.context.get("retry_after", exc.context["reset"])
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=_body("rate_limit_exceeded", exc.message or "rate limit exceeded", exc.context),
            headers=headers,
        )

    @app.exception_handler(SandboxQuotaExceededError)
    async def _sandbox_quota_429(_: Request, exc: SandboxQuotaExceededError) -> JSONResponse:
        """Per-user sandbox cap exhausted (spec 12 T09c, D-12-17 quota path).

        Distinct from :class:`RateLimitExceededError` (general per-endpoint
        rate limit): the sandbox cap is a per-tenant concurrent-session policy
        (D-12-17; bounds SCP-12-1 multi-tenant attack surface). A 429 with
        ``Retry-After`` lets the client back off; the structured ``context``
        body (``user_id``, ``current_count``, ``cap``) surfaces the cap state
        for client-side messaging.

        ``Retry-After: 60`` is a coarse hint — quota frees whenever one of the
        user's existing sessions is released or reaped (D-12-17 idle_timeout
        default 300s; reap cadence 60s). The 60s value matches the reaper
        cadence so the next reap-window is the worst-case wait for a slot
        freed by idle reap; sessions released sooner via ``pool.release()``
        free the slot immediately.
        """
        _log.info(
            "sandbox quota rejection user={user} count={count} cap={cap}",
            user=exc.context.get("user_id", "?"),
            count=exc.context.get("current_count", "?"),
            cap=exc.context.get("cap", "?"),
        )
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=_body(
                "sandbox_quota_exceeded",
                exc.message or "sandbox quota exceeded for this user",
                exc.context,
            ),
            headers={"Retry-After": "60"},
        )

    @app.exception_handler(SandboxUnavailableError)
    async def _sandbox_unavailable_503(_: Request, exc: SandboxUnavailableError) -> JSONResponse:
        """Substrate unreachable (spec 12 T09c, D-12-5 no-degraded-fallback path).

        Distinct from quota-exceeded (above): substrate-unavailability is an
        infrastructure outage (E2B API down; pool closed; SDK missing), not
        a per-tenant policy decision. Per D-12-5 there is no degraded fallback
        — the client retries later. ``Retry-After: 30`` is a brief back-off
        hint; the substrate-status dashboard is the authoritative recovery
        signal.
        """
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_body(
                "sandbox_unavailable",
                exc.message or "sandbox substrate is unavailable",
                exc.context,
            ),
            headers={"Retry-After": "30"},
        )

    @app.exception_handler(RefinementLimitError)
    async def _refine_limit_422(_: Request, exc: RefinementLimitError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_body(
                "refinement_limit_exceeded", exc.message or "refinement limit reached", exc.context
            ),
        )

    @app.exception_handler(SchemaVersionMismatchError)
    async def _schema_422(_: Request, exc: SchemaVersionMismatchError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_body(
                "schema_version_mismatch", exc.message or "unsupported schema_version", exc.context
            ),
        )

    @app.exception_handler(RuntimeWriteForbiddenError)
    async def _write_forbidden(_: Request, exc: RuntimeWriteForbiddenError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=_body("write_forbidden", exc.message or "write not permitted", exc.context),
        )

    @app.exception_handler(ValidationError)
    async def _pydantic_422(_: Request, exc: ValidationError) -> JSONResponse:
        # Invalid persona YAML / request body → structured 422 (acceptance #2).
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"error": "validation_error", "detail": exc.errors(include_url=False)},
        )

    @app.exception_handler(RequestValidationError)
    async def _request_422(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"error": "validation_error", "detail": exc.errors()},
        )

    @app.exception_handler(PersonaError)
    async def _domain_500(_: Request, exc: PersonaError) -> JSONResponse:
        # Any other domain error we didn't specifically map: 500, logged, body
        # generic (don't leak internal detail to the client).
        _log.error("unhandled domain error: {err}", err=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error", "detail": "an internal error occurred"},
        )
