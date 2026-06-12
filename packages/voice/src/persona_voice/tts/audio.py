"""Audio adaptation — provider PCM output → V1 outbound AudioChunks (T06, R-V3-4).

All six evaluated TTS providers emit headerless PCM16LE mono at exactly
24000 Hz natively in streaming mode (research.md §R-V3-4) — the launch
Cartesia backend requests ``{raw, pcm_s16le, 24000}``. So T06 is
**configuration + re-framing, not transcoding**: the only mandatory work
is turning the provider's variable-length byte bursts into the steady
fixed-size :class:`persona_voice.loop.streaming.AudioChunk` frames V1's
outbound rail wants (PCM16 mono 24 kHz, D-V1-6).

**No pacing (D-V3-X-no-pacing-t06).** This module has no clock, no sleeps,
no jitter buffer — it is purely ``bytes in → frames out``. ``rtc.AudioSource``
on the V1 transport side runs a native 10 ms repeating task that re-slices
whatever is pushed and blocks ``capture_frame`` at ~``queue_size_ms`` of
buffered audio; that await IS the pacer/backpressure valve (verified
against LiveKit Agents + Pipecat source). A clock here would double-pace
and cause audible jitter. The re-framer is therefore fully deterministic
— unit tests need no timing.

**Resampling is out of scope** (research-confirmed default). No launch /
alternative provider needs it (all native 24 kHz). A future non-24 kHz
backend (e.g. a local TTS at 22.05/44.1 kHz) adds a lazily-engaged
``livekit.rtc.AudioResampler`` contingency stage in front of the
re-framer; until then :func:`assert_rail_format` fails fast on any
non-rail input rather than silently guessing.

**Sample alignment.** PCM16 is 2 bytes/sample; provider chunk boundaries
are NOT guaranteed even-byte (HTTP-chunked transports can split anywhere).
The re-framer only ever emits whole frames (even byte counts), carrying an
odd trailing byte forward to the next push, so every emitted
:class:`AudioChunk` contains whole int16 samples.
"""

from __future__ import annotations

from persona.logging import get_logger

from persona_voice.loop.streaming import AudioChunk
from persona_voice.tts.errors import TTSAudioFormatError

__all__ = [
    "OUTBOUND_CHANNELS",
    "OUTBOUND_SAMPLE_RATE",
    "PCM16Reframer",
    "assert_rail_format",
]

OUTBOUND_SAMPLE_RATE = 24000
"""V1 outbound rail sample rate (D-V1-6). PCM16 mono."""

OUTBOUND_CHANNELS = 1
_BYTES_PER_SAMPLE = 2

_log = get_logger("voice.tts.audio")


def assert_rail_format(sample_rate: int, num_channels: int) -> None:
    """Fail fast unless ``(sample_rate, num_channels)`` is the V1 rail.

    Called by the backend on the provider's declared output format. Per
    R-V3-4 the launch providers are pinned to the rail at construction, so
    a mismatch is a config bug surfaced immediately — never patched over
    at runtime (no silent resample/downmix).

    Raises:
        TTSAudioFormatError: rate is not 24000 Hz or channels is not mono.
    """
    if sample_rate != OUTBOUND_SAMPLE_RATE or num_channels != OUTBOUND_CHANNELS:
        raise TTSAudioFormatError(
            "provider audio is not the V1 outbound rail (PCM16 mono 24 kHz)",
            context={
                "sample_rate": str(sample_rate),
                "num_channels": str(num_channels),
                "expected_sample_rate": str(OUTBOUND_SAMPLE_RATE),
                "expected_channels": str(OUTBOUND_CHANNELS),
            },
        )


class PCM16Reframer:
    """Re-frame variable provider byte bursts into fixed AudioChunk frames.

    Deterministic and clockless (D-V3-X-no-pacing-t06). One instance per
    synthesis stream — :meth:`clear` resets it for barge-in so in-flight
    bytes from a cancelled utterance never leak into the next.

    Args:
        frame_ms: Steady-state frame size in milliseconds. Default 50 ms
            (1200 samples / 2400 bytes at 24 kHz mono) — LiveKit Agents'
            track-edge target.
        progressive: When ``True`` (default) the first frame is 20 ms and
            doubles (40 → 50 ms) toward ``frame_ms`` to minimise
            time-to-first-audio, then holds steady. The native transport
            layer re-slices to 10 ms regardless, so this is a TTFA /
            barge-in knob only (research.md §R-V3-4).
    """

    def __init__(self, *, frame_ms: int = 50, progressive: bool = True) -> None:
        self._target_samples = OUTBOUND_SAMPLE_RATE * frame_ms // 1000
        self._first_samples = (
            min(OUTBOUND_SAMPLE_RATE * 20 // 1000, self._target_samples)
            if progressive
            else self._target_samples
        )
        self._progressive = progressive
        self._buf = bytearray()
        self._next_samples = self._first_samples

    def push(self, data: bytes) -> list[AudioChunk]:
        """Accumulate ``data`` and return any whole frames now available."""
        self._buf += data
        out: list[AudioChunk] = []
        while True:
            frame_bytes = self._next_samples * OUTBOUND_CHANNELS * _BYTES_PER_SAMPLE
            if len(self._buf) < frame_bytes:
                break
            chunk = bytes(self._buf[:frame_bytes])
            del self._buf[:frame_bytes]
            out.append(self._make_chunk(chunk, self._next_samples))
            self._advance()
        return out

    def flush(self) -> AudioChunk | None:
        """Emit the sub-frame remainder at end-of-utterance, if any.

        Drops a single trailing odd byte (logged) so a frame is never
        emitted with a split sample. Returns ``None`` when nothing whole
        remains.
        """
        n = len(self._buf)
        whole = n - (n % 2)
        if n % 2:
            _log.warning(
                "dropping trailing odd byte at flush (provider emitted an odd total byte count)",
            )
        if whole == 0:
            self._buf.clear()
            return None
        chunk = bytes(self._buf[:whole])
        self._buf.clear()
        samples = whole // (OUTBOUND_CHANNELS * _BYTES_PER_SAMPLE)
        return self._make_chunk(chunk, samples)

    def clear(self) -> None:
        """Reset accumulator + progressive ramp (barge-in / new utterance)."""
        self._buf.clear()
        self._next_samples = self._first_samples

    def _advance(self) -> None:
        if self._progressive and self._next_samples < self._target_samples:
            self._next_samples = min(self._next_samples * 2, self._target_samples)

    @staticmethod
    def _make_chunk(data: bytes, samples: int) -> AudioChunk:
        return AudioChunk(
            data=data,
            sample_rate=OUTBOUND_SAMPLE_RATE,
            num_channels=OUTBOUND_CHANNELS,
            samples_per_channel=samples,
        )
