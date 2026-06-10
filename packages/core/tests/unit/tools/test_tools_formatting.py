"""Tests for the provider-aware tool-result formatter (T05, D-03-6)."""

# ruff: noqa: ANN401, ARG001, ARG002, ERA001
from __future__ import annotations

import pytest
from persona.schema.tools import ToolCall, ToolResult
from persona.tools.formatting import format_tool_result


def _call() -> ToolCall:
    return ToolCall(name="web_search", args={"q": "norway tenancy"}, call_id="tcid-1")


def _result(*, is_error: bool = False, content: str = "results") -> ToolResult:
    return ToolResult(tool_name="web_search", content=content, call_id="tcid-1", is_error=is_error)


# ---------------------------------------------------------------------------
# Section: Anthropic shape
# ---------------------------------------------------------------------------


class TestAnthropicShape:
    """Anthropic tool_result uses the same role=tool shape as OpenAI/DeepSeek
    (spec 11 launch fix). ``_message_to_anthropic`` lifts it into the proper
    structured ``tool_result`` block list on a user message; we used to JSON-
    encode the block ourselves which Anthropic doesn't accept as content."""

    def test_role_is_tool(self) -> None:
        msg = format_tool_result(_call(), _result(), provider_name="anthropic")
        assert msg.role == "tool"

    def test_content_is_raw_result_text(self) -> None:
        msg = format_tool_result(_call(), _result(content="some text"), provider_name="anthropic")
        assert msg.content == "some text"

    def test_error_content_unchanged(self) -> None:
        # No "Error:" prefix on the Anthropic branch — the metadata flag is the
        # signal; the structured block carries the raw content unchanged.
        msg = format_tool_result(
            _call(),
            _result(is_error=True, content="Connection refused"),
            provider_name="anthropic",
        )
        assert msg.content == "Connection refused"
        assert msg.metadata["is_error"] == "True"

    def test_metadata_carries_bookkeeping(self) -> None:
        msg = format_tool_result(_call(), _result(), provider_name="anthropic")
        assert msg.metadata["tool_call_id"] == "tcid-1"
        assert msg.metadata["tool_name"] == "web_search"
        assert msg.metadata["is_error"] == "False"
        assert msg.metadata["provider_format"] == "anthropic"


# ---------------------------------------------------------------------------
# Section: OpenAI-family shape
# ---------------------------------------------------------------------------


# D-20-X-nvidia-allow-set-extend: nvidia added 2026-06-10 after a production
# run-time crash surfaced the formatter's allow-set gap (the atomic invariant
# is a SIX-touch: Provider Literal + DEFAULT_BASE_URLS + _NATIVE_TOOLS_
# CAPABILITY + _VISION_CAPABILITY + _factory.py's _OPENAI_COMPAT_PROVIDERS +
# this provider_name switch). NVIDIA uses the same OpenAI-compat tool-result
# shape as the other openai-SDK providers.
@pytest.mark.parametrize("provider", ["openai", "deepseek", "groq", "together", "nvidia"])
class TestOpenAIFamilyShape:
    """OpenAI-compat providers use role=tool with tool_call_id + content."""

    def test_role_is_tool(self, provider: str) -> None:
        msg = format_tool_result(_call(), _result(), provider_name=provider)
        assert msg.role == "tool"

    def test_content_is_bare_text(self, provider: str) -> None:
        msg = format_tool_result(_call(), _result(content="bare text"), provider_name=provider)
        assert msg.content == "bare text"

    def test_error_prefixed_in_content(self, provider: str) -> None:
        msg = format_tool_result(
            _call(),
            _result(is_error=True, content="API down"),
            provider_name=provider,
        )
        assert msg.content == "Error: API down"

    def test_error_not_double_prefixed(self, provider: str) -> None:
        # Tool body may already include "Error:" — don't duplicate.
        msg = format_tool_result(
            _call(),
            _result(is_error=True, content="Error: already prefixed"),
            provider_name=provider,
        )
        assert msg.content == "Error: already prefixed"

    def test_metadata(self, provider: str) -> None:
        msg = format_tool_result(_call(), _result(), provider_name=provider)
        assert msg.metadata["tool_call_id"] == "tcid-1"
        assert msg.metadata["tool_name"] == "web_search"
        assert msg.metadata["provider_format"] == "openai"


# ---------------------------------------------------------------------------
# Section: Ollama / local (shim) shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["ollama", "local"])
class TestShimShape:
    """Ollama and local HF backends consume the shim's plain-text format."""

    def test_role_is_user(self, provider: str) -> None:
        msg = format_tool_result(_call(), _result(), provider_name=provider)
        assert msg.role == "user"

    def test_content_format(self, provider: str) -> None:
        msg = format_tool_result(_call(), _result(content="42"), provider_name=provider)
        assert msg.content == "web_search returned: 42"

    def test_error_in_content_directly(self, provider: str) -> None:
        msg = format_tool_result(
            _call(),
            _result(is_error=True, content="boom"),
            provider_name=provider,
        )
        # Shim doesn't have a separate is_error field — content carries the error inline.
        assert "boom" in msg.content
        assert msg.metadata["is_error"] == "True"

    def test_metadata(self, provider: str) -> None:
        msg = format_tool_result(_call(), _result(), provider_name=provider)
        assert msg.metadata["provider_format"] == "shim"
        assert msg.metadata["tool_call_id"] == "tcid-1"


# ---------------------------------------------------------------------------
# Section: Unknown provider
# ---------------------------------------------------------------------------


class TestUnknownProvider:
    """Unknown provider_name raises ValueError (D-03-6 — programmer error)."""

    def test_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown provider_name"):
            format_tool_result(_call(), _result(), provider_name="bogus")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown provider_name"):
            format_tool_result(_call(), _result(), provider_name="")


# ---------------------------------------------------------------------------
# Section: ConversationMessage invariants preserved
# ---------------------------------------------------------------------------


class TestConversationMessageInvariants:
    """The returned message must satisfy the existing ConversationMessage contract."""

    def test_created_at_is_tz_aware(self) -> None:
        msg = format_tool_result(_call(), _result(), provider_name="anthropic")
        assert msg.created_at.tzinfo is not None

    def test_message_is_frozen(self) -> None:
        from pydantic import ValidationError

        msg = format_tool_result(_call(), _result(), provider_name="openai")
        with pytest.raises(ValidationError):
            msg.role = "assistant"  # type: ignore[misc]
