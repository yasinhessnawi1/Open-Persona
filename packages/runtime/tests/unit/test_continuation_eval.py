"""The continuation-eval gate logic, proven on canned judge output (Spec A2, T11 / A2-R-2).

Model-free: the rubric→verdict map, the self-consistency vote, the gate aggregation, and —
the load-bearing one — the honesty clause (OSSIFICATION only when the fresh fact was provably
present; F-absent → INCONCLUSIVE, never a false-fail). The real LLM judge is exercised in
``tests/integration/test_continuation_eval_external.py``.
"""

from __future__ import annotations

from pathlib import Path

from _continuation_eval import (
    RubricScores,
    Verdict,
    gate,
    load_scenarios,
    majority_verdict,
    verdict_for,
)

_SUITE = Path(__file__).resolve().parents[1] / "fixtures" / "continuation_scenarios.yaml"


def _scores(**overrides: object) -> RubricScores:
    base: dict[str, object] = {
        "contract_anchoring": 2,
        "builds_on_conclusions": 2,
        "freshness": 2,
        "no_redundant_rework": 2,
        "coherent_next_step": 2,
        "fresh_fact_present": True,
    }
    base.update(overrides)
    return RubricScores(**base)  # type: ignore[arg-type]


# --- the committed scenario suite --------------------------------------------


def test_suite_has_three_missions_each_with_both_planted_artifacts() -> None:
    scenarios = load_scenarios(_SUITE)
    assert {s.mission for s in scenarios} == {"research", "monitoring", "preparation"}
    for s in scenarios:
        assert s.established_conclusion  # the anti-amnesia probe
        assert s.injected_fresh_fact  # the anti-ossification probe
        assert s.invalidated_plan_step
        assert s.injected_leg >= 1


# --- the rubric → verdict map ------------------------------------------------


def test_all_high_is_coherent() -> None:
    assert verdict_for(_scores()) == Verdict.COHERENT


def test_no_build_on_conclusions_is_amnesia() -> None:
    assert verdict_for(_scores(builds_on_conclusions=0)) == Verdict.AMNESIA


def test_ignored_fresh_fact_that_was_present_is_ossification() -> None:
    assert verdict_for(_scores(freshness=0, fresh_fact_present=True)) == Verdict.OSSIFICATION


def test_honesty_clause_fresh_fact_absent_is_inconclusive_not_ossification() -> None:
    # THE key clause: F never reached the context → a plumbing bug, NOT a reasoning failure.
    # Must not be labelled OSSIFICATION (which would false-fail the gate on plumbing).
    assert verdict_for(_scores(freshness=0, fresh_fact_present=False)) == Verdict.INCONCLUSIVE


def test_borderline_total_is_inconclusive() -> None:
    # anchored + building + fresh but weak everywhere → below 7/10 → review, not a pass.
    weak = _scores(
        contract_anchoring=1,
        builds_on_conclusions=1,
        freshness=1,
        no_redundant_rework=1,
        coherent_next_step=1,
    )
    assert weak.total == 5
    assert verdict_for(weak) == Verdict.INCONCLUSIVE


# --- self-consistency --------------------------------------------------------


def test_majority_verdict() -> None:
    assert (
        majority_verdict([Verdict.COHERENT, Verdict.COHERENT, Verdict.AMNESIA]) == Verdict.COHERENT
    )
    # a split (no strict majority) → INCONCLUSIVE (route to a human).
    assert majority_verdict([Verdict.COHERENT, Verdict.AMNESIA]) == Verdict.INCONCLUSIVE


# --- the gate ----------------------------------------------------------------


def test_gate_passes_on_all_coherent() -> None:
    report = gate([Verdict.COHERENT, Verdict.COHERENT, Verdict.COHERENT])
    assert report.coherent_rate == 1.0
    assert report.passes(coherent_threshold=0.8)


def test_gate_fails_on_any_reasoning_failure() -> None:
    assert not gate([Verdict.COHERENT, Verdict.AMNESIA]).passes(coherent_threshold=0.5)
    assert not gate([Verdict.COHERENT, Verdict.OSSIFICATION]).passes(coherent_threshold=0.5)


def test_gate_does_not_fail_on_inconclusive_plumbing() -> None:
    # An F-absent (plumbing) leg is INCONCLUSIVE → routes to review, does NOT fail the gate on
    # reasoning, and does NOT inflate the coherent rate either.
    report = gate([Verdict.COHERENT, Verdict.COHERENT, Verdict.COHERENT, Verdict.INCONCLUSIVE])
    assert report.inconclusive == 1
    assert report.coherent_rate == 0.75
    assert report.passes(coherent_threshold=0.75)
