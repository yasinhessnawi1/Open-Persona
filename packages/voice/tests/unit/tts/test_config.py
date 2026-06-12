"""Unit tests for :class:`StreamingTTSConfig` (T03).

Mirrors V2's ``test_config.py`` discipline. Covers: env-var reads under
``PERSONA_TTS_*``, :class:`~pydantic.SecretStr` repr safety, Field
constraints on every numeric knob (fail-fast at construction), defaults
matching the D-V3-1 / D-V3-2 LOCKs, and ``extra="ignore"`` tolerance.
"""

from __future__ import annotations

import os

import pytest
from persona_voice.tts import StreamingTTSConfig
from pydantic import SecretStr, ValidationError


@pytest.fixture(autouse=True)
def _strip_persona_tts_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("PERSONA_TTS_"):
            monkeypatch.delenv(key, raising=False)


# ---------- defaults (D-V3-1 / D-V3-2 LOCKs) -------------------------------


def test_defaults_match_locked_launch_shape() -> None:
    config = StreamingTTSConfig()
    assert config.provider == "cartesia"
    assert config.model == "sonic-3.5"
    assert config.api_key is None
    assert config.base_url is None
    assert config.request_timeout_s == 60.0
    assert config.voice_default is None
    # D-V3-2 chunking knobs.
    assert config.chunk_min_first_chars == 10
    assert config.chunk_max_first_words == 30
    assert config.chunk_min_chars == 20
    assert config.chunk_max_chars == 300
    # D-V3-1 Cartesia knobs.
    assert config.cartesia_version == "2026-03-01"
    # D-V3-X-chunker-placement: provider buffer zeroed (client chunker
    # load-bearing).
    assert config.cartesia_max_buffer_delay_ms == 0


# ---------- env-var reads --------------------------------------------------


def test_reads_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONA_TTS_PROVIDER", "elevenlabs")
    monkeypatch.setenv("PERSONA_TTS_MODEL", "eleven_flash_v2_5")
    monkeypatch.setenv("PERSONA_TTS_API_KEY", "tts-secret")
    monkeypatch.setenv("PERSONA_TTS_VOICE_DEFAULT", "voice-xyz")
    config = StreamingTTSConfig()
    assert config.provider == "elevenlabs"
    assert config.model == "eleven_flash_v2_5"
    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "tts-secret"
    assert config.voice_default == "voice-xyz"


def test_extra_env_vars_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    # extra="ignore" — tolerate unrelated PERSONA_TTS_* the process env may
    # carry without erroring (Pydantic Settings convention).
    monkeypatch.setenv("PERSONA_TTS_SOMETHING_UNKNOWN", "x")
    config = StreamingTTSConfig()
    assert config.provider == "cartesia"


# ---------- SecretStr repr safety ------------------------------------------


def test_api_key_is_secret_str_and_not_in_repr() -> None:
    config = StreamingTTSConfig(api_key=SecretStr("super-secret"))
    assert "super-secret" not in repr(config)
    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "super-secret"


# ---------- Field constraints (fail-fast) ----------------------------------


def test_request_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        StreamingTTSConfig(request_timeout_s=0.0)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("chunk_min_first_chars", 0),
        ("chunk_max_first_words", 0),
        ("chunk_min_chars", 0),
        ("chunk_max_chars", 10),  # below the ge=20 floor
        ("cartesia_max_buffer_delay_ms", -1),
        ("cartesia_max_buffer_delay_ms", 5001),
    ],
)
def test_numeric_knobs_reject_out_of_range(field: str, bad_value: int) -> None:
    with pytest.raises(ValidationError):
        StreamingTTSConfig(**{field: bad_value})


def test_provider_literal_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        StreamingTTSConfig(provider="nonsense")  # type: ignore[arg-type]
