"""Concrete Cartesia streaming-TTS backend (T04, D-V3-1 LOCK launch).

Implements :class:`persona_voice.tts.protocol.StreamingTTS` against the
Cartesia Sonic 3.5 WebSocket *contexts* API — the only module in
persona-voice that imports the ``cartesia`` SDK (Spec 02 adapter-boundary
discipline; callers depend on the Protocol + our
:class:`persona_voice.tts.errors.TTSError` hierarchy, never on the
vendor).

**Streaming shape (R-V3-1 + R-V3-4).** One WebSocket *context* per
utterance: text chunks are sent with ``continue_=True`` as the seam
adapter's chunker produces them (``max_buffer_delay_ms=0`` so the client
chunker is the single segmentation point per D-V3-X-chunker-placement);
audio streams back concurrently as base64 ``chunk`` events carrying raw
``pcm_s16le`` @ 24 kHz mono — the V1 outbound rail, requested natively so
no transcoding is needed (R-V3-4). The :class:`PCM16Reframer` turns the
provider's variable bursts into steady :class:`AudioChunk` frames. The
first audio frame is yielded well before the text stream completes (spec
§6 criterion #2). At end-of-text ``no_more_inputs`` flushes the context.

**Cancellation (D-V3-5 / D-V3-X-cancel-flush-additive-shape).**
:meth:`cancel` is idempotent + synchronous-effect-first: it marks the
stream cancelled and sends Cartesia's per-context cancel. The receive loop
checks the flag and stops yielding; the full six-step barge-in teardown
(transport-queue clear + watchdog) is the T09 seam adapter's job.

**Live-wire validation.** The real provider behaviour (latency, prosody,
flush/cancel timing) is validated at T14 external smoke against a real
``PERSONA_TTS_API_KEY``; the unit tests exercise this backend's structure
(fail-fast, error mapping, reframe wiring, cancel) against an injected
fake client.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, cast

from cartesia import (
    AsyncCartesia,
    CartesiaError,
)
from cartesia import (
    AuthenticationError as _CartesiaAuthError,
)
from cartesia import (
    RateLimitError as _CartesiaRateLimitError,
)

from persona_voice.tts.audio import OUTBOUND_SAMPLE_RATE, PCM16Reframer
from persona_voice.tts.catalogue import normalize_gender
from persona_voice.tts.errors import (
    TTSAuthenticationError,
    TTSError,
    TTSRateLimitError,
    TTSStreamFailureError,
)
from persona_voice.tts.types import VoiceCatalogueEntry

if TYPE_CHECKING:
    import asyncio
    from collections.abc import AsyncIterator

    from persona_voice.loop.streaming import AudioChunk
    from persona_voice.tts.config import StreamingTTSConfig
    from persona_voice.tts.types import ResolvedVoice, VoiceGender

__all__ = ["CartesiaStreamingTTS"]

_PROVIDER_NAME = "cartesia"

# Research R-V3-1: Cartesia bills ~1 credit/char; effective ≈ $0.023/min on
# the Startup tier (≈ 2.3 cents/min). D-V3-X-cost: assume worst-case 2×
# overage until published rates are confirmed; the real per-session cost is
# measured at T14 + rolled into VoiceLog (T11). This seeds
# ``tts_provider_cost_cents_per_minute``.
_EST_COST_CENTS_PER_MINUTE = 2.3


class CartesiaStreamingTTS:
    """Streaming-TTS backend for Cartesia Sonic 3.5 (D-V3-1 launch).

    Args:
        config: Streaming-TTS config (``PERSONA_TTS_*``). ``api_key`` is
            required — a missing/empty key fails fast at construction with
            :class:`TTSAuthenticationError` (Spec 02 D-02-10).
        client: Test seam — an injected ``AsyncCartesia`` (or compatible
            fake). Production passes ``None`` and the real client is built
            from ``config``. The SDK is imported only in this module.
    """

    def __init__(
        self,
        config: StreamingTTSConfig,
        *,
        client: AsyncCartesia | None = None,
    ) -> None:
        key = config.api_key.get_secret_value() if config.api_key is not None else ""
        if not key:
            raise TTSAuthenticationError(
                "PERSONA_TTS_API_KEY is required for the Cartesia backend",
                context={"provider": _PROVIDER_NAME},
            )
        self._config = config
        self._model = config.model
        self._client: AsyncCartesia = client or AsyncCartesia(
            api_key=key,
            base_url=config.base_url,
            # Disable hidden SDK retries on the real-time path — N×retries
            # silently inflate worst-case latency (D-20-10 spirit).
            max_retries=0,
        )
        self._cancelled = False
        self._closed = False
        self._active_ctx: Any | None = None

    # ----- introspection / capability ---------------------------------

    @property
    def provider_name(self) -> str:
        return _PROVIDER_NAME

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def consumes_raw_text(self) -> bool:
        # D-V3-2 / D-V3-X-chunker-placement: the client chunker is
        # load-bearing; Cartesia's server buffer is zeroed
        # (max_buffer_delay_ms=0) so chunking happens exactly once.
        return False

    @property
    def cost_cents_per_minute(self) -> float:
        """Estimated provider cost for VoiceLog (T11 / D-V3-X-cost)."""
        return _EST_COST_CENTS_PER_MINUTE

    # ----- catalogue (VoiceCatalogue Protocol, D-V3-3) -----------------

    async def list_voices(
        self,
        *,
        gender: VoiceGender | None = None,
        language: str | None = None,
        limit: int | None = None,
    ) -> tuple[VoiceCatalogueEntry, ...]:
        """List Cartesia catalogue voices as data-only records (D-V3-3).

        Gender/language filters are applied client-side over the normalised
        metadata so the surface stays provider-independent; ``preview_url``
        is the provider sample URL passed through for V6/F5 (not rendered).
        """
        entries: list[VoiceCatalogueEntry] = []
        try:
            async for voice in self._client.voices.list(expand=["preview_file_url"]):
                entry_gender = normalize_gender(voice.gender)
                if gender is not None and entry_gender != gender:
                    continue
                if language is not None and voice.language != language:
                    continue
                entries.append(
                    VoiceCatalogueEntry(
                        voice_id=voice.id,
                        name=voice.name,
                        gender=entry_gender,
                        language=voice.language,
                        description=voice.description,
                        preview_url=voice.preview_file_url,
                    )
                )
                if limit is not None and len(entries) >= limit:
                    break
        except CartesiaError as exc:
            raise self._map_error(exc) from exc
        return tuple(entries)

    # ----- synthesis ---------------------------------------------------

    async def synthesize(
        self,
        text_stream: AsyncIterator[str],
        voice: ResolvedVoice,
    ) -> AsyncIterator[AudioChunk]:
        """Stream synthesised audio for an incremental reply-text stream."""
        if voice.provider != _PROVIDER_NAME:
            raise TTSError(
                "resolved voice is not addressed to the cartesia provider",
                context={"provider": _PROVIDER_NAME, "voice_provider": voice.provider},
            )

        import asyncio

        self._cancelled = False
        reframer = PCM16Reframer()
        voice_param = cast("dict[str, Any]", {"mode": "id", "id": voice.voice_ref})
        output_format = {
            "container": "raw",
            "encoding": "pcm_s16le",
            "sample_rate": OUTBOUND_SAMPLE_RATE,
        }

        # One WebSocket *context* per utterance (R-V3-5 per-utterance stream
        # lifetime). ``websocket_connect()`` returns the rich connection
        # manager; ``__aenter__`` opens the live connection (the documented
        # ``.enter()`` direct-use path).
        manager = self._client.tts.websocket_connect()
        try:
            connection = await manager.__aenter__()
        except CartesiaError as exc:
            raise self._map_error(exc) from exc

        ctx = connection.context(
            model_id=self._model,
            voice=cast("Any", voice_param),
            output_format=cast("Any", output_format),
            max_buffer_delay_ms=self._config.cartesia_max_buffer_delay_ms,
        )
        self._active_ctx = ctx
        sender: asyncio.Task[None] = asyncio.create_task(
            self._drain_text(ctx, text_stream, voice_param)
        )

        try:
            async for event in ctx.receive():
                if self._cancelled:
                    break
                event_type = getattr(event, "type", None)
                if event_type == "chunk":
                    # ``audio`` exists only on the Chunk variant of the
                    # response union — getattr narrows without importing the
                    # vendor's internal model types.
                    audio = getattr(event, "audio", None)
                    if audio:
                        for frame in reframer.push(audio):
                            yield frame
                elif event_type == "done":
                    break
                elif event_type == "error":
                    raise TTSStreamFailureError(
                        getattr(event, "message", "cartesia stream error"),
                        context={
                            "provider": _PROVIDER_NAME,
                            "error_code": str(getattr(event, "error_code", "")),
                            "status_code": str(getattr(event, "status_code", "")),
                        },
                    )
            if not self._cancelled:
                tail = reframer.flush()
                if tail is not None:
                    yield tail
        except CartesiaError as exc:
            raise self._map_error(exc) from exc
        finally:
            await self._cancel_task(sender)
            self._active_ctx = None
            with contextlib.suppress(CartesiaError):
                await connection.close()

    async def _drain_text(
        self,
        ctx: Any,  # noqa: ANN401 — vendor AsyncWebSocketContext (dynamic SDK boundary)
        text_stream: AsyncIterator[str],
        voice_param: dict[str, Any],
    ) -> None:
        """Feed reply-text chunks into the context, then signal end-of-input.

        Sender side of the concurrent send/receive pair. Provider errors
        here surface to the consumer through the receive loop's error event
        / connection close; this task swallows them so cancellation does
        not raise out of the background task.
        """
        try:
            async for chunk in text_stream:
                if self._cancelled:
                    return
                if chunk:
                    await ctx.send(
                        transcript=chunk,
                        voice=cast("Any", voice_param),
                        continue_=True,
                    )
            if not self._cancelled:
                await ctx.no_more_inputs()
        except CartesiaError:
            return

    async def cancel(self) -> None:
        """Abort in-flight synthesis (V4 barge-in). Idempotent."""
        if self._cancelled:
            return
        self._cancelled = True
        ctx = self._active_ctx
        if ctx is not None:
            with contextlib.suppress(CartesiaError):
                await ctx.cancel()

    async def close(self) -> None:
        """Close the provider connection gracefully. Idempotent."""
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(CartesiaError):
            await self._client.close()

    # ----- helpers -----------------------------------------------------

    @staticmethod
    async def _cancel_task(task: asyncio.Task[None]) -> None:
        import asyncio

        if task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, CartesiaError):
            await task

    @staticmethod
    def _map_error(exc: CartesiaError) -> TTSError:
        """Map a Cartesia SDK exception to the TTS domain hierarchy."""
        context = {"provider": _PROVIDER_NAME, "error_class": type(exc).__name__}
        if isinstance(exc, _CartesiaAuthError):
            return TTSAuthenticationError(str(exc), context=context)
        if isinstance(exc, _CartesiaRateLimitError):
            return TTSRateLimitError(str(exc), context=context)
        # Connection drops, timeouts, 5xx, and any other provider failure
        # during streaming surface as a recoverable stream failure — the
        # persona falls silent cleanly (spec §6 criterion #11).
        return TTSStreamFailureError(str(exc), context=context)
