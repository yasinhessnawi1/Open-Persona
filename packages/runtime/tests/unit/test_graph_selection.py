"""T2 — the relevance gate + light projection (K3-D-2, K3-D-3, K3-D-4).

Deterministic unit coverage of ``select_graph_knowledge``: the dense-similarity
floor, the narrow sparse-only fallback, the injection node budget, and the
HybridResult → GraphKnowledgeItem projection (recency bucket, origin source,
carried wellbeing tag). The floor *value* is validated separately against real
bge-small vectors (the calibration sweep, an external test); here the gate
*mechanism* is pinned with synthetic distances so it is fast and exact.

Stuffing / starving / small-talk are the three named cases (criterion 3):
- starving: a substantive turn pulls the node that bears on it (high similarity);
- stuffing: loosely-related nodes (sub-floor similarity) are NOT injected;
- small-talk: nothing clears the gate → an empty bundle.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from persona.graph.config import GraphSettings
from persona.graph.fusion import HybridResult
from persona.graph.models import ConceptNode, NodeKind, NodeProvenance
from persona.schema.chunks import WriteSource
from persona_runtime.graph_selection import select_graph_knowledge
from persona_runtime.prompt import GraphContext, GraphRecency

_NOW = datetime(2026, 6, 25, tzinfo=UTC)


def _node(
    *,
    node_id: str = "n1",
    concept_name: str = "focus struggles",
    content: str = "Finds long study sessions hard to concentrate through.",
    distance: float | None = None,
    wellbeing_category: str | None = None,
    learned_at: datetime | None = None,
    persona_id: str | None = "kai",
    interaction_id: str | None = "conv-1",
) -> ConceptNode:
    written = learned_at if learned_at is not None else _NOW
    return ConceptNode(
        id=node_id,
        node_kind=NodeKind.CONCEPT,
        concept_name=concept_name,
        content=content,
        wellbeing_category=wellbeing_category,
        distance=distance,
        provenance=(
            NodeProvenance(
                source=WriteSource.PERSONA_SELF,
                persona_id=persona_id,
                interaction_id=interaction_id,
                written_at=written,
            ),
        ),
        created_at=written,
    )


def _result(
    node: ConceptNode,
    *,
    rank: int = 1,
    dense_rank: int | None = 1,
    sparse_rank: int | None = None,
    via_traversal: bool = False,
) -> HybridResult:
    return HybridResult(
        node=node,
        score=0.5,
        rank=rank,
        dense_rank=dense_rank,
        sparse_rank=sparse_rank,
        via_traversal=via_traversal,
    )


def _settings(**overrides: object) -> GraphSettings:
    base: dict[str, object] = {"inject_similarity_floor": 0.80, "inject_sparse_rank_cap": 3}
    base.update(overrides)
    return GraphSettings(**base)  # type: ignore[arg-type]


class TestDenseGate:
    def test_starving_a_relevant_node_clears_the_floor(self) -> None:
        # similarity = 1 - 0.10 = 0.90 >= 0.80 → injected.
        ctx = select_graph_knowledge(
            [_result(_node(distance=0.10))], settings=_settings(), now=_NOW
        )
        assert len(ctx.items) == 1
        assert ctx.items[0].relevance == 0.90

    def test_stuffing_a_loosely_related_node_is_dropped(self) -> None:
        # similarity = 1 - 0.35 = 0.65 < 0.80 → not injected (the anti-stuffing guard).
        ctx = select_graph_knowledge(
            [_result(_node(distance=0.35))], settings=_settings(), now=_NOW
        )
        assert ctx.items == ()

    def test_floor_is_inclusive(self) -> None:
        # similarity exactly at the floor injects (>= , matching sweep semantics).
        ctx = select_graph_knowledge(
            [_result(_node(distance=0.20))], settings=_settings(), now=_NOW
        )
        assert len(ctx.items) == 1

    def test_never_gates_on_rrf_score(self) -> None:
        # A high RRF score must NOT rescue a sub-floor node (the whole D-K3-3 point).
        node = _node(distance=0.50)  # similarity 0.50, well under the floor
        result = HybridResult(node=node, score=999.0, rank=1, dense_rank=1)
        ctx = select_graph_knowledge([result], settings=_settings(), now=_NOW)
        assert ctx.items == ()


class TestSparseOnlyFallback:
    def test_top_rank_exact_term_hit_injects(self) -> None:
        # No distance (sparse-only), sparse_rank 2 <= cap 3 → injected.
        node = _node(distance=None, concept_name="metformin")
        ctx = select_graph_knowledge(
            [_result(node, dense_rank=None, sparse_rank=2)], settings=_settings(), now=_NOW
        )
        assert len(ctx.items) == 1
        assert ctx.items[0].relevance is None  # no embedding distance

    def test_below_rank_cap_sparse_hit_is_dropped(self) -> None:
        node = _node(distance=None)
        ctx = select_graph_knowledge(
            [_result(node, dense_rank=None, sparse_rank=4)], settings=_settings(), now=_NOW
        )
        assert ctx.items == ()

    def test_pure_traversal_node_with_no_signal_is_not_injected(self) -> None:
        # via_traversal, no distance, no sparse_rank → no relevance reading → the
        # exact loosely-related node the gate keeps out (criterion 3).
        node = _node(distance=None)
        ctx = select_graph_knowledge(
            [_result(node, dense_rank=None, sparse_rank=None, via_traversal=True)],
            settings=_settings(),
            now=_NOW,
        )
        assert ctx.items == ()


class TestSmallTalk:
    def test_nothing_clears_the_gate_yields_empty_bundle(self) -> None:
        results = [
            _result(_node(node_id="a", distance=0.40)),
            _result(_node(node_id="b", distance=None), dense_rank=None, sparse_rank=9),
        ]
        ctx = select_graph_knowledge(results, settings=_settings(), now=_NOW)
        assert ctx == GraphContext()

    def test_no_results_yields_empty_bundle(self) -> None:
        assert select_graph_knowledge([], settings=_settings(), now=_NOW) == GraphContext()


class TestBudget:
    def test_truncates_to_the_injection_budget_preserving_k1_rank_order(self) -> None:
        results = [_result(_node(node_id=f"n{i}", distance=0.05), rank=i) for i in range(1, 6)]
        ctx = select_graph_knowledge(results, settings=_settings(), now=_NOW, max_items=3)
        assert len(ctx.items) == 3

    def test_max_items_zero_injects_nothing(self) -> None:
        ctx = select_graph_knowledge(
            [_result(_node(distance=0.05))], settings=_settings(), now=_NOW, max_items=0
        )
        assert ctx.items == ()

    def test_defaults_to_the_k1_result_budget(self) -> None:
        results = [_result(_node(node_id=f"n{i}", distance=0.05), rank=i) for i in range(1, 15)]
        ctx = select_graph_knowledge(results, settings=_settings(result_budget=10), now=_NOW)
        assert len(ctx.items) == 10


class TestProjection:
    def test_recency_buckets_from_the_latest_contribution(self) -> None:
        recent = select_graph_knowledge(
            [_result(_node(distance=0.05, learned_at=_NOW - timedelta(days=3)))],
            settings=_settings(),
            now=_NOW,
        ).items[0]
        while_back = select_graph_knowledge(
            [_result(_node(distance=0.05, learned_at=_NOW - timedelta(days=60)))],
            settings=_settings(),
            now=_NOW,
        ).items[0]
        long_ago = select_graph_knowledge(
            [_result(_node(distance=0.05, learned_at=_NOW - timedelta(days=400)))],
            settings=_settings(),
            now=_NOW,
        ).items[0]
        assert recent.recency is GraphRecency.RECENT
        assert while_back.recency is GraphRecency.A_WHILE_BACK
        assert long_ago.recency is GraphRecency.LONG_AGO

    def test_source_comes_from_provenance_for_honest_attribution(self) -> None:
        item = select_graph_knowledge(
            [_result(_node(distance=0.05, persona_id="kai", interaction_id="conv-9"))],
            settings=_settings(),
            now=_NOW,
        ).items[0]
        assert item.source_persona == "kai"
        assert item.source_interaction == "conv-9"

    def test_user_sourced_node_has_no_persona_attribution(self) -> None:
        item = select_graph_knowledge(
            [_result(_node(distance=0.05, persona_id=None, interaction_id=None))],
            settings=_settings(),
            now=_NOW,
        ).items[0]
        assert item.source_persona is None

    def test_wellbeing_category_is_carried_for_k4_routing(self) -> None:
        item = select_graph_knowledge(
            [_result(_node(distance=0.05, wellbeing_category="eating"))],
            settings=_settings(),
            now=_NOW,
        ).items[0]
        assert item.wellbeing_category == "eating"

    def test_content_and_name_are_copied_verbatim(self) -> None:
        item = select_graph_knowledge(
            [_result(_node(distance=0.05, concept_name="vegetarian", content="Eats veg."))],
            settings=_settings(),
            now=_NOW,
        ).items[0]
        assert item.concept_name == "vegetarian"
        assert item.content == "Eats veg."
