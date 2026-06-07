"""WebRTC transport facade for persona-voice (spec V1 T05).

Wraps the ``livekit.rtc.Room`` API in a thin :class:`VoiceRoom` facade so the
rest of persona-voice depends on a small, testable surface and not on the
LiveKit SDK details (D-V1-1 branch (A); R-V1-5 "adopt the seam shape, reject
the SDK coupling").
"""

from __future__ import annotations

from persona_voice.transport.room import (
    AUDIO_INBOUND_CHANNELS,
    AUDIO_INBOUND_SAMPLE_RATE,
    AUDIO_OUTBOUND_CHANNELS,
    AUDIO_OUTBOUND_SAMPLE_RATE,
    InboundAudioFrame,
    InboundAudioHandler,
    RoomSubstrate,
    VoiceRoom,
)

__all__ = [
    "AUDIO_INBOUND_CHANNELS",
    "AUDIO_INBOUND_SAMPLE_RATE",
    "AUDIO_OUTBOUND_CHANNELS",
    "AUDIO_OUTBOUND_SAMPLE_RATE",
    "InboundAudioFrame",
    "InboundAudioHandler",
    "RoomSubstrate",
    "VoiceRoom",
]
