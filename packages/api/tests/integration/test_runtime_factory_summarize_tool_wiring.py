"""RuntimeFactory wires ``text_summarize`` with the small-tier backend.

Spec 26 T07 / D-26-X-text-summarize-wiring-test-kind. ``text_summarize`` is the
one spec-26 tool that is NOT a ``build_default_toolbox`` built-in: it needs a
model, so the composition root injects it as an ``extra_tool`` with the SMALL
tier (mirroring ``code_execution`` / ``generate_image``). This test is the
no-§2.9-wiring-gap proof for that path — it asserts the composed Toolbox
registers + advertises + dispatches the tool, NOT a ``build_default_toolbox``
test (which would false-green, since the tool is intentionally absent there).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from persona.schema.conversation import ConversationMessage
from persona.schema.persona import Persona, PersonaIdentity
from persona.schema.tools import ToolCall
from persona_api.services.runtime_factory import RuntimeFactory

pytestmark = pytest.mark.integration


@dataclass
class _FakeResponse:
    content: str


class _FakeBackend:
    """In-memory small-tier ChatBackend stand-in; records calls."""

    def __init__(self) -> None:
        self.calls: list[list[ConversationMessage]] = []

    async def chat(self, messages: list[ConversationMessage], **kwargs: Any) -> _FakeResponse:  # noqa: ANN401, ARG002
        self.calls.append(messages)
        return _FakeResponse(content="A concise summary.")


class _FakeTierRegistry:
    """Returns the same fake backend for any tier (only ``small`` is used)."""

    def __init__(self, backend: _FakeBackend) -> None:
        self._backend = backend

    def get(self, tier_name: str) -> _FakeBackend:  # noqa: ARG002
        return self._backend


def _make_persona(*, tools: list[str]) -> Persona:
    return Persona(
        persona_id="persona_summarize_wire_test",
        identity=PersonaIdentity(
            name="Astrid",
            role="assistant",
            background="A helper for summarize-wiring tests.",
        ),
        tools=tools,
    )


def _make_factory(*, tier_registry: _FakeTierRegistry | None) -> RuntimeFactory:
    return RuntimeFactory(
        rls_engine=None,  # type: ignore[arg-type]
        embedder=None,  # type: ignore[arg-type]
        tier_registry=tier_registry,  # type: ignore[arg-type]
        turn_log_writer=None,  # type: ignore[arg-type]
        audit_root=Path("/tmp/persona-summarize-audit"),
        sandbox_pool=None,
        workspace_root=None,
        image_backend=None,
    )


@pytest.mark.asyncio
async def test_factory_registers_text_summarize_with_small_tier() -> None:
    """With a tier registry present, the toolbox registers ``text_summarize``."""
    backend = _FakeBackend()
    factory = _make_factory(tier_registry=_FakeTierRegistry(backend))
    persona = _make_persona(tools=["text_summarize"])

    toolbox = await factory._build_toolbox(persona, scanned_skills=[])  # type: ignore[attr-defined]

    names = toolbox.names()  # type: ignore[attr-defined]
    assert "text_summarize" in names, (
        f"text_summarize MUST be registered when a tier registry is present; got {names!r}"
    )


@pytest.mark.asyncio
async def test_text_summarize_not_a_build_default_builtin() -> None:
    """``text_summarize`` is absent from ``build_default_toolbox`` (it's runtime-wired).

    This pins the T1 architecture decision: putting its wiring test at the
    ``build_default_toolbox`` layer would false-green. It only exists once the
    runtime factory injects it.
    """
    from persona.config import PersonaCoreConfig
    from persona.tools import build_default_toolbox

    persona = _make_persona(tools=["text_summarize"])
    toolbox, _ = await build_default_toolbox(PersonaCoreConfig(), persona)
    assert "text_summarize" not in toolbox.names()


@pytest.mark.asyncio
async def test_persona_allow_list_gates_text_summarize() -> None:
    """Registered unconditionally (backend present) but advertised only when allowed."""
    backend = _FakeBackend()
    factory = _make_factory(tier_registry=_FakeTierRegistry(backend))
    persona = _make_persona(tools=["calculator"])  # does NOT declare text_summarize

    toolbox = await factory._build_toolbox(persona, scanned_skills=[])  # type: ignore[attr-defined]

    advertised = toolbox.names()  # type: ignore[attr-defined]
    assert "text_summarize" not in advertised
    assert "calculator" in advertised  # sanity — wires not crossed


@pytest.mark.asyncio
async def test_mock_dispatch_lands_on_small_backend() -> None:
    """A simulated tool call dispatches through the toolbox to the small backend."""
    backend = _FakeBackend()
    factory = _make_factory(tier_registry=_FakeTierRegistry(backend))
    persona = _make_persona(tools=["text_summarize"])

    toolbox = await factory._build_toolbox(persona, scanned_skills=[])  # type: ignore[attr-defined]
    call = ToolCall(
        name="text_summarize",
        args={"text": "A long passage that needs condensing.", "max_words": 30},
        call_id="call_summarize_1",
    )
    result = await toolbox.dispatch(call)  # type: ignore[attr-defined]

    assert len(backend.calls) == 1
    assert result.tool_name == "text_summarize"
    assert result.is_error is False
    assert result.content == "A concise summary."


@pytest.mark.asyncio
async def test_absent_tier_registry_omits_text_summarize() -> None:
    """Partial composition (no tier registry) boots cleanly without the tool."""
    factory = _make_factory(tier_registry=None)
    persona = _make_persona(tools=["text_summarize"])

    toolbox = await factory._build_toolbox(persona, scanned_skills=[])  # type: ignore[attr-defined]
    assert "text_summarize" not in toolbox.names()  # type: ignore[attr-defined]
