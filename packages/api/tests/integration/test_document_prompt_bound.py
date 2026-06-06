"""Bounded-prompt-tokens regression test (spec 14 T20).

**Dominant Concern #2 structural guard**: under document load, the prompt-
token bound at turn N stays bounded by a documented constant
(:data:`PROMPT_BOUND` = 30 000 tokens per D-14-X-prompt-bound-target),
NOT by the cumulative content the user has uploaded. The size-aware
ingestion strategy (T12, D-14-1) + the conservative D-14-5 rank rule + the
"what's in scope" synopsis (T16) together protect this property; T20
verifies it.

The Spec 11 soak measured ``max_prompt_tokens=20553`` at turn 100 (mid-
tier scenario, no documents). 30 000 leaves ~9000 tokens of headroom
over that empirical peak — tight enough to catch a real regression
(e.g., accidentally inlining a whole large document) while loose enough
that transient retrieval-set growth doesn't flap.

Scenario:
1. Build a 50-page document's worth of text (~25 000 tokens).
2. Chunk it via the T05 chunker; write chunks to an in-memory
   :class:`~persona.stores.document_store.DocumentStore`.
3. Run a 5-turn conversation; each turn queries the DocumentStore for
   top-k chunks, builds a :class:`DocumentContext`, then calls
   :meth:`PromptBuilder.build` and asserts the assembled prompt token
   count is under :data:`PROMPT_BOUND`.

No DB required — this exercises the prompt-builder + chunker + store
composition that protects the bound; the DB is just storage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from persona.documents.chunker import DocumentSection, chunk_document
from persona.schema.documents import DocumentChunk
from persona.schema.persona import Persona, PersonaIdentity
from persona.skills import count_tokens
from persona.stores.document_store import DocumentStore
from persona_runtime.prompt import (
    DocumentContext,
    DocumentDescriptor,
    PromptBuilder,
    RetrievedContext,
)

if TYPE_CHECKING:
    from persona.schema.chunks import PersonaChunk


#: D-14-X-prompt-bound-target. Per-turn prompt < this bound under all
#: documented document-load scenarios. ~45% headroom over Spec 11's
#: empirical ``max_prompt_tokens=20553``.
PROMPT_BOUND: int = 30_000


class _InMemoryBackend:
    """The same in-memory backend pattern as the T13/T18 tests."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], list[PersonaChunk]] = {}

    def upsert(
        self,
        *,
        persona_id: str,
        store_kind: str,
        chunks: list[PersonaChunk],
    ) -> None:
        key = (persona_id, store_kind)
        existing = self.store.setdefault(key, [])
        existing_ids = {c.id for c in chunks}
        kept = [c for c in existing if c.id not in existing_ids]
        kept.extend(chunks)
        self.store[key] = kept

    def query(
        self,
        *,
        persona_id: str,
        store_kind: str,
        text: str,  # noqa: ARG002 — fake doesn't embed
        top_k: int,
        where: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> list[PersonaChunk]:
        return list(self.store.get((persona_id, store_kind), []))[:top_k]

    def get_all(self, *, persona_id: str, store_kind: str) -> list[PersonaChunk]:
        return list(self.store.get((persona_id, store_kind), []))

    def delete_persona(self, persona_id: str, store_kind: str) -> None:
        self.store.pop((persona_id, store_kind), None)

    def delete_documents(self, *, persona_id: str, store_kind: str, ids: list[str]) -> None:
        key = (persona_id, store_kind)
        self.store[key] = [c for c in self.store.get(key, []) if c.id not in set(ids)]


def _persona() -> Persona:
    return Persona(
        persona_id="astrid",
        identity=PersonaIdentity(
            name="Astrid",
            role="Norwegian tenancy law assistant",
            background="Knows husleieloven and tenant rights deeply.",
            constraints=[
                "Never give binding legal advice.",
                "Always recommend a qualified lawyer for disputes.",
                "Cite husleieloven section numbers when stating facts.",
            ],
        ),
    )


def _build_50_page_document_sections() -> list[DocumentSection]:
    """Synthesize ~50 pages of text — ~25 000 tokens of legal-text-like content.

    Each "page" is a paragraph of ~200 tokens (boilerplate legal language)
    so chunking produces a non-trivial number of chunks but the structure
    is realistic for retrieval.
    """
    sentences = [
        "The tenant shall pay rent monthly in arrears on the first of each calendar month.",
        "The landlord retains responsibility for the structural integrity of the demised premises.",
        "Section 5-3 of husleieloven governs the maintenance of heating systems.",
        "Either party may terminate this agreement with two months written notice served by post.",
        "Disputes arising from this agreement should first be referred to Husleietvistutvalget.",
        "The premises are let for residential purposes only and not for commercial use.",
        "The tenant accepts the property in its current condition as described in the "
        "inventory schedule.",
        "Any modifications to the property require prior written consent from the landlord.",
        "Subletting is permitted only with the landlord's express written approval "
        "per section 7-1.",
        "Common-area maintenance fees are billed quarterly and itemised on the rent statement.",
    ]
    sections: list[DocumentSection] = []
    for page_num in range(1, 51):
        body = " ".join(sentences) + f" (Page {page_num} continues the boilerplate clause.)"
        # Repeat to fatten each "page" to ~200 tokens.
        full_body = "\n\n".join([body] * 3)
        sections.append(DocumentSection(text=full_body, page=page_num))
    return sections


@pytest.fixture
def builder() -> PromptBuilder:
    return PromptBuilder()


@pytest.fixture
def document_store() -> DocumentStore:
    return DocumentStore(backend=_InMemoryBackend())  # type: ignore[arg-type]


@pytest.fixture
def persona() -> Persona:
    return _persona()


@pytest.fixture
def large_doc_setup(
    document_store: DocumentStore,
) -> tuple[str, DocumentDescriptor]:
    """Chunk + write a 50-page document; return (doc_ref, descriptor)."""
    conversation_id = "conv_T20"
    doc_ref = "tenancy_agreement.pdf"
    sections = _build_50_page_document_sections()
    chunks = chunk_document(
        sections=sections,
        conversation_id=conversation_id,
        doc_ref=doc_ref,
        document_format="pdf",
        title=doc_ref,
        chunk_size_tokens=512,
        overlap_tokens=64,
    )
    document_store.write(conversation_id, chunks)
    descriptor = DocumentDescriptor(
        title=doc_ref,
        format="pdf",
        page_count=50,
        size_bytes=120_000,  # ~120 KB synthetic
    )
    return doc_ref, descriptor


@pytest.mark.integration
class TestBoundedPromptTokens:
    """The headline Dominant Concern #2 regression guard.

    50-page document × 5-turn conversation → prompt < 30 000 tokens
    every turn.
    """

    def _query_messages(self) -> list[str]:
        # Realistic per-turn user queries against the document.
        return [
            "What does the agreement say about rent?",
            "When can the tenant terminate the lease?",
            "Who is responsible for heating-system repairs?",
            "Is subletting allowed?",
            "Where do disputes get resolved?",
        ]

    def test_50_page_doc_over_5_turns_stays_under_bound(
        self,
        builder: PromptBuilder,
        persona: Persona,
        document_store: DocumentStore,
        large_doc_setup: tuple[str, DocumentDescriptor],
    ) -> None:
        doc_ref, descriptor = large_doc_setup
        conversation_id = "conv_T20"

        per_turn_tokens: list[int] = []
        for query in self._query_messages():
            # Per-turn retrieval (top-k=3, the conventional default).
            persona_chunks = document_store._backend.query(  # noqa: SLF001
                persona_id=conversation_id,
                store_kind="document",
                text=query,
                top_k=3,
            )
            retrieved_doc_chunks = tuple(
                DocumentChunk.from_persona_chunk(c) for c in persona_chunks
            )
            document_context = DocumentContext(
                attached_documents=(descriptor,),
                retrieved_chunks=retrieved_doc_chunks,
            )

            messages = builder.build(
                persona,
                RetrievedContext(),  # no other retrieved context this turn
                history=[],
                skill_index="",
                user_message=query,
                max_tokens=PROMPT_BOUND,
                document_context=document_context,
            )
            total = sum(count_tokens(m.content) for m in messages)
            per_turn_tokens.append(total)

        # Every turn's prompt is under the documented bound.
        for turn_index, tokens in enumerate(per_turn_tokens, start=1):
            assert tokens < PROMPT_BOUND, (
                f"Turn {turn_index}: prompt size {tokens} exceeds "
                f"D-14-X-prompt-bound-target ({PROMPT_BOUND}) — "
                "Dominant Concern #2 regression"
            )

    def test_per_turn_size_bounded_not_cumulative(
        self,
        builder: PromptBuilder,
        persona: Persona,
        document_store: DocumentStore,
        large_doc_setup: tuple[str, DocumentDescriptor],
    ) -> None:
        """The structural property: per-turn prompt is bounded by a constant,
        NOT by the cumulative content uploaded. Verified by asserting that
        prompt size doesn't grow turn-over-turn beyond a small ceiling
        (retrieved set size + history accumulation are bounded)."""
        doc_ref, descriptor = large_doc_setup
        conversation_id = "conv_T20"

        per_turn_tokens: list[int] = []
        for query in self._query_messages():
            persona_chunks = document_store._backend.query(  # noqa: SLF001
                persona_id=conversation_id,
                store_kind="document",
                text=query,
                top_k=3,
            )
            retrieved = tuple(DocumentChunk.from_persona_chunk(c) for c in persona_chunks)
            doc_ctx = DocumentContext(
                attached_documents=(descriptor,),
                retrieved_chunks=retrieved,
            )
            messages = builder.build(
                persona,
                RetrievedContext(),
                history=[],
                skill_index="",
                user_message=query,
                max_tokens=PROMPT_BOUND,
                document_context=doc_ctx,
            )
            per_turn_tokens.append(sum(count_tokens(m.content) for m in messages))

        # Max per-turn tokens stays well under the bound.
        assert max(per_turn_tokens) < PROMPT_BOUND

        # The variance across turns is bounded — turn-over-turn growth is
        # not proportional to cumulative content. (Spread under ~2000 tokens
        # for fixed top_k retrieval and zero history.)
        spread = max(per_turn_tokens) - min(per_turn_tokens)
        assert spread < 2000, (
            f"Per-turn prompt spread ({spread}) too large — retrieval "
            "size may be unbounded across turns"
        )


@pytest.mark.integration
class TestSynopsisAlwaysPresent:
    """T16 structural-defence guard (revisited at T20 time): even on a
    turn where no chunks were retrieved, the synopsis still lists the
    attached document — under document load, the model is always told
    what's in scope.
    """

    def test_synopsis_present_when_no_chunks_retrieved(
        self,
        builder: PromptBuilder,
        persona: Persona,
        large_doc_setup: tuple[str, DocumentDescriptor],
    ) -> None:
        _, descriptor = large_doc_setup
        doc_ctx = DocumentContext(
            attached_documents=(descriptor,),
            retrieved_chunks=(),  # no chunks retrieved this turn
        )
        messages = builder.build(
            persona,
            RetrievedContext(),
            history=[],
            skill_index="",
            user_message="unrelated question",
            max_tokens=PROMPT_BOUND,
            document_context=doc_ctx,
        )
        system_text = messages[0].content
        # Synopsis appears even with zero retrieved chunks.
        assert "Attached documents:" in system_text
        assert descriptor.title in system_text
