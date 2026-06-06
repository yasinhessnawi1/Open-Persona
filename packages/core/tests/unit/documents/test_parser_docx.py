"""Tests for ``persona.documents.parsers.docx`` (spec 14 T08)."""

from __future__ import annotations

from pathlib import Path

import pytest
from persona.documents.errors import CorruptDocumentError
from persona.documents.parsers.docx import parse_docx

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "documents"


class TestSampleDocx:
    def test_returns_sections(self) -> None:
        result = parse_docx(FIXTURE_DIR / "sample.docx")
        assert len(result.sections) > 0

    def test_headings_stamped_on_sections(self) -> None:
        result = parse_docx(FIXTURE_DIR / "sample.docx")
        section_headings = [s.section for s in result.sections if s.section]
        # The fixture has H1 + three H2 headings plus one Schedule table section.
        assert "Tenancy Memo" in section_headings
        assert any("Overview" in h for h in section_headings)
        assert any("Term" in h for h in section_headings)
        assert any("Rent" in h for h in section_headings)

    def test_paragraph_body_preserved(self) -> None:
        result = parse_docx(FIXTURE_DIR / "sample.docx")
        full = " ".join(s.text for s in result.sections)
        assert "twelve months" in full
        assert "12,000" in full

    def test_table_rendered_as_pipe_separated(self) -> None:
        result = parse_docx(FIXTURE_DIR / "sample.docx")
        full = "\n".join(s.text for s in result.sections)
        # Header row pipe-separated.
        assert "date | description | amount" in full
        # At least one data row visible.
        assert "12000" in full

    def test_size_bytes_populated(self) -> None:
        result = parse_docx(FIXTURE_DIR / "sample.docx")
        assert result.size_bytes is not None
        assert result.size_bytes > 0

    def test_no_flags_on_clean_input(self) -> None:
        result = parse_docx(FIXTURE_DIR / "sample.docx")
        assert result.flags == ()


class TestCorruptDocx:
    def test_corrupt_bytes_raise_corrupt_error(self) -> None:
        with pytest.raises(CorruptDocumentError) as excinfo:
            parse_docx(FIXTURE_DIR / "corrupt.docx")
        assert excinfo.value.context["format"] == "docx"
        # reason discriminator should map to a known bucket.
        assert excinfo.value.context["reason"] in {
            "not_a_docx",
            "not_a_zip",
            "unknown",
        }
        assert "corrupt.docx" in excinfo.value.context["filename"]


class TestEmpty:
    def test_empty_file_raises_corrupt(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.docx"
        empty.write_bytes(b"")
        with pytest.raises(CorruptDocumentError) as excinfo:
            parse_docx(empty)
        assert excinfo.value.context["reason"] == "empty_file"

    def test_docx_with_no_text_raises_empty_after_decode(self, tmp_path: Path) -> None:
        # Build a real but empty docx (no paragraphs / tables with text).
        from docx import Document  # noqa: PLC0415

        doc = Document()
        out = tmp_path / "blank.docx"
        doc.save(str(out))
        with pytest.raises(CorruptDocumentError) as excinfo:
            parse_docx(out)
        assert excinfo.value.context["reason"] == "empty_after_decode"
