"""Author-time voice-language warning at persona save (Spec 32 D-32-4).

The call-time soft-fallback (Spec 32 B) keeps an unserviceable-language call from
crashing; this is its non-blocking author-time complement — the author is warned
when a persona declares a language the configured voice providers can't serve,
before a call rather than during one.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from loguru import logger
from persona.schema.persona import Persona, PersonaIdentity
from persona_api.services.persona_service import _warn_if_language_unserviceable


@pytest.fixture
def captured_warnings() -> Iterator[list[str]]:
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m), level="WARNING")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


def _persona(language_default: str) -> Persona:
    return Persona(
        persona_id="persona_x",
        owner_id="owner_x",
        identity=PersonaIdentity(
            name="Astrid",
            role="assistant",
            background="bg",
            language_default=language_default,
        ),
    )


def test_unserviceable_language_logs_a_warning(captured_warnings: list[str]) -> None:
    _warn_if_language_unserviceable(_persona("klingon"))
    assert any("unserviceable voice language" in m for m in captured_warnings)
    assert any("klingon" in m for m in captured_warnings)


def test_served_language_is_silent(captured_warnings: list[str]) -> None:
    _warn_if_language_unserviceable(_persona("nb"))  # collapses to served `no`
    _warn_if_language_unserviceable(_persona("en"))
    assert captured_warnings == []
