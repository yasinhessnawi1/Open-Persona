"""The runtime LLM extraction pipeline (Spec K2, T2; the ratified layering split).

``LlmExtractor`` is the persona-RUNTIME implementation of the persona-CORE
``Extractor`` Protocol: build the versioned prompt → ONE ``backend.chat`` call →
lenient parse → bounded candidates. The LLM lives here (core stays LLM-free per
K0); the small/mid-tier backend is INJECTED at composition (D-K2-3 — the Spec-18
router picks the tier; the synthesis handler / direct-write tool wire it in T7/T8).

Design notes:
- **Deterministic.** temperature 0.0 (the grounded-extraction posture).
- **Lenient.** Unparseable output → no candidates, never a raised exception
  (synthesis is off-critical-path; extracting nothing beats crashing). Provider
  errors (auth/429/timeout) propagate UNCHANGED — the runtime is a composition
  layer (D-05-2), the synthesis job's retry policy owns them.
- **Restraint backstop.** The prompt anchors 0-3 candidates; ``max_candidates`` is
  a defense-in-depth cap against a runaway model. A truncation is LOGGED, never
  silent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.logging import get_logger

from persona_runtime.extraction.parse import parse_candidates
from persona_runtime.extraction.prompt import build_extraction_messages

if TYPE_CHECKING:
    from persona.backends.protocol import ChatBackend
    from persona.extraction import ExtractionCandidate, ExtractionInput

__all__ = ["LlmExtractor"]

_logger = get_logger("extraction.pipeline")

# Defense-in-depth cap; the prompt's restraint rule is the primary control. Env-
# bound at composition (T8); a generous default so it only catches runaways.
_DEFAULT_MAX_CANDIDATES = 8
# Bounds the response; extraction output is small (a handful of short candidates).
_DEFAULT_MAX_TOKENS = 1536


class LlmExtractor:
    """Extracts grounded knowledge candidates from an interaction via one model call.

    Implements the core :class:`persona.extraction.Extractor` Protocol.

    Args:
        backend: The (small/mid-tier) chat backend, injected at composition.
        max_candidates: Restraint backstop — candidates beyond this are dropped
            with a warning (the prompt is the primary restraint control).
        max_tokens: Response cap for the extraction call.
    """

    def __init__(
        self,
        *,
        backend: ChatBackend,
        max_candidates: int = _DEFAULT_MAX_CANDIDATES,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        self._backend = backend
        self._max_candidates = max_candidates
        self._max_tokens = max_tokens

    async def extract(self, interaction: ExtractionInput) -> tuple[ExtractionCandidate, ...]:
        """Extract grounded candidates from ``interaction`` (empty tuple if none)."""
        messages = build_extraction_messages(interaction)
        response = await self._backend.chat(messages, temperature=0.0, max_tokens=self._max_tokens)
        candidates = parse_candidates(response.content)
        if len(candidates) > self._max_candidates:
            _logger.warning(
                "extraction truncated to max_candidates",
                interaction_id=interaction.interaction_id,
                produced=len(candidates),
                max_candidates=self._max_candidates,
            )
            candidates = candidates[: self._max_candidates]
        return candidates
