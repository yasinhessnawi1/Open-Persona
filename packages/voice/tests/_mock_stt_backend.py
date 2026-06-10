"""Test-only :class:`StreamingSTT` implementation — scripted fixture.

Lives in ``tests/`` (not ``src/``) per Spec 02 D-02-12: production code
does not ship a ``mock`` STT provider. T11's integration spine + T05's
seam adapter tests + downstream V4 work inject this backend explicitly
with pre-recorded :class:`Transcript` + :class:`SpeechActivityEvent`
scripts to drive the loop without touching a real provider.

The shape mirrors :class:`persona_voice.stt.deepgram_backend.DeepgramStreamingSTT`
exactly:

* :meth:`push_audio` records inbound frames so tests can assert on
  what the loop forwarded (e.g. byte counts, sample-rate negotiation).
* :meth:`transcripts` yields the scripted :class:`Transcript` records in
  order; iterator terminates after the script is exhausted OR
  :meth:`close` is called (idempotency contract).
* :meth:`speech_activity_events` yields the scripted activity events
  with ``source="provider"`` — same shape the real Deepgram backend
  uses for ``SpeechStarted``/``UtteranceEnd``.
* :meth:`close` is idempotent and drains the iterators cleanly.
* :attr:`provider_name` returns ``"mock"`` + :attr:`model_name` returns
  ``"scripted"`` so observability stays unambiguous.
"""

# ruff: noqa: ANN401, ARG002

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from persona_voice.loop.streaming import Transcript
    from persona_voice.stt.types import SpeechEndedEvent, SpeechStartedEvent


__all__ = ["MockStreamingSTT"]


class MockStreamingSTT:
    """Scripted :class:`StreamingSTT` for V2 unit + integration tests.

    Replays the supplied :class:`Transcript` and
    :class:`SpeechActivityEvent` lists in order on the
    :meth:`transcripts` / :meth:`speech_activity_events` iterators.

    Attributes:
        pushed: List of ``(pcm, sample_rate)`` tuples recorded from
            :meth:`push_audio` — tests assert on this to verify the
            loop forwarded audio correctly.
        closed: ``True`` once :meth:`close` has run; second calls are
            no-ops (idempotency contract per the
            :class:`StreamingSTT` Protocol docstring).
    """

    def __init__(
        self,
        scripted_transcripts: list[Transcript],
        scripted_activity_events: list[SpeechStartedEvent | SpeechEndedEvent] | None = None,
    ) -> None:
        """Initialise the scripted backend.

        Args:
            scripted_transcripts: Records the :meth:`transcripts`
                iterator yields in order before terminating.
            scripted_activity_events: Records the
                :meth:`speech_activity_events` iterator yields in order.
                ``None`` (default) means the activity iterator yields
                nothing — useful when only the transcript path is under
                test.
        """
        self._transcript_queue: asyncio.Queue[Transcript | None] = asyncio.Queue()
        self._activity_queue: asyncio.Queue[SpeechStartedEvent | SpeechEndedEvent | None] = (
            asyncio.Queue()
        )
        for transcript in scripted_transcripts:
            self._transcript_queue.put_nowait(transcript)
        self._transcript_queue.put_nowait(None)
        for event in scripted_activity_events or []:
            self._activity_queue.put_nowait(event)
        self._activity_queue.put_nowait(None)
        self.pushed: list[tuple[bytes, int]] = []
        self.closed: bool = False

    @property
    def provider_name(self) -> str:
        """Always ``"mock"`` — observability discriminator for tests."""
        return "mock"

    @property
    def model_name(self) -> str:
        """Always ``"scripted"`` — pairs with :attr:`provider_name`."""
        return "scripted"

    async def push_audio(self, pcm: bytes, sample_rate: int) -> None:
        """Record an inbound frame. Tests assert on :attr:`pushed`."""
        if self.closed:
            return
        self.pushed.append((pcm, sample_rate))

    def transcripts(self) -> AsyncIterator[Transcript]:
        """Yield the scripted :class:`Transcript` records in order."""
        return self._iter_transcripts()

    async def _iter_transcripts(self) -> AsyncIterator[Transcript]:
        while True:
            item = await self._transcript_queue.get()
            if item is None:
                return
            yield item

    def speech_activity_events(
        self,
    ) -> AsyncIterator[SpeechStartedEvent | SpeechEndedEvent]:
        """Yield the scripted :class:`SpeechActivityEvent` records."""
        return self._iter_activity()

    async def _iter_activity(
        self,
    ) -> AsyncIterator[SpeechStartedEvent | SpeechEndedEvent]:
        while True:
            item = await self._activity_queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        """Idempotent close — drains the iterators cleanly."""
        if self.closed:
            return
        self.closed = True
        await self._transcript_queue.put(None)
        await self._activity_queue.put(None)
