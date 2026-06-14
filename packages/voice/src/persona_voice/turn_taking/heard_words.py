"""Barged-over memory honesty — the V5 memory-write seam (spec V4 T07).

When a persona reply is interrupted, memory must record what was *heard*, not
what was *planned* (spec V4 §8 risk; D-V4-4). V1's loop emits a
:class:`persona_voice.loop.streaming.HeardReply` per turn (the spoken-so-far
text + a ``truncated`` flag); this module defines the V4/V5 contract those map
onto:

* :class:`BargedReply` — the frozen record V5 writes to episodic memory.
* :class:`TurnTranscriptListener` — the seam V5 implements to receive it.

The loop→V5 adaptation (``HeardReply`` → ``BargedReply`` → this listener) lives
in :mod:`persona_voice.turn_taking.bridge` (``HeardWordsBridge``), which is also
where the loop is wired — keeping this module a pure contract with no loop
dependency (and so no import cycle).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BargedReply",
    "TurnTranscriptListener",
]


class BargedReply(BaseModel):
    """What the persona actually said on a turn — the memory-honesty record.

    Carries the heard-so-far ``heard_text`` and whether the turn was
    ``truncated`` (cut short by barge-in / continuation). V5 writes
    ``heard_text`` to episodic memory; a truncated reply records only the
    prefix the user actually heard, never the unspoken remainder (D-V4-4).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    heard_text: str
    truncated: bool
    token_count: int = Field(ge=0)


@runtime_checkable
class TurnTranscriptListener(Protocol):
    """V5 seam — receives the committed (heard) reply for the memory write.

    V4 owns the record *shape* (:class:`BargedReply`) because V5 needs the
    contract to write against; V5 owns the memory write itself. Implementations
    MUST NOT raise.
    """

    async def on_reply_committed(self, reply: BargedReply) -> None: ...
