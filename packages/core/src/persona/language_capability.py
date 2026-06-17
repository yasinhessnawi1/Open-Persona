"""Voice-language capability registry — the per-persona declared-language spine
(Spec 32 Feature B / B1).

Per-persona voice routing turns a persona's declared ``identity.language_default``
into concrete provider parameters: the Deepgram STT *model + language code*, the
Cartesia TTS *language code*, and (via the prompt builder) the reply language.
This module is the single, centralized place those decisions are made — the
improvement over scattering per-provider maps (Dograh) — and the single place
**fail-soft** happens: a language the configured providers cannot serve resolves
to English plus a typed :class:`LanguageFallbackEvent`, never a crash and never a
silent wrong-language call.

It lives in persona-core (the lowest layer) because three layers consume it: the
voice STT/TTS seams (resolve the provider params), the runtime prompt builder
(the resolved canonical language drives "respond in {language}"), and the API
author-time validation (:meth:`CapabilityRegistry.is_serviceable`). It is the
Tier-2 seam too — a Whisper-class STT or an additional TTS is a new table entry,
not a code change.

**Canonical tag — mirrored from Pipecat (BSD-2 / Daily).** :class:`CanonicalLanguage`
mirrors the subset of Pipecat's ``Language`` enum we serve, and
:meth:`CapabilityRegistry.normalize` mirrors Pipecat's ``resolve_language``
base-code-fallback idiom. Pipecat encodes Norwegian five ways
(``nb``/``nb-NO``/``no``/``nn``/``nn-NO``); we collapse all of them to the single
provider-served code ``no`` (D-32-X-norwegian-collapse-to-no) — the literal fix
for the Deepgram ``nb`` websocket 400.

    Mirrors values + the resolve idiom from pipecat-ai/pipecat
    (src/pipecat/transcriptions/language.py), Copyright (c) 2024-2026, Daily,
    BSD 2-Clause. Retained per the licence; see MAINTENANCE.md.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

__all__ = [
    "CanonicalLanguage",
    "CapabilityRegistry",
    "LanguageCapability",
    "LanguageFallbackEvent",
    "Provider",
    "STTRoute",
    "TTSRoute",
    "default_capability_registry",
    "language_display_name",
    "serviceability_warning",
]


class Provider(StrEnum):
    """The v1 voice providers — one STT, one TTS (D-32-SCOPE: no new backends)."""

    DEEPGRAM = "deepgram"
    """Streaming STT (Deepgram nova-3 / nova-2)."""
    CARTESIA = "cartesia"
    """Streaming TTS (Cartesia Sonic)."""


class CanonicalLanguage(StrEnum):
    """The canonical language tag — the served subset of Pipecat's ``Language``.

    Values are the provider-served codes (Norwegian collapses to ``no``); the
    BCP-47 region/Bokmål/Nynorsk variants are folded in by
    :meth:`CapabilityRegistry.normalize`, not carried as separate members.
    """

    EN = "en"
    NO = "no"
    DE = "de"
    FR = "fr"
    ES = "es"
    AR = "ar"


_DISPLAY_NAMES: dict[CanonicalLanguage, str] = {
    CanonicalLanguage.EN: "English",
    CanonicalLanguage.NO: "Norwegian",
    CanonicalLanguage.DE: "German",
    CanonicalLanguage.FR: "French",
    CanonicalLanguage.ES: "Spanish",
    CanonicalLanguage.AR: "Arabic",
}


def language_display_name(canonical: CanonicalLanguage) -> str:
    """The human-readable English name for a canonical language.

    Used by the prompt builder's reply-language directive (B5) and author-time
    UI; falls back to the code itself for any member without a name entry.
    """
    return _DISPLAY_NAMES.get(canonical, canonical.value)


# Raw ``language_default`` strings (lowercased) → canonical. Covers the BCP-47
# region variants and the five-way Norwegian encoding (collapsed to ``no``).
# A miss falls back to the base code (``split("-")[0]``) before giving up, so an
# unlisted region tag like ``de-CH`` still resolves via ``de`` — the Pipecat
# ``resolve_language`` idiom.
_ALIASES: dict[str, CanonicalLanguage] = {
    "en": CanonicalLanguage.EN,
    "eng": CanonicalLanguage.EN,
    "no": CanonicalLanguage.NO,
    "nb": CanonicalLanguage.NO,
    "nn": CanonicalLanguage.NO,
    "nob": CanonicalLanguage.NO,
    "nno": CanonicalLanguage.NO,
    "nor": CanonicalLanguage.NO,
    "de": CanonicalLanguage.DE,
    "ger": CanonicalLanguage.DE,
    "deu": CanonicalLanguage.DE,
    "fr": CanonicalLanguage.FR,
    "fra": CanonicalLanguage.FR,
    "fre": CanonicalLanguage.FR,
    "es": CanonicalLanguage.ES,
    "spa": CanonicalLanguage.ES,
    "ar": CanonicalLanguage.AR,
    "ara": CanonicalLanguage.AR,
}


class LanguageCapability(BaseModel):
    """Whether a provider serves a canonical language, and with what parameters.

    ``model`` is the STT model id (e.g. ``nova-3``); it is ``None`` for TTS
    capabilities (Cartesia selects its model elsewhere). ``note`` records the v1
    verification status — ``acceptance-tested`` for ``{en, no}``, ``supported,
    unverified`` for the rest of the provider-served set (D-32-X-full-set-registry).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    canonical: CanonicalLanguage
    provider: Provider
    supported: bool
    code: str
    model: str | None = None
    note: str = ""


class LanguageFallbackEvent(BaseModel):
    """Typed event emitted when a declared language cannot be served (fail-soft).

    Non-DB; carried back from a resolver so the caller logs a warning and emits
    it on the observability path (D-32-X-typed-event). The same shape is produced
    by both the STT and TTS fallbacks so the two paths report identically.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    declared: str
    resolved: CanonicalLanguage
    provider: Provider
    reason: str


class STTRoute(BaseModel):
    """The resolved Deepgram parameters for a call (model + language code)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    code: str
    canonical: CanonicalLanguage
    fallback: LanguageFallbackEvent | None = None


class TTSRoute(BaseModel):
    """The resolved Cartesia language code for a call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    canonical: CanonicalLanguage
    fallback: LanguageFallbackEvent | None = None


# --- the built-in v1 provider matrices (research R-32-1) --------------------
#
# Deepgram: nova-3 covers English AND Norwegian (back-filled monolingual; the
# spec's "nova-2 required for Norwegian" was corrected — D-32-X-deepgram-no-nova3).
# Cartesia Sonic serves all of these (42-language enum). ``{en, no}`` are the
# acceptance-tested pair; the rest are served-but-unverified (fail-soft still
# guards genuinely-unsupported codes).

_DEFAULT_STT: dict[CanonicalLanguage, LanguageCapability] = {
    CanonicalLanguage.EN: LanguageCapability(
        canonical=CanonicalLanguage.EN,
        provider=Provider.DEEPGRAM,
        supported=True,
        code="en",
        model="nova-3",
        note="acceptance-tested",
    ),
    CanonicalLanguage.NO: LanguageCapability(
        canonical=CanonicalLanguage.NO,
        provider=Provider.DEEPGRAM,
        supported=True,
        code="no",
        model="nova-3",
        note="acceptance-tested; nova-2 fallback",
    ),
    CanonicalLanguage.DE: LanguageCapability(
        canonical=CanonicalLanguage.DE,
        provider=Provider.DEEPGRAM,
        supported=True,
        code="de",
        model="nova-3",
        note="supported, unverified",
    ),
    CanonicalLanguage.FR: LanguageCapability(
        canonical=CanonicalLanguage.FR,
        provider=Provider.DEEPGRAM,
        supported=True,
        code="fr",
        model="nova-3",
        note="supported, unverified",
    ),
    CanonicalLanguage.ES: LanguageCapability(
        canonical=CanonicalLanguage.ES,
        provider=Provider.DEEPGRAM,
        supported=True,
        code="es",
        model="nova-3",
        note="supported, unverified",
    ),
    CanonicalLanguage.AR: LanguageCapability(
        canonical=CanonicalLanguage.AR,
        provider=Provider.DEEPGRAM,
        supported=True,
        code="ar",
        model="nova-3",
        note="supported, unverified; verify streaming at lock",
    ),
}

_DEFAULT_TTS: dict[CanonicalLanguage, LanguageCapability] = {
    lang: LanguageCapability(
        canonical=lang,
        provider=Provider.CARTESIA,
        supported=True,
        code=lang.value,
        note="acceptance-tested"
        if lang in (CanonicalLanguage.EN, CanonicalLanguage.NO)
        else "supported, unverified",
    )
    for lang in CanonicalLanguage
}


class CapabilityRegistry:
    """Resolve a persona's declared language to provider parameters (fail-soft).

    Pure reads: every method returns data (the route + any fallback event); the
    caller performs the side effects (logging, dispatching the event). The
    English default is always present in both tables so a fallback can never
    itself fail.

    Args:
        default: the fail-soft language (English by configuration).
        stt: the Deepgram capability table (defaults to the built-in matrix).
        tts: the Cartesia capability table (defaults to the built-in matrix).
    """

    def __init__(
        self,
        *,
        default: CanonicalLanguage = CanonicalLanguage.EN,
        stt: dict[CanonicalLanguage, LanguageCapability] | None = None,
        tts: dict[CanonicalLanguage, LanguageCapability] | None = None,
    ) -> None:
        self._default = default
        self._stt = stt if stt is not None else dict(_DEFAULT_STT)
        self._tts = tts if tts is not None else dict(_DEFAULT_TTS)

    def normalize(self, raw: str) -> CanonicalLanguage | None:
        """Map a raw ``language_default`` string to a canonical tag, or ``None``.

        Case-insensitive; collapses the Norwegian variants and BCP-47 region tags
        (``en-US`` → ``en``, ``de-CH`` → ``de``) via a base-code fallback. Returns
        ``None`` when the string is not a recognized served language.
        """
        key = raw.strip().lower()
        if not key:
            return None
        if key in _ALIASES:
            return _ALIASES[key]
        base = key.split("-", 1)[0]
        return _ALIASES.get(base)

    def is_serviceable(self, raw: str, *, provider: Provider | None = None) -> bool:
        """Whether the declared language is served (author-time validation, D-32-4).

        With ``provider`` unset, the language must be served by **both** STT and
        TTS (a usable call needs both halves). With a provider, only that half is
        checked.
        """
        canonical = self.normalize(raw)
        if canonical is None:
            return False
        if provider == Provider.DEEPGRAM:
            return self._supported(self._stt, canonical)
        if provider == Provider.CARTESIA:
            return self._supported(self._tts, canonical)
        return self._supported(self._stt, canonical) and self._supported(self._tts, canonical)

    def resolve_stt(self, raw: str) -> STTRoute:
        """Resolve the Deepgram model + language code; fail soft to English."""
        canonical = self.normalize(raw)
        if canonical is not None and self._supported(self._stt, canonical):
            cap = self._stt[canonical]
            return STTRoute(model=_require_model(cap), code=cap.code, canonical=canonical)
        default = self._stt[self._default]
        return STTRoute(
            model=_require_model(default),
            code=default.code,
            canonical=self._default,
            fallback=self._fallback(raw, canonical, Provider.DEEPGRAM),
        )

    def resolve_tts(self, raw: str) -> TTSRoute:
        """Resolve the Cartesia language code; fail soft to English."""
        canonical = self.normalize(raw)
        if canonical is not None and self._supported(self._tts, canonical):
            cap = self._tts[canonical]
            return TTSRoute(code=cap.code, canonical=canonical)
        default = self._tts[self._default]
        return TTSRoute(
            code=default.code,
            canonical=self._default,
            fallback=self._fallback(raw, canonical, Provider.CARTESIA),
        )

    @staticmethod
    def _supported(
        table: dict[CanonicalLanguage, LanguageCapability],
        canonical: CanonicalLanguage,
    ) -> bool:
        cap = table.get(canonical)
        return cap is not None and cap.supported

    def _fallback(
        self,
        declared: str,
        canonical: CanonicalLanguage | None,
        provider: Provider,
    ) -> LanguageFallbackEvent:
        reason = (
            "unrecognized language"
            if canonical is None
            else f"{provider.value} does not serve {canonical.value}"
        )
        return LanguageFallbackEvent(
            declared=declared,
            resolved=self._default,
            provider=provider,
            reason=reason,
        )


def _require_model(cap: LanguageCapability) -> str:
    """An STT capability must carry a model id (the table guarantees it)."""
    if cap.model is None:  # pragma: no cover - guarded by the built-in table
        msg = f"STT capability for {cap.canonical} has no model"
        raise ValueError(msg)
    return cap.model


def default_capability_registry() -> CapabilityRegistry:
    """The process-wide registry over the built-in v1 provider matrices."""
    return CapabilityRegistry()


def serviceability_warning(
    language_default: str,
    *,
    registry: CapabilityRegistry | None = None,
) -> str | None:
    """A human author-time warning if a declared language isn't serviceable (D-32-4).

    Returns ``None`` when the configured providers can serve the language end to
    end (STT + TTS); otherwise a short message explaining that voice calls will
    fall back to English. The non-blocking complement to the call-time
    soft-fallback — surfaced at persona create/update so an author learns before
    a call, never during one.
    """
    reg = registry if registry is not None else default_capability_registry()
    if reg.is_serviceable(language_default):
        return None
    if reg.normalize(language_default) is None:
        return (
            f"Declared language '{language_default}' is not recognized; "
            "voice calls will be spoken in English."
        )
    return (
        f"Declared language '{language_default}' is not fully supported by the "
        "configured voice providers; voice calls will fall back to English."
    )
