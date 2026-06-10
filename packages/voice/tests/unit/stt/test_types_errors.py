"""Unit tests for V2 STT boundary records + STT domain exception hierarchy.

Covers the D-05-9 boundary discipline (frozen + extra="forbid"), the
Literal-discriminator pinning V4 dispatches on (event_type + source),
the default values per R-V2-2 v4_consumer_contract, and the
:class:`persona.errors.PersonaError` inheritance chain V2's STT
hierarchy mirrors from Spec 02's
:class:`persona.backends.errors.ProviderError`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.errors import PersonaError
from persona_voice.stt import (
    SpeechActivityEvent,
    SpeechEndedEvent,
    SpeechStartedEvent,
    STTAudioFormatError,
    STTAuthenticationError,
    STTError,
    STTRateLimitError,
    STTStreamFailureError,
)
from pydantic import ValidationError


def _now() -> datetime:
    return datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)


# ---------- frozen + extra=forbid (D-05-9 boundary discipline) -------------


def test_speech_started_event_is_frozen() -> None:
    event = SpeechStartedEvent(ts_audio_s=1.25, ts_emit=_now(), source="silero")
    with pytest.raises(ValidationError):
        event.ts_audio_s = 2.0  # type: ignore[misc]


def test_speech_started_event_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SpeechStartedEvent(  # type: ignore[call-arg]
            ts_audio_s=0.5,
            ts_emit=_now(),
            source="silero",
            unknown_field="x",
        )


def test_speech_ended_event_is_frozen_and_forbids_extra() -> None:
    event = SpeechEndedEvent(ts_audio_s=2.0, ts_emit=_now(), source="silero")
    with pytest.raises(ValidationError):
        event.transcript_settled = True  # type: ignore[misc]
    with pytest.raises(ValidationError):
        SpeechEndedEvent(  # type: ignore[call-arg]
            ts_audio_s=2.0,
            ts_emit=_now(),
            source="silero",
            extra="nope",
        )


# ---------- default values per R-V2-2 v4_consumer_contract -----------------


def test_speech_ended_event_default_values() -> None:
    event = SpeechEndedEvent(ts_audio_s=3.0, ts_emit=_now(), source="silero")
    assert event.transcript_settled is False
    assert event.corroborates is False
    assert event.silence_duration_ms is None
    assert event.synthetic is False


def test_speech_started_event_default_confidence_is_none() -> None:
    event = SpeechStartedEvent(ts_audio_s=0.0, ts_emit=_now(), source="provider")
    assert event.confidence is None
    assert event.synthetic is False


# ---------- Literal discriminator pinning (event_type) ---------------------


def test_speech_started_event_type_is_pinned() -> None:
    event = SpeechStartedEvent(ts_audio_s=0.1, ts_emit=_now(), source="silero")
    assert event.event_type == "speech_started"
    with pytest.raises(ValidationError):
        SpeechStartedEvent(  # type: ignore[arg-type]
            ts_audio_s=0.1,
            ts_emit=_now(),
            source="silero",
            event_type="speech_ended",
        )


def test_speech_ended_event_type_is_pinned() -> None:
    event = SpeechEndedEvent(ts_audio_s=0.2, ts_emit=_now(), source="silero")
    assert event.event_type == "speech_ended"
    with pytest.raises(ValidationError):
        SpeechEndedEvent(  # type: ignore[arg-type]
            ts_audio_s=0.2,
            ts_emit=_now(),
            source="silero",
            event_type="speech_started",
        )


# ---------- source Literal pinning -----------------------------------------


def test_source_literal_accepts_known_values() -> None:
    for src in ("silero", "provider", "webrtc"):
        event = SpeechStartedEvent(ts_audio_s=0.0, ts_emit=_now(), source=src)
        assert event.source == src


def test_source_literal_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        SpeechStartedEvent(  # type: ignore[arg-type]
            ts_audio_s=0.0,
            ts_emit=_now(),
            source="deepgram_native",
        )


def test_speech_activity_event_base_can_be_constructed() -> None:
    event = SpeechActivityEvent(ts_audio_s=0.0, ts_emit=_now(), source="silero")
    assert event.synthetic is False


# ---------- STT error hierarchy mirrors Spec 02 ProviderError --------------


def test_stt_error_inherits_persona_error() -> None:
    assert issubclass(STTError, PersonaError)


@pytest.mark.parametrize(
    "subclass",
    [
        STTAuthenticationError,
        STTRateLimitError,
        STTStreamFailureError,
        STTAudioFormatError,
    ],
)
def test_stt_error_subclasses_inherit_stt_error(
    subclass: type[STTError],
) -> None:
    assert issubclass(subclass, STTError)
    assert issubclass(subclass, PersonaError)


def test_stt_error_context_carries_provider_name() -> None:
    err = STTAuthenticationError(
        "missing PERSONA_STT_API_KEY",
        context={"provider": "deepgram"},
    )
    assert err.context["provider"] == "deepgram"
    assert "provider=deepgram" in str(err)


def test_stt_rate_limit_error_carries_retry_after() -> None:
    err = STTRateLimitError(
        "rate-limited",
        context={"provider": "deepgram", "retry_after_s": "30"},
    )
    assert err.context["retry_after_s"] == "30"
