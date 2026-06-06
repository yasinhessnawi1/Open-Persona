"""Tests for ``persona.documents.parsers.xlsx`` (spec 14 T09)."""

from __future__ import annotations

from pathlib import Path

import pytest
from persona.documents.errors import CorruptDocumentError
from persona.documents.parsers import FLAG_ROW_CAP_TRUNCATED
from persona.documents.parsers.xlsx import parse_xlsx

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "documents"


class TestSampleXlsx:
    def test_one_section_per_sheet(self) -> None:
        result = parse_xlsx(FIXTURE_DIR / "sample.xlsx")
        # The fixture has Q1 + Q2 = 2 sheets.
        assert len(result.sections) == 2

    def test_sheet_name_stamped_on_section(self) -> None:
        result = parse_xlsx(FIXTURE_DIR / "sample.xlsx")
        sheet_names = {s.sheet for s in result.sections}
        assert sheet_names == {"Q1", "Q2"}

    def test_sheet_names_listed_on_result(self) -> None:
        result = parse_xlsx(FIXTURE_DIR / "sample.xlsx")
        assert result.sheet_names == ("Q1", "Q2")

    def test_pipe_separated_table(self) -> None:
        result = parse_xlsx(FIXTURE_DIR / "sample.xlsx")
        q1 = next(s for s in result.sections if s.sheet == "Q1")
        assert q1.text.startswith("month | revenue | cost")
        assert "Jan" in q1.text
        assert "10000" in q1.text

    def test_no_truncation_flag_on_small_workbook(self) -> None:
        result = parse_xlsx(FIXTURE_DIR / "sample.xlsx")
        assert FLAG_ROW_CAP_TRUNCATED not in result.flags

    def test_size_bytes_populated(self) -> None:
        result = parse_xlsx(FIXTURE_DIR / "sample.xlsx")
        assert result.size_bytes is not None
        assert result.size_bytes > 0


class TestOversizeXlsx:
    def test_oversize_sheet_sets_truncation_flag(self) -> None:
        result = parse_xlsx(FIXTURE_DIR / "oversize.xlsx")
        assert FLAG_ROW_CAP_TRUNCATED in result.flags

    def test_oversize_sheet_has_summary_header(self) -> None:
        result = parse_xlsx(FIXTURE_DIR / "oversize.xlsx")
        big = next(s for s in result.sections if s.sheet == "big")
        assert "[Sheet summary" in big.text
        assert "2000 data rows" in big.text

    def test_oversize_sheet_shows_first_and_last(self) -> None:
        result = parse_xlsx(FIXTURE_DIR / "oversize.xlsx")
        big = next(s for s in result.sections if s.sheet == "big")
        # First data row.
        assert "row 0" in big.text
        # Last data row.
        assert "row 1999" in big.text

    def test_oversize_sheet_points_at_sandbox(self) -> None:
        # §8 tabular-boundary signal: point the user at Spec 17.
        result = parse_xlsx(FIXTURE_DIR / "oversize.xlsx")
        big = next(s for s in result.sections if s.sheet == "big")
        assert "sandbox" in big.text.lower()


class TestCorruptXlsx:
    def test_corrupt_bytes_raise_corrupt_error(self) -> None:
        with pytest.raises(CorruptDocumentError) as excinfo:
            parse_xlsx(FIXTURE_DIR / "corrupt.xlsx")
        assert excinfo.value.context["format"] == "xlsx"
        assert excinfo.value.context["reason"] in {
            "not_an_xlsx",
            "not_a_zip",
            "unknown",
        }


class TestEmpty:
    def test_empty_file_raises_corrupt(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.xlsx"
        empty.write_bytes(b"")
        with pytest.raises(CorruptDocumentError) as excinfo:
            parse_xlsx(empty)
        assert excinfo.value.context["reason"] == "empty_file"

    def test_workbook_with_no_text_raises_empty_after_decode(self, tmp_path: Path) -> None:
        import openpyxl  # noqa: PLC0415

        wb = openpyxl.Workbook()
        # Strip the default sheet content — just an empty workbook.
        out = tmp_path / "blank.xlsx"
        wb.save(str(out))
        with pytest.raises(CorruptDocumentError) as excinfo:
            parse_xlsx(out)
        assert excinfo.value.context["reason"] == "empty_after_decode"
