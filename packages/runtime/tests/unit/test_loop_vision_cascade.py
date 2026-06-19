"""Image-workspace cascade — ConversationLoop.turn image threading (Parts 1+2).

Completes the never-landed "Spec 13 T12 image-workspace-cascade": uploaded
chat images must reach BOTH the model (as a multimodal user message) AND the
sandbox (as staged input files). These tests pin the loop-level contract:

* **Part 2 (model vision)** — a turn carrying an image produces a user
  :class:`ConversationMessage` whose ``content`` is
  ``[TextContent(user_message), ImageContent(workspace_path, media_type), ...]``
  so it flows into the backend's existing vision serialisers.
* **Part 1 (wiring)** — ``loop.turn`` accepts ``images`` and forwards them to
  BOTH consumers: the prompt's multimodal user message AND the loop's
  ``deferred_input_files`` (drained by the sandbox tool). A no-image turn is
  unchanged (text-only ``content=str``).
"""

from __future__ import annotations

import pytest
from _fakes import FakeStore, ScriptedBackend, ScriptedRound  # type: ignore[import-not-found]
from persona.backends import BackendConfig
from persona.history import ConversationHistoryManager
from persona.schema.content import ImageContent, TextContent
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


def _make_loop(
    backend: ScriptedBackend,
) -> tuple[ConversationLoop, MemoryTurnLogWriter]:
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
    writer = MemoryTurnLogWriter()
    loop = ConversationLoop(
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
        turn_log_writer=writer,
    )
    return loop, writer


def _conv() -> Conversation:
    return Conversation(conversation_id="c1", persona_id="astrid", messages=[])


def _png() -> TurnImage:
    return TurnImage(
        workspace_path="uploads/cat.png",
        media_type="image/png",
        content_bytes=b"\x89PNG\r\n\x1a\nFAKEBYTES",
    )


class TestModelVision:
    """Part 2 — an image turn builds a multimodal user message."""

    @pytest.mark.asyncio
    async def test_image_turn_sends_multimodal_user_message_to_backend(self) -> None:
        backend = ScriptedBackend([ScriptedRound(text="I see a cat.")], supports_vision=True)
        loop, _ = _make_loop(backend)
        conv = _conv()

        chunks = [c async for c in loop.turn(conv, "what is this?", images=[_png()])]

        assert chunks[-1].is_final is True
        assert backend.last_stream_messages is not None
        user_msg = backend.last_stream_messages[-1]
        assert user_msg.role == "user"
        assert isinstance(user_msg.content, list)
        assert user_msg.content[0] == TextContent(text="what is this?")
        image_block = user_msg.content[1]
        assert isinstance(image_block, ImageContent)
        assert image_block.workspace_path == "uploads/cat.png"
        assert image_block.media_type == "image/png"
        # The resolved upload bytes ride inline so the cached, workspace_root-less
        # chat tier backend can base64-serialise them to the model (Step 2 gap).
        assert image_block.inline_bytes == b"\x89PNG\r\n\x1a\nFAKEBYTES"

    @pytest.mark.asyncio
    async def test_multiple_images_each_become_an_image_block(self) -> None:
        backend = ScriptedBackend([ScriptedRound(text="two images")], supports_vision=True)
        loop, _ = _make_loop(backend)
        conv = _conv()
        imgs = [
            _png(),
            TurnImage(
                workspace_path="uploads/b.jpg",
                media_type="image/jpeg",
                content_bytes=b"\xff\xd8\xffJPEG",
            ),
        ]

        _ = [c async for c in loop.turn(conv, "compare", images=imgs)]

        user_msg = backend.last_stream_messages[-1]  # type: ignore[index]
        assert isinstance(user_msg.content, list)
        image_blocks = [b for b in user_msg.content if isinstance(b, ImageContent)]
        assert [b.workspace_path for b in image_blocks] == ["uploads/cat.png", "uploads/b.jpg"]

    @pytest.mark.asyncio
    async def test_no_image_turn_keeps_string_content(self) -> None:
        """Regression — a text-only turn's user message stays ``content=str``."""
        backend = ScriptedBackend([ScriptedRound(text="hello")])
        loop, _ = _make_loop(backend)
        conv = _conv()

        _ = [c async for c in loop.turn(conv, "hi there")]

        user_msg = backend.last_stream_messages[-1]  # type: ignore[index]
        assert user_msg.content == "hi there"


class TestSandboxStaging:
    """Part 1/4 — an image turn stages the bytes into deferred_input_files."""

    @pytest.mark.asyncio
    async def test_image_turn_stages_deferred_input_file(self) -> None:
        backend = ScriptedBackend([ScriptedRound(text="ok")], supports_vision=True)
        loop, _ = _make_loop(backend)
        conv = _conv()

        _ = [c async for c in loop.turn(conv, "describe", images=[_png()])]

        staged = loop.deferred_input_files
        assert len(staged) == 1
        sf = staged[0]
        # Staged under the sandbox input mount (/workspace/in) at the same
        # relative ``uploads/<ref>`` path the model references; bytes inline.
        assert sf.path == "uploads/cat.png"
        assert sf.content_bytes == b"\x89PNG\r\n\x1a\nFAKEBYTES"
        assert sf.media_type == "image/png"

    @pytest.mark.asyncio
    async def test_no_image_turn_stages_nothing(self) -> None:
        backend = ScriptedBackend([ScriptedRound(text="ok")])
        loop, _ = _make_loop(backend)
        conv = _conv()

        _ = [c async for c in loop.turn(conv, "hi")]

        assert loop.deferred_input_files == []


class TestConversationWriteBack:
    """Part 1 — the persisted user turn reflects the multimodal content."""

    @pytest.mark.asyncio
    async def test_appended_user_message_is_multimodal(self) -> None:
        backend = ScriptedBackend([ScriptedRound(text="seen")], supports_vision=True)
        loop, _ = _make_loop(backend)
        conv = _conv()

        _ = [c async for c in loop.turn(conv, "look", images=[_png()])]

        # The loop appends user + assistant on success (D-S05-4).
        user_turns = [m for m in conv.messages if m.role == "user"]
        assert len(user_turns) == 1
        assert isinstance(user_turns[0].content, list)
        assert any(isinstance(b, ImageContent) for b in user_turns[0].content)
