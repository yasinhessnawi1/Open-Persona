"""The per-call language plan resolved at session build (Spec 32 B2).

``resolve_call_languages`` turns a persona's ``identity.language_default`` into a
single :class:`CallLanguagePlan` carrying the STT route, the TTS route, and the
**reply language** (= what TTS will actually speak, so a TTS fall-back also
steers the reply text — never English phonetics over Norwegian words). The
composition root logs the plan's fallbacks and threads it onto the turn context.
"""

from __future__ import annotations

from persona.language_capability import (
    CanonicalLanguage,
    CapabilityRegistry,
    LanguageCapability,
    Provider,
)
from persona_voice.agent.language import (
    CallLanguagePlan,
    apply_stt_route,
    apply_tts_route,
    resolve_call_languages,
)
from persona_voice.stt.config import StreamingSTTConfig
from persona_voice.tts.config import StreamingTTSConfig


def test_english_persona_plan_has_no_fallbacks() -> None:
    plan = resolve_call_languages("en")
    assert plan.stt.code == "en"
    assert plan.stt.model == "nova-3"
    assert plan.tts.code == "en"
    assert plan.reply_language == CanonicalLanguage.EN
    assert plan.fallbacks == ()


def test_norwegian_persona_routes_to_no_and_nova3() -> None:
    plan = resolve_call_languages("nb")  # declares Bokmål; collapses to no
    assert plan.stt.model == "nova-3"
    assert plan.stt.code == "no"
    assert plan.tts.code == "no"
    assert plan.reply_language == CanonicalLanguage.NO
    assert plan.fallbacks == ()


def test_unserved_language_falls_back_both_halves() -> None:
    plan = resolve_call_languages("klingon")
    assert plan.stt.code == "en"
    assert plan.tts.code == "en"
    assert plan.reply_language == CanonicalLanguage.EN
    # both STT and TTS report the fallback
    assert len(plan.fallbacks) == 2
    assert {f.provider for f in plan.fallbacks} == {Provider.DEEPGRAM, Provider.CARTESIA}


def test_reply_language_follows_tts_when_only_tts_falls_back() -> None:
    """If TTS cannot speak the declared language, the reply must be English too —
    so the user never hears English phonetics reading the declared language."""
    registry = CapabilityRegistry(
        stt={
            CanonicalLanguage.EN: LanguageCapability(
                canonical=CanonicalLanguage.EN,
                provider=Provider.DEEPGRAM,
                supported=True,
                code="en",
                model="nova-3",
            ),
            CanonicalLanguage.NO: LanguageCapability(
                canonical=CanonicalLanguage.NO,
                provider=Provider.DEEPGRAM,
                supported=True,
                code="no",
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
            # NO absent from TTS → TTS falls back to English.
        },
    )
    plan = resolve_call_languages("no", registry=registry)
    assert plan.stt.code == "no"  # STT still serves Norwegian
    assert plan.tts.code == "en"  # TTS fell back
    assert plan.reply_language == CanonicalLanguage.EN  # reply follows the voice
    assert len(plan.fallbacks) == 1
    assert plan.fallbacks[0].provider == Provider.CARTESIA


def test_plan_is_frozen() -> None:
    import pytest
    from pydantic import ValidationError

    plan = resolve_call_languages("en")
    with pytest.raises(ValidationError):
        plan.stt = plan.stt  # type: ignore[misc]


def test_isinstance_call_language_plan() -> None:
    assert isinstance(resolve_call_languages("en"), CallLanguagePlan)


# ---------- B3: applying the STT route to the Deepgram config ---------------


def test_apply_stt_route_pins_norwegian_to_nova3_and_no() -> None:
    base = StreamingSTTConfig(model="nova-3", language_hint="en")
    plan = resolve_call_languages("nb")
    pinned = apply_stt_route(base, plan.stt)
    assert pinned.model == "nova-3"
    assert pinned.language_hint == "no"  # NOT nb, NOT the global en hint
    # The base config is unchanged (model_copy, not mutation).
    assert base.language_hint == "en"


def test_apply_stt_route_preserves_other_config_fields() -> None:
    base = StreamingSTTConfig(model="nova-3", language_hint="en", deepgram_endpointing_ms=250)
    pinned = apply_stt_route(base, resolve_call_languages("en").stt)
    assert pinned.deepgram_endpointing_ms == 250
    assert pinned.language_hint == "en"


# ---------- B4: applying the TTS route to the Cartesia config ---------------


def test_apply_tts_route_sets_norwegian_language_code() -> None:
    base = StreamingTTSConfig(provider="cartesia")
    pinned = apply_tts_route(base, resolve_call_languages("nb").tts)
    assert pinned.language == "no"
    assert base.language is None  # copy, not mutation


def test_apply_tts_route_english_sets_en() -> None:
    pinned = apply_tts_route(
        StreamingTTSConfig(provider="cartesia"), resolve_call_languages("en").tts
    )
    assert pinned.language == "en"
