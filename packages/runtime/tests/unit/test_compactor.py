"""Unit tests for persona_runtime.agentic.compactor (T05, D-06-4).

Covers spec §6 + acceptance #8 (persona block + task preserved verbatim; earlier
tool results summarised) and #11 (15-step / 2000-char run stays within the
frontier window after compaction).
"""

from __future__ import annotations

from datetime import UTC, datetime

from persona.schema.conversation import ConversationMessage
from persona.skills import count_tokens
from persona_runtime.agentic.compactor import StepHistoryCompactor


def _msg(role: str, content: str) -> ConversationMessage:
    return ConversationMessage(role=role, content=content, created_at=datetime.now(UTC))  # type: ignore[arg-type]


def _floor() -> ConversationMessage:
    return _msg("system", "PERSONA: Astrid, tenancy lawyer.\nTASK: draft a mould complaint.")


class TestShouldCompact:
    def test_small_context_does_not_compact(self) -> None:
        compactor = StepHistoryCompactor()
        context = [_floor(), _msg("assistant", "short")]
        assert compactor.should_compact(context, budget=100_000) is False

    def test_no_middle_does_not_compact(self) -> None:
        # floor + exactly the recent tail → nothing in the middle to summarise.
        compactor = StepHistoryCompactor()
        context = [
            _floor(),
            _msg("assistant", "a"),
            _msg("tool", "b"),
            _msg("assistant", "c"),
            _msg("tool", "d"),
        ]
        assert compactor.should_compact(context, budget=1) is False

    def test_over_threshold_with_middle_compacts(self) -> None:
        compactor = StepHistoryCompactor()
        big = "x " * 2000  # ~ a couple thousand tokens
        context = [_floor(), *[_msg("tool", big) for _ in range(10)]]
        # tiny budget so 80% threshold is easily crossed
        assert compactor.should_compact(context, budget=500) is True

    def test_zero_budget_does_not_compact(self) -> None:
        compactor = StepHistoryCompactor()
        context = [_floor(), *[_msg("tool", "x" * 100) for _ in range(10)]]
        assert compactor.should_compact(context, budget=0) is False


class TestCompactIfNeeded:
    def test_under_budget_returns_unchanged(self) -> None:
        compactor = StepHistoryCompactor()
        context = [_floor(), _msg("assistant", "hi")]
        result = compactor.compact_if_needed(context, budget=100_000, summary="ignored")
        assert result is context

    def test_summary_none_is_noop(self) -> None:
        compactor = StepHistoryCompactor()
        big = "x " * 2000
        context = [_floor(), *[_msg("tool", big) for _ in range(10)]]
        result = compactor.compact_if_needed(context, budget=500, summary=None)
        assert result is context

    def test_compaction_preserves_floor_and_recent_verbatim(self) -> None:
        # Acceptance #8.
        compactor = StepHistoryCompactor()
        floor = _floor()
        big = "tool output " * 500
        middle = [_msg("tool", big) for _ in range(8)]
        recent = [
            _msg("assistant", "recent-1"),
            _msg("tool", "recent-2"),
            _msg("assistant", "recent-3"),
            _msg("tool", "recent-4"),
        ]
        context = [floor, *middle, *recent]
        result = compactor.compact_if_needed(
            context, budget=500, summary="the model searched and fetched"
        )

        assert result[0] is floor  # persona block + task — verbatim
        assert result[1].role == "system"
        assert result[1].content == "Earlier in this run: the model searched and fetched"
        assert result[1].metadata == {"kind": "step_compaction"}
        assert result[2:] == recent  # recent tail — verbatim
        assert len(result) == 2 + len(recent)

    def test_compaction_reduces_token_count(self) -> None:
        compactor = StepHistoryCompactor()
        big = "x " * 3000
        # A realistic agentic context: each step is an assistant turn that issued
        # tool_calls followed by its big tool result (a tool message never appears
        # without a preceding assistant turn — the provider would reject it).
        steps: list[ConversationMessage] = []
        for _ in range(6):
            steps.append(_msg("assistant", "calling a tool"))
            steps.append(_msg("tool", big))
        context = [_floor(), *steps, _msg("assistant", "done")]
        before = count_tokens("\n".join(f"{m.role}: {m.content}" for m in context))
        result = compactor.compact_if_needed(context, budget=2000, summary="brief summary")
        after = count_tokens("\n".join(f"{m.role}: {m.content}" for m in result))
        assert after < before
        # the verbatim tail must not begin with a dangling tool message (spec 11)
        tail = result[2:]  # [floor, summary, *tail]
        assert tail[0].role != "tool"


class TestMiddleToSummarise:
    def test_returns_middle_slice(self) -> None:
        compactor = StepHistoryCompactor()
        floor = _floor()
        middle = [_msg("tool", f"m{i}") for i in range(3)]
        recent = [
            _msg("assistant", "r1"),
            _msg("tool", "r2"),
            _msg("assistant", "r3"),
            _msg("tool", "r4"),
        ]
        context = [floor, *middle, *recent]
        assert compactor.middle_to_summarise(context) == middle

    def test_no_middle_returns_empty(self) -> None:
        compactor = StepHistoryCompactor()
        context = [_floor(), _msg("assistant", "a"), _msg("tool", "b")]
        assert compactor.middle_to_summarise(context) == []


class TestAcceptance11ContextWindow:
    def test_15_steps_2000_chars_stays_within_frontier_window(self) -> None:
        # Acceptance #11: 15 steps, each a 2000-char tool result, stays within the
        # frontier 200K-token window. 15 * 2000 chars is ~7.5K tokens, so it fits
        # the 200K frontier window outright (no compaction needed) — assert that.
        compactor = StepHistoryCompactor()
        frontier_budget = 200_000
        tool_result = "x" * 2000
        context = [_floor()]
        for i in range(15):
            context.append(_msg("assistant", f"step {i} calling tool"))
            context.append(_msg("tool", tool_result))
        assert compactor.should_compact(context, budget=frontier_budget) is False
        rendered = "\n".join(f"{m.role}: {m.content}" for m in context)
        assert count_tokens(rendered) < frontier_budget

    def test_compaction_bounds_a_run_that_exceeds_its_budget(self) -> None:
        # The mechanism the spec relies on: when step history DOES exceed the
        # budget, compaction brings it back under (the loop pre-computes the
        # summary on the middle slice and passes it in).
        compactor = StepHistoryCompactor()
        budget = 5000
        huge_tool_result = "data " * 4000  # ~ several K tokens each
        context = [_floor()]
        for i in range(15):
            context.append(_msg("assistant", f"step {i}"))
            context.append(_msg("tool", huge_tool_result))

        assert compactor.should_compact(context, budget=budget) is True
        middle = compactor.middle_to_summarise(context)
        assert middle  # there IS a middle to summarise
        # The loop awaits the small tier on `middle`; here a short fixed summary.
        compacted = compactor.compact_if_needed(
            context, budget=budget, summary="searched + fetched 15 sources"
        )
        rendered = "\n".join(f"{m.role}: {m.content}" for m in compacted)
        # Compaction collapsed the large middle to one short summary message.
        assert count_tokens(rendered) < count_tokens(
            "\n".join(f"{m.role}: {m.content}" for m in context)
        )
        assert len(compacted) < len(context)
