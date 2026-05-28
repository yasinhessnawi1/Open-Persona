"""Skills layer — scanner, injector, index renderer, and the synthetic
``use_skill`` activation tool (spec 04).

Public surface:

- :class:`persona.schema.skills.SkillSpec` — the Pydantic model (extended
  additively in spec 04 per D-04-1; spec-01's four-field shape preserved).
- :class:`SkillScanner` — discovers declared skills under one or more
  paths, parses YAML front matter, validates ``tools_required`` against
  the persona's allow-list.
- :func:`render_skill_index` — pure function producing the compact
  "available skills" Markdown block injected into every system prompt.
- :class:`SkillInjector` — enforces the 2000-token-per-turn skill content
  budget; verbatim / summarise / truncate branches.
- :func:`make_use_skill_tool` — factory producing the synthetic
  ``use_skill`` :class:`persona.tools.AsyncTool` (Pattern-1 activation per
  D-04-9). Spec 05's runtime composes this into the toolbox when the
  persona declares skills (D-04-10).
- :func:`count_tokens` — wraps ``tiktoken cl100k_base`` for any downstream
  consumer that needs the same encoder.
- :exc:`SkillManifestError` — raised by the front-matter parser; caught
  by the scanner's per-skill envelope (D-04-4).
"""

from __future__ import annotations

from persona.errors import SkillManifestError
from persona.schema.skills import SkillSpec
from persona.skills._tokens import count_tokens
from persona.skills.index import render_skill_index
from persona.skills.injector import SkillInjector
from persona.skills.scanner import SkillScanner
from persona.skills.use_skill_tool import make_use_skill_tool

__all__ = [
    "SkillInjector",
    "SkillManifestError",
    "SkillScanner",
    "SkillSpec",
    "count_tokens",
    "make_use_skill_tool",
    "render_skill_index",
]
