"""Conversation history manager — summarise-and-compact.

Implements architecture §5.1.1: when a conversation grows past
``compact_every`` turns, fold turns ``[compacted_up_to, boundary - keep_recent)``
into a running summary and return only the summary + the last
``keep_recent`` verbatim turns. The full transcript stays in the episodic
store regardless; compaction only affects what we put into the prompt.

The summariser is injected (dependency inversion). The manager never calls
a model itself — it just decides *when* to summarise and which range to
hand the summariser.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from persona.logging import get_logger
from persona.schema.conversation import ConversationMessage

if TYPE_CHECKING:
    from collections.abc import Callable

    from persona.schema.conversation import Conversation


Summariser = "Callable[[list[ConversationMessage]], str]"


__all__ = ["ConversationHistoryManager", "Summariser"]


class ConversationHistoryManager:
    """Compact long conversations into a summary + last-N-verbatim.

    Args:
        compact_every: Turn count threshold. Conversations with this many
            turns or fewer return verbatim. The spec recommends 10.
        keep_recent: How many of the most recent turns to keep verbatim
            once compaction has fired. The spec recommends 5.

    Raises:
        ValueError: ``compact_every <= 0`` or ``keep_recent <= 0`` or
            ``keep_recent >= compact_every`` (the latter would mean we
            never compact anything).
    """

    def __init__(self, compact_every: int = 10, keep_recent: int = 5) -> None:
        if compact_every <= 0:
            msg = f"compact_every must be positive; got {compact_every!r}"
            raise ValueError(msg)
        if keep_recent <= 0:
            msg = f"keep_recent must be positive; got {keep_recent!r}"
            raise ValueError(msg)
        if keep_recent >= compact_every:
            msg = (
                f"keep_recent ({keep_recent}) must be less than "
                f"compact_every ({compact_every}); otherwise nothing is ever compacted"
            )
            raise ValueError(msg)
        self._compact_every = compact_every
        self._keep_recent = keep_recent
        self._log = get_logger("history")

    @property
    def compact_every(self) -> int:
        return self._compact_every

    @property
    def keep_recent(self) -> int:
        return self._keep_recent

    def manage(
        self,
        conversation: Conversation,
        summariser: Callable[[list[ConversationMessage]], str],
    ) -> list[ConversationMessage]:
        """Return the message list to put into the next prompt.

        Mutates ``conversation`` in-place by updating
        ``compacted_summary`` and ``compacted_up_to`` when a new compaction
        boundary is crossed. The full ``conversation.messages`` list is
        never modified — the episodic store keeps the verbatim record.

        Args:
            conversation: The live conversation, mutable.
            summariser: Function that turns a list of messages into a short
                summary string. Called at most once per ``manage`` call.

        Returns:
            The messages the runtime should feed into the next prompt.
        """
        turn_count = conversation.turn_count
        if turn_count <= self._compact_every:
            return list(conversation.messages)

        # The boundary is "everything older than the last keep_recent turns".
        boundary = turn_count - self._keep_recent

        # Range we need to summarise: from where we last stopped, up to
        # ``boundary`` (exclusive). If we've already covered this range,
        # no new summary call is needed.
        already_covered = conversation.compacted_up_to
        if boundary > already_covered:
            new_range = list(conversation.messages[already_covered:boundary])
            self._log.info(
                "compacting conversation conversation_id={cid} from={from_idx} to={to_idx}",
                cid=conversation.conversation_id,
                from_idx=already_covered,
                to_idx=boundary,
            )
            new_segment = summariser(new_range)
            conversation.compacted_summary = _join_summaries(
                conversation.compacted_summary, new_segment
            )
            conversation.compacted_up_to = boundary

        return self._build_prompt_messages(conversation, boundary)

    def _build_prompt_messages(
        self, conversation: Conversation, boundary: int
    ) -> list[ConversationMessage]:
        """Assemble the prompt input: a system summary message + the recent verbatim turns."""
        recent = list(conversation.messages[boundary:])
        if not conversation.compacted_summary:
            return recent
        summary_message = ConversationMessage(
            role="system",
            content=f"Earlier in this conversation: {conversation.compacted_summary}",
            created_at=datetime.now(UTC),
            metadata={"kind": "compacted_summary"},
        )
        return [summary_message, *recent]


def _join_summaries(existing: str, new_segment: str) -> str:
    """Concatenate the existing summary with a new segment, separated by a paragraph break."""
    new_segment = new_segment.strip()
    if not new_segment:
        return existing
    if not existing:
        return new_segment
    return f"{existing.rstrip()}\n\n{new_segment}"
