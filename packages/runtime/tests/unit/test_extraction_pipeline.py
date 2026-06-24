"""The runtime LLM extractor pipeline (Spec K2, T2; the ratified layering split).

``LlmExtractor`` implements the persona-CORE ``Extractor`` Protocol in
persona-RUNTIME: build the versioned prompt → one ``backend.chat`` call (the
small/mid tier is injected at composition, D-K2-3) → lenient parse → bounded
candidates. Mocked-LLM tests: the LLM is a stub backend returning canned content,
so these grade the *pipeline logic*, not a real model (that is T6's job).
"""

# ruff: noqa: ANN401 — the ChatBackend stub mirrors the protocol's loose **kwargs sig.

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from loguru import logger as _loguru_logger
from persona.backends.types import ChatResponse, TokenUsage
from persona.extraction import ExtractionInput, Extractor, InteractionKind
from persona_runtime.extraction.pipeline import LlmExtractor

if TYPE_CHECKING:
    from collections.abc import Iterator

    from persona.schema.conversation import ConversationMessage


@pytest.fixture
def loguru_capture() -> Iterator[list[str]]:
    """Capture emitted loguru messages (the project wraps loguru, so caplog is blind)."""
    captured: list[str] = []
    sink_id = _loguru_logger.add(lambda msg: captured.append(str(msg)), level="INFO")
    try:
        yield captured
    finally:
        _loguru_logger.remove(sink_id)


class _StubBackend:
    """A ChatBackend stub that returns a fixed ``content`` and records the call."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict[str, Any]] = []

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model_name(self) -> str:
        return "claude-haiku-4-5-20251001"

    @property
    def supports_native_tools(self) -> bool:
        return False

    @property
    def supports_vision(self) -> bool:
        return False

    async def chat(
        self, messages: list[ConversationMessage], *, temperature: float = 0.0, **kwargs: Any
    ) -> ChatResponse:
        self.calls.append({"messages": messages, "temperature": temperature, **kwargs})
        return ChatResponse(
            content=self._content,
            tool_calls=[],
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model=self.model_name,
            provider=self.provider_name,
            latency_ms=0.0,
        )

    def chat_stream(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - unused
        raise NotImplementedError


def _input(content: str = "USER: I'm vegetarian now.") -> ExtractionInput:
    return ExtractionInput(
        interaction_kind=InteractionKind.CONVERSATION,
        interaction_id="c1",
        persona_id="p1",
        content=content,
    )


def test_llm_extractor_satisfies_the_core_protocol() -> None:
    assert isinstance(LlmExtractor(backend=_StubBackend("{}")), Extractor)


@pytest.mark.asyncio
async def test_extract_parses_the_models_candidates() -> None:
    backend = _StubBackend(
        '{"candidates": [{"concept_name": "vegetarian", "content": "user is vegetarian",'
        ' "node_kind": "preference", "evidence_span": "I\'m vegetarian now"}]}'
    )
    out = await LlmExtractor(backend=backend).extract(_input())
    assert [c.concept_name for c in out] == ["vegetarian"]


@pytest.mark.asyncio
async def test_extract_is_deterministic_temperature_zero() -> None:
    backend = _StubBackend('{"candidates": []}')
    await LlmExtractor(backend=backend).extract(_input())
    assert backend.calls[0]["temperature"] == 0.0


@pytest.mark.asyncio
async def test_extract_sends_system_prompt_and_interaction_content() -> None:
    backend = _StubBackend('{"candidates": []}')
    await LlmExtractor(backend=backend).extract(_input("USER: my daughter is named Lina"))
    msgs = backend.calls[0]["messages"]
    assert msgs[0].role == "system"
    assert any("Lina" in m.content for m in msgs if isinstance(m.content, str))


@pytest.mark.asyncio
async def test_unparseable_output_yields_no_candidates_never_raises() -> None:
    out = await LlmExtractor(backend=_StubBackend("sorry, nothing to extract")).extract(_input())
    assert out == ()


@pytest.mark.asyncio
async def test_restraint_cap_truncates_and_logs(loguru_capture: list[str]) -> None:
    # A runaway model is bounded by a structural backstop (the prompt anchors 0–3;
    # the cap is defense-in-depth). The drop is logged, never silent.
    items = ", ".join(
        f'{{"concept_name": "c{i}", "content": "d", "node_kind": "fact", "evidence_span": "e{i}"}}'
        for i in range(10)
    )
    backend = _StubBackend(f'{{"candidates": [{items}]}}')
    out = await LlmExtractor(backend=backend, max_candidates=3).extract(_input())
    assert len(out) == 3
    assert any("truncat" in m.lower() or "max_candidates" in m for m in loguru_capture)
