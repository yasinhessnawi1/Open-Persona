"""Tests for the spec-13 T05/T06 OpenAI-compat multimodal serialisers.

Covers the ``_message_to_anthropic`` (T05) and ``_message_to_openai``
(T06) extensions that handle ``ConversationMessage.content`` being a
``list[MessageContent]`` per T03's schema widening:

* :class:`TextContent` blocks become ``{"type": "text", "text": ...}``.
* :class:`ImageContent` blocks become base64 image blocks per D-13-2 —
  Anthropic emits ``{"type": "image", "source": {"type": "base64", ...}}``
  while OpenAI emits ``{"type": "image_url", "image_url":
  {"url": "data:<media>;base64,<b64>"}}``. Bytes resolved from
  ``workspace_root / block.workspace_path``.
* Block order is preserved across mixed lists (text/image/text and
  multi-image content per D-13-5's 4-image cap).
* The text-only ``content=str`` path remains byte-for-byte identical to
  the Phase 1 wire shape (gated against a captured snapshot here as
  well as the cross-cutting T01 corpus).
* :class:`BackendVisionNotSupportedError` is raised BEFORE any
  filesystem touch when ``supports_vision=False`` or
  ``workspace_root=None`` and the list carries any image blocks. The
  exception's ``context`` carries the structured keys the runtime
  layer's re-dispatcher consumes.
"""

# ruff: noqa: SLF001 — tests construct backends and reach into private state

from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path

import pytest
from persona.backends.config import BackendConfig
from persona.backends.errors import BackendVisionNotSupportedError
from persona.backends.openai_compat import (
    OpenAICompatibleBackend,
    _message_to_anthropic,
    _message_to_openai,
)
from persona.schema.content import ImageContent, TextContent
from persona.schema.conversation import ConversationMessage
from pydantic import SecretStr

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 6, 5, tzinfo=UTC)


_FIXTURE_BYTES = b"\x89PNGfake"  # tiny payload — the serialiser doesn't decode it


@pytest.fixture
def workspace_with_image(tmp_path: Path) -> tuple[Path, str, bytes]:
    """Create a workspace root with a single fake-PNG fixture.

    Returns ``(workspace_root, workspace_relative_path, raw_bytes)`` so
    tests can both reference the path and assert the base64 round-trip.
    """
    rel_path = "images/turn-0/0.png"
    target = tmp_path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_FIXTURE_BYTES)
    return tmp_path, rel_path, _FIXTURE_BYTES


@pytest.fixture
def workspace_with_four_images(tmp_path: Path) -> tuple[Path, list[tuple[str, bytes]]]:
    """Workspace root with four distinct fake-image fixtures."""
    entries: list[tuple[str, bytes]] = []
    for i in range(4):
        rel = f"images/turn-0/{i}.png"
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = b"img-" + str(i).encode("ascii")
        target.write_bytes(payload)
        entries.append((rel, payload))
    return tmp_path, entries


# -----------------------------------------------------------------------------
# Anthropic serialiser — list-form content
# -----------------------------------------------------------------------------


class TestAnthropicSerialiser:
    """Anthropic ``_message_to_anthropic`` multimodal extension (T05)."""

    def test_text_image_text_list_serialises_in_order(
        self, workspace_with_image: tuple[Path, str, bytes]
    ) -> None:
        """A [text, image, text] content list serialises to a 3-block array.

        The order matches the caller's content-list order — the image
        block sits between the two text blocks on the wire.
        """
        workspace_root, rel_path, raw_bytes = workspace_with_image
        msg = ConversationMessage(
            role="user",
            content=[
                TextContent(text="before"),
                ImageContent(workspace_path=rel_path, media_type="image/png"),
                TextContent(text="after"),
            ],
            created_at=_now(),
        )

        out = _message_to_anthropic(
            msg,
            workspace_root=workspace_root,
            supports_vision=True,
            backend="anthropic",
            model="claude-sonnet-4-5",
        )

        assert out["role"] == "user"
        blocks = out["content"]
        assert isinstance(blocks, list)
        assert len(blocks) == 3
        assert blocks[0] == {"type": "text", "text": "before"}
        assert blocks[1]["type"] == "image"
        assert blocks[2] == {"type": "text", "text": "after"}

        # Image block well-formed per D-13-2 (Anthropic only accepts base64).
        source = blocks[1]["source"]
        assert source["type"] == "base64"
        assert source["media_type"] == "image/png"
        assert source["data"] == base64.standard_b64encode(raw_bytes).decode("ascii")

    def test_image_block_well_formed_base64(
        self, workspace_with_image: tuple[Path, str, bytes]
    ) -> None:
        workspace_root, rel_path, raw_bytes = workspace_with_image
        msg = ConversationMessage(
            role="user",
            content=[
                TextContent(text="describe"),
                ImageContent(workspace_path=rel_path, media_type="image/jpeg"),
            ],
            created_at=_now(),
        )
        out = _message_to_anthropic(
            msg,
            workspace_root=workspace_root,
            supports_vision=True,
            backend="anthropic",
            model="claude-sonnet-4-5",
        )
        image_block = out["content"][1]
        assert image_block["type"] == "image"
        assert image_block["source"] == {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(raw_bytes).decode("ascii"),
        }

    def test_four_image_block_order_preserved(
        self,
        workspace_with_four_images: tuple[Path, list[tuple[str, bytes]]],
    ) -> None:
        """Four-image content (D-13-5 cap) preserved in declaration order."""
        workspace_root, entries = workspace_with_four_images
        blocks_in: list[TextContent | ImageContent] = [TextContent(text="grid:")]
        blocks_in.extend(
            ImageContent(workspace_path=rel, media_type="image/png") for rel, _ in entries
        )
        msg = ConversationMessage(
            role="user",
            content=blocks_in,
            created_at=_now(),
        )

        out = _message_to_anthropic(
            msg,
            workspace_root=workspace_root,
            supports_vision=True,
            backend="anthropic",
            model="claude-sonnet-4-5",
        )

        out_blocks = out["content"]
        assert len(out_blocks) == 5  # 1 text + 4 images
        assert out_blocks[0] == {"type": "text", "text": "grid:"}
        for i, (_, payload) in enumerate(entries, start=1):
            block = out_blocks[i]
            assert block["type"] == "image"
            assert block["source"]["media_type"] == "image/png"
            assert block["source"]["data"] == base64.standard_b64encode(payload).decode("ascii")

    def test_text_only_str_byte_for_byte_unchanged(self) -> None:
        """Text-only ``content=str`` round-trips identically (Phase 1 wire shape).

        Captured snapshot mirrors the cross-cutting T01 snapshot corpus
        and pins the existing serialiser branch so the multimodal
        widening is provably additive.
        """
        msg = ConversationMessage(
            role="user",
            content="hello world",
            created_at=_now(),
        )
        snapshot = {"role": "user", "content": "hello world"}

        # Both with-workspace and no-workspace calls produce the snapshot —
        # the str path never touches the workspace_root parameter.
        for ws in (None, Path("/tmp/persona-ws-irrelevant")):
            out = _message_to_anthropic(
                msg,
                workspace_root=ws,
                supports_vision=True,
                backend="anthropic",
                model="claude-sonnet-4-5",
            )
            assert out == snapshot

    def test_system_str_content_path_unchanged(self) -> None:
        """system role text-only stays a str message body (Phase 1)."""
        msg = ConversationMessage(
            role="system",
            content="you are helpful",
            created_at=_now(),
        )
        out = _message_to_anthropic(
            msg,
            workspace_root=None,
            supports_vision=True,
            backend="anthropic",
            model="claude-sonnet-4-5",
        )
        assert out == {"role": "system", "content": "you are helpful"}

    def test_assistant_with_tool_calls_str_path_unchanged(self) -> None:
        """The assistant-with-tool_calls Phase 1 branch is untouched.

        The native-tool path still emits text + tool_use blocks per the
        spec 11 launch fix; T05 must not regress this when the content
        is the existing str form.
        """
        from persona.schema.tools import ToolCall

        msg = ConversationMessage(
            role="assistant",
            content="searching…",
            created_at=_now(),
            tool_calls=[ToolCall(name="web_search", args={"q": "x"}, call_id="tu_1")],
        )
        out = _message_to_anthropic(
            msg,
            workspace_root=None,
            supports_vision=True,
            backend="anthropic",
            model="claude-sonnet-4-5",
        )
        assert out["role"] == "assistant"
        blocks = out["content"]
        assert {"type": "text", "text": "searching…"} in blocks
        tool_use = next(b for b in blocks if b["type"] == "tool_use")
        assert tool_use["id"] == "tu_1"
        assert tool_use["name"] == "web_search"
        assert tool_use["input"] == {"q": "x"}

    def test_raises_when_supports_vision_false(
        self, workspace_with_image: tuple[Path, str, bytes]
    ) -> None:
        """A list-form msg with an image -> BackendVisionNotSupportedError when off.

        The check fires BEFORE the filesystem read; the structured
        context carries backend / model / image_count so the runtime
        re-dispatcher has what it needs (D-13-X-error-hierarchy).
        """
        workspace_root, rel_path, _ = workspace_with_image
        msg = ConversationMessage(
            role="user",
            content=[
                TextContent(text="look"),
                ImageContent(workspace_path=rel_path, media_type="image/png"),
            ],
            created_at=_now(),
        )
        with pytest.raises(BackendVisionNotSupportedError) as exc_info:
            _message_to_anthropic(
                msg,
                workspace_root=workspace_root,
                supports_vision=False,
                backend="deepseek",
                model="deepseek-chat",
            )
        ctx = exc_info.value.context
        assert ctx["backend"] == "deepseek"
        assert ctx["model"] == "deepseek-chat"
        assert ctx["image_count"] == "1"

    def test_raises_when_workspace_root_is_none(
        self, workspace_with_image: tuple[Path, str, bytes]
    ) -> None:
        """No workspace_root + image block -> BackendVisionNotSupportedError."""
        _, rel_path, _ = workspace_with_image
        msg = ConversationMessage(
            role="user",
            content=[ImageContent(workspace_path=rel_path, media_type="image/png")],
            created_at=_now(),
        )
        with pytest.raises(BackendVisionNotSupportedError) as exc_info:
            _message_to_anthropic(
                msg,
                workspace_root=None,
                supports_vision=True,
                backend="anthropic",
                model="claude-sonnet-4-5",
            )
        ctx = exc_info.value.context
        assert ctx["backend"] == "anthropic"
        assert ctx["model"] == "claude-sonnet-4-5"
        assert ctx["image_count"] == "1"
        assert ctx.get("reason") == "missing_workspace_root"

    def test_raises_before_filesystem_touch_when_supports_vision_false(
        self, tmp_path: Path
    ) -> None:
        """Vision-off path raises BEFORE touching the workspace.

        The fixture file does not exist on disk; if the serialiser read
        bytes before the capability check, this would raise
        :class:`FileNotFoundError` instead.
        """
        msg = ConversationMessage(
            role="user",
            content=[ImageContent(workspace_path="does/not/exist.png", media_type="image/png")],
            created_at=_now(),
        )
        with pytest.raises(BackendVisionNotSupportedError):
            _message_to_anthropic(
                msg,
                workspace_root=tmp_path,
                supports_vision=False,
                backend="groq",
                model="llama-3.3-70b-versatile",
            )

    def test_list_with_no_images_does_not_require_workspace(self) -> None:
        """A list of text-only blocks doesn't trigger the vision guard.

        The guard counts ImageContent specifically; a multi-text list
        (which the schema permits as long as it's not the
        single-text-as-list shape forbidden by
        ``_reject_single_text_as_list``) goes through without needing
        a workspace_root.
        """
        msg = ConversationMessage(
            role="user",
            content=[
                TextContent(text="first"),
                TextContent(text="second"),
            ],
            created_at=_now(),
        )
        out = _message_to_anthropic(
            msg,
            workspace_root=None,
            supports_vision=False,
            backend="deepseek",
            model="deepseek-chat",
        )
        assert out == {
            "role": "user",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
            ],
        }


# -----------------------------------------------------------------------------
# Backend constructor — workspace_root kwarg
# -----------------------------------------------------------------------------


class TestOpenAICompatibleBackendWorkspaceRoot:
    def test_default_is_none(self) -> None:
        backend = OpenAICompatibleBackend(
            BackendConfig(
                provider="anthropic",
                model="claude-sonnet-4-5",
                api_key=SecretStr("test-key"),
            )
        )
        assert backend._workspace_root is None

    def test_kwarg_round_trip(self, tmp_path: Path) -> None:
        backend = OpenAICompatibleBackend(
            BackendConfig(
                provider="anthropic",
                model="claude-sonnet-4-5",
                api_key=SecretStr("test-key"),
            ),
            workspace_root=tmp_path,
        )
        assert backend._workspace_root == tmp_path


# -----------------------------------------------------------------------------
# OpenAI serialiser — list-form content (T06)
# -----------------------------------------------------------------------------


class TestOpenAISerialiser:
    """OpenAI ``_message_to_openai`` multimodal extension (T06)."""

    def test_text_image_text_list_serialises_in_order(
        self, workspace_with_image: tuple[Path, str, bytes]
    ) -> None:
        """A [text, image, text] content list serialises to a 3-part array.

        Order matches the caller's content-list order; the image part
        is sandwiched between the two text parts on the wire (OpenAI's
        Vision Chat Completions multi-part content schema).
        """
        workspace_root, rel_path, raw_bytes = workspace_with_image
        msg = ConversationMessage(
            role="user",
            content=[
                TextContent(text="before"),
                ImageContent(workspace_path=rel_path, media_type="image/png"),
                TextContent(text="after"),
            ],
            created_at=_now(),
        )

        out = _message_to_openai(
            msg,
            workspace_root=workspace_root,
            supports_vision=True,
            backend="openai",
            model="gpt-4o",
        )

        assert out["role"] == "user"
        parts = out["content"]
        assert isinstance(parts, list)
        assert len(parts) == 3
        assert parts[0] == {"type": "text", "text": "before"}
        assert parts[1]["type"] == "image_url"
        assert parts[2] == {"type": "text", "text": "after"}

        # Image part is a base64 data URL per D-13-2.
        expected_b64 = base64.standard_b64encode(raw_bytes).decode("ascii")
        assert parts[1]["image_url"] == {
            "url": f"data:image/png;base64,{expected_b64}",
        }

    def test_image_part_is_data_url_base64(
        self, workspace_with_image: tuple[Path, str, bytes]
    ) -> None:
        """The image part carries a fully-qualified ``data:`` URL."""
        workspace_root, rel_path, raw_bytes = workspace_with_image
        msg = ConversationMessage(
            role="user",
            content=[
                TextContent(text="describe"),
                ImageContent(workspace_path=rel_path, media_type="image/png"),
            ],
            created_at=_now(),
        )
        out = _message_to_openai(
            msg,
            workspace_root=workspace_root,
            supports_vision=True,
            backend="openai",
            model="gpt-4o-mini",
        )
        image_part = out["content"][1]
        assert image_part["type"] == "image_url"
        url = image_part["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        # The payload after the comma is the literal base64 of the bytes.
        payload = url.split(",", 1)[1]
        assert payload == base64.standard_b64encode(raw_bytes).decode("ascii")

    @pytest.mark.parametrize(
        "media_type",
        ["image/png", "image/jpeg", "image/webp", "image/gif"],
    )
    def test_data_url_prefix_matches_media_type(
        self,
        tmp_path: Path,
        media_type: str,
    ) -> None:
        """The ``data:`` URL prefix matches the block's media_type exactly.

        Covers all four MIME types D-13-3 permits on
        :class:`ImageContent`.
        """
        rel_path = "images/turn-0/0.bin"
        target = tmp_path / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = b"raw-image-bytes"
        target.write_bytes(payload)

        # Cast through Literal — pytest parametrize hands us a runtime str,
        # but the schema only accepts the four allowed values, all of which
        # pytest is iterating through here.
        msg = ConversationMessage(
            role="user",
            content=[
                ImageContent(workspace_path=rel_path, media_type=media_type),  # type: ignore[arg-type]
            ],
            created_at=_now(),
        )
        out = _message_to_openai(
            msg,
            workspace_root=tmp_path,
            supports_vision=True,
            backend="openai",
            model="gpt-4o",
        )
        url = out["content"][0]["image_url"]["url"]
        expected_b64 = base64.standard_b64encode(payload).decode("ascii")
        assert url == f"data:{media_type};base64,{expected_b64}"

    def test_multi_image_preserved_in_order(
        self,
        workspace_with_four_images: tuple[Path, list[tuple[str, bytes]]],
    ) -> None:
        """Four-image content (D-13-5 cap) preserved in declaration order."""
        workspace_root, entries = workspace_with_four_images
        blocks_in: list[TextContent | ImageContent] = [TextContent(text="grid:")]
        blocks_in.extend(
            ImageContent(workspace_path=rel, media_type="image/png") for rel, _ in entries
        )
        msg = ConversationMessage(
            role="user",
            content=blocks_in,
            created_at=_now(),
        )

        out = _message_to_openai(
            msg,
            workspace_root=workspace_root,
            supports_vision=True,
            backend="openai",
            model="gpt-4o",
        )

        parts = out["content"]
        assert len(parts) == 5  # 1 text + 4 images
        assert parts[0] == {"type": "text", "text": "grid:"}
        for i, (_, payload) in enumerate(entries, start=1):
            part = parts[i]
            assert part["type"] == "image_url"
            expected_b64 = base64.standard_b64encode(payload).decode("ascii")
            assert part["image_url"] == {
                "url": f"data:image/png;base64,{expected_b64}",
            }

    def test_text_only_str_byte_for_byte_unchanged(self) -> None:
        """Text-only ``content=str`` round-trips identically (Phase 1 wire shape).

        The str path must remain byte-for-byte identical to the
        pre-T06 serialiser so the T01 cross-cutting corpus continues
        to pass on the OpenAI side too.
        """
        msg = ConversationMessage(
            role="user",
            content="hello world",
            created_at=_now(),
        )
        snapshot = {"role": "user", "content": "hello world"}

        # All four combinations of workspace-root and supports_vision
        # must yield the same str-path snapshot — the workspace_root
        # parameter is irrelevant for the str path.
        for ws in (None, Path("/tmp/persona-ws-irrelevant")):
            for sv in (True, False):
                out = _message_to_openai(
                    msg,
                    workspace_root=ws,
                    supports_vision=sv,
                    backend="openai",
                    model="gpt-4o",
                )
                assert out == snapshot

    def test_system_str_content_path_unchanged(self) -> None:
        """system role text-only stays a str message body (Phase 1)."""
        msg = ConversationMessage(
            role="system",
            content="you are helpful",
            created_at=_now(),
        )
        out = _message_to_openai(
            msg,
            workspace_root=None,
            supports_vision=True,
            backend="openai",
            model="gpt-4o",
        )
        assert out == {"role": "system", "content": "you are helpful"}

    def test_tool_role_str_path_unchanged(self) -> None:
        """``role="tool"`` str path keeps ``tool_call_id`` plumbing."""
        msg = ConversationMessage(
            role="tool",
            content="42",
            created_at=_now(),
            metadata={"tool_call_id": "call_abc"},
        )
        out = _message_to_openai(
            msg,
            workspace_root=None,
            supports_vision=True,
            backend="openai",
            model="gpt-4o",
        )
        assert out == {
            "role": "tool",
            "content": "42",
            "tool_call_id": "call_abc",
        }

    def test_assistant_with_tool_calls_str_path_unchanged(self) -> None:
        """The assistant.tool_calls Phase 1 branch is untouched.

        T06 must not regress the OpenAI native-function-calling round
        trip — arguments stays a JSON string, the tool_calls payload
        is emitted on the assistant message exactly as before.
        """
        from persona.schema.tools import ToolCall

        msg = ConversationMessage(
            role="assistant",
            content="searching",
            created_at=_now(),
            tool_calls=[ToolCall(name="web_search", args={"q": "x"}, call_id="call_1")],
        )
        out = _message_to_openai(
            msg,
            workspace_root=None,
            supports_vision=True,
            backend="openai",
            model="gpt-4o",
        )
        assert out["role"] == "assistant"
        assert out["content"] == "searching"
        assert out["tool_calls"] == [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "web_search", "arguments": '{"q": "x"}'},
            }
        ]

    def test_raises_when_supports_vision_false(
        self, workspace_with_image: tuple[Path, str, bytes]
    ) -> None:
        """A list-form msg with an image -> BackendVisionNotSupportedError when off.

        The check fires BEFORE the filesystem read; the structured
        context carries backend / model / image_count so the runtime
        re-dispatcher has what it needs (D-13-X-error-hierarchy).
        """
        workspace_root, rel_path, _ = workspace_with_image
        msg = ConversationMessage(
            role="user",
            content=[
                TextContent(text="look"),
                ImageContent(workspace_path=rel_path, media_type="image/png"),
            ],
            created_at=_now(),
        )
        with pytest.raises(BackendVisionNotSupportedError) as exc_info:
            _message_to_openai(
                msg,
                workspace_root=workspace_root,
                supports_vision=False,
                backend="deepseek",
                model="deepseek-chat",
            )
        ctx = exc_info.value.context
        assert ctx["backend"] == "deepseek"
        assert ctx["model"] == "deepseek-chat"
        assert ctx["image_count"] == "1"

    def test_raises_when_workspace_root_is_none(
        self, workspace_with_image: tuple[Path, str, bytes]
    ) -> None:
        """No workspace_root + image block -> BackendVisionNotSupportedError."""
        _, rel_path, _ = workspace_with_image
        msg = ConversationMessage(
            role="user",
            content=[ImageContent(workspace_path=rel_path, media_type="image/png")],
            created_at=_now(),
        )
        with pytest.raises(BackendVisionNotSupportedError) as exc_info:
            _message_to_openai(
                msg,
                workspace_root=None,
                supports_vision=True,
                backend="openai",
                model="gpt-4o",
            )
        ctx = exc_info.value.context
        assert ctx["backend"] == "openai"
        assert ctx["model"] == "gpt-4o"
        assert ctx["image_count"] == "1"
        assert ctx.get("reason") == "missing_workspace_root"

    def test_raises_before_filesystem_touch_when_supports_vision_false(
        self, tmp_path: Path
    ) -> None:
        """Vision-off path raises BEFORE touching the workspace.

        The fixture file does not exist on disk; if the serialiser
        read bytes before the capability check, this would raise
        :class:`FileNotFoundError` instead.
        """
        msg = ConversationMessage(
            role="user",
            content=[ImageContent(workspace_path="does/not/exist.png", media_type="image/png")],
            created_at=_now(),
        )
        with pytest.raises(BackendVisionNotSupportedError):
            _message_to_openai(
                msg,
                workspace_root=tmp_path,
                supports_vision=False,
                backend="groq",
                model="llama-3.3-70b-versatile",
            )

    def test_list_with_no_images_does_not_require_workspace(self) -> None:
        """A list of text-only blocks doesn't trigger the vision guard.

        The guard counts ImageContent specifically; a multi-text list
        goes through without needing a workspace_root.
        """
        msg = ConversationMessage(
            role="user",
            content=[
                TextContent(text="first"),
                TextContent(text="second"),
            ],
            created_at=_now(),
        )
        out = _message_to_openai(
            msg,
            workspace_root=None,
            supports_vision=False,
            backend="deepseek",
            model="deepseek-chat",
        )
        assert out == {
            "role": "user",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
            ],
        }
