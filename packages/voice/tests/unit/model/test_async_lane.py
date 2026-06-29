"""Unit tests for the V10 async-artifact production lane (T3, V10-D-X-async-lane).

A slow visual tool (``generate_image``, 5–20s) is produced OFF the turn path so
the audio stays live: the lane spawns a session-scoped task that dispatches the
call through P2's ``dispatch_with_activity`` seam (so the render frame +
activity events emit over ``on_event``), and on completion hands a narration to
``on_ready`` (the orchestrator's floor-gated ``notify_artifact_ready``). The lane
bounds in-flight production (V10-D-4) and cancels everything at call teardown.
"""

from __future__ import annotations

# ruff: noqa: ARG001 — test-double tools with intentionally unused signatures.
import asyncio

import pytest
from persona.schema.tools import ToolCall, ToolResult
from persona.tools import Toolbox
from persona.tools.protocol import tool
from persona_runtime.agentic.events import RunEvent
from persona_voice.loop.streaming import Transcript
from persona_voice.model.async_lane import AsyncArtifactLane

pytestmark = pytest.mark.asyncio


@tool(name="generate_image", description="Generate an image.")
async def _generate_image(prompt: str) -> ToolResult:
    return ToolResult(tool_name="generate_image", content=f"image of {prompt}")


def _call() -> ToolCall:
    return ToolCall(name="generate_image", args={"prompt": "a castle"}, call_id="c1")


async def test_submit_dispatches_through_seam_and_narrates_on_ready() -> None:
    events: list[RunEvent] = []
    narrations: list[Transcript] = []

    async def _on_event(ev: RunEvent) -> None:
        events.append(ev)

    async def _on_ready(narration: Transcript) -> None:
        narrations.append(narration)

    lane = AsyncArtifactLane(
        toolbox=Toolbox([_generate_image], allow_list=None),  # type: ignore[list-item]
        on_ready=_on_ready,
        on_event=_on_event,
    )
    lane.submit(_call())
    await lane.join()

    # Routed through P2's seam → paired activity events (the "creating…" badge).
    types = [e.type for e in events]
    assert "activity_start" in types
    assert "activity_end" in types
    assert next(e for e in events if e.type == "activity_start").data["kind"] == "imagegen"
    # The RENDER: an artifact-bearing tool_result frame emitted when ready, so the
    # FileRendererPanel can render it (the same event chat carries the artifact on).
    assert "tool_result" in types
    assert next(e for e in events if e.type == "tool_result").data["tool_name"] == "generate_image"
    # Produced → exactly one narration handed to the floor-gated sink.
    assert len(narrations) == 1
    assert narrations[0].is_final


async def test_failed_production_emits_no_narration() -> None:
    @tool(name="generate_image", description="A failing generator.")
    async def _boom(prompt: str) -> ToolResult:
        raise RuntimeError("provider down")

    narrations: list[Transcript] = []

    async def _on_ready(narration: Transcript) -> None:
        narrations.append(narration)

    lane = AsyncArtifactLane(
        toolbox=Toolbox([_boom], allow_list=None),  # type: ignore[list-item]
        on_ready=_on_ready,
    )
    lane.submit(_call())
    await lane.join()

    # Fail-soft: a failed production narrates nothing (the activity_end error
    # frame is the signal); it never strands or crashes the lane.
    assert narrations == []


async def test_concurrency_cap_bounds_inflight_production() -> None:
    gate = asyncio.Event()
    inflight = 0
    max_inflight = 0

    @tool(name="generate_image", description="A blocking generator.")
    async def _blocking(prompt: str) -> ToolResult:
        nonlocal inflight, max_inflight
        inflight += 1
        max_inflight = max(max_inflight, inflight)
        await gate.wait()
        inflight -= 1
        return ToolResult(tool_name="generate_image", content="done")

    async def _on_ready(narration: Transcript) -> None:
        return None

    lane = AsyncArtifactLane(
        toolbox=Toolbox([_blocking], allow_list=None),  # type: ignore[list-item]
        on_ready=_on_ready,
        concurrency=2,
    )
    for _ in range(4):
        lane.submit(_call())
    # Let the tasks reach the dispatch point; the cap must hold at 2.
    await asyncio.sleep(0.05)
    assert max_inflight == 2  # never more than the cap dispatching at once

    gate.set()
    await lane.join()
    assert max_inflight == 2


async def test_shutdown_cancels_inflight_and_skips_narration() -> None:
    gate = asyncio.Event()  # never set — the production hangs until cancelled

    @tool(name="generate_image", description="A hanging generator.")
    async def _hang(prompt: str) -> ToolResult:
        await gate.wait()
        return ToolResult(tool_name="generate_image", content="never")

    narrations: list[Transcript] = []

    async def _on_ready(narration: Transcript) -> None:
        narrations.append(narration)

    lane = AsyncArtifactLane(
        toolbox=Toolbox([_hang], allow_list=None),  # type: ignore[list-item]
        on_ready=_on_ready,
    )
    lane.submit(_call())
    await asyncio.sleep(0.01)  # let it reach the hang

    await lane.shutdown()  # call teardown cancels in-flight production

    assert narrations == []  # cancelled before completion → no narration
