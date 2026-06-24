"""Smoke test for the persona-connectors workspace bootstrap (Spec C1 T1).

Verifies the package installs cleanly into the uv workspace, the public surface
imports without error, the version matches the pyproject pin, and the three
workspace dependencies C1-D-1 mandates (persona-core, persona-runtime, and —
the deliberate departure from voice — persona-api) are all wired into the same
virtualenv. The minimum bar T1 must clear before T2 onward adds anything real.
"""

from __future__ import annotations


def test_persona_connectors_imports() -> None:
    """The package is importable from the workspace install at the pinned version."""
    import persona_connectors

    assert persona_connectors.__version__ == "0.1.0"


def test_persona_core_is_importable_from_connectors() -> None:
    """persona-connectors depends on persona-core[postgres] (C1-D-6 shared
    contracts + direct store access). The schema module is a stable liveness probe.
    """
    from persona.schema import persona as _persona_schema  # noqa: F401


def test_persona_runtime_is_importable_from_connectors() -> None:
    """persona-connectors drives the turn-based chat loop (C1-D-1) — persona-runtime
    must be in the same venv (the departure from V1, which deferred it to V5).
    """
    from persona_runtime import loop as _loop  # noqa: F401


def test_persona_api_is_importable_from_connectors() -> None:
    """C1-D-1: the deliberate departure from voice's no-api-dep posture — the
    connector reuses persona-api's chat flow + C0 delivery in-process.
    """
    from persona_api.middleware import rls_context as _rls  # noqa: F401
