"""Per-call language plan, resolved once at session build (Spec 32 B2).

The composition root resolves a persona's ``identity.language_default`` into a
single :class:`CallLanguagePlan` and threads it through the call: the STT route
pins the Deepgram model + code (B3), the TTS route pins the Cartesia code (B4),
and :attr:`CallLanguagePlan.reply_language` — the language TTS will *actually*
speak — drives the prompt builder's "respond in {language}" injection (B5).

Keying the reply language on the TTS route (not the raw declared language) is the
correctness point: if the providers cannot speak the declared language and TTS
falls back to English, the reply text falls back with it, so the user never hears
English phonetics over Norwegian words. One resolution, one source of truth.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.language_capability import (
    CanonicalLanguage,
    CapabilityRegistry,
    LanguageFallbackEvent,
    STTRoute,
    TTSRoute,
    default_capability_registry,
)
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from persona_voice.stt.config import StreamingSTTConfig
    from persona_voice.tts.config import StreamingTTSConfig

__all__ = [
    "CallLanguagePlan",
    "apply_stt_route",
    "apply_tts_route",
    "resolve_call_languages",
]


class CallLanguagePlan(BaseModel):
    """The resolved language routing for one voice call (frozen)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stt: STTRoute
    tts: TTSRoute

    @property
    def reply_language(self) -> CanonicalLanguage:
        """The language the LLM must reply in — what TTS will actually speak."""
        return self.tts.canonical

    @property
    def fallbacks(self) -> tuple[LanguageFallbackEvent, ...]:
        """Every fail-soft that fired (STT and/or TTS), for logging + the event."""
        return tuple(e for e in (self.stt.fallback, self.tts.fallback) if e is not None)


def resolve_call_languages(
    language_default: str,
    *,
    registry: CapabilityRegistry | None = None,
) -> CallLanguagePlan:
    """Resolve a persona's declared language into the per-call routing plan.

    Args:
        language_default: the persona's ``identity.language_default``.
        registry: the capability registry (defaults to the built-in v1 matrices).

    Returns:
        The :class:`CallLanguagePlan` — STT route, TTS route, and the derived
        reply language. Never raises for an unsupported language: it fails soft to
        English and records the fallback events on the routes.
    """
    reg = registry if registry is not None else default_capability_registry()
    return CallLanguagePlan(
        stt=reg.resolve_stt(language_default),
        tts=reg.resolve_tts(language_default),
    )


def apply_stt_route(config: StreamingSTTConfig, route: STTRoute) -> StreamingSTTConfig:
    """Pin a Deepgram config to a resolved STT route for this call (Spec 32 B3).

    Overrides the model + language code per the persona's declared language,
    replacing the global ``PERSONA_STT_LANGUAGE_HINT`` default — nova-3 + ``no``
    for Norwegian (D-32-X-deepgram-no-nova3). The Deepgram backend already reads
    ``config.model`` + ``config.language_hint``, so pinning the config before the
    socket opens is the whole change. Returns a copy; the base config is untouched.
    """
    return config.model_copy(update={"model": route.model, "language_hint": route.code})


def apply_tts_route(config: StreamingTTSConfig, route: TTSRoute) -> StreamingTTSConfig:
    """Pin a Cartesia config to a resolved TTS route for this call (Spec 32 B4).

    Sets the synthesis ``language`` code per the persona's declared language —
    the missing parameter that made Norwegian text read with English phonetics.
    Cartesia voices are multilingual, so this is purely a language code, not a
    voice constraint (D-32-4). Returns a copy; the base config is untouched.
    """
    return config.model_copy(update={"language": route.code})
