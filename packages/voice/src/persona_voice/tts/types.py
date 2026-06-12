"""Boundary records for the V3 streaming-TTS layer.

Two record families live here, both Pydantic v2 ``frozen=True`` +
``extra="forbid"`` per the D-05-9 boundary discipline:

* :class:`ResolvedVoice` — the output of the voice-resolution indirection
  (D-V3-X-cloning-seam-shape). A persona's ``voice`` (a
  :class:`persona.schema.persona.VoiceSpec` held in persona-core, added at
  T07) resolves AT SYNTHESIS TIME, fallibly, to one of these. v1 always
  resolves a catalogue selection; the ``addressing`` extension + the
  ``ai_generated`` provenance flag are the entire v1 investment in the
  cloning seam — a v0.2 cloned-voice profile produces the SAME
  :class:`ResolvedVoice` shape with no change to the synthesis path.
* :class:`VoiceCatalogueEntry` — one row of a provider's voice catalogue
  (D-V3-3), surfaced data-only so authoring (Spec 10) and management
  (Spec F5) can present real choices. Preview rendering belongs to V6/F5;
  v1 carries only the provider-supplied ``preview_url`` pass-through.

**Synthesis-output record.** V3 does NOT define its own audio record —
the synthesised-audio boundary is V1's
:class:`persona_voice.loop.streaming.AudioChunk` (PCM16 mono, explicit
``sample_rate`` per D-V1-6), reused verbatim and re-exported from the
package surface. The :class:`StreamingTTS` Protocol yields it directly.

**Why ``ResolvedVoice`` lives in persona-voice, not persona-core.**
persona-voice depends on persona-core, never the reverse. The
provider-neutral *input* type (``CatalogueVoice`` / ``VoiceSpec``) lives
in persona-core's schema (the ``voice`` field, T07); the *resolved* type
— which carries provider-scoped handles a backend consumes — lives here
in the voice layer, where the backends are.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ResolvedVoice",
    "VoiceCatalogueEntry",
    "VoiceGender",
]

VoiceGender = Literal["masculine", "feminine", "neutral", "unspecified"]
"""Catalogue gender tag.

Normalised across providers (Cartesia exposes
``masculine``/``feminine``/``gender_neutral``; ElevenLabs uses free-text
labels) onto this closed set so authoring/management can filter
provider-independently. ``unspecified`` covers voices a provider does not
tag.
"""


class ResolvedVoice(BaseModel):
    """The synthesis-time resolution of a persona's ``voice`` (D-V3-X-cloning-seam-shape).

    Produced by ``resolve_voice`` (T07); consumed by the concrete backend
    (T04) which passes ``voice_ref`` (plus any provider-interpreted
    ``addressing`` fields) to the provider. The indirection is the cloning
    seam: today ``voice_ref`` is a catalogue voice id; a v0.2 cloned-voice
    profile resolves to the same record shape (a different ``voice_ref``,
    possibly populated ``addressing``) without re-architecting the
    synthesis path.

    Attributes:
        provider: The TTS provider this voice is addressed to (must match
            the configured backend's ``provider_name``). Lowercase ASCII.
        voice_ref: The opaque provider-scoped handle the backend passes to
            the provider — a catalogue voice id at v1. The majority
            provider cluster (Cartesia, ElevenLabs, ...) addresses both
            catalogue and (future) cloned voices by this same handle
            namespace (R-V3-3).
        addressing: Provider-interpreted extra addressing material, EMPTY
            at v1. The seam's future-proofing: Azure custom voices need a
            ``(name, endpoint_id)`` tuple, Google instant clones a
            client-held ``voice_cloning_key`` — each backend reads only
            its own provider's keys (adapter-boundary rule). v1 backends
            assert this is empty.
        ai_generated: EU AI Act Art. 50 provenance flag (D-V3-X-ai-provenance-flag).
            Always ``True`` — all synthesised speech is AI-generated; the
            transparency obligation binds catalogue voices (not just
            clones) from 2026-08-02. Carrying the flag now means the
            downstream disclosure surface (V6 UI / transport metadata)
            lands without re-architecture.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    voice_ref: str
    addressing: Mapping[str, str] = Field(default_factory=dict)
    ai_generated: Literal[True] = True


class VoiceCatalogueEntry(BaseModel):
    """One voice in a provider's catalogue (D-V3-3) — data-only.

    Surfaced by ``list_voices`` (T08) so authoring (Spec 10) and
    management (Spec F5) can present real choices. V3 does NOT render
    previews; ``preview_url`` is the provider-supplied sample URL passed
    through for V6/F5 to use if they choose.

    Attributes:
        voice_id: The provider-scoped handle — the value a persona's
            ``voice`` selects and that becomes
            :attr:`ResolvedVoice.voice_ref`.
        name: Human-readable voice name from the provider.
        gender: Normalised :data:`VoiceGender` tag.
        language: Primary language tag (ISO 639-1 or locale, e.g. ``"en"``
            / ``"nb-NO"``). A voice may still be language-spanning at
            synthesis time per D-V3-6 — this is the catalogue's primary
            tag, not a hard lock.
        description: Free-text provider description (style/age cues live
            here; providers rarely expose them as structured fields).
        preview_url: Optional provider-hosted sample-audio URL passed
            through for V6/F5 preview UX; ``None`` when the provider
            offers none.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    voice_id: str
    name: str
    gender: VoiceGender = "unspecified"
    language: str | None = None
    description: str | None = None
    preview_url: str | None = None
