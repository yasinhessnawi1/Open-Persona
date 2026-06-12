"""Unit tests for the voice catalogue surface (T08, D-V3-3).

Covers :func:`normalize_gender`, the :class:`VoiceCatalogue` Protocol
conformance of the Cartesia backend, and the backend's ``list_voices``
mapping + client-side gender/language/limit filtering against an injected
fake client.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from persona_voice.tts import StreamingTTSConfig, VoiceCatalogueEntry
from persona_voice.tts.cartesia_backend import CartesiaStreamingTTS
from persona_voice.tts.catalogue import VoiceCatalogue, normalize_gender

# ---------- normalize_gender -----------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("masculine", "masculine"),
        ("feminine", "feminine"),
        ("gender_neutral", "neutral"),
        ("neutral", "neutral"),
        ("MASCULINE", "masculine"),
        (None, "unspecified"),
        ("nonbinary-unknown", "unspecified"),
    ],
)
def test_normalize_gender(raw: str | None, expected: str) -> None:
    assert normalize_gender(raw) == expected


# ---------- fake Cartesia voices surface -----------------------------------


class _FakeVoice:
    def __init__(
        self,
        voice_id: str,
        name: str,
        gender: str | None,
        language: str | None,
        description: str | None = None,
        preview: str | None = None,
    ) -> None:
        self.id = voice_id
        self.name = name
        self.gender = gender
        self.language = language
        self.description = description
        self.preview_file_url = preview


class _FakePage:
    def __init__(self, voices: list[_FakeVoice]) -> None:
        self._voices = voices

    async def __aiter__(self) -> AsyncIterator[_FakeVoice]:
        for voice in self._voices:
            yield voice


class _FakeVoices:
    def __init__(self, voices: list[_FakeVoice]) -> None:
        self._voices = voices

    def list(self, **kwargs: object) -> _FakePage:  # noqa: ARG002
        return _FakePage(self._voices)


class _CatalogueClient:
    def __init__(self, voices: list[_FakeVoice]) -> None:
        self.voices = _FakeVoices(voices)


_VOICES = [
    _FakeVoice("v1", "Astrid", "feminine", "nb", "warm", "https://p/1.wav"),
    _FakeVoice("v2", "Kai", "masculine", "en", None, None),
    _FakeVoice("v3", "Maren", "gender_neutral", "sv", None, None),
    _FakeVoice("v4", "Sami", None, "ar", None, None),
]


def _backend(voices: list[_FakeVoice]) -> CartesiaStreamingTTS:
    config = StreamingTTSConfig(provider="cartesia", api_key="ct-key")
    return CartesiaStreamingTTS(config, client=_CatalogueClient(voices))  # type: ignore[arg-type]


# ---------- Protocol conformance -------------------------------------------


def test_backend_satisfies_voice_catalogue_protocol() -> None:
    assert isinstance(_backend([]), VoiceCatalogue)


# ---------- list_voices ----------------------------------------------------


@pytest.mark.asyncio
async def test_list_voices_maps_all_metadata() -> None:
    entries = await _backend(_VOICES).list_voices()
    assert all(isinstance(e, VoiceCatalogueEntry) for e in entries)
    astrid = entries[0]
    assert astrid.voice_id == "v1"
    assert astrid.name == "Astrid"
    assert astrid.gender == "feminine"
    assert astrid.language == "nb"
    assert astrid.description == "warm"
    assert astrid.preview_url == "https://p/1.wav"
    # gender_neutral normalises to neutral; missing gender → unspecified.
    assert entries[2].gender == "neutral"
    assert entries[3].gender == "unspecified"


@pytest.mark.asyncio
async def test_list_voices_filters_by_gender() -> None:
    entries = await _backend(_VOICES).list_voices(gender="masculine")
    assert [e.voice_id for e in entries] == ["v2"]


@pytest.mark.asyncio
async def test_list_voices_filters_by_language() -> None:
    entries = await _backend(_VOICES).list_voices(language="ar")
    assert [e.voice_id for e in entries] == ["v4"]


@pytest.mark.asyncio
async def test_list_voices_respects_limit() -> None:
    entries = await _backend(_VOICES).list_voices(limit=2)
    assert len(entries) == 2


@pytest.mark.asyncio
async def test_list_voices_empty_catalogue() -> None:
    assert await _backend([]).list_voices() == ()
