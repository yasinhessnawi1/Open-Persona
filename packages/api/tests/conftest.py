"""Shared test fixtures for persona-api.

The api test tree has NO ``__init__.py`` (spec-05 finding #1 — avoids a
``tests.conftest`` module-name collision across packages). Shared fakes/fixtures
live here.

Integration tests need a real Postgres 16 + pgvector. They read ``DATABASE_URL``
(sync psycopg3 dialect, ``postgresql+psycopg://``) and skip if it is unset or the
server is unreachable — CI sets it to a Docker service; locally:

    docker compose up -d postgres
    export DATABASE_URL=postgresql+psycopg://persona:persona@localhost:5432/persona
    uv run pytest -m integration
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from sqlalchemy import Engine


@pytest.fixture(autouse=True, scope="session")
def _default_cloud_edition() -> Iterator[None]:
    """Run the existing api suite as the CLOUD edition (Spec 33).

    The pre-Spec-33 behavior — Clerk auth, multi-tenant RLS, metered credits — is
    now the ``cloud`` edition; ``community`` (the product default) has no auth
    wall. The existing suite asserts the cloud behavior, so default every
    ``APIConfig()`` built without an explicit edition to ``cloud`` here (an
    explicit ``edition=`` kwarg still wins — community-specific tests pass it).
    """
    prior = os.environ.get("PERSONA_EDITION")
    os.environ["PERSONA_EDITION"] = "cloud"
    yield
    if prior is None:
        os.environ.pop("PERSONA_EDITION", None)
    else:
        os.environ["PERSONA_EDITION"] = prior


class HashEmbedder384:
    """Deterministic 384-dim L2-normalised embedder for Postgres tests.

    Mirrors ``packages/core/tests/_embedder.py``'s HashEmbedder but at the
    production vector(384) dimension (the core fake is 32-dim, which the
    Postgres column rejects). SHA-256-derived, so the same text always maps to
    the same vector — lets decay/recall tests reason about ranking.
    """

    model_name: str = "test-hash-embedder-384"
    dimension: int = 384

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            # Derive floats from digest BYTES (0..255 → -0.5..0.5). Never NaN
            # (unlike reinterpreting raw bytes as IEEE-754 floats, which can
            # land on NaN/inf bit patterns that pgvector rejects).
            byts: list[int] = []
            counter = 0
            while len(byts) < self.dimension:
                byts.extend(hashlib.sha256(f"{text}:{counter}".encode()).digest())
                counter += 1
            vec = [(b / 255.0) - 0.5 for b in byts[: self.dimension]]
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out


@pytest.fixture
def embedder() -> HashEmbedder384:
    return HashEmbedder384()


@pytest.fixture(scope="session")
def real_embedder() -> object:
    """The production ``bge-small-en-v1.5`` embedder (384-dim), CPU-pinned.

    The :class:`HashEmbedder384` above is a SHA-256 whole-text hash with zero
    semantic structure (two distinct texts → cosine ≈ 0), so it cannot exercise
    any test whose assertion depends on genuine query↔content similarity (e.g.
    K3's cross-persona semantic injection gate, which fires on dense cosine ≥
    ``inject_similarity_floor``). Those tests use THIS fixture: the same embedder
    the runtime ships, so the gate sees production-accurate similarities.

    Session-scoped — the model load is ~3-5s and the weights are immutable, so
    one load is shared across the (few) tests that need real semantics. Pinned to
    ``cpu`` for deterministic, hardware-independent CI scores. Skips (rather than
    errors) if ``sentence-transformers`` is not installed.
    """
    try:
        from persona.stores.embedder import SentenceTransformerEmbedder
    except ImportError:  # pragma: no cover - optional heavy dep
        pytest.skip("sentence-transformers not installed; skipping real-embedder test")
    return SentenceTransformerEmbedder(device="cpu")


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


@pytest.fixture(scope="session")
def database_url() -> str:
    """The sync Postgres DSN, or skip the test if unavailable.

    SAFETY GATE: the integration fixtures that depend on this (``pg_engine``,
    ``migrated_engine``, and every migration test's ``clean_db``) start with
    ``DROP SCHEMA public CASCADE``. ``DATABASE_URL`` is the SAME variable
    ``run-local.sh`` exports for the dev database, so an accidental
    ``pytest -m integration`` in a dev shell would silently wipe the dev schema.
    We therefore refuse to hand out the URL unless the target is provably
    disposable: its database name ends in ``_test``, OR ``PERSONA_TEST_DB=1`` is
    set to explicitly affirm the database is throwaway (CI sets this). Otherwise
    we skip — the destructive fixtures never run against a dev DB.
    """
    from sqlalchemy.engine import make_url

    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL not set; skipping Postgres integration test")
    if "+asyncpg" in url:
        # The transport is sync (D-07-1); coerce a stray async DSN.
        url = url.replace("+asyncpg", "+psycopg")
    db_name = make_url(url).database or ""
    if os.environ.get("PERSONA_TEST_DB") != "1" and not db_name.endswith("_test"):
        pytest.skip(
            f"Refusing to run destructive integration fixtures against database "
            f"{db_name!r}: they DROP SCHEMA public CASCADE. Point DATABASE_URL at a "
            f"database whose name ends in '_test', or set PERSONA_TEST_DB=1 to confirm "
            f"this database is disposable. (Guards against wiping the dev DB.)"
        )
    return url


@pytest.fixture
def pg_engine(database_url: str) -> Iterator[Engine]:
    """A sync SQLAlchemy engine on a freshly-built schema (tables only).

    Drops and recreates all tables (via the Core ``MetaData``) so each test
    starts clean. This builds TABLES but NOT the RLS policies — store/CRUD/decay
    tests don't need RLS (they run as superuser). RLS-isolation tests use
    :func:`migrated_engine` instead, which runs the real Alembic migration so
    the policies exist.
    """
    from persona_api.db import metadata
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import OperationalError

    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            metadata.create_all(conn)
    except OperationalError as exc:
        pytest.skip(f"Postgres unreachable at DATABASE_URL: {exc}")
    yield engine
    engine.dispose()


def _migrate_to_head(database_url: str, *, evict_stale: bool = False) -> None:
    """``DROP SCHEMA public CASCADE`` + ``alembic upgrade head`` + grant persona_app.

    Builds the full schema (tables, indexes, RLS ENABLE/FORCE + policies) and
    grants the non-superuser ``persona_app`` role if it exists. Used once at
    session start by :func:`_session_migrated_db`, and again as a self-repair
    if a migration test (which manipulates the shared schema directly) left it
    away from head.

    ``evict_stale`` (session-start ONLY): before ``DROP SCHEMA``, terminate
    leftover *idle* backends on the test DB. A prior integration run that was
    killed mid-test (e.g. the 120s pytest-timeout, or Ctrl-C) leaves its pooled
    :func:`persona_api.middleware.rls_context.make_rls_engine` connections alive
    on the shared throwaway DB (test fixtures ``yield`` those engines without
    disposing — they only die on GC, which a hard kill skips). Those orphans
    still hold catalog snapshots / object locks, so the next session's
    ``DROP SCHEMA public CASCADE`` + ``alembic upgrade head`` races a stale
    catalog → the ``pg_type_typname_nsp_index`` duplicate-key / partial-schema /
    ``relation … does not exist`` cascade this fixture exists to prevent. Evicting
    only ``idle`` orphans (never ``active`` / ``idle in transaction``) at session
    start — BEFORE this session opens any pool of its own — is safe because no
    in-session connection is idle yet; the only idle backends are the dead run's.

    TRADEOFF / POLICY: two integration suites pointed at ONE test DB at the same
    time is unsupported — the per-test ``TRUNCATE`` in :func:`migrated_engine`
    would corrupt the other run's data regardless of this eviction. Isolate the
    test DB per worktree (each run gets its own ``…_test`` DB). This eviction
    heals the common *sequential* case (a crashed run, then a fresh one); it does
    NOT make concurrent runs correct. ``evict_stale`` defaults False so the
    mid-session self-repair call path never kills this session's own live pools.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url)
    api_dir = Path(__file__).resolve().parents[1]
    try:
        if evict_stale:
            # Fresh connection, run before DROP SCHEMA. ``pg_backend_pid()``
            # excludes this very connection; ``state = 'idle'`` excludes any
            # busy backend (a concurrent run mid-statement / mid-transaction is
            # left untouched — only abandoned, between-checkout orphans go).
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND pid <> pg_backend_pid() AND state = 'idle'"
                    )
                )
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        cfg = Config(str(api_dir / "alembic.ini"))
        cfg.set_main_option("script_location", str(api_dir / "alembic"))
        cfg.set_main_option("sqlalchemy.url", database_url)
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            if conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'persona_app'")).first():
                conn.execute(text("GRANT USAGE ON SCHEMA public TO persona_app"))
                conn.execute(
                    text(
                        "GRANT SELECT, INSERT, UPDATE, DELETE "
                        "ON ALL TABLES IN SCHEMA public TO persona_app"
                    )
                )
    finally:
        engine.dispose()


_HEAD_REVISION: str | None = None


def _alembic_head_revision() -> str:
    """The single head revision id of the alembic migration scripts.

    Memoised for the session (the scripts don't change mid-run). Used by
    :func:`_schema_is_at_head` to detect a DB stamped at an OLDER head — the case
    a structural-only check misses when a new migration lands.
    """
    global _HEAD_REVISION
    if _HEAD_REVISION is None:
        from pathlib import Path

        from alembic.config import Config
        from alembic.script import ScriptDirectory

        api_dir = Path(__file__).resolve().parents[1]
        cfg = Config(str(api_dir / "alembic.ini"))
        cfg.set_main_option("script_location", str(api_dir / "alembic"))
        _HEAD_REVISION = ScriptDirectory.from_config(cfg).get_current_head() or ""
    return _HEAD_REVISION


def _schema_is_at_head(engine: Engine) -> bool:
    """True iff the migrated schema is fully intact (tables + RLS + grants).

    Other shared-schema consumers in the same session can leave the DB short of
    a full migration:

    - the migration up/down tests (``test_migration*.py``) drop + partially
      re-migrate directly, so tables may be missing or at ``base``;
    - the :func:`pg_engine` fixture rebuilds TABLES ONLY (no ``alembic_version``,
      no RLS policies, no ``persona_app`` grants).

    A bare table-existence check would treat a ``pg_engine`` rebuild as "at head"
    and skip the re-migrate, leaving the RLS-isolation tests without policies or
    the ``persona_app`` grants (→ ``InsufficientPrivilege``). So require all of:
    the key table exists, Alembic stamped a revision, the RLS policy is present,
    AND the ``persona_app`` role (when it exists) still holds its schema/table
    grants. Any miss → :func:`migrated_engine` re-migrates before truncating.

    The grant check closes a real bleed: ``test_migration_graph`` ends by running
    a raw ``alembic upgrade head`` on a freshly recreated ``public`` schema (via a
    SEPARATE engine), which rebuilds the tables + RLS but does NOT re-run
    :func:`_migrate_to_head`'s ``GRANT … TO persona_app``. Without the grant probe,
    ``_schema_is_at_head`` returned True (tables + version + policy all present), so
    the self-repair was skipped and the next ``persona_app``-driven RLS test saw
    every table as ``relation … does not exist`` (Postgres masks a missing
    schema-USAGE grant as a missing relation). Re-granting is exactly what
    :func:`_migrate_to_head` does, so failing the head check here triggers it.

    Every probe runs in a SINGLE connection inside one ``to_regclass`` lookup so
    the table-existence test and the row reads observe ONE catalog snapshot.
    Splitting them (``inspect()`` in one transaction, then a ``SELECT ... FROM
    alembic_version`` in another) was a TOCTOU bug: a concurrent ``DROP SCHEMA``
    via a separate engine — exactly what the migration / ``pg_engine`` tests do —
    could vanish ``alembic_version`` between the two, so the unguarded ``SELECT``
    raised ``UndefinedTable`` instead of the function returning "not at head".
    ``to_regclass`` returns NULL (never raises) for a missing relation, which is
    the whole point: a missing table means "not at head", not a crashed fixture.
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        has_chunks = (
            conn.execute(text("SELECT to_regclass('public.memory_chunks')")).scalar() is not None
        )
        has_version_tbl = (
            conn.execute(text("SELECT to_regclass('public.alembic_version')")).scalar() is not None
        )
        if not (has_chunks and has_version_tbl):
            return False
        # The DB must be at the CURRENT head, not merely stamped at SOME revision.
        # Without this, a DB stale at an OLDER head — after a new migration lands
        # (e.g. ``019_task_model`` adds ``tasks``), or after ``test_migration_graph``
        # leaves it sub-head — passes every structural probe below (the old tables,
        # policy, and grants are all present) so the session re-migrate is SKIPPED
        # and the new migration's tables are never created → the new spec's
        # integration tests fail with ``relation … does not exist``.
        rev_row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
        at_head_rev = rev_row is not None and rev_row[0] == _alembic_head_revision()
        has_policy = (
            conn.execute(
                text("SELECT 1 FROM pg_policies WHERE tablename = 'memory_chunks'")
            ).first()
            is not None
        )
        # Grants: only meaningful when the non-superuser role exists. If it does,
        # require BOTH schema USAGE and SELECT on a representative migrated table —
        # a grant-less rebuild (test_migration_graph's raw upgrade) trips this and
        # forces a re-migrate (which re-grants), so persona_app RLS tests don't see
        # the post-rebuild tables as "does not exist".
        role_exists = (
            conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'persona_app'")).first()
            is not None
        )
        has_grants = True
        if role_exists:
            has_grants = bool(
                conn.execute(
                    text(
                        "SELECT has_schema_privilege('persona_app', 'public', 'USAGE') "
                        "AND has_table_privilege('persona_app', 'public.personas', 'SELECT')"
                    )
                ).scalar()
            )
    return at_head_rev and has_policy and has_grants


def _truncate_all_data(engine: Engine) -> None:
    """TRUNCATE every data table (RESTART IDENTITY CASCADE), keep the schema.

    Per-test data reset that PRESERVES the schema + RLS policies migrated once
    at session start. TRUNCATE (not transaction-rollback) because the app under
    test opens its OWN pooled connections (``make_rls_engine``) — a rollback in
    the test's connection would not undo the app's committed writes. Runs as the
    superuser engine (``persona_app`` cannot truncate). ``alembic_version`` is
    preserved so the schema stays at head across tests.
    """
    from sqlalchemy import inspect, text

    tables = [t for t in inspect(engine).get_table_names() if t != "alembic_version"]
    if not tables:
        return
    quoted = ", ".join(f'"{t}"' for t in tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def _session_migrated_db(database_url: str) -> Iterator[Engine]:
    """Migrate the throwaway DB to head ONCE per test session.

    The schema + RLS policies are built a single time here; per-test isolation
    is then achieved by TRUNCATE in :func:`migrated_engine` rather than by
    rebuilding the schema each test. Rebuilding per test raced the app's pooled
    RLS connections (``DROP SCHEMA public CASCADE`` while a pooled connection
    still referenced the dropped tables → sporadic ``pg_type`` duplicate-key /
    ``relation ... does not exist`` setup errors on serial local runs).
    """
    from sqlalchemy import create_engine
    from sqlalchemy.exc import OperationalError

    try:
        # Session start: evict idle orphan backends left by a crashed/killed
        # prior run on this shared throwaway DB before the rebuild (see
        # _migrate_to_head's evict_stale docstring). Safe here only — no
        # in-session pool exists yet. The mid-session self-repair call in
        # migrated_engine deliberately omits it (would kill this run's pools).
        _migrate_to_head(database_url, evict_stale=True)
    except OperationalError as exc:
        pytest.skip(f"Postgres unreachable at DATABASE_URL: {exc}")
    engine = create_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def migrated_engine(_session_migrated_db: Engine, database_url: str) -> Engine:
    """A superuser engine on the session-migrated schema, cleaned per test.

    Returns the session-scoped migrated engine (schema + RLS policies built once
    by :func:`_session_migrated_db`) after TRUNCATE-ing all data tables, so each
    test starts with an empty-but-fully-migrated DB. This is the drop-in
    replacement for the former per-test ``DROP SCHEMA`` + ``alembic upgrade``
    fixture — same interface, no per-test schema-rebuild race.

    Self-repair: if a migration up/down test (``test_migration*.py``, which
    manipulates the shared schema directly via ``database_url``) left the schema
    away from head, re-migrate before truncating so the next consumer still sees
    a head schema.

    Stale-connection guard: migration/``pg_engine`` tests ``DROP SCHEMA`` via a
    SEPARATE engine, which silently invalidates this session engine's pooled
    connections (they still reference the dropped relations' OIDs → spurious
    ``relation ... does not exist`` on the next query, even though the schema is
    in fact rebuilt). So dispose the pool first: every check / truncate / test
    query below then runs on a fresh connection that sees the current catalog.
    """
    engine = _session_migrated_db
    engine.dispose()
    if not _schema_is_at_head(engine):
        _migrate_to_head(database_url)
    _truncate_all_data(engine)
    return engine
