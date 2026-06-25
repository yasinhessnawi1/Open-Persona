"""The task cost ledger (Spec A2, T2, criterion 8).

A0 *meters* spend per leg (model / sandbox / external); A2 *accounts* per task; A3
*enforces* against the total (D-A0-X-metering-bar). This module is the pure accounting
value type: a frozen per-kind tally with a functional :meth:`CostLedger.record` that
returns a new ledger (the durable store persists it; the leg handler adds to it
atomically with the checkpoint append — D-A2-X-idempotency).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["CostLedger", "SpendKind"]


class SpendKind(StrEnum):
    """The spend class A0 meters and A2 accounts (mirrors the A0 ``meter(kind=...)``)."""

    MODEL = "model"
    SANDBOX = "sandbox"
    EXTERNAL = "external"


class CostLedger(BaseModel):
    """Cumulative task spend, tallied by kind in ``amount_micros`` (criterion 8).

    Frozen; :meth:`record` returns a new ledger (functional update, like ``Job``'s
    transition). ``total_micros`` is the number A3 enforces against and A6 displays.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_micros: int = Field(default=0, ge=0)
    sandbox_micros: int = Field(default=0, ge=0)
    external_micros: int = Field(default=0, ge=0)

    @property
    def total_micros(self) -> int:
        """The grand total across all spend kinds."""
        return self.model_micros + self.sandbox_micros + self.external_micros

    def record(self, kind: SpendKind, amount_micros: int) -> CostLedger:
        """Return a new ledger with ``amount_micros`` added to ``kind``'s tally.

        Args:
            kind: The spend class to credit.
            amount_micros: A non-negative spend amount (``amount_micros`` unit).

        Returns:
            A new :class:`CostLedger`; the receiver is unchanged (frozen).

        Raises:
            ValueError: If ``amount_micros`` is negative.
        """
        if amount_micros < 0:
            msg = "spend amount must be non-negative"
            raise ValueError(msg)
        field = f"{kind.value}_micros"
        current: int = getattr(self, field)
        return self.model_copy(update={field: current + amount_micros})
