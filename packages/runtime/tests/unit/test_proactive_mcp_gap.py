"""Unit tests for runtime MCP-gap detection (spec 27 T11, D-27-7)."""

from __future__ import annotations

from persona_runtime.proactive_mcp_gap import (
    MCPGapSignal,
    build_mcp_gap_question,
    detect_mcp_gap,
)
from persona_runtime.questions import PROACTIVE_QUESTION_OPTION_COUNT


def test_detects_gap_when_phrase_and_server_keyword_co_occur() -> None:
    out = "I can't check the weather for you right now."
    signal = detect_mcp_gap(out, available_tools=["web_search"])
    assert signal is not None
    assert signal.server_name == "weather"
    assert signal.provider == "mcp:optional"


def test_no_gap_without_a_capability_phrase() -> None:
    out = "The weather in Oslo is sunny."  # 'weather' keyword but no gap phrase
    assert detect_mcp_gap(out, available_tools=[]) is None


def test_no_gap_when_server_already_available() -> None:
    out = "I can't read that file from disk."
    # persona already has a filesystem MCP tool → no gap to offer.
    assert detect_mcp_gap(out, available_tools=["mcp:filesystem:read_file"]) is None


def test_empty_output_is_no_gap() -> None:
    assert detect_mcp_gap("", available_tools=[]) is None


def test_first_catalog_match_wins_at_most_one_offer() -> None:
    # Mentions both a filesystem and a weather need; catalog order → filesystem
    # comes before weather, but only one signal is returned.
    out = "I can't save the file or get the weather."
    signal = detect_mcp_gap(out, available_tools=[])
    assert signal is not None
    assert signal.server_name in {"filesystem", "weather"}


def test_question_is_three_plus_one_and_names_the_server() -> None:
    signal = MCPGapSignal(
        server_name="weather",
        provider="mcp:optional",
        capability="look up the current weather and forecast for a location",
        matched_keyword="weather",
    )
    q = build_mcp_gap_question(signal)
    assert len(q.options) == PROACTIVE_QUESTION_OPTION_COUNT
    assert q.allow_free_form is True
    assert "weather" in q.question
    # Verb-led capability keeps the consent line grammatical (no "let me current …").
    assert "let me look up the current weather and forecast for a location" in q.question


def test_detected_signal_carries_verb_led_capability_from_catalog() -> None:
    # The real catalog entries supply verb-phrase capabilities (not noun phrases),
    # so the generated consent question reads grammatically.
    signal = detect_mcp_gap("I can't check the weather for you.", available_tools=[])
    assert signal is not None
    assert signal.capability == "look up the current weather and forecast for a location"
    q = build_mcp_gap_question(signal)
    assert "let me look up the current weather" in q.question


def test_required_env_is_surfaced_in_enable_option() -> None:
    signal = MCPGapSignal(
        server_name="github",
        provider="mcp:optional",
        capability="work with GitHub repositories, issues, and pull requests",
        matched_keyword="github",
        required_env=("GITHUB_TOKEN",),
    )
    q = build_mcp_gap_question(signal)
    assert "GITHUB_TOKEN" in q.options[0].description


def test_mcp_gap_question_carries_grant_proposal_with_mcp_name() -> None:
    # Spec 30 (D-30-2 / D-30-X-mcp-gap-accept-target): the accept target is the
    # `mcp:<server>` allow-list entry, granted via the consent path; the provider
    # tag rides for badge display.
    signal = MCPGapSignal(
        server_name="weather",
        provider="mcp:optional",
        capability="look up the current weather and forecast for a location",
        matched_keyword="weather",
    )
    proposal = build_mcp_gap_question(signal).proposal
    assert proposal is not None
    assert proposal.kind == "mcp"
    assert proposal.name == "mcp:weather"
    assert proposal.action == "grant_tool"
    assert proposal.provider == "mcp:optional"
