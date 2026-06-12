"""Task detection — map a chat message to a declared skill/tool (spec 21 §2.2).

The auto-dispatch gate's first stage (D-21-3 / D-21-10): a cheap, pure,
deterministic detector that decides whether a user message is a *task request*
mapping to one of the persona's **declared** capabilities. It is precision-biased
and false-positive-averse — a false positive launches an unwanted agentic run, so
the detector must under-trigger (R-21-3): high dispatch threshold, an ambiguity
margin (a near-tie between two capabilities is a clarifying question, not a coin
flip), and hard guards that veto capability-questions / negations.

Shape (R-21-3 §1): a data-driven :class:`TaskTriggerRegistry` built from a seed
of :class:`TriggerEntry` rows (extensible by passing extra entries to the
constructor — never by editing code, never via decorator/import side effects).
Entries are filtered to the persona's declared tool + skill allow-set *before*
compiling, so undeclared capabilities are inert (spec acceptance #6). The public
surface is the pure :meth:`TaskTriggerRegistry.detect`.

Detection only *proposes*; the API-layer auto-dispatcher (T10) consults the
consent gate and creates the run (D-21-10 layer split). A false positive costs
at most a confirmation prompt, never an unwanted run.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from collections.abc import Sequence

    from persona.schema.persona import Persona

__all__ = [
    "DEFAULT_DISPATCH_THRESHOLD",
    "DEFAULT_AMBIGUITY_MARGIN",
    "SEED_TRIGGERS",
    "TaskDetection",
    "TaskTriggerRegistry",
    "TriggerEntry",
    "default_registry",
]

#: A single ≥2-token dispatch-grade phrase clears this; weak single tokens do not.
DEFAULT_DISPATCH_THRESHOLD: float = 2.0
#: A tie/near-tie between two capabilities falls below this → clarifying question.
DEFAULT_AMBIGUITY_MARGIN: float = 1.0

_CapabilityKind = Literal["skill", "tool"]
_GuardKind = Literal["requires_url", "requires_filename"]

# Global veto guards (R-21-3 §2): convert "can you draft emails?" and "I don't
# need you to research this" from a dispatch into ordinary chat.
_CAPABILITY_QUESTION = re.compile(
    r"\b(can|could|would|will)\s+you\b|\bare you able to\b|\bhow (would|do) you\b|\bwhat (does|can)\b",  # noqa: E501
    re.IGNORECASE,
)
_NEGATION = re.compile(
    r"\b(don't|do not|never mind|no need)\b|\bwithout\b|\bdon’t\b", re.IGNORECASE
)
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_FILENAME = re.compile(r"\b[\w-]+\.\w{1,5}\b")

# Single-word triggers that are also common nouns. When preceded by a determiner
# ("that research paper") they are nouns, not an imperative verb — skip them.
# Keyword detection cannot do POS; this guard handles the frequent noun case
# (R-21-3 §5 #2) without a tagger. The consent gate is the second safety net.
_NOUN_AMBIGUOUS: frozenset[str] = frozenset({"research", "investigate", "illustrate", "google"})
_DETERMINER = re.compile(
    r"\b(that|this|the|a|an|my|your|his|her|their|its|some|any)\s+$", re.IGNORECASE
)


class TriggerEntry(BaseModel):
    """One capability's trigger phrases (R-21-3 §1). Frozen + ``extra="forbid"``.

    Attributes:
        capability: The declared skill or tool name this entry maps to. Must
            match a name in the persona's allow-set or the entry is inert.
        kind: Whether ``capability`` is a ``"skill"`` or a ``"tool"``.
        phrases: Lowercase trigger phrases, word-boundary matched.
        weight: Per-match weight; ``None`` → the matched phrase's token count
            (longer = more specific).
        dispatch_grade: ``False`` → the entry contributes score but cannot
            trigger a dispatch on its own (e.g. "look up").
        guards: Per-entry guards that must hold for the entry to count
            (``requires_url`` / ``requires_filename``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: str = Field(min_length=1)
    kind: _CapabilityKind
    phrases: tuple[str, ...] = Field(min_length=1)
    weight: float | None = None
    dispatch_grade: bool = True
    guards: tuple[_GuardKind, ...] = ()


class TaskDetection(BaseModel):
    """A detection verdict with provenance (R-21-3 §1). Frozen + ``extra="forbid"``.

    Attributes:
        capability: The winning capability.
        kind: ``"skill"`` or ``"tool"``.
        score: The winning capability's summed weight.
        matched_phrases: The phrases that matched (audit + consent prompt).
        runner_up: The second-place capability, if any (drives the ambiguity
            clarifying question).
        dispatchable: ``True`` for a clean dispatch; ``False`` when the top two
            capabilities are within the ambiguity margin (the orchestrator asks
            a clarifying question instead of dispatching).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: str
    kind: _CapabilityKind
    score: float
    matched_phrases: tuple[str, ...]
    runner_up: str | None = None
    dispatchable: bool = True


def _entry(
    capability: str,
    kind: _CapabilityKind,
    phrases: Sequence[str],
    *,
    dispatch_grade: bool = True,
    guards: Sequence[_GuardKind] = (),
) -> TriggerEntry:
    # A dispatch-grade match alone clears the threshold (weight == threshold);
    # a sub-dispatch match contributes but cannot trigger on its own (R-21-3 §3).
    # Fixed weights (not token-count) so strong single-word verbs ("investigate")
    # dispatch and two single-phrase capabilities tie within the ambiguity margin.
    return TriggerEntry(
        capability=capability,
        kind=kind,
        phrases=tuple(phrases),
        weight=DEFAULT_DISPATCH_THRESHOLD if dispatch_grade else 1.0,
        dispatch_grade=dispatch_grade,
        guards=tuple(guards),
    )


# The 20-entry seed taxonomy (R-21-3 §2). Lowercase, word-boundary matched.
SEED_TRIGGERS: tuple[TriggerEntry, ...] = (
    # Skill — web_research
    _entry("web_research", "skill", ["research", "do research on", "research into"]),
    _entry("web_research", "skill", ["investigate"]),
    _entry("web_research", "skill", ["look into"]),
    _entry(
        "web_research", "skill", ["find out about", "find information on", "find information about"]
    ),
    _entry("web_research", "skill", ["gather sources on", "find sources for"]),
    _entry("web_research", "skill", ["deep dive on", "deep dive into"], dispatch_grade=False),
    # Skill — document_drafting
    _entry("document_drafting", "skill", ["draft a", "draft an", "draft the"]),
    _entry(
        "document_drafting",
        "skill",
        ["write a report", "write a document", "write a memo", "write a summary", "write a letter"],
    ),
    _entry("document_drafting", "skill", ["compose a", "compose an"]),
    _entry(
        "document_drafting", "skill", ["prepare a document", "prepare a brief", "prepare a report"]
    ),
    _entry("document_drafting", "skill", ["write up", "put together a document"]),
    # Tool — web_search
    _entry("web_search", "tool", ["search the web for", "search online for"]),
    _entry("web_search", "tool", ["google"]),
    _entry("web_search", "tool", ["look up"], dispatch_grade=False),
    # Tool — web_fetch
    _entry(
        "web_fetch",
        "tool",
        ["fetch this page", "fetch the url", "open this link"],
        guards=["requires_url"],
    ),
    _entry(
        "web_fetch",
        "tool",
        ["summarize this article", "summarize this page"],
        dispatch_grade=False,
        guards=["requires_url"],
    ),
    # Tool — file_write and file_read
    _entry("file_write", "tool", ["save this to a file", "write to a file", "create a file"]),
    _entry("file_read", "tool", ["read the file", "open the file"], guards=["requires_filename"]),
    # Tool — image_generation
    _entry(
        "image_generation",
        "tool",
        ["generate an image of", "create an image of", "create a picture of"],
    ),
    _entry("image_generation", "tool", ["draw me a", "illustrate", "make a logo"]),
)


class _CompiledEntry:
    """An entry with its phrases compiled to one word-boundary regex alternation."""

    __slots__ = ("entry", "regex")

    def __init__(self, entry: TriggerEntry) -> None:
        self.entry = entry
        alternation = "|".join(re.escape(p) for p in entry.phrases)
        # Both word boundaries: "google" must not match "googled" (R-21-3 §5 #4).
        self.regex = re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)


class _CapScore:
    __slots__ = ("capability", "kind", "score", "phrases", "has_dispatch_grade")

    def __init__(self, capability: str, kind: _CapabilityKind) -> None:
        self.capability = capability
        self.kind = kind
        self.score = 0.0
        self.phrases: list[str] = []
        self.has_dispatch_grade = False


class TaskTriggerRegistry:
    """Compiles trigger entries (filtered to a persona's allow-set) into a detector.

    Construction-time work (R-21-3 §1): keep only entries whose capability is in
    the persona's declared tools/skills, then compile one regex alternation per
    entry. Detection is a pure function over the compiled set.
    """

    def __init__(
        self,
        entries: Sequence[TriggerEntry],
        *,
        allowed_skills: Sequence[str],
        allowed_tools: Sequence[str],
        dispatch_threshold: float = DEFAULT_DISPATCH_THRESHOLD,
        ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN,
    ) -> None:
        skills = set(allowed_skills)
        tools = set(allowed_tools)
        self._compiled: list[_CompiledEntry] = [
            _CompiledEntry(e)
            for e in entries
            if (e.kind == "skill" and e.capability in skills)
            or (e.kind == "tool" and e.capability in tools)
        ]
        self._dispatch_threshold = dispatch_threshold
        self._ambiguity_margin = ambiguity_margin

    def detect(self, message: str) -> TaskDetection | None:
        """Return a task detection for ``message``, or ``None`` (pure; R-21-3 §3).

        ``None`` when: a guard vetoes (capability-question / negation), no
        capability clears the dispatch threshold, or the winner has no
        dispatch-grade match. A returned detection is ``dispatchable=True`` for a
        clean win, or ``dispatchable=False`` (with ``runner_up`` set) when the
        top two are within the ambiguity margin.
        """
        if _CAPABILITY_QUESTION.search(message) or _NEGATION.search(message):
            return None
        has_url = bool(_URL.search(message))
        has_filename = bool(_FILENAME.search(message))

        scores: dict[str, _CapScore] = {}
        for compiled in self._compiled:
            entry = compiled.entry
            if "requires_url" in entry.guards and not has_url:
                continue
            if "requires_filename" in entry.guards and not has_filename:
                continue
            match = compiled.regex.search(message)
            if match is None:
                continue
            span = match.group(0)
            if span.lower() in _NOUN_AMBIGUOUS and _DETERMINER.search(message[: match.start()]):
                continue  # determiner + ambiguous word → noun, not an imperative
            weight = entry.weight if entry.weight is not None else float(len(span.split()))
            cap = scores.setdefault(entry.capability, _CapScore(entry.capability, entry.kind))
            cap.score += weight
            cap.phrases.append(span)
            cap.has_dispatch_grade = cap.has_dispatch_grade or entry.dispatch_grade

        if not scores:
            return None
        ranked = sorted(scores.values(), key=lambda c: c.score, reverse=True)
        top = ranked[0]
        if top.score < self._dispatch_threshold or not top.has_dispatch_grade:
            return None

        runner_up = ranked[1] if len(ranked) > 1 else None
        dispatchable = runner_up is None or (top.score - runner_up.score) >= self._ambiguity_margin
        return TaskDetection(
            capability=top.capability,
            kind=top.kind,
            score=top.score,
            matched_phrases=tuple(top.phrases),
            runner_up=runner_up.capability if runner_up is not None else None,
            dispatchable=dispatchable,
        )


def default_registry(
    persona: Persona, *, extra_entries: Sequence[TriggerEntry] = ()
) -> TaskTriggerRegistry:
    """Build a registry from the seed (+ optional extras) for ``persona``'s allow-set.

    The persona's ``tools`` and ``skills`` lists are the allow-set; extras let a
    persona contribute trigger phrases without touching code (open/closed).
    """
    return TaskTriggerRegistry(
        (*SEED_TRIGGERS, *extra_entries),
        allowed_skills=persona.skills,
        allowed_tools=persona.tools,
    )
