"""The shipped example personas (``packages/core/examples/``) stay valid.

Spec 11 §3 ships three demo personas with ``persona-core``. This is the guard
(acceptance #1, "the personas load and validate"): every YAML in ``examples/``
loads as a v1.0 :class:`Persona`, declares a ``persona_id`` matching its
filename, carries at least one safety constraint (the canonical "don't
fabricate / say when you don't know" floor), and has at least one non-``fact``
worldview claim (epistemic diversity). A future schema change that breaks a
shipped demo fails here, not in front of a viewer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from persona.schema.persona import Persona

_EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
_EXAMPLE_FILES = sorted(_EXAMPLES_DIR.glob("*.yaml"))

# A constraint counts as a safety constraint if it commits to not fabricating or
# to admitting uncertainty — the canonical floor every persona must hold.
_SAFETY_MARKERS = ("fabricat", "uncertain", "unsure", "don't know", "do not know", "say so")


def test_examples_dir_has_the_three_demo_personas() -> None:
    stems = {p.stem for p in _EXAMPLE_FILES}
    assert {"astrid_tenancy_law", "kai_research", "maren_writing_coach"} <= stems
    assert len(_EXAMPLE_FILES) >= 3


@pytest.mark.parametrize("path", _EXAMPLE_FILES, ids=lambda p: p.stem)
def test_example_persona_loads_and_is_complete(path: Path) -> None:
    persona = Persona.from_yaml(path)

    # declared id matches the filename (deterministic, demo-stable)
    assert persona.persona_id == path.stem
    assert persona.schema_version == "1.0"
    assert persona.visibility == "public"

    # identity present and non-trivial
    assert persona.identity.name
    assert persona.identity.role
    assert persona.identity.background.strip()

    # full depth: the spec's authored shape (§3)
    assert len(persona.identity.constraints) >= 3, "expected >=3 constraints"
    assert len(persona.self_facts) >= 4, "expected >=4 self_facts"
    assert len(persona.worldview) >= 4, "expected >=4 worldview claims"

    # >=1 safety constraint (the canonical floor)
    constraints_lc = " ".join(persona.identity.constraints).lower()
    assert any(m in constraints_lc for m in _SAFETY_MARKERS), (
        "expected at least one safety constraint (don't-fabricate / admit-uncertainty)"
    )

    # epistemic diversity: >=1 non-`fact` worldview claim
    assert any(claim.epistemic != "fact" for claim in persona.worldview), (
        "expected at least one non-`fact` worldview claim"
    )


@pytest.mark.parametrize("path", _EXAMPLE_FILES, ids=lambda p: p.stem)
def test_example_persona_tools_back_declared_skills(path: Path) -> None:
    """Every declared skill's tool requirements are covered by the persona's
    tools — so the demo persona is internally consistent (a skill it can't run
    is dead weight). Mirrors the built-in skills' ``tools_required``."""
    persona = Persona.from_yaml(path)
    skill_tool_requirements = {
        "web_research": {"web_search", "web_fetch", "file_write"},
        "document_drafting": {"file_write"},
    }
    declared_tools = set(persona.tools)
    for skill in persona.skills:
        required = skill_tool_requirements.get(skill, set())
        missing = required - declared_tools
        assert not missing, f"{path.stem}: skill {skill!r} needs tools {missing} not declared"
