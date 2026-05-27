"""Tests for ``persona.history`` — spec §8 #5."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.history import ConversationHistoryManager
from persona.schema.conversation import Conversation, ConversationMessage

UTC_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)


def _msg(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=text, created_at=UTC_NOW)


def _conv(n_turns: int) -> Conversation:
    return Conversation(
        conversation_id="c1",
        persona_id="astrid",
        messages=[_msg(f"m{i}") for i in range(n_turns)],
    )


def _counter_summariser() -> tuple[list[int], object]:
    """Returns a (call_count_list, summariser) pair for assertion clarity."""
    calls: list[int] = []

    def summariser(messages: list[ConversationMessage]) -> str:
        calls.append(len(messages))
        joined = " ".join(m.content for m in messages)
        return f"summary({joined})"

    return calls, summariser


class TestConstructor:
    def test_default_values(self) -> None:
        mgr = ConversationHistoryManager()
        assert mgr.compact_every == 10
        assert mgr.keep_recent == 5

    def test_compact_every_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="compact_every"):
            ConversationHistoryManager(compact_every=0)

    def test_keep_recent_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="keep_recent"):
            ConversationHistoryManager(keep_recent=0)

    def test_keep_recent_must_be_less_than_compact_every(self) -> None:
        with pytest.raises(ValueError, match="ever compacted"):
            ConversationHistoryManager(compact_every=5, keep_recent=5)
        with pytest.raises(ValueError, match="ever compacted"):
            ConversationHistoryManager(compact_every=5, keep_recent=10)


class TestBelowThreshold:
    def test_returns_full_transcript(self) -> None:
        mgr = ConversationHistoryManager(compact_every=10, keep_recent=5)
        conv = _conv(7)
        calls, summariser = _counter_summariser()
        out = mgr.manage(conv, summariser)  # type: ignore[arg-type]
        assert len(out) == 7
        assert calls == []  # summariser not invoked
        assert conv.compacted_up_to == 0
        assert conv.compacted_summary == ""

    def test_exact_threshold_returns_full_transcript(self) -> None:
        mgr = ConversationHistoryManager(compact_every=10, keep_recent=5)
        conv = _conv(10)
        calls, summariser = _counter_summariser()
        out = mgr.manage(conv, summariser)  # type: ignore[arg-type]
        assert len(out) == 10
        assert calls == []


class TestAboveThreshold:
    def test_compacts_and_returns_summary_plus_recent(self) -> None:
        mgr = ConversationHistoryManager(compact_every=10, keep_recent=5)
        conv = _conv(12)
        calls, summariser = _counter_summariser()
        out = mgr.manage(conv, summariser)  # type: ignore[arg-type]

        # Boundary = 12 - 5 = 7. New range covered: messages[0:7] = 7 messages.
        assert calls == [7]
        # Output: 1 system summary + 5 verbatim recents.
        assert len(out) == 6
        assert out[0].role == "system"
        assert "summary(" in out[0].content
        assert out[-1].content == "m11"
        assert conv.compacted_up_to == 7
        assert conv.compacted_summary  # non-empty

    def test_subsequent_call_at_same_boundary_does_not_re_summarise(self) -> None:
        mgr = ConversationHistoryManager(compact_every=10, keep_recent=5)
        conv = _conv(12)
        calls, summariser = _counter_summariser()
        mgr.manage(conv, summariser)  # type: ignore[arg-type]
        # Second call without adding messages should reuse the existing summary.
        mgr.manage(conv, summariser)  # type: ignore[arg-type]
        assert calls == [7]  # only the first call invoked summariser

    def test_growing_conversation_advances_compaction(self) -> None:
        mgr = ConversationHistoryManager(compact_every=10, keep_recent=5)
        conv = _conv(12)
        calls, summariser = _counter_summariser()
        mgr.manage(conv, summariser)  # type: ignore[arg-type]
        # Add 3 more messages → turn 15. New boundary = 10. New range to
        # summarise: [7, 10) = 3 messages.
        conv.messages.extend([_msg(f"m{12 + i}") for i in range(3)])
        mgr.manage(conv, summariser)  # type: ignore[arg-type]
        assert calls == [7, 3]
        assert conv.compacted_up_to == 10

    def test_jump_from_below_threshold_to_far_above_compacts_in_one_call(self) -> None:
        mgr = ConversationHistoryManager(compact_every=10, keep_recent=5)
        conv = _conv(22)
        calls, summariser = _counter_summariser()
        out = mgr.manage(conv, summariser)  # type: ignore[arg-type]
        # Boundary = 22 - 5 = 17. Compact messages[0:17] in one call.
        assert calls == [17]
        # Output: 1 summary + 5 recents.
        assert len(out) == 6
        assert conv.compacted_up_to == 17


class TestSummaryAccumulation:
    def test_summary_is_concatenated_across_compactions(self) -> None:
        mgr = ConversationHistoryManager(compact_every=10, keep_recent=5)
        conv = _conv(12)
        _, summariser = _counter_summariser()
        mgr.manage(conv, summariser)  # type: ignore[arg-type]
        first_summary = conv.compacted_summary

        conv.messages.extend([_msg(f"extra_{i}") for i in range(5)])
        mgr.manage(conv, summariser)  # type: ignore[arg-type]
        # The summary should contain the old segment plus a new one.
        assert first_summary in conv.compacted_summary
        assert conv.compacted_summary != first_summary

    def test_empty_summariser_output_does_not_advance_summary_text(self) -> None:
        mgr = ConversationHistoryManager(compact_every=10, keep_recent=5)
        conv = _conv(12)

        def empty(_messages: list[ConversationMessage]) -> str:
            return ""

        mgr.manage(conv, empty)
        # Boundary still moved forward; summary is still empty.
        assert conv.compacted_up_to == 7
        assert conv.compacted_summary == ""


class TestPropagation:
    def test_summariser_exceptions_propagate(self) -> None:
        mgr = ConversationHistoryManager(compact_every=10, keep_recent=5)
        conv = _conv(12)

        def broken(_messages: list[ConversationMessage]) -> str:
            raise RuntimeError("summariser broke")

        with pytest.raises(RuntimeError, match="summariser broke"):
            mgr.manage(conv, broken)
        # Nothing was updated.
        assert conv.compacted_up_to == 0
        assert conv.compacted_summary == ""

    def test_full_messages_list_is_never_truncated(self) -> None:
        mgr = ConversationHistoryManager(compact_every=10, keep_recent=5)
        conv = _conv(12)
        _, summariser = _counter_summariser()
        mgr.manage(conv, summariser)  # type: ignore[arg-type]
        # Full transcript persists — episodic store would still see all 12.
        assert len(conv.messages) == 12
