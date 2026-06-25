"""Unit tests for the leg box (Spec A2, T3; D-A2-2).

Every leg is bounded by steps + wall-clock + (optional) spend so any leg can end
cleanly soon — the structural trick that degrades deploys/waits/cancellation to one
cheap operation. The box is pure config + pure predicates; the runtime trips the
``CancelToken`` at a step boundary when a bound is hit (no loop modification).
"""

from __future__ import annotations

import pytest
from persona.tasks import (
    DEFAULT_LEG_MAX_STEPS,
    DEFAULT_LEG_WALL_CLOCK_SECONDS,
    LegBox,
    LegBoxLimit,
)
from pydantic import ValidationError

# The A0 drain budget the wall-clock must sit inside (D-A0-5 / worker.py:94).
_A0_DRAIN_SECONDS = 270.0


def test_defaults_match_the_decision() -> None:
    assert DEFAULT_LEG_MAX_STEPS == 10
    assert DEFAULT_LEG_WALL_CLOCK_SECONDS == 180.0
    box = LegBox()
    assert box.max_steps == 10
    assert box.wall_clock_seconds == 180.0
    assert box.budget_micros is None  # no spend cap unless configured


def test_wall_clock_sits_inside_the_a0_drain_with_margin() -> None:
    # D-A2-2: ≥30s margin below the 270s drain so the checkpoint commit lands inside it.
    assert DEFAULT_LEG_WALL_CLOCK_SECONDS < _A0_DRAIN_SECONDS
    assert _A0_DRAIN_SECONDS - DEFAULT_LEG_WALL_CLOCK_SECONDS >= 30.0


def test_box_is_frozen_and_forbids_extra() -> None:
    box = LegBox()
    with pytest.raises(ValidationError):
        box.max_steps = 5  # type: ignore[misc]
    with pytest.raises(ValidationError):
        LegBox(surprise=1)  # type: ignore[call-arg]


def test_box_field_constraints() -> None:
    with pytest.raises(ValidationError):
        LegBox(max_steps=0)
    with pytest.raises(ValidationError):
        LegBox(wall_clock_seconds=0)
    with pytest.raises(ValidationError):
        LegBox(budget_micros=-1)


def test_within_all_bounds_is_not_exhausted() -> None:
    box = LegBox(max_steps=10, wall_clock_seconds=180.0, budget_micros=1000)
    assert box.exhausted_by(steps_taken=3, elapsed_seconds=10.0, spent_micros=100) is None


def test_steps_exhaustion() -> None:
    box = LegBox(max_steps=10)
    assert (
        box.exhausted_by(steps_taken=10, elapsed_seconds=1.0, spent_micros=0) is LegBoxLimit.STEPS
    )


def test_wall_clock_exhaustion() -> None:
    box = LegBox(wall_clock_seconds=180.0)
    got = box.exhausted_by(steps_taken=1, elapsed_seconds=180.0, spent_micros=0)
    assert got is LegBoxLimit.WALL_CLOCK


def test_budget_exhaustion_only_when_a_cap_is_set() -> None:
    capped = LegBox(budget_micros=500)
    assert (
        capped.exhausted_by(steps_taken=1, elapsed_seconds=1.0, spent_micros=500)
        is LegBoxLimit.BUDGET
    )
    uncapped = LegBox(budget_micros=None)
    assert uncapped.exhausted_by(steps_taken=1, elapsed_seconds=1.0, spent_micros=10**9) is None


def test_exhaustion_priority_is_wall_clock_then_budget_then_steps() -> None:
    # All three breached at once → wall-clock wins (the drain-critical bound).
    box = LegBox(max_steps=5, wall_clock_seconds=180.0, budget_micros=500)
    assert (
        box.exhausted_by(steps_taken=5, elapsed_seconds=200.0, spent_micros=600)
        is LegBoxLimit.WALL_CLOCK
    )
    # steps + budget (no wall-clock) → budget wins over steps.
    assert (
        box.exhausted_by(steps_taken=5, elapsed_seconds=10.0, spent_micros=600)
        is LegBoxLimit.BUDGET
    )
