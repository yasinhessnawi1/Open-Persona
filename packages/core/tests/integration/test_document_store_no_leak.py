"""T04 — The criterion-#6 binary structural guard (the no-leak test).

Dominant Concern #1 says it directly: *documents are working material, NOT
persona identity.* The four typed stores (``identity`` / ``self_facts`` /
``worldview`` / ``episodic``) define who the persona IS — permanent across
conversations, versioned, audited, identity-bearing. Uploaded documents are
*what the user handed the persona to read this conversation* — ephemeral
working material.

This test is the binary structural guard for that line: a representative
``DocumentStore`` scenario (write a chunk, query it, get_all, delete one
document, delete the conversation) is exercised through real
:class:`persona.stores.identity.IdentityStore`,
:class:`persona.stores.self_facts.SelfFactsStore`,
:class:`persona.stores.worldview.WorldviewStore`, and
:class:`persona.stores.episodic.EpisodicStore` instances composed against
the **same** in-memory backend. The four typed stores' ``.write()`` methods
are instrumented with call counters; every counter MUST remain at zero
throughout.

If a future Phase-5 task accidentally routes a document write through a
typed store's ``.write()`` (e.g. by extending ``EpisodicStore`` for
documents, by reusing the source-policy table on an upload, by collapsing
the sibling-class discipline), this test catches it.

**This test STAYS GREEN for the rest of Phase 5** as the regression guard
against Dominant Concern #1.
"""
# ruff: noqa: ANN401 — spies wrap arbitrary write() signatures

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from persona.audit import MemoryAuditLogger
from persona.schema.documents import (
    DocumentChunk,
    make_document_chunk_id,
)
from persona.stores.document_store import DocumentStore
from persona.stores.episodic import EpisodicStore
from persona.stores.identity import IdentityStore
from persona.stores.self_facts import SelfFactsStore
from persona.stores.worldview import WorldviewStore

if TYPE_CHECKING:
    from persona.schema.chunks import PersonaChunk

UTC_NOW = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)


class _SharedBackend:
    """In-memory ``Backend`` shared across the four typed stores + DocumentStore.

    The shared backend makes the test realistic — the DocumentStore writes
    pass through the SAME transport the typed stores use. If a future
    DocumentStore accidentally called a typed store's ``.write()``, the
    spy counters would see it; if a future DocumentStore re-implemented
    storage via a side channel that bypassed the shared backend, T04
    couldn't catch that — but the typed-store spies still would.
    """

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
        chunks = list(self.store.get((persona_id, store_kind), []))
        return chunks[:top_k]

    def get_all(self, *, persona_id: str, store_kind: str) -> list[PersonaChunk]:
        return list(self.store.get((persona_id, store_kind), []))

    def delete_persona(self, persona_id: str, store_kind: str) -> None:
        self.store.pop((persona_id, store_kind), None)

    def delete_documents(
        self,
        *,
        persona_id: str,
        store_kind: str,
        ids: list[str],
    ) -> None:
        key = (persona_id, store_kind)
        existing = self.store.get(key, [])
        self.store[key] = [c for c in existing if c.id not in set(ids)]


@pytest.fixture
def shared_backend() -> _SharedBackend:
    return _SharedBackend()


@pytest.fixture
def audit_logger() -> MemoryAuditLogger:
    return MemoryAuditLogger()


@pytest.fixture
def identity_store(
    shared_backend: _SharedBackend, audit_logger: MemoryAuditLogger
) -> IdentityStore:
    return IdentityStore(backend=shared_backend, audit_logger=audit_logger)


@pytest.fixture
def self_facts_store(
    shared_backend: _SharedBackend, audit_logger: MemoryAuditLogger
) -> SelfFactsStore:
    return SelfFactsStore(backend=shared_backend, audit_logger=audit_logger)


@pytest.fixture
def worldview_store(
    shared_backend: _SharedBackend, audit_logger: MemoryAuditLogger
) -> WorldviewStore:
    return WorldviewStore(backend=shared_backend, audit_logger=audit_logger)


@pytest.fixture
def episodic_store(
    shared_backend: _SharedBackend, audit_logger: MemoryAuditLogger
) -> EpisodicStore:
    return EpisodicStore(backend=shared_backend, audit_logger=audit_logger)


@pytest.fixture
def document_store(shared_backend: _SharedBackend) -> DocumentStore:
    return DocumentStore(backend=shared_backend)


@pytest.fixture
def call_spies(
    monkeypatch: pytest.MonkeyPatch,
    identity_store: IdentityStore,
    self_facts_store: SelfFactsStore,
    worldview_store: WorldviewStore,
    episodic_store: EpisodicStore,
) -> dict[str, list[tuple[str, ...]]]:
    """Instrument the four typed stores' ``.write()`` methods with call spies.

    Every call appends a record to the spy list. Empty spy lists at the end
    of the scenario = criterion #6 holds.
    """
    spies: dict[str, list[tuple[str, ...]]] = {
        "identity": [],
        "self_facts": [],
        "worldview": [],
        "episodic": [],
    }

    def _spy(store_name: str, original_write: Any) -> Any:
        def _wrapped(
            persona_id: str,
            chunks: list[PersonaChunk],
            **kwargs: Any,
        ) -> None:
            spies[store_name].append((store_name, persona_id, str(len(chunks))))
            original_write(persona_id, chunks, **kwargs)

        return _wrapped

    monkeypatch.setattr(identity_store, "write", _spy("identity", identity_store.write))
    monkeypatch.setattr(self_facts_store, "write", _spy("self_facts", self_facts_store.write))
    monkeypatch.setattr(worldview_store, "write", _spy("worldview", worldview_store.write))
    monkeypatch.setattr(episodic_store, "write", _spy("episodic", episodic_store.write))
    return spies


def _make_doc_chunk(conv: str, doc_ref: str, index: int) -> DocumentChunk:
    return DocumentChunk(
        id=make_document_chunk_id(conv, doc_ref, index),
        text=f"document content chunk {index}",
        doc_ref=doc_ref,
        format="pdf",
        title=doc_ref,
        page=index + 1,
        created_at=UTC_NOW,
    )


def test_document_store_write_does_not_touch_any_typed_store(
    document_store: DocumentStore,
    call_spies: dict[str, list[tuple[str, ...]]],
) -> None:
    """The headline test for criterion #6.

    A document write must not invoke any typed store's ``.write()``.
    """
    document_store.write(
        "conv-A",
        [_make_doc_chunk("conv-A", "memo.pdf", 0)],
    )
    for store_name, calls in call_spies.items():
        assert calls == [], f"DocumentStore.write leaked into {store_name}.write(): {calls}"


def test_document_store_query_does_not_touch_any_typed_store(
    document_store: DocumentStore,
    call_spies: dict[str, list[tuple[str, ...]]],
) -> None:
    document_store.write(
        "conv-A",
        [_make_doc_chunk("conv-A", "memo.pdf", 0)],
    )
    document_store.query("conv-A", "anything", top_k=5)
    for store_name, calls in call_spies.items():
        assert calls == [], f"DocumentStore.query leaked into {store_name}.write(): {calls}"


def test_document_store_get_all_does_not_touch_any_typed_store(
    document_store: DocumentStore,
    call_spies: dict[str, list[tuple[str, ...]]],
) -> None:
    document_store.write(
        "conv-A",
        [_make_doc_chunk("conv-A", "memo.pdf", 0)],
    )
    document_store.get_all("conv-A")
    for store_name, calls in call_spies.items():
        assert calls == [], f"DocumentStore.get_all leaked into {store_name}.write(): {calls}"


def test_document_store_delete_does_not_touch_any_typed_store(
    document_store: DocumentStore,
    call_spies: dict[str, list[tuple[str, ...]]],
) -> None:
    document_store.write(
        "conv-A",
        [_make_doc_chunk("conv-A", "memo.pdf", 0)],
    )
    document_store.delete("conv-A")
    for store_name, calls in call_spies.items():
        assert calls == [], f"DocumentStore.delete leaked into {store_name}.write(): {calls}"


def test_document_store_delete_document_does_not_touch_any_typed_store(
    document_store: DocumentStore,
    call_spies: dict[str, list[tuple[str, ...]]],
) -> None:
    document_store.write(
        "conv-A",
        [
            _make_doc_chunk("conv-A", "a.pdf", 0),
            _make_doc_chunk("conv-A", "b.pdf", 0),
        ],
    )
    document_store.delete_document("conv-A", "a.pdf")
    for store_name, calls in call_spies.items():
        assert calls == [], (
            f"DocumentStore.delete_document leaked into {store_name}.write(): {calls}"
        )


def test_representative_scenario_zero_typed_store_writes(
    document_store: DocumentStore,
    call_spies: dict[str, list[tuple[str, ...]]],
) -> None:
    """The composite scenario: every DocumentStore operation in sequence.

    A multi-conversation, multi-document scenario covering the full
    DocumentStore surface. All four typed-store write counters remain at
    zero at the end. This is the regression guard that runs alongside the
    per-operation tests above and stays green for the rest of Phase 5.
    """
    # Conversation A — two documents
    document_store.write(
        "conv-A",
        [
            _make_doc_chunk("conv-A", "tenancy.pdf", 0),
            _make_doc_chunk("conv-A", "tenancy.pdf", 1),
            _make_doc_chunk("conv-A", "lease.docx", 0),
        ],
    )
    # Conversation B — one document
    document_store.write(
        "conv-B",
        [_make_doc_chunk("conv-B", "report.xlsx", 0)],
    )

    # Querying never writes — but verify.
    document_store.query("conv-A", "what does the tenancy contract say?", top_k=3)
    document_store.query("conv-B", "what is the Q1 revenue?", top_k=3)

    # Get-all the attached docs (used by the synopsis path).
    assert len(document_store.get_all("conv-A")) == 3
    assert len(document_store.get_all("conv-B")) == 1

    # Remove one document from conv-A.
    document_store.delete_document("conv-A", "tenancy.pdf")
    assert {c.doc_ref for c in document_store.get_all("conv-A")} == {"lease.docx"}

    # Delete conv-B entirely (the cascade path).
    document_store.delete("conv-B")
    assert document_store.get_all("conv-B") == []

    # FINAL ASSERTION: every typed-store write counter is empty.
    for store_name, calls in call_spies.items():
        assert calls == [], (
            f"Composite DocumentStore scenario leaked into {store_name}.write(): "
            f"{calls} — Dominant Concern #1 violation"
        )


def test_typed_stores_remain_functional_after_document_writes(
    document_store: DocumentStore,
    identity_store: IdentityStore,
    audit_logger: MemoryAuditLogger,
    call_spies: dict[str, list[tuple[str, ...]]],
) -> None:
    """The typed stores still work — DocumentStore doesn't corrupt them.

    A document write under conv-A is invisible to the IdentityStore
    operating under persona-X. The two stores share a backend but their
    ``(persona_id, store_kind)`` namespaces don't collide.
    """
    # Document write under conv-A.
    document_store.write(
        "conv-A",
        [_make_doc_chunk("conv-A", "memo.pdf", 0)],
    )

    # Identity get_all under a different scope returns empty — no bleed.
    identity_chunks = identity_store.get_all("persona-X")
    assert identity_chunks == []

    # The audit log carries nothing — no typed-store write happened.
    assert audit_logger.events == []  # type: ignore[attr-defined]

    # Spies stay empty.
    for store_name, calls in call_spies.items():
        assert calls == [], store_name
