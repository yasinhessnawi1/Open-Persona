"""Scripted :class:`StreamingTTS` mock for V3 tests (Spec 02 D-02-12 pattern).

Lives in ``tests/`` (NOT ``src/``) — the mock is a test double, never a
production provider. Integration tests (T13) wire it behind the V1
``TTSStream`` seam to exercise the loop without a live Cartesia
connection; the real provider is exercised only by the T14 external smoke.

It yields a scripted sequence of :class:`AudioChunk` records for any text
stream, supports the ``cancel`` / ``close`` lifecycle, and records what it
was fed so tests can assert on it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from persona_voice.loop.streaming import AudioChunk
from persona_voice.tts.types import ResolvedVoice


class MockStreamingTTS:
    """A scripted, deterministic StreamingTTS for tests.

    Args:
        chunks: The :class:`AudioChunk` records to emit, in order, one per
            text item consumed from the stream (extra chunks emit after
            the stream ends; fewer chunks than text items is fine).
        consumes_raw_text: Capability flag to report.
    """

    def __init__(
        self,
        chunks: list[AudioChunk] | None = None,
        *,
        consumes_raw_text: bool = False,
    ) -> None:
        self._chunks = chunks or [
            AudioChunk(
                data=b"\x00\x01" * 480,
                sample_rate=24000,
                num_channels=1,
                samples_per_channel=480,
            )
        ]
        self._consumes_raw_text = consumes_raw_text
        self.received_text: list[str] = []
        self.received_voice: ResolvedVoice | None = None
        self.cancelled = False
        self.closed = False

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return "mock-tts"

    @property
    def consumes_raw_text(self) -> bool:
        return self._consumes_raw_text

    async def _iter(
        self, text_stream: AsyncIterator[str], voice: ResolvedVoice
    ) -> AsyncIterator[AudioChunk]:
        self.received_voice = voice
        idx = 0
        async for text in text_stream:
            if self.cancelled:
                return
            self.received_text.append(text)
            if idx < len(self._chunks):
                yield self._chunks[idx]
                idx += 1
        # Emit any remaining scripted chunks after the stream completes.
        while not self.cancelled and idx < len(self._chunks):
            yield self._chunks[idx]
            idx += 1

    def synthesize(
        self, text_stream: AsyncIterator[str], voice: ResolvedVoice
    ) -> AsyncIterator[AudioChunk]:
        return self._iter(text_stream, voice)

    async def cancel(self) -> None:
        self.cancelled = True

    async def close(self) -> None:
        self.closed = True
