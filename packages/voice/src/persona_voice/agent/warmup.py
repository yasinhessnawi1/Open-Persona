"""Off-loop embedder warm-up (Spec 32 Feature A, task A1).

The shared ``bge`` embedder (``BAAI/bge-small-en-v1.5``) is app-scoped but loads
lazily — the first ``encode()`` materialises the model synchronously and blocks
the agent loop, truncating the persona's first reply and dropping the next
turn's audio. :func:`start_embedder_warmup` pays that cold load **once, off the
event loop**, at session build, so the first real ``encode()`` (turn 0's recall)
is already warm.

The single throwaway call is GIL- and tokenizer-safe; we never fan ``encode``
across threads (that would serialise on the GIL and can trip the fast
tokenizer's ``Already borrowed`` guard — research R-32-2). The call is wrapped so
a failed cold load completes cleanly rather than surfacing as an unretrieved
task exception: the turn-0 gate (D-32-X-warmup-gates-turn0) waits for
*completion* bounded by the ring degrade ladder, then proceeds regardless.
"""

from __future__ import annotations

import asyncio

from persona.logging import get_logger
from persona.stores import Embedder

__all__ = ["start_embedder_warmup"]

_logger = get_logger("agent.warmup")

# One short, representative string — enough to materialise the model + tokenizer
# and pre-tune the kernels the first real recall will hit.
_WARMUP_TEXT = "warm up the embedder"


async def _run_warmup(embedder: Embedder) -> None:
    """Run one throwaway ``encode`` on a worker thread; never raise."""
    try:
        await asyncio.to_thread(embedder.encode, [_WARMUP_TEXT])
    except Exception as exc:  # noqa: BLE001 — warm-up is best-effort; turn 0 degrades
        _logger.warning(
            "embedder warm-up failed; first turn will pay the cold load (model={model})",
            model=getattr(embedder, "model_name", "?"),
            error=str(exc),
        )


def start_embedder_warmup(embedder: Embedder) -> asyncio.Task[None]:
    """Kick a one-shot, off-loop embedder warm-up and return its task.

    The task is scheduled immediately and runs concurrently — the caller (the
    session build) is not blocked. The turn-0 generation path awaits the
    returned task (bounded by the ring degrade ladder) so the cold load is hidden
    inside "ringing" instead of truncating the first reply.

    Args:
        embedder: the shared embedder whose first ``encode()`` would otherwise
            load synchronously on the agent loop.

    Returns:
        The warm-up :class:`asyncio.Task`; awaiting it yields when the cold load
        has completed (or failed and been logged).
    """
    return asyncio.create_task(_run_warmup(embedder))
