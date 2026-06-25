"""The assembled Telegram flow, end-to-end on real PG (Spec C2 T9 — the automated leg).

The agent-runnable half of the live leg: a real inbound Telegram update drives the
WHOLE assembled flow against **real Postgres** (identity resolution on the dispatch
engine, persona listing + foreground under RLS) and a **faithful Bot API stub**
(httpx MockTransport), with only ``run_turn`` stubbed (no LLM key needed). Proves
inbound → resolve → route → foreground → (turn) → render → send, AND that ownership
holds over the platform: an unlinked sender gets a link-instruction and zero access.

The real-Telegram round-trip (real bot token via long-poll) is the user-run operator
pass (see the close-out runbook); this is the CI-automatable proof.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING

import httpx
import pytest
from persona_api.middleware.rls_context import current_user_id
from persona_connectors.composition import build_persona_name_lister
from persona_connectors.domain.linking import LinkingService
from persona_connectors.domain.resolution import InboundIdentityResolver
from persona_connectors.infra import PostgresConversationStateStore, PostgresLinkStore
from persona_connectors.telegram.client import TelegramClient
from persona_connectors.telegram.connector import TelegramConnector
from persona_connectors.telegram.flow import InboundFlow, TurnRequest
from persona_connectors.telegram.linking import TelegramLinkingService
from pydantic import SecretStr
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

_ASTRID_YAML = "identity:\n  name: Astrid\n  role: companion\n  background: helpful"


@contextlib.contextmanager
def _owner_scope(owner_id: str) -> Iterator[None]:
    token = current_user_id.set(owner_id)
    try:
        yield
    finally:
        current_user_id.reset(token)


def _recording_client(records: list[tuple[str, dict[str, object]]]) -> TelegramClient:
    def handler(request: httpx.Request) -> httpx.Response:
        method = str(request.url).rsplit("/", 1)[-1]
        body = json.loads(request.content) if request.content else {}
        records.append((method, body))
        if method == "sendMessage":
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(200, json={"ok": True, "result": True})

    transport = httpx.MockTransport(handler)
    return TelegramClient(
        bot_token=SecretStr("123:abc"), http=httpx.AsyncClient(transport=transport)
    )


def _assemble_flow(
    *,
    app_engine: Engine,
    dispatch_engine: Engine,
    records: list[tuple[str, dict[str, object]]],
    reply: str = "Hi, I'm here.",
) -> InboundFlow:
    client = _recording_client(records)
    link_store = PostgresLinkStore(rls_engine=app_engine, dispatch_engine=dispatch_engine)
    linking = LinkingService(link_store)
    conversation_store = PostgresConversationStateStore(
        rls_engine=app_engine, dispatch_engine=dispatch_engine
    )
    connector = TelegramConnector(
        client=client, conversation_store=conversation_store, owner_scope=_owner_scope
    )

    async def run_turn(_request: TurnRequest) -> str:
        await asyncio.sleep(0)  # yield so the typing-indicator task fires (a real turn awaits)
        return reply

    return InboundFlow(
        resolver=InboundIdentityResolver(linking),
        linking=TelegramLinkingService(linking=linking, bot_username="bot"),
        conversation_store=conversation_store,
        connector=connector,
        client=client,
        list_persona_names=build_persona_name_lister(
            rls_engine=app_engine, owner_scope=_owner_scope
        ),
        run_turn=run_turn,
        now=lambda: _now(),
    )


def _now() -> object:
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _seed_astrid_and_link(engine: Engine, *, sender_id: str) -> None:
    """Give pa a real identity YAML + bind a telegram sender to user_a (BYPASSRLS)."""
    with engine.begin() as conn:
        conn.execute(text("UPDATE personas SET yaml = :y WHERE id = 'pa'"), {"y": _ASTRID_YAML})
        conn.execute(
            text(
                "INSERT INTO connector_identities "
                "(platform, platform_identity, owner_id, status, linked_at) "
                "VALUES ('telegram', :sid, 'user_a', 'active', now())"
            ),
            {"sid": sender_id},
        )


def _inbound(text_body: str, *, sender_id: str, chat_id: str) -> dict[str, object]:
    # sender_id is the numeric Telegram user id (normalise produces str(from.id),
    # which must equal the bound connector_identities.platform_identity).
    return {
        "update_id": 1,
        "message": {
            "message_id": 7,
            "from": {"id": int(sender_id)},
            "chat": {"id": int(chat_id), "type": "private"},
            "date": 1_780_000_000,
            "text": text_body,
        },
    }


@pytest.mark.asyncio
async def test_linked_inbound_drives_a_persona_reply_to_telegram(
    app_engine: Engine, migrated_engine: Engine
) -> None:
    """A linked sender addressing Astrid → the persona reply is sent to their chat."""
    _seed_astrid_and_link(migrated_engine, sender_id="999")
    records: list[tuple[str, dict[str, object]]] = []
    flow = _assemble_flow(app_engine=app_engine, dispatch_engine=migrated_engine, records=records)

    await flow.handle(_inbound("Astrid, hello", sender_id="999", chat_id="12345"))

    sends = [body for method, body in records if method == "sendMessage"]
    assert len(sends) == 1
    assert sends[0]["chat_id"] == "12345"
    assert "<b>Astrid</b>" in str(sends[0]["text"])  # the name tag rendered
    assert "Hi, I'm here." in str(sends[0]["text"])
    assert sends[0]["parse_mode"] == "HTML"
    # The typing indicator fired while the turn ran (D-C2-4).
    assert any(method == "sendChatAction" for method, _ in records)

    # The conversation state was really persisted (foreground created the slot).
    with _owner_scope("user_a"), app_engine.begin() as conn:
        active = conn.execute(
            text(
                "SELECT active_persona_id FROM connector_channels "
                "WHERE owner_id='user_a' AND platform='telegram' AND channel_key='12345'"
            )
        ).scalar()
    assert active == "pa"


@pytest.mark.asyncio
async def test_unlinked_inbound_gets_link_instruction_zero_access(
    app_engine: Engine, migrated_engine: Engine
) -> None:
    """Ownership over the platform: an unlinked sender reaches NO persona (criterion 9)."""
    records: list[tuple[str, dict[str, object]]] = []
    flow = _assemble_flow(app_engine=app_engine, dispatch_engine=migrated_engine, records=records)

    await flow.handle(_inbound("Astrid, hello", sender_id="888", chat_id="66666"))

    sends = [body for method, body in records if method == "sendMessage"]
    assert len(sends) == 1
    assert "link" in str(sends[0]["text"]).lower()  # the link-instruction, not a persona reply
    assert "<b>" not in str(sends[0]["text"])  # no persona name tag → no persona was reached

    # No conversation/channel state was created for the intruder.
    with migrated_engine.begin() as conn:
        channels = conn.execute(
            text("SELECT count(*) FROM connector_channels WHERE channel_key='66666'")
        ).scalar()
    assert channels == 0
