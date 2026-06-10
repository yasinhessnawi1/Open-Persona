"""Tests for :class:`persona.backends.multi_model.MultiModelChatBackend`.

Covers Spec 20 T15 deliverables — the cross-provider ordered-fallback
ChatBackend wrapper that composes N concrete backends per:

* **D-20-9** — three-bucket classifier
  (``RETRY-THEN-FALLBACK`` / ``FALLBACK-NO-RETRY`` / ``SURFACE``).
* **D-20-10** — N=1 same-model retry, 200ms ± jitter sleep, then fallback.
* **D-20-12** — cross-provider :class:`AuthenticationError` skip-and-fallback
  with structured WARNING log.
* **D-20-15** — runtime :class:`ProviderCredentialMissingError` →
  FALLBACK-NO-RETRY.
* **D-20-16** — :class:`AllModelsFailedError` slots under
  :class:`PersonaError` (NOT :class:`ProviderError`).

The tests use lightweight scripted :class:`ChatBackend` doubles rather than
real provider SDKs — the wrapper does not care which backend it talks to.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from persona.backends.errors import (
    AllModelsFailedError,
    AuthenticationError,
    BackendTimeoutError,
    ModelNotFoundError,
    ProviderCredentialMissingError,
    ProviderError,
    RateLimitError,
)
from persona.backends.multi_model import (
    AttemptRecord,
    MultiModelChatBackend,
)
from persona.backends.protocol import ChatBackend
from persona.backends.types import ChatResponse, StreamChunk, TokenUsage
from persona.errors import PersonaError
from persona.schema.conversation import ConversationMessage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from persona.backends.types import ToolSpec

# --------------------------------------------------------------------------- #
# Scripted ChatBackend double
# --------------------------------------------------------------------------- #


class _ScriptedBackend:
    """A ChatBackend that returns or raises whatever it's scripted with.

    ``script`` is a list of "outcomes" consumed one per :meth:`chat` (or
    :meth:`chat_stream`) call. Each outcome is either:

    * an :class:`Exception` instance to raise, or
    * a :class:`ChatResponse` to return (for ``chat``), or
    * a list of :class:`StreamChunk` (or a partial list ending in an
      :class:`Exception`) for ``chat_stream``.

    When the script is exhausted the backend raises :class:`IndexError` so
    bugs in the wrapper that over-invoke a backend surface immediately.
    """

    def __init__(
        self,
        provider: str,
        model: str,
        script: list[object],
        *,
        supports_native_tools: bool = True,
        supports_vision: bool = False,
    ) -> None:
        self._provider = provider
        self._model = model
        self._script = list(script)
        self._supports_native_tools = supports_native_tools
        self._supports_vision = supports_vision
        self.call_count = 0
        self.stream_call_count = 0

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def supports_native_tools(self) -> bool:
        return self._supports_native_tools

    @property
    def supports_vision(self) -> bool:
        return self._supports_vision

    async def chat(
        self,
        messages: list[ConversationMessage],  # noqa: ARG002
        *,
        tools: list[ToolSpec] | None = None,  # noqa: ARG002
        temperature: float = 0.0,  # noqa: ARG002
        max_tokens: int = 4096,  # noqa: ARG002
        stop: list[str] | None = None,  # noqa: ARG002
    ) -> ChatResponse:
        self.call_count += 1
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, ChatResponse)
        return outcome

    async def chat_stream(
        self,
        messages: list[ConversationMessage],  # noqa: ARG002
        *,
        tools: list[ToolSpec] | None = None,  # noqa: ARG002
        temperature: float = 0.0,  # noqa: ARG002
        max_tokens: int = 4096,  # noqa: ARG002
        stop: list[str] | None = None,  # noqa: ARG002
    ) -> AsyncIterator[StreamChunk]:
        self.stream_call_count += 1
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, list)
        for item in outcome:
            if isinstance(item, Exception):
                raise item
            assert isinstance(item, StreamChunk)
            yield item


def _ok_response(provider: str, model: str, content: str = "ok") -> ChatResponse:
    return ChatResponse(
        content=content,
        usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        model=model,
        provider=provider,
        latency_ms=1.0,
    )


def _user_msg() -> ConversationMessage:
    return ConversationMessage(role="user", content="hi", created_at=datetime.now(UTC))


# --------------------------------------------------------------------------- #
# Construction invariants
# --------------------------------------------------------------------------- #


class TestConstruction:
    def test_empty_backends_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="at least one backend"):
            MultiModelChatBackend([])

    def test_single_backend_is_valid(self) -> None:
        backend = _ScriptedBackend("openai", "gpt-4o", [_ok_response("openai", "gpt-4o")])
        wrapper = MultiModelChatBackend([backend])
        assert wrapper.provider_name == "openai"
        assert wrapper.model_name == "gpt-4o"

    def test_is_chat_backend_protocol_member(self) -> None:
        backend = _ScriptedBackend("openai", "gpt-4o", [])
        wrapper = MultiModelChatBackend([backend])
        assert isinstance(wrapper, ChatBackend)

    def test_capability_properties_permissive_any(self) -> None:
        """``supports_native_tools`` + ``supports_vision`` use ``any()``
        semantics (permissive ceiling), NOT ``all()`` (conservative floor).

        Earlier ``all(...)`` semantics broke the runtime tool-call protocol on
        mixed-capability chains (production bug 2026-06-10 19:44 UTC): the
        wrapper claimed False when one slot's specific model wasn't in the
        native-tools allow-list; the runtime skipped the
        ``assistant_with_tool_calls`` append; ``format_tool_result`` still
        emitted ``role="tool"``; orphaned-tool-message on the next round →
        DeepSeek 400. Fix: at call time the wrapper dispatches to whichever
        backend serves the request — if ANY supports the capability, the
        wrapper can deliver it (via fallback if needed). The active backend's
        own property still gates whether THAT backend uses native vs shim.
        """
        a = _ScriptedBackend(
            "openai", "gpt-4o", [], supports_native_tools=True, supports_vision=True
        )
        b = _ScriptedBackend(
            "anthropic", "claude", [], supports_native_tools=False, supports_vision=True
        )
        wrapper = MultiModelChatBackend([a, b])
        # ANY supports native → wrapper reports True (was False under all()).
        assert wrapper.supports_native_tools is True
        # ANY supports vision → wrapper reports True.
        assert wrapper.supports_vision is True

    def test_capability_properties_all_false_when_none_support(self) -> None:
        """When NO backend supports the capability, the wrapper reports False.

        Regression gate: ``any([False, False])`` is False — the wrapper
        correctly fail-loud propagates this so callers requesting an
        unsupported capability hit fail-loud at the wrapper, not a confusing
        400 from one specific backend mid-fallback.
        """
        a = _ScriptedBackend(
            "openai", "gpt-4o", [], supports_native_tools=False, supports_vision=False
        )
        b = _ScriptedBackend(
            "anthropic", "claude", [], supports_native_tools=False, supports_vision=False
        )
        wrapper = MultiModelChatBackend([a, b])
        assert wrapper.supports_native_tools is False
        assert wrapper.supports_vision is False


# --------------------------------------------------------------------------- #
# Happy paths
# --------------------------------------------------------------------------- #


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_single_backend_success(self) -> None:
        backend = _ScriptedBackend("openai", "gpt-4o", [_ok_response("openai", "gpt-4o")])
        wrapper = MultiModelChatBackend([backend])
        response = await wrapper.chat([_user_msg()])
        assert response.provider == "openai"
        assert backend.call_count == 1

    @pytest.mark.asyncio
    async def test_two_backends_primary_succeeds_no_fallback(self) -> None:
        primary = _ScriptedBackend(
            "openai", "gpt-4o", [_ok_response("openai", "gpt-4o", "primary")]
        )
        secondary = _ScriptedBackend(
            "anthropic", "claude", [_ok_response("anthropic", "claude", "secondary")]
        )
        wrapper = MultiModelChatBackend([primary, secondary])
        response = await wrapper.chat([_user_msg()])
        assert response.content == "primary"
        assert primary.call_count == 1
        assert secondary.call_count == 0

    @pytest.mark.asyncio
    async def test_rate_limit_then_success_retries_same_model(self) -> None:
        """RateLimitError without Retry-After → N=1 retry, primary succeeds on retry."""
        primary = _ScriptedBackend(
            "openai",
            "gpt-4o",
            [
                RateLimitError("rl", context={"provider": "openai"}),
                _ok_response("openai", "gpt-4o", "after-retry"),
            ],
        )
        secondary = _ScriptedBackend("anthropic", "claude", [_ok_response("anthropic", "claude")])
        wrapper = MultiModelChatBackend([primary, secondary])
        response = await wrapper.chat([_user_msg()])
        assert response.content == "after-retry"
        assert primary.call_count == 2
        assert secondary.call_count == 0


# --------------------------------------------------------------------------- #
# D-20-9 RETRY-THEN-FALLBACK bucket
# --------------------------------------------------------------------------- #


class TestRetryThenFallback:
    @pytest.mark.asyncio
    async def test_timeout_exhausts_retry_then_falls_back(self) -> None:
        primary = _ScriptedBackend(
            "openai",
            "gpt-4o",
            [
                BackendTimeoutError("t1", context={"provider": "openai"}),
                BackendTimeoutError("t2", context={"provider": "openai"}),
            ],
        )
        secondary = _ScriptedBackend("anthropic", "claude", [_ok_response("anthropic", "claude")])
        wrapper = MultiModelChatBackend([primary, secondary])
        response = await wrapper.chat([_user_msg()])
        assert response.provider == "anthropic"
        assert primary.call_count == 2  # original + 1 retry
        assert secondary.call_count == 1

    @pytest.mark.asyncio
    async def test_provider_error_5xx_retry_then_fallback(self) -> None:
        primary = _ScriptedBackend(
            "openai",
            "gpt-4o",
            [
                ProviderError("500", context={"provider": "openai", "status_code": "500"}),
                ProviderError("500", context={"provider": "openai", "status_code": "500"}),
            ],
        )
        secondary = _ScriptedBackend("anthropic", "claude", [_ok_response("anthropic", "claude")])
        wrapper = MultiModelChatBackend([primary, secondary])
        response = await wrapper.chat([_user_msg()])
        assert response.provider == "anthropic"
        assert primary.call_count == 2

    @pytest.mark.asyncio
    async def test_max_retries_zero_disables_retry(self) -> None:
        primary = _ScriptedBackend(
            "openai",
            "gpt-4o",
            [BackendTimeoutError("t1", context={"provider": "openai"})],
        )
        secondary = _ScriptedBackend("anthropic", "claude", [_ok_response("anthropic", "claude")])
        wrapper = MultiModelChatBackend([primary, secondary], max_retries_per_backend=0)
        response = await wrapper.chat([_user_msg()])
        assert response.provider == "anthropic"
        assert primary.call_count == 1


# --------------------------------------------------------------------------- #
# D-20-9 FALLBACK-NO-RETRY bucket
# --------------------------------------------------------------------------- #


class TestFallbackNoRetry:
    @pytest.mark.asyncio
    async def test_rate_limit_with_long_retry_after_skips_retry(self) -> None:
        """Retry-After=10s > 2s cutoff → FALLBACK-NO-RETRY; secondary tried."""
        primary = _ScriptedBackend(
            "openai",
            "gpt-4o",
            [RateLimitError("rl", context={"provider": "openai", "retry_after_s": "10"})],
        )
        secondary = _ScriptedBackend("anthropic", "claude", [_ok_response("anthropic", "claude")])
        wrapper = MultiModelChatBackend([primary, secondary])
        response = await wrapper.chat([_user_msg()])
        assert response.provider == "anthropic"
        assert primary.call_count == 1  # no retry

    @pytest.mark.asyncio
    async def test_rate_limit_with_credits_expired_reason_no_retry(self) -> None:
        primary = _ScriptedBackend(
            "openai",
            "gpt-4o",
            [RateLimitError("rl", context={"provider": "openai", "reason": "credits_expired"})],
        )
        secondary = _ScriptedBackend("anthropic", "claude", [_ok_response("anthropic", "claude")])
        wrapper = MultiModelChatBackend([primary, secondary])
        response = await wrapper.chat([_user_msg()])
        assert response.provider == "anthropic"
        assert primary.call_count == 1

    @pytest.mark.asyncio
    async def test_model_not_found_falls_back_no_retry(self) -> None:
        primary = _ScriptedBackend(
            "openai",
            "gpt-4o",
            [ModelNotFoundError("nope", context={"provider": "openai", "model": "gpt-4o"})],
        )
        secondary = _ScriptedBackend("anthropic", "claude", [_ok_response("anthropic", "claude")])
        wrapper = MultiModelChatBackend([primary, secondary])
        response = await wrapper.chat([_user_msg()])
        assert response.provider == "anthropic"
        assert primary.call_count == 1

    @pytest.mark.asyncio
    async def test_authentication_error_skip_and_fallback_with_warning(self) -> None:
        """D-20-12 — cross-provider auth → SKIP-AND-FALLBACK + WARNING log."""
        from loguru import logger as _loguru_logger

        captured: list[str] = []
        sink_id = _loguru_logger.add(
            lambda msg: captured.append(str(msg)),
            level="WARNING",
            serialize=True,
        )
        try:
            primary = _ScriptedBackend(
                "openai",
                "gpt-4o",
                [AuthenticationError("bad key", context={"provider": "openai"})],
            )
            secondary = _ScriptedBackend(
                "anthropic", "claude", [_ok_response("anthropic", "claude")]
            )
            wrapper = MultiModelChatBackend([primary, secondary], tier_name="frontier")
            response = await wrapper.chat([_user_msg()])
        finally:
            _loguru_logger.remove(sink_id)
        assert response.provider == "anthropic"
        assert primary.call_count == 1
        # WARNING log must mention the fallback engagement.
        joined = "".join(captured)
        assert "fallback" in joined.lower()
        assert "AuthenticationError" in joined

    @pytest.mark.asyncio
    async def test_provider_credential_missing_runtime_falls_back(self) -> None:
        """D-20-15 runtime path — resolver did not catch this slot earlier."""
        primary = _ScriptedBackend(
            "nvidia",
            "nemotron",
            [
                ProviderCredentialMissingError(
                    "missing",
                    context={"provider": "nvidia", "env_var": "PERSONA_NVIDIA_API_KEY"},
                )
            ],
        )
        secondary = _ScriptedBackend("anthropic", "claude", [_ok_response("anthropic", "claude")])
        wrapper = MultiModelChatBackend([primary, secondary])
        response = await wrapper.chat([_user_msg()])
        assert response.provider == "anthropic"
        assert primary.call_count == 1


# --------------------------------------------------------------------------- #
# D-20-9 SURFACE bucket
# --------------------------------------------------------------------------- #


class TestSurface:
    @pytest.mark.asyncio
    async def test_content_policy_violation_surfaces_no_fallback(self) -> None:
        primary = _ScriptedBackend(
            "openai",
            "gpt-4o",
            [
                ProviderError(
                    "blocked",
                    context={
                        "provider": "openai",
                        "status_code": "400",
                        "reason": "content_policy_violation",
                    },
                )
            ],
        )
        secondary = _ScriptedBackend("anthropic", "claude", [_ok_response("anthropic", "claude")])
        wrapper = MultiModelChatBackend([primary, secondary])
        with pytest.raises(ProviderError):
            await wrapper.chat([_user_msg()])
        assert primary.call_count == 1
        assert secondary.call_count == 0

    @pytest.mark.asyncio
    async def test_bad_request_400_generic_surfaces(self) -> None:
        primary = _ScriptedBackend(
            "openai",
            "gpt-4o",
            [ProviderError("bad", context={"provider": "openai", "status_code": "400"})],
        )
        secondary = _ScriptedBackend("anthropic", "claude", [_ok_response("anthropic", "claude")])
        wrapper = MultiModelChatBackend([primary, secondary])
        with pytest.raises(ProviderError):
            await wrapper.chat([_user_msg()])
        assert secondary.call_count == 0

    @pytest.mark.asyncio
    async def test_non_persona_error_surfaces_as_programmer_bug(self) -> None:
        primary = _ScriptedBackend("openai", "gpt-4o", [TypeError("bug")])
        secondary = _ScriptedBackend("anthropic", "claude", [_ok_response("anthropic", "claude")])
        wrapper = MultiModelChatBackend([primary, secondary])
        with pytest.raises(TypeError):
            await wrapper.chat([_user_msg()])
        assert secondary.call_count == 0


# --------------------------------------------------------------------------- #
# Exhaustion → AllModelsFailedError (D-20-16)
# --------------------------------------------------------------------------- #


class TestExhaustion:
    @pytest.mark.asyncio
    async def test_all_three_rate_limit_raises_all_models_failed(self) -> None:
        b1 = _ScriptedBackend(
            "openai",
            "gpt-4o",
            [
                RateLimitError("rl", context={"provider": "openai", "retry_after_s": "10"}),
            ],
        )
        b2 = _ScriptedBackend(
            "anthropic",
            "claude",
            [
                RateLimitError("rl", context={"provider": "anthropic", "retry_after_s": "10"}),
            ],
        )
        b3 = _ScriptedBackend(
            "nvidia",
            "nemotron",
            [
                RateLimitError("rl", context={"provider": "nvidia", "retry_after_s": "10"}),
            ],
        )
        wrapper = MultiModelChatBackend([b1, b2, b3], tier_name="frontier")
        with pytest.raises(AllModelsFailedError) as excinfo:
            await wrapper.chat([_user_msg()])
        err = excinfo.value
        assert isinstance(err, PersonaError)
        assert err.context["tier"] == "frontier"
        assert err.context["attempt_count"] == "3"
        assert err.context["final_error_class"] == "RateLimitError"
        # Each backend hit once (no retry — Retry-After=10s skips retry).
        assert b1.call_count == 1
        assert b2.call_count == 1
        assert b3.call_count == 1

    @pytest.mark.asyncio
    async def test_all_models_failed_is_not_provider_error_d20_16(self) -> None:
        """D-20-16 partition — AllModelsFailedError is PersonaError, NOT ProviderError."""
        backend = _ScriptedBackend(
            "openai",
            "gpt-4o",
            [RateLimitError("rl", context={"provider": "openai", "retry_after_s": "10"})],
        )
        wrapper = MultiModelChatBackend([backend])
        with pytest.raises(AllModelsFailedError) as excinfo:
            await wrapper.chat([_user_msg()])
        # MUST NOT be catchable as ProviderError.
        assert not isinstance(excinfo.value, ProviderError)
        assert isinstance(excinfo.value, PersonaError)


# --------------------------------------------------------------------------- #
# AttemptRecord shape
# --------------------------------------------------------------------------- #


class TestAttemptRecord:
    def test_attempt_record_is_frozen_dataclass(self) -> None:
        rec = AttemptRecord(
            provider="openai",
            model="gpt-4o",
            last_error_class="RateLimitError",
            last_error_status_code=429,
            retried_same_model=True,
        )
        with pytest.raises(FrozenInstanceError):
            rec.provider = "anthropic"  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_attempts_carry_retried_flag(self) -> None:
        primary = _ScriptedBackend(
            "openai",
            "gpt-4o",
            [
                BackendTimeoutError("t1", context={"provider": "openai"}),
                BackendTimeoutError("t2", context={"provider": "openai"}),
            ],
        )
        secondary = _ScriptedBackend(
            "anthropic",
            "claude",
            [RateLimitError("rl", context={"provider": "anthropic", "retry_after_s": "10"})],
        )
        wrapper = MultiModelChatBackend([primary, secondary])
        with pytest.raises(AllModelsFailedError) as excinfo:
            await wrapper.chat([_user_msg()])
        # attempts_json carries the retried flag — verify by substring.
        attempts_json = excinfo.value.context["attempts_json"]
        assert "'retried_same_model': True" in attempts_json  # primary retried
        assert "'retried_same_model': False" in attempts_json  # secondary did not


# --------------------------------------------------------------------------- #
# Streaming behaviour
# --------------------------------------------------------------------------- #


def _chunk(delta: str, *, is_final: bool = False) -> StreamChunk:
    usage = TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2) if is_final else None
    return StreamChunk(delta=delta, is_final=is_final, usage=usage)


class TestStreaming:
    @pytest.mark.asyncio
    async def test_stream_fallback_before_first_chunk(self) -> None:
        """Error BEFORE any chunk yielded → wrapper falls back per D-20-9."""
        primary = _ScriptedBackend(
            "openai",
            "gpt-4o",
            [BackendTimeoutError("t1", context={"provider": "openai"})] * 2,
        )
        secondary = _ScriptedBackend(
            "anthropic",
            "claude",
            [[_chunk("hello"), _chunk("", is_final=True)]],
        )
        wrapper = MultiModelChatBackend([primary, secondary])
        chunks: list[StreamChunk] = []
        async for c in wrapper.chat_stream([_user_msg()]):
            chunks.append(c)
        assert len(chunks) == 2
        assert chunks[0].delta == "hello"
        assert chunks[-1].is_final is True

    @pytest.mark.asyncio
    async def test_stream_error_after_first_chunk_surfaces(self) -> None:
        """Error AFTER first chunk → surface; secondary NOT tried."""
        primary = _ScriptedBackend(
            "openai",
            "gpt-4o",
            [[_chunk("partial"), BackendTimeoutError("mid", context={"provider": "openai"})]],
        )
        secondary = _ScriptedBackend(
            "anthropic",
            "claude",
            [[_chunk("", is_final=True)]],
        )
        wrapper = MultiModelChatBackend([primary, secondary])
        received: list[StreamChunk] = []

        async def _consume() -> None:
            async for c in wrapper.chat_stream([_user_msg()]):
                received.append(c)

        with pytest.raises(BackendTimeoutError):
            await _consume()
        assert received == [_chunk("partial")]
        assert secondary.stream_call_count == 0

    @pytest.mark.asyncio
    async def test_stream_single_backend_success(self) -> None:
        primary = _ScriptedBackend(
            "openai",
            "gpt-4o",
            [[_chunk("a"), _chunk("b"), _chunk("", is_final=True)]],
        )
        wrapper = MultiModelChatBackend([primary])
        chunks: list[StreamChunk] = []
        async for c in wrapper.chat_stream([_user_msg()]):
            chunks.append(c)
        assert [c.delta for c in chunks] == ["a", "b", ""]
        assert chunks[-1].is_final is True
