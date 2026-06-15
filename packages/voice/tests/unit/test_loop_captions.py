"""Unit tests for the V6 A1 caption-listener port on StreamingLoop (spec V6).

Asserts the loop forwards (a) each V2 user transcript and (b) the V5 persona
reply text (streamed partials + a final) to the caption listener — the seam the
:class:`DataChannelBroadcaster` consumes. The port is additive (default None),
so these are the only tests that exercise it; every existing loop test stays
green because the listener is unwired there.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from persona_voice.loop.streaming import AudioChunk, StreamingLoop, Transcript

pytestmark = [pytest.mark.asyncio]


class _CaptionSpy:
    def __init__(self) -> None:
        self.user: list[tuple[str, bool]] = []
        self.persona: list[tuple[str, bool]] = []

    async def on_user_transcript(self, transcript: Transcript) -> None:
        self.user.append((transcript.text, transcript.is_final))

    async def on_persona_text(self, text: str, *, is_final: bool) -> None:
        self.persona.append((text, is_final))


class _FakeRoomAudio:
    def set_inbound_handler(self, _handler: object) -> None:
        return None

    async def publish_outbound(self) -> None:
        return None

    async def capture_outbound_frame(self, _frame: object) -> None:
        return None

    def clear_outbound(self) -> None:
        return None


class _FakeSession:
    async def notify(self, _event: object) -> None:
        return None


class _FakeTTS:
    """Consumes the token stream; emits one 24 kHz chunk per token."""

    def synthesize(self, text_stream: AsyncIterator[str]) -> AsyncIterator[AudioChunk]:
        return self._run(text_stream)

    async def _run(self, text_stream: AsyncIterator[str]) -> AsyncIterator[AudioChunk]:
        async for _token in text_stream:
            yield AudioChunk(
                data=b"\x00\x00", sample_rate=24_000, num_channels=1, samples_per_channel=1
            )

    async def cancel(self) -> None:
        return None


async def _model(_final_transcript: Transcript) -> AsyncIterator[str]:
    async def _gen() -> AsyncIterator[str]:
        for token in ("Tenants", " have", " rights."):
            yield token

    return _gen()


def _loop(spy: _CaptionSpy) -> StreamingLoop:
    return StreamingLoop(
        voice_room=_FakeRoomAudio(),  # type: ignore[arg-type]
        session=_FakeSession(),  # type: ignore[arg-type]
        tts=_FakeTTS(),
        model=_model,
        caption_listener=spy,
    )


async def test_persona_text_streams_partials_then_a_final() -> None:
    spy = _CaptionSpy()
    loop = _loop(spy)

    await loop.invoke_model_for_turn(Transcript(is_final=True, text="hi", confidence=1.0))

    # One running partial per token (mutate-and-replace), then the final.
    assert spy.persona == [
        ("Tenants", False),
        ("Tenants have", False),
        ("Tenants have rights.", False),
        ("Tenants have rights.", True),
    ]


async def test_user_transcripts_are_forwarded_from_the_orchestrated_pipeline() -> None:
    spy = _CaptionSpy()
    loop = _loop(spy)

    class _FakeStt:
        def transcripts(self) -> AsyncIterator[Transcript]:
            async def _gen() -> AsyncIterator[Transcript]:
                yield Transcript(is_final=False, text="hel", confidence=0.5)
                yield Transcript(is_final=True, text="hello", confidence=0.9)

            return _gen()

    class _FakeOrch:
        def __init__(self) -> None:
            self.seen: list[str] = []

        async def on_transcript(self, transcript: Transcript) -> None:
            self.seen.append(transcript.text)

    orch = _FakeOrch()
    await loop._run_orchestrated_pipeline(_FakeStt(), orch)  # type: ignore[arg-type]  # noqa: SLF001

    # The orchestrator still sees every transcript (unchanged), AND the caption
    # listener now receives each one too.
    assert orch.seen == ["hel", "hello"]
    assert spy.user == [("hel", False), ("hello", True)]
