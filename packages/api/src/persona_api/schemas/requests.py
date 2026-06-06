"""Request models for the hosted API (spec 08, T06).

Frozen Pydantic v2, ``extra="forbid"`` on every input boundary (fail-fast on
unexpected fields). The OpenAPI spec FastAPI derives from these is the contract
the web app's TypeScript client (spec 09) is generated from — keep them clean.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AuthorPersonaRequest",
    "ChannelContext",
    "CreateConversationRequest",
    "CreatePersonaRequest",
    "ImageRef",
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


# Defined as a sibling Pydantic v2 frozen model on the API request surface
# (NOT imported from ``persona_api.services.image_service.ImageRef``): the
# image-service dataclass is the internal upload-return type; this Pydantic
# model is the external request-body shape — matching the rest of the
# request-model conventions in this file (frozen, ``extra="forbid"``,
# OpenAPI-derivable).
class ImageRef(_Input):
    """Image reference carried on a chat message (spec 13, D-13-X-now option c).

    Refers to a previously-uploaded image in the persona's workspace (Spec 03).
    Image bytes live exactly once in the workspace; the chat body and the
    persisted ``messages`` row carry only ``workspace_path`` + ``media_type``
    so storage scales with reference count, not with image bytes.

    Attributes:
        workspace_path: Workspace-relative path returned by the uploads route
            (``uploads/<ref>.<ext>``). Resolved against
            ``workspace_root/owner_id/persona_id`` at backend send time.
        media_type: One of the four supported image MIME types per D-13-3:
            ``image/png``, ``image/jpeg``, ``image/webp``, ``image/gif``.
            Any other value is rejected at validation time.
    """

    workspace_path: str = Field(min_length=1)
    media_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"]


class PostMessageRequest(_Input):
    """Send a user message; the response streams over SSE (§5.2).

    ``channel`` is the optional connector passthrough (D-08-3) — null for the
    web UI. The runtime ignores it in v0.1; the API just stores it on the
    message row and echoes ``format_hints`` on the ``done`` event.

    ``images`` is the optional spec-13 multimodal extension (D-13-X-now option
    c, D-13-5): up to 4 :class:`ImageRef` per message. ``None`` (the default)
    keeps the text-only path byte-for-byte unchanged. An empty list is
    equivalent to ``None`` semantically but rejected as a validation error so
    callers don't accidentally send ``images=[]`` and skip the cap check; pass
    ``None`` or omit the field.

    The cap is enforced via :class:`Field`'s built-in ``min_length`` /
    ``max_length`` (D-13-5) so the failure surfaces as a structured
    ``too_long`` / ``too_short`` Pydantic v2 error — JSON-serialisable through
    the API's ``_request_422`` handler in :mod:`persona_api.errors` (a custom
    ``field_validator`` would attach a raw :class:`ValueError` to ``ctx`` and
    break the response body's ``json.dumps``).
    """

    content: str = Field(min_length=1)
    channel: ChannelContext | None = None
    images: list[ImageRef] | None = Field(default=None, min_length=1, max_length=4)


class StartRunRequest(_Input):
    """Start an agentic run for a task (§5.3)."""

    task: str = Field(min_length=1)


class RespondToRunRequest(_Input):
    """Answer an ask-user question raised by a running agentic loop (§5.3)."""

    answer: str
