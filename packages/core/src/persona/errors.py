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
    "CreditsExhaustedError",
    "MCPConnectionError",
    "MCPServerUnavailableError",
    "PersonaError",
    "PersonaNotFoundError",
    "PersonaSelfWriteForbiddenError",
    "RuntimeWriteForbiddenError",
    "SandboxViolationError",
    "SchemaVersionMismatchError",
    "SkillManifestError",
    "StoreNotFoundError",
    "ToolExecutionError",
    "ToolNotAllowedError",
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


class ToolExecutionError(PersonaError):
    """Raised when a tool execution fails inside the toolbox."""


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
