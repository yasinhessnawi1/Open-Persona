"""T17 (§2.7 / D-20-17) — ``_compose_image_backend`` recognizes the
``PERSONA_IMAGEGEN_MODELS`` list, not just the legacy triplet.

The §2.7 bug: a Setup C ``PERSONA_IMAGEGEN_MODELS`` config with no triplet
``PERSONA_IMAGEGEN_API_KEY`` silently fell back to the hard-coded
``openai/gpt-image-1`` default and 503'd. These tests pin the four D-20-17
cases via the real composition function + env monkeypatching (no network —
backend construction stores the key without validating it).
"""

from __future__ import annotations

import pytest
from persona.imagegen.cloudflare_image import CloudflareImageBackend
from persona_api.app import _compose_image_backend

_FLUX = "@cf/black-forest-labs/flux-1-schnell"

_IMAGEGEN_ENV_VARS = [
    "PERSONA_IMAGEGEN_MODELS",
    "PERSONA_IMAGEGEN_PROVIDER",
    "PERSONA_IMAGEGEN_MODEL",
    "PERSONA_IMAGEGEN_API_KEY",
    "PERSONA_IMAGEGEN_CLOUDFLARE_ACCOUNT_ID",
    "PERSONA_CLOUDFLARE_ACCOUNT_ID",
    "PERSONA_CLOUDFLARE_API_KEY",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _IMAGEGEN_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_neither_configured_returns_none() -> None:
    # Case (a-none): no MODELS, no triplet key → None (clear "not configured").
    # (env cleared by the autouse _clean_env fixture.)
    assert _compose_image_backend() is None


def test_triplet_path_builds_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    # Case (b): triplet only.
    monkeypatch.setenv("PERSONA_IMAGEGEN_PROVIDER", "cloudflare")
    monkeypatch.setenv("PERSONA_IMAGEGEN_MODEL", _FLUX)
    monkeypatch.setenv("PERSONA_IMAGEGEN_API_KEY", "tok")
    monkeypatch.setenv("PERSONA_IMAGEGEN_CLOUDFLARE_ACCOUNT_ID", "acc123")
    backend = _compose_image_backend()
    assert isinstance(backend, CloudflareImageBackend)


def test_models_list_path_recognized(monkeypatch: pytest.MonkeyPatch) -> None:
    # Case (d) — the §2.7 fix: MODELS set, NO triplet key. Pre-fix this
    # returned None (ignored MODELS); now it builds the MODELS backend.
    monkeypatch.setenv("PERSONA_IMAGEGEN_MODELS", f"cloudflare/{_FLUX}")
    monkeypatch.setenv("PERSONA_CLOUDFLARE_API_KEY", "tok")
    monkeypatch.setenv("PERSONA_IMAGEGEN_CLOUDFLARE_ACCOUNT_ID", "acc123")
    backend = _compose_image_backend()
    assert isinstance(backend, CloudflareImageBackend)
    assert backend.model_name == _FLUX


def test_models_wins_over_triplet(monkeypatch: pytest.MonkeyPatch) -> None:
    # Case (c): both set → MODELS wins. Triplet points at a different model;
    # the composed backend must reflect the MODELS entry.
    monkeypatch.setenv("PERSONA_IMAGEGEN_MODELS", f"cloudflare/{_FLUX}")
    monkeypatch.setenv("PERSONA_CLOUDFLARE_API_KEY", "tok")
    monkeypatch.setenv("PERSONA_IMAGEGEN_CLOUDFLARE_ACCOUNT_ID", "acc123")
    monkeypatch.setenv("PERSONA_IMAGEGEN_PROVIDER", "openai")
    monkeypatch.setenv("PERSONA_IMAGEGEN_MODEL", "gpt-image-1")
    monkeypatch.setenv("PERSONA_IMAGEGEN_API_KEY", "sk-triplet")
    backend = _compose_image_backend()
    assert isinstance(backend, CloudflareImageBackend)
    assert backend.model_name == _FLUX


def test_unresolvable_models_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # MODELS set but the slot's credential is missing → all-fail → None
    # (boot cleanly; 503 at request time), not a startup crash.
    monkeypatch.setenv("PERSONA_IMAGEGEN_MODELS", f"cloudflare/{_FLUX}")
    # No PERSONA_CLOUDFLARE_API_KEY set.
    assert _compose_image_backend() is None


def test_models_path_account_id_via_per_provider_name(monkeypatch: pytest.MonkeyPatch) -> None:
    # Operator-pass regression (§2.7): on the MODELS path the API key resolves
    # via PERSONA_CLOUDFLARE_API_KEY, so the account_id's intuitive sibling is
    # PERSONA_CLOUDFLARE_ACCOUNT_ID (NOT the imagegen-prefixed name). Must work.
    monkeypatch.setenv("PERSONA_IMAGEGEN_MODELS", f"cloudflare/{_FLUX}")
    monkeypatch.setenv("PERSONA_CLOUDFLARE_API_KEY", "tok")
    monkeypatch.setenv("PERSONA_CLOUDFLARE_ACCOUNT_ID", "acc123")  # per-provider name
    backend = _compose_image_backend()
    assert isinstance(backend, CloudflareImageBackend)
    assert backend.model_name == _FLUX
