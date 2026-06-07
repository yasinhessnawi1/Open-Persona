"""FastAPI application factory (spec 08, T01).

``create_app()`` is the composition root's entry point. It assembles the app,
registers exception handlers (T02), routers (T07+), middleware, and a lifespan
context that owns the long-lived collaborators — the database engine(s), the
``TierRegistry``, and the toolbox/MCP clients (T10 fills the body; on shutdown it
calls ``await tier_registry.aclose()`` + ``await client.disconnect()`` per the
spec-05/06 lifecycle handoff).

The app is stateless (twelve-factor): all persistent state lives in Postgres.
Single uvicorn worker for v0.1 (S08-4 / D-08-5) — the in-memory run event bus
requires it.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from persona.imagegen import (
    ImageBackend,
    ImageBackendConfig,
    ImageGenUnavailableError,
    load_image_backend,
)
from persona.logging import get_logger
from persona.stores.document_store import DocumentStore
from persona.stores.postgres import PostgresBackend
from persona_runtime.errors import TierNotConfiguredError
from persona_runtime.tier import tier_registry_from_env

from persona_api.background.run_worker import RunRegistry
from persona_api.config import APIConfig
from persona_api.db.engine import create_db_engine
from persona_api.errors import register_exception_handlers
from persona_api.middleware.rate_limit import (
    InMemoryRateLimitStore,
    PostgresRateLimitStore,
    RateLimiter,
    RateLimitStore,
)
from persona_api.middleware.rls_context import make_rls_engine
from persona_api.routes import (
    conversations,
    documents,
    health,
    imagegen,
    me,
    personas,
    runs,
    tools,
    uploads,
)
from persona_api.sandbox import HostedSandbox, SandboxPool, SandboxPoolConfig
from persona_api.services import persona_service
from persona_api.services.runtime_factory import RuntimeFactory
from persona_api.services.turn_log_writer import PostgresTurnLogWriter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy import Engine

__all__ = ["create_app"]


_LOG = get_logger("api.app")


def _compose_image_backend() -> ImageBackend | None:
    """Build the image-generation backend at startup (spec 15 T16).

    Reads ``PERSONA_IMAGEGEN_*`` env vars via
    :class:`persona.imagegen.ImageBackendConfig`. When ``api_key`` is
    ``None`` (the env var is unset) we return ``None`` and log a warning
    rather than raising — dev environments without an image provider
    boot cleanly; the route raises
    :class:`persona.imagegen.ImageGenUnavailableError` → 503 on a request
    that needs the backend, same fail-loud pattern as the Spec 12
    sandbox-pool absence (D-12-5).

    Returns ``None`` when no provider is configured OR when the concrete
    backend constructor itself raises
    :class:`ImageGenUnavailableError` at construction time (auth check
    happens then per D-15-X-construction-time-fail-fast; we let the
    deployment boot regardless so chat / runs / authoring routes stay
    available).
    """
    config = ImageBackendConfig.from_env(prefix="PERSONA_IMAGEGEN_")
    if config.api_key is None:
        _LOG.warning(
            "PERSONA_IMAGEGEN_API_KEY not set — image generation will return 503"
            " (provider={provider} model={model})",
            provider=config.provider,
            model=config.model,
        )
        return None
    try:
        return load_image_backend(config)
    except ImageGenUnavailableError as exc:
        _LOG.warning(
            "image backend construction failed; image generation will return 503"
            " (provider={provider} model={model} reason={reason})",
            provider=config.provider,
            model=config.model,
            reason=str(exc),
        )
        return None


def _e2b_api_key_present() -> bool:
    """Whether ``E2B_API_KEY`` is set in the environment.

    The pool is wired only when E2B is reachable (D-12-12 substrate). Dev
    environments without an E2B account boot cleanly without a hosted pool;
    the ``code_execution`` tool surfaces ``SandboxUnavailableError`` to the
    model (D-12-5 no degraded fallback).
    """
    import os

    return bool(os.environ.get("E2B_API_KEY", "").strip())


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown for the long-lived collaborators.

    T05 builds the **RLS engine** here — the per-request engine whose pool
    listener structurally scopes every connection (D-08-1); it is the spine the
    auth/route DB access composes through. T10 extends this with the embedder,
    typed stores, and the ``TierRegistry`` (and tears them down — calling
    ``await tier_registry.aclose()`` + ``await client.disconnect()`` per MCP
    client, D-05-4 / spec-06 handoff).
    """
    config: APIConfig = app.state.config
    rls_engine: Engine | None = None
    if config.effective_app_database_url:
        rls_engine = make_rls_engine(
            config.effective_app_database_url, pool_size=config.db_pool_size
        )
    app.state.rls_engine = rls_engine
    # Superuser engine for JIT user provisioning (spec-09 integration): a freshly
    # authenticated Clerk user has no `users` row (webhook mirroring deferred,
    # spec 08), yet everything FKs users.id. The auth dep upserts it via this
    # RLS-bypassing engine. None when no superuser DSN is set.
    admin_engine: Engine | None = (
        create_db_engine(config.database_url) if config.database_url else None
    )
    app.state.admin_engine = admin_engine
    # The embedder for persona memory population (D-08-8). Lazy: weights load on
    # first encode, not at startup. Shared (thread-safe read path).
    app.state.embedder = persona_service.default_embedder(config.embedder_model)
    app.state.audit_root = Path(config.audit_root)
    # Spec 13 D-13-4: workspace root for image uploads + (later) per-persona
    # tool artefacts. Resolved up front so routes/services can rely on it.
    app.state.workspace_root = Path(config.workspace_root)
    app.state.workspace_root.mkdir(parents=True, exist_ok=True)
    # Rate limiter (§6, D-08-5). Postgres-backed when a non-RLS engine is
    # available; in-memory otherwise. The buckets table is NOT under RLS, so the
    # Postgres store uses a plain (non-listener) engine.
    app.state.rate_limiter = _build_rate_limiter(config, rls_engine)

    # F3 — DocumentStore builder (Spec 14 T17/T18). Per-request callable so
    # routes/uploads.py + routes/documents.py + routes/conversations.py
    # (cascade-delete) can construct a DocumentStore bound to the RLS-scoped
    # engine. Cross-tenant access is structurally blocked by D-08-1's pool
    # listener; the builder is just the composition wiring. None when no DB
    # engine is configured (test paths override `app.state.build_document_store`
    # with an in-memory fake; see routes/documents.py).
    if rls_engine is not None:
        _document_backend = PostgresBackend(engine=rls_engine, embedder=app.state.embedder)
        app.state.build_document_store = lambda: DocumentStore(backend=_document_backend)
    # Sibling alias for routes/documents.py which reads `sandbox_root` (legacy
    # name from the spec-03 sandbox-path resolver). Same value as
    # `workspace_root`; keeping the alias avoids a route rename ripple.
    app.state.sandbox_root = app.state.workspace_root
    # The agentic-run registry (T11, D-08-5): in-process event bus + task tracker.
    # Single worker (S08-4). Cancelled on shutdown.
    run_registry = RunRegistry(rls_engine) if rls_engine is not None else None
    app.state.run_registry = run_registry

    # Hosted sandbox + pool (spec 12 T08/T09; D-12-12/D-12-17). Built BEFORE
    # the runtime factory so the factory can compose the ``code_execution``
    # tool when the pool is present. Gated on E2B_API_KEY — dev environments
    # without an E2B account boot cleanly; the tool is absent in that case
    # and the model would surface ``SandboxUnavailableError`` if it tried
    # (D-12-5 no degraded fallback).
    sandbox_pool: SandboxPool | None = None
    if _e2b_api_key_present():
        pool_cfg = SandboxPoolConfig()
        sandbox_pool = SandboxPool(
            sandbox=HostedSandbox(),  # SDK reads E2B_API_KEY from env (D-12-12)
            max_per_user=pool_cfg.max_per_user,
            idle_timeout_s=pool_cfg.idle_timeout_s,
            reap_interval_s=pool_cfg.reap_interval_s,
        )
        await sandbox_pool.start()  # spawns the pool-owned background reaper
    app.state.sandbox_pool = sandbox_pool

    # Image-generation backend (spec 15 T16). Composed at startup so the
    # route layer can dispatch through ``app.state.image_backend`` without
    # re-reading env vars per request. ``None`` when no provider is
    # configured — the route raises ``ImageGenUnavailableError`` → 503
    # in that case (same dev-friendly graceful-absence shape as the
    # E2B-less sandbox pool).
    app.state.image_backend = _compose_image_backend()

    # Runtime composition root (T10): the TierRegistry (app-scoped) + the
    # per-request loop builders. Built only when a model backend is configured
    # AND a DB engine exists; tests override app.state.build_conversation_loop
    # with a scripted loop, so a missing registry doesn't block them.
    runtime_factory: RuntimeFactory | None = None
    if rls_engine is not None:
        try:
            tier_registry = tier_registry_from_env()
        except TierNotConfiguredError:
            tier_registry = None
        if tier_registry is not None:
            runtime_factory = RuntimeFactory(
                rls_engine=rls_engine,
                embedder=app.state.embedder,
                tier_registry=tier_registry,
                # Postgres turn_logs (D-08-7); RLS-scoped via conversations.
                turn_log_writer=PostgresTurnLogWriter(rls_engine),
                audit_root=Path(config.audit_root),
                # Spec 12 T10: pass the hosted sandbox pool (may be None when
                # E2B_API_KEY is unset; factory absents code_execution in that case).
                sandbox_pool=sandbox_pool,
                # Spec 17 D-17-X-bytes-persistence: thread the workspace root
                # so the code_execution tool persists produced files into the
                # served persona workspace + stages intermediate/* cross-turn.
                workspace_root=app.state.workspace_root,
            )
            app.state.tier_registry = tier_registry
            app.state.authoring_tier = config.authoring_tier
            app.state.build_conversation_loop = runtime_factory.build_conversation_loop
            app.state.build_agentic_loop = runtime_factory.build_agentic_loop
            app.state.title_builder = runtime_factory.build_title  # auto-title (small tier)

    try:
        yield
    finally:
        if run_registry is not None:
            await run_registry.aclose()  # cancel in-flight run tasks (S08-2)
        if runtime_factory is not None:
            await runtime_factory.aclose()  # tier_registry.aclose() + MCP disconnect (D-05-4)
        if sandbox_pool is not None:
            # Cancels the reaper, drains sessions, closes substrate
            # (D-12-12 Gate 4 mid-exec-kill cleanliness inherits).
            await sandbox_pool.aclose()
        if admin_engine is not None:
            admin_engine.dispose()
        if rls_engine is not None:
            rls_engine.dispose()


def create_app(config: APIConfig | None = None) -> FastAPI:
    """Build and return the FastAPI application.

    Args:
        config: An :class:`APIConfig`. Defaults to one loaded from the
            environment. Injected in tests to override DSNs/auth knobs.
    """
    config = config or APIConfig()

    app = FastAPI(
        title="Persona API",
        version="0.8.0",
        summary="Hosted service for building and running typed-memory AI personas.",
        lifespan=_lifespan,
    )
    app.state.config = config

    register_exception_handlers(app)

    # CORS for the spec-09 web app (browser → API is cross-origin). Bearer auth
    # (no cookies) → allow_credentials=False; expose the rate-limit headers so the
    # browser client can read them. Origins from PERSONA_API_CORS_ORIGINS.
    if config.cors_origins_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_origins_list,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=[
                "X-RateLimit-Limit",
                "X-RateLimit-Remaining",
                "X-RateLimit-Reset",
                "Retry-After",
            ],
        )

    # Routers are registered as they land (T07 personas, T08 conversations,
    # T11 runs, T12 me/health, T13 tools). Kept as an explicit include list so
    # the surface is visible in one place.
    _register_routers(app)

    return app


def _build_rate_limiter(config: APIConfig, rls_engine: Engine | None) -> RateLimiter:
    """Build the §6 limiter: Postgres-backed when an engine exists + configured,
    else in-memory (dev/tests)."""
    per_endpoint = {
        "messages": config.rate_limit_messages,
        "runs": config.rate_limit_runs,
        "author": config.rate_limit_author,
    }
    store: RateLimitStore
    if config.rate_limit_backend == "postgres" and rls_engine is not None:
        store = PostgresRateLimitStore(rls_engine)
    else:
        store = InMemoryRateLimitStore()
    return RateLimiter(store, default_limit=config.rate_limit_default, per_endpoint=per_endpoint)


def _register_routers(app: FastAPI) -> None:
    """Include all route modules. Extended task-by-task in Phase 5."""
    app.include_router(personas.router)
    app.include_router(conversations.router)
    app.include_router(runs.router)
    app.include_router(me.router)
    app.include_router(health.router)
    app.include_router(tools.router)
    app.include_router(documents.router)
    app.include_router(uploads.router)
    app.include_router(imagegen.router)
