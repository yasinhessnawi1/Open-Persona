"""The connector framework's OWNED surface — import-decoupled from persona-api.

Everything C1 owns that is NOT reused from persona-api lives here: the
normalisation contracts + render ladder (T2), the ``Connector`` protocol (T2),
and — landing in later tasks — the per-persona parallel-conversation model,
persona name-parsing, and the account-linking lifecycle. The reversibility
guarantee (C1-D-1) is enforced structurally: **no module under this package may
import persona_api** — a future extract-to-core is then a dependency swap, not a
reshape. The import-guard test (`test_owned_surface_decoupled.py`) locks it.

These modules MAY reuse persona-core (``persona.*``) — a lower layer that is
itself api-free (e.g. C0's ``PersonaIdentityTag`` / ``DeliveryResult``).
"""

from __future__ import annotations

from persona_connectors.domain.addressing import (
    Addressed,
    AddressingResult,
    Ambiguous,
    NoName,
    parse_addressed_persona,
)
from persona_connectors.domain.boundaries import is_idle_expired
from persona_connectors.domain.conversation_model import (
    ConversationStateStore,
    FlipPlan,
    ForegroundResult,
    NoOp,
    Switch,
    decide_foreground,
)
from persona_connectors.domain.normalise import (
    Capabilities,
    NormalisedInbound,
    NormalisedOutbound,
    RenderTier,
    plain_name_prefix,
    render_tier,
)
from persona_connectors.domain.protocol import Connector
from persona_connectors.domain.resolution import (
    InboundIdentityResolver,
    ResolutionResult,
    ResolvedIdentity,
    UnlinkedIdentity,
    build_link_instruction,
)

__all__ = [
    "Addressed",
    "AddressingResult",
    "Ambiguous",
    "Capabilities",
    "Connector",
    "ConversationStateStore",
    "FlipPlan",
    "ForegroundResult",
    "InboundIdentityResolver",
    "NoName",
    "NoOp",
    "NormalisedInbound",
    "NormalisedOutbound",
    "RenderTier",
    "ResolutionResult",
    "ResolvedIdentity",
    "Switch",
    "UnlinkedIdentity",
    "build_link_instruction",
    "decide_foreground",
    "is_idle_expired",
    "parse_addressed_persona",
    "plain_name_prefix",
    "render_tier",
]
