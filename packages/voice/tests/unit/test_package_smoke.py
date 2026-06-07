"""Smoke test for the persona-voice workspace bootstrap (Spec V1 T02).

Verifies the package installs cleanly into the uv workspace, the public surface
imports without error, and the version string matches the pyproject pin. This
is the minimum bar T02 (workspace bootstrap) must clear before T03 onward can
add anything real.
"""

from __future__ import annotations


def test_persona_voice_imports() -> None:
    """The package is importable from the workspace install."""
    import persona_voice

    assert persona_voice.__version__ == "0.1.0"


def test_persona_core_is_importable_from_voice() -> None:
    """persona-voice depends on persona-core[postgres] (D-V1-4 direct store
    access). The workspace install must wire both into the same virtualenv.
    The schema module is the most stable surface and a good liveness probe.
    """
    from persona.schema import persona as _persona_schema  # noqa: F401


def test_livekit_substrate_is_importable() -> None:
    """LiveKit Python SDK (D-V1-1 branch (A) substrate) must be installed.

    The `livekit` package (NOT `livekit-rtc` — the historical alias) ships the
    Room / Participant / Track API V1 consumes. `livekit.api` (from the
    `livekit-api` PyPI package) ships AccessToken issuance for T04.
    """
    import livekit  # noqa: F401
    import livekit.api  # noqa: F401
