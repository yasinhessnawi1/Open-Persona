"""Unit tests for the sandbox-error → HTTP handlers (spec 12 T09c).

Verifies the two D-12-17 disambiguation paths:

- :class:`SandboxQuotaExceededError` → 429 with ``Retry-After: 60`` and the
  structured ``context`` body. Distinct from generic
  :class:`RateLimitExceededError` (per-endpoint rate limit): sandbox quota is
  a per-tenant policy enforcement (SCP-12-1 multi-tenant attack surface).
- :class:`SandboxUnavailableError` → 503 with ``Retry-After: 30``. Substrate
  outage; per D-12-5 there is no degraded fallback — the client retries later.

The tests construct a minimal FastAPI app, register the handlers, and assert
the response shape via Starlette's TestClient. No DB, no Spec-08 auth — the
boundary verification is purely on the handler mapping.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from persona.sandbox.errors import (
    SandboxQuotaExceededError,
    SandboxUnavailableError,
)
from persona_api.errors import register_exception_handlers


@pytest.fixture
def client() -> TestClient:
    """A bare app with the sandbox handlers wired and two test routes."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/_test/quota")
    async def _raise_quota() -> None:
        raise SandboxQuotaExceededError(
            "user 'alice' already holds 2 sandbox(es); cap is 2",
            context={"user_id": "alice", "current_count": "2", "cap": "2"},
        )

    @app.get("/_test/unavailable")
    async def _raise_unavailable() -> None:
        raise SandboxUnavailableError(
            "E2B sandbox creation failed",
            context={"reason": "e2b_create_failed"},
        )

    return TestClient(app)


# ----------------------------------------------------------- SandboxQuotaExceededError → 429


def test_quota_exceeded_maps_to_429(client: TestClient) -> None:
    response = client.get("/_test/quota")
    assert response.status_code == 429


def test_quota_exceeded_carries_retry_after(client: TestClient) -> None:
    response = client.get("/_test/quota")
    # Matches the reaper cadence (D-12-17 60s default) so the next reap-window
    # is the worst-case wait for an idle-timeout-freed slot.
    assert response.headers["Retry-After"] == "60"


def test_quota_exceeded_body_carries_structured_context(client: TestClient) -> None:
    body = client.get("/_test/quota").json()
    assert body["error"] == "sandbox_quota_exceeded"
    assert body["context"] == {"user_id": "alice", "current_count": "2", "cap": "2"}
    # Detail is the human-readable message; not asserted byte-for-byte
    # but must be present.
    assert "detail" in body
    assert body["detail"]


def test_quota_exceeded_distinct_from_rate_limit(client: TestClient) -> None:
    """The error code is `sandbox_quota_exceeded`, NOT `rate_limit_exceeded`.

    Different error → different message → different user action (D-12-17:
    sandbox quota is per-tenant policy, not per-endpoint rate limit).
    """
    body = client.get("/_test/quota").json()
    assert body["error"] != "rate_limit_exceeded"
    assert body["error"] == "sandbox_quota_exceeded"


# ----------------------------------------------------------- SandboxUnavailableError → 503


def test_unavailable_maps_to_503(client: TestClient) -> None:
    response = client.get("/_test/unavailable")
    assert response.status_code == 503


def test_unavailable_carries_retry_after(client: TestClient) -> None:
    response = client.get("/_test/unavailable")
    assert response.headers["Retry-After"] == "30"


def test_unavailable_body_carries_structured_context(client: TestClient) -> None:
    body = client.get("/_test/unavailable").json()
    assert body["error"] == "sandbox_unavailable"
    assert body["context"] == {"reason": "e2b_create_failed"}
    assert "detail" in body
    assert body["detail"]


def test_unavailable_distinct_from_quota_exceeded(client: TestClient) -> None:
    """503 (infra outage) is distinct from 429 (per-tenant policy).

    Different recovery paths: 503 retries against substrate health; 429 retries
    after one of the user's own sessions frees a slot. The error codes must
    be distinguishable client-side without inspecting the message body.
    """
    body = client.get("/_test/unavailable").json()
    assert body["error"] != "sandbox_quota_exceeded"
    assert body["error"] == "sandbox_unavailable"
