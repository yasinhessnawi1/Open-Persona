"""T1 — the graph-knowledge context value types (K3-D-1, K3-D-4).

The pure shapes the PromptBuilder will inject: a light, user-facing projection
of a retrieved graph node (``GraphKnowledgeItem``), a coarse age bucket
(``GraphRecency``), and the per-turn bundle (``GraphContext``). These carry only
what a persona needs to *use* shared knowledge well — never a metadata dump that
invites performing it (D-K3-4). The injection gate, the projection from
``HybridResult``, and the rendering live in later tasks; this task is the shape.
"""

from __future__ import annotations

import pytest
from persona_runtime.prompt import GraphContext, GraphKnowledgeItem, GraphRecency
from pydantic import ValidationError


class TestGraphRecency:
    def test_the_three_coarse_buckets(self) -> None:
        # Light by design: enough to frame old knowledge tentatively, never a
        # timestamp. Exactly three buckets, string-valued (StrEnum).
        assert {r.value for r in GraphRecency} == {"recent", "a_while_back", "long_ago"}

    def test_is_string_valued(self) -> None:
        assert GraphRecency.RECENT == "recent"


class TestGraphKnowledgeItem:
    def _minimal(self, **overrides: object) -> GraphKnowledgeItem:
        base: dict[str, object] = {
            "concept_name": "focus struggles",
            "content": "Finds it hard to concentrate in long study sessions.",
            "recency": GraphRecency.A_WHILE_BACK,
        }
        base.update(overrides)
        return GraphKnowledgeItem(**base)  # type: ignore[arg-type]

    def test_minimal_construction_defaults_provenance_to_none(self) -> None:
        item = self._minimal()
        assert item.concept_name == "focus struggles"
        assert item.recency is GraphRecency.A_WHILE_BACK
        # The provenance/category/relevance facets are all optional — a node may
        # carry none of them (user/system-sourced, untagged, sparse-only).
        assert item.source_persona is None
        assert item.source_interaction is None
        assert item.wellbeing_category is None
        assert item.relevance is None

    def test_carries_the_full_light_shape(self) -> None:
        item = self._minimal(
            source_persona="kai",
            source_interaction="conv-123",
            wellbeing_category=None,
            relevance=0.86,
        )
        assert item.source_persona == "kai"
        assert item.source_interaction == "conv-123"
        assert item.relevance == pytest.approx(0.86)

    def test_relevance_is_none_for_the_sparse_only_fallback(self) -> None:
        # Sparse-only nodes have no embedding distance — relevance stays None,
        # the signal that this item entered via the top-rank fallback (D-K3-3).
        item = self._minimal(relevance=None)
        assert item.relevance is None

    def test_is_frozen(self) -> None:
        item = self._minimal()
        with pytest.raises(ValidationError):
            item.content = "mutated"  # type: ignore[misc]

    def test_forbids_extra_fields(self) -> None:
        # No metadata dump leaking in through a stray field (D-K3-4 discipline).
        with pytest.raises(ValidationError):
            self._minimal(persona_id="oops")

    def test_recency_is_required(self) -> None:
        with pytest.raises(ValidationError):
            GraphKnowledgeItem(  # type: ignore[call-arg]
                concept_name="x", content="y"
            )


class TestGraphContext:
    def test_defaults_to_an_empty_bundle(self) -> None:
        # Zero-graph: an empty bundle renders nothing → byte-identical Phase-1
        # prompt (criterion 9). The empty default is the invisible path.
        ctx = GraphContext()
        assert ctx.items == ()

    def test_holds_items_as_an_immutable_tuple(self) -> None:
        item = GraphKnowledgeItem(
            concept_name="vegetarian",
            content="Eats vegetarian.",
            recency=GraphRecency.RECENT,
        )
        ctx = GraphContext(items=(item,))
        assert ctx.items == (item,)

    def test_is_frozen(self) -> None:
        ctx = GraphContext()
        with pytest.raises(ValidationError):
            ctx.items = ()  # type: ignore[misc]

    def test_forbids_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            GraphContext(guidance="not here")  # type: ignore[call-arg]
