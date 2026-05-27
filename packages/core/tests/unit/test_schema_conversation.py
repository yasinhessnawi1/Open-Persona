"""Tests for ``persona.schema.conversation``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from persona.schema.conversation import Conversation, ConversationMessage
from pydantic import ValidationError

UTC_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)


class TestConversationMessage:
    def test_minimal_message(self) -> None:
        msg = ConversationMessage(role="user", content="hello", created_at=UTC_NOW)
        assert msg.metadata == {}

    def test_role_literal_enforced(self) -> None:
        ConversationMessage(role="assistant", content="hi", created_at=UTC_NOW)
        ConversationMessage(role="system", content="hi", created_at=UTC_NOW)
        ConversationMessage(role="tool", content="hi", created_at=UTC_NOW)
        with pytest.raises(ValidationError):
            ConversationMessage(role="other", content="hi", created_at=UTC_NOW)  # type: ignore[arg-type]

    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValidationError, match="naive"):
            ConversationMessage(
                role="user",
                content="x",
                created_at=datetime(2026, 5, 27, 12, 0, 0),  # noqa: DTZ001
            )

    def test_non_utc_offset_accepted(self) -> None:
        oslo = timezone(timedelta(hours=2))
        msg = ConversationMessage(
            role="user",
            content="x",
            created_at=datetime(2026, 5, 27, 14, 0, 0, tzinfo=oslo),
        )
        # We accept any tz-aware datetime on conversation messages without
        # normalising to UTC — conversations may want to preserve local time
        # for display. (Chunks are normalised; messages are not.)
        assert msg.created_at.tzinfo is not None

    def test_frozen(self) -> None:
        msg = ConversationMessage(role="user", content="x", created_at=UTC_NOW)
        with pytest.raises(ValidationError):
            msg.content = "y"  # type: ignore[misc]

    def test_extra_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            ConversationMessage(
                role="user",
                content="x",
                created_at=UTC_NOW,
                unknown="x",  # type: ignore[call-arg]
            )


class TestConversation:
    def test_construction_defaults(self) -> None:
        conv = Conversation(conversation_id="c1", persona_id="astrid")
        assert conv.messages == []
        assert conv.compacted_summary == ""
        assert conv.compacted_up_to == 0
        assert conv.turn_count == 0

    def test_compacted_up_to_must_be_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            Conversation(conversation_id="c", persona_id="p", compacted_up_to=-1)

    def test_turn_count_reflects_messages(self) -> None:
        msgs = [
            ConversationMessage(role="user", content=str(i), created_at=UTC_NOW) for i in range(3)
        ]
        conv = Conversation(conversation_id="c", persona_id="p", messages=msgs)
        assert conv.turn_count == 3

    def test_mutable_compaction_fields(self) -> None:
        """Conversation is mutable on purpose — compaction state is updated in place."""
        conv = Conversation(conversation_id="c", persona_id="p")
        conv.compacted_summary = "earlier turns folded here"
        conv.compacted_up_to = 5
        assert conv.compacted_summary.startswith("earlier")
        assert conv.compacted_up_to == 5

    def test_extra_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            Conversation(
                conversation_id="c",
                persona_id="p",
                unknown="x",  # type: ignore[call-arg]
            )
