"""Runtime tool-gap detection (spec 26 T10).

Mirror image of Spec 25's ``detect_tool_refusals`` (which flags AVAILABLE tools
the model declined): this detects when the model says it *can't* do something
and a known-tool-catalog tool would enable it, **but the persona's allow-list
lacks that tool**. It then offers one-tap consent via a Spec-21
:class:`ProactiveQuestion` (the 3+1 shape).

Detection is **keyword/heuristic tier-1** (D-26-4): a capability-gap phrase
("I can't", "I don't have …") must co-occur with a catalog tool's keyword. The
catalog (``persona.tools.TOOL_CATALOG``) owns the phrase→tool vocabulary
(D-26-X-known-tool-catalog), so adding a tool extends detection automatically. A
semantic tier-2 is a named fast-follow (D-26-4), not built here.

Pure functions; no I/O. The loop calls :func:`detect_tool_gap` post-generation
and, when it fires, surfaces :func:`build_tool_gap_question` and records the
gap on the TurnLog (T12).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from persona.tools import TOOL_CATALOG
from pydantic import BaseModel, ConfigDict

from persona_runtime.questions import ProactiveQuestion, QuestionOption

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["ToolGapSignal", "build_tool_gap_question", "detect_tool_gap"]

#: Conservative capability-gap phrases. Matched case-insensitively as substrings
#: of the model's output. Kept tight to avoid false positives on ordinary text.
_GAP_PHRASES: tuple[str, ...] = (
    "i don't have",
    "i do not have",
    "i can't",
    "i cannot",
    "i'm not able to",
    "i am not able to",
    "i'm unable to",
    "i am unable to",
    "i don't have access",
    "i do not have access",
    "no access to",
    "don't have a tool",
    "don't have the tool",
    "i lack the",
    "i don't have the ability",
)
_GAP_RE = re.compile("|".join(re.escape(p) for p in _GAP_PHRASES))


class ToolGapSignal(BaseModel):
    """A detected capability gap the persona could close by enabling a tool.

    Attributes:
        tool_name: The catalog tool that would close the gap (not in the
            persona's allow-list).
        description: The tool's authoring-facing description (for the prompt).
        matched_keyword: The catalog keyword that fired (for telemetry/debug).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_name: str
    description: str
    matched_keyword: str


def detect_tool_gap(model_output: str, available_tools: Iterable[str]) -> ToolGapSignal | None:
    """Detect a closeable tool gap in the model's output (D-26-4, tier-1).

    Returns a :class:`ToolGapSignal` when (a) a capability-gap phrase is present
    AND (b) a catalog tool's keyword appears AND (c) that tool is NOT in
    ``available_tools``. Returns ``None`` otherwise. The first catalog match (in
    catalog order) wins — at most one offer per turn. Pure + deterministic.

    Args:
        model_output: The assistant's final turn text. May be empty.
        available_tools: The tools currently available to the persona (e.g.
            ``Toolbox.names()`` — registered ∩ allow-listed).

    Returns:
        The gap signal, or ``None`` if no closeable gap was detected.
    """
    if not model_output:
        return None
    lowered = model_output.lower()
    if not _GAP_RE.search(lowered):
        return None
    available = set(available_tools)
    for entry in TOOL_CATALOG:
        if entry.name in available or not entry.keywords:
            continue
        for keyword in entry.keywords:
            if keyword in lowered:
                return ToolGapSignal(
                    tool_name=entry.name,
                    description=entry.description,
                    matched_keyword=keyword,
                )
    return None


def build_tool_gap_question(signal: ToolGapSignal) -> ProactiveQuestion:
    """Build the Spec-21 3+1 consent question for a detected gap (D-26-5)."""
    # Lower-case the first letter of the description so it reads inline.
    desc = signal.description
    desc_inline = (desc[0].lower() + desc[1:]) if desc else desc
    return ProactiveQuestion(
        question=(
            f"I don't have the `{signal.tool_name}` tool, which would let me "
            f"{desc_inline.rstrip('.')}. Want me to enable it for this persona?"
        ),
        options=(
            QuestionOption(
                label="Enable it and retry",
                description=f"Add `{signal.tool_name}` to this persona and try again.",
            ),
            QuestionOption(
                label="Find another way",
                description="Answer using the tools I already have.",
            ),
            QuestionOption(
                label="Just explain the tool",
                description="Tell me what it does without enabling it.",
            ),
        ),
        allow_free_form=True,
    )
