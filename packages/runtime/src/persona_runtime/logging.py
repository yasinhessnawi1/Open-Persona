"""Per-turn telemetry — TurnLog, the writer port, and cost estimation (T06).

Every completed turn produces one :class:`TurnLog` (spec §7) recording the tier,
model, token usage, latency, estimated cost, tool-call count, skill used, and
whether history compaction fired. The log is written through a
:class:`TurnLogWriter` port: :class:`JSONLTurnLogWriter` for the CLI/local path,
:class:`MemoryTurnLogWriter` for tests. The hosted service (spec 08) implements
the same port against the Postgres ``turn_logs`` table.

`TurnLog` is a frozen **Pydantic** model, not a ``@dataclass`` (D-05-9): it
crosses the API/Postgres boundary and needs ``model_dump_json`` + tz-aware
datetime validation, following the D-02-2 / D-03-3 precedent.

Cost (D-05-10, S05-3) is an *estimate* from a hand-maintained price table — not
a billing record. Unknown ``(provider, model)`` pairs cost ``0.0`` and log a
warning. **Prices are illustrative v0.1 placeholders; verify against provider
pricing pages before any billing use.**
"""

from __future__ import annotations

import threading
from datetime import datetime  # noqa: TC003 — Pydantic needs runtime access for TurnLog.timestamp
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from persona.logging import get_logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from persona_runtime.routing import (
    RoutingDecision,  # noqa: TC001 — Pydantic model field needs runtime reference
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "JSONLTurnLogWriter",
    "MemoryTurnLogWriter",
    "TurnLog",
    "TurnLogWriter",
    "estimate_cost_cents",
]

_logger = get_logger("runtime.turnlog")


class TurnLog(BaseModel):
    """Telemetry for one completed turn (spec §7).

    Frozen + ``extra="forbid"``. ``timestamp`` must be tz-aware (UTC), matching
    the spec-01 model convention.

    Spec 18 (T12; D-18-X-turnlog-extension) extends the shape additively with
    routing observability:

    * :attr:`routing_decision` — the full :class:`RoutingDecision` from
      :meth:`Router.route`; serialises as nested JSON when persisted.
    * :attr:`routing_latency_ms` — wall-clock duration of the router's
      decision (from :func:`time.perf_counter` around the :meth:`route`
      call). Distinct from :attr:`latency_ms` which covers the whole turn.
    * :attr:`routing_fallback_triggered` — whether the :class:`UnifiedRouter`
      smart path fell back to the embedded :class:`HeuristicRouter`.
    * :attr:`routing_fallback_reason` — one of ``"timeout"`` /
      ``"scoring_error"`` / ``"empty_metadata"`` / ``"partial_metadata:<tier>"``
      when ``routing_fallback_triggered`` is ``True`` (D-18-X-fallback-instrumentation).

    All four routing fields are OPTIONAL — pre-Spec-18 callers (legacy tests
    that construct TurnLog directly without routing data) stay green by
    omitting them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    conversation_id: str
    turn_index: int = Field(ge=0)
    tier_used: str
    model_name: str
    provider: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0.0)
    cost_cents: float = Field(ge=0.0)
    tool_calls: int = Field(default=0, ge=0)
    skill_used: str | None = None
    history_compacted: bool = False
    timestamp: datetime
    routing_decision: RoutingDecision | None = None
    routing_latency_ms: float = Field(default=0.0, ge=0.0)
    routing_fallback_triggered: bool = False
    routing_fallback_reason: str | None = None

    @field_validator("timestamp", mode="after")
    @classmethod
    def _timestamp_must_be_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            msg = "naive datetime not allowed on TurnLog.timestamp"
            raise ValueError(msg)
        return value


@runtime_checkable
class TurnLogWriter(Protocol):
    """Port for persisting a :class:`TurnLog`. Write-only (CQS)."""

    def write(self, log: TurnLog) -> None:
        """Persist one turn log. No return value (CQS)."""
        ...


class JSONLTurnLogWriter:
    """Appends one JSON object per turn to ``<root>/<conversation_id>.jsonl``.

    Mirrors the spec-01 audit-log path convention (D-01-6 / D-05-10): co-located
    with the data, env-overridable via ``PERSONA_TURNLOG_PATH``. Single-process
    safe via a lock; the line-append itself is atomic enough at persona scale.
    Hosted multi-process safety lands with the Postgres writer in spec 08.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = threading.Lock()

    def write(self, log: TurnLog) -> None:
        """Append ``log`` as one JSON line under the conversation's file."""
        self._root.mkdir(parents=True, exist_ok=True)
        payload = log.model_dump_json()
        path = self._root / f"{log.conversation_id}.jsonl"
        with self._lock, path.open("a", encoding="utf-8") as f:
            f.write(payload)
            f.write("\n")


class MemoryTurnLogWriter:
    """In-memory writer for tests (mirrors spec-03's MemoryToolAuditLogger)."""

    def __init__(self) -> None:
        self.logs: list[TurnLog] = []

    def write(self, log: TurnLog) -> None:
        """Record ``log`` in insertion order."""
        self.logs.append(log)


# Hand-maintained estimate (S05-3 / D-05-10): (provider, model) ->
# (prompt_cents_per_1k_tokens, completion_cents_per_1k_tokens).
# v0.1 PLACEHOLDERS — verify against provider pricing before any billing use.
_PRICE_TABLE: dict[tuple[str, str], tuple[float, float]] = {
    ("anthropic", "claude-sonnet-4-6"): (0.30, 1.50),
    ("anthropic", "claude-haiku-4-5"): (0.08, 0.40),
    ("deepseek", "deepseek-chat"): (0.014, 0.028),
    ("groq", "llama-3.1-8b-instant"): (0.005, 0.008),
}

_warned_unknown: set[tuple[str, str]] = set()


def estimate_cost_cents(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Estimate the turn cost in cents from the price table (S05-3).

    Returns ``0.0`` for an unknown ``(provider, model)`` pair and logs a warning
    once per unknown pair. This is an estimate for telemetry, not a billing
    record.
    """
    key = (provider, model)
    prices = _PRICE_TABLE.get(key)
    if prices is None:
        if key not in _warned_unknown:
            _warned_unknown.add(key)
            _logger.warning(
                "no price-table entry; cost estimated as 0 provider={provider} model={model}",
                provider=provider,
                model=model,
            )
        return 0.0
    prompt_price, completion_price = prices
    return (prompt_tokens / 1000.0) * prompt_price + (completion_tokens / 1000.0) * completion_price
