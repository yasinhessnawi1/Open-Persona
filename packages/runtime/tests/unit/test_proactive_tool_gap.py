"""Tests for runtime tool-gap detection (spec 26 T10)."""

from __future__ import annotations

from persona_runtime.proactive_tool_gap import (
    ToolGapSignal,
    build_tool_gap_question,
    detect_tool_gap,
)
from persona_runtime.questions import PROACTIVE_QUESTION_OPTION_COUNT


class TestDetectToolGap:
    def test_gap_phrase_plus_unavailable_tool_keyword_fires(self) -> None:
        signal = detect_tool_gap(
            "I can't calculate that precisely in my head.",
            available_tools=["web_search"],
        )
        assert signal is not None
        assert signal.tool_name == "calculator"

    def test_no_gap_phrase_no_signal(self) -> None:
        # Keyword present but no capability-gap phrase → not a gap.
        assert detect_tool_gap("Sure, I'll calculate that for you.", []) is None

    def test_gap_phrase_but_tool_already_available_no_signal(self) -> None:
        # The persona HAS calculator → not a gap (Spec 25 refusal-retry covers this).
        assert detect_tool_gap("I can't calculate that.", available_tools=["calculator"]) is None

    def test_gap_phrase_without_any_catalog_keyword_no_signal(self) -> None:
        assert detect_tool_gap("I can't help with feelings.", available_tools=[]) is None

    def test_currency_gap_detected(self) -> None:
        signal = detect_tool_gap(
            "I don't have a way to convert currency at live rates.",
            available_tools=[],
        )
        assert signal is not None
        assert signal.tool_name == "currency_convert"

    def test_empty_output_no_signal(self) -> None:
        assert detect_tool_gap("", []) is None

    def test_first_catalog_match_wins(self) -> None:
        # Both 'search the web' and 'calculate' appear; catalog order puts
        # web_search first.
        signal = detect_tool_gap(
            "I can't search the web or calculate that.",
            available_tools=[],
        )
        assert signal is not None
        assert signal.tool_name == "web_search"

    def test_case_insensitive(self) -> None:
        signal = detect_tool_gap("I CAN'T CALCULATE THAT.", available_tools=[])
        assert signal is not None
        assert signal.tool_name == "calculator"


class TestBuildToolGapQuestion:
    def test_three_plus_one_shape(self) -> None:
        signal = ToolGapSignal(
            tool_name="calculator",
            description="Evaluate exact arithmetic.",
            matched_keyword="calculate",
        )
        question = build_tool_gap_question(signal)
        assert len(question.options) == PROACTIVE_QUESTION_OPTION_COUNT
        assert question.allow_free_form is True
        assert "calculator" in question.question
        assert question.options[0].label == "Enable it and retry"
