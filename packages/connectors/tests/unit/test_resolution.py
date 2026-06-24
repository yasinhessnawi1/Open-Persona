"""Inbound identity resolution — the criteria-7 gate at the front of the flow (C1 T4).

An inbound message resolves ``(platform, sender_id)`` to the linked Persona user
(downstream then runs owner-scoped under RLS), or — if the identity is not linked
— yields a link-instruction and ZERO access (never another user's personas). The
decision is pure (over the T3 ``LinkingService``); the actual ``current_user_id``
owner-scope is applied by the caller (composition). Owned surface — api-free.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from persona_connectors.domain import NormalisedInbound
from persona_connectors.domain.linking import LinkingService, LinkRecord
from persona_connectors.domain.resolution import (
    InboundIdentityResolver,
    ResolvedIdentity,
    UnlinkedIdentity,
    build_link_instruction,
)

_NOW = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)
_TTL = timedelta(minutes=15)


class _BoundStore:
    """A minimal LinkStore holding one pre-bound active identity (resolve-only fake)."""

    def __init__(self, *, owner: str, platform: str, identity: str) -> None:
        self._record = LinkRecord(
            platform=platform,
            platform_identity=identity,
            owner_id=owner,
            status="active",
            linked_at=_NOW,
        )

    def get_active_identity(self, *, platform: str, platform_identity: str) -> LinkRecord | None:
        rec = self._record
        if rec.platform == platform and rec.platform_identity == platform_identity:
            return rec
        return None

    # Unused by resolve(); present so the object satisfies the LinkStore port shape.
    def create_token(self, token: object) -> None: ...
    def get_token_by_hash(self, token_hash: str) -> None:  # noqa: ARG002 — port stub
        return None

    def consume_token(self, token_hash: str, *, now: datetime) -> None: ...
    def bind_identity(self, **kwargs: object) -> None: ...
    def revoke_identity(self, **kwargs: object) -> None: ...


def _inbound(platform: str, sender_id: str) -> NormalisedInbound:
    return NormalisedInbound(
        platform=platform,
        sender_id=sender_id,
        conversation_key=sender_id,
        message_id="m-1",
        text="hei",
        received_at=_NOW,
    )


def _linked_service(*, owner: str, platform: str, identity: str) -> LinkingService:
    return LinkingService(_BoundStore(owner=owner, platform=platform, identity=identity))


def test_linked_identity_resolves_to_its_owner() -> None:
    """A linked inbound resolves to its owner (downstream runs owner-scoped)."""
    svc = _linked_service(owner="u1", platform="telegram", identity="tg-1")
    resolver = InboundIdentityResolver(svc)
    result = resolver.resolve(_inbound("telegram", "tg-1"))
    assert isinstance(result, ResolvedIdentity)
    assert result.owner_id == "u1"


def test_unlinked_identity_yields_link_instruction_not_access() -> None:
    """An unlinked inbound yields a link-instruction (zero access — never an owner)."""
    svc = _linked_service(owner="u1", platform="telegram", identity="tg-1")
    resolver = InboundIdentityResolver(svc)
    result = resolver.resolve(_inbound("telegram", "stranger"))
    assert isinstance(result, UnlinkedIdentity)
    assert result.instruction  # a non-empty link-instruction to send back
    # The unlinked result carries NO owner id — there is no access path to scope.
    assert not hasattr(result, "owner_id")


def test_resolution_is_keyed_on_platform_and_sender() -> None:
    """Same sender id on a DIFFERENT platform is a different (unlinked) identity."""
    svc = _linked_service(owner="u1", platform="telegram", identity="shared-id")
    resolver = InboundIdentityResolver(svc)
    assert isinstance(resolver.resolve(_inbound("telegram", "shared-id")), ResolvedIdentity)
    assert isinstance(resolver.resolve(_inbound("discord", "shared-id")), UnlinkedIdentity)


def test_build_link_instruction_is_nonempty_and_names_the_platform() -> None:
    """The instruction is plain text (cross-platform-renderable), F1 voice, platform-named."""
    text = build_link_instruction("telegram")
    assert text
    assert "telegram" in text.lower()
    # Plain text — no platform markup baked in (the connector renders per its tier).
    assert "<b>" not in text
    assert "*" not in text
