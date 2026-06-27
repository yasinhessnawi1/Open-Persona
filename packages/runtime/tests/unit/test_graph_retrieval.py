"""T3 — the additive graph enrichment of retrieve_context + owner-scoped wiring.

Two things proven here:

- **Additive, never breaking (D-K3-X-a2-seam).** ``retrieve_context`` is shared by
  the text loop AND A2's leg reconstruction. The new ``graph`` field defaults to
  an empty :class:`GraphContext`, and the new ``graph_retrieval`` param defaults
  to ``None`` → every existing caller is byte-identical (the zero-graph path).

- **Owner-scoped, fail-closed (the K2 owner_provider mirror).** ``make_graph_retrieval``
  resolves the owner per call exactly as K2's direct-write does, so the graph
  query is confined to the ContextVar owner — never a cross-owner read — and
  returns nothing when there is no owner context (fail-closed, no leak).
"""

from __future__ import annotations

from datetime import UTC, datetime

from _fakes import FakeStore  # type: ignore[import-not-found]
from persona.graph.config import GraphSettings
from persona.graph.fusion import HybridResult
from persona.graph.models import ConceptNode, NodeKind, NodeProvenance
from persona.schema.chunks import PersonaChunk, WriteSource
from persona_runtime.graph_selection import make_graph_retrieval
from persona_runtime.prompt import GraphContext, GraphKnowledgeItem, GraphRecency
from persona_runtime.retrieval import retrieve_context

_NOW = datetime(2026, 6, 25, tzinfo=UTC)


def _chunk(text: str) -> PersonaChunk:
    return PersonaChunk(id=f"id-{abs(hash(text)) % 10000}", text=text, metadata={}, created_at=_NOW)


def _stores() -> dict[str, FakeStore]:
    identity = FakeStore()
    identity.write("astrid", [_chunk("I am Astrid.")], source=None)  # type: ignore[arg-type]
    return {
        "identity": identity,
        "self_facts": FakeStore(query_results=[_chunk("I specialise in tenancy law.")]),
        "worldview": FakeStore(query_results=[_chunk("Tenants have strong protections.")]),
        "episodic": FakeStore(query_results=[_chunk("Last time we discussed mould.")]),
    }


def _result(distance: float | None, *, sparse_rank: int | None = None) -> HybridResult:
    node = ConceptNode(
        id="n1",
        node_kind=NodeKind.CONCEPT,
        concept_name="vegetarian",
        content="Eats a vegetarian diet.",
        distance=distance,
        provenance=(
            NodeProvenance(source=WriteSource.PERSONA_SELF, persona_id="kai", written_at=_NOW),
        ),
        created_at=_NOW,
    )
    return HybridResult(
        node=node,
        score=0.5,
        rank=1,
        dense_rank=None if distance is None else 1,
        sparse_rank=sparse_rank,
    )


class _RecordingRetriever:
    """A fake HybridRetriever that records the owner it was queried for."""

    def __init__(self, results: list[HybridResult] | None = None) -> None:
        self.results = results if results is not None else []
        self.calls: list[tuple[str, str]] = []

    def retrieve(
        self,
        owner_id: str,
        query: str,
        *,
        allowlist: set[str] | None = None,  # noqa: ARG002 — GraphRetriever contract (K4 seam)
        top_k: int | None = None,  # noqa: ARG002 — GraphRetriever contract
    ) -> list[HybridResult]:
        self.calls.append((owner_id, query))
        return self.results


class TestRetrieveContextGraphEnrichment:
    def test_graph_defaults_to_empty_when_no_retrieval_is_wired(self) -> None:
        # The additive guarantee: existing callers (chat + A2 legs) see an empty
        # graph and otherwise byte-identical behaviour.
        ctx = retrieve_context(_stores(), "astrid", "What are my rights?")
        assert ctx.graph == GraphContext()

    def test_existing_fields_are_unperturbed(self) -> None:
        ctx = retrieve_context(_stores(), "astrid", "What are my rights?")
        assert [c.text for c in ctx.identity] == ["I am Astrid."]
        assert [c.text for c in ctx.self_facts] == ["I specialise in tenancy law."]

    def test_graph_is_populated_from_the_injected_retrieval(self) -> None:
        item = GraphKnowledgeItem(concept_name="x", content="y", recency=GraphRecency.RECENT)
        ctx = retrieve_context(
            _stores(), "astrid", "q", graph_retrieval=lambda _q: GraphContext(items=(item,))
        )
        assert ctx.graph.items == (item,)

    def test_graph_retrieval_receives_this_turns_message_as_query(self) -> None:
        seen: list[str] = []

        def _capture(query: str) -> GraphContext:
            seen.append(query)
            return GraphContext()

        retrieve_context(_stores(), "astrid", "help me budget", graph_retrieval=_capture)
        assert seen == ["help me budget"]


class TestMakeGraphRetrievalScope:
    def _settings(self) -> GraphSettings:
        return GraphSettings(inject_similarity_floor=0.66)

    def test_query_is_scoped_to_the_contextvar_owner(self) -> None:
        retriever = _RecordingRetriever([_result(distance=0.10)])
        retrieve = make_graph_retrieval(
            retriever=retriever,
            owner_provider=lambda: "user-A",
            settings=self._settings(),
            now=lambda: _NOW,
        )
        retrieve("do I eat meat?")
        assert retriever.calls == [("user-A", "do I eat meat?")]

    def test_owner_resolved_per_call_no_cross_owner_leak(self) -> None:
        # The owner is resolved at dispatch (per call), like direct-write — a
        # second request under a different owner must query THAT owner only.
        owner = {"id": "user-A"}
        retriever = _RecordingRetriever([_result(distance=0.10)])
        retrieve = make_graph_retrieval(
            retriever=retriever,
            owner_provider=lambda: owner["id"],
            settings=self._settings(),
            now=lambda: _NOW,
        )
        retrieve("q1")
        owner["id"] = "user-B"
        retrieve("q2")
        assert retriever.calls == [("user-A", "q1"), ("user-B", "q2")]
        owners = {o for o, _ in retriever.calls}
        assert owners == {"user-A", "user-B"}  # never a single leaked owner

    def test_no_owner_context_fails_closed_and_never_queries(self) -> None:
        retriever = _RecordingRetriever([_result(distance=0.10)])
        retrieve = make_graph_retrieval(
            retriever=retriever,
            owner_provider=lambda: None,
            settings=self._settings(),
            now=lambda: _NOW,
        )
        assert retrieve("q") == GraphContext()
        assert retriever.calls == []  # fail-closed: no owner → no read at all

    def test_applies_the_relevance_gate(self) -> None:
        settings = self._settings()
        below = make_graph_retrieval(
            retriever=_RecordingRetriever([_result(distance=0.50)]),  # sim 0.50 < 0.66
            owner_provider=lambda: "user-A",
            settings=settings,
            now=lambda: _NOW,
        )
        above = make_graph_retrieval(
            retriever=_RecordingRetriever([_result(distance=0.10)]),  # sim 0.90 >= 0.66
            owner_provider=lambda: "user-A",
            settings=settings,
            now=lambda: _NOW,
        )
        assert below("q").items == ()
        assert len(above("q").items) == 1

    def test_max_items_is_threaded_to_selection(self) -> None:
        results = [_result(distance=0.10) for _ in range(5)]
        retrieve = make_graph_retrieval(
            retriever=_RecordingRetriever(results),
            owner_provider=lambda: "user-A",
            settings=self._settings(),
            now=lambda: _NOW,
            max_items=2,
        )
        assert len(retrieve("q").items) == 2
