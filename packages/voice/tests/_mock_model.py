"""Cancellable stub ModelReplyProducer for V4 tests (spec V4 T06/T07).

The Phase-1-refinement test double: a fixed-reply model that streams tokens
one at a time and supports **cancellable mid-stream emission with controllable
pacing**, so barge-in / continuation cancellation (T07) is deterministically
testable. After the first token it parks on a release gate (default), leaving
the generation mid-stream and cancellable; the test releases the gate to let it
finish, or cancels the awaiting task to simulate barge-in.

It mirrors the real Spec 02 backend's cancellation shape: the generator runs
its teardown (sets ``cancelled``) when the consuming task is cancelled, exactly
as the backend's ``async with provider.stream()`` closes on cancel.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from persona_voice.loop.streaming import Transcript

__all__ = ["CancellableStubModel"]


class CancellableStubModel:
    """A fixed-reply, cancellable, gated streaming model (ModelReplyProducer)."""

    def __init__(
        self,
        reply_tokens: Sequence[str],
        *,
        hold_after_first: bool = True,
        first_token_delay_s: float = 0.0,
    ) -> None:
        """Args:
        reply_tokens: The tokens the model "generates" for any turn.
        hold_after_first: If ``True`` (default), the generator parks on
            :attr:`release_rest` after emitting the first token, so the test
            can deterministically barge in mid-stream. If ``False``, all tokens
            stream back-to-back.
        first_token_delay_s: Budget-proxy first-token latency (the operator pass
            sets this to V1's budgeted LLM TTFT ~0.4 s so the full-loop number is
            budget-representative without a live V5 — D5 "passed against
            budget-proxy model TTFT", spec V4 operator-pass charter §1).
        """
        self._reply_tokens = tuple(reply_tokens)
        self._hold_after_first = hold_after_first
        self._first_token_delay_s = first_token_delay_s
        self.emitted: list[str] = []
        self.cancelled = False
        self.calls: list[str] = []
        self.first_token = asyncio.Event()
        self.release_rest = asyncio.Event()

    async def __call__(self, final_transcript: Transcript) -> AsyncIterator[str]:
        self.calls.append(final_transcript.text)
        return self._generate()

    async def _generate(self) -> AsyncIterator[str]:
        try:
            if self._first_token_delay_s > 0.0:
                await asyncio.sleep(self._first_token_delay_s)
            for index, token in enumerate(self._reply_tokens):
                self.emitted.append(token)
                yield token
                if index == 0:
                    self.first_token.set()
                    if self._hold_after_first:
                        await self.release_rest.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
