"""Unit tests for the task contract (Spec A2, T2).

The contract is the A4-authored anchor against drift (D-A2-1). It is a frozen value
type: goal, scope, acceptance criteria (statement + status), and stated bounds. It
carries NO mutation method — a leg structurally cannot rewrite it; status advances
through the Task (test_task.py), never on the contract object.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.tasks import (
    AcceptanceCriterion,
    AcceptanceStatus,
    Contract,
    ContractBounds,
)
from pydantic import ValidationError


def test_acceptance_status_values() -> None:
    assert {s.value for s in AcceptanceStatus} == {"pending", "done", "failed"}


def test_acceptance_criterion_defaults_pending() -> None:
    c = AcceptanceCriterion(id="c1", statement="prices tracked in prices.csv")
    assert c.status == AcceptanceStatus.PENDING


def test_acceptance_criterion_is_frozen_and_forbids_extra() -> None:
    c = AcceptanceCriterion(id="c1", statement="x")
    with pytest.raises(ValidationError):
        c.statement = "y"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        AcceptanceCriterion(id="c1", statement="x", extra="no")  # type: ignore[call-arg]


def test_minimal_contract() -> None:
    contract = Contract(goal="find the cheapest Oslo→Bergen fare this week")
    assert contract.goal.startswith("find the cheapest")
    assert contract.scope == ""
    assert contract.acceptance_criteria == ()
    assert contract.bounds == ContractBounds()


def test_full_contract_round_trips() -> None:
    contract = Contract(
        goal="find the cheapest fare",
        scope="self-hosted only; under 2000kr",
        acceptance_criteria=(
            AcceptanceCriterion(id="c1", statement="a fare under 2000kr is found"),
            AcceptanceCriterion(
                id="c2", statement="the user is notified", status=AcceptanceStatus.DONE
            ),
        ),
        bounds=ContractBounds(total_budget_micros=500_000, max_legs=20),
    )
    assert contract.acceptance_criteria[1].status == AcceptanceStatus.DONE
    assert contract.bounds.total_budget_micros == 500_000
    assert contract.bounds.max_legs == 20


def test_contract_is_frozen_and_forbids_extra() -> None:
    contract = Contract(goal="x")
    with pytest.raises(ValidationError):
        contract.goal = "y"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Contract(goal="x", surprise="no")  # type: ignore[call-arg]


def test_contract_bounds_all_optional() -> None:
    bounds = ContractBounds()
    assert bounds.total_budget_micros is None
    assert bounds.deadline is None
    assert bounds.max_legs is None


def test_contract_bounds_deadline_must_be_tz_aware() -> None:
    with pytest.raises(ValidationError):
        ContractBounds(deadline=datetime(2026, 6, 24, 12, 0))  # noqa: DTZ001 — naive on purpose
    ok = ContractBounds(deadline=datetime(2026, 6, 24, 12, 0, tzinfo=UTC))
    assert ok.deadline is not None
