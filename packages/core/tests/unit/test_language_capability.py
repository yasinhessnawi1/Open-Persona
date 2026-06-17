"""Unit tests for the voice-language capability registry (Spec 32 B1).

The registry is the spine of per-persona declared-language routing: it
normalizes a persona's ``language_default`` to a canonical tag, resolves the
per-provider STT model+code and TTS code, and fails soft to English with a typed
event for anything the providers cannot serve (never crash, never silent
wrong-language).
"""

from __future__ import annotations

import pytest
from persona.language_capability import (
    CanonicalLanguage,
    CapabilityRegistry,
    LanguageCapability,
    LanguageFallbackEvent,
    Provider,
    serviceability_warning,
)
from pydantic import ValidationError

# ---------- normalization (the nb/nn -> no + en-US -> en collapse) -----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("en", CanonicalLanguage.EN),
        ("EN", CanonicalLanguage.EN),
        ("en-US", CanonicalLanguage.EN),
        ("en-GB", CanonicalLanguage.EN),
        ("  en  ", CanonicalLanguage.EN),
        ("no", CanonicalLanguage.NO),
        ("nb", CanonicalLanguage.NO),
        ("nb-NO", CanonicalLanguage.NO),
        ("nn", CanonicalLanguage.NO),
        ("nn-NO", CanonicalLanguage.NO),
        ("NB", CanonicalLanguage.NO),
        ("de", CanonicalLanguage.DE),
        ("de-CH", CanonicalLanguage.DE),  # base-code fallback
        ("fr", CanonicalLanguage.FR),
        ("es", CanonicalLanguage.ES),
        ("ar", CanonicalLanguage.AR),
    ],
)
def test_normalize_maps_variants_to_canonical(raw: str, expected: CanonicalLanguage) -> None:
    assert CapabilityRegistry().normalize(raw) == expected


@pytest.mark.parametrize("raw", ["", "xx", "klingon", "zz-ZZ", "   "])
def test_normalize_returns_none_for_unrecognized(raw: str) -> None:
    assert CapabilityRegistry().normalize(raw) is None


# ---------- STT resolution (Deepgram model + code) --------------------------


def test_resolve_stt_norwegian_is_nova3_code_no() -> None:
    route = CapabilityRegistry().resolve_stt("nb")  # a persona declaring nb
    assert route.model == "nova-3"
    assert route.code == "no"  # NOT nb — the literal 400 fix
    assert route.canonical == CanonicalLanguage.NO
    assert route.fallback is None


def test_resolve_stt_english_unchanged() -> None:
    route = CapabilityRegistry().resolve_stt("en")
    assert route.model == "nova-3"
    assert route.code == "en"
    assert route.fallback is None


def test_resolve_stt_unrecognized_falls_back_to_english_with_event() -> None:
    route = CapabilityRegistry().resolve_stt("klingon")
    assert route.canonical == CanonicalLanguage.EN
    assert route.code == "en"
    assert route.model == "nova-3"
    assert route.fallback is not None
    assert route.fallback.declared == "klingon"
    assert route.fallback.resolved == CanonicalLanguage.EN
    assert route.fallback.provider == Provider.DEEPGRAM
    assert route.fallback.reason  # non-empty


# ---------- TTS resolution (Cartesia code) ----------------------------------


def test_resolve_tts_norwegian_passes_language_code() -> None:
    route = CapabilityRegistry().resolve_tts("no")
    assert route.code == "no"
    assert route.canonical == CanonicalLanguage.NO
    assert route.fallback is None


def test_resolve_tts_unrecognized_falls_back_to_english_with_event() -> None:
    route = CapabilityRegistry().resolve_tts("xx")
    assert route.code == "en"
    assert route.canonical == CanonicalLanguage.EN
    assert route.fallback is not None
    assert route.fallback.provider == Provider.CARTESIA


# ---------- a KNOWN-but-unsupported language also fails soft -----------------


def test_known_canonical_marked_unsupported_falls_back() -> None:
    """Fail-soft is keyed on provider support, not just normalization: a canonical
    the registry knows but a provider does not serve still degrades to English."""
    registry = CapabilityRegistry(
        stt={
            CanonicalLanguage.EN: LanguageCapability(
                canonical=CanonicalLanguage.EN,
                provider=Provider.DEEPGRAM,
                supported=True,
                code="en",
                model="nova-3",
            ),
            CanonicalLanguage.AR: LanguageCapability(
                canonical=CanonicalLanguage.AR,
                provider=Provider.DEEPGRAM,
                supported=False,  # pretend Deepgram can't serve it
                code="ar",
                model="nova-3",
            ),
        },
    )
    route = registry.resolve_stt("ar")
    assert route.code == "en"
    assert route.canonical == CanonicalLanguage.EN
    assert route.fallback is not None


# ---------- is_serviceable (author-time validation hook) --------------------


def test_is_serviceable_true_for_served_language() -> None:
    assert CapabilityRegistry().is_serviceable("no") is True
    assert CapabilityRegistry().is_serviceable("en") is True


def test_is_serviceable_false_for_unrecognized() -> None:
    assert CapabilityRegistry().is_serviceable("klingon") is False


def test_is_serviceable_per_provider() -> None:
    registry = CapabilityRegistry(
        stt={
            CanonicalLanguage.EN: LanguageCapability(
                canonical=CanonicalLanguage.EN,
                provider=Provider.DEEPGRAM,
                supported=True,
                code="en",
                model="nova-3",
            ),
        },
        tts={
            CanonicalLanguage.EN: LanguageCapability(
                canonical=CanonicalLanguage.EN,
                provider=Provider.CARTESIA,
                supported=True,
                code="en",
            ),
            CanonicalLanguage.NO: LanguageCapability(
                canonical=CanonicalLanguage.NO,
                provider=Provider.CARTESIA,
                supported=True,
                code="no",
            ),
        },
    )
    # NO is serviceable by Cartesia (TTS) but absent from this STT table.
    assert registry.is_serviceable("no", provider=Provider.CARTESIA) is True
    assert registry.is_serviceable("no", provider=Provider.DEEPGRAM) is False
    assert registry.is_serviceable("no") is False  # both required


# ---------- fail-soft default is always resolvable --------------------------


def test_default_english_is_always_present_and_never_falls_back() -> None:
    route = CapabilityRegistry().resolve_stt("en")
    assert route.fallback is None
    tts = CapabilityRegistry().resolve_tts("en")
    assert tts.fallback is None


# ---------- frozen / wire-safe shapes ---------------------------------------


def test_canonical_language_is_json_safe_string() -> None:
    assert CanonicalLanguage.NO == "no"
    assert CanonicalLanguage.EN == "en"
    assert Provider.DEEPGRAM == "deepgram"


def test_fallback_event_is_frozen_and_forbids_extra() -> None:
    event = LanguageFallbackEvent(
        declared="xx",
        resolved=CanonicalLanguage.EN,
        provider=Provider.CARTESIA,
        reason="unsupported",
    )
    with pytest.raises(ValidationError):
        LanguageFallbackEvent(
            declared="xx",
            resolved=CanonicalLanguage.EN,
            provider=Provider.CARTESIA,
            reason="unsupported",
            extra="nope",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        event.declared = "yy"  # type: ignore[misc]  # frozen: cannot mutate


# ---------- author-time serviceability warning (D-32-4) ---------------------


def test_serviceability_warning_none_for_served_languages() -> None:
    assert serviceability_warning("en") is None
    assert serviceability_warning("nb") is None  # collapses to served `no`


def test_serviceability_warning_names_unrecognized_language() -> None:
    warning = serviceability_warning("klingon")
    assert warning is not None
    assert "klingon" in warning
    assert "English" in warning


def test_serviceability_warning_for_known_but_unsupported() -> None:
    registry = CapabilityRegistry(
        stt={
            CanonicalLanguage.EN: LanguageCapability(
                canonical=CanonicalLanguage.EN,
                provider=Provider.DEEPGRAM,
                supported=True,
                code="en",
                model="nova-3",
            ),
        },
        tts={
            CanonicalLanguage.EN: LanguageCapability(
                canonical=CanonicalLanguage.EN,
                provider=Provider.CARTESIA,
                supported=True,
                code="en",
            ),
        },
    )
    # NO is normalizable but absent from these tables → not serviceable.
    warning = serviceability_warning("no", registry=registry)
    assert warning is not None
    assert "English" in warning
