"""Document ingestion — parsers, chunker, ingest strategy (spec 14).

Public surface re-exports the user-facing types. The
:mod:`persona.documents.parsers` package is lazy-imported per format
(D-14-X-documents-extra) so a minimal install (``pip install persona-core``
without the ``[documents]`` extra) stays minimal — importing
``persona.documents`` does NOT load ``pypdf`` / ``python-docx`` / ``openpyxl`` /
``pypdfium2``.

Documents are conversation-scoped, NOT persona-scoped (Dominant Concern #1).
See ``docs/specs/phase2/spec_14/decisions.md`` for the four load-bearing
locks that govern this module.
"""

from __future__ import annotations

from persona.documents.errors import (
    CorruptDocumentError,
    MissingDependencyError,
    UnsupportedFormatError,
)

__all__ = [
    "CorruptDocumentError",
    "MissingDependencyError",
    "UnsupportedFormatError",
]
