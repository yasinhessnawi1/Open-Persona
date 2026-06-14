"""LLM-assisted persona authoring (spec 10, T03, §3.3 / §4 / D-10-2, D-10-3).

Turns a natural-language description into a **draft** persona envelope
(:class:`AuthoringDraft`) — YAML + clarifying questions + the prompt version —
WITHOUT creating a persona row. The user reviews/refines the draft, then saves
via ``POST /v1/personas`` (which creates the row).

The retry-with-error-feedback loop here is the *model-agnosticism mechanism*
(D-10-3): the prompt raises compliance probability, but this loop is what makes
the contract hold regardless of model — it feeds Pydantic's validation errors
back to the model and re-asks once. The backend is injected (the route resolves
it from the TierRegistry), so this service is decoupled from provider wiring and
testable with a scripted backend.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import yaml
from persona.schema.conversation import ConversationMessage
from persona.schema.persona import Persona
from persona.tools import TOOL_CATALOG, known_tool_names
from pydantic import ValidationError

from persona_api.schemas.responses import AuthoringDraft, ToolRecommendation
from persona_api.services.authoring_parse import split_response
from persona_api.services.authoring_prompt import (
    AUTHORING_PROMPT_VERSION,
    QUESTIONS_MARKER,
    build_authoring_prompt,
    build_refinement_prompt,
)

if TYPE_CHECKING:
    from persona.backends import ChatBackend

__all__ = [
    "RECOMMENDER_PROMPT_VERSION",
    "generate_authoring_draft",
    "recommend_tools_for_persona",
    "refine_authoring_draft",
]

#: Tool-recommender prompt version (spec 26 T09). Bump when the rubric changes.
RECOMMENDER_PROMPT_VERSION = "v1"
#: Hard cap on returned recommendations (D-26-X-recommender-output-mechanism).
_MAX_RECOMMENDATIONS = 10
#: Confidence floor; weaker "just in case" tools are dropped.
_CONFIDENCE_FLOOR = 0.5


def _validate_yaml(yaml_text: str) -> list[str]:
    """Validate a draft YAML against the v1.0 schema; return error strings ([] = valid).

    Uses placeholder id/owner (the create endpoint assigns the real ones). Both
    YAML-parse failures and Pydantic ``extra="forbid"`` / type failures surface
    as human-readable strings the retry feeds back to the model.
    """
    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        return [f"invalid YAML: {str(exc)[:200]}"]
    if not isinstance(raw, dict):
        return [f"top-level YAML must be a mapping, got {type(raw).__name__}"]
    raw = dict(raw)
    raw.setdefault("persona_id", "draft")
    raw.setdefault("owner_id", "draft")
    try:
        Persona.model_validate(raw)
    except ValidationError as exc:
        return [
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors(include_url=False)
        ]
    return []


def _retry_feedback(errors: list[str]) -> str:
    """The follow-up message that feeds validation errors back to the model (§3.3)."""
    joined = "\n".join(f"- {e}" for e in errors)
    return (
        "The YAML you produced has the following validation errors:\n"
        f"{joined}\n"
        "Please fix them and return the corrected persona in the same format "
        f"(the YAML, then a line with only {QUESTIONS_MARKER}, then the JSON "
        "questions array). Do not add any field that is not in the schema."
    )


async def _generate(backend: ChatBackend, messages: list[ConversationMessage]) -> AuthoringDraft:
    """Run the chat → parse → validate → retry-once loop; return a draft envelope.

    On first-attempt validity, returns immediately. On failure, feeds the errors
    back and re-asks once (§3.3). If the retry also fails, returns the best-effort
    YAML with the errors attached (the structured form fixes them) — never raises.
    """
    response = await backend.chat(messages, temperature=0.0)
    yaml_text, questions = split_response(response.content)
    errors = _validate_yaml(yaml_text)
    if not errors:
        return AuthoringDraft(
            yaml=yaml_text, questions=questions, prompt_version=AUTHORING_PROMPT_VERSION
        )

    now = datetime.now(UTC)
    retry_messages = [
        *messages,
        ConversationMessage(role="assistant", content=response.content, created_at=now),
        ConversationMessage(role="user", content=_retry_feedback(errors), created_at=now),
    ]
    retry = await backend.chat(retry_messages, temperature=0.0)
    yaml_text2, questions2 = split_response(retry.content)
    errors2 = _validate_yaml(yaml_text2)
    if not errors2:
        return AuthoringDraft(
            yaml=yaml_text2, questions=questions2, prompt_version=AUTHORING_PROMPT_VERSION
        )
    # Retry exhausted: hand back the best-effort YAML + the errors (§3.3) so the
    # structured form can fix them. Never raise — a draft is for review.
    return AuthoringDraft(
        yaml=yaml_text2,
        questions=questions2,
        prompt_version=AUTHORING_PROMPT_VERSION,
        errors=errors2,
    )


async def generate_authoring_draft(
    backend: ChatBackend,
    description: str,
    available_tools: list[str],
    available_skills: list[str],
) -> AuthoringDraft:
    """Generate a draft persona from a description (no row created; D-10-2).

    Args:
        backend: The authoring-tier chat backend (injected; D-10-1).
        description: The user's natural-language persona description.
        available_tools: Tool names to inject so the model only suggests real tools (S10-3).
        available_skills: Skill names to inject.

    Returns:
        An :class:`AuthoringDraft` (validated YAML + 2-4 questions + prompt version,
        or best-effort YAML + errors if the retry was exhausted).
    """
    messages = build_authoring_prompt(description, available_tools, available_skills)
    return await _generate(backend, messages)


async def refine_authoring_draft(
    backend: ChatBackend,
    current_yaml: str,
    question: str,
    answer: str,
    available_tools: list[str],
    available_skills: list[str],
) -> AuthoringDraft:
    """Refine a draft by applying the user's answer to a clarifying question (§4).

    Same parse/validate/retry path as :func:`generate_authoring_draft`.

    Args:
        backend: The authoring-tier chat backend.
        current_yaml: The draft YAML being refined.
        question: The clarifying question the user answered.
        answer: The user's answer.
        available_tools: Tool names to inject.
        available_skills: Skill names to inject.

    Returns:
        An updated :class:`AuthoringDraft`.
    """
    messages = build_refinement_prompt(
        current_yaml, question, answer, available_tools, available_skills
    )
    return await _generate(backend, messages)


# -- tool recommender (spec 26 T09) -----------------------------------------


def _catalog_block() -> str:
    """Render the known-tool catalog as the recommender's candidate list."""
    return "\n".join(f"- {e.name}: {e.description}" for e in TOOL_CATALOG)


def _recommender_messages(description: str) -> list[ConversationMessage]:
    """Build the rubric-based recommender prompt (D-26-X-recommender-output-mechanism).

    The full catalog is enumerated inline; the rubric biases toward precision so
    the model returns a small advertised set — which the tool-count literature
    shows preserves downstream selection accuracy (research R-26-1). The output
    contract is a bare JSON array, validated + catalog-filtered after the call.
    """
    now = datetime.now(UTC)
    system = (
        "You recommend a SMALL set of tools for an AI persona, given its "
        "description. Choose only from this catalog:\n\n"
        f"{_catalog_block()}\n\n"
        "Rules:\n"
        "- Recommend a tool ONLY if the persona's identity, role, or tasks imply "
        "a RECURRING need for it. Prefer precision over recall.\n"
        "- Never recommend a tool 'just in case'. A focused persona needs 3-8 "
        "tools; never more than 10.\n"
        "- A smaller, well-matched set makes the persona MORE accurate at "
        "picking the right tool at runtime — extra tools hurt.\n"
        "- Use ONLY tool names from the catalog above. Do not invent names.\n\n"
        'Output ONLY a JSON array of objects, each {"tool_name": str, '
        '"rationale": str (one line), "confidence": number 0-1}. No prose, no '
        "code fences. Example:\n"
        '[{"tool_name": "web_search", "rationale": "Looks up current case law.", '
        '"confidence": 0.9}]'
    )
    return [
        ConversationMessage(role="system", content=system, created_at=now),
        ConversationMessage(role="user", content=description, created_at=now),
    ]


def _extract_json_array(text: str) -> list[object] | None:
    """Extract the first JSON array from ``text`` (tolerates surrounding prose)."""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, list) else None


def _parse_recommendations(raw: list[object]) -> list[ToolRecommendation]:
    """Validate raw items → ToolRecommendation, dropping invalid/hallucinated/weak.

    Applies the post-hoc guards (D-26-X-recommender-output-mechanism):
    catalog-membership filter (drop hallucinated names), confidence floor, dedup
    keeping the highest confidence per tool, sort descending, cap at the max.
    """
    catalog = known_tool_names()
    best: dict[str, ToolRecommendation] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            rec = ToolRecommendation.model_validate(item)
        except ValidationError:
            continue
        if rec.tool_name not in catalog or rec.confidence < _CONFIDENCE_FLOOR:
            continue
        existing = best.get(rec.tool_name)
        if existing is None or rec.confidence > existing.confidence:
            best[rec.tool_name] = rec
    ranked = sorted(best.values(), key=lambda r: r.confidence, reverse=True)
    return ranked[:_MAX_RECOMMENDATIONS]


async def recommend_tools_for_persona(
    backend: ChatBackend,
    description: str,
) -> list[ToolRecommendation]:
    """Recommend a ranked tool subset for a persona description (spec 26 T09).

    Uses a single mid-tier call (the route injects the mid backend, D-26-2) with
    a rubric prompt enumerating the known-tool catalog, then validates +
    catalog-filters the result in code. Retries once if the model returns no
    parseable JSON array. Never raises — an unparseable second attempt yields an
    empty list (the authoring form simply shows no recommendations).

    Args:
        backend: The mid-tier chat backend (injected; D-26-2).
        description: The user's natural-language persona description.

    Returns:
        Up to 10 :class:`ToolRecommendation`s, highest-confidence first, each
        with a catalog-valid ``tool_name`` and ``confidence >= 0.5``.
    """
    messages = _recommender_messages(description)
    response = await backend.chat(messages, temperature=0.0)
    raw = _extract_json_array(response.content)
    if raw is None:
        now = datetime.now(UTC)
        retry_messages = [
            *messages,
            ConversationMessage(role="assistant", content=response.content, created_at=now),
            ConversationMessage(
                role="user",
                content="Return ONLY a JSON array as specified — no prose, no code fences.",
                created_at=now,
            ),
        ]
        retry = await backend.chat(retry_messages, temperature=0.0)
        raw = _extract_json_array(retry.content)
        if raw is None:
            return []
    return _parse_recommendations(raw)
