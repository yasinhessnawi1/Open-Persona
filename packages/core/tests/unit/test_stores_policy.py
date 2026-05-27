"""Tests for ``persona.stores.policy``.

Pure-function tests that exercise the per-source policy table without
touching any storage backend. The four store-specific tables are tested
indirectly through ``test_stores_integration.py`` once a backend is wired.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.errors import PersonaSelfWriteForbiddenError, RuntimeWriteForbiddenError
from persona.schema.chunks import ChunkProvenance, PersonaChunk, WriteSource
from persona.stores.policy import (
    PersonaSelfRequirement,
    PolicyDecision,
    PolicyRule,
    PolicyTable,
    evaluate_write_policy,
)

UTC_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)


def _chunk(*, confidence: str | None = None, epistemic: str | None = None) -> PersonaChunk:
    metadata: dict[str, str] = {}
    if confidence is not None:
        metadata["confidence"] = confidence
    if epistemic is not None:
        metadata["epistemic"] = epistemic
    return PersonaChunk(
        id="c1",
        text="t",
        metadata=metadata,
        created_at=UTC_NOW,
        provenance=ChunkProvenance(source=WriteSource.USER, logical_id="lid", written_at=UTC_NOW),
    )


def _policy(rules: dict[WriteSource, PolicyRule]) -> PolicyTable:
    return rules


class TestAccept:
    def test_accept_short_circuits(self) -> None:
        policy = _policy({WriteSource.USER: PolicyRule(decision=PolicyDecision.ACCEPT)})
        # No error means accept.
        evaluate_write_policy(
            policy=policy,
            source=WriteSource.USER,
            force=False,
            chunks=[_chunk()],
            reason=None,
            store_kind="self_facts",
            persona_id="p",
        )


class TestReject:
    def test_user_reject_raises_runtime_write_forbidden(self) -> None:
        policy = _policy({WriteSource.USER: PolicyRule(decision=PolicyDecision.REJECT)})
        with pytest.raises(RuntimeWriteForbiddenError, match="rejected"):
            evaluate_write_policy(
                policy=policy,
                source=WriteSource.USER,
                force=True,  # force does not override REJECT
                chunks=[_chunk()],
                reason="anything",
                store_kind="identity",
                persona_id="p",
            )

    def test_persona_self_reject_raises_subclass(self) -> None:
        policy = _policy({WriteSource.PERSONA_SELF: PolicyRule(decision=PolicyDecision.REJECT)})
        with pytest.raises(PersonaSelfWriteForbiddenError):
            evaluate_write_policy(
                policy=policy,
                source=WriteSource.PERSONA_SELF,
                force=True,
                chunks=[_chunk()],
                reason="r",
                store_kind="identity",
                persona_id="p",
            )

    def test_unknown_source_raises(self) -> None:
        with pytest.raises(RuntimeWriteForbiddenError, match="no policy rule"):
            evaluate_write_policy(
                policy={},
                source=WriteSource.USER,
                force=True,
                chunks=[_chunk()],
                reason=None,
                store_kind="self_facts",
                persona_id="p",
            )


class TestForceOnly:
    def _ff_rule(self) -> PolicyRule:
        return PolicyRule(decision=PolicyDecision.FORCE_ONLY)

    def test_system_force_only_without_force_raises(self) -> None:
        policy = _policy({WriteSource.SYSTEM: self._ff_rule()})
        with pytest.raises(RuntimeWriteForbiddenError, match="force=True"):
            evaluate_write_policy(
                policy=policy,
                source=WriteSource.SYSTEM,
                force=False,
                chunks=[_chunk()],
                reason=None,
                store_kind="self_facts",
                persona_id="p",
            )

    def test_system_force_only_with_force_accepts(self) -> None:
        policy = _policy({WriteSource.SYSTEM: self._ff_rule()})
        evaluate_write_policy(
            policy=policy,
            source=WriteSource.SYSTEM,
            force=True,
            chunks=[_chunk()],
            reason=None,
            store_kind="self_facts",
            persona_id="p",
        )

    def test_persona_self_force_only_without_force_raises_subclass(self) -> None:
        policy = _policy({WriteSource.PERSONA_SELF: self._ff_rule()})
        with pytest.raises(PersonaSelfWriteForbiddenError, match="force=True"):
            evaluate_write_policy(
                policy=policy,
                source=WriteSource.PERSONA_SELF,
                force=False,
                chunks=[_chunk()],
                reason="r",
                store_kind="self_facts",
                persona_id="p",
            )


class TestPersonaSelfExtras:
    def _confidence_rule(self) -> PolicyRule:
        return PolicyRule(
            decision=PolicyDecision.FORCE_ONLY,
            persona_self_requirement=PersonaSelfRequirement.CONFIDENCE_THRESHOLD,
            persona_self_confidence_threshold=0.8,
            requires_reason=True,
        )

    def _epistemic_rule(self) -> PolicyRule:
        return PolicyRule(
            decision=PolicyDecision.FORCE_ONLY,
            persona_self_requirement=PersonaSelfRequirement.EPISTEMIC_TAG_SET,
            requires_reason=True,
        )

    def test_reason_required(self) -> None:
        policy = _policy({WriteSource.PERSONA_SELF: self._confidence_rule()})
        with pytest.raises(PersonaSelfWriteForbiddenError, match="reason"):
            evaluate_write_policy(
                policy=policy,
                source=WriteSource.PERSONA_SELF,
                force=True,
                chunks=[_chunk(confidence="0.9")],
                reason=None,
                store_kind="self_facts",
                persona_id="p",
            )

    def test_confidence_threshold_accepts_above(self) -> None:
        policy = _policy({WriteSource.PERSONA_SELF: self._confidence_rule()})
        evaluate_write_policy(
            policy=policy,
            source=WriteSource.PERSONA_SELF,
            force=True,
            chunks=[_chunk(confidence="0.9")],
            reason="learned",
            store_kind="self_facts",
            persona_id="p",
        )

    def test_confidence_threshold_rejects_below(self) -> None:
        policy = _policy({WriteSource.PERSONA_SELF: self._confidence_rule()})
        with pytest.raises(PersonaSelfWriteForbiddenError, match="below threshold"):
            evaluate_write_policy(
                policy=policy,
                source=WriteSource.PERSONA_SELF,
                force=True,
                chunks=[_chunk(confidence="0.5")],
                reason="learned",
                store_kind="self_facts",
                persona_id="p",
            )

    def test_confidence_missing_rejected(self) -> None:
        policy = _policy({WriteSource.PERSONA_SELF: self._confidence_rule()})
        with pytest.raises(PersonaSelfWriteForbiddenError, match="confidence metadata"):
            evaluate_write_policy(
                policy=policy,
                source=WriteSource.PERSONA_SELF,
                force=True,
                chunks=[_chunk()],
                reason="learned",
                store_kind="self_facts",
                persona_id="p",
            )

    def test_confidence_not_a_number_rejected(self) -> None:
        policy = _policy({WriteSource.PERSONA_SELF: self._confidence_rule()})
        with pytest.raises(PersonaSelfWriteForbiddenError, match="not a number"):
            evaluate_write_policy(
                policy=policy,
                source=WriteSource.PERSONA_SELF,
                force=True,
                chunks=[_chunk(confidence="high")],
                reason="learned",
                store_kind="self_facts",
                persona_id="p",
            )

    def test_epistemic_tag_required(self) -> None:
        policy = _policy({WriteSource.PERSONA_SELF: self._epistemic_rule()})
        with pytest.raises(PersonaSelfWriteForbiddenError, match="epistemic"):
            evaluate_write_policy(
                policy=policy,
                source=WriteSource.PERSONA_SELF,
                force=True,
                chunks=[_chunk()],
                reason="r",
                store_kind="worldview",
                persona_id="p",
            )

    def test_epistemic_tag_accepted_when_set(self) -> None:
        policy = _policy({WriteSource.PERSONA_SELF: self._epistemic_rule()})
        evaluate_write_policy(
            policy=policy,
            source=WriteSource.PERSONA_SELF,
            force=True,
            chunks=[_chunk(epistemic="belief")],
            reason="r",
            store_kind="worldview",
            persona_id="p",
        )

    def test_extras_skipped_for_system_writes(self) -> None:
        """System writes don't go through persona_self extras even when the rule has them."""
        rule = self._confidence_rule()
        policy = _policy({WriteSource.SYSTEM: rule})
        # No confidence metadata; should still accept because system writes
        # bypass persona_self-specific checks.
        evaluate_write_policy(
            policy=policy,
            source=WriteSource.SYSTEM,
            force=True,
            chunks=[_chunk()],
            reason=None,
            store_kind="self_facts",
            persona_id="p",
        )
