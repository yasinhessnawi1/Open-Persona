"""Embedder warm-up (Spec 32 Feature A, task A1).

The shared ``bge`` embedder loads lazily; the first ``encode()`` materialises the
model synchronously and blocks the agent loop — the *root* cause of the
first-turn truncation. ``start_embedder_warmup`` pays that cold load once, off
the event loop, at session build, and returns the task so the turn-0 path can
gate on its completion (D-32-X-warmup-gates-turn0).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence

import pytest
from persona_voice.agent.warmup import start_embedder_warmup

pytestmark = [pytest.mark.asyncio]


class _RecordingEmbedder:
    """Minimal :class:`persona.stores.Embedder` that records its encode calls."""

    model_name = "fake-warmup"
    dimension = 3

    def __init__(self, on_encode: object = None) -> None:
        self.calls: list[list[str]] = []
        self.threads: list[str] = []
        self._on_encode = on_encode

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        self.threads.append(threading.current_thread().name)
        batch = list(texts)
        self.calls.append(batch)
        if callable(self._on_encode):
            self._on_encode()
        return [[0.0] * self.dimension for _ in batch]


async def test_warmup_encodes_once_with_nonempty_text() -> None:
    embedder = _RecordingEmbedder()

    await start_embedder_warmup(embedder)

    assert len(embedder.calls) == 1
    batch = embedder.calls[0]
    assert batch, "warm-up must encode a non-empty batch"
    assert all(isinstance(t, str) and t for t in batch), "warm-up text must be non-empty strings"


async def test_warmup_runs_off_the_event_loop_thread() -> None:
    embedder = _RecordingEmbedder()
    loop_thread = threading.current_thread().name

    await start_embedder_warmup(embedder)

    assert embedder.threads == [embedder.threads[0]]
    assert embedder.threads[0] != loop_thread, "encode must run in a worker thread, not the loop"


async def test_warmup_does_not_block_the_caller() -> None:
    started = threading.Event()
    release = threading.Event()

    def _block() -> None:
        started.set()
        release.wait(timeout=5.0)

    embedder = _RecordingEmbedder(on_encode=_block)
    task = start_embedder_warmup(embedder)

    # Yield to the loop so the task can dispatch encode to a worker thread, then
    # observe that the caller regained control while encode is still in flight.
    await asyncio.sleep(0.05)
    assert started.is_set(), "encode should be running on a worker thread"
    assert not task.done(), "start must return before the cold encode completes"

    release.set()
    await task
    assert task.done()


async def test_warmup_swallows_encode_failure() -> None:
    def _boom() -> None:
        raise RuntimeError("cold load failed")

    embedder = _RecordingEmbedder(on_encode=_boom)

    # A failing warm-up must complete cleanly (never propagate) so the turn-0
    # gate sees completion and degrades, never an unhandled task exception.
    await start_embedder_warmup(embedder)
