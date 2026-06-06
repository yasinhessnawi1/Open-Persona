"""Vision-path tests for :class:`OllamaBackend` (Spec 13 T07).

Covers the two paths the fail-loud contract specifies:

* ``use_vision=False`` (default): any message carrying
  :class:`ImageContent` raises :class:`BackendVisionNotSupportedError`
  with the D-13-X-error-hierarchy context shape **before** any HTTP
  call goes out — both ``chat`` and ``chat_stream``.

* ``use_vision=True`` + ``workspace_root`` supplied: a list-form user
  message with one :class:`ImageContent` block reaches the mocked
  ``/api/chat`` POST with the image bytes shipped as a base64 string on
  the user message's ``images`` field (the Ollama native shape).
"""

# ruff: noqa: ANN401, SLF001 — mocks use Any; tests poke private state

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from persona.backends.config import BackendConfig
from persona.backends.errors import BackendVisionNotSupportedError
from persona.backends.ollama import OllamaBackend
from persona.errors import PersonaError
from persona.schema.content import ImageContent, TextContent
from persona.schema.conversation import ConversationMessage

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _config(model: str = "llava") -> BackendConfig:
    return BackendConfig(provider="ollama", model=model)


def _image_msg(workspace_path: str = "img/cat.png") -> ConversationMessage:
    return ConversationMessage(
        role="user",
        content=[
            TextContent(text="describe"),
            ImageContent(workspace_path=workspace_path, media_type="image/png"),
        ],
        created_at=datetime.now(UTC),
    )


def _text_msg(text: str = "hello") -> ConversationMessage:
    return ConversationMessage(role="user", content=text, created_at=datetime.now(UTC))


def _mock_response(
    *,
    status: int = 200,
    json_body: dict[str, Any] | None = None,
) -> Any:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.is_success = 200 <= status < 300
    response.headers = {"content-type": "application/json"}
    response.json = MagicMock(return_value=json_body or {})
    return response


@pytest.fixture
def workspace_with_image(tmp_path: Path) -> tuple[Path, str, bytes]:
    """Create a workspace dir with an image and return (root, rel_path, bytes)."""
    img_dir = tmp_path / "img"
    img_dir.mkdir()
    image_path = img_dir / "cat.png"
    image_path.write_bytes(_PNG_BYTES)
    return tmp_path, "img/cat.png", _PNG_BYTES


# ---------------------------------------------------------------------------
# Default (use_vision=False): fail-loud guard
# ---------------------------------------------------------------------------


class TestDefaultFailsLoud:
    @pytest.mark.asyncio
    async def test_chat_raises_on_image_content(self) -> None:
        backend = OllamaBackend(_config())
        # Post should never be reached; install a sentinel that would blow up
        # if it were called.
        client = MagicMock()
        client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        backend._client = client

        with pytest.raises(BackendVisionNotSupportedError) as info:
            await backend.chat([_image_msg()])

        err = info.value
        assert err.context["backend"] == "ollama"
        assert err.context["model"] == "llava"
        assert err.context["image_count"] == "1"

    @pytest.mark.asyncio
    async def test_chat_stream_raises_on_image_content(self) -> None:
        backend = OllamaBackend(_config(model="llama3"))
        client = MagicMock()
        client.stream = MagicMock(side_effect=AssertionError("HTTP must not be called"))
        backend._client = client

        with pytest.raises(BackendVisionNotSupportedError) as info:
            # chat_stream is an async generator; advancing it triggers the guard.
            async for _ in backend.chat_stream([_image_msg()]):
                pass  # pragma: no cover

        err = info.value
        assert err.context == {
            "backend": "ollama",
            "model": "llama3",
            "image_count": "1",
        }

    @pytest.mark.asyncio
    async def test_image_count_aggregates_across_messages(self) -> None:
        backend = OllamaBackend(_config())
        client = MagicMock()
        client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        backend._client = client

        msgs = [
            _image_msg("img/a.png"),
            _text_msg("intermediate"),
            _image_msg("img/b.png"),
        ]
        with pytest.raises(BackendVisionNotSupportedError) as info:
            await backend.chat(msgs)
        assert info.value.context["image_count"] == "2"

    @pytest.mark.asyncio
    async def test_text_only_does_not_trigger_guard(self) -> None:
        backend = OllamaBackend(_config())
        client = MagicMock()
        client.post = AsyncMock(
            return_value=_mock_response(
                json_body={
                    "model": "llava",
                    "message": {"role": "assistant", "content": "hi"},
                    "done": True,
                }
            )
        )
        backend._client = client
        response = await backend.chat([_text_msg()])
        assert response.content == "hi"

    def test_error_caught_as_persona_error(self) -> None:
        # The base class is PersonaError directly (flat hierarchy per D-13-X
        # error hierarchy + D-03-1). Callers can catch on either type.
        with pytest.raises(PersonaError):
            raise BackendVisionNotSupportedError(
                context={"backend": "ollama", "model": "llama3", "image_count": "1"}
            )


# ---------------------------------------------------------------------------
# use_vision=True path: images shipped via the user message ``images`` field
# ---------------------------------------------------------------------------


class TestVisionEnabledPath:
    @pytest.mark.asyncio
    async def test_chat_ships_image_as_base64_on_user_message(
        self, workspace_with_image: tuple[Path, str, bytes]
    ) -> None:
        workspace_root, rel_path, raw_bytes = workspace_with_image
        backend = OllamaBackend(
            _config(model="llava"),
            use_vision=True,
            workspace_root=workspace_root,
        )
        assert backend.supports_vision is True

        client = MagicMock()
        client.post = AsyncMock(
            return_value=_mock_response(
                json_body={
                    "model": "llava",
                    "message": {"role": "assistant", "content": "a cat"},
                    "done": True,
                    "prompt_eval_count": 10,
                    "eval_count": 3,
                }
            )
        )
        backend._client = client

        msg = ConversationMessage(
            role="user",
            content=[
                TextContent(text="What is in this image?"),
                ImageContent(workspace_path=rel_path, media_type="image/png"),
            ],
            created_at=datetime.now(UTC),
        )
        response = await backend.chat([msg])
        assert response.content == "a cat"

        # Verify the HTTP body: the user message must carry ``content`` (the
        # concatenated text) and ``images`` (a list of base64 strings).
        client.post.assert_awaited_once()
        kwargs = client.post.await_args.kwargs
        body = kwargs["json"]
        assert body["model"] == "llava"
        ollama_msgs = body["messages"]
        assert len(ollama_msgs) == 1
        sent = ollama_msgs[0]
        assert sent["role"] == "user"
        assert sent["content"] == "What is in this image?"
        expected_b64 = base64.b64encode(raw_bytes).decode("ascii")
        assert sent["images"] == [expected_b64]

    @pytest.mark.asyncio
    async def test_text_only_message_omits_images_field(
        self, workspace_with_image: tuple[Path, str, bytes]
    ) -> None:
        workspace_root, _rel_path, _raw = workspace_with_image
        backend = OllamaBackend(
            _config(model="llava"),
            use_vision=True,
            workspace_root=workspace_root,
        )
        client = MagicMock()
        client.post = AsyncMock(
            return_value=_mock_response(
                json_body={
                    "model": "llava",
                    "message": {"role": "assistant", "content": "ok"},
                    "done": True,
                }
            )
        )
        backend._client = client

        await backend.chat([_text_msg("hello")])
        body = client.post.await_args.kwargs["json"]
        sent = body["messages"][0]
        assert sent["content"] == "hello"
        assert "images" not in sent

    @pytest.mark.asyncio
    async def test_missing_workspace_root_raises_loud(self) -> None:
        # use_vision=True but no workspace_root → the request reaches
        # _convert_message and the load helper raises the same
        # BackendVisionNotSupportedError shape rather than silently
        # dropping the image.
        backend = OllamaBackend(
            _config(model="llava"),
            use_vision=True,
            workspace_root=None,
        )
        client = MagicMock()
        client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        backend._client = client

        with pytest.raises(BackendVisionNotSupportedError) as info:
            await backend.chat([_image_msg()])
        assert info.value.context["backend"] == "ollama"
        assert info.value.context["model"] == "llava"
