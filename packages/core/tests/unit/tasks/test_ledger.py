"""Unit tests for the task cost ledger (Spec A2, T2, criterion 8).

A0 *meters* per leg; A2 *accounts* per task. The ledger sums leg costs by kind
(model / sandbox / external) — the number A3 enforces against and A6 displays.
Pure value type; functional update (returns a new ledger).
"""

from __future__ import annotations

import pytest
from persona.tasks import CostLedger, SpendKind


def test_spend_kind_values() -> None:
    assert SpendKind.MODEL == "model"
    assert SpendKind.SANDBOX == "sandbox"
    assert SpendKind.EXTERNAL == "external"


def test_empty_ledger_is_zero() -> None:
    ledger = CostLedger()
    assert ledger.model_micros == 0
    assert ledger.sandbox_micros == 0
    assert ledger.external_micros == 0
    assert ledger.total_micros == 0


def test_record_returns_new_ledger_with_spend() -> None:
    ledger = CostLedger()
    after = ledger.record(SpendKind.MODEL, 1500)
    assert after.model_micros == 1500
    assert after.total_micros == 1500
    # functional update — the original is untouched.
    assert ledger.total_micros == 0


def test_record_accumulates_across_kinds() -> None:
    ledger = (
        CostLedger()
        .record(SpendKind.MODEL, 1000)
        .record(SpendKind.SANDBOX, 200)
        .record(SpendKind.EXTERNAL, 50)
        .record(SpendKind.MODEL, 500)
    )
    assert ledger.model_micros == 1500
    assert ledger.sandbox_micros == 200
    assert ledger.external_micros == 50
    assert ledger.total_micros == 1750


def test_record_zero_is_allowed() -> None:
    ledger = CostLedger().record(SpendKind.MODEL, 0)
    assert ledger.total_micros == 0


def test_record_negative_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        CostLedger().record(SpendKind.MODEL, -1)


def test_ledger_is_frozen() -> None:
    from pydantic import ValidationError

    ledger = CostLedger()
    with pytest.raises(ValidationError):
        ledger.model_micros = 5  # type: ignore[misc]


def test_ledger_forbids_negative_at_construction() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CostLedger(model_micros=-1)
