"""Long-poll inbound transport (Spec C2 T7, D-C2-1) — the dev receive path.

The zero-infra alternative to the webhook: pull updates with ``getUpdates`` in a
loop (no public HTTPS endpoint needed). Mutually exclusive with the webhook per
bot — the composition root picks one by config. Each consumed update advances the
``offset`` (``last_update_id + 1``) so Telegram drops it from the next batch (the
ack), then is handed to the same injected ``on_update`` handler the webhook uses —
so the inbound flow is transport-agnostic.

api-free: a loop over the injected client + handler; ``should_continue`` is
injected so a test (or a shutdown signal) can stop it deterministically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from persona_connectors.telegram.client import TelegramClient

__all__ = ["run_long_poll"]

_DEFAULT_ALLOWED = ("message",)


def _always() -> bool:
    return True


async def run_long_poll(
    *,
    client: TelegramClient,
    on_update: Callable[[dict[str, object]], Awaitable[None]],
    timeout: int = 30,
    allowed_updates: list[str] | None = None,
    should_continue: Callable[[], bool] = _always,
) -> None:
    """Poll Telegram for updates and dispatch each to ``on_update`` (D-C2-1, dev).

    Args:
        client: The Bot API client.
        on_update: The inbound-update handler (the flow) — the SAME one the webhook
            calls, so the flow never knows which transport delivered the update.
        timeout: The long-poll wait (seconds) passed to ``getUpdates``.
        allowed_updates: Update types to receive (defaults to ``["message"]`` —
            text-message-only v1).
        should_continue: Polled each iteration; return ``False`` to stop the loop
            (a shutdown hook / a test bound).
    """
    allowed = list(allowed_updates) if allowed_updates is not None else list(_DEFAULT_ALLOWED)
    offset: int | None = None
    while should_continue():
        updates = await client.get_updates(offset=offset, timeout=timeout, allowed_updates=allowed)
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int) and not isinstance(update_id, bool):
                offset = update_id + 1  # ack: this update won't be re-delivered
            await on_update(update)
