"""Conversation lifecycle + SSE chat (spec 08, T08, KEYSTONE 1).

The chat pathway: load the persona's conversation, drive
``ConversationLoop.turn`` (spec 05), stream its ``StreamChunk``s as SSE events,
and — only AFTER the final chunk — persist the new messages (incl. the
``channel`` passthrough, D-08-3), the compacted conversation state, the
``turn_log`` (T12), and the credits deduction (T12).

The persist-after-final discipline is load-bearing (research §3, D-05-12):
persistence runs in the normal flow after the generator's final yield, NEVER in
a ``finally`` — a client disconnect cancels the async generator mid-stream, and
a ``finally`` would persist a half-finished turn (state corruption). The async
generator simply suspends; persistence is skipped.

The ``ConversationLoop`` is built per-request by the runtime factory (T10),
which this service receives as an injected ``loop_builder`` so it stays testable
with a scripted backend.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

from persona.errors import PersonaNotFoundError
from persona.logging import get_logger
from persona.schema.conversation import Conversation, ConversationMessage
from sqlalchemy import delete, insert, select, update

from persona_api.db.models import conversations as conversations_t
from persona_api.db.models import messages as messages_t
from persona_api.db.models import personas as personas_t
from persona_api.errors import ConversationNotFoundError
from persona_api.services import credits_service  # sibling module (no back-import)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from persona.backends import StreamChunk
    from persona_runtime.agentic.events import RunEvent
    from persona_runtime.loop import ConversationLoop
    from sqlalchemy import Connection, Engine

    from persona_api.schemas import ChannelContext

    # The runtime factory (T10) builds a ConversationLoop for a persona under the
    # current request's RLS scope, given the persona_id.
    LoopBuilder = Callable[[str], Awaitable[ConversationLoop]]


__all__ = [
    "create_conversation",
    "delete_conversation",
    "get_conversation",
    "list_conversations",
    "set_title",
    "stream_chat",
]

_log = get_logger("api.chat")

# The message role, mirroring ConversationMessage.role + the messages_role_check
# DB CHECK constraint (the source of truth that makes the cast in _to_message
# sound).
Role = Literal["user", "assistant", "system", "tool"]

# A title builder turns the first user message into a short conversation title.
TitleBuilder = "Callable[[str], Awaitable[str]]"
_MAX_TITLE_LEN = 120


def create_conversation(*, rls_engine: Engine, owner_id: str, persona_id: str, title: str) -> str:
    """Create a conversation against a persona (RLS-scoped). Returns its id."""
    conv_id = f"conv_{uuid.uuid4().hex}"
    with rls_engine.begin() as conn:
        # Verify the persona is the caller's (RLS would hide it otherwise).
        exists = conn.execute(select(personas_t.c.id).where(personas_t.c.id == persona_id)).first()
        if exists is None:
            raise PersonaNotFoundError("persona not found", context={"id": persona_id})
        conn.execute(
            insert(conversations_t).values(
                id=conv_id, owner_id=owner_id, persona_id=persona_id, title=title
            )
        )
    return conv_id


def delete_conversation(*, rls_engine: Engine, conversation_id: str) -> None:
    """Delete a conversation (cascades to its messages + turn_logs via FK).

    RLS-scoped → a conversation that isn't the caller's is invisible and the
    delete matches no row → 404.
    """
    with rls_engine.begin() as conn:
        result = conn.execute(
            delete(conversations_t)
            .where(conversations_t.c.id == conversation_id)
            .returning(conversations_t.c.id)
        )
        if result.first() is None:
            raise ConversationNotFoundError(
                "conversation not found", context={"id": conversation_id}
            )


def set_title(*, rls_engine: Engine, conversation_id: str, title: str) -> None:
    """Set a conversation's title (RLS-scoped; used by the auto-title path)."""
    with rls_engine.begin() as conn:
        conn.execute(
            update(conversations_t)
            .where(conversations_t.c.id == conversation_id)
            .values(title=title)
        )


def list_conversations(*, rls_engine: Engine, limit: int, offset: int) -> list[dict[str, object]]:
    """List the caller's conversations (RLS-scoped), paginated."""
    with rls_engine.begin() as conn:
        rows = (
            conn.execute(
                select(conversations_t)
                .order_by(conversations_t.c.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def get_conversation(*, rls_engine: Engine, conversation_id: str) -> dict[str, object]:
    """Return a conversation + its full message history (RLS-scoped → 404)."""
    with rls_engine.begin() as conn:
        conv = (
            conn.execute(select(conversations_t).where(conversations_t.c.id == conversation_id))
            .mappings()
            .first()
        )
        if conv is None:
            raise ConversationNotFoundError(
                "conversation not found", context={"id": conversation_id}
            )
        msgs = (
            conn.execute(
                select(messages_t)
                .where(messages_t.c.conversation_id == conversation_id)
                .order_by(messages_t.c.created_at.asc())
            )
            .mappings()
            .all()
        )
    out = dict(conv)
    out["messages"] = [dict(m) for m in msgs]
    return out


def _load_conversation(conn: Connection, conversation_id: str) -> Conversation:
    """Materialise a runtime Conversation from the DB rows (RLS-scoped)."""
    conv = (
        conn.execute(select(conversations_t).where(conversations_t.c.id == conversation_id))
        .mappings()
        .first()
    )
    if conv is None:
        raise ConversationNotFoundError("conversation not found", context={"id": conversation_id})
    msgs = (
        conn.execute(
            select(messages_t)
            .where(messages_t.c.conversation_id == conversation_id)
            .order_by(messages_t.c.created_at.asc())
        )
        .mappings()
        .all()
    )
    return Conversation(
        conversation_id=str(conv["id"]),
        persona_id=str(conv["persona_id"]),
        messages=[_to_message(dict(m)) for m in msgs],
        compacted_summary=str(conv["compacted_summary"]),
        compacted_up_to=int(conv["compacted_up_to"]),
    )


def _to_message(row: dict[str, object]) -> ConversationMessage:
    """Build a ConversationMessage from a DB row. ``role`` is constrained to the
    valid set by the ``messages_role_check`` DB CHECK, so the cast is sound."""
    role = cast("Role", str(row["role"]))
    created_at = cast("datetime", row["created_at"])
    return ConversationMessage(role=role, content=str(row["content"]), created_at=created_at)


def _sse(event: str, data: dict[str, object]) -> bytes:
    """Format one SSE event frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def stream_chat(
    *,
    rls_engine: Engine,
    loop_builder: LoopBuilder,
    owner_id: str,
    conversation_id: str,
    user_message: str,
    channel: ChannelContext | None,
    credits_per_turn: int = 1,
    title_builder: Callable[[str], Awaitable[str]] | None = None,
) -> AsyncIterator[bytes]:
    """Drive ConversationLoop.turn and stream SSE; persist after the final yield.

    Yields SSE frames (``chunk`` / ``done``). After the loop's final chunk — and
    ONLY on clean completion (a client disconnect cancels this generator and
    skips persistence) — the new messages, compaction state, channel, turn_log
    (the loop wrote it), and the credits deduction are committed (D-08-6: a
    failed/cancelled turn deducts nothing).

    On the FIRST turn of a conversation (no prior messages), an optional
    ``title_builder`` (the small tier) generates a short title from the first
    user message — best-effort: a failure leaves the default title untouched.

    Granular turn events (``tool_calling`` / ``tool_result`` as tools dispatch,
    plus the router's ``tier``) are surfaced via the loop's ``on_event``
    callback and emitted as SSE frames IN ORDER, interleaved with the text
    chunks, before the terminal ``done`` event. These reuse the SAME
    :class:`RunEvent` shapes as the run-viewer stream — one event vocabulary
    covers both (closes the gap spec 09 found).
    """
    # Load the conversation under the RLS scope (its own short transaction).
    with rls_engine.begin() as conn:
        conversation = _load_conversation(conn, conversation_id)
        prior_msg_count = len(conversation.messages)
    persona_id = conversation.persona_id
    is_first_turn = prior_msg_count == 0

    loop = await loop_builder(persona_id)

    # The loop fires on_event synchronously between chunk yields; buffer the
    # events and flush them (in order) before the next chunk's frame, so the SSE
    # stream preserves the true interleaving. `tier` is captured for `done`.
    pending: list[RunEvent] = []
    tier = "frontier"  # fallback; replaced by the router's real choice via on_event

    async def _on_event(event: RunEvent) -> None:
        pending.append(event)

    def _drain() -> list[bytes]:
        nonlocal tier
        frames: list[bytes] = []
        for ev in pending:
            if ev.type == "tier":
                tier = str(ev.data.get("tier", tier))
                continue  # tier rides the `done` event, not its own SSE frame
            frames.append(_sse(ev.type, ev.data))
        pending.clear()
        return frames

    last_chunk: StreamChunk | None = None
    async for chunk in loop.turn(conversation, user_message, _on_event):
        last_chunk = chunk
        for frame in _drain():  # tool_calling / tool_result that fired before this chunk
            yield frame
        if chunk.delta:
            yield _sse("chunk", {"delta": chunk.delta, "is_final": chunk.is_final})
    for frame in _drain():  # any trailing events after the final chunk
        yield frame

    # ---- persist-after-final (only reached on clean completion) ----
    usage = last_chunk.usage if last_chunk is not None else None
    _persist_turn(
        rls_engine=rls_engine,
        conversation=conversation,
        prior_msg_count=prior_msg_count,
        channel=channel,
    )
    # Auto-title the conversation from its first user message (best-effort, small
    # tier). Failure leaves the default title — never breaks the turn.
    if is_first_turn and title_builder is not None:
        await _maybe_set_title(rls_engine, conversation_id, user_message, title_builder)
    # Deduct credits per successful turn (after the stream completes — D-08-6).
    credits_service.deduct(
        rls_engine=rls_engine, user_id=owner_id, amount=credits_per_turn, reason="chat_turn"
    )
    done: dict[str, object] = {
        "usage": (
            {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens}
            if usage is not None
            else {}
        ),
        "tier": tier,  # the router's real choice for this turn (D-08 gap fix)
        "format_hints": {},  # D-08-3: the API echoes empty; connectors populate (spec 12)
    }
    yield _sse("done", done)


def _persist_turn(
    *,
    rls_engine: Engine,
    conversation: Conversation,
    prior_msg_count: int,
    channel: ChannelContext | None,
) -> None:
    """Insert the new messages + update compaction state (one RLS-scoped txn).

    The loop appended the user message + assistant response to ``conversation``
    in place (D-S05-4). We persist only the messages beyond ``prior_msg_count``,
    tagging the FIRST new (user) message with the ``channel`` passthrough.
    """
    new_messages = conversation.messages[prior_msg_count:]
    channel_json = channel.model_dump() if channel is not None else None
    now = datetime.now(UTC)
    with rls_engine.begin() as conn:
        for i, msg in enumerate(new_messages):
            conn.execute(
                insert(messages_t).values(
                    id=f"msg_{uuid.uuid4().hex}",
                    conversation_id=conversation.conversation_id,
                    role=msg.role,
                    content=msg.content,
                    # Only the user message carries the inbound channel context.
                    channel=channel_json if (i == 0 and msg.role == "user") else None,
                )
            )
        conn.execute(
            update(conversations_t)
            .where(conversations_t.c.id == conversation.conversation_id)
            .values(
                compacted_summary=conversation.compacted_summary,
                compacted_up_to=conversation.compacted_up_to,
                updated_at=now,
            )
        )


async def _maybe_set_title(
    rls_engine: Engine,
    conversation_id: str,
    first_message: str,
    title_builder: Callable[[str], Awaitable[str]],
) -> None:
    """Generate + persist a short title from the first message. Best-effort: any
    failure (model error, timeout) is logged and swallowed — the conversation
    keeps its default title rather than breaking the turn."""
    try:
        title = (await title_builder(first_message)).strip()
        if title:
            set_title(
                rls_engine=rls_engine, conversation_id=conversation_id, title=title[:_MAX_TITLE_LEN]
            )
    except Exception as exc:  # noqa: BLE001 — auto-title must never break a chat turn
        _log.warning("auto-title failed for {cid}: {err}", cid=conversation_id, err=str(exc))
