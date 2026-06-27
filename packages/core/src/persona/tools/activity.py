"""Activity events — pre/post-execution instrumentation around tool dispatch (P2).

P2 (Persona Activity Events) emits a structured **activity-start** event the moment the
persona is about to use *any* capability, paired with an **activity-end** on completion,
so the UI renders a live "using <X>…" state and every turn leaves an ordered,
persisted traceability trail. This module is the ``persona-core`` half: a layer-pure
:class:`ActivityObserver` **port** plus the :class:`ObservedToolbox` decorator that
invokes it around any dispatch target. ``persona-runtime`` supplies the ``RunEvent``
adapter (P2-D-1) — **core never imports ``RunEvent``** (keeps ``mypy --strict`` + the
hexagonal layering: core defines the port, runtime adapts it).

The emit seam (P2-D-1, P2-D-a3-composition):

- :class:`ObservedToolbox` is the **outermost** toolbox decorator. It wraps any dispatch
  target — the bare :class:`~persona.tools.toolbox.Toolbox` for chat/voice, or A3's
  ``PolicyGatedToolbox`` for unattended legs — so emit lives **outside** any gate. The
  composition root assembles ``ObservedToolbox(PolicyGatedToolbox(Toolbox))``; a T3
  structural test enforces that wiring. Were the order reversed
  (``PolicyGatedToolbox(ObservedToolbox(...))``) a gated call would raise before emit and
  produce **zero** events — the exact gap this ordering closes.
- The lifecycle is ``on_start`` → ``try: inner.dispatch`` → ``finally: on_end``. So an
  exception (a not-allowed raise, or A3's ``GatedActionProposedError``) **still** emits a
  paired end before propagating — record-and-re-raise, never swallow. A gated call
  therefore emits ``activity_start`` then ``activity_end(status="awaiting_approval")``,
  never zero events.
- ``activity_id`` is minted here (``uuid4``); duration is measured with a monotonic clock.
- Arguments are redacted at this boundary (P2-D-2, :func:`persona.tools.redaction.redact_args`)
  — the security gate — before they reach any observer.

Coverage caveat (honest framing, P2-D-1): because core cannot import ``RunEvent``,
"cannot ship silent" is not literally structural — it is *the observer is wired at the
composition root + a T3 test enforces it*, not a type-level impossibility.
"""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from persona.logging import get_logger
from persona.tools.redaction import redact_args

if TYPE_CHECKING:
    from persona.backends.types import ToolSpec
    from persona.schema.tools import ToolCall, ToolResult
    from persona.tools.kind import ToolKind

__all__ = [
    "ActivityEnd",
    "ActivityKind",
    "ActivityObserver",
    "ActivityStart",
    "ActivityStatus",
    "ActivityStatusCarrier",
    "DispatchContext",
    "ObservedToolbox",
    "activity_kind_for",
    "activity_label",
]

_logger = get_logger("tools.activity")

#: The capability family a dispatched call belongs to — the badge the UI renders.
#: ``memory_recall`` is set by the runtime bridge (it does not flow through dispatch);
#: every other kind is derived from the tool name by :func:`activity_kind_for`.
ActivityKind = Literal["tool", "skill", "mcp", "sandbox", "imagegen", "web", "memory_recall"]

#: The terminal outcome of an activity. ``denied`` = A3 unattended-denied (returned as a
#: ``ToolResult(is_error=True)`` the model recovers from); ``awaiting_approval`` = A3
#: gated (raised ``GatedActionProposedError``) — see P2-D-a3-composition.
ActivityStatus = Literal["ok", "error", "denied", "awaiting_approval"]


class DispatchContext(BaseModel):
    """Per-dispatch context the loop passes so the observer can place the event.

    A :class:`~persona.tools.toolbox.Toolbox` is built once per loop and reused across
    every turn/step, so the step cannot be closed over at construction (it would go
    stale). The loop's ``_dispatch`` wrapper — which has the step in scope — passes a
    fresh ``DispatchContext`` per call (P2-D-1).

    Attributes:
        step: The step index the activity belongs to (``-1`` for run-level chat/voice
            turns, mirroring ``RunEvent.tier``/``memory_recall``; a real step index
            inside the agentic loop).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    step: int = -1


class ActivityStart(BaseModel):
    """Emitted before a capability runs — drives the live "using <X>…" state.

    Attributes:
        activity_id: Stable id pairing this start with its :class:`ActivityEnd`.
        kind: The capability family (:data:`ActivityKind`).
        name: The specific name (tool name, ``mcp:server:tool``, ``use_skill``).
        label: A human label ("Searching the web", "Creating an image").
        args_summary: A redacted, bounded summary of the call args (P2-D-2). Never
            contains secrets; JSON-safe ``dict[str, str]``.
        step: The step index (mirrors :attr:`DispatchContext.step`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    activity_id: str
    kind: ActivityKind
    name: str
    label: str
    args_summary: dict[str, str] = Field(default_factory=dict)
    step: int = -1


class ActivityEnd(BaseModel):
    """Emitted when a capability completes (or is denied/gated) — resolves the state.

    Attributes:
        activity_id: Pairs this end with its :class:`ActivityStart`.
        status: Terminal outcome (:data:`ActivityStatus`).
        duration_ms: Wall-clock duration measured with a monotonic clock.
        is_error: True on ``error``/``denied`` (the model-recoverable failure surface).
        result_summary: A short, bounded summary of the result/outcome.
        step: The step index (mirrors :attr:`ActivityStart.step`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    activity_id: str
    status: ActivityStatus
    duration_ms: float
    is_error: bool
    result_summary: str = ""
    step: int = -1


@runtime_checkable
class ActivityObserver(Protocol):
    """The port the runtime adapter implements to turn activity into ``RunEvent``s.

    Both hooks are ``async`` because the run/chat ``on_event`` sink is async. An
    implementation MUST NOT raise — :class:`ObservedToolbox` guards the calls so a
    broken observer cannot break a turn, but a well-behaved adapter swallows + logs its
    own errors too (research §7).
    """

    async def on_activity_start(self, start: ActivityStart) -> None:
        """A capability is about to run."""
        ...

    async def on_activity_end(self, end: ActivityEnd) -> None:
        """A capability finished, was denied, or is awaiting approval."""
        ...


@runtime_checkable
class ActivityStatusCarrier(Protocol):
    """An exception that maps to a non-``error`` activity end (e.g. A3's gate).

    Lets :class:`ObservedToolbox` classify a raise without importing the raising
    layer: A3's ``GatedActionProposedError`` declares ``activity_status =
    "awaiting_approval"`` (the merge-back contract, P2-D-a3-composition); any other raise
    falls back to ``"error"``.
    """

    @property
    def activity_status(self) -> ActivityStatus:
        """The end status this exception represents."""
        ...


class _DispatchTarget(Protocol):
    """The toolbox surface :class:`ObservedToolbox` wraps and forwards.

    Satisfied by the bare :class:`~persona.tools.toolbox.Toolbox` and by A3's
    ``PolicyGatedToolbox`` (both expose the same dispatch + query surface).
    """

    async def dispatch(self, tool_call: ToolCall) -> ToolResult: ...
    def kind_for(self, tool_name: str) -> ToolKind: ...
    def get_specs(self) -> list[ToolSpec]: ...
    def is_allowed(self, tool_name: str) -> bool: ...
    def names(self) -> list[str]: ...


def activity_kind_for(tool_name: str) -> ActivityKind:
    """Map a dispatched tool name to its :data:`ActivityKind`.

    Pure + total (unknown → ``"tool"``). ``memory_recall`` is intentionally not derived
    here — it is bridged by the runtime from the retrieval path, not from a dispatch.
    """
    if tool_name.startswith("mcp:"):
        return "mcp"
    if tool_name == "use_skill":
        return "skill"
    if tool_name in {"web_search", "web_fetch"}:
        return "web"
    if tool_name == "code_execution":
        return "sandbox"
    if tool_name == "generate_image":
        return "imagegen"
    return "tool"


def activity_label(kind: ActivityKind, name: str) -> str:
    """A concise human label for the live "using <X>…" affordance.

    English defaults; the frontend may localise from ``kind`` + ``name``. ``name`` is a
    trusted tool identifier (not user input), safe to render verbatim.
    """
    if kind == "web":
        return "Searching the web"
    if kind == "sandbox":
        return "Running code"
    if kind == "imagegen":
        return "Creating an image"
    if kind == "skill":
        return "Using a skill"
    if kind == "mcp":
        # ``mcp:server:tool`` — name the server when present.
        parts = name.split(":", 2)
        server = parts[1] if len(parts) >= 2 and parts[1] else "an MCP server"
        return f"Using {server}"
    if kind == "memory_recall":
        return "Recalling from memory"
    return f"Using {name}"


def _summarise_result(result: ToolResult) -> str:
    """A short, bounded summary of a result's content for the end event."""
    text = result.content or ""
    if len(text) > _VALUE_SUMMARY_MAX:
        return f"{text[:_VALUE_SUMMARY_MAX]}…"
    return text


_VALUE_SUMMARY_MAX = 120


class ObservedToolbox:
    """Outermost toolbox decorator — emits a paired activity event around each dispatch.

    Wraps any :class:`_DispatchTarget` (the bare ``Toolbox``, or A3's
    ``PolicyGatedToolbox``) and an :class:`ActivityObserver`. Query methods forward
    unchanged; :meth:`dispatch` is instrumented. See the module docstring for the emit
    seam and the A3 composition contract.
    """

    def __init__(self, inner: _DispatchTarget, observer: ActivityObserver) -> None:
        self._inner = inner
        self._observer = observer

    # Section: query methods (forwarded unchanged)

    def is_allowed(self, tool_name: str) -> bool:
        """Forward to the wrapped target."""
        return self._inner.is_allowed(tool_name)

    def names(self) -> list[str]:
        """Forward to the wrapped target."""
        return self._inner.names()

    def get_specs(self) -> list[ToolSpec]:
        """Forward to the wrapped target."""
        return self._inner.get_specs()

    def kind_for(self, tool_name: str) -> ToolKind:
        """Forward to the wrapped target."""
        return self._inner.kind_for(tool_name)

    # Section: instrumented dispatch

    async def dispatch(
        self, tool_call: ToolCall, *, context: DispatchContext | None = None
    ) -> ToolResult:
        """Emit ``on_start`` → run the wrapped dispatch → emit ``on_end`` (always).

        The end event fires from a ``finally`` so a denial/exception still produces a
        paired end before propagating (record-and-re-raise). Status starts pessimistic
        (``error``) and is set to ``ok`` only on a successful, non-error result; an
        :class:`ActivityStatusCarrier` raise (A3's gate) maps to its declared status.
        Observer calls are guarded so a broken observer never breaks the turn.
        """
        ctx = context if context is not None else DispatchContext()
        kind = activity_kind_for(tool_call.name)
        activity_id = uuid4().hex
        start = ActivityStart(
            activity_id=activity_id,
            kind=kind,
            name=tool_call.name,
            label=activity_label(kind, tool_call.name),
            args_summary=redact_args(tool_call.args),
            step=ctx.step,
        )
        await self._emit_start(start)

        status: ActivityStatus = "error"
        is_error = True
        result_summary = ""
        started = monotonic()
        try:
            result = await self._inner.dispatch(tool_call)
            is_error = result.is_error
            status = "error" if result.is_error else "ok"
            result_summary = _summarise_result(result)
            return result
        except Exception as exc:  # noqa: BLE001 — classify, emit a paired end, re-raise
            if isinstance(exc, ActivityStatusCarrier):
                status = exc.activity_status
            result_summary = f"{type(exc).__name__}: {exc}"[:_VALUE_SUMMARY_MAX]
            raise
        finally:
            await self._emit_end(
                ActivityEnd(
                    activity_id=activity_id,
                    status=status,
                    duration_ms=(monotonic() - started) * 1000.0,
                    is_error=is_error,
                    result_summary=result_summary,
                    step=ctx.step,
                )
            )

    async def _emit_start(self, start: ActivityStart) -> None:
        try:
            await self._observer.on_activity_start(start)
        except Exception:  # noqa: BLE001 — instrumentation must never break a turn
            _logger.warning("activity observer on_start failed", activity_id=start.activity_id)

    async def _emit_end(self, end: ActivityEnd) -> None:
        try:
            await self._observer.on_activity_end(end)
        except Exception:  # noqa: BLE001 — instrumentation must never break a turn
            _logger.warning("activity observer on_end failed", activity_id=end.activity_id)
