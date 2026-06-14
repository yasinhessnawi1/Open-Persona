"""Tests for the authoring tool-recommender (spec 26 T09).

The recommender's RELEVANCE quality depends on the live model (verified at the
operator pass, T14). These tests pin the deterministic post-processing the
service owns: JSON extraction, catalog-membership filtering (drop hallucinated
names), the confidence floor, dedup, descending sort, the cap, and retry-once.
A scripted backend stands in for the model.
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
    """Returns queued responses in order; records the prompts it saw."""

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.prompts: list[list[ConversationMessage]] = []

    async def chat(self, messages: list[ConversationMessage], **kwargs: Any) -> _Resp:
        self.prompts.append(messages)
        return _Resp(self._replies.pop(0))


@pytest.mark.asyncio
async def test_returns_validated_catalog_recommendations() -> None:
    backend = _ScriptedBackend(
        json.dumps(
            [
                {"tool_name": "web_search", "rationale": "find case law", "confidence": 0.9},
                {"tool_name": "web_fetch", "rationale": "read statutes", "confidence": 0.8},
            ]
        )
    )
    recs = await authoring_service.recommend_tools_for_persona(
        backend,  # type: ignore[arg-type]
        "a Norwegian legal assistant for tenancy law",
    )
    assert [r.tool_name for r in recs] == ["web_search", "web_fetch"]
    assert recs[0].confidence == 0.9


@pytest.mark.asyncio
async def test_hallucinated_tool_names_dropped() -> None:
    backend = _ScriptedBackend(
        json.dumps(
            [
                {"tool_name": "calculator", "rationale": "math", "confidence": 0.9},
                {"tool_name": "search_legal_db", "rationale": "made up", "confidence": 0.95},
            ]
        )
    )
    recs = await authoring_service.recommend_tools_for_persona(backend, "x")  # type: ignore[arg-type]
    assert [r.tool_name for r in recs] == ["calculator"]


@pytest.mark.asyncio
async def test_confidence_floor_drops_weak_recs() -> None:
    backend = _ScriptedBackend(
        json.dumps(
            [
                {"tool_name": "calculator", "rationale": "strong", "confidence": 0.8},
                {"tool_name": "datetime", "rationale": "weak", "confidence": 0.3},
            ]
        )
    )
    recs = await authoring_service.recommend_tools_for_persona(backend, "x")  # type: ignore[arg-type]
    assert [r.tool_name for r in recs] == ["calculator"]


@pytest.mark.asyncio
async def test_sorted_descending_and_capped_at_ten() -> None:
    catalog = [
        "web_search",
        "web_fetch",
        "file_read",
        "file_write",
        "code_execution",
        "calculator",
        "datetime",
        "regex_match",
        "text_diff",
        "text_summarize",
        "json_query",
        "currency_convert",
    ]
    items = [
        {"tool_name": name, "rationale": "r", "confidence": round(0.5 + i * 0.04, 2)}
        for i, name in enumerate(catalog)
    ]
    backend = _ScriptedBackend(json.dumps(items))
    recs = await authoring_service.recommend_tools_for_persona(backend, "x")  # type: ignore[arg-type]
    assert len(recs) == 10  # capped
    confidences = [r.confidence for r in recs]
    assert confidences == sorted(confidences, reverse=True)  # descending


@pytest.mark.asyncio
async def test_dedup_keeps_highest_confidence() -> None:
    backend = _ScriptedBackend(
        json.dumps(
            [
                {"tool_name": "calculator", "rationale": "low", "confidence": 0.6},
                {"tool_name": "calculator", "rationale": "high", "confidence": 0.9},
            ]
        )
    )
    recs = await authoring_service.recommend_tools_for_persona(backend, "x")  # type: ignore[arg-type]
    assert len(recs) == 1
    assert recs[0].confidence == 0.9


@pytest.mark.asyncio
async def test_tolerates_prose_around_json() -> None:
    backend = _ScriptedBackend(
        'Here are my picks:\n[{"tool_name": "calculator", "rationale": "math", '
        '"confidence": 0.7}]\nHope that helps!'
    )
    recs = await authoring_service.recommend_tools_for_persona(backend, "x")  # type: ignore[arg-type]
    assert [r.tool_name for r in recs] == ["calculator"]


@pytest.mark.asyncio
async def test_retry_once_on_unparseable_then_succeeds() -> None:
    backend = _ScriptedBackend(
        "I cannot do that.",  # no JSON array → triggers retry
        json.dumps([{"tool_name": "datetime", "rationale": "time", "confidence": 0.8}]),
    )
    recs = await authoring_service.recommend_tools_for_persona(backend, "x")  # type: ignore[arg-type]
    assert [r.tool_name for r in recs] == ["datetime"]
    assert len(backend.prompts) == 2  # original + retry


@pytest.mark.asyncio
async def test_empty_list_when_both_attempts_unparseable() -> None:
    backend = _ScriptedBackend("nope", "still nope")
    recs = await authoring_service.recommend_tools_for_persona(backend, "x")  # type: ignore[arg-type]
    assert recs == []


@pytest.mark.asyncio
async def test_catalog_is_enumerated_in_prompt() -> None:
    backend = _ScriptedBackend(json.dumps([]))
    await authoring_service.recommend_tools_for_persona(backend, "x")  # type: ignore[arg-type]
    system_prompt = backend.prompts[0][0].content
    # The rubric enumerates the catalog so the model picks real names.
    assert "calculator" in system_prompt
    assert "currency_convert" in system_prompt
