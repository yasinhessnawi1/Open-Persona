"""Tests for ``persona.imagegen.result`` — boundary types (spec 15 T03)."""

from __future__ import annotations

import pytest
from persona.imagegen import (
    GeneratedImage,
    GenerationResult,
    ImageGenOptions,
)
from pydantic import ValidationError


class TestImageGenOptions:
    """``ImageGenOptions`` — neutral request knobs (D-15-3)."""

    def test_defaults(self) -> None:
        options = ImageGenOptions()
        assert options.size == "1024x1024"
        assert options.count == 1
        assert options.quality == "standard"

    def test_explicit_values(self) -> None:
        options = ImageGenOptions(size="1024x1792", count=2, quality="high")
        assert options.size == "1024x1792"
        assert options.count == 2
        assert options.quality == "high"

    @pytest.mark.parametrize("size", ["1024x1024", "1024x1792", "1792x1024"])
    def test_accepts_all_three_presets(self, size: str) -> None:
        options = ImageGenOptions(size=size)  # type: ignore[arg-type]
        assert options.size == size

    def test_rejects_arbitrary_size_string(self) -> None:
        with pytest.raises(ValidationError):
            ImageGenOptions(size="512x512")  # type: ignore[arg-type]

    @pytest.mark.parametrize("count", [1, 2, 3, 4])
    def test_count_in_range(self, count: int) -> None:
        options = ImageGenOptions(count=count)
        assert options.count == count

    @pytest.mark.parametrize("bad_count", [0, -1, 5, 10])
    def test_count_cap_enforced(self, bad_count: int) -> None:
        # D-15-3 + LF-13-2: count uses Field(ge=1, le=4). The cap is the
        # expressive-headroom-vs-cost-containment lever — at pre-deduct
        # + per-user advisory-lock cap=1, count=4 stays bounded
        # ($0.16–$0.668/call OpenAI medium→high, $0.16 fal flat) and the
        # parallel-fire denial-of-wallet surface T17 proves closed
        # holds regardless of count.
        with pytest.raises(ValidationError):
            ImageGenOptions(count=bad_count)

    @pytest.mark.parametrize("quality", ["standard", "high"])
    def test_accepts_quality_literal(self, quality: str) -> None:
        options = ImageGenOptions(quality=quality)  # type: ignore[arg-type]
        assert options.quality == quality

    def test_rejects_unknown_quality(self) -> None:
        with pytest.raises(ValidationError):
            ImageGenOptions(quality="ultra")  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        options = ImageGenOptions()
        with pytest.raises(ValidationError):
            options.count = 2  # type: ignore[misc]

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ImageGenOptions.model_validate(
                {"size": "1024x1024", "count": 1, "quality": "standard", "seed": 42}
            )

    def test_json_round_trip(self) -> None:
        options = ImageGenOptions(size="1792x1024", count=2, quality="high")
        dumped = options.model_dump(mode="json")
        assert dumped == {"size": "1792x1024", "count": 2, "quality": "high"}
        assert ImageGenOptions.model_validate(dumped) == options


class TestGeneratedImage:
    """``GeneratedImage`` — backend bytes + service workspace_path."""

    def test_construct_from_backend_layer(self) -> None:
        # Backend populates image_bytes; workspace_path is None.
        img = GeneratedImage(
            image_bytes=b"\x89PNG\r\n\x1a\n",
            media_type="image/png",
            width=1024,
            height=1024,
        )
        assert img.image_bytes == b"\x89PNG\r\n\x1a\n"
        assert img.workspace_path is None
        assert img.media_type == "image/png"
        assert img.width == 1024
        assert img.height == 1024
        assert img.revised_prompt is None

    def test_service_layer_rewrites_with_workspace_path(self) -> None:
        # Backend returns bytes; service writes to disk then model_copy's
        # with workspace_path set + image_bytes zeroed for the response.
        from_backend = GeneratedImage(
            image_bytes=b"binary data",
            media_type="image/png",
            width=1024,
            height=1024,
            revised_prompt="a watercolour cat",
        )
        from_service = from_backend.model_copy(
            update={"workspace_path": "uploads/deadbeef.png", "image_bytes": b""}
        )
        assert from_service.workspace_path == "uploads/deadbeef.png"
        assert from_service.image_bytes == b""
        assert from_service.media_type == "image/png"
        assert from_service.revised_prompt == "a watercolour cat"

    @pytest.mark.parametrize("media_type", ["image/png", "image/jpeg", "image/webp"])
    def test_accepts_all_allowed_media_types(self, media_type: str) -> None:
        img = GeneratedImage(
            media_type=media_type,  # type: ignore[arg-type]
            width=1,
            height=1,
        )
        assert img.media_type == media_type

    def test_rejects_unknown_media_type(self) -> None:
        with pytest.raises(ValidationError):
            GeneratedImage(
                media_type="image/gif",  # type: ignore[arg-type]
                width=1,
                height=1,
            )

    @pytest.mark.parametrize("bad", [0, -1, -1024])
    def test_width_must_be_positive(self, bad: int) -> None:
        with pytest.raises(ValidationError):
            GeneratedImage(media_type="image/png", width=bad, height=1)

    @pytest.mark.parametrize("bad", [0, -1, -1024])
    def test_height_must_be_positive(self, bad: int) -> None:
        with pytest.raises(ValidationError):
            GeneratedImage(media_type="image/png", width=1, height=bad)

    def test_image_bytes_default_is_empty(self) -> None:
        img = GeneratedImage(media_type="image/png", width=1, height=1)
        assert img.image_bytes == b""

    def test_frozen(self) -> None:
        img = GeneratedImage(media_type="image/png", width=1, height=1)
        with pytest.raises(ValidationError):
            img.workspace_path = "uploads/x.png"  # type: ignore[misc]

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GeneratedImage.model_validate(
                {
                    "media_type": "image/png",
                    "width": 1,
                    "height": 1,
                    "seed": 42,
                }
            )

    def test_json_round_trip(self) -> None:
        img = GeneratedImage(
            workspace_path="uploads/abc.png",
            media_type="image/png",
            width=1024,
            height=1024,
            revised_prompt="a cat",
        )
        dumped = img.model_dump(mode="json")
        # bytes serialises as a string in JSON mode; round-trip must match.
        assert GeneratedImage.model_validate(dumped) == img


class TestGenerationResult:
    """``GenerationResult`` — aggregate response from a backend call."""

    def _img(self, *, workspace_path: str | None = None) -> GeneratedImage:
        return GeneratedImage(
            workspace_path=workspace_path,
            media_type="image/png",
            width=1024,
            height=1024,
        )

    def test_construct_minimal(self) -> None:
        result = GenerationResult(
            images=[self._img()],
            provider="openai",
            model="gpt-image-1",
            latency_ms=4200.5,
        )
        assert len(result.images) == 1
        assert result.provider == "openai"
        assert result.model == "gpt-image-1"
        assert result.latency_ms == 4200.5

    def test_multi_image_batch(self) -> None:
        result = GenerationResult(
            images=[
                self._img(workspace_path="uploads/a.png"),
                self._img(workspace_path="uploads/b.png"),
            ],
            provider="fal",
            model="fal-ai/flux-pro/v1.1",
            latency_ms=8000.0,
        )
        assert len(result.images) == 2

    def test_at_least_one_image_required(self) -> None:
        with pytest.raises(ValidationError):
            GenerationResult(
                images=[],
                provider="openai",
                model="gpt-image-1",
                latency_ms=1.0,
            )

    def test_negative_latency_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GenerationResult(
                images=[self._img()],
                provider="openai",
                model="gpt-image-1",
                latency_ms=-0.1,
            )

    def test_zero_latency_allowed(self) -> None:
        # Useful for fake-backend tests; matches ChatResponse precedent.
        result = GenerationResult(
            images=[self._img()],
            provider="fake",
            model="fake",
            latency_ms=0.0,
        )
        assert result.latency_ms == 0.0

    def test_frozen(self) -> None:
        result = GenerationResult(
            images=[self._img()],
            provider="openai",
            model="gpt-image-1",
            latency_ms=1.0,
        )
        with pytest.raises(ValidationError):
            result.provider = "fal"  # type: ignore[misc]

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GenerationResult.model_validate(
                {
                    "images": [self._img().model_dump()],
                    "provider": "openai",
                    "model": "gpt-image-1",
                    "latency_ms": 1.0,
                    "credits_charged": 100,
                }
            )

    def test_json_round_trip(self) -> None:
        result = GenerationResult(
            images=[self._img(workspace_path="uploads/x.png")],
            provider="openai",
            model="gpt-image-1",
            latency_ms=4200.5,
        )
        dumped = result.model_dump(mode="json")
        assert GenerationResult.model_validate(dumped) == result

    def test_images_list_is_immutable_after_construction(self) -> None:
        # Pydantic v2 frozen models prevent reassignment; the underlying
        # list is still a list, but the model itself can't swap it.
        result = GenerationResult(
            images=[self._img()],
            provider="openai",
            model="gpt-image-1",
            latency_ms=1.0,
        )
        with pytest.raises(ValidationError):
            result.images = []  # type: ignore[misc]


class TestReExportsFromInit:
    """``persona.imagegen`` re-exports the boundary types (task scope)."""

    def test_all_three_models_importable_from_package(self) -> None:
        from persona.imagegen import GeneratedImage as _GeneratedImage
        from persona.imagegen import GenerationResult as _GenerationResult
        from persona.imagegen import ImageGenOptions as _ImageGenOptions

        assert _ImageGenOptions is ImageGenOptions
        assert _GeneratedImage is GeneratedImage
        assert _GenerationResult is GenerationResult

    def test_literal_aliases_importable_from_package(self) -> None:
        # The Literal aliases support type-narrowing in the per-provider
        # backends (T06 + T07); re-export keeps the public surface tidy.
        from persona.imagegen import ImageMediaType, ImageQuality, ImageSize

        assert ImageSize is not None
        assert ImageQuality is not None
        assert ImageMediaType is not None
