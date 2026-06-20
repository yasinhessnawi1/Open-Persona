"""Health endpoints (spec 08, T12, §8.3).

``GET /healthz`` → 200 ``{"status":"ok","db":"connected"}`` when Postgres is
reachable, 503 when it isn't. No auth (uptime monitors + load balancers hit it).

``GET /livez`` → always 200 ``{"status":"ok"}``. Liveness only — no dependency
checks. Fly.io's machine health check points here so a DB blip can't kill the
process; the deep ``/healthz`` readiness check stays for monitoring.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(tags=["health"])

__all__ = ["router"]


@router.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    """Liveness + DB connectivity check."""
    engine = getattr(request.app.state, "rls_engine", None)
    if engine is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "db": "not_configured"},
        )
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — any connectivity failure → 503
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable", "db": "disconnected"},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "ok", "db": "connected"},
    )


@router.get("/livez")
async def livez() -> JSONResponse:
    """Liveness probe — process is up; no dependency checks."""
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ok"})
