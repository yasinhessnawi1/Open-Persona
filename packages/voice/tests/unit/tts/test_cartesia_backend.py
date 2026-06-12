"""Unit tests for the Cartesia streaming-TTS backend (T04, D-V3-1).

The real Cartesia SDK is isolated behind an injected fake client (the
``client=`` constructor seam) so these tests exercise the backend's
*structure* — fail-fast, introspection, voice-provider guard, the
synthesize → reframe → yield happy path, done/error handling, and cancel —
without a live connection. Real provider behaviour is validated at the T14
external smoke.

The fake SDK surface mirrors the vendor's dynamically-typed
``AsyncWebSocketContext`` (``voice``/``**kwargs`` are ``Any``; ``send`` and
``context`` carry params they don't all use) — the same
``# ruff: noqa: ANN401, ARG002`` carve-out V2's deepgram backend test uses
for the identical adapter-boundary reason.
"""

# ruff: noqa: ANN401, ARG002

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from cartesia import CartesiaError
from persona_voice.loop.streaming import AudioChunk
from persona_voice.tts import (
    ResolvedVoice,
    StreamingTTS,
    StreamingTTSConfig,
    TTSAuthenticationError,
    TTSError,
    TTSStreamFailureError,
)
from persona_voice.tts.cartesia_backend import CartesiaStreamingTTS

_VOICE = ResolvedVoice(provider="cartesia", voice_ref="voice-1")


# ---------- fake Cartesia SDK surface --------------------------------------


class _FakeEvent:
    def __init__(
        self,
        event_type: str,
        *,
        audio: bytes | None = None,
        message: str = "",
        error_code: str = "",
        status_code: str = "",
    ) -> None:
        self.type = event_type
        self.audio = audio
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


class _FakeCtx:
    def __init__(self, events: list[_FakeEvent]) -> None:
        self._events = events
        self.sent: list[str] = []
        self.no_more = False
        self.cancelled = False
        self.context_kwargs: dict[str, Any] = {}

    async def send(self, *, transcript: str, voice: Any, continue_: bool = True) -> None:
        self.sent.append(transcript)

    async def no_more_inputs(self) -> None:
        self.no_more = True

    async def cancel(self) -> None:
        self.cancelled = True

    async def receive(self) -> AsyncIterator[_FakeEvent]:
        for event in self._events:
            yield event


class _FakeConn:
    def __init__(self, ctx: _FakeCtx) -> None:
        self._ctx = ctx
        self.closed = False

    def context(self, **kwargs: Any) -> _FakeCtx:
        self._ctx.context_kwargs = kwargs
        return self._ctx

    async def close(self) -> None:
        self.closed = True


class _FakeManager:
    def __init__(self, conn: _FakeConn, *, raise_on_connect: bool) -> None:
        self._conn = conn
        self._raise = raise_on_connect

    async def __aenter__(self) -> _FakeConn:
        if self._raise:
            raise CartesiaError("connect failed")
        return self._conn


class _FakeTTS:
    def __init__(self, conn: _FakeConn, *, raise_on_connect: bool = False) -> None:
        self._conn = conn
        self._raise = raise_on_connect

    def websocket_connect(self) -> _FakeManager:
        return _FakeManager(self._conn, raise_on_connect=self._raise)


class _FakeClient:
    def __init__(self, ctx: _FakeCtx, *, raise_on_connect: bool = False) -> None:
        self.tts = _FakeTTS(_FakeConn(ctx), raise_on_connect=raise_on_connect)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _backend(ctx: _FakeCtx, *, raise_on_connect: bool = False) -> CartesiaStreamingTTS:
    config = StreamingTTSConfig(provider="cartesia", api_key="ct-key")
    client = _FakeClient(ctx, raise_on_connect=raise_on_connect)
    return CartesiaStreamingTTS(config, client=client)  # type: ignore[arg-type]


async def _text(*items: str) -> AsyncIterator[str]:
    for item in items:
        yield item


# ---------- construction / introspection -----------------------------------


def test_fails_fast_without_api_key() -> None:
    config = StreamingTTSConfig(provider="cartesia")
    with pytest.raises(TTSAuthenticationError) as exc:
        CartesiaStreamingTTS(config)
    assert exc.value.context["provider"] == "cartesia"


def test_satisfies_streaming_tts_protocol() -> None:
    backend = _backend(_FakeCtx([]))
    assert isinstance(backend, StreamingTTS)


def test_introspection_properties() -> None:
    backend = _backend(_FakeCtx([]))
    assert backend.provider_name == "cartesia"
    assert backend.model_name == "sonic-3.5"
    assert backend.consumes_raw_text is False
    assert backend.cost_cents_per_minute > 0


# ---------- synthesize happy path ------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_reframes_provider_audio() -> None:
    # Two chunk events of 1200 samples (2400 bytes) each → 4800 bytes total,
    # reframed into progressive frames + flushed remainder.
    events = [
        _FakeEvent("chunk", audio=b"\x01\x02" * 1200),
        _FakeEvent("chunk", audio=b"\x03\x04" * 1200),
        _FakeEvent("done"),
    ]
    ctx = _FakeCtx(events)
    backend = _backend(ctx)
    frames = [f async for f in backend.synthesize(_text("Hello.", "World."), _VOICE)]
    assert frames
    assert all(isinstance(f, AudioChunk) for f in frames)
    assert all(f.sample_rate == 24000 for f in frames)
    # Re-framing is lossless: total bytes out == total provider bytes in.
    assert sum(len(f.data) for f in frames) == 4800
    # First frame is the progressive 20 ms opener (480 samples).
    assert frames[0].samples_per_channel == 480


@pytest.mark.asyncio
async def test_synthesize_requests_raw_pcm16_24k_and_zero_buffer() -> None:
    ctx = _FakeCtx([_FakeEvent("done")])
    backend = _backend(ctx)
    _ = [f async for f in backend.synthesize(_text("Hi."), _VOICE)]
    kw = ctx.context_kwargs
    assert kw["output_format"]["encoding"] == "pcm_s16le"
    assert kw["output_format"]["sample_rate"] == 24000
    assert kw["voice"]["id"] == "voice-1"
    # D-V3-X-chunker-placement: client chunker is the single segmentation
    # point → provider buffer zeroed.
    assert kw["max_buffer_delay_ms"] == 0


@pytest.mark.asyncio
async def test_drain_text_feeds_chunks_and_signals_end() -> None:
    # The sender side runs concurrently with receive in synthesize; test it
    # directly for determinism (a scripted receive() can race the sender).
    ctx = _FakeCtx([])
    backend = _backend(ctx)
    await backend._drain_text(ctx, _text("One. ", "Two."), {"mode": "id", "id": "v"})
    assert ctx.sent == ["One. ", "Two."]
    assert ctx.no_more is True


# ---------- error handling -------------------------------------------------


@pytest.mark.asyncio
async def test_error_event_raises_stream_failure() -> None:
    events = [_FakeEvent("error", message="boom", error_code="E1", status_code="500")]
    backend = _backend(_FakeCtx(events))
    with pytest.raises(TTSStreamFailureError) as exc:
        _ = [f async for f in backend.synthesize(_text("Hi."), _VOICE)]
    assert exc.value.context["provider"] == "cartesia"
    assert exc.value.context["error_code"] == "E1"


@pytest.mark.asyncio
async def test_connect_failure_maps_to_stream_failure() -> None:
    backend = _backend(_FakeCtx([]), raise_on_connect=True)
    with pytest.raises(TTSStreamFailureError):
        _ = [f async for f in backend.synthesize(_text("Hi."), _VOICE)]


@pytest.mark.asyncio
async def test_voice_provider_mismatch_raises() -> None:
    backend = _backend(_FakeCtx([_FakeEvent("done")]))
    wrong = ResolvedVoice(provider="elevenlabs", voice_ref="v")
    with pytest.raises(TTSError) as exc:
        _ = [f async for f in backend.synthesize(_text("Hi."), wrong)]
    assert exc.value.context["voice_provider"] == "elevenlabs"


def test_map_error_classifies_auth_and_rate() -> None:
    from cartesia import AuthenticationError, RateLimitError
    from persona_voice.tts import TTSAuthenticationError, TTSRateLimitError

    auth = AuthenticationError.__new__(AuthenticationError)
    rate = RateLimitError.__new__(RateLimitError)
    generic = CartesiaError("boom")
    assert isinstance(CartesiaStreamingTTS._map_error(auth), TTSAuthenticationError)
    assert isinstance(CartesiaStreamingTTS._map_error(rate), TTSRateLimitError)
    assert isinstance(CartesiaStreamingTTS._map_error(generic), TTSStreamFailureError)


# ---------- cancel / close -------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_is_idempotent_and_cancels_active_context() -> None:
    ctx = _FakeCtx([_FakeEvent("done")])
    backend = _backend(ctx)
    backend._active_ctx = ctx  # simulate an in-flight synthesis
    await backend.cancel()
    assert ctx.cancelled is True
    # Second call is a no-op (idempotent).
    ctx.cancelled = False
    await backend.cancel()
    assert ctx.cancelled is False


@pytest.mark.asyncio
async def test_close_is_idempotent() -> None:
    client_holder = _FakeCtx([])
    config = StreamingTTSConfig(provider="cartesia", api_key="ct-key")
    fake = _FakeClient(client_holder)
    backend = CartesiaStreamingTTS(config, client=fake)  # type: ignore[arg-type]
    await backend.close()
    assert fake.closed is True
    fake.closed = False
    await backend.close()
    assert fake.closed is False  # no-op second time
