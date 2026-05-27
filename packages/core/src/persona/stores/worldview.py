"""Worldview store — force-gated; persona_self writes need an epistemic tag."""

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

__all__ = ["WorldviewStore"]


class WorldviewStore(TypedStore):
    """Claims the persona holds about the world."""

    STORE_KIND: ClassVar[str] = "worldview"
    _POLICY: ClassVar[PolicyTable] = {
        WriteSource.SYSTEM: PolicyRule(decision=PolicyDecision.FORCE_ONLY),
        WriteSource.USER: PolicyRule(decision=PolicyDecision.ACCEPT),
        WriteSource.PERSONA_SELF: PolicyRule(
            decision=PolicyDecision.FORCE_ONLY,
            persona_self_requirement=PersonaSelfRequirement.EPISTEMIC_TAG_SET,
            requires_reason=True,
        ),
    }
