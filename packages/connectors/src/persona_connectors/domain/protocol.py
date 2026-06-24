"""The `Connector` protocol (Spec C1 T2, C1-D-6) — the provider-independent contract.

The shared flow calls this protocol; each per-platform adapter (C2–C5) implements
it. Mirrors the project's one-protocol-thin-adapters pattern (backends / stores /
STT / TTS / router). Deliberately minimal so it fits ALL SIX platforms:

- **outbound** — :meth:`Connector.send` IS the C0 ``MessageDeliverer`` half: it
  takes a :class:`~persona_connectors.domain.normalise.NormalisedOutbound` and
  reports a :class:`~persona.delivery.DeliveryResult` (``pending``/``failed`` —
  never a silent drop; the WhatsApp-window reject maps here per
  D-C1-X-platform-rejection), so a connector registers directly into C0's
  ``DeliveryRouter`` (T10) with no parallel outcome type;
- **capabilities** — a static :class:`~persona_connectors.domain.normalise.Capabilities`
  descriptor so the flow never assumes an absent feature;
- **lifecycle** — :meth:`start` / :meth:`close` for the platform connection.

**Inbound** is intentionally NOT a protocol method: an adapter converts its
platform payload to a ``NormalisedInbound`` and hands it to the framework flow
(T9). That keeps the contract transport-agnostic — webhook (Slack/WhatsApp),
long-poll/gateway (Telegram/Discord), and poll (email) adapters all fit without
the protocol forcing one inbound shape.

Owned surface — api-free; reuses persona-core only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from persona.delivery import DeliveryResult

    from persona_connectors.domain.normalise import Capabilities, NormalisedOutbound

__all__ = ["Connector"]


@runtime_checkable
class Connector(Protocol):
    """The contract each platform adapter (C2–C5) implements.

    Attributes:
        platform: The opaque channel key (e.g. ``"telegram"``) — the key this
            connector registers under in C0's ``DeliveryRouter`` and the value
            carried on ``NormalisedInbound.platform``. Never branched on by the
            framework (D-08-3).
        capabilities: What this channel can do (drives the render tier + the
            flow's feature assumptions).
    """

    platform: str
    capabilities: Capabilities

    async def send(self, outbound: NormalisedOutbound) -> DeliveryResult:
        """Send ``outbound`` to the platform and report the outcome.

        Lowers the semantic ``persona`` name tag to this connector's render tier
        (author-affordance / bold-prefix / plain-prefix) and delivers. Reports
        :class:`~persona.delivery.DeliveryOutcome` ``pending`` when the user is
        not reachable right now and ``failed`` on a platform rejection (e.g. the
        WhatsApp 24h-window — D-C1-X-platform-rejection) — **never a silent drop**
        (the C0 no-silent-drop contract); raises only on a true transport fault.
        """
        ...

    async def start(self) -> None:
        """Establish the platform connection / begin consuming events."""
        ...

    async def close(self) -> None:
        """Tear down the platform connection, releasing resources."""
        ...
