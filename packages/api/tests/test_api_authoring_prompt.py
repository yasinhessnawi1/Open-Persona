"""Unit tests for the versioned authoring prompt (spec 10, T01, §3 / D-10-4).

No DB, no model. Covers: the §3.2 tool/skill injection substitution, the
message roles/order for both the authoring and refinement prompts, and the
guard that both few-shot example personas themselves validate as v1.0 (so they
can't silently drift invalid — the cross-model compliance lever, S10-2).
"""

from __future__ import annotations

import yaml
from persona.schema.persona import Persona
from persona_api.services.authoring_prompt import (
    AUTHORING_PROMPT_VERSION,
    EXAMPLE_COMPLEX_YAML,
    EXAMPLE_SIMPLE_YAML,
    QUESTIONS_MARKER,
    build_authoring_prompt,
    build_refinement_prompt,
)

_TOOLS = ["web_search", "web_fetch"]
_SKILLS = ["web_research", "document_drafting"]


def test_prompt_version_is_set() -> None:
    assert isinstance(AUTHORING_PROMPT_VERSION, str)
    assert AUTHORING_PROMPT_VERSION


def test_build_authoring_prompt_injects_tools_and_skills() -> None:
    msgs = build_authoring_prompt("a cooking assistant", _TOOLS, _SKILLS)
    assert [m.role for m in msgs] == ["system", "user"]
    system = msgs[0].content
    # placeholders are substituted (no literal leftovers)
    assert "[AVAILABLE_TOOLS]" not in system
    assert "[AVAILABLE_SKILLS]" not in system
    assert "- web_search" in system
    assert "- web_research" in system
    assert msgs[1].content == "a cooking assistant"


def test_build_authoring_prompt_empty_catalog_says_none() -> None:
    msgs = build_authoring_prompt("x", [], [])
    system = msgs[0].content
    assert "[AVAILABLE_TOOLS]" not in system
    assert "(none available)" in system


def test_authoring_prompt_states_the_questions_marker() -> None:
    system = build_authoring_prompt("x", _TOOLS, _SKILLS)[0].content
    assert QUESTIONS_MARKER in system


def test_build_refinement_prompt_message_sequence() -> None:
    msgs = build_refinement_prompt(
        current_yaml=EXAMPLE_SIMPLE_YAML,
        question="Which cuisine?",
        answer="Italian.",
        available_tools=_TOOLS,
        available_skills=_SKILLS,
    )
    assert [m.role for m in msgs] == ["system", "user", "assistant", "user", "user"]
    assert "Which cuisine?" in msgs[2].content
    assert msgs[3].content == "Italian."
    assert EXAMPLE_SIMPLE_YAML in msgs[1].content
    # refinement re-injects the catalog so it still only suggests real tools
    assert "[AVAILABLE_TOOLS]" not in msgs[0].content


def _validate(yaml_text: str) -> Persona:
    raw = yaml.safe_load(yaml_text)
    assert isinstance(raw, dict)
    raw.setdefault("persona_id", "draft")
    raw.setdefault("owner_id", "draft")
    return Persona.model_validate(raw)


def test_few_shot_simple_example_validates_as_v1() -> None:
    p = _validate(EXAMPLE_SIMPLE_YAML)
    # the cross-model levers the prompt teaches: a safety constraint + epistemic diversity
    assert any("fabricate" in c.lower() for c in p.identity.constraints)
    assert any(w.epistemic != "fact" for w in p.worldview)


def test_few_shot_complex_example_validates_as_v1() -> None:
    p = _validate(EXAMPLE_COMPLEX_YAML)
    assert p.identity.language_default == "nb"
    assert any("legal advice" in c.lower() for c in p.identity.constraints)
    assert any(w.epistemic != "fact" for w in p.worldview)
    # only suggests tools/skills that exist in the catalog
    assert set(p.tools) <= {"web_search", "web_fetch", "file_read", "file_write"}
    assert set(p.skills) <= {"web_research", "document_drafting"}
