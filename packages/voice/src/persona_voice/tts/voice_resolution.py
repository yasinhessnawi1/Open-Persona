"""Per-persona voice resolution — the cloning seam (T07, D-V3-X-cloning-seam-shape).

:func:`resolve_voice` is the synthesis-time indirection between a persona's
:class:`persona.schema.persona.VoiceSpec` (held in persona-core) and a
concrete :class:`persona_voice.tts.types.ResolvedVoice` the backend
addresses. v1 resolves a catalogue selection; the resolution *step* — not a
hard-coded id — is the seam: a v0.2 cloned-voice profile resolves to the
same :class:`ResolvedVoice` shape through this same function, with no change
to the synthesis path.

**Fallible by design.** Resolution raises
:class:`persona_voice.tts.errors.TTSVoiceNotFoundError` for an unknown
provider, a voice outside the configured catalogue, or a voice-less persona
with no default — the exact error path a v0.2 cloned-voice readiness /
consent-revoked check reuses. Provisioning (clone creation) stays OUT of the
resolver: it only ever consumes already-provisioned references.

**v1 ships ONLY the seam:** the resolution indirection + the always-empty
``addressing`` map + the always-``True`` ``ai_generated`` provenance flag
(EU AI Act Art. 50, D-V3-X-ai-provenance-flag). No cloning code, no consent
capture, no sample-audio handling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona_voice.tts.errors import TTSVoiceNotFoundError
from persona_voice.tts.types import ResolvedVoice

if TYPE_CHECKING:
    from collections.abc import Collection

    from persona.schema.persona import VoiceSpec

__all__ = ["resolve_voice"]


def resolve_voice(
    spec: VoiceSpec | None,
    *,
    provider: str,
    default_voice_id: str | None = None,
    allowed_voice_ids: Collection[str] | None = None,
) -> ResolvedVoice:
    """Resolve a persona's ``voice`` to a concrete :class:`ResolvedVoice`.

    Args:
        spec: The persona's ``identity.voice`` (a
            :class:`persona.schema.persona.CatalogueVoice`), or ``None`` for
            a persona that did not author one.
        provider: The configured TTS backend's ``provider_name``. The
            resolved voice must be addressed to this provider.
        default_voice_id: The neutral catalogue fallback (D-V3-4;
            ``PERSONA_TTS_VOICE_DEFAULT``) used when ``spec`` is ``None``.
        allowed_voice_ids: Optional catalogue membership set (from T08's
            :func:`persona_voice.tts.catalogue.list_voices`). When provided,
            the resolved voice id must be a member; when ``None`` the check
            is skipped (the configured id is trusted — a live catalogue
            fetch is not always available at resolution time).

    Returns:
        A frozen :class:`ResolvedVoice` for ``provider``.

    Raises:
        TTSVoiceNotFoundError: no voice and no default (D-V3-4); the spec's
            provider does not match ``provider``; or the voice id is not in
            ``allowed_voice_ids``. ``context`` carries ``provider`` + ``voice``.
    """
    if spec is None:
        if not default_voice_id:
            raise TTSVoiceNotFoundError(
                "persona has no voice and no PERSONA_TTS_VOICE_DEFAULT is configured",
                context={"provider": provider, "voice": ""},
            )
        return _build(provider, default_voice_id, allowed_voice_ids)

    if spec.provider != provider:
        raise TTSVoiceNotFoundError(
            "persona voice is addressed to a different provider than the configured TTS backend",
            context={"provider": provider, "voice": spec.voice_id},
        )
    return _build(provider, spec.voice_id, allowed_voice_ids)


def _build(
    provider: str,
    voice_id: str,
    allowed_voice_ids: Collection[str] | None,
) -> ResolvedVoice:
    if allowed_voice_ids is not None and voice_id not in allowed_voice_ids:
        raise TTSVoiceNotFoundError(
            "voice id is not in the provider's catalogue",
            context={"provider": provider, "voice": voice_id},
        )
    return ResolvedVoice(provider=provider, voice_ref=voice_id)
