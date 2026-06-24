"""Update/contradiction target resolution (Spec K2, T4; criterion 9).

When the user corrects themselves ("I no longer work at X"), the candidate carries
``update_intent`` + a free-text ``update_target_hint``. This resolver finds the
existing graph node the correction is about (via the K0/K1 dense read) so the
assembler can hand merge a ``target_node_id`` — and merge evolves that node WITH
provenance (no silent overwrite, K0 criterion 5, exercised from the write side).
A non-confident match resolves to ``None`` (the correction falls back to a new
node rather than risk evolving the WRONG node — a wrong overwrite is worse).
"""

# ruff: noqa: ARG002 — the GraphStore double implements unused read legs.

from __future__ import annotations

from datetime import UTC, datetime

from persona.extraction import ExtractionCandidate
from persona.graph.models import ConceptNode, NodeKind, NodeProvenance
from persona.graph.protocol import UpdateIntent
from persona.schema.chunks import WriteSource
from persona_runtime.extraction.update import UpdateResolver


def _node(node_id: str, *, distance: float) -> ConceptNode:
    return ConceptNode(
        id=node_id,
        node_kind=NodeKind.CIRCUMSTANCE,
        concept_name="employment",
        content="The user works at Acme Corp.",
        distance=distance,
        provenance=(NodeProvenance(source=WriteSource.SYSTEM, written_at=datetime.now(UTC)),),
        created_at=datetime.now(UTC),
    )


class _FakeStore:
    """A GraphStore double exposing only the dense read the resolver uses."""

    def __init__(self, hits: list[ConceptNode]) -> None:
        self._hits = hits
        self.queries: list[str] = []

    def search_dense(
        self,
        owner_id: str,
        query: str,
        top_k: int,
        *,
        allowlist: set[str] | None = None,
    ) -> list[ConceptNode]:
        self.queries.append(query)
        return self._hits[:top_k]


def _candidate(*, update_intent: UpdateIntent, hint: str | None = None) -> ExtractionCandidate:
    return ExtractionCandidate(
        concept_name="employment",
        content="The user no longer works at Acme.",
        node_kind=NodeKind.CIRCUMSTANCE,
        evidence_span="I left Acme",
        update_intent=update_intent,
        update_target_hint=hint,
    )


def test_no_update_intent_resolves_to_none_and_does_not_query() -> None:
    store = _FakeStore([_node("n1", distance=0.01)])
    resolver = UpdateResolver(store=store)
    assert resolver.resolve_target("u1", _candidate(update_intent=UpdateIntent.NONE)) is None
    assert store.queries == []  # no graph read when there is nothing to update


def test_update_without_a_hint_resolves_to_none() -> None:
    store = _FakeStore([_node("n1", distance=0.01)])
    resolver = UpdateResolver(store=store)
    cand = _candidate(update_intent=UpdateIntent.CONTRADICT, hint=None)
    assert resolver.resolve_target("u1", cand) is None
    assert store.queries == []


def test_contradiction_resolves_to_the_matching_node() -> None:
    # criterion 9: the correction flows to merge as an update targeting the prior node.
    store = _FakeStore([_node("node-works-at-acme", distance=0.05)])
    resolver = UpdateResolver(store=store)
    cand = _candidate(update_intent=UpdateIntent.CONTRADICT, hint="works at Acme")
    assert resolver.resolve_target("u1", cand) == "node-works-at-acme"
    assert store.queries == ["works at Acme"]


def test_no_graph_hit_resolves_to_none() -> None:
    resolver = UpdateResolver(store=_FakeStore([]))
    cand = _candidate(update_intent=UpdateIntent.UPDATE, hint="works at Acme")
    assert resolver.resolve_target("u1", cand) is None


def test_a_low_confidence_match_is_rejected_to_avoid_evolving_the_wrong_node() -> None:
    # The top hit is too far — better a new node than evolving the wrong one.
    store = _FakeStore([_node("far-node", distance=0.95)])
    resolver = UpdateResolver(store=store, max_distance=0.4)
    cand = _candidate(update_intent=UpdateIntent.CONTRADICT, hint="something unrelated")
    assert resolver.resolve_target("u1", cand) is None


def test_fake_store_satisfies_the_search_dense_signature() -> None:
    # Smoke: the resolver only depends on the dense read leg (duck-typed).
    store = _FakeStore([_node("n", distance=0.1)])
    out = store.search_dense("u1", "q", 1)
    assert out[0].id == "n"
