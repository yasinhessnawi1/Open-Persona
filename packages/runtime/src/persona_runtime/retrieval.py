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

__all__ = ["DEFAULT_RETRIEVE_TOP_K", "retrieve_context"]

#: Default top-k for the per-turn variable-store queries (the text loop's
#: historical ``_RETRIEVE_TOP_K``).
DEFAULT_RETRIEVE_TOP_K = 3


def retrieve_context(
    stores: Mapping[str, MemoryStore],
    persona_id: str,
    user_message: str,
    *,
    top_k: int = DEFAULT_RETRIEVE_TOP_K,
    identity: list[PersonaChunk] | None = None,
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

    Returns:
        The :class:`RetrievedContext` the prompt builder conditions on.
    """
    resolved_identity = identity if identity is not None else stores["identity"].get_all(persona_id)
    return RetrievedContext(
        identity=resolved_identity,
        self_facts=stores["self_facts"].query(persona_id, user_message, top_k),
        worldview=stores["worldview"].query(persona_id, user_message, top_k),
        episodic=stores["episodic"].query(persona_id, user_message, top_k),
    )
