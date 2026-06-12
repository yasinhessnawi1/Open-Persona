"""Unit tests for the V3 audio adaptation re-framer (T06, R-V3-4).

Covers: rail-format fail-fast, fixed-frame re-framing, progressive
first-frame ramp, odd-byte carry-over across pushes, sub-frame flush +
odd-byte drop, and clear()/barge-in reset. Deterministic — no timing
(D-V3-X-no-pacing-t06).
"""

from __future__ import annotations

import pytest
from persona_voice.tts.audio import (
    OUTBOUND_CHANNELS,
    OUTBOUND_SAMPLE_RATE,
    PCM16Reframer,
    assert_rail_format,
)
from persona_voice.tts.errors import TTSAudioFormatError

# 50 ms steady-state frame at 24 kHz mono PCM16 = 1200 samples = 2400 bytes.
_STEADY_SAMPLES = OUTBOUND_SAMPLE_RATE * 50 // 1000
_STEADY_BYTES = _STEADY_SAMPLES * 2
_FIRST_SAMPLES = OUTBOUND_SAMPLE_RATE * 20 // 1000  # 480 (progressive first)
_FIRST_BYTES = _FIRST_SAMPLES * 2


# ---------- assert_rail_format ---------------------------------------------


def test_assert_rail_format_accepts_rail() -> None:
    assert_rail_format(OUTBOUND_SAMPLE_RATE, OUTBOUND_CHANNELS)  # no raise


@pytest.mark.parametrize(
    ("rate", "channels"),
    [(16000, 1), (44100, 1), (24000, 2), (48000, 2)],
)
def test_assert_rail_format_rejects_non_rail(rate: int, channels: int) -> None:
    with pytest.raises(TTSAudioFormatError) as exc:
        assert_rail_format(rate, channels)
    assert exc.value.context["sample_rate"] == str(rate)


# ---------- re-framing -----------------------------------------------------


def test_non_progressive_emits_fixed_frames() -> None:
    r = PCM16Reframer(progressive=False)
    out = r.push(b"\x01\x02" * _STEADY_SAMPLES * 3)  # exactly 3 frames
    assert len(out) == 3
    assert all(c.samples_per_channel == _STEADY_SAMPLES for c in out)
    assert all(len(c.data) == _STEADY_BYTES for c in out)
    assert all(c.sample_rate == OUTBOUND_SAMPLE_RATE for c in out)


def test_progressive_first_frame_is_shorter_then_ramps() -> None:
    r = PCM16Reframer(progressive=True)
    # Push a big block so several frames pop at once.
    out = r.push(b"\x00\x01" * (_STEADY_SAMPLES * 4))
    sizes = [c.samples_per_channel for c in out]
    # 20 ms first, doubling 40 → 50, then steady at 50.
    assert sizes[0] == _FIRST_SAMPLES
    assert sizes[1] == _FIRST_SAMPLES * 2
    assert sizes[2] == _STEADY_SAMPLES
    assert sizes[3] == _STEADY_SAMPLES


def test_partial_frame_is_buffered_not_emitted() -> None:
    r = PCM16Reframer(progressive=False)
    assert r.push(b"\x00\x00" * (_STEADY_SAMPLES - 1)) == []  # one sample short


# ---------- odd-byte carry -------------------------------------------------


def test_odd_byte_carries_across_pushes() -> None:
    r = PCM16Reframer(progressive=False)
    # First push: one full frame + 1 odd byte. Only the whole frame emits;
    # the odd byte is carried (never splits a sample).
    out = r.push(b"\xaa\xbb" * _STEADY_SAMPLES + b"\xcc")
    assert len(out) == 1
    assert len(out[0].data) == _STEADY_BYTES
    # Second push supplies the byte that completes the carried sample; with
    # enough bytes another whole frame emerges.
    out2 = r.push(b"\xdd" + b"\x11\x22" * (_STEADY_SAMPLES - 1))
    assert len(out2) == 1
    assert len(out2[0].data) == _STEADY_BYTES
    # The carried+completed sample is the first sample of the new frame.
    assert out2[0].data[:2] == b"\xcc\xdd"


# ---------- flush ----------------------------------------------------------


def test_flush_emits_subframe_remainder() -> None:
    r = PCM16Reframer(progressive=False)
    r.push(b"\x01\x02" * 100)  # 100 samples < one frame
    chunk = r.flush()
    assert chunk is not None
    assert chunk.samples_per_channel == 100
    assert len(chunk.data) == 200


def test_flush_drops_single_odd_byte() -> None:
    r = PCM16Reframer(progressive=False)
    r.push(b"\x01\x02\x03")  # 3 bytes = 1 sample + 1 odd byte
    chunk = r.flush()
    assert chunk is not None
    assert chunk.samples_per_channel == 1
    assert len(chunk.data) == 2  # odd byte dropped


def test_flush_returns_none_when_empty() -> None:
    r = PCM16Reframer(progressive=False)
    assert r.flush() is None


def test_flush_returns_none_for_single_odd_byte_only() -> None:
    r = PCM16Reframer(progressive=False)
    r.push(b"\x07")
    assert r.flush() is None


# ---------- clear / barge-in ------------------------------------------------


def test_clear_resets_buffer_and_progressive_ramp() -> None:
    r = PCM16Reframer(progressive=True)
    r.push(b"\x00\x01" * (_STEADY_SAMPLES * 3))  # advances the ramp
    r.push(b"\x00")  # leave a stray byte buffered
    r.clear()
    # Buffer cleared: a fresh full block starts the ramp over at 20 ms.
    out = r.push(b"\x02\x03" * (_STEADY_SAMPLES * 2))
    assert out[0].samples_per_channel == _FIRST_SAMPLES
    # And the stray pre-clear byte did not leak into the first frame.
    assert out[0].data[:2] == b"\x02\x03"
