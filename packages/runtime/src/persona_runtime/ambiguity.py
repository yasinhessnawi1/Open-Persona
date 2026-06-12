"""Tier-1 ambiguity detection — the proactive-question trigger (spec 21, D-21-1).

A pure, dependency-free keyword/phrase detector that flags when a user message
is under-specified enough to warrant a clarifying question. It is a cheap,
high-recall *trigger*, not an arbiter (R-21-2: naive ambiguity classification is
near chance, so we bias for precision via tight patterns + hard suppressors and
let the autonomy policy gate what actually gets asked).

Two-step contract, kept separate on purpose:

- :func:`detect_ambiguity` — pure detection over the message + a small
  :class:`DetectionContext`. Returns the highest-priority :class:`AmbiguitySignal`
  or ``None``. Independent of autonomy: it detects, it does not decide to ask.
- :func:`should_ask` — gates a signal against an
  :class:`~persona.autonomy.AutonomyPolicy`. ``signal.signal_class in
  policy.asks_on``. This is where class C (conflicting constraints) is
  *detected but never asked* (D-21-19, it is absent from every level's
  ``asks_on``) and class D (safety-critical) always passes (gate bypass). The
  caller asks when ``should_ask`` is true and otherwise prepends a stated
  assumption (D-21-18).

Tier-2 LLM escalation is a locked Protocol seam only in v0.1 (D-21-1):
:class:`AmbiguityEscalator` defines the contract; no implementation ships.

The four :class:`~persona.autonomy.AmbiguityClass` values live in persona-core
so this runtime module imports them downward (layering).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from persona.autonomy import AmbiguityClass
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from persona.autonomy import AutonomyPolicy

__all__ = [
    "AmbiguityEscalator",
    "AmbiguitySignal",
    "DetectionContext",
    "EscalationVerdict",
    "detect_ambiguity",
    "should_ask",
]

#: Messages longer than this are scanned only at their head and tail windows so
#: a pasted document body cannot trigger false positives (R-21-2 edge case).
_LONG_MESSAGE_CHARS: int = 2000
_WINDOW_CHARS: int = 400

# Class priority for picking a single winning signal when several fire. Safety
# first (recall-biased), then the precision-biased classes, then detect-only C.
_CLASS_PRIORITY: dict[AmbiguityClass, int] = {
    AmbiguityClass.SAFETY_CRITICAL_GAP: 3,
    AmbiguityClass.MISSING_PARAMETER: 2,
    AmbiguityClass.VAGUE_SCOPE: 1,
    AmbiguityClass.CONFLICTING_CONSTRAINTS: 0,
}

# User-override phrases that suppress any question for the request (the user has
# explicitly told the persona to proceed). EN + Norwegian Bokmål.
_OVERRIDE_PHRASES: tuple[str, ...] = (
    "just do it",
    "just go ahead",
    "use your judgment",
    "use your judgement",
    "guess",
    "whatever you think",
    "bare gjør det",
    "bare kjør på",
    "gjør som du vil",
)


@dataclass(frozen=True, slots=True)
class _Pattern:
    """One compiled detection rule (internal seed-table row)."""

    pattern_id: str
    signal_class: AmbiguityClass
    regex: re.Pattern[str]
    weight: float
    missing_element: str
    language: str
    # Deictic rules (bare "it"/"den"/"det" as object) only fire on a referent-less
    # message — i.e. when there is no prior turn that could have introduced one.
    requires_no_referent: bool = False


def _p(
    pattern_id: str,
    signal_class: AmbiguityClass,
    pattern: str,
    weight: float,
    missing_element: str,
    language: str,
    *,
    requires_no_referent: bool = False,
) -> _Pattern:
    return _Pattern(
        pattern_id=pattern_id,
        signal_class=signal_class,
        regex=re.compile(pattern, re.IGNORECASE),
        weight=weight,
        missing_element=missing_element,
        language=language,
        requires_no_referent=requires_no_referent,
    )


# Seed table (R-21-2 §1). Tight, precision-biased patterns across the four
# classes, EN + Norwegian Bokmål. Compiled once at import (fail-fast on a bad
# regex). Not exhaustive — the extensible surface is the table itself.
_A = AmbiguityClass.MISSING_PARAMETER
_B = AmbiguityClass.VAGUE_SCOPE
_C = AmbiguityClass.CONFLICTING_CONSTRAINTS
_D = AmbiguityClass.SAFETY_CRITICAL_GAP

_PATTERNS: tuple[_Pattern, ...] = (
    # Class A — missing parameter (precision).
    _p(
        "A.bare_send_en",
        _A,
        r"^\s*(send|book|schedule|email|call)\s*[.?!]?\s*$",
        2.0,
        "target",
        "en",
    ),
    _p("A.bare_send_no", _A, r"^\s*(send|bestill|ring|book)\s*[.?!]?\s*$", 2.0, "target", "no"),
    _p(
        "A.send_pronoun_en",
        _A,
        r"\b(send|email|deliver)\s+(it|them|this|that)\b",
        2.0,
        "recipient",
        "en",
        requires_no_referent=True,
    ),
    _p(
        "A.send_pronoun_no",
        _A,
        r"\b(send|lever)\s+(den|det|dette|disse)\b",
        2.0,
        "recipient",
        "no",
        requires_no_referent=True,
    ),
    _p(
        "A.meeting_no_time_en",
        _A,
        r"\b(set up|schedule|arrange)\s+(a\s+)?(meeting|call|appointment)\b(?!.*\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|at\s+\d)\b)",  # noqa: E501
        2.0,
        "time",
        "en",
    ),
    _p("A.remind_bare_en", _A, r"\bremind me\b(?!\s+(to|about)\b)", 2.0, "subject", "en"),
    # Class B — vague scope (precision).
    _p(
        "B.draft_scopeless_en",
        _B,
        r"\b(draft|write|compose|prepare)\s+(a|an|the)\s+(complaint|letter|document|report|memo|email)\b(?!.*\b(about|regarding|for|to|on)\b)",
        1.5,
        "scope",
        "en",
    ),
    _p(
        "B.draft_scopeless_no",
        _B,
        r"\b(skriv|lag|forfatt)\s+(en|et)\s+(klage|brev|rapport|notat)\b(?!.*\b(om|til|angående|for)\b)",
        1.5,
        "scope",
        "no",
    ),
    _p(
        "B.fix_proform_en",
        _B,
        r"\b(fix|handle|sort out|deal with|take care of)\s+(this|that|it)\b",
        1.5,
        "referent",
        "en",
        requires_no_referent=True,
    ),
    _p(
        "B.make_it_better_en",
        _B,
        r"\bmake\s+(it|this|that)\s+(better|nicer|good)\b",
        1.5,
        "criteria",
        "en",
    ),
    _p(
        "B.bare_everything_en",
        _B,
        r"^\s*(everything|all of it|the usual)\s*[.?!]?\s*$",
        1.5,
        "scope",
        "en",
    ),
    # Class C — conflicting constraints (detect-and-log only, D-21-19).
    _p(
        "C.cheap_premium_en",
        _C,
        r"\b(cheap|cheapest|low ?cost)\b.*\b(premium|best quality|top|luxur)",
        1.0,
        "priority",
        "en",
    ),
    _p(
        "C.brief_comprehensive_en",
        _C,
        r"\b(brief|short|concise)\b.*\b(comprehensive|thorough|detailed|exhaustive)\b",
        1.0,
        "depth",
        "en",
    ),
    _p(
        "C.billig_best_no",
        _C,
        r"\b(billig|billigst)\b.*\b(best kvalitet|premium|luksus)",
        1.0,
        "priority",
        "no",
    ),
    # Class D — safety-critical gap (recall; bypasses the gate at every level).
    _p(
        "D.delete_bulk_en",
        _D,
        r"\b(delete|remove|wipe|drop|erase)\s+(everything|all|all of (it|them)|the (whole|entire))\b",  # noqa: E501
        3.0,
        "target",
        "en",
    ),
    _p(
        "D.delete_bulk_no",
        _D,
        r"\b(slett|fjern)\s+(alt|alle|alt sammen|hele)\b",
        3.0,
        "target",
        "no",
    ),
    _p(
        "D.delete_pronoun_en",
        _D,
        r"\b(delete|remove|wipe|drop)\s+(it|them|that|those)\b",
        3.0,
        "target",
        "en",
        requires_no_referent=True,
    ),
    _p(
        "D.send_money_en",
        _D,
        r"\b(pay|send|transfer|wire)\b(?=.*\b(money|payment|\$|kr|nok|usd|eur)\b)(?!.*\b\d)",
        3.0,
        "amount",
        "en",
    ),
    _p(
        "D.publish_underspecified_en",
        _D,
        r"\b(publish|post|send)\s+(it|this|that)\s+(publicly|to everyone|to the list|live)\b",
        3.0,
        "target",
        "en",
        requires_no_referent=True,
    ),
)


class DetectionContext(BaseModel):
    """The minimal conversation context the detector needs (R-21-2 §1).

    A message-local detector cannot handle follow-ups, answers to its own
    question, or deictics, so the loop supplies a small, serialisable context.
    Frozen + ``extra="forbid"``.

    Attributes:
        prev_turn_was_question: The previous assistant/persona turn was itself a
            question. Hard suppressor: this message is the *answer*, never a new
            ambiguity (suppressor #1).
        has_prior_context: There are earlier turns that could have introduced a
            referent. Gates deictic patterns (bare "it"/"den"): a first-turn
            ``"send it"`` is referent-less and fires; a later one is suppressed.
        language: The persona's default language (``"en"``, ``"no"``/``"nb"``).
            Advisory only — both language tables always run (Bokmål/Danish are
            hard to distinguish on short text), so no language gating.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    prev_turn_was_question: bool = False
    has_prior_context: bool = False
    language: str = "en"


class AmbiguitySignal(BaseModel):
    """A detected ambiguity (one winning pattern match). Frozen + ``extra="forbid"``.

    Attributes:
        signal_class: Which of the four ambiguity classes fired.
        pattern_id: The seed-table rule id (audit/telemetry).
        matched_span: The substring that matched (audit/telemetry).
        missing_element: What the question should resolve ("recipient", "time",
            "scope", "target", ...). Localising the gap beats a generic
            "please clarify" (R-21-2).
        weight: The rule's weight (higher = stronger signal).
        language: The matched rule's language tag.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_class: AmbiguityClass
    pattern_id: str
    matched_span: str
    missing_element: str
    weight: float = Field(ge=0.0)
    language: str


class EscalationVerdict(BaseModel):
    """Tier-2 escalation result (D-21-1 seam contract). Not produced in v0.1.

    Locked now so an implementation slots in additively: a single flat
    structured call deciding ask-vs-proceed and, when asking, the question text
    plus three option labels.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Literal["ask", "proceed"]
    reason: str = ""
    question: str = ""
    options: tuple[str, str, str] | None = None


@runtime_checkable
class AmbiguityEscalator(Protocol):
    """Tier-2 LLM escalation port (D-21-1). Seam only — no v0.1 implementation.

    A future implementation runs an LLM judgment on a tier-1 signal to confirm
    or veto asking, and to author the question. Injected into the loop via
    constructor when it ships; until then the loops run tier-1 only.
    """

    async def escalate(self, message: str, signal: AmbiguitySignal) -> EscalationVerdict:
        """Judge whether ``signal`` on ``message`` warrants asking, and how."""
        ...


def _search_text(message: str) -> str:
    """Return the text to scan: the whole message, or head+tail for long pastes."""
    if len(message) <= _LONG_MESSAGE_CHARS:
        return message
    return message[:_WINDOW_CHARS] + "\n" + message[-_WINDOW_CHARS:]


def _is_override(message: str) -> bool:
    lowered = message.casefold()
    return any(phrase in lowered for phrase in _OVERRIDE_PHRASES)


def detect_ambiguity(message: str, ctx: DetectionContext) -> AmbiguitySignal | None:
    """Detect the highest-priority ambiguity in ``message`` (pure; D-21-1).

    Suppressors run first (previous turn was a question; an explicit user
    override; long-message windowing; deictic referent gating). Then every seed
    pattern is matched and the winner is chosen by class priority (safety first)
    then weight. Detection is independent of autonomy — :func:`should_ask` gates
    what is actually asked.

    Args:
        message: The user's message.
        ctx: The small conversation context.

    Returns:
        The winning :class:`AmbiguitySignal`, or ``None`` if nothing fired or a
        suppressor applied.
    """
    if ctx.prev_turn_was_question:  # suppressor #1 — this message is an answer
        return None
    if _is_override(message):  # suppressor #2 — user said proceed
        return None

    text = _search_text(message)  # suppressor #3 — window long pastes
    best: tuple[int, float, _Pattern, str] | None = None
    for pattern in _PATTERNS:
        if pattern.requires_no_referent and ctx.has_prior_context:
            continue  # suppressor #4 — deictic with a possible referent upstream
        match = pattern.regex.search(text)
        if match is None:
            continue
        priority = _CLASS_PRIORITY[pattern.signal_class]
        candidate = (priority, pattern.weight, pattern, match.group(0))
        if best is None or (priority, pattern.weight) > (best[0], best[1]):
            best = candidate

    if best is None:
        return None
    _priority, _weight, pattern, span = best
    return AmbiguitySignal(
        signal_class=pattern.signal_class,
        pattern_id=pattern.pattern_id,
        matched_span=span.strip(),
        missing_element=pattern.missing_element,
        weight=pattern.weight,
        language=pattern.language,
    )


def should_ask(signal: AmbiguitySignal, policy: AutonomyPolicy) -> bool:
    """Return whether ``signal`` warrants a question under ``policy`` (gating).

    ``signal.signal_class in policy.asks_on``. Class D is in every level's
    ``asks_on`` (gate bypass); class C is in none (detect-and-log only,
    D-21-19), so this returns ``False`` for it at every level and the caller
    prepends a stated assumption instead (D-21-18).
    """
    return signal.signal_class in policy.asks_on
