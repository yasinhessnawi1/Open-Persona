"""Tests for ``HFLocalBackend``.

All tests mock ``transformers`` / ``torch`` so no real weights load and
no GPU is required. The warm-up NaN guard requires real bitsandbytes
init and lives behind ``@pytest.mark.external`` (not in this file).
"""

# ruff: noqa: ANN401, SLF001, ARG001, ARG002, ARG003, ERA001 — fixtures, mock Any, section headers

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from persona.backends.config import BackendConfig
from persona.backends.errors import ModelNotFoundError, ProviderError
from persona.backends.protocol import ChatBackend
from persona.backends.types import StreamChunk, ToolSpec
from persona.schema.conversation import ConversationMessage


def _msg(role: str, content: str) -> ConversationMessage:
    return ConversationMessage(role=role, content=content, created_at=datetime.now(UTC))


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
def fake_transformers(fake_torch: Any) -> Any:  # noqa: ARG001
    module = types.ModuleType("transformers")

    class FakeTokenizer:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.calls: list[Any] = []

        @classmethod
        def from_pretrained(cls, model_id: str, **kwargs: Any) -> FakeTokenizer:
            return cls()

        def __call__(self, text: str, *args: Any, **kwargs: Any) -> Any:
            shape = MagicMock()
            shape.__getitem__ = MagicMock(side_effect=lambda i: 7 if i == 1 else 1)
            ids = MagicMock()
            ids.shape = shape
            ids.to = MagicMock(return_value=ids)  # supports .to(device)
            result = MagicMock()
            result.__getitem__ = MagicMock(return_value=ids)
            result.to = MagicMock(return_value=result)
            return result

        def apply_chat_template(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
            return "\n".join(f"{m['role']}: {m['content']}" for m in messages)

        def decode(self, ids: Any, **kwargs: Any) -> str:
            return "generated text"

    class FakeModel:
        device = "cuda:0"
        generation_config: Any = None

        @classmethod
        def from_pretrained(cls, model_id: str, **kwargs: Any) -> FakeModel:
            return cls()

        def generate(self, *args: Any, **kwargs: Any) -> Any:
            shape = MagicMock()
            shape.__getitem__ = MagicMock(return_value=10)
            row = MagicMock()
            row.__getitem__ = MagicMock(return_value=row)
            row.shape = shape
            out = MagicMock()
            out.__getitem__ = MagicMock(return_value=row)
            return out

    class FakeBitsAndBytesConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class FakeGenerationConfig:
        pass

    class FakeStoppingCriteriaList(list):  # type: ignore[type-arg]
        def __init__(self, criteria: list[Any]) -> None:
            super().__init__(criteria)

    class FakeAsyncStreamer:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._items: list[str] = ["Hel", "lo"]
            self._index = 0

        def __aiter__(self) -> FakeAsyncStreamer:
            return self

        async def __anext__(self) -> str:
            if self._index >= len(self._items):
                raise StopAsyncIteration
            item = self._items[self._index]
            self._index += 1
            return item

        def end(self) -> None:
            return None

    module.AutoTokenizer = FakeTokenizer  # type: ignore[attr-defined]
    module.AutoModelForCausalLM = FakeModel  # type: ignore[attr-defined]
    module.BitsAndBytesConfig = FakeBitsAndBytesConfig  # type: ignore[attr-defined]
    module.GenerationConfig = FakeGenerationConfig  # type: ignore[attr-defined]
    module.StoppingCriteriaList = FakeStoppingCriteriaList  # type: ignore[attr-defined]
    module.AsyncTextIteratorStreamer = FakeAsyncStreamer  # type: ignore[attr-defined]
    return module


@pytest.fixture
def patched_imports(fake_torch: Any, fake_transformers: Any) -> Any:
    """Patch ``importlib.import_module`` and ``sys.modules`` for transformers/torch."""
    original_modules = sys.modules.copy()
    sys.modules["torch"] = fake_torch
    sys.modules["transformers"] = fake_transformers
    yield
    sys.modules.clear()
    sys.modules.update(original_modules)


def _config(
    *,
    model_id: str = "google/gemma-2-9b-it",
    quantization: str = "4bit",
) -> BackendConfig:
    return BackendConfig(
        provider="local",
        model="local-stub",
        local_model_id=model_id,
        local_quantization=quantization,  # type: ignore[arg-type]
    )


# -----------------------------------------------------------------------------
# Construction
# -----------------------------------------------------------------------------


class TestConstruction:
    def test_constructs_without_loading_weights(self, patched_imports: Any) -> None:
        from persona.backends.hf_local import HFLocalBackend

        backend = HFLocalBackend(_config())
        assert backend.provider_name == "local"
        assert backend.model_name == "google/gemma-2-9b-it"
        assert backend.supports_native_tools is False
        # D-02-10 — weights NOT loaded at construction.
        assert backend._model is None
        assert backend._tokenizer is None

    def test_protocol_membership(self, patched_imports: Any) -> None:
        from persona.backends.hf_local import HFLocalBackend

        assert isinstance(HFLocalBackend(_config()), ChatBackend)

    def test_missing_model_id_raises(self, patched_imports: Any) -> None:
        from persona.backends.hf_local import HFLocalBackend

        config = BackendConfig(provider="local", model="x", local_model_id=None)
        with pytest.raises(ModelNotFoundError):
            HFLocalBackend(config)

    def test_wrong_provider_raises(self, patched_imports: Any) -> None:
        from persona.backends.hf_local import HFLocalBackend

        config = BackendConfig(provider="ollama", model="llama3")
        with pytest.raises(ProviderError):
            HFLocalBackend(config)


# -----------------------------------------------------------------------------
# Lazy load
# -----------------------------------------------------------------------------


class TestLazyLoad:
    @pytest.mark.asyncio
    async def test_first_chat_triggers_load(self, patched_imports: Any) -> None:
        from persona.backends.hf_local import HFLocalBackend

        backend = HFLocalBackend(_config())
        assert backend._model is None
        await backend.chat([_msg("user", "hi")])
        assert backend._model is not None
        assert backend._tokenizer is not None

    @pytest.mark.asyncio
    async def test_second_chat_reuses_loaded_model(self, patched_imports: Any) -> None:
        from persona.backends.hf_local import HFLocalBackend

        backend = HFLocalBackend(_config())
        await backend.chat([_msg("user", "first")])
        first_model = backend._model
        await backend.chat([_msg("user", "second")])
        assert backend._model is first_model

    @pytest.mark.asyncio
    async def test_generation_config_overridden(self, patched_imports: Any) -> None:
        # D-02-11 — `generation_config` is replaced with a fresh GenerationConfig
        # after load, suppressing model-card sampling defaults.
        from persona.backends.hf_local import HFLocalBackend

        backend = HFLocalBackend(_config())
        await backend.chat([_msg("user", "hi")])
        assert backend._model.generation_config is not None
        # Class name comes from our FakeGenerationConfig fixture.
        assert type(backend._model.generation_config).__name__ == "FakeGenerationConfig"


# -----------------------------------------------------------------------------
# System-role fold for Gemma-2 (D-02-11)
# -----------------------------------------------------------------------------


class TestGemma2Fold:
    @pytest.mark.asyncio
    async def test_system_folded_into_first_user_message(self, patched_imports: Any) -> None:
        from persona.backends.hf_local import HFLocalBackend

        backend = HFLocalBackend(_config(model_id="google/gemma-2-9b-it"))
        await backend.chat(
            [
                _msg("system", "You are X."),
                _msg("user", "Hello."),
            ]
        )
        # Fold happens in _fold_system_for_gemma2; we test the helper directly.
        folded = backend._fold_system_for_gemma2(
            [
                _msg("system", "You are X."),
                _msg("user", "Hello."),
            ]
        )
        assert len(folded) == 1
        assert folded[0]["role"] == "user"
        assert "You are X." in folded[0]["content"]
        assert "Hello." in folded[0]["content"]

    @pytest.mark.asyncio
    async def test_no_fold_for_non_gemma_model(self, patched_imports: Any) -> None:
        from persona.backends.hf_local import HFLocalBackend

        backend = HFLocalBackend(_config(model_id="meta-llama/Llama-3.1-8B-Instruct"))
        folded = backend._fold_system_for_gemma2(
            [
                _msg("system", "You are X."),
                _msg("user", "Hi"),
            ]
        )
        assert len(folded) == 2
        assert folded[0]["role"] == "system"
        assert folded[1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_fold_with_no_user_message_creates_one(self, patched_imports: Any) -> None:
        from persona.backends.hf_local import HFLocalBackend

        backend = HFLocalBackend(_config(model_id="google/gemma-2-9b-it"))
        folded = backend._fold_system_for_gemma2([_msg("system", "You are X.")])
        assert len(folded) == 1
        assert folded[0]["role"] == "user"


# -----------------------------------------------------------------------------
# chat() shape
# -----------------------------------------------------------------------------


class TestChat:
    @pytest.mark.asyncio
    async def test_chat_returns_response(self, patched_imports: Any) -> None:
        from persona.backends.hf_local import HFLocalBackend

        backend = HFLocalBackend(_config())
        response = await backend.chat([_msg("user", "hi")])
        assert response.content == "generated text"
        assert response.provider == "local"
        assert response.tool_calls == []

    @pytest.mark.asyncio
    async def test_chat_with_shim_tool_call(self, patched_imports: Any) -> None:
        from persona.backends.hf_local import HFLocalBackend

        backend = HFLocalBackend(_config())
        # Patch the decoded output to contain a tool call.
        backend = HFLocalBackend(_config())
        with patch.object(
            backend,
            "_generate_blocking",
            return_value=('Searching {"tool": "web_search", "args": {"q": "x"}}', 5),
        ):
            await backend._ensure_loaded()
            response = await backend.chat(
                [_msg("user", "search")],
                tools=[ToolSpec(name="web_search", description="x", parameters={})],
            )
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "web_search"


# -----------------------------------------------------------------------------
# Section: streaming
# -----------------------------------------------------------------------------


class TestStream:
    @pytest.mark.asyncio
    async def test_stream_yields_chunks_and_final(self, patched_imports: Any) -> None:
        from persona.backends.hf_local import HFLocalBackend

        backend = HFLocalBackend(_config())
        chunks: list[StreamChunk] = []
        async for c in backend.chat_stream([_msg("user", "hi")]):
            chunks.append(c)
        text_chunks = [c for c in chunks if not c.is_final and c.delta]
        finals = [c for c in chunks if c.is_final]
        assert len(text_chunks) >= 1
        assert len(finals) == 1
        assert finals[0].usage is not None


# -----------------------------------------------------------------------------
# Import guard
# -----------------------------------------------------------------------------


class TestImportGuard:
    def test_missing_transformers_raises_with_install_hint(self) -> None:
        # Simulate `import transformers` failing.
        original = sys.modules.pop("transformers", None)
        try:
            with patch("importlib.import_module", side_effect=ImportError("no module")):
                from persona.backends.errors import AuthenticationError
                from persona.backends.hf_local import HFLocalBackend

                with pytest.raises(AuthenticationError) as info:
                    HFLocalBackend(_config())
                assert "[local] extras" in str(info.value)
        finally:
            if original is not None:
                sys.modules["transformers"] = original


# -----------------------------------------------------------------------------
# Quantisation config
# -----------------------------------------------------------------------------


class TestQuantizationConfig:
    def test_4bit_builds_nf4_config(
        self, patched_imports: Any, fake_torch: Any, fake_transformers: Any
    ) -> None:
        from persona.backends.hf_local import _build_quantization_config

        cfg = _build_quantization_config(fake_transformers, fake_torch, mode="4bit")
        assert cfg is not None
        assert cfg.kwargs["load_in_4bit"] is True
        assert cfg.kwargs["bnb_4bit_quant_type"] == "nf4"

    def test_8bit_builds_int8_config(self, fake_torch: Any, fake_transformers: Any) -> None:
        from persona.backends.hf_local import _build_quantization_config

        cfg = _build_quantization_config(fake_transformers, fake_torch, mode="8bit")
        assert cfg is not None
        assert cfg.kwargs["load_in_8bit"] is True

    def test_none_returns_none(self, fake_torch: Any, fake_transformers: Any) -> None:
        from persona.backends.hf_local import _build_quantization_config

        cfg = _build_quantization_config(fake_transformers, fake_torch, mode="none")
        assert cfg is None
