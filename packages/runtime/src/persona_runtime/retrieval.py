"""Shared per-turn conditioning retrieval (extracted from the text loop, D-V5-6).

The persona-conditioning retrieval — reading identity from its store and
querying self-facts / worldview / episodic for *this* turn — is the single
authoritative representation of "what the persona knows right now." Both the
text :class:`~persona_runtime.loop.ConversationLoop` and the voice turn
(spec V5) MUST share it: reimplementing it would risk conditioning drift
between modalities, which is the persona-bypass the voice spec forbids
(spec V5 §8; criteria 1+2).

This module is the extraction point. :func:`retrieve_context` was previously
``ConversationLoop._retrieve``; the loop now delegates to it byte-identically
(the only behavioural change is *where* the code lives). The added ``identity``
keyword is the spec-V5 D-V5-1 session-cache hook: identity is immutable at
runtime (Spec 01), so a voice session reads it once and passes it back each
turn, skipping the redundant ``get_all`` — while the variable stores are still
queried per turn. The text loop never passes it, so its behaviour is unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona_runtime.prompt import RetrievedContext

if TYPE_CHECKING:
    from collections.abc import Mapping

    from persona.schema.chunks import PersonaChunk
    from persona.stores.protocol import MemoryStore

__all__ = [
    "DEFAULT_RETRIEVE_TOP_K",
    "EARLY_RETRIEVE_TOP_K",
    "dynamic_top_k",
    "retrieve_context",
]

#: Floor top-k for the per-turn variable-store queries (the text loop's
#: historical ``_RETRIEVE_TOP_K``). Reached once the live history is long
#: enough to carry the recent context itself.
DEFAULT_RETRIEVE_TOP_K = 3

#: Upper bound used at the very start of a conversation, before the live
#: history carries any recent context. A fresh turn has spare prompt budget
#: and needs more retrieved memory to establish continuity from prior
#: sessions, so it pulls this many chunks and decays toward the floor.
EARLY_RETRIEVE_TOP_K = 8

#: Minimum number of *recency*-ordered episodic chunks always folded in
#: (alongside similarity) once a conversation is in progress, so the tail of
#: the previous session surfaces even when the current message is not
#: semantically near it ("what were we talking about?").
_MIN_RECENCY = 2


def dynamic_top_k(
    history_turns: int,
    *,
    high: int = EARLY_RETRIEVE_TOP_K,
    low: int = DEFAULT_RETRIEVE_TOP_K,
) -> int:
    """Per-turn retrieval budget that shrinks as the live history grows.

    A fresh conversation (``history_turns <= 0``) has no recent context in the
    prompt yet and ample budget, so it pulls ``high`` chunks to establish
    continuity from prior sessions. Each subsequent turn the live history
    already carries the recent context and the prompt fills, so the budget
    decays one chunk per accumulated turn down to the ``low`` floor.

    Args:
        history_turns: Turns already in the managed history (the
            conversation-progression signal). ``0`` is the first turn.
        high: Budget at the start of a conversation.
        low: Floor budget once history carries the recent context.

    Returns:
        The retrieval budget for this turn, in ``[low, high]``.
    """
    if history_turns <= 0:
        return high
    return max(low, high - history_turns)


def _recall_episodic(
    store: MemoryStore,
    persona_id: str,
    user_message: str,
    k: int,
    *,
    recency: bool,
) -> list[PersonaChunk]:
    """Episodic recall for one turn: similarity, optionally recency-augmented.

    With ``recency=False`` this is the historical behaviour — the top-``k``
    chunks by semantic similarity to ``user_message``. With ``recency=True``
    the most-recent turns are guaranteed a place in the budget (deduped against
    the similarity hits) so cross-session continuity does not depend on the
    current message embedding near a past one. The merged result is returned
    oldest-first so the prompt reads chronologically.
    """
    similar = store.query(persona_id, user_message, k)
    if not recency:
        return similar
    n_recent = min(k, max(_MIN_RECENCY, k // 2))
    recent = store.recent(persona_id, n_recent)
    seen = {c.id for c in recent}
    extra = [c for c in similar if c.id not in seen][: max(0, k - len(recent))]
    merged = [*recent, *extra]
    merged.sort(key=lambda c: c.created_at)
    return merged


def retrieve_context(
    stores: Mapping[str, MemoryStore],
    persona_id: str,
    user_message: str,
    *,
    top_k: int = DEFAULT_RETRIEVE_TOP_K,
    identity: list[PersonaChunk] | None = None,
    history_turns: int | None = None,
) -> RetrievedContext:
    """Retrieve this turn's conditioning context from the typed stores.

    Identity comes from ``identity.get_all(persona_id)`` (session-constant);
    self-facts / worldview / episodic from ``query(persona_id, user_message,
    top_k)`` (per turn). This is the shared conditioning-retrieval used by both
    the text loop and the voice turn (D-V5-6) — never reimplemented.

    Args:
        stores: The four typed memory stores keyed by kind (``identity`` /
            ``self_facts`` / ``worldview`` / ``episodic``).
        persona_id: The persona whose stores to read.
        user_message: This turn's message — the query for the variable stores.
        top_k: How many chunks to retrieve per variable store.
        identity: Pre-fetched identity chunks (the D-V5-1 voice session cache).
            ``None`` (the default, and the text loop's path) reads identity from
            its store this call — byte-identical to the historical behaviour.
        history_turns: Turns already in the managed history. When given, the
            per-turn budget is computed dynamically (:func:`dynamic_top_k` —
            high on a fresh turn, decaying to ``top_k`` as history grows) and
            episodic recall is recency-augmented so the previous session's tail
            surfaces regardless of semantic match. ``None`` (the default)
            preserves the historical fixed-``top_k`` similarity-only behaviour.

    Returns:
        The :class:`RetrievedContext` the prompt builder conditions on.
    """
    resolved_identity = identity if identity is not None else stores["identity"].get_all(persona_id)
    dynamic = history_turns is not None
    k = dynamic_top_k(history_turns) if history_turns is not None else top_k
    return RetrievedContext(
        identity=resolved_identity,
        self_facts=stores["self_facts"].query(persona_id, user_message, k),
        worldview=stores["worldview"].query(persona_id, user_message, k),
        episodic=_recall_episodic(stores["episodic"], persona_id, user_message, k, recency=dynamic),
    )
