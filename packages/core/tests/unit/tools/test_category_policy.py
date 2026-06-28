"""Tests for the A3 per-task category policy matrix (A3-D-1, A3-D-X-policy-split).

The matrix maps each :class:`ActionCategory` to an allow / gate / deny decision over the
conservative defaults (free → allow, gated-by-default → gate). The safety properties:

- an **unconfigured** policy gates the four consequential categories and allows the four safe
  ones (criterion 2);
- a tool spanning multiple categories takes the **most-restrictive** decision (deny > gate >
  allow) — so network-enabled ``code_execution`` (``{compute, external_mutate}``) gates;
- a per-task **override** tightens (or, deliberately, loosens) a single category without
  touching the rest, and a new category added later inherits its conservative default for
  free (sparse overrides).
"""

from __future__ import annotations

import pytest
from persona.tools.categories import FREE_CATEGORIES, GATED_BY_DEFAULT, ActionCategory
from persona.tools.category_policy import (
    DEFAULT_POLICY,
    CategoryDecision,
    CategoryPolicy,
    CategoryRule,
)
from pydantic import ValidationError


class TestDecisionEnum:
    def test_three_decisions(self) -> None:
        assert {d.value for d in CategoryDecision} == {"allow", "gate", "deny"}


class TestConservativeDefaults:
    def test_default_policy_allows_the_four_free_categories(self) -> None:
        for category in FREE_CATEGORIES:
            assert DEFAULT_POLICY.decide(category) is CategoryDecision.ALLOW

    def test_default_policy_gates_the_four_consequential_categories(self) -> None:
        # criterion 2: an unconfigured task cannot spend / communicate-as-user /
        # external-mutate / use-credentials without an approval — they gate.
        for category in GATED_BY_DEFAULT:
            assert DEFAULT_POLICY.decide(category) is CategoryDecision.GATE

    def test_default_policy_has_no_denies(self) -> None:
        # deny is an explicit per-task tightening, never a default.
        assert all(DEFAULT_POLICY.decide(c) is not CategoryDecision.DENY for c in ActionCategory)


class TestOverrides:
    def test_override_loosens_a_single_category(self) -> None:
        # "yes, it may spend without asking" — granted once at the contract (A4).
        policy = CategoryPolicy(
            overrides=(
                CategoryRule(category=ActionCategory.SPEND, decision=CategoryDecision.ALLOW),
            )
        )
        assert policy.decide(ActionCategory.SPEND) is CategoryDecision.ALLOW
        # every other category keeps its conservative default.
        assert policy.decide(ActionCategory.EXTERNAL_MUTATE) is CategoryDecision.GATE
        assert policy.decide(ActionCategory.OBSERVE) is CategoryDecision.ALLOW

    def test_override_tightens_to_deny(self) -> None:
        policy = CategoryPolicy(
            overrides=(
                CategoryRule(
                    category=ActionCategory.EXTERNAL_MUTATE, decision=CategoryDecision.DENY
                ),
            )
        )
        assert policy.decide(ActionCategory.EXTERNAL_MUTATE) is CategoryDecision.DENY

    def test_duplicate_category_in_overrides_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CategoryPolicy(
                overrides=(
                    CategoryRule(category=ActionCategory.SPEND, decision=CategoryDecision.ALLOW),
                    CategoryRule(category=ActionCategory.SPEND, decision=CategoryDecision.DENY),
                )
            )

    def test_policy_is_frozen_and_forbids_extra(self) -> None:
        with pytest.raises(ValidationError):
            CategoryPolicy(unknown_field=1)  # type: ignore[call-arg]
        policy = CategoryPolicy()
        with pytest.raises(ValidationError):
            policy.overrides = ()  # type: ignore[misc]


class TestDecideTool:
    """A tool's decision = the most-restrictive across all its categories."""

    def test_single_free_category_allows(self) -> None:
        assert DEFAULT_POLICY.decide_tool(frozenset({ActionCategory.OBSERVE})) is (
            CategoryDecision.ALLOW
        )

    def test_gated_category_gates(self) -> None:
        assert DEFAULT_POLICY.decide_tool(frozenset({ActionCategory.SPEND})) is (
            CategoryDecision.GATE
        )

    def test_network_enabled_code_execution_gates(self) -> None:
        # {compute (allow), external_mutate (gate)} → gate (the most-restrictive).
        cats = frozenset({ActionCategory.COMPUTE, ActionCategory.EXTERNAL_MUTATE})
        assert DEFAULT_POLICY.decide_tool(cats) is CategoryDecision.GATE

    def test_deny_dominates_gate_and_allow(self) -> None:
        policy = CategoryPolicy(
            overrides=(
                CategoryRule(
                    category=ActionCategory.EXTERNAL_MUTATE, decision=CategoryDecision.DENY
                ),
            )
        )
        cats = frozenset({ActionCategory.COMPUTE, ActionCategory.EXTERNAL_MUTATE})
        assert policy.decide_tool(cats) is CategoryDecision.DENY

    def test_empty_categories_gates_defensively(self) -> None:
        # resolve_action_categories never returns empty, but a defensive call gates.
        assert DEFAULT_POLICY.decide_tool(frozenset()) is CategoryDecision.GATE


class TestContractCarriesPolicy:
    def test_contract_defaults_to_the_conservative_policy(self) -> None:
        from persona.tasks import Contract

        contract = Contract(goal="monitor the appeal")
        assert contract.category_policy == DEFAULT_POLICY
        assert contract.category_policy.decide(ActionCategory.SPEND) is CategoryDecision.GATE

    def test_contract_round_trips_a_policy_through_json(self) -> None:
        from persona.tasks import Contract

        policy = CategoryPolicy(
            overrides=(
                CategoryRule(category=ActionCategory.SPEND, decision=CategoryDecision.ALLOW),
            )
        )
        contract = Contract(goal="book under 1500kr", category_policy=policy)
        rebuilt = Contract.model_validate(contract.model_dump(mode="json"))
        assert rebuilt.category_policy.decide(ActionCategory.SPEND) is CategoryDecision.ALLOW
        assert rebuilt == contract
