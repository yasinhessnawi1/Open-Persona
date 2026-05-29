"""Request models for the hosted API (spec 08, T06).

Frozen Pydantic v2, ``extra="forbid"`` on every input boundary (fail-fast on
unexpected fields). The OpenAPI spec FastAPI derives from these is the contract
the web app's TypeScript client (spec 09) is generated from — keep them clean.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AuthorPersonaRequest",
    "ChannelContext",
    "CreateConversationRequest",
    "CreatePersonaRequest",
    "PostMessageRequest",
    "RefinePersonaRequest",
    "RespondToRunRequest",
    "StartRunRequest",
    "UpdatePersonaRequest",
]


class _Input(BaseModel):
    """Base for request bodies: frozen, reject unknown fields (fail-fast)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ChannelContext(_Input):
    """Opaque connector context passed through the chat endpoint (D-08-3).

    The API stores this on the message row and never interprets it — ``platform``
    is a free-form string, NEVER an enum the API branches on. All connector logic
    lives in the future spec-12 connectors. Null/absent is the web-UI case.
    """

    platform: str
    platform_user_id: str | None = None
    platform_chat_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class CreatePersonaRequest(_Input):
    """Create a persona from a YAML document (validated against the v1.0 schema).

    ``avatar_url`` is an optional presentation field (not part of the YAML
    schema) — the persona-list / chat-header visual identity.
    """

    yaml: str
    avatar_url: str | None = None


class UpdatePersonaRequest(_Input):
    """Replace a persona's YAML (re-validated against the v1.0 schema)."""

    yaml: str
    avatar_url: str | None = None


class AuthorPersonaRequest(_Input):
    """LLM-assisted authoring from a natural-language description (§5.1, §6.3)."""

    description: str = Field(min_length=1, max_length=4000)


class RefinePersonaRequest(_Input):
    """Refine a draft persona by answering a clarifying question (spec 10, §4 / D-10-2).

    Stateless: ``round`` is the count of refinements already applied (the UI owns
    the counter); the server rejects ``round > 3`` as the backstop on the
    3-round cap (D-10-5).
    """

    current_yaml: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    round: int = Field(default=0, ge=0)


class CreateConversationRequest(_Input):
    """Start a new conversation against a persona."""

    title: str = ""


class PostMessageRequest(_Input):
    """Send a user message; the response streams over SSE (§5.2).

    ``channel`` is the optional connector passthrough (D-08-3) — null for the
    web UI. The runtime ignores it in v0.1; the API just stores it on the
    message row and echoes ``format_hints`` on the ``done`` event.
    """

    content: str = Field(min_length=1)
    channel: ChannelContext | None = None


class StartRunRequest(_Input):
    """Start an agentic run for a task (§5.3)."""

    task: str = Field(min_length=1)


class RespondToRunRequest(_Input):
    """Answer an ask-user question raised by a running agentic loop (§5.3)."""

    answer: str
