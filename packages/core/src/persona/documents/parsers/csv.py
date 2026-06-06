"""CSV parser (spec 14 T07).

Stdlib ``csv`` — no extra dependency. Produces a text-table representation
(headers + ``|``-separated rows) under D-14-3's row cap. Oversize sheets
emit a summary + first/last 50 rows + the
:data:`persona.documents.parsers.FLAG_ROW_CAP_TRUNCATED` flag so the model
sees that only a sample is present and can route the user toward the
sandbox (Spec 17) for full-data computation per §8.

The row cap defaults to **1000** (D-14-3) and is overridable via the
``PERSONA_DOC_SPREADSHEET_ROW_CAP`` environment variable so operators can
tune for tight memory or generous frontier-context deployments. Outside
the range ``[10, 50000]`` is clamped.
"""

from __future__ import annotations

import csv
import io
import os
from typing import TYPE_CHECKING

from persona.documents.chunker import DocumentSection
from persona.documents.errors import CorruptDocumentError
from persona.documents.parsers import (
    FLAG_CORRUPT,
    FLAG_ROW_CAP_TRUNCATED,
    ParseResult,
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "DEFAULT_ROW_CAP",
    "ROW_CAP_ENV_VAR",
    "ROW_CAP_MAX",
    "ROW_CAP_MIN",
    "SAMPLE_ROWS_PER_END",
    "parse_csv",
    "resolve_row_cap",
]

#: Default row cap per D-14-3.
DEFAULT_ROW_CAP: int = 1000

#: Operator env override.
ROW_CAP_ENV_VAR: str = "PERSONA_DOC_SPREADSHEET_ROW_CAP"

#: Minimum row cap (so the env override can't degenerate the path).
ROW_CAP_MIN: int = 10

#: Maximum row cap (so memory stays bounded).
ROW_CAP_MAX: int = 50_000

#: Rows shown at each end when truncating oversize CSVs.
SAMPLE_ROWS_PER_END: int = 50


def resolve_row_cap() -> int:
    """Resolve the row cap from the env override + clamp into the valid range.

    Returns the default when the env var is unset, malformed, or out of range.
    Fail-safe — never raises (operator misconfiguration shouldn't crash
    upload).
    """
    raw = os.environ.get(ROW_CAP_ENV_VAR)
    if not raw:
        return DEFAULT_ROW_CAP
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_ROW_CAP
    if parsed < ROW_CAP_MIN or parsed > ROW_CAP_MAX:
        return DEFAULT_ROW_CAP
    return parsed


def parse_csv(path: Path) -> ParseResult:
    """Parse a CSV file to text-table form.

    Args:
        path: Path to the file.

    Returns:
        :class:`ParseResult` with one :class:`DocumentSection` carrying the
        formatted table. The :attr:`ParseResult.flags` tuple includes
        :data:`FLAG_ROW_CAP_TRUNCATED` if the source exceeded the row cap.

    Raises:
        CorruptDocumentError: The file is empty, all-whitespace, or yields
            no rows after CSV parsing (criterion #8).
    """
    size_bytes = path.stat().st_size
    filename = path.name

    raw_bytes = path.read_bytes()
    if not raw_bytes:
        raise CorruptDocumentError(
            "file is empty",
            context={
                "format": "csv",
                "reason": "empty_file",
                "filename": filename,
            },
        )

    text_strict_failed = False
    try:
        text = raw_bytes.decode("utf-8-sig")  # tolerate BOM
    except UnicodeDecodeError:
        text_strict_failed = True
        text = raw_bytes.decode("utf-8", errors="replace")

    if not text.strip():
        raise CorruptDocumentError(
            "extraction yielded no text",
            context={
                "format": "csv",
                "reason": "empty_after_decode",
                "filename": filename,
            },
        )

    flags: list[str] = []
    if text_strict_failed:
        flags.append(FLAG_CORRUPT)

    rows = _read_rows(text)
    if not rows:
        raise CorruptDocumentError(
            "CSV produced zero rows after parsing",
            context={
                "format": "csv",
                "reason": "no_rows",
                "filename": filename,
            },
        )

    row_cap = resolve_row_cap()
    section_text, truncated = _format_table(rows, row_cap=row_cap)
    if truncated:
        flags.append(FLAG_ROW_CAP_TRUNCATED)

    return ParseResult(
        sections=(DocumentSection(text=section_text),),
        flags=tuple(flags),
        size_bytes=size_bytes,
    )


def _read_rows(text: str) -> list[list[str]]:
    """Read CSV rows from text. Sniffer-friendly default fallback."""
    sample = text[:2048]
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect=dialect)
    return [[(cell or "").strip() for cell in row] for row in reader if any(row)]


def _format_table(rows: list[list[str]], *, row_cap: int) -> tuple[str, bool]:
    """Render rows as a pipe-separated text table.

    For oversize input, emit:

    - A one-line summary header (column count + total row count).
    - The header row.
    - The first :data:`SAMPLE_ROWS_PER_END` data rows.
    - A ``... N rows hidden ...`` separator.
    - The last :data:`SAMPLE_ROWS_PER_END` data rows.

    Returns:
        ``(formatted_text, truncated)`` — ``truncated`` is ``True`` when
        the row cap was exceeded.
    """
    if not rows:
        return ("", False)

    header = rows[0]
    data_rows = rows[1:]
    total_data_rows = len(data_rows)

    if total_data_rows + 1 <= row_cap:
        formatted = " | ".join(header)
        for row in data_rows:
            formatted += "\n" + " | ".join(_normalise_row(row, width=len(header)))
        return (formatted, False)

    # Oversize: keep header + first N + last N.
    head = data_rows[:SAMPLE_ROWS_PER_END]
    tail = data_rows[-SAMPLE_ROWS_PER_END:]
    hidden_count = total_data_rows - len(head) - len(tail)

    lines = [
        f"[Spreadsheet summary — {len(header)} columns, {total_data_rows} data rows]",
        " | ".join(header),
    ]
    for row in head:
        lines.append(" | ".join(_normalise_row(row, width=len(header))))
    if hidden_count > 0:
        lines.append(f"... {hidden_count} rows hidden ...")
    for row in tail:
        lines.append(" | ".join(_normalise_row(row, width=len(header))))
    lines.append(
        "(Truncated for prompt budget. Use the code-execution sandbox for "
        "computation over the full data — spec §8 tabular boundary.)"
    )
    return ("\n".join(lines), True)


def _normalise_row(row: list[str], *, width: int) -> list[str]:
    """Pad or truncate a row to ``width`` cells so the table is rectangular."""
    if len(row) < width:
        return [*row, *([""] * (width - len(row)))]
    if len(row) > width:
        return row[:width]
    return row
