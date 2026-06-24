"""The pre-resolution extraction boundary shapes (Spec K2, T1; D-K2-4).

``ExtractionCandidate`` is what the RUNTIME extractor produces and the K2
orchestrator maps into K0's ``KnowledgeCandidate`` (resolving mentions → entity
ids, mapping the evidence span → ``NodeProvenance.grounding``). The contracts
live in persona-CORE; the LLM that fills them lives in runtime (the ratified
layering split). These tests pin the frozen-boundary discipline + the grounding
invariant (a candidate with no evidence span is rejected at construction — the
structural half of the grounded-extraction safety bar).
"""

from __future__ import annotations

import pytest
from persona.extraction import (
    EntityMention,
    ExtractionCandidate,
    ExtractionInput,
    InteractionKind,
    ProposedRelation,
)
from persona.graph.models import LinkType, NodeKind
from persona.graph.protocol import UpdateIntent
from persona.wellbeing import WellbeingCategory
from pydantic import ValidationError

# --- InteractionKind: the channel-agnostic discriminator (matches the migration CHECK) ---


def test_interaction_kind_values_match_the_synthesis_markers_check() -> None:
    # synthesis_markers.interaction_kind CHECK IN ('conversation','agentic_run','voice').
    assert {k.value for k in InteractionKind} == {"conversation", "agentic_run", "voice"}


# --- EntityMention ---


def test_entity_mention_is_frozen_and_forbids_extra() -> None:
    m = EntityMention(surface="Dr. Hansen")
    with pytest.raises(ValidationError):
        m.surface = "x"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        EntityMention(surface="x", unexpected="y")  # type: ignore[call-arg]


def test_entity_mention_surface_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        EntityMention(surface="")


# --- ExtractionCandidate ---


def _candidate(**overrides: object) -> ExtractionCandidate:
    base: dict[str, object] = {
        "concept_name": "vegetarian diet",
        "content": "The user follows a vegetarian diet.",
        "node_kind": NodeKind.PREFERENCE,
        "evidence_span": "I'm vegetarian now",
    }
    base.update(overrides)
    return ExtractionCandidate(**base)  # type: ignore[arg-type]


def test_candidate_is_frozen_and_forbids_extra() -> None:
    c = _candidate()
    with pytest.raises(ValidationError):
        c.content = "mutated"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _candidate(unexpected="y")


def test_candidate_requires_a_non_empty_evidence_span() -> None:
    # Grounding is structural: no quotable basis → not a candidate. This is the
    # construction-time half of criterion 5 (the runtime eval is the other half).
    with pytest.raises(ValidationError):
        ExtractionCandidate(
            concept_name="x",
            content="y",
            node_kind=NodeKind.FACT,
            evidence_span="",
        )
    with pytest.raises(ValidationError):
        ExtractionCandidate(  # type: ignore[call-arg]
            concept_name="x",
            content="y",
            node_kind=NodeKind.FACT,
        )


def test_candidate_defaults_are_conservative() -> None:
    c = _candidate()
    assert c.entity_mentions == ()
    assert c.wellbeing_category is None
    assert c.update_intent is UpdateIntent.NONE
    assert c.update_target_hint is None


def test_candidate_carries_resolved_to_be_entity_mentions() -> None:
    c = _candidate(entity_mentions=(EntityMention(surface="my doctor"),))
    assert c.entity_mentions[0].surface == "my doctor"


def test_candidate_accepts_a_wellbeing_tag_from_the_enum() -> None:
    c = _candidate(wellbeing_category=WellbeingCategory.DISORDERED_EATING)
    assert c.wellbeing_category is WellbeingCategory.DISORDERED_EATING


def test_candidate_carries_update_intent_for_contradictions() -> None:
    c = _candidate(update_intent=UpdateIntent.CONTRADICT, update_target_hint="works at X")
    assert c.update_intent is UpdateIntent.CONTRADICT
    assert c.update_target_hint == "works at X"


# --- ProposedRelation (T4): the K2-asserted temporal/causal links ---


def test_proposed_relation_is_frozen_and_forbids_extra() -> None:
    r = ProposedRelation(target_concept="burnout", link_type=LinkType.CAUSAL)
    with pytest.raises(ValidationError):
        r.reason = "x"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ProposedRelation(target_concept="x", link_type=LinkType.TEMPORAL, unexpected="y")  # type: ignore[call-arg]


def test_proposed_relation_target_concept_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        ProposedRelation(target_concept="", link_type=LinkType.TEMPORAL)


@pytest.mark.parametrize("link_type", [LinkType.SEMANTIC, LinkType.ENTITY])
def test_k2_may_only_assert_temporal_or_causal_links(link_type: LinkType) -> None:
    # SEMANTIC is K0's automatic baseline; ENTITY falls out of resolution. K2
    # asserts ONLY temporal/causal (D-K0-8) — others are rejected at the boundary.
    with pytest.raises(ValidationError):
        ProposedRelation(target_concept="x", link_type=link_type)


@pytest.mark.parametrize("link_type", [LinkType.TEMPORAL, LinkType.CAUSAL])
def test_temporal_and_causal_are_accepted(link_type: LinkType) -> None:
    r = ProposedRelation(target_concept="x", link_type=link_type, reason="stated")
    assert r.link_type is link_type


def test_candidate_proposed_relations_default_empty() -> None:
    assert _candidate().proposed_relations == ()


def test_candidate_carries_proposed_relations() -> None:
    rel = ProposedRelation(
        target_concept="burnout", link_type=LinkType.CAUSAL, reason="left job because of burnout"
    )
    c = _candidate(proposed_relations=(rel,))
    assert c.proposed_relations[0].target_concept == "burnout"
    assert c.proposed_relations[0].link_type is LinkType.CAUSAL


# --- ExtractionInput ---


def test_extraction_input_is_frozen_and_carries_provenance_material() -> None:
    inp = ExtractionInput(
        interaction_kind=InteractionKind.CONVERSATION,
        interaction_id="conv_123",
        persona_id="persona_a",
        content="full windowed text",
    )
    assert inp.interaction_kind is InteractionKind.CONVERSATION
    assert inp.interaction_id == "conv_123"
    assert inp.persona_id == "persona_a"
    with pytest.raises(ValidationError):
        inp.content = "mutated"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ExtractionInput(  # type: ignore[call-arg]
            interaction_kind=InteractionKind.VOICE,
            interaction_id="s1",
            persona_id="p",
            content="c",
            unexpected="y",
        )
