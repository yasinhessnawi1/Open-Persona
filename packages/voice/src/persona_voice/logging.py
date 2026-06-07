"""VoiceLog — the per-turn latency instrumentation for persona-voice (spec V1 T10).

The governing-constraint instrumentation every downstream voice spec reports
against. Mirrors LiveKit Agents v1.5 canonical ``TurnMetrics`` shape per
R-V1-5 (R-V1-3 per-hop budget table) so V2 / V3 / V5 adapters can populate
their own hop and the audit record stays cross-spec consistent
(D-V1-X-first-token-measurement-coordination — one measurement convention
shared by Spec 18 + V1 + V5).

Field surface:

* **Identity** — ``session_id`` / ``user_id`` / ``persona_id`` /
  ``conversation_id`` / ``turn_index`` so V1's JSONL writer round-trips into
  the same observability backend the persona-runtime ``TurnLog`` (D-05-9 +
  D-18-X-turnlog-extension) does.

* **LiveKit canonical hops** (R-V1-3 per-hop allocation, mirrored from
  LiveKit Agents v1.5 ``EOUMetrics`` + ``LLMMetrics`` + ``TTSMetrics``):

  * ``eou_at`` — V4 end-of-utterance detect (the round-trip start anchor).
  * ``stt_final_at`` — V2 final-transcript boundary.
  * ``llm_first_token_at`` — V5 first token (the same number Spec 18 records
    at runtime ``loop.py:465-468`` per D-18-X-first-token-measurement-impl;
    one measurement, two consumers).
  * ``tts_first_byte_at`` — V3 first audio byte.
  * ``audio_first_play_at`` — V1 outbound rail first frame (the round-trip
    end anchor).

* **V1's own per-hop share** (the binding 100ms P50 / 150ms P95 contract
  per D-V1-X-latency-target — the CI gate at T13 close-out):

  * ``transport_in_ms`` — LiveKit Room ingestion overhead.
  * ``transport_out_ms`` — LiveKit Room playout overhead.
  * ``loop_overhead_ms`` — V1's framing/dispatch overhead (the streaming
    loop body itself; T07 instruments at the dispatch point).

All hop fields are ``None`` until the corresponding seam wires in. T05/T07
populate the V1 hops in subsequent integration; V2/V3/V5 populate their
canonical hops as they land. The full-loop number is 🟦 operator-passed at
V4 close per D-V1-X-closeout-operator-pass-convention; V1 ships the
framework + its own measured share.

The writer surface (``VoiceLogWriter`` Protocol + ``JSONLVoiceLogWriter``
default) mirrors the persona-core ``AuditLogger`` discipline (one event
per line; flush per-write so audit is durable even on abrupt disconnect).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path  # noqa: TC003 — Pydantic field validation needs runtime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "JSONLVoiceLogWriter",
    "VoiceLog",
    "VoiceLogWriter",
    "compute_e2e_ms",
    "compute_v1_share_ms",
]


# Spec V1's binding per-hop share (D-V1-X-latency-target).
V1_BUDGET_P50_MS = 100
V1_BUDGET_P95_MS = 150


class VoiceLog(BaseModel):
    """One turn's worth of latency instrumentation (frozen boundary type).

    Frozen Pydantic v2 + ``extra="forbid"`` per D-05-9 — the JSON
    representation rolls forward into observability backends; allowing
    extra fields would let a misconfigured adapter silently smuggle
    untyped data into the audit record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Identity
    session_id: str
    user_id: str
    persona_id: str
    conversation_id: str
    turn_index: int = Field(ge=0)
    started_at: datetime
    ended_at: datetime | None = None

    # LiveKit canonical hops (populated by V2/V3/V5 adapters when wired)
    eou_at: datetime | None = None
    stt_final_at: datetime | None = None
    llm_first_token_at: datetime | None = None
    tts_first_byte_at: datetime | None = None
    audio_first_play_at: datetime | None = None

    # V1's own per-hop share (T05/T07 will populate at integration time)
    transport_in_ms: float | None = Field(default=None, ge=0.0)
    transport_out_ms: float | None = Field(default=None, ge=0.0)
    loop_overhead_ms: float | None = Field(default=None, ge=0.0)


def compute_e2e_ms(log: VoiceLog) -> float | None:
    """End-to-end round-trip in ms, from ``eou_at`` → ``audio_first_play_at``.

    Returns ``None`` if either anchor is unset (e.g. the turn ended before
    the agent spoke, or this is an echo-mode pass-through turn where the
    pipeline never produced a final transcript). When both anchors are
    set, the result is the user-perceived "live feel" number — the value
    R-V1-3's 800ms P50 / 1.5s P95 target is checked against.
    """
    if log.eou_at is None or log.audio_first_play_at is None:
        return None
    delta = log.audio_first_play_at - log.eou_at
    return delta.total_seconds() * 1000.0


def compute_v1_share_ms(log: VoiceLog) -> float | None:
    """V1's own per-hop overhead share (the CI-gate number).

    Sums ``transport_in_ms + loop_overhead_ms + transport_out_ms``. Returns
    ``None`` if any V1 hop is unset (the gate cannot be checked on a
    partially-instrumented turn). Per D-V1-X-latency-target: the resulting
    number must clear the binding 100ms P50 / 150ms P95 contract; T08/T11
    integration tests assert this at the wall-clock level once LiveKit
    Server is in the loop.
    """
    parts = (log.transport_in_ms, log.loop_overhead_ms, log.transport_out_ms)
    if any(p is None for p in parts):
        return None
    # parts are floats here after the None guard; cast for mypy.
    return float(parts[0] or 0.0) + float(parts[1] or 0.0) + float(parts[2] or 0.0)


class VoiceLogWriter(Protocol):
    """The sink :class:`StreamingLoop` writes one :class:`VoiceLog` per turn into.

    Async so a future Postgres writer can do an HTTP-friendly write
    without blocking the audio loop. The default :class:`JSONLVoiceLogWriter`
    is sync-flushed under the hood (file IO); ``await`` is the surface
    contract, not the impl shape.
    """

    async def write(self, log: VoiceLog) -> None: ...


class JSONLVoiceLogWriter:
    """Append-only JSONL writer — one :class:`VoiceLog` per line.

    Mirrors the persona-core ``JSONLAuditLogger`` discipline: each write
    opens the file in append mode, writes one ``model_dump_json()`` line
    plus a newline, and closes (flushes the OS buffer to disk). This
    survives abrupt disconnect — the last fully-written turn is durable
    even if the agent worker is SIGKILLed mid-call (the T12 reconnection
    integration test exercises this path).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        # Ensure the parent directory exists so the first write doesn't
        # surprise the caller with a FileNotFoundError after a long-running
        # session — the same idiom persona-core JSONLAuditLogger uses.
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    async def write(self, log: VoiceLog) -> None:
        line = log.model_dump_json()
        with self._path.open("a", encoding="utf-8") as fp:
            fp.write(line)
            fp.write("\n")

    def read_all(self) -> list[VoiceLog]:
        """Replay every VoiceLog persisted so far (test + audit helper).

        Each line is a JSON object — ``json.loads`` + :meth:`VoiceLog.model_validate`
        round-trips cleanly because the frozen-Pydantic boundary discipline
        means ``model_dump_json`` is the inverse of ``model_validate``.
        """
        if not self._path.exists():
            return []
        out: list[VoiceLog] = []
        with self._path.open("r", encoding="utf-8") as fp:
            for raw in fp:
                raw = raw.strip()
                if not raw:
                    continue
                out.append(VoiceLog.model_validate(json.loads(raw)))
        return out
