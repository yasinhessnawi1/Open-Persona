"""Agentic run routes (spec 08, T11, §5.3, KEYSTONE 2).

start / status / events (SSE) / respond / cancel. Every route is RLS-scoped via
``get_current_user``. The run executes as a background task (the ``RunRegistry``
on ``app.state``); ``/runs`` is rate-limited (5/min, §6).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse

from persona_api.auth import AuthenticatedUser, get_current_user
from persona_api.middleware.rate_limit import rate_limit
from persona_api.schemas import RespondToRunRequest, RunStatusResponse, StartRunRequest
from persona_api.services import audit_service, credits_service, run_service

router = APIRouter(prefix="/v1", tags=["runs"])


@router.post(
    "/personas/{persona_id}/runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunStatusResponse,
    dependencies=[Depends(rate_limit("runs"))],
)
async def start_run(
    persona_id: str,
    body: StartRunRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> RunStatusResponse:
    """Start an agentic run (returns the run_id immediately; runs in background)."""
    # Pre-flight credit guard (D-11-12 / spec 11 §5): 402 before the background task starts.
    credits_service.require_credits(rls_engine=request.app.state.rls_engine, user_id=user.id)
    run_id = await run_service.start_run(
        rls_engine=request.app.state.rls_engine,
        registry=request.app.state.run_registry,
        loop_builder=request.app.state.build_agentic_loop,
        owner_id=user.id,
        persona_id=persona_id,
        task=body.task,
    )
    audit_service.record(
        engine=request.app.state.rls_engine,
        user_id=user.id,
        action="run.start",
        target=run_id,
    )
    row = run_service.get_run(rls_engine=request.app.state.rls_engine, run_id=run_id)
    return _run_status(row)


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
async def get_run(
    run_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: ARG001 — RLS via contextvar
) -> RunStatusResponse:
    """Get a run's status + accumulated steps (RLS-scoped → 404)."""
    row = run_service.get_run(rls_engine=request.app.state.rls_engine, run_id=run_id)
    return _run_status(row)


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: ARG001 — RLS via contextvar
) -> StreamingResponse:
    """Stream the run's events as SSE (live, from the in-process event bus)."""
    # Ownership check: 404 if the run isn't the caller's.
    run_service.get_run(rls_engine=request.app.state.rls_engine, run_id=run_id)
    generator = run_service.stream_run_events(
        registry=request.app.state.run_registry, run_id=run_id
    )
    return StreamingResponse(generator, media_type="text/event-stream")


@router.post("/runs/{run_id}/respond", status_code=status.HTTP_204_NO_CONTENT)
async def respond(
    run_id: str,
    body: RespondToRunRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: ARG001 — RLS via contextvar
) -> None:
    """Deliver an answer to a run awaiting an ask-user question."""
    run_service.respond_to_run(
        rls_engine=request.app.state.rls_engine,
        registry=request.app.state.run_registry,
        run_id=run_id,
        answer=body.answer,
    )


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel(
    run_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, str]:
    """Cancel a running run (stops at the next step boundary → cancelled)."""
    run_service.cancel_run(
        rls_engine=request.app.state.rls_engine,
        registry=request.app.state.run_registry,
        run_id=run_id,
    )
    audit_service.record(
        engine=request.app.state.rls_engine,
        user_id=user.id,
        action="run.cancel",
        target=run_id,
    )
    return {"status": "cancelling"}


def _run_status(row: dict[str, object]) -> RunStatusResponse:
    return RunStatusResponse(
        id=str(row["id"]),
        persona_id=str(row["persona_id"]),
        task=str(row["task"]),
        status=str(row["status"]),
        steps=run_service.steps_json(row),
        output=row.get("output"),  # type: ignore[arg-type]
        error=row.get("error"),  # type: ignore[arg-type]
    )
