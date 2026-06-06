"""Vision-path tests for :class:`HFLocalBackend` (Spec 13 T07).

The local HF backend is matrix-empty at launch (D-13-3) — the fail-loud
:class:`BackendVisionNotSupportedError` raise IS the v0.1 vision
contract. Both ``chat`` and ``chat_stream`` must refuse list-form
content carrying :class:`ImageContent` *before* the lazy weight load
fires, so the failure mode is loud and the runtime tier-selector can
re-dispatch without paying for a model load.
"""

# ruff: noqa: ANN401, SLF001, ARG001, ARG002, ARG003 — fixtures, mock Any

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from persona.backends.config import BackendConfig
from persona.backends.errors import BackendVisionNotSupportedError
from persona.errors import PersonaError
from persona.schema.content import ImageContent, TextContent
from persona.schema.conversation import ConversationMessage


def _image_msg() -> ConversationMessage:
    return ConversationMessage(
        role="user",
        content=[
            TextContent(text="describe"),
            ImageContent(workspace_path="img/cat.png", media_type="image/png"),
        ],
        created_at=datetime.now(UTC),
    )


def _text_msg(text: str = "hello") -> ConversationMessage:
    return ConversationMessage(role="user", content=text, created_at=datetime.now(UTC))


@pytest.fixture
def fake_torch() -> Any:
    module = types.ModuleType("torch")
    module.bfloat16 = "bfloat16"  # type: ignore[attr-defined]
    module.float16 = "float16"  # type: ignore[attr-defined]
    module.no_grad = MagicMock(  # type: ignore[attr-defined]
        return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock())
    )
    return module


@pytest.fixture
def fake_transformers() -> Any:
    module = types.ModuleType("transformers")

    class FakeTokenizer:
        @classmethod
        def from_pretrained(cls, model_id: str, **kwargs: Any) -> FakeTokenizer:
            return cls()

    class FakeModel:
        @classmethod
        def from_pretrained(cls, model_id: str, **kwargs: Any) -> FakeModel:
            return cls()

    class FakeGenerationConfig:
        pass

    module.AutoTokenizer = FakeTokenizer  # type: ignore[attr-defined]
    module.AutoModelForCausalLM = FakeModel  # type: ignore[attr-defined]
    module.GenerationConfig = FakeGenerationConfig  # type: ignore[attr-defined]
    return module


@pytest.fixture
def patched_imports(fake_torch: Any, fake_transformers: Any) -> Any:
    original_modules = sys.modules.copy()
    sys.modules["torch"] = fake_torch
    sys.modules["transformers"] = fake_transformers
    yield
    sys.modules.clear()
    sys.modules.update(original_modules)


def _config(model_id: str = "google/gemma-2-9b-it") -> BackendConfig:
    return BackendConfig(
        provider="local",
        model="local-stub",
        local_model_id=model_id,
        local_quantization="4bit",  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Default (supports_vision=False): fail-loud on image content
# ---------------------------------------------------------------------------


class TestHFLocalFailsLoud:
    @pytest.mark.asyncio
    async def test_chat_raises_on_image_content(self, patched_imports: Any) -> None:
        from persona.backends.hf_local import HFLocalBackend

        backend = HFLocalBackend(_config())
        # The guard fires before _ensure_loaded — _model stays None.
        with pytest.raises(BackendVisionNotSupportedError) as info:
            await backend.chat([_image_msg()])

        err = info.value
        assert err.context["backend"] == "hf_local"
        assert err.context["model"] == "google/gemma-2-9b-it"
        assert err.context["image_count"] == "1"
        assert backend._model is None  # weights never loaded

    @pytest.mark.asyncio
    async def test_chat_stream_raises_on_image_content(self, patched_imports: Any) -> None:
        from persona.backends.hf_local import HFLocalBackend

        backend = HFLocalBackend(_config(model_id="meta-llama/Llama-3.1-8B-Instruct"))
        with pytest.raises(BackendVisionNotSupportedError) as info:
            async for _ in backend.chat_stream([_image_msg()]):
                pass  # pragma: no cover — guard fires before first yield

        err = info.value
        assert err.context == {
            "backend": "hf_local",
            "model": "meta-llama/Llama-3.1-8B-Instruct",
            "image_count": "1",
        }
        assert backend._model is None

    @pytest.mark.asyncio
    async def test_image_count_aggregates_across_messages(self, patched_imports: Any) -> None:
        from persona.backends.hf_local import HFLocalBackend

        backend = HFLocalBackend(_config())
        msgs = [
            _image_msg(),
            _text_msg("between"),
            ConversationMessage(
                role="user",
                content=[
                    ImageContent(workspace_path="a.png", media_type="image/png"),
                    ImageContent(workspace_path="b.jpg", media_type="image/jpeg"),
                ],
                created_at=datetime.now(UTC),
            ),
        ]
        with pytest.raises(BackendVisionNotSupportedError) as info:
            await backend.chat(msgs)
        assert info.value.context["image_count"] == "3"

    def test_supports_vision_is_false(self, patched_imports: Any) -> None:
        from persona.backends.hf_local import HFLocalBackend

        backend = HFLocalBackend(_config())
        assert backend.supports_vision is False

    def test_error_caught_as_persona_error(self) -> None:
        # Flat hierarchy (D-13-X + D-03-1): BackendVisionNotSupportedError
        # is a direct subclass of PersonaError.
        with pytest.raises(PersonaError):
            raise BackendVisionNotSupportedError(
                context={"backend": "hf_local", "model": "x", "image_count": "1"}
            )
