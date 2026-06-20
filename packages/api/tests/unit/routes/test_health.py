"""Unit tests for the health routes (spec 08, T12, §8.3).

Pure route tests: mount only :data:`persona_api.routes.health.router` on a bare
FastAPI app with no ``rls_engine`` on ``app.state`` — no database, no runtime,
no app factory. This isolates the liveness/readiness contract:

- ``GET /livez`` is liveness only — it must ALWAYS return 200 and never touch
  the DB, so Fly.io's machine health check can't be killed by a DB blip.
- ``GET /healthz`` is the deep readiness check — with no engine configured it
  reports 503 (degraded / not_configured), unchanged by the /livez addition.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from persona_api.routes.health import router


@pytest.fixture
def client() -> TestClient:
    """A client over a bare app with the health router and NO rls_engine."""
    app = FastAPI()
    app.include_router(router)
    # Deliberately leave app.state.rls_engine unset (getattr(..., None) → None).
    return TestClient(app)


def test_livez_is_ok_without_db_engine(client: TestClient) -> None:
    # Liveness must succeed independent of any DB engine on app.state.
    resp = client.get("/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_livez_does_not_read_rls_engine(client: TestClient) -> None:
    # Even if a sentinel engine were present, /livez must not consult it; the
    # absence of any AttributeError/connection here is the proof it's DB-free.
    client.app.state.rls_engine = object()  # never connected by livez
    resp = client.get("/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_healthz_degraded_without_db_engine(client: TestClient) -> None:
    # Deep readiness check: no engine configured → 503 (unchanged contract).
    resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded", "db": "not_configured"}
