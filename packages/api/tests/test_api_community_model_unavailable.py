"""R1-D-2 — model-required write paths return a clean 503 keyless, not a 500.

The authoring / chat / run paths need a live model. On a keyless community boot
they used to leak a 500: either an ``AttributeError`` (``app.state.tier_registry``
unset — no model configured at all) or the backend's ``AuthenticationError``
(registry built but no key) falling through the catch-all ``_domain_500``.

R1-D-2 collapses BOTH shapes to one ``ModelBackendUnavailableError`` → 503
(mirroring ``ImageGenUnavailableError``), raised by a ROUTE-LOCAL guard at these
write paths only — so a cloud bad-key still surfaces through its normal path
(the global ``AuthenticationError`` handler is untouched; R1-D-5 stays a tracked
follow-up).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from persona_api.app import create_app
from persona_api.config import APIConfig, Edition

if TYPE_CHECKING:
    from pathlib import Path

    from persona.stores.embedder import Embedder

_YAML = (
    "schema_version: '1.0'\n"
    "identity:\n"
    "  name: Sigrid\n"
    "  role: research assistant\n"
    "  background: A research assistant built to help with literature reviews.\n"
)

_MODEL_KEY_VARS = (
    "PERSONA_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
)


def _community_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    embedder: Embedder,
    *,
    provider: str | None,
    model: str | None,
) -> FastAPI:
    """Community app, keyless. ``provider=None`` ⇒ NO model config (registry
    unset shape); a provider set with no key ⇒ built-but-keyless shape."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    for var in _MODEL_KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    if provider is None:
        monkeypatch.delenv("PERSONA_PROVIDER", raising=False)
        monkeypatch.delenv("PERSONA_MODEL", raising=False)
    else:
        monkeypatch.setenv("PERSONA_PROVIDER", provider)
        monkeypatch.setenv("PERSONA_MODEL", model or "m")
    from persona_api.services import persona_service

    monkeypatch.setattr(persona_service, "default_embedder", lambda *_a, **_k: embedder)
    config = APIConfig(
        edition=Edition.community,
        community_db_path=tmp_path / "community.db",
        community_memory_path=tmp_path / "chroma",
        workspace_root=tmp_path / "work",
        audit_root=str(tmp_path / "audit"),
    )
    return create_app(config)


def _force_no_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reproduce the true keyless-no-config boot: the registry builder raises, so
    the lifespan leaves ``tier_registry`` + the loop builders unset (the shape a
    CI boot with no model env has — deterministic regardless of a local .env)."""
    from persona_api import app as app_mod
    from persona_runtime.errors import TierNotConfiguredError

    def _raise(*_a: object, **_k: object) -> object:
        raise TierNotConfiguredError("no model configured (test)")

    monkeypatch.setattr(app_mod, "tier_registry_from_env", _raise)


# ----- authoring: both keyless shapes → 503 -----------------------------------


def test_authoring_built_but_keyless_returns_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, embedder: Embedder
) -> None:
    # Registry builds (anthropic/model) but has no key → get() would 500.
    app = _community_app(
        tmp_path, monkeypatch, embedder, provider="anthropic", model="claude-sonnet-4-6"
    )
    with TestClient(app) as client:
        assert getattr(client.app.state, "tier_registry", None) is not None
        resp = client.post("/v1/personas/author", json={"description": "a helpful tutor"})
        assert resp.status_code == 503, resp.text
        assert resp.json()["error"] == "model_unavailable"


def test_authoring_registry_unset_returns_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, embedder: Embedder
) -> None:
    # No model config at all → tier_registry unset → AttributeError shape.
    _force_no_runtime(monkeypatch)
    app = _community_app(tmp_path, monkeypatch, embedder, provider=None, model=None)
    with TestClient(app) as client:
        assert getattr(client.app.state, "tier_registry", None) is None
        resp = client.post("/v1/personas/author", json={"description": "a helpful tutor"})
        assert resp.status_code == 503, resp.text


def test_recommend_tools_keyless_returns_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, embedder: Embedder
) -> None:
    app = _community_app(
        tmp_path, monkeypatch, embedder, provider="anthropic", model="claude-sonnet-4-6"
    )
    with TestClient(app) as client:
        resp = client.post("/v1/personas/recommend-tools", json={"description": "a tutor"})
        assert resp.status_code == 503, resp.text


# ----- chat / runs: loop-builder unset → 503 ----------------------------------


def test_chat_loop_builder_unset_returns_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, embedder: Embedder
) -> None:
    _force_no_runtime(monkeypatch)
    app = _community_app(tmp_path, monkeypatch, embedder, provider=None, model=None)
    with TestClient(app) as client:
        pid = client.post("/v1/personas", json={"yaml": _YAML}).json()["id"]
        conv_id = client.post(f"/v1/personas/{pid}/conversations", json={"title": ""}).json()["id"]
        resp = client.post(f"/v1/conversations/{conv_id}/messages", json={"content": "hi"})
        assert resp.status_code == 503, resp.text


def test_runs_loop_builder_unset_returns_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, embedder: Embedder
) -> None:
    _force_no_runtime(monkeypatch)
    app = _community_app(tmp_path, monkeypatch, embedder, provider=None, model=None)
    with TestClient(app) as client:
        pid = client.post("/v1/personas", json={"yaml": _YAML}).json()["id"]
        resp = client.post(f"/v1/personas/{pid}/runs", json={"task": "do a thing"})
        assert resp.status_code == 503, resp.text
