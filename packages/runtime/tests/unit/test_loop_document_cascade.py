"""Document-workspace cascade — ConversationLoop.turn document staging.

Uploaded NON-image documents (.md/.pdf/.txt/...) reached the model only as a
``document_context`` synopsis; the sandbox ``file_read`` / ``code_execution``
tools (reading from the per-conversation sandbox input mount) never saw the
actual file. This pins the loop-level contract that closes that gap, mirroring
the image cascade:

* ``loop.turn`` accepts ``documents: list[SandboxFile] | None`` and appends them
  to ``deferred_input_files`` (drained by the API's augmented input-files
  provider, so ``code_execution`` reads the ACTUAL uploaded document).
* Documents and images stage TOGETHER on a mixed turn, in a stable order.
* A no-document turn stages nothing (text-only behaviour unchanged).
"""

from __future__ import annotations

import pytest
from _fakes import FakeStore, ScriptedBackend, ScriptedRound  # type: ignore[import-not-found]
from persona.backends import BackendConfig
from persona.history import ConversationHistoryManager
from persona.sandbox.result import SandboxFile
from persona.schema.conversation import Conversation
from persona.schema.persona import Persona, PersonaIdentity
from persona.schema.tools import ToolResult
from persona.skills import SkillInjector, SkillScanner
from persona.tools import Toolbox
from persona.tools.protocol import tool
from persona_runtime.images import TurnImage
from persona_runtime.logging import MemoryTurnLogWriter
from persona_runtime.loop import ConversationLoop
from persona_runtime.prompt import PromptBuilder
from persona_runtime.router import Router
from persona_runtime.tier import TierConfig, TierRegistry

_DUMMY_CFG = BackendConfig(provider="anthropic", model="m", api_key=None)  # type: ignore[arg-type]


def _persona() -> Persona:
    return Persona(
        persona_id="astrid",
        identity=PersonaIdentity(
            name="Astrid",
            role="tenancy assistant",
            background="Knows husleieloven.",
            constraints=["Never give binding advice."],
        ),
    )


@tool(name="echo", description="Echo a message back.")
async def _echo_tool(message: str) -> ToolResult:
    return ToolResult(tool_name="echo", content=f"echoed: {message}", is_error=False)


def _make_loop(backend: ScriptedBackend) -> ConversationLoop:
    stores = {
        "identity": FakeStore(),
        "self_facts": FakeStore(),
        "worldview": FakeStore(),
        "episodic": FakeStore(),
    }
    toolbox = Toolbox([_echo_tool], allow_list=None)  # type: ignore[arg-type]
    registry = TierRegistry(
        {
            "frontier": TierConfig(name="frontier", backend_config=_DUMMY_CFG),
            "mid": TierConfig(name="mid", backend_config=_DUMMY_CFG),
            "small": TierConfig(name="small", backend_config=_DUMMY_CFG),
        }
    )
    registry._cache = {"frontier": backend, "mid": backend, "small": backend}  # type: ignore[assignment,attr-defined]  # noqa: E501, SLF001
    return ConversationLoop(
        persona=_persona(),
        stores=stores,  # type: ignore[arg-type]
        toolbox=toolbox,
        skill_scanner=SkillScanner([]),
        skill_injector=SkillInjector(),
        scanned_skills=[],
        history_manager=ConversationHistoryManager(compact_every=10, keep_recent=5),
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=registry,
        turn_log_writer=MemoryTurnLogWriter(),
    )


def _conv() -> Conversation:
    return Conversation(conversation_id="c1", persona_id="astrid", messages=[])


def _doc() -> SandboxFile:
    body = b"# Polly's Layout Test\n\nThis is the ACTUAL uploaded document.\n"
    return SandboxFile(
        path="uploads/pollys-layout-test.md",
        content_bytes=body,
        size_bytes=len(body),
        media_type="text/markdown",
    )


def _png() -> TurnImage:
    return TurnImage(
        workspace_path="uploads/cat.png",
        media_type="image/png",
        content_bytes=b"\x89PNG\r\n\x1a\nFAKEBYTES",
    )


class TestDocumentStaging:
    """A document turn stages the file bytes into deferred_input_files."""

    @pytest.mark.asyncio
    async def test_document_turn_stages_deferred_input_file(self) -> None:
        backend = ScriptedBackend([ScriptedRound(text="ok")])
        loop = _make_loop(backend)
        conv = _conv()

        _ = [c async for c in loop.turn(conv, "summarise the doc", documents=[_doc()])]

        staged = loop.deferred_input_files
        assert len(staged) == 1
        sf = staged[0]
        # The ACTUAL uploaded document — staged under the sandbox input mount at
        # the same relative ``uploads/<ref>`` path so file_read / code_execution
        # find THIS file (not a stale README from another context).
        assert sf.path == "uploads/pollys-layout-test.md"
        assert sf.content_bytes is not None
        assert b"Polly's Layout Test" in sf.content_bytes
        assert sf.media_type == "text/markdown"

    @pytest.mark.asyncio
    async def test_no_document_turn_stages_nothing(self) -> None:
        backend = ScriptedBackend([ScriptedRound(text="ok")])
        loop = _make_loop(backend)
        conv = _conv()

        _ = [c async for c in loop.turn(conv, "hi")]

        assert loop.deferred_input_files == []

    @pytest.mark.asyncio
    async def test_images_and_documents_stage_together(self) -> None:
        backend = ScriptedBackend([ScriptedRound(text="ok")], supports_vision=True)
        loop = _make_loop(backend)
        conv = _conv()

        _ = [c async for c in loop.turn(conv, "compare", images=[_png()], documents=[_doc()])]

        paths = {sf.path for sf in loop.deferred_input_files}
        assert paths == {"uploads/cat.png", "uploads/pollys-layout-test.md"}
