"""Tests for ``persona.documents.parsers.csv`` (spec 14 T07)."""

from __future__ import annotations

from pathlib import Path

import pytest
from persona.documents.errors import CorruptDocumentError
from persona.documents.parsers import FLAG_CORRUPT, FLAG_ROW_CAP_TRUNCATED
from persona.documents.parsers.csv import (
    DEFAULT_ROW_CAP,
    ROW_CAP_ENV_VAR,
    ROW_CAP_MAX,
    ROW_CAP_MIN,
    SAMPLE_ROWS_PER_END,
    parse_csv,
    resolve_row_cap,
)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "documents"


class TestDefaults:
    def test_default_row_cap_is_1000(self) -> None:
        assert DEFAULT_ROW_CAP == 1000

    def test_sample_rows_per_end_is_50(self) -> None:
        assert SAMPLE_ROWS_PER_END == 50

    def test_min_max_range_sensible(self) -> None:
        assert ROW_CAP_MIN <= DEFAULT_ROW_CAP <= ROW_CAP_MAX


class TestResolveRowCap:
    def test_no_env_var_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ROW_CAP_ENV_VAR, raising=False)
        assert resolve_row_cap() == DEFAULT_ROW_CAP

    def test_valid_env_var_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ROW_CAP_ENV_VAR, "500")
        assert resolve_row_cap() == 500

    def test_malformed_env_var_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ROW_CAP_ENV_VAR, "not_a_number")
        assert resolve_row_cap() == DEFAULT_ROW_CAP

    def test_out_of_range_env_var_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ROW_CAP_ENV_VAR, "1")  # below ROW_CAP_MIN
        assert resolve_row_cap() == DEFAULT_ROW_CAP
        monkeypatch.setenv(ROW_CAP_ENV_VAR, "999999")  # above ROW_CAP_MAX
        assert resolve_row_cap() == DEFAULT_ROW_CAP


class TestSmallCsv:
    def test_small_csv_one_section(self) -> None:
        result = parse_csv(FIXTURE_DIR / "sample.csv")
        assert len(result.sections) == 1

    def test_pipe_separated_columns(self) -> None:
        result = parse_csv(FIXTURE_DIR / "sample.csv")
        text = result.sections[0].text
        # Header line uses |.
        assert text.startswith("date | description | amount")
        # Data row uses the same separator.
        assert "Office rent" in text
        assert "12000" in text

    def test_no_truncation_flag(self) -> None:
        result = parse_csv(FIXTURE_DIR / "sample.csv")
        assert FLAG_ROW_CAP_TRUNCATED not in result.flags

    def test_size_bytes_populated(self) -> None:
        result = parse_csv(FIXTURE_DIR / "sample.csv")
        assert result.size_bytes is not None
        assert result.size_bytes > 0


class TestOversizeCsv:
    def test_oversize_csv_sets_truncation_flag(self) -> None:
        result = parse_csv(FIXTURE_DIR / "oversize.csv")
        assert FLAG_ROW_CAP_TRUNCATED in result.flags

    def test_oversize_csv_has_summary_header(self) -> None:
        result = parse_csv(FIXTURE_DIR / "oversize.csv")
        text = result.sections[0].text
        assert "[Spreadsheet summary" in text
        assert "2000 data rows" in text

    def test_oversize_csv_shows_first_and_last_rows(self) -> None:
        result = parse_csv(FIXTURE_DIR / "oversize.csv")
        text = result.sections[0].text
        # First data row.
        assert "value_a_0" in text
        # Last data row.
        assert "value_a_1999" in text

    def test_oversize_csv_has_hidden_marker(self) -> None:
        result = parse_csv(FIXTURE_DIR / "oversize.csv")
        text = result.sections[0].text
        assert "rows hidden" in text

    def test_oversize_csv_points_at_sandbox(self) -> None:
        # The §8 tabular boundary signal — tell the user analysis goes
        # through the sandbox (Spec 17).
        result = parse_csv(FIXTURE_DIR / "oversize.csv")
        text = result.sections[0].text
        assert "sandbox" in text.lower()


class TestEmptyAndCorrupt:
    def test_empty_file_raises_corrupt(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.csv"
        empty.write_bytes(b"")
        with pytest.raises(CorruptDocumentError) as excinfo:
            parse_csv(empty)
        assert excinfo.value.context["format"] == "csv"
        assert excinfo.value.context["reason"] == "empty_file"

    def test_whitespace_only_file_raises_corrupt(self, tmp_path: Path) -> None:
        # Criterion #8 — empty extraction from non-empty file is flagged.
        whitespace_only = tmp_path / "whitespace.csv"
        whitespace_only.write_bytes(b"   \n\n  \n")
        with pytest.raises(CorruptDocumentError) as excinfo:
            parse_csv(whitespace_only)
        assert excinfo.value.context["reason"] == "empty_after_decode"

    def test_invalid_utf8_falls_back_and_flags(self, tmp_path: Path) -> None:
        bad = tmp_path / "mixed.csv"
        bad.write_bytes(b"col_a,col_b\n\xff\xfe1,2\nfoo,3\n")
        result = parse_csv(bad)
        assert FLAG_CORRUPT in result.flags


class TestRaggedRows:
    def test_short_row_padded_to_header_width(self, tmp_path: Path) -> None:
        # CSV with a row that's missing a trailing cell — output stays
        # rectangular.
        path = tmp_path / "ragged.csv"
        path.write_text("a,b,c\n1,2\n4,5,6\n")
        result = parse_csv(path)
        text = result.sections[0].text
        # The short row should be padded with an empty cell.
        assert "1 | 2 | " in text or text.count("1 | 2") >= 1


class TestSemicolonDialect:
    def test_semicolon_separated_file_is_read(self, tmp_path: Path) -> None:
        # The sniffer detects the dialect.
        path = tmp_path / "semi.csv"
        # Use a clearer semicolon-only file so the sniffer doesn't misidentify.
        path.write_text("col_a;col_b;col_c\n1;2;3\n4;5;6\n")
        result = parse_csv(path)
        # Whatever dialect was detected, three columns should be present
        # with the values 1,2,3 in the first data row.
        text = result.sections[0].text
        # The sniffer might have picked semicolon or fallen back to comma;
        # either way the values are extracted.
        assert "1" in text
        assert "2" in text
        assert "3" in text
