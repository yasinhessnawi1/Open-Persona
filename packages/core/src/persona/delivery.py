"""The channel-delivery boundary for originated messages (Spec C0, T2).

One boundary, many deliverers (§4, R-C0-1): a channel implements
:class:`MessageDeliverer` to receive and deliver a persona's originated message.
The web app implements it now (over the existing Spec 08 real-time path); C1's
connector framework implements it next (deliver to the platform); direction 4,
when it drives origination autonomously, drives *this same* boundary — never N
channel-specific paths. The delivery routing (a later task) holds a registry of
deliverers and dispatches to the right one.

The port is deliberately minimal — one async ``deliver`` returning a
:class:`DeliveryResult`. A deliverer **reports** outcome (delivered / pending /
failed) rather than raising for ordinary unreachability; only a true fault raises
(re-raised as :class:`~persona.errors.MessageDeliveryError` at the api boundary).
This keeps "no silent drop" (criterion 6) a value, not an exception path:
undeliverable → ``DeliveryOutcome.pending``, recorded and present-on-next-open.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from persona.schema.origination import OriginatedMessage

__all__ = [
    "DeliveryOutcome",
    "DeliveryResult",
    "MessageDeliverer",
]


class DeliveryOutcome(StrEnum):
    """The outcome of attempting to deliver an originated message (D-C0-5).

    Values:
        DELIVERED: Handed to a channel that reached the user (e.g. pushed onto
            an open web session's live stream).
        PENDING: No channel could reach the user right now; the message is
            durably present in the conversation and surfaces on next reachability
            (criterion 6) — never silently lost.
        FAILED: A delivery fault occurred (a deliverer error). Distinct from
            ``PENDING`` (an expected, recoverable not-reachable state).
    """

    DELIVERED = "delivered"
    PENDING = "pending"
    FAILED = "failed"


class DeliveryResult(BaseModel):
    """What a delivery attempt recorded — enough for observability + direction-4
    reachability reasoning, without over-building (D-C0-5; no delivery-outcome
    table in v1).

    Attributes:
        outcome: The :class:`DeliveryOutcome` of the attempt.
        channel: The channel that handled (or attempted) delivery, e.g. ``"web"``;
            ``None`` when no channel was reachable (a ``pending`` outcome).
        detail: Optional short human-readable note (e.g. ``"open session"`` /
            ``"no active channel"``) for the audit line; never sensitive data.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: DeliveryOutcome
    channel: str | None = None
    detail: str | None = None


@runtime_checkable
class MessageDeliverer(Protocol):
    """A channel's port for delivering an originated message (the C1-facing seam).

    Implemented by the web-app deliverer (now) and every connector (C1, next);
    the routing dispatches an :class:`~persona.schema.origination.OriginatedMessage`
    to the right deliverer. ``@runtime_checkable`` so the registry can assert an
    object satisfies the port. Deliberately one method — connectors specialise the
    *implementation*, not the *interface*.
    """

    async def deliver(self, message: OriginatedMessage) -> DeliveryResult:
        """Deliver ``message`` to this channel and report the outcome.

        Reports ``DeliveryOutcome.pending`` when the user is not reachable right
        now (not a raise — criterion 6); raises only on a true delivery fault
        (re-raised as :class:`~persona.errors.MessageDeliveryError` at the api
        boundary).
        """
        ...
