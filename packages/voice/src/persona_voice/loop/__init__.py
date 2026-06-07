"""Streaming-loop skeleton with V2/V3/V4/V5 Protocol seams (spec V1 T07).

The loop wires :class:`VoiceRoom` (T05) to the four named seams downstream
voice specs plug into: V2 inbound STT, V3 outbound TTS, V4 lifecycle hooks,
V5 model entry. V1 ships the seam contracts + a pass-through echo default
so the full-duplex criterion (#3) can be verified end-to-end before any
intelligence is wired.
"""

from __future__ import annotations

from persona_voice.loop.streaming import (
    AudioChunk,
    ModelReplyProducer,
    PassThroughEchoMode,
    StreamingLoop,
    STTStream,
    Transcript,
    TTSStream,
)

__all__ = [
    "AudioChunk",
    "ModelReplyProducer",
    "PassThroughEchoMode",
    "STTStream",
    "StreamingLoop",
    "TTSStream",
    "Transcript",
]
