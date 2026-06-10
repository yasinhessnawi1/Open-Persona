"""``load_streaming_stt()`` factory — dispatches a :class:`StreamingSTTConfig`
to the right concrete :class:`StreamingSTT` backend.

Mirrors the Spec 02 :func:`persona.backends._factory.load_backend`
shape verbatim (``_factory.py:28-60``):

* Frozen set of provider tokens covered by the shared
  OpenAI-compatible / WebSocket adapter family (``deepgram`` is the
  D-V2-1 LOCK launch; ``speechmatics`` is the alternative behind the
  same Protocol seam per D-V2-1 paragraph 2; both land on the
  WebSocket adapter family but only the launch ships at T04).
* Provider-specific dispatch raises :class:`STTError` with a structured
  ``context`` dict on unknown providers.
* T04 wires the ``deepgram`` branch through to the concrete
  :class:`DeepgramStreamingSTT` class (lazy import so the factory module
  stays importable in environments without ``deepgram-sdk`` extras
  resolved at workspace install time — matches Spec 02 ``HFLocalBackend``
  lazy-import discipline at ``_factory.py:53-55``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona_voice.stt.errors import STTError

if TYPE_CHECKING:
    from persona_voice.stt.config import StreamingSTTConfig
    from persona_voice.stt.protocol import StreamingSTT

__all__ = ["load_streaming_stt"]


def load_streaming_stt(config: StreamingSTTConfig) -> StreamingSTT:
    """Construct the concrete :class:`StreamingSTT` for ``config.provider``.

    Args:
        config: Streaming-STT configuration. Provider must be one of
            the values in :data:`persona_voice.stt.config.Provider`;
            unknown values raise :class:`STTError`.

    Returns:
        A concrete backend implementing the
        :class:`persona_voice.stt.protocol.StreamingSTT` Protocol.

    Raises:
        STTAuthenticationError: ``provider="deepgram"`` with missing
            ``PERSONA_STT_API_KEY`` (the concrete Deepgram backend
            fails fast at construction per Spec 02 D-02-10).
        STTError: Unknown / unsupported provider — message lists the
            three Literal values for operator clarity; ``context``
            carries ``provider`` so structured log filters can match.
    """
    provider = config.provider
    if provider == "deepgram":
        # Lazy import keeps ``persona_voice.stt`` importable without the
        # ``deepgram-sdk`` extras resolved on this interpreter — mirrors
        # Spec 02 ``HFLocalBackend`` lazy-import discipline at
        # ``_factory.py:53-55`` so the factory stays cheap to import.
        from persona_voice.stt.deepgram_backend import DeepgramStreamingSTT

        return DeepgramStreamingSTT(config)
    raise STTError(
        f"unknown STT provider {provider!r}; expected one of "
        "deepgram, speechmatics, whisper-streaming",
        context={"provider": provider},
    )
