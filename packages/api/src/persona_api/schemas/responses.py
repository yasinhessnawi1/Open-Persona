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
    "ArtifactItem",
    "ArtifactListResponse",
    "ArtifactMetadataView",
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
    "ToolRecommendation",
    "ToolRecommendationResponse",
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
    # Spec 21 T09 (D-21-7): tri-state auto-dispatch consent surfaced to the
    # settings UI. None = never asked / revoked-to-ask, True = granted,
    # False = declined. Additive — omitted defaults to None on legacy rows.
    consent_to_auto_dispatch: bool | None = None
    consent_updated_at: datetime | None = None
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


class ToolRecommendation(_Output):
    """One recommended capability for a persona (spec 26 T09 / spec 27 T10).

    Spec 27 realises the D-26-10 unification: the same shape now carries a
    provider tag so built-in tools, skills, and MCP servers rank together. The
    ``provider`` field defaults to ``"builtin"`` so the Spec-26 shape (and its
    callers/tests) stay a forward-compatible strict subset.

    Attributes:
        tool_name: The capability name — a built-in tool name from
            ``persona.tools.TOOL_CATALOG``, a skill id, or an ``mcp:<server>``
            reference. Hallucinated names are filtered out post-hoc.
        rationale: One-line reason the capability fits this persona.
        confidence: Recommender confidence in [0, 1]; entries below the floor
            are dropped before return.
        provider: Where the capability comes from — ``"builtin"`` (tool),
            ``"skill"``, ``"mcp:builtin"`` (default-enabled MCP server), or
            ``"mcp:optional"`` (opt-in / BYO MCP server). The UI groups by
            provider but ranks across all (spec 27 §2.3 / D-27-13).
    """

    tool_name: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    provider: str = "builtin"


class ToolRecommendationResponse(_Output):
    """The ranked tool-recommendation list returned by ``/personas/recommend-tools``."""

    recommendations: list[ToolRecommendation] = Field(default_factory=list)
    prompt_version: str


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
    # Spec 30 T01 (D-30-1): the call's source badge — ``builtin`` / ``skill`` /
    # ``mcp:builtin`` / ``mcp:optional``. Optional for OpenAPI/back-compat parity
    # with the additive wire field; absent on pre-spec-30 frames.
    kind: str | None = None


class ToolResultEvent(_Output):
    """``event: tool_result`` — a tool's result (D-03-3: is_error + content)."""

    tool: str
    content: str
    is_error: bool = False
    # Spec 30 T01 (D-30-1): the call's source badge (see ToolCallEvent.kind).
    kind: str | None = None


class RoutingSummary(_Output):
    """Spec 31 (D-31-1) — concise model-decision summary on the ``done`` event.

    Additive; present only on intelligent-routing turns. The raw score vector is
    NOT here — it stays on the JSONL TurnLog. The web templates the localized
    "why" phrase from these structured/enum fields (``dominant_factor`` is the
    single highest-weighted axis the chosen model won on).
    """

    chosen_model: str
    dominant_factor: str | None = None
    model_fallback_engaged: bool = False
    model_fallback_reason: str | None = None


class BudgetSnapshot(_Output):
    """Spec 31 (D-31-2) — per-session budget snapshot for the budget indicator.

    Additive; present only when intelligent routing is on and a cap is set.
    ``session_spent_cents`` includes the just-completed turn (read post-turn).
    Caps are omitted when unset; ``max_cents_per_day`` is surfaced when set so
    the UI can show 23's configured-but-deferred fail-loud honestly.
    """

    session_spent_cents: float
    max_cents_per_turn: float | None = None
    max_cents_per_session: float | None = None
    max_cents_per_day: float | None = None


class DoneEvent(_Output):
    """``event: done`` — the terminal event.

    ``format_hints`` (D-08-3) is the connector echo channel: empty ``{}`` from
    the API; connectors populate/interpret it themselves (spec 12).
    """

    usage: dict[str, int] = Field(default_factory=dict)
    tier: str
    format_hints: dict[str, str] = Field(default_factory=dict)
    # Spec 31 — additive, SEPARATE routing (D-31-1) + budget (D-31-2) fields.
    # Both omitted on rule-based turns / when no cap is set (back-compat).
    routing: RoutingSummary | None = None
    budget: BudgetSnapshot | None = None


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


# -- artifacts (Spec F5 D-F5-1) ---------------------------------------------


class ArtifactMetadataView(_Output):
    """Sidecar metadata surfaced through the artifact list endpoint.

    Mirrors ``services.artifact_metadata.WorkspaceArtifactMetadata`` at the
    API surface. Kept as a distinct response model (rather than re-exporting
    the service shape) so the OpenAPI schema is self-contained and the
    web client gets stable types.
    """

    source: str
    type: str
    producing_spec: str
    conversation_id: str | None
    created_at: datetime
    original_name: str | None


class ArtifactItem(_Output):
    """A single workspace artifact in the F5 list view.

    The ``ref`` is the workspace-relative path the existing
    ``GET /v1/personas/{id}/uploads/{ref}`` route already knows how to
    serve — F5 reuses that route for downloads + inline rendering.
    """

    ref: str
    size_bytes: int
    media_type: str
    metadata: ArtifactMetadataView | None = None


class ArtifactListResponse(_Output):
    """Paginated artifact-list response for D-F5-1.

    ``total`` is the post-filter count; ``items`` is the window of size
    ``limit`` starting at ``offset``. The client computes ``hasMore`` from
    ``offset + items.length < total``.
    """

    total: int
    limit: int
    offset: int
    items: list[ArtifactItem]


class MCPServerDetail(_Output):
    """A bring-your-own MCP server as returned to its owner (spec 30, D-30-3).

    The credential is NEVER included — only ``has_credential`` (whether one is
    stored). ``discovered_tools`` is the cached eager-discovery result (D-30-5),
    ``None`` until a successful test-connection.
    """

    id: str
    name: str
    url: str
    auth_method: str
    enabled: bool
    has_credential: bool
    discovered_tools: list[str] | None = None
    created_at: datetime
    updated_at: datetime


class MCPServerTestResult(_Output):
    """Outcome of a BYO-MCP test-connection (spec 30, D-30-5).

    ``ok`` true → ``tools`` lists the discovered tool names (cached on the row).
    ``ok`` false → ``error`` is a short, non-sensitive reason category.
    """

    ok: bool
    tools: list[str] = Field(default_factory=list)
    error: str | None = None


class MCPCatalogServer(_Output):
    """A built-in MCP server in the management catalog (spec 30 T11).

    A persona enables a server by adding ``mcp:<name>`` to its ``tools``
    allow-list. ``provider`` is the recommender tag (``mcp:builtin`` /
    ``mcp:optional``); ``required_env`` lists env vars an operator must set.
    """

    name: str
    description: str
    provider: str
    default_enabled: bool
    required_env: list[str] = Field(default_factory=list)
