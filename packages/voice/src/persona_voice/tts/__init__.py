"""Spec V3 streaming-TTS package surface.

Public types V3 callers depend on:

* :class:`StreamingTTS` Protocol — the provider-independent streaming-TTS
  seam (mirrors Spec 02 ``ChatBackend`` / V2 ``StreamingSTT``). Callers
  depend on the Protocol, never on a provider SDK; the Cartesia launch
  backend (D-V3-1, T04) + the ElevenLabs alternative implement it.
* :class:`StreamingTTSConfig` (``env_prefix="PERSONA_TTS_"``) +
  :func:`load_streaming_tts` dispatcher.
* :class:`TTSError` (+ subclasses) — domain-exception hierarchy mirroring
  the Spec 02 ``ProviderError`` *shape* while rooting at
  :class:`persona.errors.PersonaError` (kept out of the Spec 20
  ``MultiModelChatBackend`` classifier partition; same choice V2 made).
* :class:`ResolvedVoice` / :class:`VoiceCatalogueEntry` /
  :data:`VoiceGender` — voice-resolution + catalogue boundary records
  (D-V3-X-cloning-seam-shape + D-V3-3).
* :class:`AudioChunk` — re-exported from
  :mod:`persona_voice.loop.streaming` (V1's PCM16 mono 24 kHz outbound
  record; V3's synthesis output, reused verbatim).

T04 lands the concrete Cartesia backend per D-V3-1 LOCK; T05 the chunker
per D-V3-2; T06 the audio adaptation per R-V3-4; T07 the ``voice`` schema
field + ``resolve_voice`` per D-V3-X-cloning-seam-shape; T08 the catalogue
per D-V3-3; T09 the V1 ``TTSStream`` seam adapter + stop-and-flush.
"""

from __future__ import annotations

from persona_voice.loop.streaming import AudioChunk
from persona_voice.tts._factory import load_streaming_tts
from persona_voice.tts.config import Provider, StreamingTTSConfig
from persona_voice.tts.errors import (
    TTSAudioFormatError,
    TTSAuthenticationError,
    TTSError,
    TTSRateLimitError,
    TTSStreamFailureError,
    TTSVoiceNotFoundError,
)
from persona_voice.tts.protocol import StreamingTTS
from persona_voice.tts.types import (
    ResolvedVoice,
    VoiceCatalogueEntry,
    VoiceGender,
)

__all__ = [
    "AudioChunk",
    "Provider",
    "ResolvedVoice",
    "StreamingTTS",
    "StreamingTTSConfig",
    "TTSAudioFormatError",
    "TTSAuthenticationError",
    "TTSError",
    "TTSRateLimitError",
    "TTSStreamFailureError",
    "TTSVoiceNotFoundError",
    "VoiceCatalogueEntry",
    "VoiceGender",
    "load_streaming_tts",
]
