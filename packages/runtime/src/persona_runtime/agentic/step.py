"""The `Step` model — one plan-act-reflect cycle in an agentic run (spec §7).

A :class:`Step` records what happened in one iteration of the loop: the action
the model took (`tool_call` / `ask_user` / `final` / `reasoning` / `error`), the
tool calls + results, any question/answer, the final content, and the per-step
telemetry (tier, tokens, latency).

Frozen Pydantic v2 (D-06-1): a `Step` crosses the spec-08 DB/JSON boundary as
part of :class:`~persona_runtime.agentic.run.Run` (acceptance #10 serialises the
run). The loop builds steps in mutable local state and appends already-frozen
:class:`Step` instances to its working step list.

Per-step telemetry lives on this model (D-06-3): `tier_used`, `tokens`, and
`latency_ms` are the v0.1 telemetry sink — there is no separate `StepLog` writer
(the `Run` persists its steps, so a parallel sink would be YAGNI).
"""

from __future__ import annotations

from enum import StrEnum

from persona.schema.tools import ToolCall, ToolResult  # noqa: TC002 — Pydantic needs runtime refs
from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Step", "StepType"]


class StepType(StrEnum):
    """The kind of action a step represents (spec §7).

    Values:
        TOOL_CALL: The model requested one or more tool dispatches.
        ASK_USER: The model asked the user a question (``[ASK_USER]`` marker).
        FINAL: The model produced the deliverable (``[FINAL]`` marker).
        REASONING: Intermediate text that is neither a tool call, a question,
            nor a final answer — added to context and the loop continues.
        ERROR: A step that terminated in an unrecoverable error.
    """

    TOOL_CALL = "tool_call"
    ASK_USER = "ask_user"
    FINAL = "final"
    REASONING = "reasoning"
    ERROR = "error"


class Step(BaseModel):
    """One plan-act-reflect cycle (spec §7).

    Frozen + ``extra="forbid"``. Field presence depends on ``type``: a
    ``TOOL_CALL`` step populates ``tool_calls``/``results``; an ``ASK_USER`` step
    populates ``question``/``user_answer``; ``FINAL``/``REASONING`` populate
    ``content``. The telemetry fields (``tier_used``/``tokens``/``latency_ms``)
    are populated for every step.

    Attributes:
        type: Which kind of action this step took.
        tool_calls: Tool requests the model made this step (``TOOL_CALL``).
        results: Results from dispatching ``tool_calls`` (one per call;
            failures are :class:`~persona.schema.tools.ToolResult` with
            ``is_error=True`` — there is no separate error field, D-03-3).
        question: The question text for an ``ASK_USER`` step.
        user_answer: The user's answer (``None`` if no ``user_respond`` callback
            was supplied — the loop then proceeds with best judgment).
        content: The text for a ``FINAL`` or ``REASONING`` step.
        tier_used: The model tier this step ran on (telemetry; D-06-3).
        tokens: Total tokens for this step's model call (telemetry).
        latency_ms: Wall-clock latency for this step's model call (telemetry).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: StepType
    tool_calls: list[ToolCall] = Field(default_factory=list)
    results: list[ToolResult] = Field(default_factory=list)
    question: str | None = None
    user_answer: str | None = None
    content: str | None = None
    tier_used: str | None = None
    tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)
