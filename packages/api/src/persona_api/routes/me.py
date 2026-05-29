"""Credits + usage routes (spec 08, T12, §5.5)."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — used in cast() at runtime
from typing import cast

from fastapi import APIRouter, Depends, Request

from persona_api.auth import AuthenticatedUser, get_current_user
from persona_api.schemas import CreditsResponse, UsageEntry
from persona_api.services import credits_service

router = APIRouter(prefix="/v1/me", tags=["me"])


@router.get("/credits", response_model=CreditsResponse)
async def get_credits(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> CreditsResponse:
    """The caller's current credit balance (stub counter; §5.5).

    ``low_balance`` is surfaced inline so the web app shows the under-limit
    warning without a second round-trip (D-11-12).
    """
    balance = credits_service.get_balance(rls_engine=request.app.state.rls_engine, user_id=user.id)
    low = balance < credits_service.LOW_BALANCE_THRESHOLD
    return CreditsResponse(balance=balance, low_balance=low)


@router.get("/usage", response_model=list[UsageEntry])
async def get_usage(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: ARG001 — RLS via contextvar
    limit: int = 50,
    offset: int = 0,
) -> list[UsageEntry]:
    """The caller's per-turn token usage (§5.5; turn_logs, RLS-scoped)."""
    rows = credits_service.list_turn_usage(
        rls_engine=request.app.state.rls_engine,
        limit=min(limit, 200),
        offset=offset,
    )
    return [
        UsageEntry(
            persona_id=cast("str | None", r.get("persona_id")),
            tier_used=str(r["tier_used"]),
            model_name=str(r["model_name"]),
            prompt_tokens=int(cast("int", r["prompt_tokens"])),
            completion_tokens=int(cast("int", r["completion_tokens"])),
            cost_cents=float(cast("float", r["cost_cents"])),
            created_at=cast("datetime", r["created_at"]),
        )
        for r in rows
    ]


__all__ = ["router"]
