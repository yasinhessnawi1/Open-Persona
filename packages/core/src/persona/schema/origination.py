"""The persona-initiated (originated) message outbound model (Spec C0, T1).

An *originated* message is a message a persona produces with **no preceding user
turn** — "I've finished the report", "I hit an error, how should I proceed?". It
is a first-class part of the conversation and memory (§3, criterion 2), never an
out-of-band notification: :meth:`OriginatedMessage.as_conversation_message`
bridges it to an ``assistant`` :class:`~persona.schema.conversation.ConversationMessage`
carrying the :data:`~persona.schema.conversation.ORIGINATED_METADATA_KEY` marker.

This module is the Layer-1 outbound shape the origination *capability* (D-C0-1)
carries to the delivery layer; the ``MessageDeliverer`` port, the routing, and the
RLS-scoped write into a conversation live in later tasks. The capability is
deliberately trigger-agnostic — the within-runtime caller and (later) the
direction-4 autonomous trigger construct the *same* :class:`OriginatedMessage`
and call the *same* capability (criterion 8); nothing here knows *why* it was
produced.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic needs runtime access

from pydantic import BaseModel, ConfigDict, field_validator

from persona.schema.conversation import ORIGINATED_METADATA_KEY, ConversationMessage

__all__ = [
    "OriginatedMessage",
    "PersonaIdentityTag",
]


class PersonaIdentityTag(BaseModel):
    """The persona identity carried with an originated message through delivery.

    The name-tag the user sees so they can tell *which* persona is speaking
    (criterion 5) — essential on connectors where multiple personas share one
    channel (the C1 name-tag requirement). Distinct from
    :class:`~persona.schema.persona.PersonaIdentity` (the persona's immutable
    identity-store facts): this is the lightweight delivery tag.

    Attributes:
        persona_id: The originating persona's id.
        display_name: The persona's human-facing name, shown to the user.
        visual_ref: An optional reference to the persona's F1 visual identity
            (e.g. an avatar reference), carried where the channel supports it;
            ``None`` where it does not apply.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    persona_id: str
    display_name: str
    visual_ref: str | None = None


class OriginatedMessage(BaseModel):
    """A message a persona originates to its owner, with no preceding user turn.

    The outbound shape the origination capability (D-C0-1) hands to delivery
    routing. Authorisation is the personal model (criterion 9): an originated
    message goes only from a user's own persona to *that* user — ``owner_user_id``
    is the persona's owner, the only valid recipient; the persona-api boundary
    enforces this under RLS (a cross-tenant target raises
    :class:`~persona.errors.OriginationForbiddenError`).

    Attributes:
        persona: The originating persona's identity tag (survives delivery).
        owner_user_id: The recipient — the user who owns the persona.
        content: The message text. v1 is text-only (an originated message is a
            short status/decision utterance); multimodal widening is additive
            later, mirroring ``ConversationMessage.content``.
        conversation_id: The persona's current conversation with the owner that
            this message attaches to (D-C0-3). ``None`` means there is no current
            conversation and origination starts one.
        created_at: UTC-aware datetime the persona produced the message.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    persona: PersonaIdentityTag
    owner_user_id: str
    content: str
    conversation_id: str | None = None
    created_at: datetime

    @field_validator("created_at", mode="after")
    @classmethod
    def _created_at_must_be_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            msg = "naive datetime not allowed on OriginatedMessage.created_at"
            raise ValueError(msg)
        return value

    def as_conversation_message(self) -> ConversationMessage:
        """Bridge to the first-class conversation citizen (D-C0-3, criteria 1, 2).

        Returns an ``assistant`` :class:`ConversationMessage` (the persona *is*
        the assistant — no new role, no ``role``-CHECK widening) carrying the
        originated metadata marker, so the message persists and is remembered the
        same as a reply, differing only in that no user turn preceded it.
        """
        return ConversationMessage(
            role="assistant",
            content=self.content,
            created_at=self.created_at,
            metadata={ORIGINATED_METADATA_KEY: "true"},
        )
