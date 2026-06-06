"""Default-suite mocked vision round-trip tests for Spec 13 T15.

These are the in-CI counterpart to the live external smokes (T16/T17). For
both an Anthropic-flavoured and an OpenAI-flavoured ``OpenAICompatibleBackend``
they:

1. Stand up a temp workspace root with a single fake-PNG fixture on disk.
2. Construct a ``ConversationMessage`` whose ``content`` is a list of
   ``[TextContent, ImageContent]`` — the workspace-path image ref pattern
   produced by upstream T08/T09 serialisation.
3. Patch the provider SDK's chat-completion entrypoint with an AsyncMock
   returning a canned response so the backend's request body can be
   captured and asserted against the spec wire shape.
4. Assert the captured request body carries the image block in the
   provider-native shape (base64 ``source`` for Anthropic, ``image_url``
   data-URL for OpenAI) with the base64 of the fixture bytes.
5. Assert the parsed ``ChatResponse.content`` reflects the canned reply.
6. Assert the IMAGE REF in the original ``ConversationMessage`` is
   preserved by-reference — the serialiser must not mutate the input
   message's ``workspace_path`` to bytes (per D-13-X-now option (c):
   image refs travel via the future ``messages.images`` JSONB column;
   base64 NEVER leaks into ``messages.content``).

Test infrastructure (mock SDK helpers) is taken verbatim from the
existing ``test_openai_compat.py`` patterns; this file only adds the
list-form-content round-trip coverage that T15 fold-in #7 calls for.
"""

# ruff: noqa: ANN401, SLF001 — mocks use Any return types; tests access private attrs

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from persona.backends.config import BackendConfig
from persona.backends.openai_compat import OpenAICompatibleBackend
from persona.backends.types import ChatResponse
from persona.schema.content import ImageContent, TextContent
from persona.schema.conversation import ConversationMessage
from pydantic import SecretStr

if TYPE_CHECKING:
    from pathlib import Path

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


_FIXTURE_BYTES = b"\x89PNGfake01"  # 10-byte fake payload — serialiser doesn't decode it


def _now() -> datetime:
    return datetime(2026, 6, 5, tzinfo=UTC)


@pytest.fixture
def workspace_with_image(tmp_path: Path) -> tuple[Path, str, bytes]:
    """Create a workspace root with a fake-PNG fixture for the round-trip.

    Returns ``(workspace_root, workspace_relative_path, raw_bytes)``.
    """
    rel_path = "abc.png"
    target = tmp_path / rel_path
    target.write_bytes(_FIXTURE_BYTES)
    return tmp_path, rel_path, _FIXTURE_BYTES


# -----------------------------------------------------------------------------
# Mock-SDK helpers — mirror packages/core/tests/unit/backends/test_openai_compat.py
# -----------------------------------------------------------------------------


def _mock_anthropic_message_response(
    *,
    text: str = "I see a triangle",
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> Any:
    """Build a MagicMock that mimics ``anthropic.types.Message``."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    response.model = model
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    response.usage = usage
    return response


def _mock_openai_chat_completion(
    *,
    content: str = "I see a triangle",
    model: str = "gpt-4o",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> Any:
    """Build a MagicMock that mimics ``openai.types.chat.ChatCompletion``."""
    choice = MagicMock()
    message = MagicMock()
    message.content = content
    message.tool_calls = []
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    response.model = model
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    response.usage = usage
    return response


# -----------------------------------------------------------------------------
# Anthropic mocked round-trip
# -----------------------------------------------------------------------------


class TestAnthropicMockRoundTrip:
    """End-to-end mocked vision round-trip against the Anthropic dispatch."""

    @pytest.mark.asyncio
    async def test_image_block_round_trips_through_chat(
        self, workspace_with_image: tuple[Path, str, bytes]
    ) -> None:
        """A [text, image] user message hits the SDK with a base64 image block.

        Asserts:

        * The captured ``messages.create`` kwargs carry a ``messages`` list
          whose only entry has a ``content`` array with [text, image]
          blocks in that order.
        * The image block's ``source`` is ``type=base64`` with the
          correct ``media_type`` and ``data`` (base64 of the fixture bytes).
        * The parsed ``ChatResponse.content`` reflects the canned reply
          ("I see a triangle").
        * The IMAGE REF in the original ``ConversationMessage`` stays a
          workspace_path string — the SerialiseChain did NOT mutate the
          message to inline base64 (D-13-X-now option (c)).
        """
        workspace_root, rel_path, raw_bytes = workspace_with_image
        msg = ConversationMessage(
            role="user",
            content=[
                TextContent(text="describe"),
                ImageContent(workspace_path=rel_path, media_type="image/png"),
            ],
            created_at=_now(),
        )

        backend = OpenAICompatibleBackend(
            BackendConfig(
                provider="anthropic",
                model="claude-sonnet-4-6",
                api_key=SecretStr("test-key"),
            ),
            workspace_root=workspace_root,
        )
        create_mock = AsyncMock(return_value=_mock_anthropic_message_response())
        with patch.object(
            backend._anthropic.messages,  # type: ignore[union-attr]
            "create",
            new=create_mock,
        ):
            response = await backend.chat([msg])

        # ---- Parsed response reflects the canned reply ----------------
        assert isinstance(response, ChatResponse)
        assert response.content == "I see a triangle"
        assert response.provider == "anthropic"

        # ---- Captured request body carries the [text, image] blocks ---
        call_kwargs = create_mock.call_args.kwargs
        sent_messages = call_kwargs["messages"]
        assert len(sent_messages) == 1
        sent = sent_messages[0]
        assert sent["role"] == "user"
        blocks = sent["content"]
        assert isinstance(blocks, list)
        assert len(blocks) == 2

        assert blocks[0] == {"type": "text", "text": "describe"}

        image_block = blocks[1]
        assert image_block["type"] == "image"
        source = image_block["source"]
        assert source["type"] == "base64"
        assert source["media_type"] == "image/png"
        assert source["data"] == base64.standard_b64encode(raw_bytes).decode("ascii")

        # ---- The original message ref is preserved by-reference -------
        # The serialiser must NOT mutate the ConversationMessage to inline
        # base64 — image refs travel via messages.images JSONB (D-13-X-now
        # option (c)); base64 NEVER leaks into the message store path.
        assert isinstance(msg.content, list)
        original_image = msg.content[1]
        assert isinstance(original_image, ImageContent)
        assert original_image.workspace_path == rel_path
        # The pydantic model is frozen, so attribute identity is enough
        # to prove non-mutation; double-check the type didn't drift to bytes.
        assert isinstance(original_image.workspace_path, str)


# -----------------------------------------------------------------------------
# OpenAI mocked round-trip
# -----------------------------------------------------------------------------


class TestOpenAIMockRoundTrip:
    """End-to-end mocked vision round-trip against the OpenAI dispatch."""

    @pytest.mark.asyncio
    async def test_image_part_round_trips_through_chat(
        self, workspace_with_image: tuple[Path, str, bytes]
    ) -> None:
        """A [text, image] user message hits the SDK with an image_url data URL.

        Asserts:

        * The captured ``chat.completions.create`` kwargs carry a
          ``messages`` list whose only entry has a multi-part ``content``
          array with [text, image_url] parts in that order.
        * The ``image_url.url`` is a ``data:image/png;base64,…`` URL
          carrying the base64 of the fixture bytes.
        * The parsed ``ChatResponse.content`` reflects the canned reply.
        * The IMAGE REF in the original ``ConversationMessage`` stays a
          workspace_path string — the SerialiseChain did NOT mutate the
          message to inline base64 (D-13-X-now option (c)).
        """
        workspace_root, rel_path, raw_bytes = workspace_with_image
        msg = ConversationMessage(
            role="user",
            content=[
                TextContent(text="describe"),
                ImageContent(workspace_path=rel_path, media_type="image/png"),
            ],
            created_at=_now(),
        )

        backend = OpenAICompatibleBackend(
            BackendConfig(
                provider="openai",
                model="gpt-4o",
                api_key=SecretStr("test-key"),
            ),
            workspace_root=workspace_root,
        )
        create_mock = AsyncMock(return_value=_mock_openai_chat_completion())
        with patch.object(
            backend._openai.chat.completions,  # type: ignore[union-attr]
            "create",
            new=create_mock,
        ):
            response = await backend.chat([msg])

        # ---- Parsed response reflects the canned reply ----------------
        assert isinstance(response, ChatResponse)
        assert response.content == "I see a triangle"
        assert response.provider == "openai"

        # ---- Captured request body carries the [text, image_url] parts ----
        call_kwargs = create_mock.call_args.kwargs
        sent_messages = call_kwargs["messages"]
        assert len(sent_messages) == 1
        sent = sent_messages[0]
        assert sent["role"] == "user"
        parts = sent["content"]
        assert isinstance(parts, list)
        assert len(parts) == 2

        assert parts[0] == {"type": "text", "text": "describe"}

        image_part = parts[1]
        assert image_part["type"] == "image_url"
        expected_b64 = base64.standard_b64encode(raw_bytes).decode("ascii")
        assert image_part["image_url"] == {
            "url": f"data:image/png;base64,{expected_b64}",
        }

        # ---- The original message ref is preserved by-reference -------
        assert isinstance(msg.content, list)
        original_image = msg.content[1]
        assert isinstance(original_image, ImageContent)
        assert original_image.workspace_path == rel_path
        assert isinstance(original_image.workspace_path, str)
