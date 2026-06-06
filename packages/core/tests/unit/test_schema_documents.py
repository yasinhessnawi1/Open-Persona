"""Tests for ``persona.schema.documents`` (spec 14 T02).

The sibling discipline (D-14-X-DocumentChunk-shape) is the structural intent
under test: ``DocumentChunk`` and ``PersonaChunk`` are distinct types; round-
tripping via :meth:`DocumentChunk.to_persona_chunk` /
:meth:`DocumentChunk.from_persona_chunk` is the storage-boundary conversion.

The 4-component chunk-ID format (D-14-X-document-chunk-id) carries
``conversation_id::document::doc_ref::index`` so per-document deletion is a
prefix-match on ``conversation_id::document::doc_ref::``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.schema.chunks import PersonaChunk
from persona.schema.documents import (
    DOCUMENT_METADATA_KEYS,
    DOCUMENT_STORE_KIND,
    DocumentChunk,
    make_document_chunk_id,
)
from pydantic import ValidationError

UTC_NOW = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)


class TestDocumentStoreKindConstant:
    def test_value_is_reserved_namespace(self) -> None:
        assert DOCUMENT_STORE_KIND == "document"

    def test_does_not_collide_with_typed_store_kinds(self) -> None:
        # Sanity check: the document store_kind is distinct from the four
        # typed-store kinds. T04 is the binary structural guard that no
        # cross-contamination happens; this guards the constant itself.
        typed_kinds = {"identity", "self_facts", "worldview", "episodic"}
        assert DOCUMENT_STORE_KIND not in typed_kinds


class TestDocumentMetadataKeys:
    def test_carries_doc_ref_format_title_as_required(self) -> None:
        # The three keys ``to_persona_chunk`` always writes.
        assert {"doc_ref", "format", "title"} <= DOCUMENT_METADATA_KEYS

    def test_carries_optional_position_fields(self) -> None:
        assert {"page", "section", "sheet"} <= DOCUMENT_METADATA_KEYS

    def test_is_immutable_frozenset(self) -> None:
        assert isinstance(DOCUMENT_METADATA_KEYS, frozenset)


class TestMakeDocumentChunkId:
    def test_canonical_4_component_format(self) -> None:
        result = make_document_chunk_id("conv-abc", "tenancy.pdf", 7)
        assert result == "conv-abc::document::tenancy.pdf::0007"

    def test_index_is_zero_padded_to_four_digits(self) -> None:
        assert make_document_chunk_id("c", "d", 0) == "c::document::d::0000"
        assert make_document_chunk_id("c", "d", 9999) == "c::document::d::9999"

    def test_negative_index_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            make_document_chunk_id("c", "d", -1)

    def test_conversation_id_with_delimiter_rejected(self) -> None:
        # ``::`` in conversation_id would break prefix-match for delete.
        with pytest.raises(ValueError, match="conversation_id must not contain"):
            make_document_chunk_id("conv::weird", "d", 0)

    def test_doc_ref_with_delimiter_rejected(self) -> None:
        with pytest.raises(ValueError, match="doc_ref must not contain"):
            make_document_chunk_id("c", "doc::weird", 0)

    def test_ids_within_one_doc_sort_lexicographically(self) -> None:
        # Insertion order recovery for a single document.
        ids = [make_document_chunk_id("c", "report.pdf", i) for i in range(15)]
        assert sorted(ids) == ids

    def test_prefix_match_is_clean_per_document(self) -> None:
        # The structural property D-14-X-document-chunk-id relies on:
        # delete_document(c, "a.pdf") is a prefix match that does NOT match
        # any "b.pdf" chunk.
        a_ids = [make_document_chunk_id("c", "a.pdf", i) for i in range(3)]
        b_ids = [make_document_chunk_id("c", "b.pdf", i) for i in range(3)]
        a_prefix = "c::document::a.pdf::"
        for chunk_id in a_ids:
            assert chunk_id.startswith(a_prefix)
        for chunk_id in b_ids:
            assert not chunk_id.startswith(a_prefix)

    def test_does_not_collide_with_persona_chunk_id_format(self) -> None:
        # The 4-component shape distinguishes document chunks from persona
        # chunks (which are 3-component {persona_id}::{store_kind}::{index}).
        doc_id = make_document_chunk_id("conv", "x.pdf", 0)
        # A persona-chunk ID with conversation_id-shaped persona_id would be
        # 3-component; the document chunk has one extra ``::`` segment.
        assert doc_id.count("::") == 3


class TestDocumentChunkValidation:
    def _minimal_kwargs(self) -> dict[str, object]:
        return {
            "id": make_document_chunk_id("c1", "memo.pdf", 0),
            "text": "Hello world.",
            "doc_ref": "memo.pdf",
            "format": "pdf",
            "title": "memo.pdf",
            "created_at": UTC_NOW,
        }

    def test_minimal_construction(self) -> None:
        chunk = DocumentChunk(**self._minimal_kwargs())  # type: ignore[arg-type]
        assert chunk.page is None
        assert chunk.section is None
        assert chunk.sheet is None
        assert chunk.distance is None

    def test_naive_datetime_rejected(self) -> None:
        kwargs = self._minimal_kwargs()
        kwargs["created_at"] = datetime(2026, 6, 5, 12, 0, 0)  # naive
        with pytest.raises(ValidationError, match="naive datetime not allowed"):
            DocumentChunk(**kwargs)  # type: ignore[arg-type]

    def test_extra_fields_forbidden(self) -> None:
        kwargs = {**self._minimal_kwargs(), "junk": "no"}
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            DocumentChunk(**kwargs)  # type: ignore[arg-type]

    def test_frozen_immutable(self) -> None:
        chunk = DocumentChunk(**self._minimal_kwargs())  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            chunk.text = "mutated"  # type: ignore[misc]

    def test_doc_ref_with_delimiter_rejected(self) -> None:
        kwargs = self._minimal_kwargs()
        kwargs["doc_ref"] = "weird::name"
        with pytest.raises(ValidationError, match="doc_ref must not contain"):
            DocumentChunk(**kwargs)  # type: ignore[arg-type]

    def test_page_must_be_at_least_one(self) -> None:
        kwargs = self._minimal_kwargs()
        kwargs["page"] = 0
        with pytest.raises(ValidationError):
            DocumentChunk(**kwargs)  # type: ignore[arg-type]

    def test_page_section_sheet_optional(self) -> None:
        chunk = DocumentChunk(
            id=make_document_chunk_id("c", "x.xlsx", 0),
            text="Q1 revenue: 100",
            doc_ref="x.xlsx",
            format="xlsx",
            title="x.xlsx",
            sheet="Q1",
            section=None,
            page=None,
            created_at=UTC_NOW,
        )
        assert chunk.sheet == "Q1"


class TestSiblingDiscipline:
    """The §6 isolation discipline reads itself in the type system."""

    def test_document_chunk_is_not_persona_chunk(self) -> None:
        # The two types are siblings, NOT subclasses. A function that takes
        # PersonaChunk cannot be called with a DocumentChunk (mypy --strict
        # would block that statically; this test guards runtime isinstance
        # checks Phase 5 callers might use).
        chunk = DocumentChunk(
            id=make_document_chunk_id("c", "x.pdf", 0),
            text="hello",
            doc_ref="x.pdf",
            format="pdf",
            title="x.pdf",
            created_at=UTC_NOW,
        )
        assert not isinstance(chunk, PersonaChunk)

    def test_persona_chunk_is_not_document_chunk(self) -> None:
        # And the inverse — a PersonaChunk produced via to_persona_chunk
        # is its own type, not a DocumentChunk.
        doc = DocumentChunk(
            id=make_document_chunk_id("c", "x.pdf", 0),
            text="hello",
            doc_ref="x.pdf",
            format="pdf",
            title="x.pdf",
            created_at=UTC_NOW,
        )
        pchunk = doc.to_persona_chunk()
        assert not isinstance(pchunk, DocumentChunk)


class TestToPersonaChunk:
    def test_basic_round_trip(self) -> None:
        original = DocumentChunk(
            id=make_document_chunk_id("conv", "report.pdf", 3),
            text="Section three discusses cost.",
            doc_ref="report.pdf",
            format="pdf",
            title="report.pdf",
            page=4,
            section="3 Cost analysis",
            created_at=UTC_NOW,
        )
        pchunk = original.to_persona_chunk()
        round_trip = DocumentChunk.from_persona_chunk(pchunk)
        assert round_trip == original

    def test_metadata_keys_match_standardised_set(self) -> None:
        chunk = DocumentChunk(
            id=make_document_chunk_id("c", "x.xlsx", 0),
            text="Q1 revenue: 100",
            doc_ref="x.xlsx",
            format="xlsx",
            title="x.xlsx",
            sheet="Q1",
            created_at=UTC_NOW,
        )
        pchunk = chunk.to_persona_chunk()
        # Every key in the produced metadata is in the standardised set.
        for key in pchunk.metadata:
            assert key in DOCUMENT_METADATA_KEYS

    def test_optional_fields_omitted_when_none(self) -> None:
        chunk = DocumentChunk(
            id=make_document_chunk_id("c", "memo.txt", 0),
            text="hello",
            doc_ref="memo.txt",
            format="txt",
            title="memo.txt",
            created_at=UTC_NOW,
        )
        pchunk = chunk.to_persona_chunk()
        assert "page" not in pchunk.metadata
        assert "section" not in pchunk.metadata
        assert "sheet" not in pchunk.metadata

    def test_provenance_is_none_on_persona_chunk(self) -> None:
        # D-14-X-no-source-policy-on-documents: documents don't carry the
        # three-source axis. The PersonaChunk envelope has provenance=None.
        chunk = DocumentChunk(
            id=make_document_chunk_id("c", "x.pdf", 0),
            text="hi",
            doc_ref="x.pdf",
            format="pdf",
            title="x.pdf",
            created_at=UTC_NOW,
        )
        pchunk = chunk.to_persona_chunk()
        assert pchunk.provenance is None

    def test_page_is_stringified_in_metadata(self) -> None:
        # PersonaChunk.metadata is dict[str, str]; numeric values stringify.
        chunk = DocumentChunk(
            id=make_document_chunk_id("c", "x.pdf", 0),
            text="hi",
            doc_ref="x.pdf",
            format="pdf",
            title="x.pdf",
            page=42,
            created_at=UTC_NOW,
        )
        pchunk = chunk.to_persona_chunk()
        assert pchunk.metadata["page"] == "42"

    def test_distance_field_round_trips_via_storage_path(self) -> None:
        # distance is populated by Backend.query on retrieval; from_persona_chunk
        # carries it back over.
        chunk = DocumentChunk(
            id=make_document_chunk_id("c", "x.pdf", 0),
            text="hi",
            doc_ref="x.pdf",
            format="pdf",
            title="x.pdf",
            created_at=UTC_NOW,
        )
        pchunk = chunk.to_persona_chunk()
        # Simulate a Backend.query setting distance on the retrieved chunk.
        retrieved = pchunk.model_copy(update={"distance": 0.42})
        back = DocumentChunk.from_persona_chunk(retrieved)
        assert back.distance == 0.42


class TestFromPersonaChunkValidation:
    def test_missing_doc_ref_metadata_rejected(self) -> None:
        # A bare PersonaChunk (no document-store metadata) is not a document
        # chunk; from_persona_chunk surfaces the error clearly.
        bare = PersonaChunk(
            id="someone::episodic::0001",
            text="hi",
            metadata={},  # missing all doc keys
            created_at=UTC_NOW,
        )
        with pytest.raises(ValueError, match="doc_ref"):
            DocumentChunk.from_persona_chunk(bare)

    def test_missing_format_metadata_rejected(self) -> None:
        bare = PersonaChunk(
            id="someone::document::x::0001",
            text="hi",
            metadata={"doc_ref": "x", "title": "x"},  # no format
            created_at=UTC_NOW,
        )
        with pytest.raises(ValueError, match="format"):
            DocumentChunk.from_persona_chunk(bare)

    def test_missing_title_metadata_rejected(self) -> None:
        bare = PersonaChunk(
            id="someone::document::x::0001",
            text="hi",
            metadata={"doc_ref": "x", "format": "pdf"},  # no title
            created_at=UTC_NOW,
        )
        with pytest.raises(ValueError, match="title"):
            DocumentChunk.from_persona_chunk(bare)
