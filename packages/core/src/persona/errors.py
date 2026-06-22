"""Domain exceptions for persona-core.

Every exception raised from persona-core domain logic is a subclass of
:class:`PersonaError`. Provider-specific exceptions (chromadb, httpx, etc.)
are caught at the adapter boundary and re-raised as domain exceptions so
callers depend on our types rather than on a transitive dependency.

Every domain exception accepts a ``context`` dictionary that is included in
``str(self)``. This makes log messages structured without forcing callers to
build a message-template every time — the exception carries the data it needs
to be useful in a log line.

See ``docs/specs/spec_01/decisions.md`` D-01-12 for the structured-context
rationale and ``docs/specs/spec_01/spec_01_core.md`` §11.7.
"""

from __future__ import annotations

__all__ = [
    "AuditWriteError",
    "AuthenticationError",
    "BrokenVersionChainError",
    "CalculatorError",
    "CreditsExhaustedError",
    "DuplicateJobTypeError",
    "JobStateError",
    "MCPBuiltinServerError",
    "MCPConnectionError",
    "MCPServerUnavailableError",
    "PermanentJobError",
    "PersonaError",
    "PersonaNotFoundError",
    "PersonaSelfWriteForbiddenError",
    "RuntimeWriteForbiddenError",
    "SandboxViolationError",
    "SchemaVersionMismatchError",
    "SkillArgumentValidationError",
    "SkillCompositionDepthError",
    "SkillCycleError",
    "SkillManifestError",
    "SkillNameCollisionError",
    "StoreNotFoundError",
    "ToolExecutionError",
    "ToolNotAllowedError",
    "UnknownDocumentFormatError",
    "UnknownDocumentTemplateError",
    "UnknownJobTypeError",
]


class PersonaError(Exception):
    """Base exception for all persona-core errors.

    Args:
        message: Human-readable error description.
        context: Structured context that gets appended to ``str(self)`` so
            log records carry the data callers need to debug. Keys and values
            are stringified at format time.
    """

    def __init__(
        self,
        message: str = "",
        *,
        context: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, str] = dict(context) if context else {}

    def __str__(self) -> str:
        if not self.context:
            return self.message
        ctx = " ".join(f"{k}={v}" for k, v in self.context.items())
        if self.message:
            return f"{self.message} [{ctx}]"
        return f"[{ctx}]"


class SchemaVersionMismatchError(PersonaError):
    """Raised when a persona YAML's ``schema_version`` is not supported.

    The message includes a hint about migration paths.
    """


class PersonaNotFoundError(PersonaError):
    """Raised when a persona cannot be located (by id or by path)."""


class RuntimeWriteForbiddenError(PersonaError):
    """Raised when a write violates a store's per-source policy.

    Common contexts: identity-store writes from any source, self_facts /
    worldview writes without ``force=True``. See spec 01 §5.2.
    """


class PersonaSelfWriteForbiddenError(RuntimeWriteForbiddenError):
    """Raised specifically when a ``persona_self`` write fails policy.

    Subclass of :class:`RuntimeWriteForbiddenError` so callers can either
    catch the broader error or disambiguate the persona-self path (e.g., to
    log a more specific reason or skip a self-update retry loop).
    """


class StoreNotFoundError(PersonaError):
    """Raised when a store kind is requested that the registry does not know."""


class BrokenVersionChainError(PersonaError):
    """Raised when a version chain is malformed.

    Common causes: duplicate version numbers within one ``logical_id``,
    a ``superseded_by`` pointer that does not match the next version's id,
    or a rollback target that does not exist.
    """


class AuditWriteError(PersonaError):
    """Raised when the audit logger fails to record an event.

    The store does not swallow this — failing to audit a mutation is a
    correctness issue, not an operational one.
    """


class ToolNotAllowedError(PersonaError):
    """Raised when a tool call targets a tool not in the persona's allow-list."""


class CalculatorError(PersonaError):
    """Raised when the ``calculator`` tool rejects an expression.

    Spec 26 (D-26-X-calculator-ast-scope). The ``calculator`` tool evaluates a
    strict whitelist of arithmetic AST nodes (no ``eval``); this exception
    surfaces a disallowed node type (e.g. attribute access, a non-whitelisted
    function/name), a parse error, or a DoS-guard violation (expression too
    long, exponent/AST too large, factorial argument too big). The ``@tool``
    decorator boundary turns it into a ``ToolResult(is_error=True)`` so the
    model sees a recoverable error rather than a raised exception. ``context``
    carries the offending fragment + the guard that tripped.
    """


class ToolExecutionError(PersonaError):
    """Raised when a tool execution fails inside the toolbox."""


class UnknownDocumentFormatError(PersonaError):
    """Raised when ``document_generation`` is asked for a format with no handler.

    Carries ``context={"format": ..., "available": ...}`` so the caller (and
    logs) name the rejected format and the registered alternatives. Reading-B
    dispatch error (D-24-1); the format catalogue lives in
    :mod:`persona.skills.document_generation.registry`.
    """


class UnknownDocumentTemplateError(PersonaError):
    """Raised when ``document_generation`` is asked for an unregistered template.

    Carries ``context={"template": ..., "available": ...}``. Templates are
    bundled Markdown files registered in
    :mod:`persona.skills.document_generation.registry` (D-24-2).
    """


class SandboxViolationError(PersonaError):
    """Raised when a file operation attempts to escape its sandbox directory."""


class MCPConnectionError(PersonaError):
    """Raised when an MCP server cannot be reached in fail-loud mode.

    Spec 03 §7.3: the Toolbox auto-load path catches connection errors and
    logs a warning instead (graceful degradation per D-03-20), but explicit
    callers that invoke ``MCPClient.connect(strict=True)`` get this exception.
    """


class MCPServerUnavailableError(PersonaError):
    """Raised when a registered MCP server is unreachable in strict mode.

    Subclass of :class:`PersonaError` (flat hierarchy per D-03-1). Used by
    :class:`persona.tools.mcp.client.MCPClient` when ``strict=True`` and the
    underlying transport fails.
    """


class MCPBuiltinServerError(PersonaError):
    """Raised for a built-in MCP server problem (Spec 27, flat per D-03-1).

    Covers an unknown built-in server name at the serve entrypoint and the
    launcher's spawn/health-probe failures. Distinct from
    :class:`MCPServerUnavailableError` (a *configured external* server is
    unreachable) — this is about a *built-in* server Persona ships and launches.
    """


class MCPUrlNotAllowedError(PersonaError):
    """Raised when a user-supplied MCP server URL fails the SSRF guard (Spec 30, D-30-4).

    The bring-your-own MCP surface lets a user supply an outbound URL the runtime
    connects to — a server-side request forgery surface (the live MCP-SSRF CVE
    class). The guard (:mod:`persona.tools.mcp.ssrf`) rejects a non-``https``
    scheme, a missing/unresolvable host, or a host resolving to a
    private/loopback/link-local/cloud-metadata/CGNAT/reserved address (incl.
    IPv4-mapped + NAT64-embedded forms). ``context["reason"]`` carries the
    *category* only — never the resolved internal IP (no reconnaissance aid).
    Enforced at add/test time AND on every live connect (resolve-then-pin) so a
    rebind between check and use cannot slip through.
    """


class AuthenticationError(PersonaError):
    """Raised when a request has no valid bearer token (→ 401 at the API edge).

    Relocated from ``persona_api.errors`` to persona-core at spec V1 T03
    (D-V1-X-jwt-verifier-extraction) so persona-voice can raise it from the
    extracted :func:`persona.auth.jwt_verifier.make_jwt_verifier` without taking
    a persona-api dependency. ``persona_api.errors`` re-exports for back-compat.
    """


class CreditsExhaustedError(PersonaError):
    """Raised when a user's credit balance cannot cover an operation (→ 402).

    Relocated from ``persona_api.errors`` to persona-core at Spec 19 L6c
    (D-19-X-credits-service-domain-relocation) so persona-voice can raise it
    from :func:`persona.credits.service.require_credits` without taking a
    persona-api dependency (voice surface is latency-critical per R-V1-1 — no
    HTTP/RPC hop). ``persona_api.errors`` re-exports for back-compat.
    """


class SkillManifestError(PersonaError):
    """Raised when a ``SKILL.md`` file is malformed.

    Spec 04 (D-04-3). Surfaces from
    :func:`persona.skills._frontmatter.parse_skill_markdown` for: missing
    opening/closing front-matter delimiter, malformed YAML in the front
    matter, or a non-mapping YAML value where the front-matter dict was
    expected.

    The ``context`` always carries ``{"path": "<absolute path>"}`` and may
    carry ``{"reason": "<truncated yaml.YAMLError detail>"}`` when the
    underlying problem was a YAML parse failure. The scanner's per-skill
    envelope (D-04-4) catches this exception and logs a structured warning;
    the persona keeps loading with the offending skill omitted.
    """


class SkillArgumentValidationError(PersonaError):
    """Raised when ``use_skill`` arguments fail a skill's ``parameters`` schema.

    Spec 24 (D-24-8). The skill's declared JSON Schema is compiled to a frozen
    ``extra="forbid"`` Pydantic model and the call arguments are validated
    strictly at activation time. ``context`` carries
    ``{"skill": ..., "errors": ...}``. The ``use_skill`` tool catches this and
    returns ``ToolResult(is_error=True)`` so the model can self-correct.
    """


class SkillNameCollisionError(PersonaError):
    """Raised when the ``skills.toml`` catalog has a name clash (D-24-6).

    Spec 24. A collection name that duplicates a skill id is ambiguous under the
    uniform ``kind:ref`` addressing scheme and is rejected at catalog load
    (fail-loud, per R-24-1 — unlike Semantic Kernel's silent last-write-wins).
    ``context`` carries ``{"name": ...}``.
    """


class SkillCycleError(PersonaError):
    """Raised when a ``use_skill`` activation would revisit a skill already in
    the active composition chain (A→B→A).

    Spec 24 (D-24-4). Cycle detection is a visited-set of skill names along the
    active chain; the check runs **before** the depth check so a cycle is
    diagnosed as a cycle, not a depth overflow. ``context`` carries
    ``{"requested": ..., "chain": "A→B"}``. The runtime intercept catches this
    and informs the model with a system message rather than failing the turn.
    """


class SkillCompositionDepthError(PersonaError):
    """Raised when a ``use_skill`` activation would exceed the composition depth cap.

    Spec 24 (D-24-4). The cap (``MAX_SKILL_COMPOSITION_DEPTH`` = 3) bounds skill
    chaining (research→draft→format) without enabling runaway. ``context``
    carries ``{"requested": ..., "chain": ..., "max_depth": ...}``. The runtime
    intercept catches this and informs the model rather than failing the turn.
    """


class InvalidAutonomyLevelError(PersonaError):
    """Raised when an autonomy value is not one of the three supported levels.

    Spec 21 (D-21-1 / D-21-11). Surfaces from
    :func:`persona.autonomy.resolve_autonomy` when a persona_self autonomy
    chain head stores a value outside ``{"cautious", "balanced", "decisive"}``
    (a corrupted or hand-edited chain). Fail-loud rather than silently falling
    back to the YAML default — a malformed learned-autonomy value is a data
    integrity problem the caller must see. ``context`` carries the offending
    value, the ``logical_id``, and the ``persona_id``.
    """


class AutonomyCooldownError(PersonaError):
    """Raised when a ``persona_self`` autonomy update is attempted within cooldown.

    Spec 21 (D-21-4). The learner rate-limits self-revision to at most once
    per session and once per UTC day; a second attempt inside either window
    raises this rather than appending a churn version. ``context`` carries the
    ``persona_id``, the cooldown window that tripped (``session`` | ``day``),
    and the head version's ``written_at`` timestamp.
    """


class JobStateError(PersonaError):
    """Raised when an illegal job state transition is attempted.

    Spec A0 (D-A0-2). The durable job state machine
    (``queued → claimed → running → succeeded | failed | dead``) rejects any
    move not in its transition table — e.g. resurrecting a terminal job or
    claiming a job that is already running. ``context`` carries ``from`` and
    ``to`` so the log line names the rejected edge.
    """


class DuplicateJobTypeError(PersonaError):
    """Raised when a job type is registered twice in a :class:`JobRegistry`.

    Spec A0 (D-A0-2). The registry is Toolbox-style and explicit; a duplicate
    registration would let a second runtime silently shadow a handler, so it
    fails loud at composition time. ``context`` carries the offending ``type``.
    """


class UnknownJobTypeError(PersonaError):
    """Raised when an unregistered job type is resolved from a :class:`JobRegistry`.

    Spec A0 (D-A0-2). ``context`` carries the requested ``type`` and the
    ``known`` registered types so the miss is debuggable.
    """


class PermanentJobError(PersonaError):
    """Raised by a job handler to signal a NON-retryable (permanent) failure.

    Spec A0 (T6). The durable queue is at-least-once: a handler that raises an
    ordinary exception is retried with backoff and dead-letters at exhaustion.
    A handler raises this when the failure will never succeed on retry (malformed
    input, a permanent provider rejection) — the executor moves the job straight
    to the terminal ``failed`` state, no retries. ``context`` carries the cause.
    """
