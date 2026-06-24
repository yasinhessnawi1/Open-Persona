"""Lenient parsing of the extractor's JSON output → ExtractionCandidates (Spec K2, T2).

The parser is the floor-model-friendly half of the structured-output contract
(no provider ``response_format`` dependency, D-10-1): the model returns JSON text,
this parses it. It is LENIENT and NEVER raises — a malformed candidate is dropped,
unparseable output yields an empty tuple (better to extract nothing than crash an
off-critical-path synthesis pass). The grounding invariant is enforced here too: a
candidate with no usable ``evidence_span`` cannot be constructed, so it is dropped.
"""

from __future__ import annotations

from persona.extraction import ExtractionCandidate
from persona.graph.models import LinkType, NodeKind
from persona.graph.protocol import UpdateIntent
from persona.wellbeing import WellbeingCategory
from persona_runtime.extraction.parse import parse_candidates


def test_parses_a_well_formed_object() -> None:
    text = """{"candidates": [
        {"concept_name": "vegetarian diet", "content": "The user is vegetarian.",
         "node_kind": "preference", "evidence_span": "I'm vegetarian now",
         "entity_mentions": [], "wellbeing_category": null,
         "update_intent": "none", "update_target_hint": null}
    ]}"""
    out = parse_candidates(text)
    assert len(out) == 1
    c = out[0]
    assert isinstance(c, ExtractionCandidate)
    assert c.concept_name == "vegetarian diet"
    assert c.node_kind is NodeKind.PREFERENCE
    assert c.evidence_span == "I'm vegetarian now"


def test_strips_markdown_code_fences() -> None:
    text = (
        '```json\n{"candidates": [{"concept_name": "c", "content": "d", '
        '"node_kind": "fact", "evidence_span": "said so"}]}\n```'
    )
    out = parse_candidates(text)
    assert len(out) == 1
    assert out[0].concept_name == "c"


def test_empty_or_non_json_yields_no_candidates() -> None:
    assert parse_candidates("") == ()
    assert parse_candidates("I could not find any durable facts.") == ()
    assert parse_candidates("{not json") == ()


def test_object_without_candidates_key_yields_empty() -> None:
    assert parse_candidates('{"notes": "nothing here"}') == ()


def test_explicit_empty_candidates_list() -> None:
    assert parse_candidates('{"candidates": []}') == ()


def test_a_bare_top_level_array_is_accepted() -> None:
    text = '[{"concept_name": "c", "content": "d", "node_kind": "fact", "evidence_span": "x"}]'
    out = parse_candidates(text)
    assert len(out) == 1


def test_a_candidate_missing_evidence_span_is_dropped_others_kept() -> None:
    # The grounding invariant: no quotable basis → not a candidate (criterion 5).
    text = """{"candidates": [
        {"concept_name": "ungrounded", "content": "invented", "node_kind": "fact"},
        {"concept_name": "grounded", "content": "real", "node_kind": "fact",
         "evidence_span": "the user said it"}
    ]}"""
    out = parse_candidates(text)
    assert [c.concept_name for c in out] == ["grounded"]


def test_an_invalid_node_kind_drops_that_candidate() -> None:
    text = """{"candidates": [
        {"concept_name": "x", "content": "y", "node_kind": "not_a_kind",
         "evidence_span": "z"}
    ]}"""
    assert parse_candidates(text) == ()


def test_wellbeing_category_maps_to_the_enum() -> None:
    text = """{"candidates": [
        {"concept_name": "x", "content": "y", "node_kind": "circumstance",
         "evidence_span": "z", "wellbeing_category": "disordered_eating"}
    ]}"""
    out = parse_candidates(text)
    assert out[0].wellbeing_category is WellbeingCategory.DISORDERED_EATING


def test_an_invalid_wellbeing_category_falls_back_to_untagged_not_dropped() -> None:
    # A bad tag must not lose grounded knowledge — keep the candidate, untagged.
    text = """{"candidates": [
        {"concept_name": "x", "content": "y", "node_kind": "fact",
         "evidence_span": "z", "wellbeing_category": "made_up_category"}
    ]}"""
    out = parse_candidates(text)
    assert len(out) == 1
    assert out[0].wellbeing_category is None


def test_update_intent_and_target_hint_map() -> None:
    text = """{"candidates": [
        {"concept_name": "job", "content": "no longer at X", "node_kind": "circumstance",
         "evidence_span": "I left that job", "update_intent": "contradict",
         "update_target_hint": "works at X"}
    ]}"""
    out = parse_candidates(text)
    assert out[0].update_intent is UpdateIntent.CONTRADICT
    assert out[0].update_target_hint == "works at X"


def test_entity_mentions_become_entity_mention_models() -> None:
    text = """{"candidates": [
        {"concept_name": "doctor", "content": "sees Dr. Hansen", "node_kind": "fact",
         "evidence_span": "my doctor, Dr. Hansen", "entity_mentions": ["my doctor", "Dr. Hansen"]}
    ]}"""
    out = parse_candidates(text)
    assert [m.surface for m in out[0].entity_mentions] == ["my doctor", "Dr. Hansen"]


def test_non_string_entity_mentions_are_skipped() -> None:
    text = """{"candidates": [
        {"concept_name": "c", "content": "d", "node_kind": "fact", "evidence_span": "x",
         "entity_mentions": ["ok", 5, null, {"surface": "nope"}]}
    ]}"""
    out = parse_candidates(text)
    assert [m.surface for m in out[0].entity_mentions] == ["ok"]


# --- proposed_relations (T4: temporal/causal assertion) ---


def test_temporal_and_causal_relations_are_parsed() -> None:
    text = """{"candidates": [
        {"concept_name": "left Acme", "content": "user left their job at Acme",
         "node_kind": "circumstance", "evidence_span": "I left Acme because I burned out",
         "proposed_relations": [
            {"target_concept": "burnout", "link_type": "causal", "reason": "stated cause"},
            {"target_concept": "new job", "link_type": "temporal", "reason": "then"}
         ]}
    ]}"""
    rels = parse_candidates(text)[0].proposed_relations
    assert {(r.target_concept, r.link_type) for r in rels} == {
        ("burnout", LinkType.CAUSAL),
        ("new job", LinkType.TEMPORAL),
    }


def test_non_k2_link_types_are_dropped_candidate_kept() -> None:
    # The model must not smuggle SEMANTIC/ENTITY through proposed_relations.
    text = """{"candidates": [
        {"concept_name": "c", "content": "d", "node_kind": "fact", "evidence_span": "x",
         "proposed_relations": [
            {"target_concept": "a", "link_type": "semantic"},
            {"target_concept": "b", "link_type": "entity"},
            {"target_concept": "ok", "link_type": "temporal"}
         ]}
    ]}"""
    rels = parse_candidates(text)[0].proposed_relations
    assert [(r.target_concept, r.link_type) for r in rels] == [("ok", LinkType.TEMPORAL)]


def test_relation_missing_target_concept_is_dropped() -> None:
    text = """{"candidates": [
        {"concept_name": "c", "content": "d", "node_kind": "fact", "evidence_span": "x",
         "proposed_relations": [
            {"link_type": "causal"},
            {"target_concept": "", "link_type": "temporal"},
            {"target_concept": "kept", "link_type": "causal"}
         ]}
    ]}"""
    rels = parse_candidates(text)[0].proposed_relations
    assert [r.target_concept for r in rels] == ["kept"]
