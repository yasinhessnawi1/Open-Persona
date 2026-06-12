"""RuntimeFactory wires ``generate_image`` when image_backend is composed.

Spec 25 §2.9 hotfix coverage. The bug was a wiring gap: the
``make_generate_image_tool`` factory existed in ``persona.imagegen`` with
37+ unit tests, but ``RuntimeFactory._build_toolbox`` never called it — so
the persona-callable chat path could not dispatch ``generate_image``
turns even when ``PERSONA_IMAGEGEN_API_KEY`` was set and
``app.state.image_backend`` was populated.

These tests pin the four invariants the hotfix establishes:

1. **Wired path:** factory with a (mock) ``ImageBackend`` ⇒ toolbox
   registers ``generate_image``.
2. **Absent path:** factory with ``image_backend=None`` ⇒ toolbox does
   NOT register ``generate_image`` (preserves the pre-hotfix shape for
   personas / deployments where image gen is intentionally absent).
3. **Allow-list integration:** the persona's ``tools`` declaration is
   the final gate — the tool is registered unconditionally when the
   backend is present, but only advertised via ``Toolbox.get_specs()``
   when the persona's allow-list admits it.
4. **End-to-end dispatch:** a simulated tool call dispatched through
   the toolbox lands on the mock backend's ``generate`` method and
   returns a structured success ``ToolResult``.

The tests do NOT touch the real NVIDIA / OpenAI / fal API — every
``generate`` call routes to an in-memory fake backend.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from persona.imagegen.errors import ImageProviderError
from persona.imagegen.result import GeneratedImage, GenerationResult
from persona.schema.persona import Persona, PersonaIdentity
from persona.schema.tools import ToolCall
from persona_api.services.runtime_factory import RuntimeFactory

if TYPE_CHECKING:
    from persona.imagegen.result import ImageGenOptions

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fakes — keep the test isolated from DB / real provider SDKs.
# ---------------------------------------------------------------------------


class _FakeImageBackend:
    """In-memory :class:`ImageBackend` stand-in.

    Records every ``generate`` call so tests can assert dispatch landed
    here, and returns a deterministic single-image :class:`GenerationResult`
    with PNG bytes. ``ImageBackend`` is a runtime-checkable Protocol;
    structural conformance is enough.
    """

    provider_name = "fake"
    model_name = "fake-model-1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ImageGenOptions | None]] = []

    async def generate(
        self,
        prompt: str,
        *,
        options: ImageGenOptions | None = None,
    ) -> GenerationResult:
        self.calls.append((prompt, options))
        return GenerationResult(
            images=[
                GeneratedImage(
                    image_bytes=b"\x89PNG\r\n\x1a\n",
                    workspace_path=None,
                    media_type="image/png",
                    width=1024,
                    height=1024,
                    revised_prompt=None,
                )
            ],
            provider=self.provider_name,
            model=self.model_name,
            latency_ms=12.3,
        )

    async def edit(
        self,
        input_image: GeneratedImage,  # noqa: ARG002
        instructions: str,  # noqa: ARG002
        *,
        options: ImageGenOptions | None = None,  # noqa: ARG002
    ) -> GenerationResult:
        raise ImageProviderError(
            "edit unsupported in fake backend",
            context={"reason": "unsupported_option"},
        )


def _make_persona(*, tools: list[str], visual_style: str | None = None) -> Persona:
    """Build a minimal :class:`Persona` carrying the declared allow-list."""
    return Persona(
        persona_id="persona_imgwire_test",
        identity=PersonaIdentity(
            name="Astrid",
            role="assistant",
            background="A helper for image-wiring tests.",
            visual_style=visual_style,
        ),
        tools=tools,
    )


def _make_factory(*, image_backend: _FakeImageBackend | None) -> RuntimeFactory:
    """Construct a :class:`RuntimeFactory` with stubs for everything but
    ``image_backend``.

    ``_build_toolbox`` reads only ``self._core_config``, ``self._sandbox_pool``
    (None here), ``self._workspace_root``, and ``self._image_backend`` — the
    other collaborators (engine, embedder, tier registry, turn log) are not
    exercised by the toolbox-build path. We pass ``None`` casts to satisfy
    the type checker; the runtime never touches them in these tests.
    """
    return RuntimeFactory(
        rls_engine=None,  # type: ignore[arg-type]
        embedder=None,  # type: ignore[arg-type]
        tier_registry=None,  # type: ignore[arg-type]
        turn_log_writer=None,  # type: ignore[arg-type]
        audit_root=Path("/tmp/persona-imgwire-audit"),
        sandbox_pool=None,
        workspace_root=None,
        image_backend=image_backend,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_factory_registers_generate_image_when_backend_present() -> None:
    """With image_backend set, the toolbox registers ``generate_image``.

    This is the regression assertion: pre-hotfix, the toolbox NEVER
    contained ``generate_image`` regardless of whether a backend was
    composed. Post-hotfix, the registration is unconditional when the
    backend is non-None.
    """
    backend = _FakeImageBackend()
    factory = _make_factory(image_backend=backend)
    persona = _make_persona(tools=["generate_image"])

    toolbox = await factory._build_toolbox(persona, scanned_skills=[])

    names = toolbox.names()  # type: ignore[attr-defined]
    assert "generate_image" in names, (
        f"generate_image MUST be in toolbox when image_backend is set; got {names!r}"
    )


@pytest.mark.asyncio
async def test_factory_absents_generate_image_when_no_backend() -> None:
    """With image_backend None, the toolbox does NOT register ``generate_image``.

    Preserves the pre-hotfix shape: deployments without
    ``PERSONA_IMAGEGEN_API_KEY`` (or with a construction-failed backend)
    boot cleanly and the tool is simply absent — the persona's chat path
    sees no advertised ``generate_image`` and would surface a structured
    "not registered" error if the model tried to call it.
    """
    factory = _make_factory(image_backend=None)
    persona = _make_persona(tools=["generate_image"])

    toolbox = await factory._build_toolbox(persona, scanned_skills=[])

    names = toolbox.names()  # type: ignore[attr-defined]
    assert "generate_image" not in names, (
        f"generate_image MUST NOT appear when image_backend is None; got {names!r}"
    )


@pytest.mark.asyncio
async def test_persona_allow_list_gates_generate_image_advertisement() -> None:
    """``generate_image`` is registered but NOT advertised when the persona's
    allow-list excludes it.

    The hotfix registers the tool unconditionally once a backend exists;
    the persona's ``tools`` field is the final gate for what
    ``Toolbox.get_specs()`` advertises to the model. Personas that don't
    declare ``generate_image`` see no advertised tool even when the
    deployment has an image backend composed.
    """
    backend = _FakeImageBackend()
    factory = _make_factory(image_backend=backend)
    # Persona allow-list does NOT include generate_image — only web_search.
    persona = _make_persona(tools=["web_search"])

    toolbox = await factory._build_toolbox(persona, scanned_skills=[])

    # Advertised names should not include generate_image.
    advertised = toolbox.names()  # type: ignore[attr-defined]
    assert "generate_image" not in advertised, (
        f"persona allow-list excluded generate_image; should not advertise; got {advertised!r}"
    )
    # But web_search must still surface (sanity — wires didn't get crossed).
    assert "web_search" in advertised


@pytest.mark.asyncio
async def test_mock_chat_turn_dispatches_generate_image_to_backend() -> None:
    """Simulated model tool call lands on the backend's ``generate`` method.

    This is the missing integration test the hotfix establishes: pre-hotfix,
    no chain of mocks could route a ``generate_image`` ToolCall through
    the runtime path because the tool was never in the toolbox to begin
    with. Post-hotfix, dispatch reaches the backend and returns a
    structured success result.
    """
    backend = _FakeImageBackend()
    factory = _make_factory(image_backend=backend)
    persona = _make_persona(
        tools=["generate_image"],
        visual_style="watercolor, soft edges",
    )

    toolbox = await factory._build_toolbox(persona, scanned_skills=[])

    # The model emits a ToolCall for generate_image — what the runtime would
    # produce inside ``ConversationLoop.turn`` on a real tool-calling chat
    # backend.
    call = ToolCall(
        name="generate_image",
        args={"prompt": "a startup-related illustration", "size": "1024x1024", "count": 1},
        call_id="call_imgwire_1",
    )
    result = await toolbox.dispatch(call)  # type: ignore[attr-defined]

    # Backend received exactly one generate call (the merged prompt suffix
    # is opaque from the factory's POV; we only verify dispatch landed
    # here and the result envelope is structurally correct).
    assert len(backend.calls) == 1
    dispatched_prompt, dispatched_options = backend.calls[0]
    assert "a startup-related illustration" in dispatched_prompt
    assert dispatched_options is not None
    assert dispatched_options.size == "1024x1024"
    assert dispatched_options.count == 1

    # The dispatched result echoes the factory's success envelope.
    assert result.tool_name == "generate_image"
    assert result.is_error is False
    assert result.metadata["outcome"] == "ok"
    assert result.metadata["provider"] == "fake"
    assert result.metadata["model"] == "fake-model-1"
    # The data field carries the per-image metadata the service layer
    # consumes downstream.
    assert result.data is not None
    assert len(result.data["images"]) == 1
    assert result.data["images"][0]["media_type"] == "image/png"


@pytest.mark.asyncio
async def test_visual_style_threaded_through_to_factory() -> None:
    """The persona's ``identity.visual_style`` is threaded into the tool
    factory so the merge_visual_style step applies at dispatch time.

    Regression guard: the hotfix wiring MUST pass
    ``persona_visual_style=persona.identity.visual_style`` — without
    this, every persona's visual_style would be ignored even though the
    backend exists.
    """
    backend = _FakeImageBackend()
    factory = _make_factory(image_backend=backend)
    persona = _make_persona(
        tools=["generate_image"],
        visual_style="pixel-art, 8-bit palette",
    )

    toolbox = await factory._build_toolbox(persona, scanned_skills=[])

    call = ToolCall(
        name="generate_image",
        args={"prompt": "a robot waving", "count": 1},
        call_id="call_imgwire_2",
    )
    _result = await toolbox.dispatch(call)  # type: ignore[attr-defined]

    # The merge step appends visual_style to the prompt at dispatch (D-15-4
    # "identity branch when style is None / empty / user-specifies"). With
    # a pixel-art style and a non-style-specifying prompt, the dispatched
    # prompt should contain the style suffix.
    assert len(backend.calls) == 1
    dispatched_prompt, _ = backend.calls[0]
    assert "pixel-art" in dispatched_prompt, (
        f"visual_style must merge into the prompt; got {dispatched_prompt!r}"
    )


def test_init_accepts_image_backend_keyword() -> None:
    """``RuntimeFactory.__init__`` accepts the new ``image_backend`` keyword.

    Pure-signature regression assertion — the constructor's keyword
    surface must include ``image_backend`` so the lifespan composition
    site in ``app.py`` can wire it. Independent of behaviour; catches
    accidental signature changes that would silently break the wiring
    at boot.
    """
    import inspect

    sig = inspect.signature(RuntimeFactory.__init__)
    assert "image_backend" in sig.parameters, (
        f"RuntimeFactory.__init__ must accept image_backend; got {list(sig.parameters)!r}"
    )
    param = sig.parameters["image_backend"]
    # Keyword-only (matches the rest of the constructor surface).
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    # Defaults to None so existing call-sites without image gen still work.
    assert param.default is None
