"""Inbound identity resolution — the front of the connector flow (Spec C1 T4, C1-D-5).

The first stage of the inbound→route→respond→outbound pipeline and the criteria-7
gate: map a :class:`~persona_connectors.domain.normalise.NormalisedInbound`'s
``(platform, sender_id)`` to the linked Persona-user owner, or — when the identity
is not linked — yield a link-instruction and ZERO access (never another user's
personas).

This is the **pure decision** half (over the T3 ``LinkingService``): it returns a
:class:`ResolvedIdentity` or an :class:`UnlinkedIdentity`. The caller (the
composition root / the flow) applies the actual ``current_user_id`` owner-scope
(via ``ConnectorComposition.owner_scope``) only for a resolved identity, so the
downstream runtime + stores run RLS-scoped to that owner; for an unlinked
identity it sends the instruction and stops — no scope is ever entered.

Owned surface — api-free; uses only the domain ``LinkingService`` + the connector
error hierarchy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from persona_connectors.errors import IdentityNotLinkedError

if TYPE_CHECKING:
    from persona_connectors.domain.linking import LinkingService
    from persona_connectors.domain.normalise import NormalisedInbound

__all__ = [
    "InboundIdentityResolver",
    "ResolutionResult",
    "ResolvedIdentity",
    "UnlinkedIdentity",
    "build_link_instruction",
]


class ResolvedIdentity(BaseModel):
    """A successfully resolved inbound identity — downstream runs scoped to ``owner_id``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_id: str


class UnlinkedIdentity(BaseModel):
    """An unlinked inbound identity — carries ONLY the link-instruction to send back.

    Deliberately has no ``owner_id``: there is no access path to scope. The flow
    sends :attr:`instruction` and stops (zero access).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instruction: str


# The two outcomes the flow branches on (resolved → owner-scope + proceed;
# unlinked → send the instruction + stop).
ResolutionResult = ResolvedIdentity | UnlinkedIdentity


def build_link_instruction(platform: str) -> str:
    """Build the first-contact link-instruction for an unlinked identity (C1-D-5).

    Plain text (cross-platform-renderable — the connector renders per its tier),
    in the product voice: it explains, warmly and clearly, that this platform
    account is not yet linked and how to fix it. The concrete deep-link/handshake
    is the per-platform linking step (a later task); this is the prompt that
    points the user at it.
    """
    return (
        f"I don't recognise this {platform} account yet. To reach your own personas "
        f"here, link this {platform} account from your Open Persona web app settings. "
        f"Once it's linked, just message a persona by name to start."
    )


class InboundIdentityResolver:
    """Resolve an inbound identity to its owner, or to a link-instruction (C1 T4).

    Holds no state beyond the injected :class:`LinkingService` (DI; no globals).
    The resolution read is pre-auth + cross-tenant (the ``LinkingService`` runs it
    over the dispatch engine in production — the A0-worker pattern); this resolver
    only decides the outcome.
    """

    def __init__(self, linking: LinkingService) -> None:
        self._linking = linking

    def resolve(self, inbound: NormalisedInbound) -> ResolutionResult:
        """Resolve ``inbound`` to a :class:`ResolvedIdentity` or :class:`UnlinkedIdentity`.

        Catches the domain :class:`~persona_connectors.errors.IdentityNotLinkedError`
        (the LinkingService's zero-access signal) and converts it into the
        link-instruction outcome — so the flow never has to special-case the
        not-linked path beyond "send the instruction".
        """
        try:
            owner_id = self._linking.resolve_owner(
                platform=inbound.platform, platform_identity=inbound.sender_id
            )
        except IdentityNotLinkedError:
            return UnlinkedIdentity(instruction=build_link_instruction(inbound.platform))
        return ResolvedIdentity(owner_id=owner_id)
