"""The AMBIGUOUS-band entity judge — K2's LLM resolver (Spec K2, T8c; D-K0-9).

K0's ``EntityRegistry.resolve`` is deterministic and LLM-free; it auto-merges the
confident cases, auto-separates the clear non-matches, and hands K2 the AMBIGUOUS
review band. ``LlmEntityJudge`` is the single strict binary judgement on THAT band
only: given a mention and the registry's candidate entities, ask the small/mid-tier
model whether the mention is the same real-world entity as one of them.

**Conservative by construction** (F0.5 posture, matching K0's calibration): an
out-of-range, ``NONE``, or unparseable answer resolves to a NEW entity (the orchestrator
calls ``create_entity``) — a wrong merge fuses two real people, which is worse than a
missed alias. The prompt is engineered for the floor model (D-10-1).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from persona.logging import get_logger
from persona.schema.conversation import ConversationMessage

if TYPE_CHECKING:
    from persona.backends.protocol import ChatBackend
    from persona.graph.protocol import EntityCandidate

__all__ = ["ENTITY_JUDGE_PROMPT_VERSION", "LlmEntityJudge"]

_logger = get_logger("extraction.entity_judge")

ENTITY_JUDGE_PROMPT_VERSION = "v1"

_SYSTEM_PROMPT = """\
You decide whether a user's mention refers to the SAME real-world entity (person, \
organisation, place, or thing) as one already known. You will be given the mention \
and a numbered list of known entities. Reply with ONLY the NUMBER of the entity \
that is the SAME real-world entity, or the word NONE if the mention is a different \
entity or you are not sure. Be CONSERVATIVE: only answer a number when it is \
clearly the same entity — when in doubt, answer NONE.
"""

_MAX_TOKENS = 8


class LlmEntityJudge:
    """An ``EntityRecognizer`` over the registry's AMBIGUOUS band (mocked-testable).

    Args:
        backend: The small/mid-tier chat backend (injected at composition, D-K2-3).
    """

    def __init__(self, *, backend: ChatBackend) -> None:
        self._backend = backend

    async def judge_ambiguous(
        self,
        *,
        owner_id: str,  # noqa: ARG002 — part of the EntityRecognizer contract; scoping is the registry's
        mention: str,
        candidates: tuple[EntityCandidate, ...],
    ) -> str | None:
        """Return the chosen canonical ``entity_id``, or ``None`` to create a new one."""
        if not candidates:
            return None
        now = datetime.now(UTC)
        listing = "\n".join(f"{i}. {c.canonical_name}" for i, c in enumerate(candidates, start=1))
        user = (
            f'Mention: "{mention}"\n\nKnown entities:\n{listing}\n\nAnswer with a number or NONE.'
        )
        messages = [
            ConversationMessage(role="system", content=_SYSTEM_PROMPT, created_at=now),
            ConversationMessage(role="user", content=user, created_at=now),
        ]
        response = await self._backend.chat(messages, temperature=0.0, max_tokens=_MAX_TOKENS)
        return self._parse(response.content, candidates)

    @staticmethod
    def _parse(text: str, candidates: tuple[EntityCandidate, ...]) -> str | None:
        match = re.search(r"\d+", text)
        if match is None:
            return None  # NONE / unparseable → the conservative SEPARATE
        index = int(match.group())
        if 1 <= index <= len(candidates):
            return candidates[index - 1].entity_id
        return None  # out of range → conservative
