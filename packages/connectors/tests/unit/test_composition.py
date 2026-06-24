"""ConnectorComposition — the composition root (Spec C1 T1, C1-D-1).

The single api-coupled module: it assembles the connector service's shared
foundations by reusing persona-api building blocks (the edition switch, the RLS
engine, the ``current_user_id`` owner-scope — the run_worker.py pattern,
D-C1-X-rls-spine). The delivery-router + conversation-loop wiring are seamed for
T9/T10; T1 proves the foundations construct and the owner-scope set/reset is the
real persona-api contextvar (so RLS scopes every store touch).
"""

from __future__ import annotations

import pytest
from persona_api.config import Edition
from persona_api.middleware.rls_context import current_user_id
from persona_connectors.composition import ConnectorComposition
from persona_connectors.config import ConnectorConfig
from persona_connectors.errors import ConnectorError
from sqlalchemy.engine import Engine


def test_exposes_edition_enum_for_community() -> None:
    """The string config edition is surfaced as the persona-api Edition enum."""
    comp = ConnectorComposition(ConnectorConfig(edition="community"))
    assert comp.edition is Edition.community


def test_exposes_edition_enum_for_cloud() -> None:
    comp = ConnectorComposition(ConnectorConfig(edition="cloud"))
    assert comp.edition is Edition.cloud


def test_make_engine_cloud_returns_engine_without_connecting() -> None:
    """Cloud: make_engine builds an RLS-scoped Engine (lazy — no DB connection)."""
    config = ConnectorConfig(
        edition="cloud", database_url="postgresql+psycopg://u:p@localhost:5432/db"
    )
    comp = ConnectorComposition(config)
    engine = comp.make_engine()
    assert isinstance(engine, Engine)


def test_make_engine_cloud_without_url_fails_fast() -> None:
    """Cloud edition with no database_url is a misconfiguration — fail fast (ENG-STD)."""
    comp = ConnectorComposition(ConnectorConfig(edition="cloud", database_url=""))
    with pytest.raises(ConnectorError):
        comp.make_engine()


def test_owner_scope_sets_the_persona_api_contextvar() -> None:
    """The run_worker.py RLS spine (D-C1-X-rls-spine): owner_scope sets the EXACT
    persona-api ``current_user_id`` contextvar the RLS engine listener reads, so
    every store touch inside the scope is owner-scoped.
    """
    comp = ConnectorComposition(ConnectorConfig())
    assert current_user_id.get() is None
    with comp.owner_scope("user_abc"):
        assert current_user_id.get() == "user_abc"
    assert current_user_id.get() is None


def test_owner_scope_resets_even_on_exception() -> None:
    """The contextvar is reset in a finally — an error mid-scope never leaks the owner."""
    comp = ConnectorComposition(ConnectorConfig())

    def _raise_inside_scope() -> None:
        with comp.owner_scope("user_xyz"):
            assert current_user_id.get() == "user_xyz"
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _raise_inside_scope()
    assert current_user_id.get() is None
