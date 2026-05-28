"""The agentic loop — plan-act-reflect execution for end-to-end tasks (spec 06).

When a chat turn is not enough ("draft a complaint about my landlord refusing to
fix mould"), :class:`AgenticLoop` runs the *simplest possible* agent loop: one
model decides at each step whether to call a tool, ask the user a question, or
produce a final answer — no multi-agent orchestration, no graph-of-thought
(architecture §5.2). The value is in the error-handling and budget management
around the loop, not the loop itself.

The public surface spec 08 (the API, which exposes ``/v1/runs``) imports:

- :class:`AgenticLoop` — the plan-act-reflect engine (lands in T06).
- :class:`Run` / :class:`RunStatus` / :class:`Step` / :class:`StepType` — the
  serialisable run/step data model (T02).
- :class:`CancelToken` — caller-held cancellation control (T02).
- :class:`RunEvent` — the SSE event the API serialises for the run viewer (T03).
- :exc:`MaxStepsReachedError` / :exc:`RunCancelledError` — the two agentic
  terminal exception types (defined, but the loop returns a ``Run`` rather than
  raising; D-06-2).

Spec 08 owns what the loop does not (mirrors D-S05-4 / D-05-4): it persists the
``Run`` per-step, supplies the ``user_respond`` blocking callback, serialises
``RunEvent``\\ s to SSE, and owns the ``TierRegistry`` lifecycle.
"""

from __future__ import annotations

from persona_runtime.agentic.compactor import StepHistoryCompactor
from persona_runtime.agentic.errors import MaxStepsReachedError, RunCancelledError
from persona_runtime.agentic.events import RunEvent
from persona_runtime.agentic.loop import AgenticLoop
from persona_runtime.agentic.run import CancelToken, Run, RunStatus
from persona_runtime.agentic.step import Step, StepType

__all__ = [
    "AgenticLoop",
    "CancelToken",
    "MaxStepsReachedError",
    "Run",
    "RunCancelledError",
    "RunEvent",
    "RunStatus",
    "Step",
    "StepHistoryCompactor",
    "StepType",
]
