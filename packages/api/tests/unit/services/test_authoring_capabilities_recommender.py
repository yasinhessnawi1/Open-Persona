"""Tests for the unified capability recommender (spec 27 T10, D-26-10 / D-27-13).

Pins the deterministic post-processing the service owns: provider resolution
across built-in tools + skills + MCP servers, hallucination drop, confidence
floor, dedup, descending sort, and the COMBINED (not per-category) cap. A
scripted backend stands in for the model.
"""

# ruff: noqa: ANN401, ARG002
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from persona_api.services import authoring_service

if TYPE_CHECKING:
    from persona.schema.conversation import ConversationMessage


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


class _ScriptedBackend:
    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.prompts: list[list[ConversationMessage]] = []

    async def chat(self, messages: list[ConversationMessage], **kwargs: Any) -> _Resp:
        self.prompts.append(messages)
        return _Resp(self._replies.pop(0))


_SKILLS = ("web_research", "document_generation")


@pytest.mark.asyncio
async def test_unifies_tools_skills_and_mcp_with_provider_tags() -> None:
    reply = json.dumps(
        [
            {"name": "web_search", "rationale": "fact finding", "confidence": 0.9},
            {"name": "document_generation", "rationale": "drafts docs", "confidence": 0.8},
            {"name": "mcp:filesystem", "rationale": "saves files", "confidence": 0.7},
            {"name": "mcp:github", "rationale": "reviews code", "confidence": 0.6},
        ]
    )
    recs = await authoring_service.recommend_capabilities_for_persona(
        _ScriptedBackend(reply),
        "a legal writer",
        available_skills=_SKILLS,  # type: ignore[arg-type]
    )
    by_name = {r.tool_name: r.provider for r in recs}
    assert by_name["web_search"] == "builtin"
    assert by_name["document_generation"] == "skill"
    assert by_name["mcp:filesystem"] == "mcp:builtin"  # default-enabled
    assert by_name["mcp:github"] == "mcp:optional"  # external/BYO


@pytest.mark.asyncio
async def test_hallucinated_and_unknown_skill_names_dropped() -> None:
    reply = json.dumps(
        [
            {"name": "web_search", "rationale": "ok", "confidence": 0.9},
            {"name": "telepathy", "rationale": "no", "confidence": 0.9},
            {"name": "unlisted_skill", "rationale": "no", "confidence": 0.9},
            {"name": "mcp:nonsense", "rationale": "no", "confidence": 0.9},
        ]
    )
    recs = await authoring_service.recommend_capabilities_for_persona(
        _ScriptedBackend(reply),
        "x",
        available_skills=_SKILLS,  # type: ignore[arg-type]
    )
    assert [r.tool_name for r in recs] == ["web_search"]


@pytest.mark.asyncio
async def test_bare_mcp_name_is_tolerated_and_canonicalised() -> None:
    reply = json.dumps([{"name": "weather", "rationale": "forecasts", "confidence": 0.8}])
    recs = await authoring_service.recommend_capabilities_for_persona(
        _ScriptedBackend(reply),
        "x",
        available_skills=(),  # type: ignore[arg-type]
    )
    # 'weather' is an MCP server name → canonicalised to mcp:weather, provider derived.
    assert recs[0].tool_name == "mcp:weather"
    assert recs[0].provider == "mcp:optional"


@pytest.mark.asyncio
async def test_combined_cap_is_ten_across_all_providers() -> None:
    # 8 tools + 2 skills + 2 mcp = 12 valid candidates → capped at 10 combined.
    items = (
        [
            {"name": n, "rationale": "r", "confidence": 0.99}
            for n in (
                "web_search",
                "web_fetch",
                "file_read",
                "file_write",
                "calculator",
                "datetime",
                "regex_match",
                "json_query",
            )
        ]
        + [{"name": n, "rationale": "r", "confidence": 0.99} for n in _SKILLS]
        + [
            {"name": n, "rationale": "r", "confidence": 0.99}
            for n in ("mcp:filesystem", "mcp:time")
        ]
    )
    recs = await authoring_service.recommend_capabilities_for_persona(
        _ScriptedBackend(json.dumps(items)),
        "x",
        available_skills=_SKILLS,  # type: ignore[arg-type]
    )
    assert len(recs) == 10  # COMBINED cap (D-27-13), not 10-per-category


@pytest.mark.asyncio
async def test_confidence_floor_and_descending_sort() -> None:
    reply = json.dumps(
        [
            {"name": "web_search", "rationale": "r", "confidence": 0.51},
            {"name": "calculator", "rationale": "r", "confidence": 0.4},  # below floor
            {"name": "mcp:filesystem", "rationale": "r", "confidence": 0.95},
        ]
    )
    recs = await authoring_service.recommend_capabilities_for_persona(
        _ScriptedBackend(reply),
        "x",
        available_skills=(),  # type: ignore[arg-type]
    )
    assert [r.tool_name for r in recs] == ["mcp:filesystem", "web_search"]


@pytest.mark.asyncio
async def test_retry_once_then_empty_when_unparseable() -> None:
    recs = await authoring_service.recommend_capabilities_for_persona(
        _ScriptedBackend("no json", "still none"),
        "x",
        available_skills=(),  # type: ignore[arg-type]
    )
    assert recs == []


@pytest.mark.asyncio
async def test_prompt_enumerates_all_three_providers() -> None:
    backend = _ScriptedBackend("[]")
    await authoring_service.recommend_capabilities_for_persona(
        backend,
        "x",
        available_skills=_SKILLS,  # type: ignore[arg-type]
    )
    system = backend.prompts[0][0].content
    assert "Built-in tools:" in system
    assert "Skills:" in system
    assert "MCP servers" in system
    assert "mcp:filesystem" in system


def test_tool_recommendation_provider_defaults_to_builtin() -> None:
    from persona_api.schemas.responses import ToolRecommendation

    rec = ToolRecommendation(tool_name="web_search", rationale="r", confidence=0.9)
    assert rec.provider == "builtin"  # back-compat: Spec-26 shape unchanged
