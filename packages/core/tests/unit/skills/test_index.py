"""Tests for ``persona.skills.index.render_skill_index`` (T05, D-04-6).

Pure function — same input always yields same output; no clock, no I/O.
Tests focus on:

- Empty list → empty string (NOT a header alone).
- Single skill with ``when_to_use`` → two lines per skill.
- Single skill without ``when_to_use`` → one line per skill.
- Multiple skills — order preserved.
- Deterministic across two calls with the same input.
- No trailing whitespace; no double-blank lines.
- Special characters in description / when_to_use are passed through
  verbatim (no escaping — the prompt builder consumes the string directly).
"""

# ruff: noqa: ANN401, ARG001, ARG002

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — used at runtime in fixtures

import pytest
from persona.schema.skills import SkillSpec
from persona.skills.index import render_skill_index


def _make_spec(
    tmp_path: Path,
    name: str,
    description: str,
    when_to_use: str | None = None,
) -> SkillSpec:
    return SkillSpec(
        name=name,
        description=description,
        path=tmp_path,
        when_to_use=when_to_use,
    )


class TestEmptyInput:
    def test_empty_list_returns_empty_string(self) -> None:
        assert render_skill_index([]) == ""

    def test_empty_string_is_falsy(self) -> None:
        # The prompt builder uses ``if rendered:`` to decide whether to
        # inject the block. Empty must be falsy.
        assert not render_skill_index([])


class TestSingleSkill:
    def test_skill_with_when_to_use_two_lines(self, tmp_path: Path) -> None:
        spec = _make_spec(
            tmp_path,
            "web_research",
            "Research a topic.",
            "When the user asks to research.",
        )
        out = render_skill_index([spec])
        expected = (
            "Available skills (use when appropriate):\n"
            "\n"
            "- **web_research**: Research a topic.\n"
            "  Use when: When the user asks to research."
        )
        assert out == expected

    def test_skill_without_when_to_use_one_line(self, tmp_path: Path) -> None:
        spec = _make_spec(tmp_path, "x", "y")  # when_to_use=None by default
        out = render_skill_index([spec])
        expected = "Available skills (use when appropriate):\n\n- **x**: y"
        assert out == expected
        assert "Use when:" not in out

    def test_empty_when_to_use_string_treated_as_absent(self, tmp_path: Path) -> None:
        # Pydantic accepts empty string for `str | None` (it's a string).
        # The renderer treats falsy as "absent".
        spec = SkillSpec(
            name="x",
            description="y",
            path=tmp_path,
            when_to_use="",
        )
        out = render_skill_index([spec])
        assert "Use when:" not in out


class TestMultipleSkills:
    def test_preserves_input_order(self, tmp_path: Path) -> None:
        specs = [
            _make_spec(tmp_path, "alpha", "first", "first when"),
            _make_spec(tmp_path, "beta", "second", "second when"),
            _make_spec(tmp_path, "gamma", "third", "third when"),
        ]
        out = render_skill_index(specs)
        # Order of bullets follows input.
        idx_alpha = out.index("alpha")
        idx_beta = out.index("beta")
        idx_gamma = out.index("gamma")
        assert idx_alpha < idx_beta < idx_gamma

    def test_mixed_with_and_without_when_to_use(self, tmp_path: Path) -> None:
        specs = [
            _make_spec(tmp_path, "with", "has hint", "use when X"),
            _make_spec(tmp_path, "without", "no hint"),
        ]
        out = render_skill_index(specs)
        # 'with' renders two lines; 'without' renders one.
        assert "**with**: has hint" in out
        assert "  Use when: use when X" in out
        assert "**without**: no hint" in out
        # The 'without' skill should not have a Use-when line.
        without_idx = out.index("**without**")
        # Everything after the 'without' bullet must NOT contain "Use when:".
        assert "Use when:" not in out[without_idx:]


class TestDeterminism:
    def test_same_input_same_output(self, tmp_path: Path) -> None:
        specs = [
            _make_spec(tmp_path, "a", "d", "w"),
            _make_spec(tmp_path, "b", "d2"),
        ]
        out1 = render_skill_index(specs)
        out2 = render_skill_index(specs)
        assert out1 == out2

    def test_no_side_effects_on_input(self, tmp_path: Path) -> None:
        spec = _make_spec(tmp_path, "x", "y", "z")
        before = (spec.name, spec.description, spec.when_to_use)
        render_skill_index([spec])
        after = (spec.name, spec.description, spec.when_to_use)
        assert before == after  # frozen anyway, but verify renderer doesn't try


class TestFormattingHygiene:
    def test_no_trailing_whitespace_on_any_line(self, tmp_path: Path) -> None:
        specs = [
            _make_spec(tmp_path, "a", "d", "w"),
            _make_spec(tmp_path, "b", "d2"),
        ]
        out = render_skill_index(specs)
        for line in out.split("\n"):
            assert line == line.rstrip(), f"trailing whitespace in: {line!r}"

    def test_no_double_blank_lines(self, tmp_path: Path) -> None:
        specs = [
            _make_spec(tmp_path, "a", "d", "w"),
            _make_spec(tmp_path, "b", "d2", "w2"),
            _make_spec(tmp_path, "c", "d3"),
        ]
        out = render_skill_index(specs)
        assert "\n\n\n" not in out

    def test_output_does_not_end_with_newline(self, tmp_path: Path) -> None:
        # The prompt builder controls newlines between blocks; the
        # renderer returns a clean joined-by-\n string with no trailing \n.
        spec = _make_spec(tmp_path, "x", "y")
        out = render_skill_index([spec])
        assert not out.endswith("\n")

    def test_header_present_exactly_once(self, tmp_path: Path) -> None:
        specs = [
            _make_spec(tmp_path, "a", "d"),
            _make_spec(tmp_path, "b", "d2"),
        ]
        out = render_skill_index(specs)
        assert out.count("Available skills") == 1


class TestSpecialCharacters:
    def test_passes_through_unicode(self, tmp_path: Path) -> None:
        spec = _make_spec(
            tmp_path,
            "norsk_lov",
            "Norsk husleielov — research med ÆØÅ.",
            "Når brukeren spør om norsk lov.",
        )
        out = render_skill_index([spec])
        assert "ÆØÅ" in out
        assert "Når" in out

    def test_passes_through_markdown_in_description(self, tmp_path: Path) -> None:
        # No escaping — the description is whatever the SKILL.md front
        # matter says it is. If a skill description contains stars or
        # backticks, they pass through.
        spec = _make_spec(tmp_path, "x", "uses `code` and *emphasis*")
        out = render_skill_index([spec])
        assert "`code`" in out
        assert "*emphasis*" in out


@pytest.mark.parametrize(
    ("when_to_use", "expects_use_when_line"),
    [
        (None, False),
        ("", False),
        ("non-empty", True),
        ("multi-word phrase", True),
    ],
)
def test_when_to_use_truthiness_controls_second_line(
    tmp_path: Path,
    when_to_use: str | None,
    expects_use_when_line: bool,
) -> None:
    spec = SkillSpec(
        name="x",
        description="y",
        path=tmp_path,
        when_to_use=when_to_use,
    )
    out = render_skill_index([spec])
    assert ("Use when:" in out) == expects_use_when_line
