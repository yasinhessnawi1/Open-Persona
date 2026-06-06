"""Document parsers (per-format) + format dispatcher.

The parser dispatcher + shared :class:`ParseResult` shape. Per-format
parser modules (``text.py`` / ``csv.py`` / ``docx.py`` / ``xlsx.py`` /
``pdf.py``) are **lazy-imported** by :func:`parse_document` so a minimal
install stays minimal — importing this package does NOT load ``pypdf`` /
``python-docx`` / ``openpyxl`` / ``pypdfium2`` (D-14-X-documents-extra +
the user's T11 framing note).

Dispatch is by **file extension** (lowercased) on the supplied path. The
dispatcher knows three classes of format:

- **Stdlib-only** (``.txt`` / ``.md`` / ``.csv`` / source code): always
  available; the parser modules import cleanly without the
  ``[documents]`` extra.
- **Extra-gated** (``.pdf`` / ``.docx`` / ``.xlsx``): the parser modules
  import cleanly, but their first underlying-library access raises
  :class:`~persona.documents.errors.MissingDependencyError` with a
  ``"pip install persona-core[documents]"`` install hint.
- **Unsupported** (``.pptx`` per D-14-X-pptx-deferral, anything not in
  the dispatch table): :class:`~persona.documents.errors.UnsupportedFormatError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

# Runtime ref required: Pydantic v2 introspects ``sections`` field at validation
# time so DocumentSection must be importable at runtime, not just for typing.
from persona.documents.chunker import DocumentSection  # noqa: TC001
from persona.documents.errors import UnsupportedFormatError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

__all__ = [
    "FLAG_CORRUPT",
    "FLAG_EMPTY_EXTRACTION",
    "FLAG_NO_TEXT_LAYER",
    "FLAG_PARTIAL_EXTRACTION",
    "FLAG_ROW_CAP_TRUNCATED",
    "ParseResult",
    "SUPPORTED_EXTENSIONS",
    "parse_document",
]

#: Flag set when extraction succeeded but produced less than the source
#: file's apparent content (e.g. a docx with one unreadable table; a PDF
#: page that yielded garbled text).
FLAG_PARTIAL_EXTRACTION: str = "partial_extraction"

#: Flag set when the source file appears non-empty but extraction yielded
#: no text. For PDFs this triggers the vision handoff (T21); for other
#: formats it surfaces as a :class:`persona.documents.errors.CorruptDocumentError`
#: at the parser boundary.
FLAG_EMPTY_EXTRACTION: str = "empty_extraction"

#: Flag set on PDFs whose text-layer extraction is below D-14-2's coverage
#: threshold. The ingest layer (T12 → T21) treats this as the trigger for
#: the scanned-PDF → vision boundary.
FLAG_NO_TEXT_LAYER: str = "no_text_layer"

#: Flag set on CSV / XLSX parsers when the input exceeded D-14-3's row cap
#: and only the summary + sample rows are present.
FLAG_ROW_CAP_TRUNCATED: str = "row_cap_truncated"

#: Flag set when extraction caught and recovered from corruption (e.g. a
#: UTF-8 decode error replaced bytes; an xlsx with an unreadable sheet).
FLAG_CORRUPT: str = "corrupt"


class ParseResult(BaseModel):
    """A parser's output — natural-boundary sections + format metadata.

    The :class:`persona.documents.chunker.chunk_document` function takes
    :attr:`sections` directly. The ingest layer (T12) reads :attr:`flags`
    to decide between text-path / vision-handoff (PDFs) / corrupt-error
    surfaces, and reads :attr:`page_count` / :attr:`sheet_names` /
    :attr:`size_bytes` for the auto-generated "what's in scope" synopsis
    (T16 / D-14-X-synopsis-source).

    All fields are immutable so a ParseResult can be safely cached or
    persisted alongside the source file.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sections: tuple[DocumentSection, ...]
    flags: tuple[str, ...] = ()
    page_count: int | None = Field(default=None, ge=0)
    sheet_names: tuple[str, ...] | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    #: Set ``True`` by the PDF parser when the text-layer extraction is below
    #: D-14-2's coverage threshold (chars-per-page < 50). Read by the ingest
    #: layer (T12 → T21): if ``True`` and T21's vision handoff is wired, the
    #: pages are rasterised and routed to a vision-capable model; if T21 is
    #: not yet wired in the current build (interim state), the document
    #: service surfaces a clean "scanned PDF — vision support not yet wired
    #: in this build" error to the user (Spec 13 fail-loud discipline applied
    #: at the Spec 14 interim state).
    needs_vision_handoff: bool = False

    @property
    def full_text(self) -> str:
        """Concatenated full text — used for whole-injection (T12 small-doc path)."""
        return "\n\n".join(s.text for s in self.sections if s.text.strip())

    @property
    def is_empty(self) -> bool:
        """True when the parser produced no usable text content."""
        return not any(s.text.strip() for s in self.sections)


# Mapping of supported extension → (parser-module name, function name).
# Lazy imports happen inside :func:`parse_document` so this dict can be
# inspected (and the extension set queried) without triggering any
# third-party imports.
_DISPATCH_TABLE: dict[str, tuple[str, str]] = {
    # Stdlib-only (always available; D-14-X-documents-extra not required).
    ".txt": ("persona.documents.parsers.text", "parse_text"),
    ".md": ("persona.documents.parsers.text", "parse_text"),
    ".csv": ("persona.documents.parsers.csv", "parse_csv"),
    # Source code (extension list matches text.py's CODE_FENCE_LANGUAGE_BY_EXTENSION).
    ".py": ("persona.documents.parsers.text", "parse_text"),
    ".js": ("persona.documents.parsers.text", "parse_text"),
    ".jsx": ("persona.documents.parsers.text", "parse_text"),
    ".ts": ("persona.documents.parsers.text", "parse_text"),
    ".tsx": ("persona.documents.parsers.text", "parse_text"),
    ".rs": ("persona.documents.parsers.text", "parse_text"),
    ".go": ("persona.documents.parsers.text", "parse_text"),
    ".java": ("persona.documents.parsers.text", "parse_text"),
    ".kt": ("persona.documents.parsers.text", "parse_text"),
    ".swift": ("persona.documents.parsers.text", "parse_text"),
    ".rb": ("persona.documents.parsers.text", "parse_text"),
    ".php": ("persona.documents.parsers.text", "parse_text"),
    ".c": ("persona.documents.parsers.text", "parse_text"),
    ".cc": ("persona.documents.parsers.text", "parse_text"),
    ".cpp": ("persona.documents.parsers.text", "parse_text"),
    ".h": ("persona.documents.parsers.text", "parse_text"),
    ".hpp": ("persona.documents.parsers.text", "parse_text"),
    ".cs": ("persona.documents.parsers.text", "parse_text"),
    ".scala": ("persona.documents.parsers.text", "parse_text"),
    ".sh": ("persona.documents.parsers.text", "parse_text"),
    ".bash": ("persona.documents.parsers.text", "parse_text"),
    ".zsh": ("persona.documents.parsers.text", "parse_text"),
    ".sql": ("persona.documents.parsers.text", "parse_text"),
    ".html": ("persona.documents.parsers.text", "parse_text"),
    ".css": ("persona.documents.parsers.text", "parse_text"),
    ".scss": ("persona.documents.parsers.text", "parse_text"),
    ".yaml": ("persona.documents.parsers.text", "parse_text"),
    ".yml": ("persona.documents.parsers.text", "parse_text"),
    ".toml": ("persona.documents.parsers.text", "parse_text"),
    ".json": ("persona.documents.parsers.text", "parse_text"),
    ".xml": ("persona.documents.parsers.text", "parse_text"),
    # Extra-gated (D-14-X-documents-extra): module import is fine without
    # the extra; the lazy underlying-library import in each parser raises
    # MissingDependencyError with an install hint.
    ".pdf": ("persona.documents.parsers.pdf", "parse_pdf"),
    ".docx": ("persona.documents.parsers.docx", "parse_docx"),
    ".xlsx": ("persona.documents.parsers.xlsx", "parse_xlsx"),
}

#: Public view of the supported extension set.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(_DISPATCH_TABLE.keys())


def parse_document(path: Path) -> ParseResult:
    """Parse a document by file extension, dispatching to the per-format parser.

    The dispatcher table is by file extension (lowercased). Unsupported
    extensions raise :class:`~persona.documents.errors.UnsupportedFormatError`
    immediately (no library load attempted). For extra-gated formats
    (``.pdf`` / ``.docx`` / ``.xlsx``) the per-format parser raises
    :class:`~persona.documents.errors.MissingDependencyError` if the
    underlying library isn't installed.

    Args:
        path: Path to the document.

    Returns:
        :class:`ParseResult` for the matched format.

    Raises:
        UnsupportedFormatError: Extension not in :data:`SUPPORTED_EXTENSIONS`
            (e.g. ``.pptx`` — D-14-X-pptx-deferral — or any unknown
            extension).
        MissingDependencyError: The format's parser library isn't installed
            (e.g. ``.pdf`` without ``[documents]``).
        CorruptDocumentError: The file can't be parsed (corrupt / empty /
            encrypted), per criterion #8.
    """
    extension = path.suffix.lower()
    if extension not in _DISPATCH_TABLE:
        raise UnsupportedFormatError(
            "unsupported document format",
            context={
                "format": extension or "unknown",
                "filename": path.name,
            },
        )
    module_name, function_name = _DISPATCH_TABLE[extension]
    parser_fn = _load_parser(module_name, function_name)
    return parser_fn(path)


def _load_parser(module_name: str, function_name: str) -> Callable[[Path], ParseResult]:
    """Lazy-load the parser function from its module by name.

    Module import never triggers a third-party library load (the parser
    modules import their libraries lazily inside the parse function).
    """
    import importlib  # noqa: PLC0415 — deliberate lazy import

    module = importlib.import_module(module_name)
    fn: Callable[[Path], ParseResult] = getattr(module, function_name)
    return fn
