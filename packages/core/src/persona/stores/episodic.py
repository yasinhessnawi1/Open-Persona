"""Episodic store — runtime-writable; query-time exponential decay.

Per D-01-4: ``final_score = similarity * exp(-elapsed_hours / tau)``,
default ``tau_hours = 24``, configurable per instance. The base class
returns chunks ranked by Chroma's similarity; this subclass reorders by
the decayed score at query time.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, ClassVar

from persona.schema.chunks import PersonaChunk, WriteSource
from persona.stores.base import TypedStore
from persona.stores.policy import PolicyDecision, PolicyRule, PolicyTable

__all__ = ["EpisodicStore"]


class EpisodicStore(TypedStore):
    """Runtime-writable; decay-ranked at query time."""

    STORE_KIND: ClassVar[str] = "episodic"
    _POLICY: ClassVar[PolicyTable] = {
        WriteSource.SYSTEM: PolicyRule(decision=PolicyDecision.ACCEPT),
        WriteSource.USER: PolicyRule(decision=PolicyDecision.ACCEPT),
        WriteSource.PERSONA_SELF: PolicyRule(decision=PolicyDecision.ACCEPT),
    }

    DEFAULT_TAU_HOURS: ClassVar[float] = 24.0

    def __init__(
        self,
        *,
        backend: Any,  # noqa: ANN401 — composes via the base
        audit_logger: Any,  # noqa: ANN401
        tau_hours: float = DEFAULT_TAU_HOURS,
    ) -> None:
        super().__init__(backend=backend, audit_logger=audit_logger)
        if tau_hours <= 0:
            msg = "tau_hours must be positive"
            raise ValueError(msg)
        self._tau_hours = tau_hours

    @property
    def tau_hours(self) -> float:
        return self._tau_hours

    def query(
        self,
        persona_id: str,
        query: str,
        top_k: int,
        **filters: Any,  # noqa: ANN401
    ) -> list[PersonaChunk]:
        # Pull more candidates than top_k so decay-reranking can pick a
        # different top set than Chroma's raw nearest neighbours.
        # 3x is conventional; bounded by the SQLite cap upstream.
        n_candidates = max(top_k * 3, top_k)
        candidates = super().query(persona_id, query, n_candidates, **filters)
        now = datetime.now(UTC)
        decayed = sorted(
            candidates,
            key=lambda c: self._decayed_score(c, now=now),
            reverse=True,
        )
        return decayed[:top_k]

    def _decayed_score(self, chunk: PersonaChunk, *, now: datetime) -> float:
        # Chroma returns cosine distance in ``chunk.distance``; convert to
        # similarity (1 - distance) for L2-normalised embeddings.
        similarity = 0.0 if chunk.distance is None else 1.0 - float(chunk.distance)
        elapsed_h = max(0.0, (now - chunk.created_at).total_seconds() / 3600.0)
        decay = math.exp(-elapsed_h / self._tau_hours)
        return similarity * decay
