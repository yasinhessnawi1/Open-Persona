"""Unit tests for T07 barged-over memory honesty — record, bridge, watchdog."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock

import pytest
from persona_voice.loop.streaming import HeardReply
from persona_voice.turn_taking.bridge import HeardWordsBridge, LoopTurnActions
from persona_voice.turn_taking.heard_words import BargedReply, TurnTranscriptListener

# ---------- the record ------------------------------------------------------


def test_barged_reply_is_frozen_and_forbids_extra() -> None:
    from pydantic import ValidationError

    r = BargedReply(heard_text="hi", truncated=True, token_count=1)
    assert r.heard_text == "hi"
    assert r.truncated is True
    with pytest.raises(ValidationError):
        BargedReply(heard_text="x", truncated=False, token_count=0, extra="no")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        BargedReply(heard_text="x", truncated=False, token_count=-1)


def test_barged_reply_satisfies_v5_listener_protocol() -> None:
    class _V5:
        async def on_reply_committed(self, reply: BargedReply) -> None: ...

    assert isinstance(_V5(), TurnTranscriptListener)


# ---------- HeardWordsBridge mapping ----------------------------------------


@pytest.mark.asyncio
async def test_bridge_maps_heard_reply_to_barged_reply() -> None:
    committed: list[BargedReply] = []

    class _V5:
        async def on_reply_committed(self, reply: BargedReply) -> None:
            committed.append(reply)

    bridge = HeardWordsBridge(_V5())
    await bridge.on_reply_heard(HeardReply(text="Once upon", truncated=True, token_count=2))
    assert committed == [BargedReply(heard_text="Once upon", truncated=True, token_count=2)]


# ---------- the cancel watchdog (D-V4-X-watchdog-timeout) --------------------


@pytest.mark.asyncio
async def test_cancel_watchdog_returns_even_if_teardown_hangs() -> None:
    """A model task that ignores cancellation must not hang the cancel chain."""

    async def _hung() -> None:
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            # Simulate a stuck teardown that swallows cancellation.
            await asyncio.sleep(100)

    actions = LoopTurnActions(MagicMock(), cancel_watchdog_s=0.05)
    actions._task = asyncio.create_task(_hung())  # noqa: SLF001
    # Should return within ~watchdog, not block on the hung task.
    await asyncio.wait_for(actions.cancel_generation(), timeout=1.0)
    hung = actions._task  # noqa: SLF001
    # cancel_generation cleared the handle; the underlying task is detached.
    assert hung is None

    # Clean up the still-running detached task to avoid "Task was destroyed".
    for task in asyncio.all_tasks() - {asyncio.current_task()}:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_interrupt_calls_loop_teardown() -> None:
    loop = MagicMock()
    loop.flush_outbound_and_cancel_tts = AsyncMock(return_value=None)
    actions = LoopTurnActions(loop)
    await actions.interrupt()
    loop.flush_outbound_and_cancel_tts.assert_awaited_once()
