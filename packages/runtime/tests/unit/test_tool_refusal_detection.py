"""Unit tests for Spec 25 §2.9 (low-risk half) — tool-refusal observability.

Covers two additive surfaces on :mod:`persona_runtime.logging`:

1. :func:`detect_tool_refusals` — a pure, conservative detector that flags
   when the model's text refuses a capability whose tool IS in
   ``available_tools``. Refusals for *unavailable* tools and non-refusal text
   produce the empty list (low false-positive discipline, D-25-X §2.9).
2. :attr:`TurnLog.tool_refusal_detected` — an additive ``list[str]`` field
   (Spec 18 D-18-1 NOT reopened; mirrors the existing additive-field style).
   Defaults to ``[]`` and round-trips cleanly through ``model_dump_json``.

This module covers detection + field shape only. The affirmative
tool-description rewrites and the turn-loop wiring of
:func:`detect_tool_refusals` are follow-on reconciliation work (T09/T10/T21),
out of scope here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from persona_runtime.logging import TurnLog, detect_tool_refusals

_ALL_TOOLS = [
    "code_execution",
    "file_read",
    "file_write",
    "web_search",
    "web_fetch",
    "generate_image",
]


def _now() -> datetime:
    return datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)


def _baseline_log_kwargs() -> dict[str, object]:
    return {
        "conversation_id": "c1",
        "turn_index": 0,
        "tier_used": "frontier",
        "model_name": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
        "provider": "nvidia",
        "prompt_tokens": 80,
        "completion_tokens": 40,
        "latency_ms": 1500.0,
        "cost_cents": 0.7,
        "timestamp": _now(),
    }


class TestDetectImageRefusal:
    """Image-generation refusals — the canonical §2.9 symptom."""

    def test_classic_refusal_with_tool_available(self) -> None:
        text = (
            "I can't generate images. I'm a text and code-based tool. "
            "No DALL-E, no Stable Diffusion."
        )
        assert detect_tool_refusals(text, _ALL_TOOLS) == ["generate_image"]

    def test_cannot_phrasing(self) -> None:
        text = "I cannot create images for you."
        assert detect_tool_refusals(text, _ALL_TOOLS) == ["generate_image"]

    def test_unable_phrasing(self) -> None:
        text = "Unfortunately I'm unable to generate an image right now."
        assert detect_tool_refusals(text, _ALL_TOOLS) == ["generate_image"]

    def test_refusal_when_tool_unavailable_is_empty(self) -> None:
        text = "I can't generate images."
        # generate_image NOT in the allow-list → no flag (model is accurate).
        available = ["web_search", "web_fetch"]
        assert detect_tool_refusals(text, available) == []


class TestDetectBrowseRefusal:
    """Web-fetch / web-search refusals."""

    def test_cannot_browse_flags_web_tools(self) -> None:
        text = "I can't browse the web or access live websites."
        result = detect_tool_refusals(text, _ALL_TOOLS)
        assert "web_fetch" in result
        assert "web_search" in result

    def test_browse_refusal_only_flags_available_web_tools(self) -> None:
        text = "I'm unable to browse the internet."
        # Only web_search wired; web_fetch is not.
        assert detect_tool_refusals(text, ["web_search"]) == ["web_search"]


class TestDetectCodeRefusal:
    """Code-execution refusals."""

    def test_cannot_run_code_flags_code_execution(self) -> None:
        text = "I cannot run code or execute scripts."
        assert detect_tool_refusals(text, _ALL_TOOLS) == ["code_execution"]

    def test_code_refusal_unavailable_is_empty(self) -> None:
        text = "I can't run code."
        assert detect_tool_refusals(text, ["web_search"]) == []


class TestNonRefusalText:
    """Conservative discipline — ordinary text must never flag."""

    def test_plain_answer_is_empty(self) -> None:
        text = "Here is the image you requested, rendered as a sunset over the sea."
        assert detect_tool_refusals(text, _ALL_TOOLS) == []

    def test_affirmative_capability_statement_is_empty(self) -> None:
        text = "I can generate images for you. Let me create one now."
        assert detect_tool_refusals(text, _ALL_TOOLS) == []

    def test_empty_text_is_empty(self) -> None:
        assert detect_tool_refusals("", _ALL_TOOLS) == []

    def test_empty_available_tools_is_empty(self) -> None:
        text = "I can't generate images."
        assert detect_tool_refusals(text, []) == []

    def test_unrelated_refusal_is_empty(self) -> None:
        # Refusal about something with no mapped tool capability.
        text = "I can't give you medical advice."
        assert detect_tool_refusals(text, _ALL_TOOLS) == []


class TestDetectorPurity:
    """Detector is pure: no dedup duplicates, deterministic order."""

    def test_no_duplicate_tool_names(self) -> None:
        text = "I can't generate images. I cannot create pictures either."
        # Two image-refusal matches, but the tool name appears once.
        assert detect_tool_refusals(text, _ALL_TOOLS) == ["generate_image"]

    def test_case_insensitive(self) -> None:
        text = "I CAN'T GENERATE IMAGES."
        assert detect_tool_refusals(text, _ALL_TOOLS) == ["generate_image"]


class TestTurnLogRefusalField:
    """The additive ``tool_refusal_detected`` field on TurnLog."""

    def test_defaults_to_empty_list(self) -> None:
        log = TurnLog(**_baseline_log_kwargs())  # type: ignore[arg-type]
        assert log.tool_refusal_detected == []

    def test_accepts_detected_tools(self) -> None:
        log = TurnLog(
            **_baseline_log_kwargs(),  # type: ignore[arg-type]
            tool_refusal_detected=["generate_image"],
        )
        assert log.tool_refusal_detected == ["generate_image"]

    def test_roundtrip_default(self) -> None:
        log = TurnLog(**_baseline_log_kwargs())  # type: ignore[arg-type]
        payload = json.loads(log.model_dump_json())
        assert payload["tool_refusal_detected"] == []

    def test_roundtrip_populated(self) -> None:
        log = TurnLog(
            **_baseline_log_kwargs(),  # type: ignore[arg-type]
            tool_refusal_detected=["generate_image", "web_fetch"],
        )
        payload = json.loads(log.model_dump_json())
        assert payload["tool_refusal_detected"] == ["generate_image", "web_fetch"]

    def test_detector_output_feeds_field(self) -> None:
        text = "I can't generate images."
        detected = detect_tool_refusals(text, _ALL_TOOLS)
        log = TurnLog(
            **_baseline_log_kwargs(),  # type: ignore[arg-type]
            tool_refusal_detected=detected,
        )
        assert log.tool_refusal_detected == ["generate_image"]

    def test_extra_field_still_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TurnLog(
                **_baseline_log_kwargs(),  # type: ignore[arg-type]
                refusal_undeclared="rogue",  # type: ignore[call-arg]
            )
