"""`RunEvent` — the SSE event stream for the run viewer (spec §8).

The :meth:`AgenticLoop.run` ``on_event`` callback receives :class:`RunEvent`
objects that the API (spec 08) serialises to SSE; each event type maps to a
visual element in the run viewer (spec 09). The loop never constructs a
``RunEvent`` by hand — it calls one of the typed classmethod constructors, which
are the single place each event's ``type`` string and ``data`` payload shape are
defined (DRY).

`RunEvent` is frozen Pydantic v2 (D-06-1): it crosses the spec-08 SSE
serialisation boundary. The ``data`` payload is ``dict[str, Any]`` so events can
carry structured detail (tool names, output text); the constructors are
responsible for building **JSON-safe** payloads (tool calls are rendered to
name/args dicts, never raw model objects) so ``model_dump_json`` always succeeds.
"""

from __future__ import annotations

from datetime import UTC, datetime  # noqa: TC003 — Pydantic needs runtime access
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from persona.schema.tools import ToolCall, ToolResult

    from persona_runtime.agentic.run import Run
    from persona_runtime.questions import QuestionOption

__all__ = ["RunEvent"]


class RunEvent(BaseModel):
    """One event in a run's lifecycle, serialised to SSE by the API (spec §8).

    Attributes:
        type: The event kind — one of ``started``, ``tier``, ``thinking``,
            ``tool_calling``, ``tool_result``, ``asking_user``,
            ``user_responded``, ``reasoning``, ``completed``, ``cancelled``,
            ``max_steps``, ``error``, ``finished``.
        step: The zero-based step index the event belongs to (``-1`` for
            run-level events that precede the first step, e.g. ``started``).
        data: Event-type-specific JSON-safe payload built by the constructor.
        timestamp: tz-aware UTC time the event was emitted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str
    step: int
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime

    @field_validator("timestamp", mode="after")
    @classmethod
    def _timestamp_must_be_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            msg = "naive datetime not allowed on RunEvent.timestamp; use datetime.now(UTC)"
            raise ValueError(msg)
        return value.astimezone(UTC)

    # Section: typed constructors (the single place each payload shape lives)

    @classmethod
    def started(cls, task: str) -> RunEvent:
        """The run has begun executing ``task``."""
        return cls(type="started", step=-1, data={"task": task}, timestamp=datetime.now(UTC))

    @classmethod
    def tier(cls, tier: str, routing: dict[str, Any] | None = None) -> RunEvent:
        """The model tier chosen for this turn/step (run-level; ``step=-1``).

        Used by the chat SSE stream (``ConversationLoop.turn``) to surface the
        router's actual tier choice — and available to the run viewer too. One
        event vocabulary across both streams.

        Args:
            tier: the chosen tier name.
            routing: Spec 31 (D-31-1) — an optional, concise model-decision
                summary (``chosen_model`` + ``dominant_factor`` + the
                model-fallback flag/reason). Present only when intelligent
                model-within-tier selection ran this turn; absent ⇒ the
                pre-Spec-31 bare-tier payload (back-compat). The raw score
                vector is never on the wire — it stays in the JSONL TurnLog.
        """
        data: dict[str, Any] = {"tier": tier}
        if routing is not None:
            data["routing"] = routing
        return cls(type="tier", step=-1, data=data, timestamp=datetime.now(UTC))

    @classmethod
    def thinking(cls, step: int) -> RunEvent:
        """The model is generating the next action for ``step``."""
        return cls(type="thinking", step=step, data={}, timestamp=datetime.now(UTC))

    @classmethod
    def tool_calling(
        cls,
        step: int,
        tool_calls: list[ToolCall],
        *,
        kind_of: Callable[[str], str] | None = None,
    ) -> RunEvent:
        """The model requested tool dispatches this step (JSON-safe call list).

        Spec 30 T01 (D-30-1): when ``kind_of`` is provided, each call dict carries
        an additive ``kind`` (``builtin`` / ``skill`` / ``mcp:builtin`` /
        ``mcp:optional``) so the frontend can badge the call by source. Absent
        (``kind_of is None``) the payload is byte-identical to the pre-spec-30
        shape — the back-compat default (the ``produced_files`` precedent). The
        resolver is :meth:`persona.tools.Toolbox.kind_for`; passing it (rather
        than the kind values) keeps the single resolution site authoritative.
        """
        calls = [
            {
                "name": c.name,
                "call_id": c.call_id,
                "args": c.args,
                **({"kind": kind_of(c.name)} if kind_of is not None else {}),
            }
            for c in tool_calls
        ]
        names = ", ".join(c.name for c in tool_calls)
        return cls(
            type="tool_calling",
            step=step,
            data={"tool_names": names, "tool_calls": calls},
            timestamp=datetime.now(UTC),
        )

    @classmethod
    def tool_result(
        cls, step: int, tool_name: str, result: ToolResult, *, kind: str | None = None
    ) -> RunEvent:
        """A tool dispatch completed (success or ``is_error=True``).

        D-F4-X-event-kind-for-produced-files (Spec F4 Phase 5 T02b — Option A):
        forward structured ``produced_files`` from ``ToolResult.data`` onto
        the event payload when present. The sandbox tool factory at
        ``packages/core/src/persona/sandbox/tool.py:269-279`` populates
        ``result.data["produced_files"]`` as ``list[{path, size_bytes,
        media_type}]``; pre-amendment this constructor dropped it.

        Additive (back-compat): pre-existing frames lacked the field; the
        F4 frontend dispatcher reads it when present and falls back to a
        result-block render when absent. **One edit covers both chat SSE
        AND RunEvent transports** because this constructor is the single
        place each event's payload shape is defined (see module docstring
        lines 7-8) — chat ``_sse(ev.type, ev.data)`` (bare payload, D-09-1)
        and run ``model_dump_json(event)`` (envelope with ``.data`` nested)
        both observe the same upstream shape.

        Empty ``produced_files: []`` is omitted from the payload (absence
        IS the back-compat shape; renderers treat absence as "no files").
        """
        data: dict[str, Any] = {
            "tool_name": tool_name,
            "is_error": result.is_error,
            "content": result.content,
        }
        # Spec 30 T01 (D-30-1): additive source badge. Same single-site,
        # back-compat-when-absent shape as produced_files/artifacts below; covers
        # both chat SSE and RunEvent transports. Omitted when the caller does not
        # resolve a kind (pre-spec-30 frames).
        if kind is not None:
            data["kind"] = kind
        if result.data is not None:
            pf = result.data.get("produced_files")
            if isinstance(pf, list) and pf:
                data["produced_files"] = pf
        # Spec 28 — forward ToolResult.artifacts onto the payload so the web
        # renders an inline FileCard + right-panel renderer. Same single-site,
        # additive, empty-omitted shape as produced_files above (covers both
        # chat SSE bare-payload and RunEvent envelope transports). When present,
        # artifacts are the preferred render path; the frontend normaliser
        # falls back to produced_files only when artifacts is absent.
        if result.artifacts:
            data["artifacts"] = [a.model_dump() for a in result.artifacts]
        return cls(
            type="tool_result",
            step=step,
            data=data,
            timestamp=datetime.now(UTC),
        )

    @classmethod
    def asking_user(
        cls,
        step: int,
        question: str,
        *,
        options: Sequence[QuestionOption] | None = None,
        allow_free_form: bool = True,
        proposal: Mapping[str, str] | None = None,
    ) -> RunEvent:
        """The persona asked the user a question.

        Spec 21 (D-21-9): additively carries the 3+1 proactive-question shape.
        When ``options`` is ``None`` (the model-initiated ``[ASK_USER]`` path and
        every pre-spec-21 frame) the payload is the bare ``{"question": ...}`` —
        byte-identical to the original shape, so existing renderers and the
        web ``AskingUserData`` type are unaffected. When ``options`` is present
        the payload adds the predefined options + free-form flag and the web
        renders the 3-button + free-form UI (T12). Absence IS the back-compat
        shape — exactly the ``produced_files`` precedent above.

        Args:
            step: The step index the question belongs to.
            question: The question text.
            options: The 3 predefined options, or ``None`` for a free-text ask.
            allow_free_form: Whether a free-form answer is accepted (only
                meaningful, and only emitted, when ``options`` is present).
        """
        data: dict[str, Any] = {"question": question}
        if options is not None:
            data["options"] = [{"label": o.label, "description": o.description} for o in options]
            data["allow_free_form"] = allow_free_form
        # Spec 30 (D-30-2): the general chat-proactive-question rail descriptor.
        # Additive — absent on a plain clarifying ask (the Spec-21 path); present
        # on a capability-gap offer so the web wires accept → grant/assign →
        # retry. The LOCKED {kind, name, provider?, action} envelope Spec 31 reuses.
        if proposal is not None:
            data["proposal"] = dict(proposal)
        return cls(type="asking_user", step=step, data=data, timestamp=datetime.now(UTC))

    @classmethod
    def user_responded(cls, step: int) -> RunEvent:
        """The user's answer was received and folded into context."""
        return cls(type="user_responded", step=step, data={}, timestamp=datetime.now(UTC))

    @classmethod
    def reasoning(cls, step: int, content: str) -> RunEvent:
        """Intermediate reasoning text (neither tool call, question, nor final)."""
        return cls(
            type="reasoning", step=step, data={"content": content}, timestamp=datetime.now(UTC)
        )

    @classmethod
    def completed(cls, step: int, output: str) -> RunEvent:
        """The model produced the final deliverable (``[FINAL]``)."""
        return cls(
            type="completed", step=step, data={"output": output}, timestamp=datetime.now(UTC)
        )

    @classmethod
    def cancelled(cls, step: int) -> RunEvent:
        """The run was cancelled at this step boundary."""
        return cls(type="cancelled", step=step, data={}, timestamp=datetime.now(UTC))

    @classmethod
    def max_steps(cls, step: int, summary: str) -> RunEvent:
        """The step budget was exhausted; ``summary`` is the best-effort output."""
        return cls(
            type="max_steps", step=step, data={"summary": summary}, timestamp=datetime.now(UTC)
        )

    @classmethod
    def error(cls, step: int, message: str) -> RunEvent:
        """An unrecoverable error terminated the run."""
        return cls(type="error", step=step, data={"message": message}, timestamp=datetime.now(UTC))

    @classmethod
    def finished(cls, run: Run) -> RunEvent:
        """The run is fully done (terminal); carries the final status + run id."""
        return cls(
            type="finished",
            step=len(run.steps),
            data={"run_id": run.id, "status": str(run.status)},
            timestamp=datetime.now(UTC),
        )
