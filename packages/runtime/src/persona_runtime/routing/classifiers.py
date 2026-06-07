"""Spec 18 routing signal classifiers (T06).

The Spec 05 :class:`HeuristicRouter` exposes ``_is_boilerplate`` and
``_is_persona_critical`` as private methods for back-compat. T06 extracts the
same logic as free functions so the composition root
(:class:`~persona_runtime.loop.ConversationLoop`) can pre-classify a message
into :class:`~persona_runtime.routing.types.RoutingContext` signals BEFORE
calling :meth:`Router.route` — without reaching into the router's private
methods.

The implementations are byte-for-byte identical to the Spec 05 originals
(D-18-X-strangler-fig-alias-shape "no regression in heuristic behaviour");
:class:`HeuristicRouter`'s instance methods delegate here so the two paths
cannot drift.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from persona.schema.persona import Persona

__all__ = [
    "is_boilerplate",
    "is_persona_critical",
    "persona_keywords",
]

# Acknowledgement / reformat phrases that route to the small tier (spec §6.1).
_BOILERPLATE_PATTERNS: tuple[str, ...] = (
    r"\bok\b",
    r"\bokay\b",
    r"\bthanks\b",
    r"\bthank you\b",
    r"\bthx\b",
    r"\bgot it\b",
    r"\bsounds good\b",
    r"\bgreat\b",
    r"\bperfect\b",
    r"\bnoted\b",
    r"\bunderstood\b",
    r"\breformat\b",
    r"\brephrase\b",
    r"\bsummari[sz]e (that|this|it)\b",
    r"\btry again\b",
)

# Identity / constraint-pressuring phrases that route to frontier (spec §6.1).
_CRITICAL_PATTERNS: tuple[str, ...] = (
    r"\bwho are you\b",
    r"\bwhat are you\b",
    r"\bwhat'?s your (background|role|name|purpose)\b",
    r"\btell me about yourself\b",
    r"\bare you (an? )?(ai|bot|robot|model|human)\b",
    r"\bignore (your|the|all|previous|prior)( \w+)* (instructions?|rules?|constraints?)\b",
    r"\bforget (your|the|all|previous)( \w+)* (instructions?|rules?)\b",
    r"\bjust give me the answer\b",
    r"\bdrop the (act|persona|character)\b",
    r"\bstop pretending\b",
    r"\bbreak character\b",
)

_MIN_KEYWORD_LEN = 5
_WORD_RE = re.compile(rf"[a-zA-Z]{{{_MIN_KEYWORD_LEN},}}")


def is_boilerplate(message: str) -> bool:
    """``True`` for acknowledgements + reformat/clarify requests."""
    text = message.strip().lower()
    return any(re.search(p, text) for p in _BOILERPLATE_PATTERNS)


def is_persona_critical(message: str, persona: Persona) -> bool:
    """``True`` for identity questions, constraint pressure, or worldview hits.

    Two parts: static identity/constraint-pressure phrases, plus a check
    against keywords derived from the persona's own constraints + worldview
    claims (spec §6.1). A message touching the persona's contested/worldview
    territory deserves the frontier tier.
    """
    text = message.lower()
    if any(re.search(p, text) for p in _CRITICAL_PATTERNS):
        return True
    keywords = persona_keywords(persona)
    if not keywords:
        return False
    message_words = {w.lower() for w in _WORD_RE.findall(text)}
    return bool(keywords & message_words)


def persona_keywords(persona: Persona) -> set[str]:
    """Significant words drawn from the persona's constraints + worldview.

    Lowercased, length ≥ :data:`_MIN_KEYWORD_LEN`. Derived per call — one
    process serves many personas; caching would couple the classifier to
    the persona lifecycle without measurable benefit at v0.1 volume.
    """
    keywords: set[str] = set()
    for constraint in persona.identity.constraints:
        keywords.update(w.lower() for w in _WORD_RE.findall(constraint))
    for claim in persona.worldview:
        keywords.update(w.lower() for w in _WORD_RE.findall(claim.claim))
        if claim.domain:
            keywords.update(w.lower() for w in _WORD_RE.findall(claim.domain))
    return keywords
