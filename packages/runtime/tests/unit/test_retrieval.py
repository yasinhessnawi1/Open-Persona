"""Unit tests for persona_runtime.retrieval.retrieve_context (spec V5 D-V5-6).

The conditioning retrieval extracted from ``ConversationLoop._retrieve`` so the
voice turn shares it (never reimplements it). These tests pin: (1) identity is
read via ``get_all`` and the other three via ``query`` (the text-loop behaviour,
byte-identical); (2) the D-V5-1 ``identity`` cache hook skips the identity store
read; (3) ``top_k`` is forwarded to the variable-store queries.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from _fakes import FakeStore  # type: ignore[import-not-found]
from persona.schema.chunks import PersonaChunk
from persona_runtime.retrieval import (
    DEFAULT_RETRIEVE_TOP_K,
    EARLY_RETRIEVE_TOP_K,
    dynamic_top_k,
    retrieve_context,
)


def _chunk(text: str, *, created_at: datetime | None = None) -> PersonaChunk:
    return PersonaChunk(
        id=f"id-{abs(hash(text)) % 10000}",
        text=text,
        metadata={},
        created_at=created_at or datetime.now(UTC),
    )


def _stores() -> dict[str, FakeStore]:
    identity = FakeStore()
    identity.write("astrid", [_chunk("I am Astrid.")], source=None)  # type: ignore[arg-type]
    return {
        "identity": identity,
        "self_facts": FakeStore(query_results=[_chunk("I specialise in tenancy law.")]),
        "worldview": FakeStore(query_results=[_chunk("Tenants have strong protections.")]),
        "episodic": FakeStore(query_results=[_chunk("Last time we discussed mould.")]),
    }


class _CountingIdentityStore(FakeStore):
    """A FakeStore that counts get_all calls (to prove the cache hook works)."""

    def __init__(self) -> None:
        super().__init__()
        self.get_all_calls = 0

    def get_all(self, persona_id: str, *, include_superseded: bool = False) -> list[PersonaChunk]:
        self.get_all_calls += 1
        return super().get_all(persona_id, include_superseded=include_superseded)


class TestRetrieveContext:
    def test_identity_via_get_all_others_via_query(self) -> None:
        ctx = retrieve_context(_stores(), "astrid", "What are my rights?")
        assert [c.text for c in ctx.identity] == ["I am Astrid."]
        assert [c.text for c in ctx.self_facts] == ["I specialise in tenancy law."]
        assert [c.text for c in ctx.worldview] == ["Tenants have strong protections."]
        assert [c.text for c in ctx.episodic] == ["Last time we discussed mould."]

    def test_default_top_k_is_three(self) -> None:
        assert DEFAULT_RETRIEVE_TOP_K == 3

    def test_top_k_forwarded_to_variable_queries(self) -> None:
        many = [_chunk(f"fact {i}") for i in range(10)]
        stores = _stores()
        stores["self_facts"] = FakeStore(query_results=many)
        ctx = retrieve_context(stores, "astrid", "q", top_k=2)
        assert len(ctx.self_facts) == 2


class TestDynamicTopK:
    def test_fresh_conversation_uses_high_budget(self) -> None:
        assert dynamic_top_k(0) == EARLY_RETRIEVE_TOP_K

    def test_negative_turns_clamp_to_high(self) -> None:
        assert dynamic_top_k(-3) == EARLY_RETRIEVE_TOP_K

    def test_budget_decays_one_per_turn(self) -> None:
        assert dynamic_top_k(1) == EARLY_RETRIEVE_TOP_K - 1
        assert dynamic_top_k(2) == EARLY_RETRIEVE_TOP_K - 2

    def test_budget_floors_at_default(self) -> None:
        assert dynamic_top_k(100) == DEFAULT_RETRIEVE_TOP_K

    def test_history_turns_drives_variable_query_budget(self) -> None:
        many = [_chunk(f"fact {i}") for i in range(20)]
        stores = _stores()
        stores["self_facts"] = FakeStore(query_results=many)
        # A fresh turn pulls the high budget...
        fresh = retrieve_context(stores, "astrid", "q", history_turns=0)
        assert len(fresh.self_facts) == EARLY_RETRIEVE_TOP_K
        # ...a long conversation decays to the floor.
        deep = retrieve_context(stores, "astrid", "q", history_turns=50)
        assert len(deep.self_facts) == DEFAULT_RETRIEVE_TOP_K


class TestEpisodicRecency:
    def _episodic_with_recent_turns(self) -> tuple[dict[str, FakeStore], list[str]]:
        """Episodic store whose recent turns differ from the similarity hit."""
        base = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
        recent_turns = [
            _chunk("USER: the professor gave me a C", created_at=base),
            _chunk("USER: i worked really hard on it", created_at=base + timedelta(minutes=1)),
        ]
        # query() returns an unrelated similarity hit — not the recent tail.
        episodic = FakeStore(query_results=[_chunk("USER: tell me about mould")])
        episodic.write("astrid", recent_turns, source=None)  # type: ignore[arg-type]
        stores = _stores()
        stores["episodic"] = episodic
        return stores, [c.text for c in recent_turns]

    def test_recency_surfaces_previous_turns_without_semantic_match(self) -> None:
        stores, recent_texts = self._episodic_with_recent_turns()
        ctx = retrieve_context(stores, "astrid", "what were we talking about?", history_turns=0)
        episodic_texts = [c.text for c in ctx.episodic]
        # The previous session's tail surfaces even though the query does not
        # embed near it — that is the continuity fix.
        for text in recent_texts:
            assert text in episodic_texts

    def test_recent_turns_ordered_chronologically(self) -> None:
        stores, recent_texts = self._episodic_with_recent_turns()
        ctx = retrieve_context(stores, "astrid", "anything?", history_turns=0)
        present = [c.text for c in ctx.episodic if c.text in recent_texts]
        assert present == recent_texts  # oldest -> newest

    def test_without_history_turns_recall_is_similarity_only(self) -> None:
        stores, recent_texts = self._episodic_with_recent_turns()
        ctx = retrieve_context(stores, "astrid", "what were we talking about?")
        # Legacy callers (no history_turns) keep the historical behaviour:
        # similarity hit only, no recency augmentation.
        assert [c.text for c in ctx.episodic] == ["USER: tell me about mould"]
        for text in recent_texts:
            assert text not in [c.text for c in ctx.episodic]


class TestIdentityCacheHook:
    def test_passing_identity_skips_identity_store_read(self) -> None:
        stores = _stores()
        counting = _CountingIdentityStore()
        stores["identity"] = counting
        cached = [_chunk("cached identity")]

        ctx = retrieve_context(stores, "astrid", "q", identity=cached)

        assert counting.get_all_calls == 0
        assert [c.text for c in ctx.identity] == ["cached identity"]

    def test_omitting_identity_reads_the_store(self) -> None:
        stores = _stores()
        counting = _CountingIdentityStore()
        counting.write("astrid", [_chunk("from store")], source=None)  # type: ignore[arg-type]
        stores["identity"] = counting

        ctx = retrieve_context(stores, "astrid", "q")

        assert counting.get_all_calls == 1
        assert [c.text for c in ctx.identity] == ["from store"]
