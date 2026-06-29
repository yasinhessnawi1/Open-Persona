"""The async-artifact production lane (V10 T3, V10-D-X-async-lane / V10-D-2/3/4/5).

A fast voice tool (web search / diagram) runs inline under the latency bound. A
slow *visual* tool (``generate_image``, 5–20s) cannot block the live turn without
the dead air V10 forbids, so it is produced on this lane — **off** the turn path:

* :meth:`AsyncArtifactLane.submit` spawns a **session-scoped** task (outside the
  barge-in cancellation boundary — V10-D-5), so a barge-in on the persona's
  acknowledgement turn never cancels the production.
* the task dispatches through P2's :func:`dispatch_with_activity` seam, so the
  call's ``activity_start``/``activity_end`` and its ``tool_result``+``artifacts``
  frame emit over ``on_event`` exactly as chat emits them — **this is the RENDER**,
  fired the instant the artifact completes (V10-D-3, render-when-ready).
* on success the task hands a narration prompt to ``on_ready`` (the orchestrator's
  floor-gated ``notify_artifact_ready``) — **this is the NARRATION**, decoupled
  from the render and spoken only at the next idle floor.

In-flight production is bounded (:data:`DEFAULT_ASYNC_ARTIFACT_CONCURRENCY`,
V10-D-4) and every task is cancelled at call teardown (:meth:`shutdown`).
Production runs on the asyncio loop but its CPU steps live in the tools
themselves (which offload via ``asyncio.to_thread``) — the lane never blocks the
loop servicing LiveKit/STT/TTS (the event-loop-starvation lesson, V10-R-2).
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from persona.logging import get_logger
from persona_runtime.activity import dispatch_with_activity
from persona_runtime.agentic.events import RunEvent

from persona_voice.loop.streaming import Transcript

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from persona.schema.tools import ToolCall, ToolResult
    from persona.tools import Toolbox

__all__ = ["DEFAULT_ASYNC_ARTIFACT_CONCURRENCY", "AsyncArtifactLane"]

_logger = get_logger("model.async_lane")

#: Default cap on concurrent async-artifact production per call (V10-D-4).
#: Bounds task spawn + event-loop pressure; ``generate_image`` also serialises
#: server-side at the imagegen concurrency lock. Env-overridable.
DEFAULT_ASYNC_ARTIFACT_CONCURRENCY = 3

_CONCURRENCY_ENV = "PERSONA_VOICE_ASYNC_ARTIFACT_CONCURRENCY"


def _default_concurrency() -> int:
    """Read the per-call cap from the env, falling back to the default."""
    raw = os.environ.get(_CONCURRENCY_ENV, "").strip()
    if not raw:
        return DEFAULT_ASYNC_ARTIFACT_CONCURRENCY
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_ASYNC_ARTIFACT_CONCURRENCY
    return value if value > 0 else DEFAULT_ASYNC_ARTIFACT_CONCURRENCY


def _default_narration_author(call: ToolCall, _result: ToolResult) -> Transcript:
    """Build the narration prompt the persona turns into a spoken "on screen" line.

    A nudge (like the greet-first turn-0 prompt), NOT the literal spoken text —
    the model authors the natural sentence. The orchestrator coalesces several of
    these into one utterance when more than one artifact is ready at drain time.
    """
    return Transcript(
        is_final=True,
        text=(
            f"You have just finished preparing something for the user using "
            f"{call.name}, and it is now visible on their screen. In one short, "
            f"natural sentence, let them know it is ready and on screen."
        ),
        confidence=1.0,
    )


class AsyncArtifactLane:
    """Produces slow visual artifacts off the live turn path (V10-D-X-async-lane).

    Args:
        toolbox: The persona's toolbox (the dispatch target, wrapped by the P2
            seam per call).
        on_ready: The floor-gated narration sink — the orchestrator's
            ``notify_artifact_ready``. Called once per successfully produced
            artifact with the narration prompt.
        on_event: The activity-event sink (the data-channel transport in prod;
            ``None`` skips instrumentation — the render frame then has no
            transport, but production + narration still work).
        concurrency: Max concurrent productions (default: env / 3).
        narration_author: Builds the narration prompt from the call + result
            (default: a tool-named "it's on screen" nudge).
    """

    def __init__(
        self,
        *,
        toolbox: Toolbox,
        on_ready: Callable[[Transcript], Awaitable[None]],
        on_event: Callable[[RunEvent], Awaitable[None]] | None = None,
        concurrency: int | None = None,
        narration_author: Callable[[ToolCall, ToolResult], Transcript] | None = None,
    ) -> None:
        self._toolbox = toolbox
        self._on_ready = on_ready
        self._on_event = on_event
        self._narration_author = narration_author or _default_narration_author
        self._sem = asyncio.Semaphore(
            concurrency if concurrency is not None else _default_concurrency()
        )
        self._tasks: set[asyncio.Task[None]] = set()

    def submit(self, call: ToolCall) -> None:
        """Spawn a session-scoped production task for ``call`` (fire-and-forget).

        Returns immediately so the live turn never blocks. Excess submissions
        beyond the concurrency cap wait at the semaphore (FIFO) before
        dispatching — they are spawned but not yet producing.
        """
        task = asyncio.create_task(self._run(call), name=f"async-artifact:{call.name}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, call: ToolCall) -> None:
        """Produce one artifact under the cap, then narrate it (fail-soft)."""
        try:
            async with self._sem:
                result = await dispatch_with_activity(
                    self._toolbox, call, on_event=self._on_event, step=-1
                )
        except asyncio.CancelledError:
            raise  # teardown / shutdown — propagate cleanly
        except Exception:  # noqa: BLE001 — a failed production must never crash the lane
            _logger.warning("async-artifact production failed name={name}", name=call.name)
            return
        if result.is_error:
            # The activity_end carried the error status (the visible signal); the
            # persona does not narrate a failure in v1 (V10-D-5 fail-soft).
            _logger.info("async-artifact tool returned an error name={name}", name=call.name)
            return
        # The RENDER (V10-D-3): emit the artifact-bearing tool_result frame the
        # instant production completes, so the FileRendererPanel renders it. The
        # activity_* badge fired during dispatch; per P2-D-3 keep-both, the
        # artifact payload rides tool_result (same vocabulary chat uses).
        if self._on_event is not None:
            await self._on_event(
                RunEvent.tool_result(-1, call.name, result, kind=self._toolbox.kind_for(call.name))
            )
        narration = self._narration_author(call, result)
        try:
            await self._on_ready(narration)
        except Exception:  # noqa: BLE001 — the narration sink must never break the lane
            _logger.warning("async-artifact narration sink failed name={name}", name=call.name)

    async def join(self) -> None:
        """Await all in-flight production (drain helper; used at quiescence/tests)."""
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        """Cancel every in-flight production — the call-teardown hook (V10-D-4/5)."""
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
