"""Delivery routing for originated messages (Spec C0, T6, D-C0-2).

:class:`DeliveryRouter` is the one-pipe-many-deliverers facade: it holds a registry
of :class:`~persona.delivery.MessageDeliverer` channels and, given an originated
message, picks **exactly one** target channel and dispatches to it. It is itself a
``MessageDeliverer`` — the composite the ``Originator`` (T3) depends on, so direction
4 later drives *this one boundary*, never N channel-specific paths.

The v1 default policy (D-C0-2): route to the channel the relevant conversation lives
on, with the **web app as the always-available home**. Two guarantees hold *by
construction*:

* **No double-delivery** — a single channel key is resolved, one registry lookup is
  done, one ``deliver`` is awaited. There is no fan-out.
* **No silent drop** — the home channel must be registered (a fail-fast construction
  guard), and an unresolved/unknown channel key falls back to it, so there is always
  a target. (Undeliverable-right-now is the deliverer's ``pending`` outcome, D-C0-4 —
  not a drop.)

Channel selection is an **injected resolver + dict lookup** — never an ``if
platform == …`` switch (D-08-3: the API treats the connector ``channel`` as opaque).
In v1 only the web channel exists, so the resolver returns the home; C1 supplies a
resolver that maps the conversation's channel descriptor to a registered channel key
(still a lookup, still no platform branching). Routing decision + outcome are tracked
at this layer via the api audit log (D-C0-5), distinct from the deliverer's
channel-level outcome record.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona_api.services import audit_service

if TYPE_CHECKING:
    from collections.abc import Callable

    from persona.delivery import DeliveryResult, MessageDeliverer
    from persona.schema.origination import OriginatedMessage
    from sqlalchemy import Engine

_DEFAULT_HOME = "web"


def _home_only(message: OriginatedMessage) -> str | None:  # noqa: ARG001 — v1 resolver
    """v1 channel resolver: always the home channel (only the web channel exists)."""
    return None


class DeliveryRouter:
    """Route an originated message to exactly one registered deliverer (D-C0-2).

    Args:
        deliverers: Channel-key → :class:`MessageDeliverer`. Must include
            ``home_channel``.
        rls_engine: The engine the routing audit write runs on.
        home_channel: The always-available fallback channel (default ``"web"``).
        resolve_channel: Picks the channel key for a message (``None`` → home). v1
            default is home-only; C1 injects a conversation-channel resolver.
        record: The audit sink (injected for testability; defaults to the api audit
            service).

    Raises:
        ValueError: If ``home_channel`` is not in ``deliverers`` — a fail-fast guard
            so routing always has a target (no silent drop).
    """

    def __init__(
        self,
        *,
        deliverers: dict[str, MessageDeliverer],
        rls_engine: Engine,
        home_channel: str = _DEFAULT_HOME,
        resolve_channel: Callable[[OriginatedMessage], str | None] = _home_only,
        record: Callable[..., None] = audit_service.record,
    ) -> None:
        if home_channel not in deliverers:
            msg = (
                f"home_channel {home_channel!r} has no registered deliverer; "
                f"registered: {sorted(deliverers)}"
            )
            raise ValueError(msg)
        self._deliverers = dict(deliverers)
        self._engine = rls_engine
        self._home = home_channel
        self._resolve = resolve_channel
        self._record = record

    async def deliver(self, message: OriginatedMessage) -> DeliveryResult:
        """Resolve one channel, dispatch once, track the routing decision + outcome."""
        key = self._resolve(message)
        channel = key if (key is not None and key in self._deliverers) else self._home
        deliverer = self._deliverers[channel]  # exactly one target — no fan-out
        result = await deliverer.deliver(message)
        self._audit_routing(message, channel, result)
        return result

    def _audit_routing(
        self, message: OriginatedMessage, channel: str, result: DeliveryResult
    ) -> None:
        self._record(
            engine=self._engine,
            user_id=message.owner_user_id,
            action="origination.routing",
            target=message.conversation_id or "",
            metadata={
                "persona_id": message.persona.persona_id,
                "channel": channel,
                "outcome": result.outcome.value,
            },
        )
