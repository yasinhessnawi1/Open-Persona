"""The K4 context gate + allowlist provider (Spec K4, T4; K4-D-2).

Proves the resolved gate shape: gate only the gate-eligible crisis/trauma categories,
lift when the user opens the topic in **the query OR the recent window** (not query-
alone — the uncanny re-closing failure), strengthen the gate for historical material,
build ``owner_nodes − gated`` (Option A) only when something gates, and return ``None``
on the common path (nothing flagged or nothing gated). Also proves the
``make_graph_retrieval`` seam builds the :class:`GatingContext` from the query + the
recent-window provider.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from persona.graph.config import GraphSettings
from persona.wellbeing import WellbeingCategory
from persona.wellbeing_policy import RecencyBand
from persona_runtime.graph_selection import GatingContext, make_graph_retrieval
from persona_runtime.wellbeing import FlaggedNode, make_allowlist_provider, topical_overlap

if TYPE_CHECKING:
    from collections.abc import Callable

    from persona.graph.fusion import HybridResult

_NOW = datetime(2026, 6, 28, tzinfo=UTC)


def _flagged(
    node_id: str, category: WellbeingCategory, recency: RecencyBand, text: str
) -> FlaggedNode:
    return FlaggedNode(node_id=node_id, category=category, recency=recency, text=text)


class TestTopicalOverlap:
    def test_shared_terms_score_positive(self) -> None:
        assert topical_overlap("budget my medication costs", "medication costs monthly") > 0.0

    def test_no_shared_terms_is_zero(self) -> None:
        assert topical_overlap("hiking trails and mountains", "medication dosage") == 0.0

    def test_empty_node_text_is_zero(self) -> None:
        assert topical_overlap("anything here", "") == 0.0

    def test_stopwords_do_not_count_as_overlap(self) -> None:
        # Sharing only stop-words ("i have the") is not topical overlap.
        assert topical_overlap("i have the and to", "panic attack hospital") == 0.0


class TestAllowlistProvider:
    @staticmethod
    def _provider(
        flagged: list[FlaggedNode],
        all_ids: set[str],
        scorer: Callable[[str, str], float] = topical_overlap,
    ) -> Callable[[GatingContext], set[str] | None]:
        return make_allowlist_provider(
            flagged_nodes=lambda _owner: flagged,
            owner_node_ids=lambda _owner: all_ids,
            relevance_scorer=scorer,
        )

    @staticmethod
    def _ctx(query: str = "hello there", recent: tuple[str, ...] = ()) -> GatingContext:
        return GatingContext(owner_id="u", query=query, recent_messages=recent)

    def test_nothing_flagged_returns_none(self) -> None:
        # The common path: no sensitive nodes → no subtraction → zero owner-graph read.
        provider = self._provider([], {"a", "b"})
        assert provider(self._ctx()) is None

    def test_gate_eligible_unopened_unrelated_is_subtracted(self) -> None:
        node = _flagged(
            "c1", WellbeingCategory.MENTAL_HEALTH_CRISIS, RecencyBand.ACUTE, "panic attack hospital"
        )
        provider = self._provider([node], {"c1", "other"})
        result = provider(self._ctx(query="help me plan a hiking trip"))
        assert result == {"other"}  # owner_nodes − {c1}

    def test_lifts_when_topic_raised_in_query(self) -> None:
        node = _flagged(
            "c1", WellbeingCategory.MENTAL_HEALTH_CRISIS, RecencyBand.ACUTE, "panic attack hospital"
        )
        provider = self._provider([node], {"c1", "other"})
        # The query names the topic → topic_opened → always lifts → nothing gated.
        assert provider(self._ctx(query="i had another panic attack today")) is None

    def test_lifts_when_topic_raised_in_recent_window(self) -> None:
        # The resolved-shape test: a bland follow-up query, but the recent window opened
        # the topic → the gate stays lifted (no uncanny re-closing).
        node = _flagged(
            "c1", WellbeingCategory.MENTAL_HEALTH_CRISIS, RecencyBand.ACUTE, "panic attack hospital"
        )
        provider = self._provider([node], {"c1", "other"})
        ctx = self._ctx(
            query="and what should i do next", recent=("the panic attack really scared me",)
        )
        assert provider(ctx) is None

    def test_share_with_care_category_is_never_subtracted(self) -> None:
        # An unrelated, unopened ED node still flows (share-with-care) — the persona must
        # KNOW it to handle it safely; gating it would blind the persona (criterion 2).
        node = _flagged(
            "ed1", WellbeingCategory.DISORDERED_EATING, RecencyBand.ACUTE, "eating struggle"
        )
        provider = self._provider([node], {"ed1", "other"})
        assert provider(self._ctx(query="plan a hiking trip")) is None

    def test_positive_allowlist_is_all_minus_gated(self) -> None:
        node = _flagged(
            "ab1",
            WellbeingCategory.ABUSE_VICTIMISATION,
            RecencyBand.HISTORICAL,
            "violence incident",
        )
        provider = self._provider([node], {"ab1", "x", "y"})
        assert provider(self._ctx(query="what is the weather today")) == {"x", "y"}

    def test_only_gated_nodes_are_removed(self) -> None:
        # One gated (unrelated crisis), one lifted (its topic is in the query).
        gated = _flagged(
            "g", WellbeingCategory.SELF_HARM, RecencyBand.ACUTE, "razor blades cutting"
        )
        lifted = _flagged(
            "l", WellbeingCategory.MENTAL_HEALTH_CRISIS, RecencyBand.ACUTE, "anxiety overwhelm"
        )
        provider = self._provider([gated, lifted], {"g", "l", "z"})
        result = provider(self._ctx(query="my anxiety and overwhelm are bad"))
        assert result == {"l", "z"}  # only the unrelated self-harm node subtracted

    def test_recency_strengthens_the_gate(self) -> None:
        # A fixed mid relevance (in the window, not the bare query) lifts the ACUTE node
        # but NOT the HISTORICAL one — the criterion-6 stronger-gate-for-old-material,
        # observed end to end via the real should_subtract.
        def scorer(text: str, _node: str) -> float:
            return 0.5 if "mid" in text else 0.0

        acute = _flagged("a", WellbeingCategory.MENTAL_HEALTH_CRISIS, RecencyBand.ACUTE, "x")
        hist = _flagged("h", WellbeingCategory.MENTAL_HEALTH_CRISIS, RecencyBand.HISTORICAL, "x")
        provider = self._provider([acute, hist], {"a", "h", "z"}, scorer=scorer)
        ctx = self._ctx(query="please help", recent=("mid",))  # window relevance 0.5, query 0.0
        assert provider(ctx) == {
            "a",
            "z",
        }  # historical 'h' gated (0.5 < 0.60), acute lifted (0.5 ≥ 0.45)


class _EmptyRetriever:
    def retrieve(
        self,
        owner_id: str,  # noqa: ARG002 — contract
        query: str,  # noqa: ARG002 — contract
        *,
        allowlist: set[str] | None = None,  # noqa: ARG002 — contract
        top_k: int | None = None,  # noqa: ARG002 — contract
    ) -> list[HybridResult]:
        return []


class TestGatingContextSeam:
    def test_make_graph_retrieval_builds_context_from_query_and_window(self) -> None:
        captured: list[GatingContext] = []

        def provider(ctx: GatingContext) -> set[str] | None:
            captured.append(ctx)
            return None

        retrieve = make_graph_retrieval(
            retriever=_EmptyRetriever(),
            owner_provider=lambda: "user-A",
            settings=GraphSettings(),
            now=lambda: _NOW,
            allowlist_provider=provider,
            recent_window_provider=lambda: ["an earlier message"],
        )
        retrieve("the current query")
        assert len(captured) == 1
        assert captured[0].owner_id == "user-A"
        assert captured[0].query == "the current query"
        assert captured[0].recent_messages == ("an earlier message",)

    def test_no_window_provider_defaults_to_empty(self) -> None:
        captured: list[GatingContext] = []

        def provider(ctx: GatingContext) -> set[str] | None:
            captured.append(ctx)
            return None

        retrieve = make_graph_retrieval(
            retriever=_EmptyRetriever(),
            owner_provider=lambda: "user-A",
            settings=GraphSettings(),
            now=lambda: _NOW,
            allowlist_provider=provider,
        )
        retrieve("q")
        assert captured[0].recent_messages == ()
