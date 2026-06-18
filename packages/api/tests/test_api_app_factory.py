"""Unit tests for the app factory + APIConfig (spec 08, T01).

No DB needed — these boot the app with an injected config and assert the
FastAPI instance, config loading, and the unknown-key tolerance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.testclient import TestClient
from persona_api.app import create_app
from persona_api.config import APIConfig

if TYPE_CHECKING:
    import pytest


def test_create_app_returns_fastapi_instance() -> None:
    app = create_app(APIConfig())
    assert isinstance(app, FastAPI)
    assert app.title == "Persona API"


def test_app_boots_with_test_client() -> None:
    # A trivial client boot: the app starts and serves the auto OpenAPI doc.
    app = create_app(APIConfig())
    with TestClient(app) as client:
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        assert resp.json()["info"]["title"] == "Persona API"


def test_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("PERSONA_API_RATE_LIMIT_MESSAGES", "13")
    cfg = APIConfig()
    assert cfg.database_url == "postgresql+psycopg://u:p@localhost:5432/db"
    assert cfg.rate_limit_messages == 13


def test_config_ignores_unknown_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    # extra="ignore": an unrelated PERSONA_API_* key must not crash construction.
    monkeypatch.setenv("PERSONA_API_SOMETHING_UNRELATED", "x")
    cfg = APIConfig()
    assert cfg.rate_limit_default == 60


def test_authoring_sampling_defaults() -> None:
    # Drafter creativity: temperature defaults hot (0.9); top_p/top_k unset.
    cfg = APIConfig()
    assert cfg.authoring_temperature == 0.9
    assert cfg.authoring_top_p is None
    assert cfg.authoring_top_k is None


def test_authoring_sampling_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONA_API_AUTHORING_TEMPERATURE", "1.1")
    monkeypatch.setenv("PERSONA_API_AUTHORING_TOP_P", "0.9")
    monkeypatch.setenv("PERSONA_API_AUTHORING_TOP_K", "40")
    cfg = APIConfig()
    assert cfg.authoring_temperature == 1.1
    assert cfg.authoring_top_p == 0.9
    assert cfg.authoring_top_k == 40


def test_effective_app_url_prefers_app_dsn_and_coerces_async() -> None:
    cfg = APIConfig(
        database_url="postgresql+asyncpg://owner@h/db",
        app_database_url="postgresql+asyncpg://persona_app@h/db",
    )
    # prefers the app DSN, coerces +asyncpg -> +psycopg (D-07-1)
    assert cfg.effective_app_database_url == "postgresql+psycopg://persona_app@h/db"


def test_effective_app_url_falls_back_to_database_url() -> None:
    cfg = APIConfig(database_url="postgresql+psycopg://owner@h/db", app_database_url="")
    assert cfg.effective_app_database_url == "postgresql+psycopg://owner@h/db"


def test_jwt_algorithms_list() -> None:
    cfg = APIConfig(jwt_algorithms="HS256, RS256")
    assert cfg.jwt_algorithms_list == ["HS256", "RS256"]
