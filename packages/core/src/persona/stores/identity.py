"""Identity store — immutable at runtime, no versioning, no rollback.

Identity changes require editing the persona YAML and reloading via the
registry. Every runtime write is rejected regardless of source or ``force``.
"""

from __future__ import annotations

from typing import ClassVar

from persona.schema.chunks import WriteSource
from persona.stores.base import TypedStore
from persona.stores.policy import PolicyDecision, PolicyRule, PolicyTable

__all__ = ["IdentityStore"]


class IdentityStore(TypedStore):
    """Always retrieved in full; never modified at runtime."""

    STORE_KIND: ClassVar[str] = "identity"
    SUPPORTS_VERSIONING: ClassVar[bool] = False
    _POLICY: ClassVar[PolicyTable] = {
        WriteSource.SYSTEM: PolicyRule(decision=PolicyDecision.REJECT),
        WriteSource.USER: PolicyRule(decision=PolicyDecision.REJECT),
        WriteSource.PERSONA_SELF: PolicyRule(decision=PolicyDecision.REJECT),
    }
