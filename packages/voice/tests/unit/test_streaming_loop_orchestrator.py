"""Unit tests for the V4 additive orchestrator port on StreamingLoop (spec V4 T05).

Verifies the D-V4-X-t05-orchestrator-default contract: ``invoke_model_for_turn``
runs the V5→V3 chain once, and wiring an ``orchestrator`` disables the V1
auto-invoke loop (the echo/dev baseline) so no ungated auto-loop runs in
production. V1's own ``test_streaming_loop.py`` proves the baseline path stays
green.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from persona_voice.loop.streaming import (
    AudioChunk,
    HeardReply,
    StreamingLoop,
    Transcript,
    TurnOrchestrator,
)
from persona_voice.session.state_machine import SessionStateMachine

_VOICE_TESTS_DIR = Path(__file__).resolve().parents[1]
if str(_VOICE_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_VOICE_TESTS_DIR))

from _mock_model import CancellableStubModel  # type: ignore[import-not-found]  # noqa: E402

pytestmark = pytest.mark.asyncio


def _voice_room_fake() -> Any:  # noqa: ANN401
    vr = MagicMock()
    vr.set_inbound_handler = MagicMock()
    vr.publish_outbound = AsyncMock(return_value=MagicMock())
    vr.capture_outbound_frame = AsyncMock(return_value=None)
    vr.clear_outbound = MagicMock(return_value=None)
    return vr


def _session() -> SessionStateMachine:
    return SessionStateMachine(
        session_id="s1",
        user_id="u1",
        persona_id="p1",
        conversation_id="c1",
        rls_engine=MagicMock(),
    )


class _NoopOrchestrator:
    """Satisfies the TurnOrchestrator Protocol structurally."""

    async def on_transcript(self, transcript: Transcript) -> None: ...
    async def notify_model_first_audio(self) -> None: ...
    async def notify_persona_finished(self) -> None: ...
    async def notify_processing_yielded_no_audio(self) -> None: ...


def _model_two_tokens() -> Any:  # noqa: ANN401
    async def _model(_t: Transcript) -> Any:  # noqa: ANN401
        async def _gen() -> Any:  # noqa: ANN401
            yield "Hi "
            yield "there"

        return _gen()

    return _model


class _TTS:
    def __init__(self) -> None:
        self.text: list[str] = []

    async def synthesize(self, text_stream: Any) -> Any:  # noqa: ANN401
        async for tok in text_stream:
            self.text.append(tok)
            yield AudioChunk(
                data=b"\x00\x00", sample_rate=24_000, num_channels=1, samples_per_channel=1
            )

    async def cancel(self) -> None: ...


# ---------- invoke_model_for_turn ------------------------------------------


async def test_invoke_model_for_turn_runs_v5_to_v3_chain_once() -> None:
    vr = _voice_room_fake()
    tts = _TTS()
    loop = StreamingLoop(voice_room=vr, session=_session(), model=_model_two_tokens(), tts=tts)
    await loop.invoke_model_for_turn(Transcript(is_final=True, text="hello", confidence=0.9))
    assert tts.text == ["Hi ", "there"]
    assert vr.capture_outbound_frame.await_count == 2


async def test_invoke_model_for_turn_is_noop_without_model_or_tts() -> None:
    vr = _voice_room_fake()
    loop = StreamingLoop(voice_room=vr, session=_session())
    # No model/tts wired → no-op, no outbound frames.
    await loop.invoke_model_for_turn(Transcript(is_final=True, text="x", confidence=0.9))
    vr.capture_outbound_frame.assert_not_called()


# ---------- orchestrator disables the auto-loop -----------------------------


async def test_orchestrator_satisfies_protocol() -> None:
    assert isinstance(_NoopOrchestrator(), TurnOrchestrator)


async def test_orchestrator_disables_auto_invoke_loop() -> None:
    """D-V4-X-t05-orchestrator-default — production path never AUTO-invokes.

    With an orchestrator wired, ``start_pipeline`` drains transcripts into V4
    (T06) but never invokes the model itself — V4 owns invocation. The model
    is therefore never auto-invoked from a transcript.
    """
    import asyncio

    vr = _voice_room_fake()

    class _STT:
        async def push_audio(self, pcm: bytes, sample_rate: int) -> None: ...

        async def transcripts(self) -> Any:  # noqa: ANN401
            yield Transcript(is_final=True, text="should not auto-invoke", confidence=0.9)

    tts = _TTS()
    loop = StreamingLoop(
        voice_room=vr,
        session=_session(),
        stt=_STT(),
        model=_model_two_tokens(),
        tts=tts,
        orchestrator=_NoopOrchestrator(),
    )
    await loop.start_pipeline()
    # A drain task is spawned (T06) — but it only feeds the orchestrator.
    assert loop._pipeline_task is not None  # noqa: SLF001
    await asyncio.wait_for(loop._pipeline_task, timeout=2.0)  # noqa: SLF001
    # The model was NEVER auto-invoked (the whole point of the gate).
    assert tts.text == []
    vr.capture_outbound_frame.assert_not_called()


async def test_orchestrated_pipeline_drains_transcripts_to_orchestrator() -> None:
    """T06 — in orchestrator mode the loop feeds transcripts to V4, never
    auto-invoking the model."""
    import asyncio

    vr = _voice_room_fake()

    received: list[Transcript] = []

    class _RecordingOrch:
        async def on_transcript(self, transcript: Transcript) -> None:
            received.append(transcript)

        async def notify_model_first_audio(self) -> None: ...
        async def notify_persona_finished(self) -> None: ...
        async def notify_processing_yielded_no_audio(self) -> None: ...

    t_partial = Transcript(is_final=False, text="hel", confidence=0.4)
    t_final = Transcript(is_final=True, text="hello", confidence=0.95)

    class _STT:
        async def push_audio(self, pcm: bytes, sample_rate: int) -> None: ...

        async def transcripts(self) -> Any:  # noqa: ANN401
            yield t_partial
            yield t_final

    tts = _TTS()
    loop = StreamingLoop(
        voice_room=vr,
        session=_session(),
        stt=_STT(),
        model=_model_two_tokens(),
        tts=tts,
        orchestrator=_RecordingOrch(),
    )
    await loop.start_pipeline()
    assert loop._pipeline_task is not None  # noqa: SLF001 — drain task spawned
    await asyncio.wait_for(loop._pipeline_task, timeout=2.0)  # noqa: SLF001
    # Both transcripts reached the orchestrator; the model was never auto-invoked.
    assert [t.text for t in received] == ["hel", "hello"]
    assert tts.text == []


async def test_stub_first_token_delay_is_budget_proxy() -> None:
    """The cancellable stub's first-token delay (operator-pass budget-proxy TTFT)
    holds the first token, then streams (D5 — V4 operator-pass charter §0.1)."""
    import asyncio

    model = CancellableStubModel(["a ", "b"], hold_after_first=False, first_token_delay_s=0.02)
    gen = await model(Transcript(is_final=True, text="x", confidence=0.9))
    collected = [tok async for tok in gen]
    assert collected == ["a ", "b"]
    # The delay must actually elapse before the first token (proxy TTFT).
    model2 = CancellableStubModel(["a"], hold_after_first=False, first_token_delay_s=0.05)
    gen2 = await model2(Transcript(is_final=True, text="x", confidence=0.9))
    loop = asyncio.get_running_loop()
    start = loop.time()
    _ = [tok async for tok in gen2]
    assert loop.time() - start >= 0.04


class _RecordingReplyListener:
    def __init__(self) -> None:
        self.heard: list[HeardReply] = []

    async def on_reply_heard(self, reply: HeardReply) -> None:
        self.heard.append(reply)


async def test_heard_reply_emitted_on_clean_completion() -> None:
    """T07 — a finished reply records the full spoken text, not truncated."""
    vr = _voice_room_fake()
    tts = _TTS()
    listener = _RecordingReplyListener()
    loop = StreamingLoop(
        voice_room=vr,
        session=_session(),
        model=_model_two_tokens(),
        tts=tts,
        turn_transcript_listener=listener,
    )
    await loop.invoke_model_for_turn(Transcript(is_final=True, text="hi", confidence=0.9))
    assert listener.heard == [HeardReply(text="Hi there", truncated=False, token_count=2)]


async def test_heard_reply_truncated_on_barge_in_cancel() -> None:
    """T07 — a cancelled reply records only the spoken-so-far prefix (D-V4-4)."""
    import asyncio

    vr = _voice_room_fake()
    tts = _TTS()
    listener = _RecordingReplyListener()
    model = CancellableStubModel(["Once ", "upon ", "a ", "time"], hold_after_first=True)
    loop = StreamingLoop(
        voice_room=vr,
        session=_session(),
        model=model,
        tts=tts,
        turn_transcript_listener=listener,
    )
    task = asyncio.create_task(
        loop.invoke_model_for_turn(Transcript(is_final=True, text="story", confidence=0.9))
    )
    await model.first_token.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Only the first token was spoken; the remainder was never synthesised.
    assert listener.heard == [HeardReply(text="Once ", truncated=True, token_count=1)]
    assert tts.text == ["Once "]


async def test_auto_loop_still_runs_without_orchestrator() -> None:
    """V1 baseline preserved — no orchestrator → the auto-loop invokes V5."""
    import asyncio

    vr = _voice_room_fake()

    class _STT:
        async def push_audio(self, pcm: bytes, sample_rate: int) -> None: ...

        async def transcripts(self) -> Any:  # noqa: ANN401
            yield Transcript(is_final=True, text="auto invoke me", confidence=0.9)

    tts = _TTS()
    loop = StreamingLoop(
        voice_room=vr, session=_session(), stt=_STT(), model=_model_two_tokens(), tts=tts
    )
    await loop.start_pipeline()
    assert loop._pipeline_task is not None  # noqa: SLF001
    await asyncio.wait_for(loop._pipeline_task, timeout=2.0)  # noqa: SLF001
    assert tts.text == ["Hi ", "there"]
