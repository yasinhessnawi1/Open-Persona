"""Conversation lifecycle + SSE chat routes (spec 08, T08, §5.2, KEYSTONE 1).

The message endpoint streams ``ConversationLoop.turn`` over SSE. Every route is
RLS-scoped via ``get_current_user``. The per-request loop builder comes from
``app.state.build_conversation_loop`` (the runtime factory, T10).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — used in cast() at runtime
from typing import Any, cast

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse

from persona_api.auth import AuthenticatedUser, get_current_user
from persona_api.middleware.rate_limit import rate_limit
from persona_api.schemas import (
    ConversationDetail,
    ConversationSummary,
    CreateConversationRequest,
    MessageView,
    PostMessageRequest,
)
from persona_api.services import audit_service, chat_service, credits_service

router = APIRouter(prefix="/v1", tags=["conversations"])


@router.post(
    "/personas/{persona_id}/conversations",
    status_code=status.HTTP_201_CREATED,
    response_model=ConversationSummary,
)
async def create_conversation(
    persona_id: str,
    body: CreateConversationRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> ConversationSummary:
    """Start a new conversation against a persona."""
    conv_id = chat_service.create_conversation(
        rls_engine=request.app.state.rls_engine,
        owner_id=user.id,
        persona_id=persona_id,
        title=body.title,
    )
    audit_service.record(
        engine=request.app.state.rls_engine,
        user_id=user.id,
        action="conversation.create",
        target=conv_id,
    )
    row = chat_service.get_conversation(
        rls_engine=request.app.state.rls_engine, conversation_id=conv_id
    )
    return _summary(row)


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: ARG001 — RLS via contextvar
    limit: int = 50,
    offset: int = 0,
) -> list[ConversationSummary]:
    """List the caller's conversations (paginated; RLS-scoped)."""
    rows = chat_service.list_conversations(
        rls_engine=request.app.state.rls_engine, limit=min(limit, 200), offset=offset
    )
    return [_summary(r) for r in rows]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: ARG001 — RLS via contextvar
) -> ConversationDetail:
    """Get a conversation's full message history (404 if not the caller's)."""
    row = chat_service.get_conversation(
        rls_engine=request.app.state.rls_engine, conversation_id=conversation_id
    )
    messages = cast("list[dict[str, Any]]", row["messages"])
    return ConversationDetail(
        id=str(row["id"]),
        persona_id=str(row["persona_id"]),
        title=str(row["title"]),
        messages=[
            MessageView(
                id=str(m["id"]),
                role=str(m["role"]),
                content=str(m["content"]),
                created_at=m["created_at"],
                channel=m.get("channel"),
            )
            for m in messages
        ],
        created_at=cast("datetime", row["created_at"]),
        updated_at=cast("datetime", row["updated_at"]),
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> None:
    """Delete a conversation + all its messages (cascade; 404 if not the caller's)."""
    chat_service.delete_conversation(
        rls_engine=request.app.state.rls_engine, conversation_id=conversation_id
    )
    audit_service.record(
        engine=request.app.state.rls_engine,
        user_id=user.id,
        action="conversation.delete",
        target=conversation_id,
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    dependencies=[Depends(rate_limit("messages"))],
)
async def post_message(
    conversation_id: str,
    body: PostMessageRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> StreamingResponse:
    """Send a message; stream the response as SSE (§5.2, KEYSTONE 1)."""
    # Pre-flight the conversation existence/ownership BEFORE streaming begins, so
    # a missing/cross-tenant conversation returns a clean 404 (RLS-scoped) rather
    # than raising mid-stream after SSE headers are sent ("response already
    # started"). This is the per-endpoint RLS guard for the chat path (#4).
    chat_service.get_conversation(
        rls_engine=request.app.state.rls_engine, conversation_id=conversation_id
    )
    # Pre-flight credit guard: 402 BEFORE streaming starts (D-11-12 / spec 11 §5).
    credits_service.require_credits(rls_engine=request.app.state.rls_engine, user_id=user.id)
    generator = chat_service.stream_chat(
        rls_engine=request.app.state.rls_engine,
        loop_builder=request.app.state.build_conversation_loop,
        owner_id=user.id,
        conversation_id=conversation_id,
        user_message=body.content,
        channel=body.channel,
        credits_per_turn=request.app.state.config.credits_per_turn,
        title_builder=getattr(request.app.state, "title_builder", None),
    )
    # The rate-limit dependency's headers don't auto-merge into a route-built
    # StreamingResponse (FastAPI limitation) — copy them from the stashed
    # decision so X-RateLimit-* appears on the SSE response too.
    headers: dict[str, str] = {}
    decision = getattr(request.state, "rate_limit_decision", None)
    if decision is not None:
        headers = decision.headers()
    return StreamingResponse(generator, media_type="text/event-stream", headers=headers)


def _summary(row: dict[str, object]) -> ConversationSummary:
    return ConversationSummary(
        id=str(row["id"]),
        persona_id=str(row["persona_id"]),
        title=str(row["title"]),
        created_at=row["created_at"],  # type: ignore[arg-type]
        updated_at=row["updated_at"],  # type: ignore[arg-type]
    )
