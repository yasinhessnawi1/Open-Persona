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
from persona_api.services import audit_service, chat_service, credits_service, document_service

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
    """Delete a conversation + all its messages + workspace artefacts.

    Cascade reach (T19 + Spec 13 T12 co-landing per D-14-X-cascade-coordination):

    1. DB rows (existing): the ``conversations`` row + its ``messages`` /
       ``turn_logs`` via FK cascade.
    2. **Document workspace + DocumentStore chunks** (T19): every doc
       attached to the conversation, via
       :func:`document_service.remove_all_for_conversation` — the
       cascade-helper T13 introduced specifically for this reuse.
    3. **Image workspace files** (Spec 13 T12): each image referenced by
       the conversation's messages. Spec 13 owns this branch; the two
       cascade extensions coexist additively in this same handler per
       the D-14-X-cascade-coordination locking decision.

    404 if not the caller's conversation (RLS-scoped).
    """
    # Pre-fetch persona_id BEFORE the DB delete — chat_service.delete_conversation
    # tears down the row, so we'd lose this lookup if we deferred it.
    # get_conversation also does the RLS ownership check (404 if cross-tenant).
    conv = chat_service.get_conversation(
        rls_engine=request.app.state.rls_engine, conversation_id=conversation_id
    )
    persona_id = str(conv["persona_id"])

    # DB delete (existing cascade to messages + turn_logs).
    chat_service.delete_conversation(
        rls_engine=request.app.state.rls_engine, conversation_id=conversation_id
    )

    # T19 — document workspace + DocumentStore chunks cascade.
    workspace_root = getattr(request.app.state, "workspace_root", None)
    build_document_store = getattr(request.app.state, "build_document_store", None)
    if workspace_root is not None and build_document_store is not None:
        from pathlib import Path  # noqa: PLC0415 — deliberate local import

        document_service.remove_all_for_conversation(
            sandbox_root=Path(workspace_root),
            persona_id=persona_id,
            conversation_id=conversation_id,
            document_store=build_document_store(),
        )

    # (Spec 13 T12 inserts its image-workspace-cascade here when it lands.)

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
    """Send a message; stream the response as SSE (§5.2, KEYSTONE 1).

    Multimodal (spec 13 T20): when ``body.images`` is non-empty the route
    constructs a multimodal :class:`ConversationMessage` content list
    (``[TextContent, ImageContent, ...]``) and passes ``turn_has_image=True``
    through to the runtime so :meth:`Router.choose` restricts to vision-capable
    tiers. The text-only path (``body.images is None``) is unchanged
    byte-for-byte — T03/T13 regression invariants hold.
    """
    # Pre-flight the conversation existence/ownership BEFORE streaming begins, so
    # a missing/cross-tenant conversation returns a clean 404 (RLS-scoped) rather
    # than raising mid-stream after SSE headers are sent ("response already
    # started"). This is the per-endpoint RLS guard for the chat path (#4).
    chat_service.get_conversation(
        rls_engine=request.app.state.rls_engine, conversation_id=conversation_id
    )
    # Pre-flight credit guard: 402 BEFORE streaming starts (D-11-12 / spec 11 §5).
    credits_service.require_credits(rls_engine=request.app.state.rls_engine, user_id=user.id)

    # The text-only path's ``user_message`` is BYTE-FOR-BYTE unchanged (T03/T13
    # regression invariant); image-bearing turns thread ``images`` + the
    # ``turn_has_image`` flag separately so the runtime loop's signature stays
    # ``user_message: str`` (the loop's prompt + retrieval pipeline operate on
    # text; images travel as references on the side and are persisted on the
    # ``messages.images`` JSONB column per D-13-X-now option (c)).
    turn_has_image = bool(body.images)
    generator = chat_service.stream_chat(
        rls_engine=request.app.state.rls_engine,
        loop_builder=request.app.state.build_conversation_loop,
        owner_id=user.id,
        conversation_id=conversation_id,
        user_message=body.content,
        channel=body.channel,
        credits_per_turn=request.app.state.config.credits_per_turn,
        title_builder=getattr(request.app.state, "title_builder", None),
        images=list(body.images) if body.images else None,
        turn_has_image=turn_has_image,
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
