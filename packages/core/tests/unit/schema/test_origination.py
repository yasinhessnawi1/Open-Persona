"""Tests for ``persona.schema.origination`` — the persona-initiated (originated)
message outbound model + the persona identity tag (Spec C0, T1).

An originated message is a message a persona produces with NO preceding user
turn. These types are the Layer-1 outbound shape the origination capability
(D-C0-1) carries; the delivery port + routing live in later tasks. The identity
tag (D-C0-X / criterion 5) is what survives delivery so the user can tell which
persona is speaking.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.schema.conversation import ORIGINATED_METADATA_KEY, ConversationMessage
from persona.schema.origination import OriginatedMessage, PersonaIdentityTag
from pydantic import ValidationError

UTC_NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)


class TestPersonaIdentityTag:
    def test_minimal_tag_visual_ref_defaults_none(self) -> None:
        tag = PersonaIdentityTag(persona_id="astrid", display_name="Astrid")
        assert tag.persona_id == "astrid"
        assert tag.display_name == "Astrid"
        assert tag.visual_ref is None

    def test_tag_carries_visual_ref_when_supplied(self) -> None:
        tag = PersonaIdentityTag(
            persona_id="astrid", display_name="Astrid", visual_ref="avatar://astrid.png"
        )
        assert tag.visual_ref == "avatar://astrid.png"

    def test_tag_is_frozen(self) -> None:
        tag = PersonaIdentityTag(persona_id="astrid", display_name="Astrid")
        with pytest.raises(ValidationError):
            tag.display_name = "Other"  # type: ignore[misc]

    def test_tag_forbids_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            PersonaIdentityTag(  # type: ignore[call-arg]
                persona_id="astrid", display_name="Astrid", colour="blue"
            )


class TestOriginatedMessage:
    def _tag(self) -> PersonaIdentityTag:
        return PersonaIdentityTag(persona_id="astrid", display_name="Astrid")

    def test_minimal_originated_message(self) -> None:
        msg = OriginatedMessage(
            persona=self._tag(),
            owner_user_id="user_1",
            content="I've finished the report you asked for.",
            created_at=UTC_NOW,
        )
        assert msg.owner_user_id == "user_1"
        assert msg.content.startswith("I've finished")
        assert msg.conversation_id is None  # None → origination starts one (D-C0-3)
        assert msg.persona.display_name == "Astrid"

    def test_conversation_id_attaches_to_existing_conversation(self) -> None:
        msg = OriginatedMessage(
            persona=self._tag(),
            owner_user_id="user_1",
            content="done",
            conversation_id="conv_42",
            created_at=UTC_NOW,
        )
        assert msg.conversation_id == "conv_42"

    def test_is_frozen(self) -> None:
        msg = OriginatedMessage(
            persona=self._tag(), owner_user_id="user_1", content="x", created_at=UTC_NOW
        )
        with pytest.raises(ValidationError):
            msg.content = "y"  # type: ignore[misc]

    def test_forbids_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            OriginatedMessage(  # type: ignore[call-arg]
                persona=self._tag(),
                owner_user_id="user_1",
                content="x",
                created_at=UTC_NOW,
                channel="telegram",
            )

    def test_naive_created_at_rejected(self) -> None:
        with pytest.raises(ValidationError, match="naive datetime"):
            OriginatedMessage(
                persona=self._tag(),
                owner_user_id="user_1",
                content="x",
                created_at=datetime(2026, 6, 22, 12, 0, 0),  # noqa: DTZ001 — testing rejection
            )


class TestAsConversationMessage:
    """The bridge to the first-class conversation citizen (D-C0-3 / criterion 1, 2).

    An originated message persists as an ``assistant`` row carrying the
    ``originated`` metadata marker — NOT a new role, NOT a parallel stream.
    """

    def _msg(self) -> OriginatedMessage:
        return OriginatedMessage(
            persona=PersonaIdentityTag(persona_id="astrid", display_name="Astrid"),
            owner_user_id="user_1",
            content="I hit an error — how should I proceed?",
            created_at=UTC_NOW,
        )

    def test_role_is_assistant_not_a_new_role(self) -> None:
        cm = self._msg().as_conversation_message()
        assert isinstance(cm, ConversationMessage)
        assert cm.role == "assistant"  # the persona IS the assistant (no role-CHECK widening)

    def test_carries_originated_marker(self) -> None:
        cm = self._msg().as_conversation_message()
        assert cm.metadata[ORIGINATED_METADATA_KEY] == "true"
        assert cm.is_originated is True

    def test_content_and_timestamp_preserved(self) -> None:
        cm = self._msg().as_conversation_message()
        assert cm.content == "I hit an error — how should I proceed?"
        assert cm.created_at == UTC_NOW

    def test_does_not_set_proactive_question_marker(self) -> None:
        """``originated`` and ``proactive_question`` are distinct concepts (D-21-6
        is a solicited-turn question; origination has no preceding user turn).
        T1 must never co-stamp them."""
        cm = self._msg().as_conversation_message()
        assert "proactive_question" not in cm.metadata
