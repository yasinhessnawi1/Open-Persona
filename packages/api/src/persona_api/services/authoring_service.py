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

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import yaml
from persona.schema.conversation import ConversationMessage
from persona.schema.persona import Persona
from pydantic import ValidationError

from persona_api.schemas.responses import AuthoringDraft
from persona_api.services.authoring_parse import split_response
from persona_api.services.authoring_prompt import (
    AUTHORING_PROMPT_VERSION,
    QUESTIONS_MARKER,
    build_authoring_prompt,
    build_refinement_prompt,
)

if TYPE_CHECKING:
    from persona.backends import ChatBackend

__all__ = ["generate_authoring_draft", "refine_authoring_draft"]


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
