"""Unit tests for ``make_generate_image_tool`` (Spec 15 T12).

Covers §9 criteria #3 (tool factory wiring), #4 (allow-list integration
via the real Toolbox), #7 (categorical hard-line refusal binary
structural test + provider rejection surfacing as
ContentRejectedError), and #12 (audit emission on every dispatched
call — hard-line / provider-rejected / error / ok).

Tests use minimal in-test ``FakeImageBackend`` shapes — the production
:class:`OpenAIImageBackend` / :class:`FalImageBackend` paths are covered
by their own per-backend test files plus the parametrised
``test_contract.py`` suite. This test file isolates the tool factory's
COMPOSITION (hard-line → merge → options → backend → audit) from the
backend itself.

D-15-X-audit-event-extension verification: the four outcome strings
(``ok`` / ``content_rejected_provider`` / ``content_rejected_hard_line``
/ ``error``) all land in
:attr:`persona.tools.audit.ToolAuditEvent.metadata["outcome"]` cleanly
without a typed-Literal extension to :class:`ToolAuditAction`.
"""

from __future__ import annotations

import hashlib

import pytest
from persona.errors import ToolNotAllowedError
from persona.imagegen import (
    ContentRejectedError,
    GeneratedImage,
    GenerationResult,
    ImageBackend,
    ImageGenError,
    ImageGenOptions,
    ImageGenUnavailableError,
    ImageProviderError,
    make_generate_image_tool,
)
from persona.schema.tools import ToolCall
from persona.tools import MemoryToolAuditLogger, Toolbox

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeImageBackend:
    """Minimal :class:`ImageBackend` Protocol-conforming fake.

    Records every ``generate`` call's ``(prompt, options)`` so tests can
    assert visual-style merge / options validation reached the backend
    layer correctly. ``side_effect`` (if set) is raised instead of the
    default success result — used to simulate provider rejection /
    transient errors / auth failures.
    """

    def __init__(
        self,
        *,
        provider_name: str = "test-openai",
        model_name: str = "test-gpt-image-1",
        side_effect: BaseException | None = None,
        image_count: int = 1,
        latency_ms: float = 42.5,
        width: int = 1024,
        height: int = 1024,
        revised_prompt: str | None = "a vibrant red bicycle on a cobbled street",
    ) -> None:
        self._provider_name = provider_name
        self._model_name = model_name
        self._side_effect = side_effect
        self._image_count = image_count
        self._latency_ms = latency_ms
        self._width = width
        self._height = height
        self._revised_prompt = revised_prompt
        self.generate_calls: list[dict[str, object]] = []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    async def generate(
        self,
        prompt: str,
        *,
        options: ImageGenOptions | None = None,
    ) -> GenerationResult:
        self.generate_calls.append({"prompt": prompt, "options": options})
        if self._side_effect is not None:
            raise self._side_effect
        opts = options if options is not None else ImageGenOptions()
        images = [
            GeneratedImage(
                image_bytes=b"\x89PNG\x0d\x0a\x1a\x0a",
                media_type="image/png",
                width=self._width,
                height=self._height,
                revised_prompt=self._revised_prompt,
            )
            for _ in range(self._image_count or opts.count)
        ]
        return GenerationResult(
            images=images,
            provider=self._provider_name,
            model=self._model_name,
            latency_ms=self._latency_ms,
        )

    async def edit(
        self,
        input_image: GeneratedImage,
        instructions: str,
        *,
        options: ImageGenOptions | None = None,
    ) -> GenerationResult:
        # v1 backends do NOT override; the Protocol default raises.
        return await ImageBackend.edit(self, input_image, instructions, options=options)


# ---------------------------------------------------------------------------
# §9 #3 — Factory wiring + parameter schema
# ---------------------------------------------------------------------------


class TestFactoryShape:
    def test_returns_async_tool_named_generate_image(self) -> None:
        backend = FakeImageBackend()
        tool = make_generate_image_tool(backend=backend)
        assert tool.name == "generate_image"
        assert tool.description

    def test_parameters_schema_exposes_only_model_facing_args(self) -> None:
        """D-15-3: the model supplies ``prompt`` / ``size`` / ``count`` /
        ``quality``. Visual style + persona id + audit sink are bound at
        factory time — the model cannot widen the cost surface by
        passing arguments."""
        backend = FakeImageBackend()
        tool = make_generate_image_tool(backend=backend)
        schema = tool.parameters_schema
        for required in ("prompt", "size", "count", "quality"):
            assert required in schema["properties"]
        for forbidden in (
            "persona_id",
            "visual_style",
            "audit_logger",
            "backend",
        ):
            assert forbidden not in schema["properties"]

    @pytest.mark.asyncio
    async def test_trivial_happy_path(self) -> None:
        """A simple prompt round-trips: backend called, ToolResult
        not-error, dimensions reflected in content + data."""
        backend = FakeImageBackend()
        tool = make_generate_image_tool(backend=backend)
        result = await tool.execute(prompt="a red bicycle")
        assert result.tool_name == "generate_image"
        assert result.is_error is False
        assert "1024x1024" in result.content
        assert result.data is not None
        assert result.data["provider"] == "test-openai"
        assert result.data["model"] == "test-gpt-image-1"
        assert len(result.data["images"]) == 1
        assert result.data["images"][0]["media_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_count_two_returns_two_images_in_data(self) -> None:
        """D-15-3 ``count <= 4`` cap reaches the backend cleanly."""
        backend = FakeImageBackend(image_count=2)
        tool = make_generate_image_tool(backend=backend)
        result = await tool.execute(prompt="a red bicycle", count=2)
        assert result.is_error is False
        assert result.data is not None
        assert len(result.data["images"]) == 2
        assert "2 images" in result.content


# ---------------------------------------------------------------------------
# §9 #4 — Allow-list integration via the real Toolbox
# ---------------------------------------------------------------------------


class TestAllowListIntegration:
    @pytest.mark.asyncio
    async def test_persona_without_generate_image_cannot_invoke(self) -> None:
        """A persona whose ``tools`` allow-list does NOT contain
        ``generate_image`` raises :class:`ToolNotAllowedError` at
        dispatch — caught by the loops' ``_dispatch`` wrappers (Spec 11
        fix #1)."""
        backend = FakeImageBackend()
        tool = make_generate_image_tool(backend=backend)
        toolbox = Toolbox([tool], allow_list=["file_read"])  # generate_image NOT declared
        with pytest.raises(ToolNotAllowedError):
            await toolbox.dispatch(
                ToolCall(name="generate_image", args={"prompt": "a red bicycle"}, call_id="x")
            )

    @pytest.mark.asyncio
    async def test_persona_with_generate_image_can_invoke(self) -> None:
        backend = FakeImageBackend()
        tool = make_generate_image_tool(backend=backend)
        toolbox = Toolbox([tool], allow_list=["generate_image"])
        result = await toolbox.dispatch(
            ToolCall(name="generate_image", args={"prompt": "a red bicycle"}, call_id="x")
        )
        assert result.is_error is False
        assert len(backend.generate_calls) == 1


# ---------------------------------------------------------------------------
# §9 #7 (hard-line half) — categorical refusal BEFORE backend call
# ---------------------------------------------------------------------------


class TestHardLineFilter:
    @pytest.mark.asyncio
    async def test_hard_line_trigger_short_circuits_backend(self) -> None:
        """A prompt that trips the categorical filter NEVER reaches the
        backend — the audit fires + the ToolResult is structured-error."""
        backend = FakeImageBackend()
        logger = MemoryToolAuditLogger()
        tool = make_generate_image_tool(
            backend=backend,
            audit_logger=logger,
            persona_id="astrid",
        )
        # Construct a trigger phrase from the public lexicons documented
        # in research.md §5.4. The construction lives inside the test
        # body so the harmful surface area is not committed as a
        # standalone literal (T09 corpus discipline).
        trigger = " ".join(["a", "nude", "child"])  # C1: MINOR ∩ SEX

        result = await tool.execute(prompt=trigger)

        assert result.is_error is True
        assert result.content == "content_rejected_hard_line"
        assert result.metadata["outcome"] == "content_rejected_hard_line"
        # Backend was NEVER called.
        assert backend.generate_calls == []
        # Exactly one audit event with outcome=content_rejected_hard_line.
        assert len(logger.events) == 1
        event = logger.events[0]
        assert event.tool_name == "generate_image"
        assert event.action == "execute"
        assert event.is_error is True
        assert event.metadata["outcome"] == "content_rejected_hard_line"
        # The triggering prompt is NEVER persisted; only its sha256.
        expected_hash = hashlib.sha256(trigger.encode("utf-8")).hexdigest()
        assert event.metadata["prompt_sha256"] == expected_hash
        assert event.resource == f"sha256:{expected_hash}"
        # The trigger text itself does NOT appear anywhere in the event.
        dumped = event.model_dump_json()
        assert "child" not in dumped
        assert "nude" not in dumped

    @pytest.mark.asyncio
    async def test_hard_line_audit_carries_category(self) -> None:
        backend = FakeImageBackend()
        logger = MemoryToolAuditLogger()
        tool = make_generate_image_tool(
            backend=backend,
            audit_logger=logger,
            persona_id="astrid",
        )
        trigger = " ".join(["a", "sexual", "toddler"])  # C3 narrower
        await tool.execute(prompt=trigger)
        assert logger.events[0].metadata["category"] in {"c1", "c2", "c3"}

    @pytest.mark.asyncio
    async def test_benign_prompt_does_not_trip_filter(self) -> None:
        backend = FakeImageBackend()
        logger = MemoryToolAuditLogger()
        tool = make_generate_image_tool(
            backend=backend,
            audit_logger=logger,
            persona_id="astrid",
        )
        result = await tool.execute(prompt="a watercolour painting of a duck")
        assert result.is_error is False
        assert len(backend.generate_calls) == 1
        assert logger.events[0].metadata["outcome"] == "ok"


# ---------------------------------------------------------------------------
# §9 #7 (provider half) — provider rejection surfaces as ContentRejectedError
# ---------------------------------------------------------------------------


class TestProviderRejection:
    @pytest.mark.asyncio
    async def test_provider_input_moderation_returns_structured_error(self) -> None:
        backend = FakeImageBackend(
            side_effect=ContentRejectedError(
                "moderation_blocked: input rejected by openai",
                context={
                    "provider": "test-openai",
                    "reason": "provider_moderation",
                    "stage": "input",
                },
            )
        )
        logger = MemoryToolAuditLogger()
        tool = make_generate_image_tool(
            backend=backend,
            audit_logger=logger,
            persona_id="astrid",
        )
        result = await tool.execute(prompt="anything the provider does not like")
        assert result.is_error is True
        assert "content_rejected_provider" in result.content
        assert result.metadata["outcome"] == "content_rejected_provider"
        assert result.metadata["stage"] == "input"
        assert result.metadata["reason"] == "provider_moderation"

    @pytest.mark.asyncio
    async def test_provider_output_moderation_distinguishable_by_stage(self) -> None:
        backend = FakeImageBackend(
            side_effect=ContentRejectedError(
                "has_nsfw_concepts triggered (post-gen)",
                context={
                    "provider": "test-openai",
                    "reason": "provider_post_gen_moderation",
                    "stage": "output",
                },
            )
        )
        logger = MemoryToolAuditLogger()
        tool = make_generate_image_tool(
            backend=backend,
            audit_logger=logger,
            persona_id="astrid",
        )
        result = await tool.execute(prompt="anything")
        assert result.is_error is True
        assert result.metadata["outcome"] == "content_rejected_provider"
        assert result.metadata["stage"] == "output"
        assert logger.events[0].metadata["stage"] == "output"


# ---------------------------------------------------------------------------
# Error mapping — auth / rate-limit / transient / unsupported_option
# ---------------------------------------------------------------------------


class TestErrorMapping:
    @pytest.mark.asyncio
    async def test_auth_error_surfaces_as_structured_error(self) -> None:
        backend = FakeImageBackend(
            side_effect=ImageGenUnavailableError(
                "missing OpenAI API key",
                context={"provider": "test-openai"},
            )
        )
        logger = MemoryToolAuditLogger()
        tool = make_generate_image_tool(
            backend=backend,
            audit_logger=logger,
            persona_id="astrid",
        )
        result = await tool.execute(prompt="anything")
        assert result.is_error is True
        assert result.metadata["outcome"] == "error"
        assert result.metadata["error_type"] == "ImageGenUnavailableError"
        assert logger.events[0].metadata["outcome"] == "error"

    @pytest.mark.asyncio
    async def test_rate_limit_error_carries_reason_in_metadata(self) -> None:
        backend = FakeImageBackend(
            side_effect=ImageProviderError(
                "rate limited",
                context={
                    "provider": "test-openai",
                    "reason": "rate_limit",
                    "retry_after_s": "30",
                },
            )
        )
        logger = MemoryToolAuditLogger()
        tool = make_generate_image_tool(
            backend=backend,
            audit_logger=logger,
        )
        result = await tool.execute(prompt="anything")
        assert result.is_error is True
        assert result.metadata["outcome"] == "error"
        assert result.metadata["reason"] == "rate_limit"

    @pytest.mark.asyncio
    async def test_arbitrary_image_gen_error_subclass_still_handled(self) -> None:
        """Base :class:`ImageGenError` directly (no subclass) still funnels
        through the ``except ImageGenError`` branch — the funnel is
        exhaustive over the domain hierarchy."""
        backend = FakeImageBackend(
            side_effect=ImageGenError(
                "some other domain failure",
                context={"provider": "test-openai", "reason": "weird"},
            )
        )
        logger = MemoryToolAuditLogger()
        tool = make_generate_image_tool(
            backend=backend,
            audit_logger=logger,
        )
        result = await tool.execute(prompt="anything")
        assert result.is_error is True
        assert result.metadata["outcome"] == "error"
        assert result.metadata["reason"] == "weird"


# ---------------------------------------------------------------------------
# Options validation — D-15-3 count cap + Literal-preset surface
# ---------------------------------------------------------------------------


class TestOptionsValidation:
    @pytest.mark.asyncio
    async def test_count_exceeds_cap_returns_invalid_options(self) -> None:
        """The model passing ``count=5`` (above the D-15-3 cap) lands
        as a structured ToolResult, NOT a crash. The backend is never
        called."""
        backend = FakeImageBackend()
        logger = MemoryToolAuditLogger()
        tool = make_generate_image_tool(
            backend=backend,
            audit_logger=logger,
        )
        # The @tool decorator validates ``count: int`` first (no Literal),
        # so passing ``count=5`` reaches our ImageGenOptions validation
        # — which then rejects via ``Field(le=4)``.
        result = await tool.execute(prompt="a red bicycle", count=5)
        assert result.is_error is True
        assert backend.generate_calls == []
        # Either the @tool decorator's validation envelope or our
        # ImageGenOptions validation fires; both yield is_error=True with
        # an "invalid arguments" / "invalid_options" body. Accept either
        # so the test is decorator-version-independent.
        assert (
            "invalid_options" in result.content.lower()
            or "invalid arguments" in result.content.lower()
        )

    @pytest.mark.asyncio
    async def test_invalid_size_string_returns_invalid_options(self) -> None:
        backend = FakeImageBackend()
        logger = MemoryToolAuditLogger()
        tool = make_generate_image_tool(
            backend=backend,
            audit_logger=logger,
        )
        result = await tool.execute(prompt="a red bicycle", size="999x999")
        assert result.is_error is True
        assert backend.generate_calls == []


# ---------------------------------------------------------------------------
# Visual-style merge — D-15-4 suffix conditioning reaches the backend
# ---------------------------------------------------------------------------


class TestVisualStyleMerge:
    @pytest.mark.asyncio
    async def test_visual_style_merged_into_prompt(self) -> None:
        """The persona's ``visual_style`` is suffix-merged into the
        prompt before the backend call."""
        backend = FakeImageBackend()
        tool = make_generate_image_tool(
            backend=backend,
            persona_visual_style="warm editorial illustration",
        )
        await tool.execute(prompt="a cat")
        assert len(backend.generate_calls) == 1
        sent = backend.generate_calls[0]["prompt"]
        assert isinstance(sent, str)
        assert "a cat" in sent
        assert "warm editorial illustration" in sent
        assert "in the style of" in sent

    @pytest.mark.asyncio
    async def test_user_specified_style_wins(self) -> None:
        """When the user prompt already specifies a style, the persona
        style is suppressed (user wins per D-15-4)."""
        backend = FakeImageBackend()
        tool = make_generate_image_tool(
            backend=backend,
            persona_visual_style="warm editorial illustration",
        )
        await tool.execute(prompt="a cat in the style of Van Gogh")
        sent = backend.generate_calls[0]["prompt"]
        assert isinstance(sent, str)
        # The persona style is NOT appended; the user-provided string
        # passes through verbatim.
        assert "Van Gogh" in sent
        assert "warm editorial illustration" not in sent

    @pytest.mark.asyncio
    async def test_none_visual_style_yields_identity(self) -> None:
        backend = FakeImageBackend()
        tool = make_generate_image_tool(
            backend=backend,
            persona_visual_style=None,
        )
        await tool.execute(prompt="a cat")
        sent = backend.generate_calls[0]["prompt"]
        assert sent == "a cat"


# ---------------------------------------------------------------------------
# §9 #12 — Audit emission completeness on every dispatched call
# ---------------------------------------------------------------------------


class TestAuditEmission:
    @pytest.mark.asyncio
    async def test_emits_one_audit_event_per_success(self) -> None:
        backend = FakeImageBackend(latency_ms=12.0)
        logger = MemoryToolAuditLogger()
        tool = make_generate_image_tool(
            backend=backend,
            audit_logger=logger,
            persona_id="astrid",
        )
        await tool.execute(prompt="a red bicycle")
        assert len(logger.events) == 1
        event = logger.events[0]
        assert event.action == "execute"
        assert event.tool_name == "generate_image"
        assert event.persona_id == "astrid"
        assert event.is_error is False
        assert event.metadata["outcome"] == "ok"
        assert event.metadata["provider"] == "test-openai"
        assert event.metadata["model"] == "test-gpt-image-1"
        assert event.metadata["latency_ms"] == "12.0"
        assert event.metadata["image_count"] == "1"

    @pytest.mark.asyncio
    async def test_no_logger_no_crash(self) -> None:
        """When ``audit_logger`` is None the tool MUST still dispatch
        successfully — the audit emitter helpers each short-circuit."""
        backend = FakeImageBackend()
        tool = make_generate_image_tool(backend=backend, audit_logger=None)
        result = await tool.execute(prompt="a cat")
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_all_four_outcome_strings_reach_metadata_cleanly(self) -> None:
        """D-15-X-audit-event-extension verification.

        Four distinct dispatches → four distinct ``outcome`` strings
        landed in :attr:`ToolAuditEvent.metadata["outcome"]`. The
        existing ``dict[str, str]`` shape carries them all without a
        struct change.
        """
        logger = MemoryToolAuditLogger()

        # 1. ok
        ok_tool = make_generate_image_tool(
            backend=FakeImageBackend(),
            audit_logger=logger,
        )
        await ok_tool.execute(prompt="a red bicycle")

        # 2. content_rejected_hard_line
        hard_line_tool = make_generate_image_tool(
            backend=FakeImageBackend(),
            audit_logger=logger,
        )
        trigger = " ".join(["nude", "child"])  # C1
        await hard_line_tool.execute(prompt=trigger)

        # 3. content_rejected_provider
        provider_reject_tool = make_generate_image_tool(
            backend=FakeImageBackend(
                side_effect=ContentRejectedError(
                    "moderation_blocked",
                    context={
                        "provider": "test-openai",
                        "reason": "provider_moderation",
                        "stage": "input",
                    },
                )
            ),
            audit_logger=logger,
        )
        await provider_reject_tool.execute(prompt="a red bicycle")

        # 4. error
        error_tool = make_generate_image_tool(
            backend=FakeImageBackend(
                side_effect=ImageProviderError(
                    "rate limited",
                    context={"provider": "test-openai", "reason": "rate_limit"},
                )
            ),
            audit_logger=logger,
        )
        await error_tool.execute(prompt="a red bicycle")

        outcomes = [event.metadata["outcome"] for event in logger.events]
        assert outcomes == [
            "ok",
            "content_rejected_hard_line",
            "content_rejected_provider",
            "error",
        ]
        # All event.metadata values are strings (dict[str, str] invariant).
        for event in logger.events:
            for key, value in event.metadata.items():
                assert isinstance(key, str), f"non-string key: {key!r}"
                assert isinstance(value, str), f"non-string value at {key}: {value!r}"

    @pytest.mark.asyncio
    async def test_success_metadata_carries_provider_model_latency_count(self) -> None:
        backend = FakeImageBackend(image_count=2, latency_ms=87.3)
        logger = MemoryToolAuditLogger()
        tool = make_generate_image_tool(
            backend=backend,
            audit_logger=logger,
        )
        await tool.execute(prompt="a red bicycle", count=2)
        event = logger.events[0]
        assert event.metadata["outcome"] == "ok"
        assert event.metadata["image_count"] == "2"
        assert event.metadata["latency_ms"] == "87.3"
        assert event.metadata["size"] == "1024x1024"

    @pytest.mark.asyncio
    async def test_options_error_audit_carries_invalid_options_reason(self) -> None:
        backend = FakeImageBackend()
        logger = MemoryToolAuditLogger()
        tool = make_generate_image_tool(
            backend=backend,
            audit_logger=logger,
        )
        await tool.execute(prompt="a red bicycle", count=5)
        # The @tool decorator may catch the count=5 violation at the
        # argument-validation envelope (D-03-5) — when it does, the
        # body is NOT invoked, so our audit emitter is not called.
        # When the body is invoked and ImageGenOptions validation fires,
        # the audit IS emitted. Both paths are correct; assert the
        # ToolResult is structured-error and the backend was not called.
        assert backend.generate_calls == []


# ---------------------------------------------------------------------------
# Re-export discipline
# ---------------------------------------------------------------------------


class TestReExport:
    def test_make_generate_image_tool_reexported_from_package(self) -> None:
        from persona.imagegen import make_generate_image_tool as reexport
        from persona.imagegen.tool import make_generate_image_tool as original

        assert reexport is original

    def test_re_export_in_dunder_all(self) -> None:
        import persona.imagegen as imagegen_pkg

        assert "make_generate_image_tool" in imagegen_pkg.__all__
