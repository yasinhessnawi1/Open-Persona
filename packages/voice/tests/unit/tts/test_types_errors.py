"""Unit tests for V3 TTS boundary types + domain exceptions (T02).

Covers:

* :class:`ResolvedVoice` / :class:`VoiceCatalogueEntry` frozen +
  ``extra="forbid"`` invariants, defaults, and the always-``True``
  ``ai_generated`` provenance flag (D-V3-X-ai-provenance-flag).
* JSON round-trip losslessness for both records.
* The :class:`TTSError` hierarchy shape — rooted at
  :class:`persona.errors.PersonaError` (NOT
  :class:`persona.backends.errors.ProviderError`), mirroring V2's
  ``STTError`` choice so voice errors stay out of the Spec 20
  ``MultiModelChatBackend`` classifier partition.
* ``context`` dict carriage on raise.
"""

from __future__ import annotations

import pytest
from persona.backends.errors import ProviderError
from persona.errors import PersonaError
from persona_voice.tts import (
    ResolvedVoice,
    TTSAudioFormatError,
    TTSAuthenticationError,
    TTSError,
    TTSRateLimitError,
    TTSStreamFailureError,
    TTSVoiceNotFoundError,
    VoiceCatalogueEntry,
)
from pydantic import ValidationError

# ---------- ResolvedVoice --------------------------------------------------


def test_resolved_voice_minimal_defaults() -> None:
    rv = ResolvedVoice(provider="cartesia", voice_ref="voice-abc")
    assert rv.provider == "cartesia"
    assert rv.voice_ref == "voice-abc"
    assert rv.addressing == {}
    # D-V3-X-ai-provenance-flag — always True, EU AI Act Art. 50 hook.
    assert rv.ai_generated is True


def test_resolved_voice_is_frozen() -> None:
    rv = ResolvedVoice(provider="cartesia", voice_ref="v1")
    with pytest.raises(ValidationError):
        rv.voice_ref = "v2"  # type: ignore[misc]


def test_resolved_voice_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        ResolvedVoice(provider="cartesia", voice_ref="v1", clone_id="x")  # type: ignore[call-arg]


def test_resolved_voice_ai_generated_cannot_be_false() -> None:
    # Literal[True] — a False provenance flag is a validation error, not a
    # silent downgrade. All synthesised speech is AI-generated.
    with pytest.raises(ValidationError):
        ResolvedVoice(provider="cartesia", voice_ref="v1", ai_generated=False)  # type: ignore[arg-type]


def test_resolved_voice_addressing_carries_provider_extras() -> None:
    # The seam: addressing is empty at v1 but the field exists so a v0.2
    # Azure (name+endpoint) / Google (cloning-key) voice resolves to the
    # SAME shape without re-architecting.
    rv = ResolvedVoice(
        provider="azure",
        voice_ref="DragonLatestNeural",
        addressing={"speaker_profile_id": "spk-123"},
    )
    assert rv.addressing["speaker_profile_id"] == "spk-123"


def test_resolved_voice_json_round_trip() -> None:
    rv = ResolvedVoice(provider="cartesia", voice_ref="v1", addressing={"k": "v"})
    assert ResolvedVoice.model_validate_json(rv.model_dump_json()) == rv


# ---------- VoiceCatalogueEntry --------------------------------------------


def test_catalogue_entry_minimal_defaults() -> None:
    e = VoiceCatalogueEntry(voice_id="v1", name="Astrid")
    assert e.voice_id == "v1"
    assert e.name == "Astrid"
    assert e.gender == "unspecified"
    assert e.language is None
    assert e.description is None
    assert e.preview_url is None


def test_catalogue_entry_is_frozen_and_forbids_extra() -> None:
    e = VoiceCatalogueEntry(voice_id="v1", name="Astrid")
    with pytest.raises(ValidationError):
        e.name = "Kai"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        VoiceCatalogueEntry(voice_id="v1", name="Astrid", accent="nordic")  # type: ignore[call-arg]


@pytest.mark.parametrize("gender", ["masculine", "feminine", "neutral", "unspecified"])
def test_catalogue_entry_gender_literal_accepts_normalised_set(gender: str) -> None:
    e = VoiceCatalogueEntry(voice_id="v1", name="x", gender=gender)  # type: ignore[arg-type]
    assert e.gender == gender


def test_catalogue_entry_rejects_unnormalised_gender() -> None:
    with pytest.raises(ValidationError):
        VoiceCatalogueEntry(voice_id="v1", name="x", gender="gender_neutral")  # type: ignore[arg-type]


def test_catalogue_entry_json_round_trip() -> None:
    e = VoiceCatalogueEntry(
        voice_id="v1",
        name="Astrid",
        gender="feminine",
        language="nb-NO",
        description="warm narrator",
        preview_url="https://example/preview.wav",
    )
    assert VoiceCatalogueEntry.model_validate_json(e.model_dump_json()) == e


# ---------- TTSError hierarchy ---------------------------------------------

_SUBCLASSES = [
    TTSAuthenticationError,
    TTSRateLimitError,
    TTSStreamFailureError,
    TTSAudioFormatError,
    TTSVoiceNotFoundError,
]


def test_tts_error_roots_at_persona_error_not_provider_error() -> None:
    # The deliberate rooting choice (mirrors V2 STTError): voice errors must
    # NOT be catchable as ProviderError, so they never enter the Spec 20
    # MultiModelChatBackend D-20-9 retry/fallback classifier.
    assert issubclass(TTSError, PersonaError)
    assert not issubclass(TTSError, ProviderError)


@pytest.mark.parametrize("subclass", _SUBCLASSES)
def test_subclasses_inherit_tts_error(subclass: type[TTSError]) -> None:
    assert issubclass(subclass, TTSError)
    assert not issubclass(subclass, ProviderError)


@pytest.mark.parametrize("subclass", _SUBCLASSES)
def test_subclasses_carry_structured_context(subclass: type[TTSError]) -> None:
    err = subclass("boom", context={"provider": "cartesia"})
    assert err.context["provider"] == "cartesia"


def test_voice_not_found_is_a_tts_error() -> None:
    # The resolution-time fallible error (D-V3-X-cloning-seam-shape).
    err = TTSVoiceNotFoundError(
        "voice not in allowed set",
        context={"provider": "cartesia", "voice": "missing"},
    )
    assert isinstance(err, TTSError)
    assert err.context["voice"] == "missing"
