"""``pdf`` format handler — was ``builtin/pdf_generation/`` (D-24-1)."""

from __future__ import annotations

from persona.skills.document_generation.protocol import FormatHandler

#: PDF report via the pre-installed ``matplotlib`` PdfPages backend. The sandbox
#: has egress disabled (no ``pip install``), and ``reportlab`` is NOT in the
#: default template, so the SKILL.md teaches matplotlib's PdfPages for offline
#: PDF (degrade to ``docx`` when rich text/tables exceed it).
PDF = FormatHandler(
    format_key="pdf",
    output_extension=".pdf",
    library="matplotlib",
    supplement_topics=("flowables", "pagination", "images"),
)

__all__ = ["PDF"]
