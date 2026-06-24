"""Persona name-parsing / addressing (Spec C1 T7, D-C1-4) — conservative + multilingual.

Detects which persona a message addresses, used to drive a SWITCH (the sticky-active
pointer routes everything else). Optimised for **precision over recall** (the
research + the §8 over-trigger risk): a missed address is harmless (the message
routes to the already-active persona), a false switch is the only real cost — and
the no-op-on-re-naming-active rule (downstream, in the T6 ``decide_foreground``)
caps even that.

The rules (stdlib ``re`` only — Unicode-aware by default for ``str`` patterns; no
``regex`` dependency, D-C1-X-no-new-dep):

- inspect only the **leading or trailing** position — never mid-sentence (the
  "every *max* in a sentence" class is eliminated structurally);
- a **leading** name is the deliberate address convention (comma optional); a
  **trailing** name requires a preceding vocative comma/colon (so "what's the max"
  does not address persona Max — the comma isn't universal across languages, but
  requiring it for the weaker trailing position is the conservative choice);
- **exact** whole-word match, case-insensitive, Unicode word boundaries (matches
  ``Søren``/``김``; not ``maximum``/``Annabelle``); no fuzzy matching;
- if **two or more distinct personas** match → :class:`Ambiguous` (the flow stays
  on the active persona rather than guess a switch).

Owned surface — api-free; stdlib + persona-core only.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "Addressed",
    "AddressingResult",
    "Ambiguous",
    "NoName",
    "parse_addressed_persona",
]


class Addressed(BaseModel):
    """Exactly one persona is addressed — the flow foregrounds it (T6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    persona_id: str


class NoName(BaseModel):
    """No persona named — the message routes to the active persona (or the
    list-and-instructions reply when none is active)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Ambiguous(BaseModel):
    """More than one distinct persona matched — the flow stays on the active persona
    (precision over recall) rather than guess a switch.

    Attributes:
        candidate_persona_ids: The matched persona ids (for an optional soft
            disambiguation prompt).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_persona_ids: list[str]


# The three outcomes the flow branches on.
AddressingResult = Addressed | NoName | Ambiguous

# Trailing punctuation allowed after a trailing-position name ("…, Kai?!").
_TRAILING_PUNCT = r"[?!.\s]*"


def _addresses(text: str, name: str) -> bool:
    """True if ``text`` addresses ``name`` at the leading or (comma-prefixed) trailing position."""
    escaped = re.escape(name)
    # Leading: the name at the start, a word boundary after (comma optional — the
    # deliberate "Name, …" / "Name …" convention).
    leading = re.compile(rf"^\s*{escaped}\b", re.IGNORECASE)
    # Trailing: a vocative comma/colon, then the name at the end (+ optional punct).
    trailing = re.compile(rf"[,:]\s*{escaped}\b{_TRAILING_PUNCT}$", re.IGNORECASE)
    return bool(leading.search(text) or trailing.search(text))


def parse_addressed_persona(
    text: str, *, persona_names: Mapping[str, Sequence[str]]
) -> AddressingResult:
    """Parse which persona ``text`` addresses, if any (D-C1-4).

    Args:
        text: The inbound message text.
        persona_names: ``persona_id`` → the persona's addressable names (its display
            name + any configured aliases).

    Returns:
        :class:`Addressed` when exactly one persona is named at a valid position,
        :class:`Ambiguous` when two or more distinct personas are, else
        :class:`NoName`.
    """
    matched = {
        persona_id
        for persona_id, names in persona_names.items()
        if any(_addresses(text, name) for name in names)
    }
    if not matched:
        return NoName()
    if len(matched) == 1:
        return Addressed(persona_id=next(iter(matched)))
    return Ambiguous(candidate_persona_ids=sorted(matched))
