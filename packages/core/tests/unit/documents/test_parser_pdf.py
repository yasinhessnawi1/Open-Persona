"""Tests for ``persona.documents.parsers.pdf`` (spec 14 T10).

The text-extraction path only. T11's parametrised empty-extraction test
asserts that the no-text-layer **flag is set** for a scanned-like PDF; the
vision-handoff trigger is wired here via :attr:`ParseResult.needs_vision_handoff`
but the actual handoff lands in T21 (gated on Spec 13's `ImageContent`).

Per the user's framing note: until T21 lands, the parser sets
``needs_vision_handoff=True`` on scanned-PDFs; the document service (T13)
returns a clean "scanned PDF — vision support not yet wired in this
build" error to the user (Spec 13 fail-loud discipline applied at the
Spec 14 interim state).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from persona.documents.errors import CorruptDocumentError
from persona.documents.parsers import FLAG_NO_TEXT_LAYER
from persona.documents.parsers.pdf import (
    DEFAULT_NO_TEXT_LAYER_THRESHOLD,
    NO_TEXT_LAYER_THRESHOLD_ENV_VAR,
    parse_pdf,
    resolve_no_text_layer_threshold,
)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "documents"


class TestDefaults:
    def test_default_threshold_is_50(self) -> None:
        # D-14-2 lean — conservative; tolerates extraction noise without
        # false-positive routing to expensive vision.
        assert DEFAULT_NO_TEXT_LAYER_THRESHOLD == 50


class TestResolveThreshold:
    def test_no_env_var_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(NO_TEXT_LAYER_THRESHOLD_ENV_VAR, raising=False)
        assert resolve_no_text_layer_threshold() == DEFAULT_NO_TEXT_LAYER_THRESHOLD

    def test_valid_env_var_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(NO_TEXT_LAYER_THRESHOLD_ENV_VAR, "100")
        assert resolve_no_text_layer_threshold() == 100

    def test_malformed_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(NO_TEXT_LAYER_THRESHOLD_ENV_VAR, "not_a_number")
        assert resolve_no_text_layer_threshold() == DEFAULT_NO_TEXT_LAYER_THRESHOLD


class TestSampleTextPdf:
    def test_returns_sections(self) -> None:
        result = parse_pdf(FIXTURE_DIR / "sample-text.pdf")
        assert len(result.sections) > 0

    def test_text_content_preserved(self) -> None:
        result = parse_pdf(FIXTURE_DIR / "sample-text.pdf")
        full = result.full_text
        assert "twelve months" in full
        assert "12000" in full

    def test_page_number_stamped_on_section(self) -> None:
        result = parse_pdf(FIXTURE_DIR / "sample-text.pdf")
        assert result.sections[0].page == 1

    def test_page_count_populated(self) -> None:
        result = parse_pdf(FIXTURE_DIR / "sample-text.pdf")
        assert result.page_count == 1

    def test_no_vision_handoff_for_text_pdf(self) -> None:
        # The text PDF has chars/page well above the threshold.
        result = parse_pdf(FIXTURE_DIR / "sample-text.pdf")
        assert result.needs_vision_handoff is False
        assert FLAG_NO_TEXT_LAYER not in result.flags

    def test_size_bytes_populated(self) -> None:
        result = parse_pdf(FIXTURE_DIR / "sample-text.pdf")
        assert result.size_bytes is not None
        assert result.size_bytes > 0


class TestScannedLikePdf:
    """A PDF below the no-text-layer threshold — sets the vision flag.

    The vision handoff itself is T21's substance; here we assert the flag
    is SET, not that vision is invoked.
    """

    def test_no_text_layer_flag_set(self) -> None:
        # Fixture: 3 pages with ~1 char each → way below 50 chars/page.
        result = parse_pdf(FIXTURE_DIR / "scanned-like.pdf")
        assert FLAG_NO_TEXT_LAYER in result.flags

    def test_needs_vision_handoff_true(self) -> None:
        result = parse_pdf(FIXTURE_DIR / "scanned-like.pdf")
        assert result.needs_vision_handoff is True

    def test_page_count_still_populated(self) -> None:
        result = parse_pdf(FIXTURE_DIR / "scanned-like.pdf")
        assert result.page_count == 3


class TestCorruptPdf:
    def test_corrupt_bytes_raise_corrupt_error(self) -> None:
        with pytest.raises(CorruptDocumentError) as excinfo:
            parse_pdf(FIXTURE_DIR / "corrupt.pdf")
        assert excinfo.value.context["format"] == "pdf"
        assert excinfo.value.context["reason"] in {
            "not_a_pdf",
            "empty_file",
            "unknown",
        }


class TestEmpty:
    def test_empty_file_raises_corrupt(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.pdf"
        empty.write_bytes(b"")
        with pytest.raises(CorruptDocumentError) as excinfo:
            parse_pdf(empty)
        assert excinfo.value.context["reason"] == "empty_file"
