"""Self-facts store — near-immutable; force-gated runtime writes.

Per architecture §4.3 and D-01-5:
- ``user`` writes accepted (the owner can always edit).
- ``system`` writes require ``force=True``.
- ``persona_self`` writes require ``force=True`` AND ``confidence >= 0.8``
  AND a non-empty reason.
"""

from __future__ import annotations

from typing import ClassVar

from persona.schema.chunks import WriteSource
from persona.stores.base import TypedStore
from persona.stores.policy import (
    PersonaSelfRequirement,
    PolicyDecision,
    PolicyRule,
    PolicyTable,
)

__all__ = ["SelfFactsStore"]


class SelfFactsStore(TypedStore):
    """Facts the persona holds about itself."""

    STORE_KIND: ClassVar[str] = "self_facts"
    _POLICY: ClassVar[PolicyTable] = {
        WriteSource.SYSTEM: PolicyRule(decision=PolicyDecision.FORCE_ONLY),
        WriteSource.USER: PolicyRule(decision=PolicyDecision.ACCEPT),
        WriteSource.PERSONA_SELF: PolicyRule(
            decision=PolicyDecision.FORCE_ONLY,
            persona_self_requirement=PersonaSelfRequirement.CONFIDENCE_THRESHOLD,
            persona_self_confidence_threshold=0.8,
            requires_reason=True,
        ),
    }
