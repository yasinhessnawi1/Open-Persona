"""``pptx`` format handler — was ``builtin/pptx_generation/`` (D-24-1)."""

from __future__ import annotations

from persona.skills.document_generation.protocol import FormatHandler

#: PowerPoint ``.pptx`` — ``python-pptx`` is NOT in the default sandbox template
#: and cannot be installed offline (egress disabled). The SKILL.md degrades this
#: format honestly (offer ``docx``/``md`` instead) until a custom sandbox
#: template ships ``python-pptx``. ``library`` records the unavailable status so
#: callers introspecting the descriptor see it is not a pre-installed route.
PPTX = FormatHandler(
    format_key="pptx",
    output_extension=".pptx",
    library="unavailable-offline",
    supplement_topics=("layouts", "charts", "theme"),
)

__all__ = ["PPTX"]
