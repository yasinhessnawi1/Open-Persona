"""Tests for ``persona.autonomy`` — spec 21 T02 (AutonomyLevel / AutonomyPolicy / resolve).

Covers the per-level policy table (D-21-5 caps + spec §2.1 gating), the
class-C-never / class-D-always invariants (D-21-19), and the load-time resolver
(D-21-8 / D-21-11): YAML default when no learning chain exists, head-version
override when it does, and fail-loud on a corrupted chain head.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import get_args

import pytest
from persona.autonomy import (
    AUTONOMY_LEVELS,
    AUTONOMY_LOGICAL_ID,
    AUTONOMY_METADATA_KEY,
    AUTONOMY_POLICIES,
    AmbiguityClass,
    AutonomyPolicy,
    policy_for,
    resolve_autonomy,
)
from persona.errors import InvalidAutonomyLevelError
from persona.schema.chunks import ChunkProvenance, PersonaChunk, WriteSource
from persona.schema.persona import Persona
from pydantic import ValidationError

LEVELS = ["cautious", "balanced", "decisive"]


def _persona(level: str = "cautious") -> Persona:
    return Persona.model_validate(
        {
            "schema_version": "1.0",
            "identity": {"name": "n", "role": "r", "background": "b"},
            "autonomy": level,
        },
    )


def _autonomy_chunk(
    level: str,
    *,
    version: int,
    superseded_by: str | None,
    chunk_id: str,
) -> PersonaChunk:
    """Build one self_facts chunk in the autonomy learning chain."""
    return PersonaChunk(
        id=chunk_id,
        text=f"Autonomy preference set to {level}.",
        metadata={AUTONOMY_METADATA_KEY: level, "confidence": "0.9"},
        created_at=datetime.now(UTC),
        provenance=ChunkProvenance(
            source=WriteSource.PERSONA_SELF,
            logical_id=AUTONOMY_LOGICAL_ID,
            version=version,
            superseded_by=superseded_by,
            written_at=datetime.now(UTC),
            written_by="frontier:test",
            reason="learned",
        ),
    )


class TestTypeAliasReconciliation:
    def test_autonomy_levels_match_schema_field(self) -> None:
        """``AUTONOMY_LEVELS`` must equal ``Persona.autonomy``'s Literal args (D-21-11).

        Schema is the lower layer and cannot import this module, so the values
        are duplicated; this test is the drift guard.
        """
        field_args = get_args(Persona.model_fields["autonomy"].annotation)
        assert frozenset(field_args) == AUTONOMY_LEVELS

    def test_levels_constant_is_exactly_three(self) -> None:
        assert frozenset({"cautious", "balanced", "decisive"}) == AUTONOMY_LEVELS


class TestAutonomyPolicyTable:
    @pytest.mark.parametrize("level", LEVELS)
    def test_policy_for_returns_matching_level(self, level: str) -> None:
        policy = policy_for(level)  # type: ignore[arg-type]
        assert policy.level == level

    def test_per_run_caps_match_d_21_5(self) -> None:
        assert policy_for("cautious").questions_per_run == 5
        assert policy_for("balanced").questions_per_run == 3
        assert policy_for("decisive").questions_per_run == 1

    @pytest.mark.parametrize("level", LEVELS)
    def test_per_turn_cap_is_one_for_every_level(self, level: str) -> None:
        assert policy_for(level).questions_per_turn == 1  # type: ignore[arg-type]

    def test_gating_matches_spec_2_1_table(self) -> None:
        assert policy_for("cautious").asks_on == frozenset(
            {
                AmbiguityClass.MISSING_PARAMETER,
                AmbiguityClass.VAGUE_SCOPE,
                AmbiguityClass.SAFETY_CRITICAL_GAP,
            }
        )
        assert policy_for("balanced").asks_on == frozenset(
            {AmbiguityClass.MISSING_PARAMETER, AmbiguityClass.SAFETY_CRITICAL_GAP}
        )
        assert policy_for("decisive").asks_on == frozenset({AmbiguityClass.SAFETY_CRITICAL_GAP})

    @pytest.mark.parametrize("level", LEVELS)
    def test_class_d_bypasses_gate_at_every_level(self, level: str) -> None:
        """SAFETY_CRITICAL_GAP always escalates regardless of autonomy (D-21-1)."""
        assert AmbiguityClass.SAFETY_CRITICAL_GAP in policy_for(level).asks_on  # type: ignore[arg-type]

    @pytest.mark.parametrize("level", LEVELS)
    def test_class_c_never_triggers_at_any_level(self, level: str) -> None:
        """CONFLICTING_CONSTRAINTS is detect-and-log only in v0.1 (D-21-19)."""
        assert (
            AmbiguityClass.CONFLICTING_CONSTRAINTS not in policy_for(level).asks_on  # type: ignore[arg-type]
        )

    def test_policy_for_unknown_level_raises(self) -> None:
        with pytest.raises(InvalidAutonomyLevelError, match="unknown autonomy level"):
            policy_for("aggressive")  # type: ignore[arg-type]

    def test_policy_is_frozen(self) -> None:
        policy = policy_for("cautious")
        with pytest.raises(ValidationError):
            policy.questions_per_run = 99  # type: ignore[misc]

    def test_policy_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            AutonomyPolicy.model_validate(
                {
                    "level": "cautious",
                    "questions_per_turn": 1,
                    "questions_per_run": 5,
                    "asks_on": [],
                    "bogus": True,
                }
            )

    def test_table_covers_all_levels(self) -> None:
        assert set(AUTONOMY_POLICIES) == AUTONOMY_LEVELS


class TestResolveAutonomy:
    @pytest.mark.parametrize("level", LEVELS)
    def test_no_chain_returns_yaml_default(self, level: str) -> None:
        assert resolve_autonomy(_persona(level), []) == level

    def test_no_autonomy_chunk_among_other_self_facts_returns_default(self) -> None:
        """A self_facts chunk under a *different* logical_id does not override."""
        other = PersonaChunk(
            id="p::self_facts::0001",
            text="user's landlord is named Bjørn",
            metadata={"confidence": "0.9"},
            created_at=datetime.now(UTC),
            provenance=ChunkProvenance(
                source=WriteSource.PERSONA_SELF,
                logical_id="p::self_facts::0001",
                version=1,
                written_at=datetime.now(UTC),
            ),
        )
        assert resolve_autonomy(_persona("cautious"), [other]) == "cautious"

    def test_head_version_overrides_yaml_default(self) -> None:
        head = _autonomy_chunk(
            "decisive", version=1, superseded_by=None, chunk_id="p::self_facts::0001"
        )
        assert resolve_autonomy(_persona("cautious"), [head]) == "decisive"

    def test_current_head_wins_over_superseded(self) -> None:
        """A two-version chain resolves to the non-superseded head (D-21-8)."""
        v1 = _autonomy_chunk(
            "balanced",
            version=1,
            superseded_by="p::self_facts::0002",
            chunk_id="p::self_facts::0001",
        )
        v2 = _autonomy_chunk(
            "decisive", version=2, superseded_by=None, chunk_id="p::self_facts::0002"
        )
        assert resolve_autonomy(_persona("cautious"), [v1, v2]) == "decisive"

    def test_rolled_back_head_resolves_to_restored_level(self) -> None:
        """A rollback appends a new head; resolution follows the live head."""
        v1 = _autonomy_chunk(
            "decisive",
            version=1,
            superseded_by="p::self_facts::0002",
            chunk_id="p::self_facts::0001",
        )
        v2 = _autonomy_chunk(
            "balanced",
            version=2,
            superseded_by="p::self_facts::0003",
            chunk_id="p::self_facts::0002",
        )
        # rollback to v1's level appended as v3 (append-only rollback)
        v3 = _autonomy_chunk(
            "decisive", version=3, superseded_by=None, chunk_id="p::self_facts::0003"
        )
        assert resolve_autonomy(_persona("cautious"), [v1, v2, v3]) == "decisive"

    def test_invalid_level_in_head_metadata_raises(self) -> None:
        bad = _autonomy_chunk(
            "balanced", version=1, superseded_by=None, chunk_id="p::self_facts::0001"
        )
        bad = bad.model_copy(
            update={"metadata": {AUTONOMY_METADATA_KEY: "reckless", "confidence": "0.9"}}
        )
        with pytest.raises(InvalidAutonomyLevelError, match="invalid level"):
            resolve_autonomy(_persona("cautious"), [bad])

    def test_missing_level_metadata_key_raises(self) -> None:
        bad = _autonomy_chunk(
            "balanced", version=1, superseded_by=None, chunk_id="p::self_facts::0001"
        )
        bad = bad.model_copy(update={"metadata": {"confidence": "0.9"}})
        with pytest.raises(InvalidAutonomyLevelError):
            resolve_autonomy(_persona("cautious"), [bad])
