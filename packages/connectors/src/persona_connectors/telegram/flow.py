"""The Telegram inbound flow orchestrator (Spec C2 flow) — the I/O, not the decision.

This wires one inbound Telegram update through the framework: classify → (/start
redeem | resolve identity | /new | route | run the turn) → render → send. The
**routing decision is C1's** (:func:`~persona_connectors.domain.routing.decide_route`
over :func:`~persona_connectors.domain.addressing.parse_addressed_persona` +
:meth:`~persona_connectors.domain.conversation_model.ConversationStateStore.current_foreground`)
— this module supplies only the **I/O**: extracting the text, the no-streaming
typing loop (D-C2-4), rendering, and sending. So C3–C5 inherit the same decision
tree and reimplement only their own transport.

The no-streaming pattern (§3, D-C2-4): the persona reply is *collected to completion*
by the injected ``run_turn`` (which drives ``ConversationLoop.turn`` under the
owner scope — wired by the composition root) while a "typing…" chat action refreshes;
then it's rendered (HTML bold tag + UTF-16 split) and sent whole via the connector.

**api-free** (the reversibility ideal): the api-coupled bits — running the turn and
listing the owner's personas — are injected callables the composition root supplies;
this module imports no ``persona_api``. Ownership holds exactly as on the web: an
unlinked identity gets a link-instruction and ZERO access (the C1 resolution gate);
a resolved owner only ever touches their own personas (the injected callables +
the store run RLS-scoped to that owner).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from persona.schema.origination import PersonaIdentityTag
from pydantic import BaseModel, ConfigDict

from persona_connectors.domain.addressing import parse_addressed_persona
from persona_connectors.domain.normalise import NormalisedOutbound
from persona_connectors.domain.resolution import UnlinkedIdentity
from persona_connectors.domain.routing import ListAndInstructions, decide_route
from persona_connectors.telegram.inbound import (
    PLATFORM,
    InboundIgnore,
    InboundNonText,
    classify_update,
)
from persona_connectors.telegram.linking import RedeemStatus
from persona_connectors.telegram.non_text import decline_message
from persona_connectors.telegram.replies import (
    NEW_CONVERSATION_MESSAGE,
    NO_ACTIVE_TO_RESET_MESSAGE,
    NO_PERSONAS_MESSAGE,
    render_list_and_instructions,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
    from datetime import datetime

    from persona_connectors.domain.conversation_model import ConversationStateStore
    from persona_connectors.domain.normalise import NormalisedInbound
    from persona_connectors.domain.resolution import InboundIdentityResolver
    from persona_connectors.telegram.client import TelegramClient
    from persona_connectors.telegram.connector import TelegramConnector
    from persona_connectors.telegram.linking import TelegramLinkingService

__all__ = ["InboundFlow", "TurnRequest"]

# The typing chat action lasts ~5s, so refresh just under that while the turn runs.
_TYPING_REFRESH_SECONDS = 4.0


class TurnRequest(BaseModel):
    """What the injected turn-runner needs to drive one persona turn (collected whole)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_id: str
    conversation_id: str
    persona_id: str
    text: str


def _leading_command(text: str) -> str | None:
    """The leading bot command (``/new`` / ``/start``), ``@bot`` suffix stripped, else None."""
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None
    base = parts[0].split("@", 1)[0]
    return base if base in ("/new", "/start") else None


@contextlib.asynccontextmanager
async def _typing_indicator(client: TelegramClient, chat_id: str) -> AsyncIterator[None]:
    """Show + refresh the "typing…" chat action while the body runs (D-C2-4).

    Sends the action immediately (instant responsiveness) and re-sends every ~4s
    (the status auto-clears at ~5s) until the context exits; the final reply send
    clears it. The refresh task is always cancelled on exit.
    """

    async def _refresh() -> None:
        while True:
            await client.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(_TYPING_REFRESH_SECONDS)

    task = asyncio.create_task(_refresh())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class InboundFlow:
    """Orchestrates one inbound Telegram update through the C1 framework (the I/O half).

    All dependencies are injected (DI; no globals). The api-coupled callables
    (``run_turn`` drives ``ConversationLoop.turn``; ``list_persona_names`` reads the
    owner's personas) are owner-scoped by the composition root, keeping this module
    api-free.
    """

    def __init__(
        self,
        *,
        resolver: InboundIdentityResolver,
        linking: TelegramLinkingService,
        conversation_store: ConversationStateStore,
        connector: TelegramConnector,
        client: TelegramClient,
        list_persona_names: Callable[[str], Mapping[str, Sequence[str]]],
        run_turn: Callable[[TurnRequest], Awaitable[str]],
        now: Callable[[], datetime],
    ) -> None:
        self._resolver = resolver
        self._linking = linking
        self._store = conversation_store
        self._connector = connector
        self._client = client
        self._list_persona_names = list_persona_names
        self._run_turn = run_turn
        self._now = now

    async def handle(self, update: dict[str, object]) -> None:
        """Handle one raw Telegram ``Update`` (the transport's ``on_update`` callback)."""
        outcome = classify_update(update, now=self._now())
        if isinstance(outcome, InboundIgnore):
            return
        if isinstance(outcome, InboundNonText):
            # Non-text → a friendly text-only decline (D-C2-6); no runtime turn.
            await self._client.send_message(
                chat_id=outcome.conversation_key, text=decline_message(outcome.kind)
            )
            return
        await self._handle_text(outcome.inbound)

    async def _handle_text(self, inbound: NormalisedInbound) -> None:
        chat = inbound.conversation_key

        # 1. Account linking: /start <token> redeems + binds (or fails closed). A
        #    bare /start (no token) is handled later (greeting), once linked.
        redeem = self._linking.redeem_start_command(
            text=inbound.text, platform_identity=inbound.sender_id, now=self._now()
        )
        if redeem.status in (RedeemStatus.linked, RedeemStatus.failed):
            await self._client.send_message(chat_id=chat, text=redeem.message or "")
            return

        # 2. Ownership gate (C1): resolve the identity to its owner, or zero access.
        resolution = self._resolver.resolve(inbound)
        if isinstance(resolution, UnlinkedIdentity):
            await self._client.send_message(chat_id=chat, text=resolution.instruction)
            return
        owner_id = resolution.owner_id

        # 3. The owner's personas (RLS-scoped read via the injected lister).
        names = self._list_persona_names(owner_id)
        if not names:
            await self._client.send_message(chat_id=chat, text=NO_PERSONAS_MESSAGE)
            return

        # 4. Boundary / greeting commands.
        command = _leading_command(inbound.text)
        if command == "/new":
            new_conversation = self._store.apply_new(
                owner_id=owner_id, platform=PLATFORM, channel_key=chat
            )
            message = (
                NEW_CONVERSATION_MESSAGE
                if new_conversation is not None
                else NO_ACTIVE_TO_RESET_MESSAGE
            )
            await self._client.send_message(chat_id=chat, text=message)
            return
        if command == "/start":  # bare /start (the token case already returned above)
            await self._client.send_message(chat_id=chat, text=render_list_and_instructions(names))
            return

        # 5. Route (C1's decision) → drive a persona, or list-and-instructions.
        addressing = parse_addressed_persona(inbound.text, persona_names=names)
        active = self._store.current_foreground(
            owner_id=owner_id, platform=PLATFORM, channel_key=chat
        )
        decision = decide_route(
            addressing,
            active_persona_id=active.persona_id if active is not None else None,
            owner_persona_ids=list(names),
        )
        if isinstance(decision, ListAndInstructions):
            await self._client.send_message(chat_id=chat, text=render_list_and_instructions(names))
            return

        # 6. Drive the turn: foreground (flip-or-continue) → collect the reply with a
        #    typing indicator (no-streaming) → render + send whole.
        foreground = self._store.foreground(
            owner_id=owner_id, platform=PLATFORM, channel_key=chat, persona_id=decision.persona_id
        )
        addressable = names.get(decision.persona_id)
        display_name = addressable[0] if addressable else decision.persona_id
        tag = PersonaIdentityTag(
            persona_id=decision.persona_id, display_name=display_name, visual_ref=None
        )
        async with _typing_indicator(self._client, chat):
            reply = await self._run_turn(
                TurnRequest(
                    owner_id=owner_id,
                    conversation_id=foreground.conversation_id,
                    persona_id=decision.persona_id,
                    text=inbound.text,
                )
            )
        await self._connector.send(
            NormalisedOutbound(persona=tag, text=reply, conversation_key=chat)
        )
