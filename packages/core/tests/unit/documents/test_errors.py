"""Tests for ``persona.documents.errors`` (spec 14 T02).

Two leaf exceptions flat under ``PersonaError`` (D-03-1 precedent — introduce
a ``DocumentError`` intermediate only when a third subclass lands). Both
carry the standard ``context: dict[str, str]`` keyword shape so log lines
and audit events get structured data.
"""

from __future__ import annotations

import pytest
from persona.documents import CorruptDocumentError, UnsupportedFormatError
from persona.errors import PersonaError


class TestUnsupportedFormatError:
    def test_is_persona_error_subclass(self) -> None:
        assert issubclass(UnsupportedFormatError, PersonaError)

    def test_is_flat_under_persona_error(self) -> None:
        # D-03-1 precedent — direct subclass of PersonaError, no
        # intermediate DocumentError parent yet.
        assert UnsupportedFormatError.__mro__[1] is PersonaError

    def test_carries_structured_context(self) -> None:
        err = UnsupportedFormatError(
            "rar not supported",
            context={"format": "rar", "filename": "archive.rar"},
        )
        assert err.context == {"format": "rar", "filename": "archive.rar"}

    def test_str_includes_context(self) -> None:
        err = UnsupportedFormatError(
            "rar not supported",
            context={"format": "rar", "filename": "archive.rar"},
        )
        rendered = str(err)
        assert "rar not supported" in rendered
        assert "format=rar" in rendered
        assert "filename=archive.rar" in rendered

    def test_can_be_caught_as_persona_error(self) -> None:
        # Callers that want to handle any persona-core error can use the
        # base type; this guards that path against an accidental
        # exception-hierarchy break.
        with pytest.raises(PersonaError):
            raise UnsupportedFormatError("no", context={"format": "rar", "filename": "x.rar"})

    def test_empty_context_renders_message_only(self) -> None:
        err = UnsupportedFormatError("oops")
        assert str(err) == "oops"


class TestCorruptDocumentError:
    def test_is_persona_error_subclass(self) -> None:
        assert issubclass(CorruptDocumentError, PersonaError)

    def test_is_flat_under_persona_error(self) -> None:
        assert CorruptDocumentError.__mro__[1] is PersonaError

    def test_carries_format_reason_filename_context(self) -> None:
        err = CorruptDocumentError(
            "encrypted PDF",
            context={
                "format": "pdf",
                "reason": "encrypted",
                "filename": "tenancy.pdf",
            },
        )
        assert err.context["format"] == "pdf"
        assert err.context["reason"] == "encrypted"
        assert err.context["filename"] == "tenancy.pdf"

    def test_str_includes_context(self) -> None:
        err = CorruptDocumentError(
            "encrypted PDF",
            context={"format": "pdf", "reason": "encrypted", "filename": "x.pdf"},
        )
        rendered = str(err)
        assert "encrypted PDF" in rendered
        assert "reason=encrypted" in rendered

    def test_can_be_caught_as_persona_error(self) -> None:
        with pytest.raises(PersonaError):
            raise CorruptDocumentError(
                "no", context={"format": "pdf", "reason": "x", "filename": "x.pdf"}
            )

    def test_two_subclasses_are_distinct(self) -> None:
        # Both are leaves under PersonaError but not under each other —
        # callers can disambiguate the failure mode (4xx for unsupported;
        # 4xx with different code or 5xx for corrupt).
        assert not issubclass(CorruptDocumentError, UnsupportedFormatError)
        assert not issubclass(UnsupportedFormatError, CorruptDocumentError)
