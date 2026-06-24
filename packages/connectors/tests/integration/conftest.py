"""Shared fixtures for persona-connectors integration tests (Spec C1).

Scoped to ``integration/`` so unit tests never depend on a database. The schema is
built **once per session** from the canonical Core ``MetaData`` (the proven api
``pg_engine`` pattern — NOT in-process Alembic, which duplicates
``alembic_version`` across the fixture's pooled connection) with the connector
RLS policies applied; **data is cleaned per test** via ``TRUNCATE`` (no per-test
``DROP SCHEMA``, which races on the connection-pool catalog). ``migrated_engine``
is the superuser/dispatch (BYPASSRLS) engine; ``app_engine`` is the non-superuser
``persona_app`` RLS engine. The migration FILE's own correctness is proven
out-of-process by ``alembic upgrade head``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

# The connector tables' RLS policy (mirrors connector_identity_linking verbatim).
_CUR = "current_setting('app.current_user_id', true)"
_CONNECTOR_RLS_TABLES = (
    "connector_link_tokens",
    "connector_identities",
    "connector_channels",
    "connector_conversations",
)


@pytest.fixture(scope="session")
def database_url() -> str:
    """The superuser DSN for the disposable test database (guards the dev DB)."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set; skipping connector integration tests")
    url = url.replace("+asyncpg", "+psycopg")
    db_name = make_url(url).database or ""
    if os.environ.get("PERSONA_TEST_DB") != "1" and not db_name.endswith("_test"):
        pytest.skip(
            f"Refusing destructive fixtures against {db_name!r} (they DROP SCHEMA public "
            f"CASCADE). Use a '_test' database or set PERSONA_TEST_DB=1."
        )
    return url


@pytest.fixture(scope="session")
def migrated_engine(database_url: str) -> Iterator[Engine]:
    """A superuser engine on a session-built schema WITH the connector RLS policies.

    Built once: ``DROP SCHEMA`` → ``MetaData.create_all`` → ENABLE/FORCE/CREATE
    POLICY on the connector tables (the migration's RLS DDL) → grant ``persona_app``.
    Also the cross-tenant dispatch (superuser/BYPASSRLS) engine the store uses.
    """
    from persona_api.db import metadata as target_metadata
    from persona_api.db.rls import upgrade_rls_sql

    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            target_metadata.create_all(conn)
            # The base-table RLS (personas/conversations/messages/…) — so a
            # downstream read scoped to the resolved owner is genuinely RLS-confined,
            # not just the connector tables.
            for statement in upgrade_rls_sql():
                conn.execute(text(statement))
            for table in _CONNECTOR_RLS_TABLES:
                conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
                conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
                conn.execute(text(f"DROP POLICY IF EXISTS user_isolation ON {table}"))
                conn.execute(
                    text(
                        f"CREATE POLICY user_isolation ON {table} "
                        f"USING (owner_id = {_CUR}) WITH CHECK (owner_id = {_CUR})"
                    )
                )
            if conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'persona_app'")).first():
                conn.execute(text("GRANT USAGE ON SCHEMA public TO persona_app"))
                conn.execute(
                    text(
                        "GRANT SELECT, INSERT, UPDATE, DELETE "
                        "ON ALL TABLES IN SCHEMA public TO persona_app"
                    )
                )
            # Seed the stable tenants ONCE (superuser bypasses RLS): two owners +
            # personas (pa/pa2 for user_a, pb for user_b). These are immutable across
            # tests — the per-test ``_clean`` truncates only the mutable connector +
            # conversation state, NOT users/personas, so the heavy ``TRUNCATE users
            # CASCADE`` (ACCESS EXCLUSIVE on ~20 cascaded tables, the pool-contention
            # source) never runs per test.
            conn.execute(
                text(
                    "INSERT INTO users (id, email) VALUES "
                    "('user_a', 'a@example.com'), ('user_b', 'b@example.com')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO personas (id, owner_id, yaml) VALUES "
                    "('pa', 'user_a', 'name: A'), ('pa2', 'user_a', 'name: A2'), "
                    "('pb', 'user_b', 'name: B')"
                )
            )
    except OperationalError as exc:
        pytest.skip(f"Postgres unreachable at DATABASE_URL: {exc}")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_engine(migrated_engine: Engine) -> Iterator[Engine]:  # noqa: ARG001 — schema first
    """The non-superuser ``persona_app`` RLS engine (skips if APP_DATABASE_URL unset)."""
    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL (non-superuser role) not set; skipping RLS test")
    from persona_api.middleware.rls_context import make_rls_engine

    engine = make_rls_engine(app_url.replace("+asyncpg", "+psycopg"))
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean(migrated_engine: Engine) -> None:
    """Clear only the per-test mutable state before each test (narrow truncate).

    Truncates the connector state + ``conversations`` (``conversations CASCADE``
    pulls ``connector_conversations`` + ``messages``); users + personas are stable
    session seeds and are NOT touched — so the broad, lock-heavy ``TRUNCATE users
    CASCADE`` never runs per test (eliminating the pool-contention flake). Setup-only
    (the test body runs after); committed before the body.
    """
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE conversations, connector_channels, "
                "connector_link_tokens, connector_identities CASCADE"
            )
        )
