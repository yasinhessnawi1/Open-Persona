"""Unit tests for persona_voice.model.VoiceHistoryCompactor (spec V5 T6; D-V5-3).

The discipline (criterion 7): the live turn uses a fast view that never calls the
summariser and never advances the compaction boundary; the slow summarise runs
only in the background ``compact`` step.
"""

# ruff: noqa: ARG001, ARG002 — fakes with intentionally unused arguments.

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.history import ConversationHistoryManager
from persona.schema.conversation import Conversation, ConversationMessage
from persona_voice.model import VoiceHistoryCompactor, make_small_tier_summariser


def _msg(i: int) -> ConversationMessage:
    return ConversationMessage(
        role="user" if i % 2 == 0 else "assistant",
        content=f"turn {i}",
        created_at=datetime.now(UTC),
    )


def _conv(n: int, *, compacted_up_to: int = 0, summary: str = "") -> Conversation:
    return Conversation(
        conversation_id="c1",
        persona_id="astrid",
        messages=[_msg(i) for i in range(n)],
        compacted_up_to=compacted_up_to,
        compacted_summary=summary,
    )


def _compactor() -> VoiceHistoryCompactor:
    return VoiceHistoryCompactor(ConversationHistoryManager(compact_every=10, keep_recent=5))


class TestLiveHistory:
    def test_short_conversation_returns_all_verbatim(self) -> None:
        conv = _conv(6)
        assert _compactor().live_history(conv) == conv.messages

    def test_long_conversation_without_summary_returns_untrimmed_tail(self) -> None:
        conv = _conv(16)  # > compact_every, but nothing summarised yet
        history = _compactor().live_history(conv)
        # No summary message yet; everything from compacted_up_to (0) is verbatim.
        assert all(m.metadata.get("kind") != "compacted_summary" for m in history)
        assert len(history) == 16

    def test_after_summary_returns_summary_plus_tail(self) -> None:
        conv = _conv(16, compacted_up_to=11, summary="earlier stuff")
        history = _compactor().live_history(conv)
        assert history[0].metadata.get("kind") == "compacted_summary"
        assert "earlier stuff" in history[0].content
        assert history[1:] == conv.messages[11:]

    def test_live_history_never_mutates_compaction_state(self) -> None:
        conv = _conv(16)
        _compactor().live_history(conv)
        assert conv.compacted_up_to == 0  # the live view never advances the boundary
        assert conv.compacted_summary == ""  # nor calls the summariser


class TestIsCompactionDue:
    def test_short_conversation_not_due(self) -> None:
        assert _compactor().is_compaction_due(_conv(6)) is False

    def test_long_conversation_due(self) -> None:
        assert _compactor().is_compaction_due(_conv(16)) is True

    def test_already_compacted_not_due(self) -> None:
        # boundary = 16 - 5 = 11; compacted_up_to already 11 → not due.
        assert _compactor().is_compaction_due(_conv(16, compacted_up_to=11)) is False


class TestCompact:
    @pytest.mark.asyncio
    async def test_compaction_advances_state_using_summariser(self) -> None:
        conv = _conv(16)
        seen: list[int] = []

        async def summariser(messages: list[ConversationMessage]) -> str:
            seen.append(len(messages))
            return "SUMMARY"

        ran = await _compactor().compact(conv, summariser)

        assert ran is True
        assert seen == [11]  # messages[0:11] handed to the slow summariser
        assert conv.compacted_up_to == 11
        assert "SUMMARY" in conv.compacted_summary

    @pytest.mark.asyncio
    async def test_compaction_noop_when_not_due_never_calls_summariser(self) -> None:
        conv = _conv(6)
        called = False

        async def summariser(messages: list[ConversationMessage]) -> str:
            nonlocal called
            called = True
            return "x"

        ran = await _compactor().compact(conv, summariser)

        assert ran is False
        assert called is False


class TestSmallTierSummariser:
    @pytest.mark.asyncio
    async def test_builds_summariser_calling_small_tier(self) -> None:
        class _Resp:
            content = "  a concise summary  "

        class _Backend:
            async def chat(self, messages: object) -> _Resp:
                return _Resp()

        class _Registry:
            def get(self, tier: str) -> _Backend:
                assert tier == "small"  # summaries run on the boilerplate tier
                return _Backend()

        summariser = make_small_tier_summariser(_Registry())  # type: ignore[arg-type]
        result = await summariser([_msg(0), _msg(1)])
        assert result == "a concise summary"
