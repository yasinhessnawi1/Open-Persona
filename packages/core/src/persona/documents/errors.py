"""Domain exceptions raised by document ingestion (spec 14).

Three leaf subclasses of :class:`persona.errors.PersonaError`, flat under it
(D-03-1 precedent: introduce an intermediate ``DocumentError`` parent only
when a fourth subclass lands). All carry the standard
``context: dict[str, str]`` keyword so log lines + audit events get
structured data.
"""

from __future__ import annotations

from persona.errors import PersonaError

__all__ = [
    "CorruptDocumentError",
    "MissingDependencyError",
    "UnsupportedFormatError",
]


class UnsupportedFormatError(PersonaError):
    """Raised when an upload's format is not in the supported list.

    The supported formats are PDF / docx / xlsx / csv / txt / md / source
    code (spec 14 §6, §9 criterion #1). ``.pptx`` is deferred from v0.1
    (D-14-X-pptx-deferral); presenting one raises this exception.

    The ``context`` always carries:

    - ``"format"``: the detected MIME type or file extension (best-effort
      lower-cased).
    - ``"filename"``: the uploaded filename (control-char-stripped, truncated
      for audit-log safety).

    Surfaces from :func:`persona.documents.parsers.parse_document` (the
    dispatcher in :mod:`persona.documents.parsers`) and from the API service
    layer (:mod:`persona_api.services.document_service`) at upload-validation
    time. The API translates this into a structured 4xx error following the
    canonical error shape (D-11-14).
    """


class CorruptDocumentError(PersonaError):
    """Raised when a supported-format file cannot be parsed at all.

    The §6 fail-safe convention is *partial extraction + a flag*, NOT raise —
    a docx that loses one table extracts the rest and flags the loss in the
    parse result. This exception is reserved for the genuinely-unrecoverable
    cases: an encrypted PDF without the passphrase, a corrupt xlsx whose
    central directory is unreadable, a docx that ``python-docx`` rejects as
    not-a-docx, etc.

    The ``context`` always carries:

    - ``"format"``: the parser that surfaced the failure
      (``"pdf"`` / ``"docx"`` / ``"xlsx"`` / ``"csv"`` / ``"txt"`` / ``"md"`` /
      ``"code"``).
    - ``"reason"``: a short discriminator
      (``"encrypted"`` / ``"truncated"`` / ``"not_a_zip"`` /
      ``"central_directory_missing"`` / ``"unknown"``).
    - ``"filename"``: the uploaded filename (control-char-stripped).

    Surfaces from :mod:`persona.documents.parsers.pdf` /
    :mod:`persona.documents.parsers.docx` / etc. Parsers catch the
    library-specific exception at their adapter boundary and re-raise as
    this domain type (per the engineering-standards §1 "catch provider
    exceptions at the adapter boundary and re-raise as domain"). The API
    translates this into a structured 4xx error.
    """


class MissingDependencyError(PersonaError):
    """Raised when a parser's underlying library isn't installed.

    The parser libraries (``pypdf`` / ``pypdfium2`` / ``python-docx`` /
    ``openpyxl``) gate behind the ``[documents]`` optional extra
    (D-14-X-documents-extra). A minimal install (``pip install persona-core``
    without the extra) can ``import persona.documents.parsers`` cleanly —
    only the per-format dispatch (T11) raises this when a user tries to
    parse a format whose library isn't installed.

    The ``context`` always carries:

    - ``"format"``: the format that can't be parsed
      (``"pdf"`` / ``"docx"`` / ``"xlsx"``).
    - ``"install_hint"``: the actionable command the user runs to enable
      the format, conventionally ``"pip install persona-core[documents]"``.

    Same minimal-install hygiene pattern as D-02-16 (the local-GPU stack
    ``[local]`` extra) and D-13-X-pillow (Pillow gating to ``persona-api``).
    The API translates this into a structured 4xx error with the install
    hint included so the operator sees the fix immediately.
    """


# NOTE: ``VisionHandoffRequiredError`` lived here as the Phase 5 interim
# contract until T21 landed. T21 wires the real vision handoff (rasterise +
# ``ImageContent``); the exception class is removed per the TODO(T21)
# close-out discipline. Scanned PDFs now succeed via
# :data:`persona.documents.ingest.IngestStrategy.VISION_HANDOFF` and the
# returned :class:`DocumentRef` carries the rasterised pages as
# :class:`~persona.schema.content.ImageContent` references.
