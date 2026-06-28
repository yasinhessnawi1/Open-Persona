"""The per-turn recent-conversation window (Spec K4, T6; K4-D-2 / K4-D-X-gating-signal-seam).

K4's context gate needs "has the user opened this topic" from the conversation, not the
bare query (query-alone re-closes the gate on a follow-up that doesn't re-name the topic
— the uncanny-concealment failure). The window is the last few conversation messages.

It rides a per-turn :class:`~contextvars.ContextVar` — the same ambient-scope pattern as
the owner id (``current_user_id``) — because the graph-retrieval callable is composed
ONCE but called per turn, and widening the retrieval signature to carry the window would
ripple through the shared ``retrieve_context`` and the voice path (the
``VoiceModelReplyProducer`` context). The ContextVar keeps the widening contained to the
K4 seam: **every loop that builds a prompt sets the window before retrieval** (chat,
voice — the conversational paths; the agentic loop is task-execution with no conversation
and no graph retrieval, so it has no window to set), and ``make_graph_retrieval``'s
``recent_window_provider`` reads it. The default is an empty window, so any unset path is
byte-identical to query-only (and a missing set is fail-safe-empty, never a crash).

ContextVars propagate into ``asyncio.to_thread`` / ``copy_context().run`` workers (the
voice path runs graph retrieval off-thread), so a set in the turn coroutine is visible to
the threaded retrieval — the same propagation the owner id already relies on.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from persona.schema.conversation import ConversationMessage

__all__ = [
    "RECENT_WINDOW_LIMIT",
    "get_recent_window",
    "set_recent_window",
    "set_recent_window_from_messages",
]

#: How many recent prior messages form the window the K4 gate reads (K4-D-2). Coarse:
#: the gate needs the topic's *presence* in recent context, not the whole transcript.
RECENT_WINDOW_LIMIT = 6

_recent_window: ContextVar[tuple[str, ...]] = ContextVar("persona_recent_window", default=())


def set_recent_window(messages: Sequence[str]) -> None:
    """Set this turn's recent-conversation window (the loop calls this before retrieval).

    Args:
        messages: The recent conversation message texts, most-recent last. The loop
            passes a short window (it need not be the whole history — the gate only needs
            "is the topic still live").
    """
    _recent_window.set(tuple(messages))


def set_recent_window_from_messages(messages: Sequence[ConversationMessage]) -> None:
    """Set the window from the conversation's recent messages (the loops' call).

    Takes the last :data:`RECENT_WINDOW_LIMIT` messages' text content (multimodal /
    non-text messages are skipped — the gate's signal is text). Every conversational
    loop (chat, voice) calls this BEFORE graph retrieval so the gate reads the
    conversation, not the bare query.
    """
    recent = messages[-RECENT_WINDOW_LIMIT:]
    set_recent_window([m.content for m in recent if isinstance(m.content, str)])


def get_recent_window() -> tuple[str, ...]:
    """This turn's recent-conversation window, or an empty tuple when unset.

    Wired as ``make_graph_retrieval``'s ``recent_window_provider``. Empty ⇒ the gate sees
    the query only (the unset / non-conversational default).
    """
    return _recent_window.get()
