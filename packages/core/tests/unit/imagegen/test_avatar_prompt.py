"""Unit tests for the avatar-prompt crafter (Spec 29 T1, D-29-1).

These tests are the T1 safety checkpoint: they assert the load-bearing
D-29-1 properties on an adversarial persona corpus — the crafted prompt is
deterministic (byte-identical), never infers demographics from the
``name``, never parses ``background`` prose into the prompt, reflects
appearance ONLY when declared in ``visual_style``, and passes the Spec 15
hard-line categorical filter cleanly (including the teacher/"children"
false-positive class, D-29-6).
"""

from __future__ import annotations

import pytest
from persona.imagegen.avatar_prompt import craft_avatar_prompt
from persona.imagegen.safety import is_hard_line_violation
from persona.schema import PersonaIdentity


def _identity(
    *,
    name: str = "Astrid",
    role: str = "software engineer",
    background: str = "An experienced professional.",
    visual_style: str | None = None,
) -> PersonaIdentity:
    """Build a minimal valid PersonaIdentity for crafting tests."""
    return PersonaIdentity(
        name=name,
        role=role,
        background=background,
        visual_style=visual_style,
    )


# ---------------------------------------------------------------------------
# Determinism (no seed / no randomness / no timestamp).
# ---------------------------------------------------------------------------


def test_craft_avatar_prompt_is_byte_identical_across_calls() -> None:
    identity = _identity(role="Norwegian tenancy law assistant", visual_style="watercolour")
    assert craft_avatar_prompt(identity) == craft_avatar_prompt(identity)


def test_equal_identities_yield_equal_prompts() -> None:
    a = _identity(role="data scientist", visual_style="cinematic")
    b = _identity(role="data scientist", visual_style="cinematic")
    assert craft_avatar_prompt(a) == craft_avatar_prompt(b)


# ---------------------------------------------------------------------------
# D-29-1: name is OMITTED — no name → demographic inference.
# ---------------------------------------------------------------------------


def test_name_is_omitted_from_prompt() -> None:
    prompt = craft_avatar_prompt(_identity(name="David", role="surgeon"))
    assert "David" not in prompt
    assert "david" not in prompt.lower()


@pytest.mark.parametrize(
    "name",
    ["David", "Maria", "Mohammed", "Li Wei", "Aaliyah", "Sven"],
)
def test_name_does_not_change_the_prompt(name: str) -> None:
    """Demographically-suggestive names must not steer the output at all."""
    baseline = craft_avatar_prompt(_identity(name="Astrid", role="surgeon"))
    assert craft_avatar_prompt(_identity(name=name, role="surgeon")) == baseline


def test_no_demographic_specifiers_when_undeclared() -> None:
    """No gender/age/diversity-injection tokens when nothing is declared."""
    prompt = craft_avatar_prompt(_identity(role="software engineer")).lower()
    for token in (
        "man",
        "woman",
        "male",
        "female",
        "young",
        "old",
        "year-old",
        "diverse",
        "ethnicity",
        "caucasian",
        "asian",
    ):
        assert token not in prompt, f"undeclared demographic token leaked: {token!r}"


# ---------------------------------------------------------------------------
# D-29-1: background is NOT parsed — prose never leaks into the prompt.
# ---------------------------------------------------------------------------


def test_background_prose_is_not_parsed_into_prompt() -> None:
    """A background stating age/gender must not surface (declared-in-visual_style only)."""
    identity = _identity(
        role="consultant",
        background="Maria is a 35-year-old woman from Oslo who mentors children.",
    )
    prompt = craft_avatar_prompt(identity).lower()
    for leaked in ("35", "year-old", "woman", "oslo", "children", "maria"):
        assert leaked not in prompt, f"background prose leaked: {leaked!r}"


# ---------------------------------------------------------------------------
# D-29-1: visual_style is THE declared-appearance channel.
# ---------------------------------------------------------------------------


def test_declared_visual_style_is_reflected() -> None:
    identity = _identity(role="novelist", visual_style="a woman in her 40s, warm photographic")
    prompt = craft_avatar_prompt(identity)
    assert "a woman in her 40s, warm photographic" in prompt


def test_absent_visual_style_yields_role_anchored_base() -> None:
    prompt = craft_avatar_prompt(_identity(role="architect", visual_style=None))
    assert "architect" in prompt
    assert "in the style of" not in prompt


# ---------------------------------------------------------------------------
# Role anchor + degenerate-input guard.
# ---------------------------------------------------------------------------


def test_role_is_the_professional_anchor() -> None:
    prompt = craft_avatar_prompt(_identity(role="marine biologist"))
    assert "marine biologist" in prompt
    assert prompt.startswith("a professional headshot portrait representing the role of")


def test_whitespace_only_role_falls_back_conservatively() -> None:
    prompt = craft_avatar_prompt(_identity(role="   "))
    assert "representing the role of a professional" in prompt


# ---------------------------------------------------------------------------
# D-29-1 / D-29-6: hard-line-clean on the adversarial persona corpus.
#
# Personas whose role/background mention benign-but-sensitive terms must
# yield a crafted prompt that does NOT trip the categorical filter, because
# (a) the crafter emits no _SEX_SET vocabulary and (b) background is never
# parsed, so the minor ∩ sex co-occurrence cannot form.
# ---------------------------------------------------------------------------


_ADVERSARIAL_CORPUS: list[tuple[str, str, str | None]] = [
    # Each tuple is role, then background, then visual_style.
    ("primary school teacher", "Works with young children and kids every day.", None),
    ("pediatric nurse", "Cares for infants and toddlers in the NICU.", None),
    ("childcare specialist", "Supervises boys and girls at an after-school club.", None),
    ("sexual health educator", "Teaches teenagers about sexual health and consent.", None),
    ("figure drawing instructor", "Runs nude life-drawing classes for adults.", None),
    ("kindergarten teacher", "Spends the day with preschoolers and babies.", "watercolour"),
    ("youth football coach", "Trains a team of under-12 boys and girls.", None),
]


@pytest.mark.parametrize(("role", "background", "visual_style"), _ADVERSARIAL_CORPUS)
def test_crafted_prompt_passes_hard_line_filter(
    role: str, background: str, visual_style: str | None
) -> None:
    prompt = craft_avatar_prompt(
        _identity(role=role, background=background, visual_style=visual_style)
    )
    triggered, category = is_hard_line_violation(prompt)
    assert triggered is False, (
        f"hard-line false-positive ({category}) for role={role!r}: {prompt!r}"
    )
    assert category is None


@pytest.mark.parametrize(("role", "background", "visual_style"), _ADVERSARIAL_CORPUS)
def test_adversarial_background_terms_do_not_leak(
    role: str, background: str, visual_style: str | None
) -> None:
    """The clean-filter result holds BECAUSE the sensitive background prose never enters."""
    prompt = craft_avatar_prompt(
        _identity(role=role, background=background, visual_style=visual_style)
    ).lower()
    for leaked in (
        "children",
        "kids",
        "infants",
        "toddlers",
        "boys",
        "girls",
        "teenagers",
        "babies",
    ):
        assert leaked not in prompt, f"background term leaked into prompt: {leaked!r}"
