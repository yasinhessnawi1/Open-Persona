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
    "BrokenVersionChainError",
    "PersonaError",
    "PersonaNotFoundError",
    "PersonaSelfWriteForbiddenError",
    "RuntimeWriteForbiddenError",
    "SandboxViolationError",
    "SchemaVersionMismatchError",
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
