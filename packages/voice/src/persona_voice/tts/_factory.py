"""``load_streaming_tts()`` factory — dispatches a :class:`StreamingTTSConfig`
to the right concrete :class:`StreamingTTS` backend.

Mirrors the Spec 02 :func:`persona.backends._factory.load_backend` / V2
:func:`persona_voice.stt._factory.load_streaming_stt` shape verbatim:

* Provider-specific dispatch raises :class:`TTSError` with a structured
  ``context`` dict on unknown providers.
* T04 wires the ``cartesia`` branch through to the concrete
  :class:`CartesiaStreamingTTS` class (lazy import so the factory module
  stays importable in environments without the ``cartesia`` SDK extras
  resolved at workspace install time — matches the Spec 02
  ``HFLocalBackend`` + V2 ``deepgram_backend`` lazy-import discipline).
* ``elevenlabs`` is the documented alternative behind the same Protocol
  seam (D-V3-1 paragraph 2); it lands as a v0.2 backend implementation if
  a D-V3-1 falsification trigger fires.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona_voice.tts.errors import TTSError

if TYPE_CHECKING:
    from persona_voice.tts.config import StreamingTTSConfig
    from persona_voice.tts.protocol import StreamingTTS

__all__ = ["load_streaming_tts"]


def load_streaming_tts(config: StreamingTTSConfig) -> StreamingTTS:
    """Construct the concrete :class:`StreamingTTS` for ``config.provider``.

    Args:
        config: Streaming-TTS configuration. Provider must be one of the
            values in :data:`persona_voice.tts.config.Provider`; unknown
            values raise :class:`TTSError`.

    Returns:
        A concrete backend implementing the
        :class:`persona_voice.tts.protocol.StreamingTTS` Protocol.

    Raises:
        TTSAuthenticationError: ``provider="cartesia"`` with missing
            ``PERSONA_TTS_API_KEY`` (the concrete Cartesia backend fails
            fast at construction per Spec 02 D-02-10).
        TTSError: Unknown / unsupported provider — message lists the
            Literal values for operator clarity; ``context`` carries
            ``provider`` so structured log filters can match.
    """
    provider = config.provider
    if provider == "cartesia":
        # Lazy import keeps ``persona_voice.tts`` importable without the
        # ``cartesia`` SDK resolved on every interpreter — mirrors the
        # Spec 02 ``HFLocalBackend`` + V2 ``deepgram_backend`` lazy-import
        # discipline so the factory stays cheap to import.
        from persona_voice.tts.cartesia_backend import CartesiaStreamingTTS

        return CartesiaStreamingTTS(config)
    raise TTSError(
        f"unknown or unwired TTS provider {provider!r}; expected one of cartesia, elevenlabs",
        context={"provider": provider},
    )
