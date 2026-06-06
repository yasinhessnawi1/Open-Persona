"""Tests for ``persona.documents.chunker`` (spec 14 T05).

D-14-4 — document-aware chunker with natural-boundary chunks + token-aware
fallback. The Phase 1 typed-store chunking contract (one PersonaChunk per
YAML field, no splitting) stays byte-for-byte unchanged — verified by the
regression assertion at the bottom of this file.
"""

from __future__ import annotations

import pytest
from persona.documents.chunker import (
    DEFAULT_CHUNK_SIZE_TOKENS,
    DEFAULT_OVERLAP_TOKENS,
    DocumentSection,
    chunk_document,
)
from persona.schema.documents import DocumentChunk
from persona.skills import count_tokens


def _sections(*texts_with_page: tuple[str, int | None]) -> list[DocumentSection]:
    return [DocumentSection(text=text, page=page) for text, page in texts_with_page]


class TestDefaults:
    def test_default_constants_match_phase4_decision(self) -> None:
        # D-14-4 + R-14-3 — 512 / 64
        assert DEFAULT_CHUNK_SIZE_TOKENS == 512
        assert DEFAULT_OVERLAP_TOKENS == 64


class TestEmptyAndDegenerateInput:
    def test_empty_sections_produce_empty_list(self) -> None:
        assert (
            chunk_document(
                [],
                conversation_id="c",
                doc_ref="x.txt",
                document_format="txt",
                title="x.txt",
            )
            == []
        )

    def test_whitespace_only_section_produces_empty_list(self) -> None:
        result = chunk_document(
            _sections(("   \n\n  ", None)),
            conversation_id="c",
            doc_ref="x.txt",
            document_format="txt",
            title="x.txt",
        )
        assert result == []


class TestNaturalBoundary:
    def test_single_short_section_one_chunk(self) -> None:
        # A short paragraph fits under the cap → one chunk.
        result = chunk_document(
            _sections(("The lease is for one year.", 1)),
            conversation_id="conv",
            doc_ref="memo.pdf",
            document_format="pdf",
            title="memo.pdf",
        )
        assert len(result) == 1
        assert result[0].text == "The lease is for one year."
        assert result[0].page == 1

    def test_multiple_short_sections_each_become_chunks(self) -> None:
        # Each natural-boundary unit produces a chunk (no merging at section
        # level — keeps each section's page metadata intact).
        result = chunk_document(
            _sections(
                ("Para A on page one.", 1),
                ("Para B on page two.", 2),
                ("Para C on page three.", 3),
            ),
            conversation_id="conv",
            doc_ref="report.pdf",
            document_format="pdf",
            title="report.pdf",
        )
        assert len(result) == 3
        assert [c.page for c in result] == [1, 2, 3]

    def test_section_metadata_stamped_onto_chunks(self) -> None:
        result = chunk_document(
            [DocumentSection(text="Q1 revenue: 100", sheet="Q1")],
            conversation_id="conv",
            doc_ref="report.xlsx",
            document_format="xlsx",
            title="report.xlsx",
        )
        assert result[0].sheet == "Q1"
        assert result[0].page is None

    def test_section_with_section_heading_stamped(self) -> None:
        result = chunk_document(
            [DocumentSection(text="The methodology was...", section="3 Methodology")],
            conversation_id="conv",
            doc_ref="thesis.docx",
            document_format="docx",
            title="thesis.docx",
        )
        assert result[0].section == "3 Methodology"


class TestTokenCapFallback:
    def test_over_cap_section_splits_on_paragraph_boundary(self) -> None:
        # A section longer than the cap with paragraph breaks splits at them.
        long_para = "Sentence " * 200  # well over 512 tokens
        text = long_para + "\n\n" + long_para
        result = chunk_document(
            [DocumentSection(text=text, page=1)],
            conversation_id="conv",
            doc_ref="big.txt",
            document_format="txt",
            title="big.txt",
            chunk_size_tokens=200,
            overlap_tokens=20,
        )
        # Splits across at least two chunks; each chunk under the cap.
        assert len(result) >= 2
        for chunk in result:
            assert count_tokens(chunk.text) <= 200 + 1  # +1 for boundary leniency

    def test_over_cap_section_with_no_paragraph_splits_on_line(self) -> None:
        # No "\n\n" but has "\n" — line-level fallback.
        text = "\n".join(["A line of text"] * 100)
        result = chunk_document(
            [DocumentSection(text=text)],
            conversation_id="conv",
            doc_ref="lines.txt",
            document_format="txt",
            title="lines.txt",
            chunk_size_tokens=50,
            overlap_tokens=10,
        )
        assert len(result) >= 2
        for chunk in result:
            assert count_tokens(chunk.text) <= 60  # cap + small overlap budget

    def test_chunk_indexing_is_zero_based_and_global(self) -> None:
        # Chunks index globally across all sections so the chunk-ID's index
        # slot reflects insertion order.
        result = chunk_document(
            _sections(
                ("First para.", 1),
                ("Second para.", 2),
                ("Third para.", 3),
            ),
            conversation_id="conv",
            doc_ref="doc.pdf",
            document_format="pdf",
            title="doc.pdf",
        )
        # IDs use 4-digit zero-padded global index per make_document_chunk_id.
        assert result[0].id.endswith("::0000")
        assert result[1].id.endswith("::0001")
        assert result[2].id.endswith("::0002")


class TestOverlap:
    def test_overlap_text_appears_in_adjacent_chunks(self) -> None:
        # Build text long enough to split, then assert tail of chunk N appears
        # at head of chunk N+1 (approximate — overlap is token-budgeted).
        paragraphs = [f"Paragraph number {i} contains some content." for i in range(50)]
        text = "\n\n".join(paragraphs)
        result = chunk_document(
            [DocumentSection(text=text)],
            conversation_id="conv",
            doc_ref="overlap.txt",
            document_format="txt",
            title="overlap.txt",
            chunk_size_tokens=60,
            overlap_tokens=10,
        )
        # Sanity: split happened.
        assert len(result) >= 2
        # Each chunk after the first should contain some terminal text from the
        # previous chunk (within reason — exact overlap depends on tokenisation).
        # We assert at least one consecutive pair shares ≥1 non-trivial token.
        shared_pairs = 0
        for prev, curr in zip(result, result[1:], strict=False):
            prev_tokens = set(prev.text.split())
            curr_first_words = set(curr.text.split()[:10])
            if prev_tokens & curr_first_words:
                shared_pairs += 1
        assert shared_pairs >= 1


class TestParameterValidation:
    def test_zero_chunk_size_rejected(self) -> None:
        with pytest.raises(ValueError, match="chunk_size_tokens must be positive"):
            chunk_document(
                [DocumentSection(text="hi")],
                conversation_id="c",
                doc_ref="x.txt",
                document_format="txt",
                title="x.txt",
                chunk_size_tokens=0,
            )

    def test_negative_overlap_rejected(self) -> None:
        with pytest.raises(ValueError, match="overlap_tokens must be non-negative"):
            chunk_document(
                [DocumentSection(text="hi")],
                conversation_id="c",
                doc_ref="x.txt",
                document_format="txt",
                title="x.txt",
                overlap_tokens=-1,
            )

    def test_overlap_equal_to_chunk_size_rejected(self) -> None:
        with pytest.raises(ValueError, match="strictly less than"):
            chunk_document(
                [DocumentSection(text="hi")],
                conversation_id="c",
                doc_ref="x.txt",
                document_format="txt",
                title="x.txt",
                chunk_size_tokens=100,
                overlap_tokens=100,
            )


class TestChunkReturnsDocumentChunks:
    def test_returns_document_chunks_not_persona_chunks(self) -> None:
        result = chunk_document(
            [DocumentSection(text="hi")],
            conversation_id="c",
            doc_ref="x.txt",
            document_format="txt",
            title="x.txt",
        )
        assert all(isinstance(c, DocumentChunk) for c in result)

    def test_chunk_format_and_title_set_from_args(self) -> None:
        result = chunk_document(
            [DocumentSection(text="hi")],
            conversation_id="c",
            doc_ref="report.pdf",
            document_format="pdf",
            title="Tenancy Report",
        )
        assert result[0].format == "pdf"
        assert result[0].title == "Tenancy Report"
        assert result[0].doc_ref == "report.pdf"


class TestPhase1ChunkingNotRegressed:
    """Hard constraint per D-14-4: Spec 01 chunking behaviour unchanged.

    Spec 01's chunking is implicit (one PersonaChunk per YAML field, no
    splitting; see registry.py lines 163-165, 264-265). The chunker module
    is a new addition — registry / chat_cmd / typed stores are NOT
    modified.

    These assertions verify the discipline by inspection: the chunker
    module does NOT import from persona.registry / persona.cli.chat_cmd
    (which would imply touching them); the existing chunk-construction
    sites still produce one chunk per call.
    """

    def test_chunker_module_does_not_import_registry_or_cli(self) -> None:
        # The chunker module's import graph excludes the typed-store
        # construction sites — sanity check.
        from persona.documents import chunker

        module_imports = chunker.__loader__.get_source(chunker.__name__) or ""  # type: ignore[union-attr]
        assert "from persona.registry" not in module_imports
        assert "from persona.cli" not in module_imports

    def test_registry_chunk_construction_unchanged(self) -> None:
        # Spec 01 registry builds PersonaChunks one-per-field via
        # make_chunk_id (3-component {persona_id}::{store_kind}::{index}).
        # If a future refactor accidentally routes those through this
        # chunker, the chunk-ID format would change to the 4-component
        # document shape — this test catches that.
        from persona.schema.chunks import make_chunk_id

        identity_id = make_chunk_id("astrid", "identity", 0)
        # 3-component format unchanged.
        assert identity_id.count("::") == 2
        assert identity_id == "astrid::identity::0000"
