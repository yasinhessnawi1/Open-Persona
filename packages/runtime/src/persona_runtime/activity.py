"""Runtime adapter — turns core activity events into ``RunEvent``s (P2-D-1).

``persona-core`` defines the layer-pure :class:`~persona.tools.activity.ActivityObserver`
port and emits :class:`~persona.tools.activity.ActivityStart` /
:class:`~persona.tools.activity.ActivityEnd` from the single dispatch boundary
(``ObservedToolbox``). It deliberately knows nothing about ``RunEvent`` (the SSE event
type lives here in the runtime). This adapter is the missing half: it implements the
core port and translates each activity into a :class:`RunEvent` fed to the loop's
``on_event`` sink — so chat + run streams gain the unified "using <X>…" contract without
core importing the runtime.

It is constructed per dispatch by the loops' ``_dispatch`` (bound to that turn/run's
``on_event``), only when a sink is present — so the non-streaming path pays nothing.
``ObservedToolbox`` already guards observer calls, but the adapter is itself thin and
non-raising.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.tools.activity import DispatchContext, ObservedToolbox

from persona_runtime.agentic.events import RunEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from persona.schema.tools import ToolCall, ToolResult
    from persona.tools.activity import ActivityEnd, ActivityStart
    from persona.tools.toolbox import Toolbox

__all__ = ["RunEventActivityObserver", "dispatch_with_activity"]


class RunEventActivityObserver:
    """Implements core's ``ActivityObserver`` by emitting ``RunEvent``s.

    Args:
        on_event: The loop's async event sink (the API serialises to SSE). The adapter
            is only built when this is non-``None``.
    """

    def __init__(self, on_event: Callable[[RunEvent], Awaitable[None]]) -> None:
        self._on_event = on_event

    async def on_activity_start(self, start: ActivityStart) -> None:
        """Emit ``activity_start`` (the pre-execution "using <X>…" signal)."""
        await self._on_event(
            RunEvent.activity_start(
                start.step,
                activity_id=start.activity_id,
                kind=start.kind,
                name=start.name,
                label=start.label,
                args_summary=start.args_summary,
            )
        )

    async def on_activity_end(self, end: ActivityEnd) -> None:
        """Emit ``activity_end`` (resolves/clears or error/awaiting-approval marks)."""
        await self._on_event(
            RunEvent.activity_end(
                end.step,
                activity_id=end.activity_id,
                status=end.status,
                duration_ms=end.duration_ms,
                is_error=end.is_error,
                result_summary=end.result_summary,
            )
        )


async def dispatch_with_activity(
    toolbox: Toolbox,
    call: ToolCall,
    *,
    on_event: Callable[[RunEvent], Awaitable[None]] | None,
    step: int,
) -> ToolResult:
    """Dispatch ``call``, emitting paired activity events when a sink is present.

    The single seam both loops route their ``_dispatch`` through (DRY). When ``on_event``
    is ``None`` (no streaming sink) the bare ``toolbox.dispatch`` runs unchanged — the
    instrumentation (and its redaction cost) is skipped. Otherwise the toolbox is wrapped
    in :class:`~persona.tools.activity.ObservedToolbox` (the **outermost** decorator, so
    emit wraps any A3 gate — P2-D-a3-composition) bound to a per-dispatch
    :class:`RunEventActivityObserver`, and the step is threaded via
    :class:`~persona.tools.activity.DispatchContext` (fresh each call, never stale).

    Structural failures (``ToolNotAllowedError`` / A3's ``GatedActionProposedError``)
    propagate exactly as before — ``ObservedToolbox`` emits a paired end first, then
    re-raises — so each loop's existing error-recovery contract is unchanged.
    """
    if on_event is None:
        return await toolbox.dispatch(call)
    observed = ObservedToolbox(toolbox, RunEventActivityObserver(on_event))
    return await observed.dispatch(call, context=DispatchContext(step=step))
