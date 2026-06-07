"""Response models for the hosted API (spec 08, T06).

Pydantic v2 models that shape the JSON the API returns + the SSE event payloads.
Clean and explicit so FastAPI's OpenAPI spec (the web app's TS-client source,
spec 09) is well-formed — optional fields are properly nullable, the SSE
payloads serialise via ``model_dump_json`` straight into ``data:`` lines.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic needs it at runtime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AuthoringDraft",
    "ChunkEvent",
    "ClarifyingQuestion",
    "ConversationDetail",
    "ConversationSummary",
    "CreditsResponse",
    "DoneEvent",
    "MessageView",
    "PersonaCapabilities",
    "PersonaDetail",
    "PersonaSummary",
    "RunStatusResponse",
    "ToolCallEvent",
    "ToolResultEvent",
    "ToolSummary",
    "UsageEntry",
]


class _Output(BaseModel):
    """Base for responses: reject unknown fields so we never leak stray data."""

    model_config = ConfigDict(extra="forbid")


# -- personas ---------------------------------------------------------------


class PersonaSummary(_Output):
    """A persona in a list view (no full YAML)."""

    id: str
    name: str
    role: str
    avatar_url: str | None = None
    created_at: datetime
    updated_at: datetime


class PersonaCapabilities(_Output):
    """Deployment-derived capability flags surfaced with the persona detail.

    Hydrated from the runtime :class:`persona_runtime.tier.TierRegistry` so the
    UI can answer "does this persona support image attachments?" BEFORE the
    user attempts to send (Spec 13 fail-loud made visible — Spec F3 §10 #7;
    D-F3-X-no-vision-surface-shape). At v0.1 the answer is deployment-wide:
    every persona under a given deployment shares the same registry, so
    ``vision`` is identical across personas — see D-F3-X-deployment-vs-persona-
    capability-framing. The field's shape survives the v0.2 inflection where
    per-persona tier pins make the answer genuinely per-persona; only the
    hydration source changes (from registry to per-persona lookup).

    Attributes:
        vision: ``True`` iff at least one configured tier resolves to a
            backend whose ``supports_vision`` is ``True``. Read via the
            public :meth:`TierRegistry.supports_vision_for` method
            (D-F3-X-tier-registry-public-contract).
        configured_tiers: Tier names registered on the active deployment
            in insertion order (``("small", "mid", "frontier")`` for the
            typical three-tier deployment). The UI may surface these in a
            disabled-attach tooltip to explain *which* models the deployment
            has configured.
    """

    vision: bool
    configured_tiers: tuple[str, ...]


class PersonaDetail(_Output):
    """A persona's full detail (YAML + metadata).

    The optional :attr:`capabilities` field (D-F3-X-capability-endpoint) is
    additive on top of the Spec 08 / Spec 09 surface: tests + composition
    roots that do not wire a :class:`TierRegistry` (e.g. unit fixtures
    without the runtime) omit the field and the API returns ``None`` so the
    persona-detail surface stays usable without runtime composition.
    """

    id: str
    yaml: str
    schema_version: str
    avatar_url: str | None = None
    capabilities: PersonaCapabilities | None = None
    created_at: datetime
    updated_at: datetime


# -- LLM-assisted authoring (spec 10, §3 / D-10-6) --------------------------


class ClarifyingQuestion(_Output):
    """One suggested question the user can answer to improve a draft persona.

    ``section`` is a free-form hint (expected: identity | self_facts | worldview
    | constraints | tools | skills) — NOT an enum, so a model that names a
    section we don't anticipate doesn't sink the parse.
    """

    section: str
    question: str


class AuthoringDraft(_Output):
    """The draft envelope returned by ``/author`` and ``/author/refine`` (D-10-2).

    A draft is NOT a persona row — the user reviews/refines it, then saves via
    ``POST /v1/personas`` (which creates the row). ``errors`` is populated only
    when validation retries are exhausted (best-effort YAML returned for the form
    to fix, §3.3); ``None`` on success.
    """

    yaml: str
    questions: list[ClarifyingQuestion] = Field(default_factory=list)
    prompt_version: str
    errors: list[str] | None = None


# -- conversations ----------------------------------------------------------


class ConversationSummary(_Output):
    """A conversation in a list view."""

    id: str
    persona_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class MessageView(_Output):
    """A single message in a conversation history."""

    id: str
    role: str
    content: str
    created_at: datetime
    # Opaque connector passthrough (D-08-3); null for web-UI messages.
    channel: dict[str, object] | None = None


class ConversationDetail(_Output):
    """Full conversation history."""

    id: str
    persona_id: str
    title: str
    messages: list[MessageView]
    created_at: datetime
    updated_at: datetime


# -- SSE chat events (§5.2) -------------------------------------------------


class ChunkEvent(_Output):
    """``event: chunk`` — an incremental delta of the assistant's response."""

    delta: str
    is_final: bool = False


class ToolCallEvent(_Output):
    """``event: tool_call`` — the model invoked a tool."""

    tool: str
    args: dict[str, object] = Field(default_factory=dict)


class ToolResultEvent(_Output):
    """``event: tool_result`` — a tool's result (D-03-3: is_error + content)."""

    tool: str
    content: str
    is_error: bool = False


class DoneEvent(_Output):
    """``event: done`` — the terminal event.

    ``format_hints`` (D-08-3) is the connector echo channel: empty ``{}`` from
    the API; connectors populate/interpret it themselves (spec 12).
    """

    usage: dict[str, int] = Field(default_factory=dict)
    tier: str
    format_hints: dict[str, str] = Field(default_factory=dict)


# -- runs (§5.3) ------------------------------------------------------------


class RunStatusResponse(_Output):
    """A run's status + its accumulated steps (JSON-serialised Run/Step)."""

    id: str
    persona_id: str
    task: str
    status: str
    steps: list[dict[str, object]] = Field(default_factory=list)
    output: str | None = None
    error: str | None = None


# -- credits / usage (§5.5) -------------------------------------------------


class CreditsResponse(_Output):
    """The user's current credit balance (stub counter).

    ``low_balance`` is True when the balance is below
    :data:`credits_service.LOW_BALANCE_THRESHOLD` (10 000 by default) — the web
    app uses it to surface the under-limit warning (D-11-12).
    """

    balance: int
    low_balance: bool = False


class UsageEntry(_Output):
    """One usage-log row (per-turn telemetry, paginated)."""

    persona_id: str | None = None
    tier_used: str
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    cost_cents: float
    created_at: datetime


# -- tools / skills (§5.4) --------------------------------------------------


class ToolSummary(_Output):
    """A tool or skill name + description (read-only listing)."""

    name: str
    description: str
