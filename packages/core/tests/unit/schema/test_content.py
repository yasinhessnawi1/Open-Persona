"""Tests for :mod:`persona.schema.content` — Spec 13 T02 typed content blocks.

Validates the frozen + ``extra="forbid"`` discipline, the four supported
media types per **D-13-3**, JSON-roundtrip identity, and the discriminated
union behaviour that T03 relies on when widening
``ConversationMessage.content``.
"""

from __future__ import annotations

from typing import Any

import pytest
from persona.schema.content import ImageContent, MessageContent, TextContent
from pydantic import TypeAdapter, ValidationError

# Reused across union-validation tests; the adapter is what Pydantic uses
# when MessageContent is the *field* annotation on ConversationMessage.
_MESSAGE_CONTENT_ADAPTER: TypeAdapter[MessageContent] = TypeAdapter(MessageContent)

_VALID_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")


class TestTextContent:
    """``TextContent`` is the text block in a multimodal content list."""

    def test_constructs_with_text(self) -> None:
        block = TextContent(text="hello world")
        assert block.text == "hello world"
        assert block.type == "text"

    def test_type_tag_defaults_to_text(self) -> None:
        # The caller does not need to pass ``type`` — it's always "text".
        dumped = TextContent(text="hi").model_dump()
        assert dumped == {"type": "text", "text": "hi"}

    def test_frozen_rejects_mutation(self) -> None:
        block = TextContent(text="hi")
        with pytest.raises(ValidationError):
            block.text = "bye"  # type: ignore[misc]

    def test_extra_forbid_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            TextContent(text="hi", unknown="x")  # type: ignore[call-arg]

    def test_explicit_wrong_type_tag_rejected(self) -> None:
        # The ``type`` field is a Literal["text"]; anything else fails.
        with pytest.raises(ValidationError):
            TextContent(type="image", text="hi")  # type: ignore[arg-type]

    def test_json_round_trip_is_identity(self) -> None:
        original = TextContent(text="round-trip me")
        recovered = TextContent.model_validate_json(original.model_dump_json())
        assert recovered == original
        assert recovered.model_dump_json() == original.model_dump_json()


class TestImageContent:
    """``ImageContent`` carries a workspace reference, never image bytes."""

    def test_constructs_with_workspace_path_and_media_type(self) -> None:
        block = ImageContent(
            workspace_path="uploads/2026/img-abc.png",
            media_type="image/png",
        )
        assert block.workspace_path == "uploads/2026/img-abc.png"
        assert block.media_type == "image/png"
        assert block.type == "image"

    def test_type_tag_is_always_image_on_dump(self) -> None:
        dumped = ImageContent(workspace_path="x.png", media_type="image/png").model_dump()
        assert dumped["type"] == "image"

    @pytest.mark.parametrize("media_type", _VALID_MEDIA_TYPES)
    def test_each_supported_media_type_accepted(self, media_type: str) -> None:
        block = ImageContent(workspace_path="ref.bin", media_type=media_type)  # type: ignore[arg-type]
        assert block.media_type == media_type

    @pytest.mark.parametrize(
        "bad_media_type",
        [
            "image/tiff",
            "image/bmp",
            "image/svg+xml",
            "application/pdf",
            "text/plain",
            "image/PNG",  # case-sensitive Literal
            "",
        ],
    )
    def test_unsupported_media_type_rejected(self, bad_media_type: str) -> None:
        with pytest.raises(ValidationError):
            ImageContent(
                workspace_path="ref.bin",
                media_type=bad_media_type,  # type: ignore[arg-type]
            )

    def test_explicit_wrong_type_tag_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ImageContent(
                type="text",  # type: ignore[arg-type]
                workspace_path="ref.png",
                media_type="image/png",
            )

    def test_frozen_rejects_mutation(self) -> None:
        block = ImageContent(workspace_path="ref.png", media_type="image/png")
        with pytest.raises(ValidationError):
            block.workspace_path = "other.png"  # type: ignore[misc]

    def test_extra_forbid_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            ImageContent(
                workspace_path="ref.png",
                media_type="image/png",
                inline_bytes=b"...",  # type: ignore[call-arg]
            )

    def test_missing_workspace_path_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ImageContent(media_type="image/png")  # type: ignore[call-arg]

    def test_missing_media_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ImageContent(workspace_path="ref.png")  # type: ignore[call-arg]

    def test_json_round_trip_is_identity(self) -> None:
        original = ImageContent(
            workspace_path="uploads/2026/photo.jpg",
            media_type="image/jpeg",
        )
        recovered = ImageContent.model_validate_json(original.model_dump_json())
        assert recovered == original
        assert recovered.model_dump_json() == original.model_dump_json()


class TestMessageContentUnion:
    """The discriminated union resolves blocks by their ``type`` tag."""

    def test_text_block_validates_through_union(self) -> None:
        payload: dict[str, Any] = {"type": "text", "text": "hello"}
        validated = _MESSAGE_CONTENT_ADAPTER.validate_python(payload)
        assert isinstance(validated, TextContent)
        assert validated.text == "hello"

    def test_image_block_validates_through_union(self) -> None:
        payload: dict[str, Any] = {
            "type": "image",
            "workspace_path": "ref.webp",
            "media_type": "image/webp",
        }
        validated = _MESSAGE_CONTENT_ADAPTER.validate_python(payload)
        assert isinstance(validated, ImageContent)
        assert validated.workspace_path == "ref.webp"
        assert validated.media_type == "image/webp"

    @pytest.mark.parametrize(
        "bad_payload",
        [
            {"type": "audio", "url": "x"},  # unknown discriminator tag
            {"type": "video", "workspace_path": "v.mp4"},  # unknown discriminator tag
            {"text": "no type tag at all"},  # missing discriminator
            {"type": "text"},  # missing text field on otherwise-valid tag
            {
                "type": "image",
                "workspace_path": "ref.tif",
                "media_type": "image/tiff",  # invalid media_type for image tag
            },
        ],
    )
    def test_invalid_payloads_rejected(self, bad_payload: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            _MESSAGE_CONTENT_ADAPTER.validate_python(bad_payload)

    def test_union_round_trip_through_json_preserves_block_type(self) -> None:
        # The discriminator tag survives a JSON round-trip on either branch.
        text_block: MessageContent = TextContent(text="t")
        image_block: MessageContent = ImageContent(workspace_path="r.png", media_type="image/png")
        for original in (text_block, image_block):
            raw = _MESSAGE_CONTENT_ADAPTER.dump_json(original)
            recovered = _MESSAGE_CONTENT_ADAPTER.validate_json(raw)
            assert type(recovered) is type(original)
            assert recovered == original
