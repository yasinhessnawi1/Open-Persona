"""Unit tests for the additive ``voice`` persona-identity field (Spec V3 T07).

Covers the D-V3-X-voice-schema-shape + D-V3-X-cloning-seam-shape contract:
the ``voice`` field is additive (existing personas unaffected — criterion 4),
accepts a mapping OR the ``"provider:voice_id"`` shorthand, and the
``CatalogueVoice`` record is frozen + ``extra="forbid"`` with a reserved
always-``None`` ``consent`` hook and a ``kind`` discriminator defaulting to
``"catalogue"``.
"""

from __future__ import annotations

import pytest
from persona.schema.persona import CatalogueVoice, PersonaIdentity
from pydantic import ValidationError


def _identity(**overrides: object) -> PersonaIdentity:
    base: dict[str, object] = {
        "name": "Astrid",
        "role": "guide",
        "background": "A calm narrator.",
    }
    base.update(overrides)
    return PersonaIdentity(**base)  # type: ignore[arg-type]


# ---------- additivity (criterion 4) ---------------------------------------


def test_voice_defaults_to_none() -> None:
    # Existing personas authored without a voice are byte-for-byte unaffected.
    assert _identity().voice is None


def test_existing_fields_unchanged() -> None:
    identity = _identity()
    assert identity.name == "Astrid"
    assert identity.visual_style is None


# ---------- mapping form ---------------------------------------------------


def test_voice_mapping_resolves_to_catalogue_voice() -> None:
    identity = _identity(voice={"provider": "cartesia", "voice_id": "abc-123"})
    assert isinstance(identity.voice, CatalogueVoice)
    assert identity.voice.provider == "cartesia"
    assert identity.voice.voice_id == "abc-123"
    # kind defaults; consent reserved-None.
    assert identity.voice.kind == "catalogue"
    assert identity.voice.consent is None


def test_voice_mapping_with_explicit_kind() -> None:
    identity = _identity(voice={"kind": "catalogue", "provider": "cartesia", "voice_id": "x"})
    assert isinstance(identity.voice, CatalogueVoice)


# ---------- string shorthand -----------------------------------------------


def test_voice_string_shorthand_parsed() -> None:
    identity = _identity(voice="cartesia:voice-xyz")
    assert isinstance(identity.voice, CatalogueVoice)
    assert identity.voice.provider == "cartesia"
    assert identity.voice.voice_id == "voice-xyz"


def test_voice_string_uses_first_colon_only() -> None:
    # Provider ids never contain ':'; a voice id might in theory — partition
    # on the FIRST colon keeps the whole remainder as the id.
    identity = _identity(voice="cartesia:a:b:c")
    assert identity.voice is not None
    assert identity.voice.voice_id == "a:b:c"


@pytest.mark.parametrize("bad", ["", "cartesia", ":id", "provider:", "  "])
def test_voice_string_without_provider_and_id_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        _identity(voice=bad)


# ---------- CatalogueVoice invariants --------------------------------------


def test_catalogue_voice_is_frozen_and_forbids_extra() -> None:
    cv = CatalogueVoice(provider="cartesia", voice_id="v")
    with pytest.raises(ValidationError):
        cv.voice_id = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        CatalogueVoice(provider="cartesia", voice_id="v", clone_id="x")  # type: ignore[call-arg]


def test_catalogue_voice_rejects_empty_fields() -> None:
    with pytest.raises(ValidationError):
        CatalogueVoice(provider="", voice_id="v")
    with pytest.raises(ValidationError):
        CatalogueVoice(provider="cartesia", voice_id="")


def test_consent_cannot_be_set_to_a_value() -> None:
    # consent is reserved (type None) — a v1 YAML cannot populate it.
    with pytest.raises(ValidationError):
        CatalogueVoice(provider="cartesia", voice_id="v", consent="signed")  # type: ignore[arg-type]


def test_voice_round_trips_through_json() -> None:
    identity = _identity(voice="cartesia:abc")
    restored = PersonaIdentity.model_validate_json(identity.model_dump_json())
    assert restored.voice == identity.voice
