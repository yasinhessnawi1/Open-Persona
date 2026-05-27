"""Sentence-transformers wrapper with lazy model loading.

Persona-RAG pattern (carried forward): the embedder doesn't load its model
until the first encode call. The cost is ~3s on M1 / ~5s on CPU. Lazy
loading means CLI commands that only read or only list don't pay it.

The :class:`Embedder` protocol exists so spec 07's Postgres backend (or a
future remote-embedding adapter) can swap in without changing the store
layer. ``SentenceTransformerEmbedder`` is the v0.1 concrete.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from persona.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

_log = get_logger("stores.embedder")

__all__ = ["Embedder", "SentenceTransformerEmbedder"]


@runtime_checkable
class Embedder(Protocol):
    """Compute float-vector embeddings for text.

    Implementations are expected to be deterministic for a given input
    (modulo floating-point variation across hardware) and to return L2-
    normalised vectors so cosine similarity behaves as expected.
    """

    model_name: str

    # ``dimension`` is a property in the concrete impl (lazy load); declaring
    # it as a read-only property on the Protocol keeps mypy happy with both
    # the property-based ``SentenceTransformerEmbedder`` and plain-attribute
    # test embedders (which still satisfy the descriptor protocol).
    @property
    def dimension(self) -> int: ...

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        """Encode a batch of texts to vectors.

        Returns one vector per input, each of length ``self.dimension``.
        Empty input yields empty output.
        """
        ...


class SentenceTransformerEmbedder:
    """``sentence-transformers``-backed embedder with lazy model load.

    Args:
        model_name: HuggingFace model id (default ``BAAI/bge-small-en-v1.5``
            per D-01 architecture decision §9.6).
        normalize: L2-normalise embeddings so cosine similarity = dot product.
        device: ``"auto"`` (detect), ``"cuda"``, ``"mps"``, ``"cpu"``.
    """

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-small-en-v1.5",
        normalize: bool = True,
        device: str = "auto",
    ) -> None:
        self.model_name = model_name
        self._normalize = normalize
        self._device = device
        self._model: object | None = None
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        """Embedding dimension reported by the loaded model."""
        if self._dimension is None:
            self._load()
        assert self._dimension is not None
        return self._dimension

    def _load(self) -> object:
        if self._model is not None:
            return self._model
        from sentence_transformers import SentenceTransformer

        _log.info(
            "loading sentence-transformers model={model} device={device}",
            model=self.model_name,
            device=self._device,
        )
        if self._device == "auto":
            model = SentenceTransformer(self.model_name)
        else:
            model = SentenceTransformer(self.model_name, device=self._device)
        dim = model.get_sentence_embedding_dimension()
        if dim is None:
            msg = f"sentence-transformers model {self.model_name!r} reports no dimension"
            raise RuntimeError(msg)
        self._model = model
        self._dimension = int(dim)
        return model

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        # SentenceTransformer.encode returns a numpy ndarray; convert to plain
        # lists so the store layer never needs to import numpy.
        vectors = model.encode(  # type: ignore[attr-defined]
            list(texts),
            normalize_embeddings=self._normalize,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [list(map(float, row)) for row in vectors]
