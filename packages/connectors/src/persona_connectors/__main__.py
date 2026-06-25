"""The connector service entry point (Spec C2 T9) — ``python -m persona_connectors``.

Brings the Telegram adapter to life: assembles the engines + the reused api runtime
(C1-D-1, the ``run_worker.py`` pattern — this module + ``composition`` + ``infra``
are the ONLY ``persona_api`` importers; the flow/domain/telegram surface stays
api-free, the reversibility ideal), registers the Telegram connector as C0's
``MessageDeliverer``, and runs the chosen inbound transport (long-poll for dev, the
zero-infra operator-pass path; webhook for prod) plus the periodic idle sweep.

Deploy seam: the heavy :class:`RuntimeFactory` (embedder / tier-registry / model
backends) is built here from the live environment and is exercised by the live
operator pass, not CI (the same posture as api's own ``@external`` turn tests). The
testable wiring (flow, routing, render, linking, the connector, the persona-name
lister) is unit + integration covered.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import uvicorn
from persona.auth.jwt_verifier import make_jwt_verifier
from persona.logging import get_logger
from persona.stores.chroma import ChromaBackend
from persona.stores.postgres import PostgresBackend
from persona_api.config import APIConfig, Edition
from persona_api.editions.factory import build_credits_policy
from persona_api.services import persona_service
from persona_api.services.runtime_factory import RuntimeFactory
from persona_api.services.turn_log_writer import PostgresTurnLogWriter
from persona_runtime.tier import tier_registry_from_env

from persona_connectors.composition import (
    ConnectorComposition,
    build_delivery_router,
    build_persona_name_lister,
    build_reply_runner,
)
from persona_connectors.config import ConnectorConfig
from persona_connectors.domain.linking import LinkingService
from persona_connectors.domain.resolution import InboundIdentityResolver
from persona_connectors.errors import ConnectorError
from persona_connectors.infra import PostgresConversationStateStore, PostgresLinkStore
from persona_connectors.telegram import (
    InboundFlow,
    TelegramClient,
    TelegramConnector,
    TelegramLinkingService,
    build_telegram_app,
    run_long_poll,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

_log = get_logger("connectors.telegram")
_IDLE_SWEEP_INTERVAL_SECONDS = 300  # run the lazy-expiry backstop every 5 minutes


def _build_runtime_factory(api_config: APIConfig, rls_engine: Engine) -> RuntimeFactory:
    """Build the reused api runtime (mirrors app.py's lifespan, community + cloud).

    The deploy seam — torch (embedder) + model backends load here from the live env.
    Code-execution + image backends are off for the connector v1 (text-to-text).
    """
    embedder = persona_service.default_embedder(api_config.embedder_model)
    if api_config.edition is Edition.community:
        memory_backend: ChromaBackend | PostgresBackend = ChromaBackend(
            persist_path=Path(api_config.community_memory_path), embedder=embedder
        )
    else:
        memory_backend = PostgresBackend(engine=rls_engine, embedder=embedder)
    return RuntimeFactory(
        rls_engine=rls_engine,
        embedder=embedder,
        tier_registry=tier_registry_from_env(),
        turn_log_writer=PostgresTurnLogWriter(rls_engine),
        audit_root=Path(api_config.audit_root),
        workspace_root=Path(api_config.workspace_root),
        api_config=api_config,
        credits_policy=build_credits_policy(api_config),
        memory_backend=memory_backend,
    )


async def _resolve_bot_username(config: ConnectorConfig, client: TelegramClient) -> str:
    """The bot @username for deep links — from config, else ``getMe`` at startup."""
    if config.telegram_bot_username:
        return config.telegram_bot_username
    me = await client.get_me()
    username = me.get("username")
    if not isinstance(username, str) or not username:
        raise ConnectorError("could not resolve the bot username via getMe")
    return username


async def _run_idle_sweep(store: PostgresConversationStateStore, idle_after: timedelta) -> None:
    """Periodically end genuinely-idle conversations (the lazy-expiry backstop, §3)."""
    while True:
        await asyncio.sleep(_IDLE_SWEEP_INTERVAL_SECONDS)
        try:
            ended = store.sweep_idle_conversations(now=datetime.now(UTC), idle_after=idle_after)
            if ended:
                _log.info("idle sweep ended {count} conversation(s)", count=ended)
        except Exception as exc:  # noqa: BLE001 — a sweep fault must not kill the service
            _log.warning("idle sweep failed: {error}", error=str(exc))


async def _amain() -> None:
    config = ConnectorConfig()
    api_config = APIConfig()
    if config.telegram_bot_token is None:
        raise ConnectorError("PERSONA_CONNECTORS_TELEGRAM_BOT_TOKEN is required to run the service")

    composition = ConnectorComposition(config)
    rls_engine = composition.make_engine()
    dispatch_engine = composition.make_dispatch_engine()

    # The reused api runtime + the injected flow callables (owner-scoped).
    runtime_factory = _build_runtime_factory(api_config, rls_engine)
    run_turn = build_reply_runner(
        runtime_factory=runtime_factory, rls_engine=rls_engine, owner_scope=composition.owner_scope
    )
    list_persona_names = build_persona_name_lister(
        rls_engine=rls_engine, owner_scope=composition.owner_scope
    )

    # The Telegram I/O + the C1 stores.
    http = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
    client = TelegramClient(
        bot_token=config.telegram_bot_token,
        http=http,
        api_base_url=config.telegram_api_base_url,
    )
    bot_username = await _resolve_bot_username(config, client)
    link_store = PostgresLinkStore(rls_engine=rls_engine, dispatch_engine=dispatch_engine)
    linking_service = LinkingService(link_store)
    resolver = InboundIdentityResolver(linking_service)
    telegram_linking = TelegramLinkingService(linking=linking_service, bot_username=bot_username)
    conversation_store = PostgresConversationStateStore(
        rls_engine=rls_engine, dispatch_engine=dispatch_engine
    )
    connector = TelegramConnector(
        client=client, conversation_store=conversation_store, owner_scope=composition.owner_scope
    )
    # Register the connector as C0's MessageDeliverer (criterion 8 — originated path).
    build_delivery_router(telegram_deliverer=connector, rls_engine=rls_engine)

    flow = InboundFlow(
        resolver=resolver,
        linking=telegram_linking,
        conversation_store=conversation_store,
        connector=connector,
        client=client,
        list_persona_names=list_persona_names,
        run_turn=run_turn,
        now=lambda: datetime.now(UTC),
    )

    idle_after = timedelta(minutes=config.idle_timeout_minutes)
    sweep = asyncio.create_task(_run_idle_sweep(conversation_store, idle_after))
    try:
        if config.telegram_transport == "webhook":
            await _serve_webhook(config, client, flow, telegram_linking)
        else:
            await client.delete_webhook()  # ensure no webhook competes with long-poll
            _log.info("starting Telegram long-poll as @{username}", username=bot_username)
            await run_long_poll(
                client=client,
                on_update=flow.handle,
                timeout=config.telegram_longpoll_timeout_seconds,
            )
    finally:
        sweep.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweep
        await http.aclose()


async def _serve_webhook(
    config: ConnectorConfig,
    client: TelegramClient,
    flow: InboundFlow,
    telegram_linking: TelegramLinkingService,
) -> None:
    """Register the webhook (with the secret token, D-C2-2) + serve the ASGI app."""
    ttl = timedelta(minutes=config.telegram_link_token_ttl_minutes)

    async def issue_deep_link(owner_id: str) -> str:
        return telegram_linking.issue_deep_link(owner_id=owner_id, now=datetime.now(UTC), ttl=ttl)

    secret = config.telegram_webhook_secret
    await client.set_webhook(
        url=config.telegram_webhook_url,
        secret_token=secret.get_secret_value() if secret is not None else None,
        allowed_updates=["message"],
    )
    app = build_telegram_app(
        webhook_secret=secret,
        on_update=flow.handle,
        issue_deep_link=issue_deep_link,
        verify_jwt=make_jwt_verifier(config),
    )
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="info"))  # noqa: S104 — container-bound service
    _log.info("starting Telegram webhook server")
    await server.serve()


def main() -> None:
    """Run the connector service (the ``python -m persona_connectors`` entry)."""
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
