"""Unit tests for persona_runtime.retrieval.retrieve_context (spec V5 D-V5-6).

The conditioning retrieval extracted from ``ConversationLoop._retrieve`` so the
voice turn shares it (never reimplements it). These tests pin: (1) identity is
read via ``get_all`` and the other three via ``query`` (the text-loop behaviour,
byte-identical); (2) the D-V5-1 ``identity`` cache hook skips the identity store
read; (3) ``top_k`` is forwarded to the variable-store queries.
"""

from __future__ import annotations

from datetime import UTC, datetime

from _fakes import FakeStore  # type: ignore[import-not-found]
from persona.schema.chunks import PersonaChunk
from persona_runtime.retrieval import DEFAULT_RETRIEVE_TOP_K, retrieve_context


def _chunk(text: str) -> PersonaChunk:
    return PersonaChunk(
        id=f"id-{abs(hash(text)) % 10000}",
        text=text,
        metadata={},
        created_at=datetime.now(UTC),
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
