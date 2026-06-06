"""Conversation message and history models.

A conversation is the sequence of turns within one persona session.
``Conversation`` carries the bookkeeping the history manager uses:
``compacted_summary`` accumulates summarised prefix text and
``compacted_up_to`` records how far the summary extends so we never
re-summarise the same turns.

Mutable on purpose: the history manager updates ``compacted_summary`` and
``compacted_up_to`` in place. Pydantic v2 still gives us
``extra="forbid"`` and per-field validation; we just opt out of frozen.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic needs runtime access
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from persona.schema.content import (
    MessageContent,  # noqa: TC001 — Pydantic needs runtime access
    TextContent,
)
from persona.schema.tools import ToolCall  # noqa: TC001 — Pydantic needs runtime access

__all__ = [
    "Conversation",
    "ConversationHistory",
    "ConversationMessage",
]


class ConversationMessage(BaseModel):
    """One turn in a conversation.

    Attributes:
        role: Speaker role.
        content: Text of the message, or a multimodal list of typed
            content blocks (Spec 13 T03 widening). Tool calls and tool
            results are represented as separate ConversationMessage
            entries with ``role="tool"`` or ``role="assistant"`` (and
            structured tool metadata in ``metadata``). The list form
            carries :class:`TextContent` and :class:`ImageContent`
            blocks in caller order; the bytes for any image block live
            in the persona workspace (Spec 03 / D-13-X-now option c)
            and are resolved by the backend serialisers at send time.
        created_at: UTC-aware datetime of the message.
        metadata: Arbitrary string-keyed metadata (tool-call ids, tier
            used, latency, etc.).
        tool_calls: Structured tool calls issued by an ``assistant`` message.
            Populated by the runtime when a native-tool-calling backend
            requests tools, so the re-prompt carries the assistant's
            ``tool_calls`` *before* the matching ``tool`` results — the
            OpenAI/Anthropic protocol requires this pairing (spec 11 soak
            finding). Empty for every other message.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["user", "assistant", "system", "tool"]
    content: str | list[MessageContent]
    created_at: datetime
    metadata: dict[str, str] = Field(default_factory=dict)
    tool_calls: list[ToolCall] = Field(default_factory=list)

    @field_validator("created_at", mode="after")
    @classmethod
    def _created_at_must_be_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            msg = "naive datetime not allowed on ConversationMessage.created_at"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _reject_single_text_as_list(self) -> ConversationMessage:
        """Forbid the degenerate single-TextContent-in-list shape (T03).

        The text-only path must remain ``content=str``; lifting it into
        a one-element list breaks the Phase 1 byte-for-byte snapshot
        corpus and serves no purpose. Multi-block lists and lists
        containing at least one ImageContent are allowed.
        """
        if (
            isinstance(self.content, list)
            and len(self.content) == 1
            and isinstance(self.content[0], TextContent)
        ):
            msg = "single-text-as-list is not allowed; use content=str for text-only messages"
            raise ValueError(msg)
        return self


class Conversation(BaseModel):
    """A live, mutable conversation thread.

    See architecture §5.1.1 and spec §6 for the summarise-and-compact
    algorithm that uses ``compacted_summary``/``compacted_up_to``.

    Attributes:
        conversation_id: Stable identifier within an owner's scope.
        persona_id: The persona this conversation belongs to.
        messages: Ordered list of turns, oldest first.
        compacted_summary: Concatenated summary of the messages from index
            0 up to ``compacted_up_to`` (exclusive). Empty until the first
            compaction fires.
        compacted_up_to: Number of messages from the start of ``messages``
            that have been folded into ``compacted_summary``. 0 means no
            compaction has happened yet.
    """

    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    persona_id: str
    messages: list[ConversationMessage] = Field(default_factory=list)
    compacted_summary: str = ""
    compacted_up_to: int = Field(default=0, ge=0)

    @property
    def turn_count(self) -> int:
        """Number of messages currently in the conversation."""
        return len(self.messages)


# Convenience alias mirroring the spec's vocabulary in §6.
ConversationHistory = list[ConversationMessage]
