"""The runtime extraction pipeline (Spec K2 — the LLM half of the write paths).

The persona-RUNTIME implementation behind the persona-CORE ``Extractor`` contract:
the versioned extraction prompt (D-K2-3), the lenient JSON parser, and
``LlmExtractor`` (build prompt → one model call → grounded candidates). Core owns
the shapes; this owns the LLM (the ratified layering split). Candidates flow on to
K0's ``GraphStore.merge`` via the K2 orchestrator (T3+).
"""

from __future__ import annotations

from persona_runtime.extraction.direct_write import make_record_user_fact_tool
from persona_runtime.extraction.entity_judge import LlmEntityJudge
from persona_runtime.extraction.parse import parse_candidates
from persona_runtime.extraction.pipeline import LlmExtractor
from persona_runtime.extraction.prompt import (
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_SYSTEM_PROMPT,
    build_extraction_messages,
)
from persona_runtime.extraction.resolution import EntityResolver
from persona_runtime.extraction.synthesizer import Synthesizer, build_synthesizer
from persona_runtime.extraction.update import UpdateResolver
from persona_runtime.extraction.windowing import Window, build_window

__all__ = [
    "EXTRACTION_PROMPT_VERSION",
    "EXTRACTION_SYSTEM_PROMPT",
    "EntityResolver",
    "LlmEntityJudge",
    "LlmExtractor",
    "Synthesizer",
    "UpdateResolver",
    "Window",
    "build_extraction_messages",
    "build_synthesizer",
    "build_window",
    "make_record_user_fact_tool",
    "parse_candidates",
]
