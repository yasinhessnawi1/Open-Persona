"""Step-history compaction for the agentic loop (spec §6).

An agentic run's context grows with every step — tool results can be large (a
``web_fetch`` returning 4000 chars across four URLs is 16K tokens of tool results
alone). The :class:`StepHistoryCompactor` keeps the context within the tier's
budget by summarising earlier step history when it crosses 80% of the budget,
while preserving the run's invariants verbatim: the **persona block + task
description** (the floor, ``context[0]``) and the **most recent steps**.

The async-bridge (D-06-4 — kept LOCAL, no shared ``_bridge.py``): the small-tier
summary needs an ``await``, but :meth:`compact_if_needed` is sync-shaped. The
*loop* owns the async call — it asks :meth:`should_compact` whether compaction
will fire, pre-computes the summary by awaiting the small tier, and passes the
resolved ``summary`` string in. This reuses the D-05-X *idiom* (predict →
pre-compute → sync callee) but NOT its machinery: the conversation manager keys
off a turn-count boundary and is stateful; this compactor keys off a token
threshold and is stateless (a run is one pass). The shared element is a pattern,
documented here, not a function.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from persona.schema.conversation import ConversationMessage
from persona.skills import count_tokens

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["StepHistoryCompactor"]

# Fraction of the tier budget above which compaction fires (spec §6).
_COMPACT_THRESHOLD = 0.8
# Messages from the tail kept verbatim — "recent 2 steps" (spec §6). A step
# contributes at most a couple of messages (an assistant turn + its tool
# results), so keeping the last few trailing messages preserves recent steps.
_KEEP_RECENT_MESSAGES = 4


def _render(messages: Sequence[ConversationMessage]) -> str:
    """Render messages to the text form used for token counting (mirrors loop.py)."""
    return "\n".join(f"{m.role}: {m.content}" for m in messages)


class StepHistoryCompactor:
    """Compacts an agentic run's step history at the tier budget (spec §6).

    Stateless — each :meth:`compact_if_needed` call recomputes from the current
    context. The persona block + task description (``context[0]``) and the most
    recent messages are never summarised; only the middle step history is.
    """

    def should_compact(self, context: Sequence[ConversationMessage], budget: int) -> bool:
        """True if ``context`` exceeds 80% of ``budget`` and has a compactable middle.

        The loop calls this BEFORE :meth:`compact_if_needed` so it knows whether
        to pre-compute the (async) small-tier summary. Returns ``False`` when the
        context is small enough OR when there is no middle to summarise (the
        floor + recent tail already account for every message).
        """
        if budget <= 0:
            return False
        if len(context) <= 1 + _KEEP_RECENT_MESSAGES:
            return False
        return count_tokens(_render(context)) > int(budget * _COMPACT_THRESHOLD)

    def compact_if_needed(
        self,
        context: list[ConversationMessage],
        budget: int,
        *,
        summary: str | None,
    ) -> list[ConversationMessage]:
        """Return a compacted context, or ``context`` unchanged if under budget.

        Args:
            context: The run's working context — ``[floor, *step_messages]``
                where ``floor`` (index 0) is the persona block + task +
                agentic-instructions system message.
            budget: The tier's context-window budget in tokens.
            summary: The pre-computed summary of the middle step history (the
                loop awaits the small tier and passes the result here; D-06-4).
                ``None`` means "no summary available" → no-op (the loop passes a
                string exactly when :meth:`should_compact` returned ``True``).

        Returns:
            ``[floor, summary_message, *recent_messages]`` when compaction fires,
            else ``context`` unchanged. The floor and the recent tail are
            byte-identical to the input (acceptance #8).
        """
        if summary is None or not self.should_compact(context, budget):
            return context

        floor = context[0]
        recent = context[self._recent_start(context):]
        summary_message = ConversationMessage(
            role="system",
            content=f"Earlier in this run: {summary}",
            created_at=datetime.now(UTC),
            metadata={"kind": "step_compaction"},
        )
        return [floor, summary_message, *recent]

    @staticmethod
    def _recent_start(context: Sequence[ConversationMessage]) -> int:
        """Index where the verbatim recent tail begins.

        Never index 0 (the floor), and never on a dangling ``tool`` message: a
        ``tool`` result must keep the preceding assistant ``tool_calls`` message
        in the same context window, or native providers (OpenAI/DeepSeek) reject
        the request ("'tool' must follow a message with 'tool_calls'"). We walk
        the boundary back over any leading ``tool`` messages so the kept tool-call
        group stays intact. Spec 11 soak finding.
        """
        start = max(1, len(context) - _KEEP_RECENT_MESSAGES)
        while start > 1 and context[start].role == "tool":
            start -= 1
        return start

    def middle_to_summarise(
        self, context: Sequence[ConversationMessage]
    ) -> list[ConversationMessage]:
        """The slice the loop should summarise: everything between floor and recent tail.

        The loop renders this, awaits the small-tier summariser on it, and passes
        the resulting string back as ``summary``. Returns ``[]`` when there is no
        middle (the caller then passes ``summary=None``).
        """
        if len(context) <= 1 + _KEEP_RECENT_MESSAGES:
            return []
        start = self._recent_start(context)
        return list(context[1:start])
