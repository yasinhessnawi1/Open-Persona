"""The inbound routing decision (Spec C1 flow / C2-gated) — pure + platform-agnostic.

Given the parsed addressing outcome
(:func:`~persona_connectors.domain.addressing.parse_addressed_persona`), the
channel's current foreground (the active persona, if any —
:meth:`~persona_connectors.domain.conversation_model.ConversationStateStore.current_foreground`),
and the owner's persona set, decide WHAT the inbound should do: drive a specific
persona, or reply with the list-and-instructions. This is the **framework
responsibility** the C1 README claims ("inbound → route → respond → outbound" +
persona-selection / name-parsing) — it lives in C1 so every adapter (C2–C5) shares
ONE decision tree instead of each reimplementing it. The adapters supply only the
I/O (extract text, run the turn, render, send).

The rules (C1-D-4 sticky-pointer + C1-D-7 first-contact):

- **Addressed(P)** → drive persona ``P`` (a switch if ``P`` isn't active; the
  foreground flip is the store's job, not this decision's).
- **NoName / Ambiguous with an active persona** → drive the active persona (the
  sticky pointer routes everything unnamed; an ambiguous match never guesses a
  switch — it stays).
- **NoName, no active, exactly one persona** → auto-foreground that sole persona
  (no pointless prompt — sticky from message one, C1-D-7).
- **Otherwise** (no active, and no single unambiguous target) →
  list-and-instructions.

Pure, deterministic, api-free — exhaustively unit-tested; CQS (a decision, no I/O,
no mutation).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from persona_connectors.domain.addressing import Addressed

if TYPE_CHECKING:
    from collections.abc import Sequence

    from persona_connectors.domain.addressing import AddressingResult

__all__ = [
    "DrivePersona",
    "ListAndInstructions",
    "RouteDecision",
    "decide_route",
]


class DrivePersona(BaseModel):
    """Drive a specific persona's conversation (the flow foregrounds + runs the turn)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    persona_id: str


class ListAndInstructions(BaseModel):
    """No clear target — reply listing the owner's personas + how to address them (C1-D-7)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# The two routing outcomes the flow branches on.
RouteDecision = DrivePersona | ListAndInstructions


def decide_route(
    addressing: AddressingResult,
    *,
    active_persona_id: str | None,
    owner_persona_ids: Sequence[str],
) -> RouteDecision:
    """Decide what an inbound message routes to (pure; C1-D-4 + C1-D-7).

    Args:
        addressing: The parsed addressing outcome (Addressed / NoName / Ambiguous).
        active_persona_id: The channel's currently-foregrounded persona, or ``None``.
        owner_persona_ids: The owner's persona ids (for the single-persona
            auto-foreground case).

    Returns:
        :class:`DrivePersona` (an addressed persona, the sticky active persona, or a
        sole persona auto-foregrounded) or :class:`ListAndInstructions` (no active
        persona and no single unambiguous target).
    """
    if isinstance(addressing, Addressed):
        return DrivePersona(persona_id=addressing.persona_id)
    # NoName or Ambiguous: the sticky pointer routes everything unnamed; an ambiguous
    # match never guesses a switch (precision over recall, C1-D-4).
    if active_persona_id is not None:
        return DrivePersona(persona_id=active_persona_id)
    # No active persona: auto-foreground a sole persona (C1-D-7), else list.
    if len(owner_persona_ids) == 1:
        return DrivePersona(persona_id=owner_persona_ids[0])
    return ListAndInstructions()
