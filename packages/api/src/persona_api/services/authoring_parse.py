"""Parse an authoring model's response into (YAML, questions) (spec 10, T02, §3.3).

The ``---QUESTIONS---`` marker is fragile by nature (the model may omit it, use
a variant, or wrap the YAML in fences), so this parser is deliberately lenient
and **never raises**: a malformed questions block degrades to "no questions"
rather than sinking an otherwise-good YAML. Together with the validate-retry
loop (T03), this is what makes the contract hold regardless of model (D-10-3).

Pure, deterministic, model-free — fully unit-tested in CI.
"""

from __future__ import annotations

import json
import re

from persona_api.schemas.responses import ClarifyingQuestion

__all__ = ["split_response", "strip_fences"]

# Markers tried in priority order. The canonical one is trusted unconditionally;
# the text variants are only honoured when a JSON array plausibly follows (so a
# stray "questions:" inside the YAML doesn't trigger a bad split).
_CANONICAL_MARKER = "---questions---"
_VARIANT_MARKERS = ("clarifying questions:", "questions:")

_JSON_ARRAY = re.compile(r"\[.*\]", re.DOTALL)


def strip_fences(text: str) -> str:
    """Remove a leading/trailing ``` fence if the model wrapped its output."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        body = lines[1:]  # drop the opening ``` / ```yaml line
        if body and body[-1].strip().startswith("```"):
            body = body[:-1]
        return "\n".join(body).strip()
    return stripped


def _find_marker(text: str) -> tuple[int, int] | None:
    """Return ``(start, length)`` of the questions marker, or None.

    Canonical ``---QUESTIONS---`` is trusted anywhere; the text variants only
    count when a ``[`` follows (the questions are a JSON array).
    """
    low = text.lower()
    i = low.find(_CANONICAL_MARKER)
    if i != -1:
        return (i, len(_CANONICAL_MARKER))
    for variant in _VARIANT_MARKERS:
        i = low.find(variant)
        if i != -1 and "[" in text[i:]:
            return (i, len(variant))
    return None


def _parse_questions(tail: str) -> list[ClarifyingQuestion]:
    """Extract the first JSON array of ``{section, question}`` objects; [] on any failure."""
    match = _JSON_ARRAY.search(tail)
    if match is None:
        return []
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    questions: list[ClarifyingQuestion] = []
    for item in raw:
        if isinstance(item, dict) and item.get("question"):
            questions.append(
                ClarifyingQuestion(
                    section=str(item.get("section", "")),
                    question=str(item["question"]),
                )
            )
    return questions


def split_response(text: str) -> tuple[str, list[ClarifyingQuestion]]:
    """Split a model response into (yaml_block, clarifying_questions).

    Tries ``---QUESTIONS---``, then ``Clarifying questions:`` / ``Questions:``
    (only when a JSON array follows), then falls back to treating the whole
    response as YAML with no questions. Never raises — YAML validity is checked
    downstream (T03); a bad questions block degrades to no questions.

    Args:
        text: The raw model response.

    Returns:
        ``(yaml_text, questions)`` — ``yaml_text`` is fence-stripped; ``questions``
        may be empty.
    """
    found = _find_marker(text)
    if found is None:
        return strip_fences(text), []
    start, length = found
    head = text[:start]
    tail = text[start + length :]
    return strip_fences(head), _parse_questions(tail)
