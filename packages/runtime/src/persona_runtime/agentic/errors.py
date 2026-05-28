"""Agentic-loop domain exceptions (D-06-2).

The agentic loop is orchestration over already-reviewed components (specs 01–05),
so it adds the smallest possible exception surface — two classes for its two
genuinely-new terminal concepts:

- :class:`MaxStepsReachedError` — the loop ran out of steps without a final answer.
- :class:`RunCancelledError` — the caller cancelled the run.

**Both are DEFINED but the loop itself does not raise them.** Max-steps and
cancellation are normal terminal *outcomes*, modelled as
:class:`~persona_runtime.agentic.run.RunStatus` values; the loop sets the status,
produces (for max-steps) a best-effort summary, and *returns* the
:class:`~persona_runtime.agentic.run.Run`. These exception types exist so the
composition root (spec 08) can choose to surface those outcomes as raised errors
to an HTTP caller if it prefers — the loop hands back a persistable ``Run`` either
way (mirrors D-05-2's "``MaxToolRoundsExceeded`` deliberately not raised").

Everything else (provider 429s, tool-not-allowed, schema mismatches) is a
spec-01/02/03 domain exception that propagates unchanged — no parallel runtime
vocabulary (hexagonal architecture, ENGINEERING_STANDARDS.md §1.2).
"""

from __future__ import annotations

from persona.errors import PersonaError

__all__ = ["MaxStepsReachedError", "RunCancelledError"]


class MaxStepsReachedError(PersonaError):
    """A run reached ``max_steps`` without producing a final answer.

    Defined for callers that prefer to surface max-steps as an exception; the
    :class:`~persona_runtime.agentic.loop.AgenticLoop` does **not** raise it
    (it sets ``RunStatus.MAX_STEPS_REACHED``, generates a best-effort summary,
    and returns the ``Run``). Carries ``context`` with ``max_steps`` and
    ``run_id`` so an operator can see which run exhausted its budget.
    """


class RunCancelledError(PersonaError):
    """A run was cancelled via its :class:`CancelToken`.

    Defined for callers that prefer to surface cancellation as an exception;
    the :class:`~persona_runtime.agentic.loop.AgenticLoop` does **not** raise it
    (it sets ``RunStatus.CANCELLED`` at the step boundary and returns the
    ``Run`` cleanly, with no half-executed step — acceptance #6). Carries
    ``context`` with ``run_id`` and the ``step`` at which cancellation took
    effect.
    """
