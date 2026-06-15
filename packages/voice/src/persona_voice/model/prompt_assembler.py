"""Per-session persona-conditioning prompt assembly for voice (spec V5 T2).

The voice turn is conditioned by *exactly* the same machinery as the text turn:
the shared :func:`persona_runtime.retrieval.retrieve_context` +
:meth:`persona_runtime.prompt.PromptBuilder.build` (D-V5-6 — never a thinner
"voice prompt" that drops the persona; spec V5 §8, criteria 1+2).

:class:`VoicePromptAssembler` adds the D-V5-1 real-time adaptation: it caches
the **session-constant** identity store-read once (identity is immutable at
runtime — Spec 01), and per turn retrieves only the **variable** stores
(self-facts / worldview / episodic) before assembling the full prompt through
the shared ``PromptBuilder``. The constant work happens once; the conditioning
content is still fully present on every turn. The history compaction that the
text loop runs synchronously moves off the critical path in T6 — this assembler
takes the already-managed history as input.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona_runtime.retrieval import DEFAULT_RETRIEVE_TOP_K, retrieve_context

if TYPE_CHECKING:
    from persona.schema.chunks import PersonaChunk
    from persona.schema.conversation import ConversationMessage
    from persona_runtime.prompt import DocumentContext, RetrievedContext

    from persona_voice.model.turn_context import VoiceTurnContext

__all__ = ["VoicePromptAssembler"]


class VoicePromptAssembler:
    """Assembles the persona-conditioned voice prompt, caching the constant block.

    Constructed once per voice session over a
    :class:`~persona_voice.model.turn_context.VoiceTurnContext`. The first turn
    reads identity from its store; every later turn reuses the cached identity
    (D-V5-1) and re-queries only the variable stores. The full conditioning
    (identity + constraints + retrieved memory + history + skills) is assembled
    by the shared ``PromptBuilder`` every turn — no bypass.

    Args:
        context: The session-bound runtime collaborators.
    """

    def __init__(self, context: VoiceTurnContext) -> None:
        self._ctx = context
        self._cached_identity: list[PersonaChunk] | None = None

    def _identity(self) -> list[PersonaChunk]:
        """The session-constant identity chunks (read once, then cached)."""
        if self._cached_identity is None:
            self._cached_identity = self._ctx.stores["identity"].get_all(self._ctx.persona_id)
        return self._cached_identity

    def retrieve(
        self, user_message: str, *, top_k: int = DEFAULT_RETRIEVE_TOP_K
    ) -> RetrievedContext:
        """Retrieve this turn's conditioning context (cached identity + variable).

        Uses the shared :func:`~persona_runtime.retrieval.retrieve_context` with
        the cached identity hook (D-V5-1) so the identity store is not re-read.
        """
        return retrieve_context(
            self._ctx.stores,
            self._ctx.persona_id,
            user_message,
            top_k=top_k,
            identity=self._identity(),
        )

    def build(
        self,
        user_message: str,
        *,
        history: list[ConversationMessage],
        max_tokens: int,
        skill_index: str = "",
        matched_skill_content: str | None = None,
        document_context: DocumentContext | None = None,
    ) -> list[ConversationMessage]:
        """Assemble the full persona-conditioned prompt for one voice turn.

        Args:
            user_message: This turn's transcribed user message.
            history: The already-managed conversation history (compacted summary
                + recent verbatim turns). Compaction runs off the critical path
                (T6); this assembler does not block on it.
            max_tokens: The chosen backend's context-window budget (from routing).
            skill_index: The rendered "available skills" block (voice tool/skill
                scope is decided in T7; defaults to empty here).
            matched_skill_content: Already-budgeted active-skill content, if any.
            document_context: Optional retrieved-document context.

        Returns:
            ``[system, *history, user]`` — the same shape the text loop builds,
            via the shared ``PromptBuilder`` (criteria 1+2; no persona-bypass).
        """
        context = self.retrieve(user_message)
        return self._ctx.prompt_builder.build(
            self._ctx.persona,
            context,
            history,
            skill_index,
            user_message,
            max_tokens=max_tokens,
            matched_skill_content=matched_skill_content,
            document_context=document_context,
        )
