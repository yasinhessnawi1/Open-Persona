"""Failure honesty — no failure path terminates without a C0 account (Spec A3, T13; criterion 7).

Every way an autonomous task can stop short — a leg dead-letters, a task is stuck, a budget is
reached, an approval expires — must produce a **user-visible C0 account**: the honest *cause* +
*options*, never silence. This module is the single home that maps each failure source to a
:class:`FailureAccount`, and — the load-bearing bit — the **exhaustive registry** that makes
"every failure path has an account" a closed, self-maintaining set (a new :class:`FailureKind`
with no builder fails the matrix test, exactly the back-door-closure discipline of T1's tool
mapping).

Each account carries a **cadence-bypass priority** (:func:`.cadence.bypasses_cap`), so a chatty
persona's cap can never suppress a failure report (T12). The worker originates the
account as a C0 message on the same Originator path; the awkward channel cases (a WhatsApp window
closed) ride C1's platform-rejection (``D-C1-X-platform-rejection`` — template/alternate, never a
silent drop), so the account still reaches the user.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from persona_api.approvals.cadence import MessagePriority

if TYPE_CHECKING:
    from persona.approvals import ActionProposal
    from persona.tasks import StuckReport

__all__ = [
    "FailureAccount",
    "FailureKind",
    "account_for_budget_pause",
    "account_for_expired_approval",
    "account_for_stuck",
    "all_failure_kinds_have_a_builder",
]


class FailureKind(StrEnum):
    """The exhaustive set of ways an autonomous task can stop short (criterion 7's matrix)."""

    LEG_DEAD_LETTER = "leg_dead_letter"
    TASK_STUCK = "task_stuck"
    BUDGET_PAUSE = "budget_pause"
    EXPIRED_APPROVAL = "expired_approval"


@dataclass(frozen=True)
class FailureAccount:
    """The honest, user-visible record of a stop — cause + options, never silence.

    Attributes:
        kind: Which failure path produced this.
        task_id: The task the user can act on.
        headline: A one-line, persona-voiceable summary (the C0 opener).
        cause: The real reason it stopped (never a disguised success).
        options: The concrete next steps the user can take — always at least one (no dead end).
        priority: A cadence-bypass class (:func:`cadence.bypasses_cap` holds) so the cap can
            never suppress it.
    """

    kind: FailureKind
    task_id: str
    headline: str
    cause: str
    options: tuple[str, ...]
    priority: MessagePriority

    def __post_init__(self) -> None:
        # An account with no cause or no options would be a silent / dead-end failure — exactly
        # what criterion 7 forbids. Fail fast at construction so a builder can never emit one.
        if not self.cause.strip():
            msg = "a FailureAccount must carry a non-empty cause (no silent failure)"
            raise ValueError(msg)
        if not self.options:
            msg = "a FailureAccount must offer at least one option (no dead end)"
            raise ValueError(msg)


def account_for_stuck(report: StuckReport, *, kind: FailureKind) -> FailureAccount:
    """Voice an A2 :class:`StuckReport` (a dead-letter or a stuck task) as an honest account."""
    next_step = report.next_step.strip() or "tell me how you'd like to proceed"
    return FailureAccount(
        kind=kind,
        task_id=report.task_id,
        headline="I've hit a wall on this task and need you.",
        cause=report.cause,
        options=(f"resume by replying ({next_step})", "cancel the task"),
        priority=MessagePriority.FAILURE,
    )


def account_for_budget_pause(task_id: str, *, cap_micros: int, spent_micros: int) -> FailureAccount:
    """Voice a budget-reached pause (T10) — it asks to extend (an approval-class C0)."""
    return FailureAccount(
        kind=FailureKind.BUDGET_PAUSE,
        task_id=task_id,
        headline="I've reached the budget you set for this task.",
        cause=f"spent {spent_micros} of {cap_micros} micros; I've paused rather than overrun it",
        options=("extend the budget (e.g. 'add another 50kr')", "cancel the task"),
        priority=MessagePriority.APPROVAL,  # the extend-ask — also a cadence-bypass class
    )


def account_for_expired_approval(proposal: ActionProposal) -> FailureAccount:
    """Voice an expired approval (T9) — the ask rotted out, the task auto-paused."""
    return FailureAccount(
        kind=FailureKind.EXPIRED_APPROVAL,
        task_id=proposal.task_id,
        headline="I didn't hear back, so I've paused this.",
        cause=f"the request to {proposal.description} expired with no decision",
        options=("ask me again to retry it", "cancel the task"),
        priority=MessagePriority.FAILURE,
    )


#: Every :class:`FailureKind` MUST have a builder here — the no-silent-failure closure. Builders
#: take different inputs, so this maps to a presence marker the matrix test enumerates; the
#: typed builders above are the real constructors.
_BUILDERS: Mapping[FailureKind, Callable[..., FailureAccount]] = {
    FailureKind.LEG_DEAD_LETTER: account_for_stuck,
    FailureKind.TASK_STUCK: account_for_stuck,
    FailureKind.BUDGET_PAUSE: account_for_budget_pause,
    FailureKind.EXPIRED_APPROVAL: account_for_expired_approval,
}


def all_failure_kinds_have_a_builder() -> bool:
    """True iff every :class:`FailureKind` maps to a builder (the criterion-7 completeness gate).

    A new failure kind added without an account builder makes this false — the matrix test then
    fails, so a silent failure path can never be introduced unnoticed.
    """
    return all(kind in _BUILDERS for kind in FailureKind)
