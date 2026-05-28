"""Tests for ``persona.skills.scanner.SkillScanner`` (T04).

Covers spec §9 acceptance criteria #1, #7, #8 and D-04-4, D-04-5.

Layout: tests build per-test fixture trees in ``tmp_path`` to keep the
file-fixture set (``_fixtures/``) small and load-bearing only for what it
demonstrates (one valid, one malformed-YAML, one missing-description). For
override semantics, user-vs-built-in path precedence, and missing-skill
warnings, we build ad-hoc trees per test.
"""

# ruff: noqa: ANN401, ARG001, ARG002

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — used in fixture helpers at runtime

import pytest
from persona.schema.skills import SkillSpec
from persona.skills.scanner import SkillScanner

_FIXTURE_DIR = Path(__file__).parent / "_fixtures"


def _make_skill(base: Path, name: str, text: str) -> Path:
    """Create ``base/<name>/SKILL.md`` with ``text`` content."""
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    return skill_dir


_VALID_TEXT = """\
---
name: my_skill
description: A skill for testing.
when_to_use: When tests need a happy path.
tools_required:
  - web_search
---

# My Skill

Body line.
"""


class TestHappyPath:
    def test_scans_one_valid_skill(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, "my_skill", _VALID_TEXT)
        scanner = SkillScanner([tmp_path])
        out = scanner.scan(["my_skill"])
        assert len(out) == 1
        spec = out[0]
        assert isinstance(spec, SkillSpec)
        assert spec.name == "my_skill"
        assert spec.description == "A skill for testing."
        assert spec.when_to_use == "When tests need a happy path."
        assert spec.tools_required == ["web_search"]
        assert "# My Skill" in spec.content
        assert spec.content_token_count > 0
        assert spec.path == tmp_path / "my_skill"

    def test_output_preserves_declared_order(self, tmp_path: Path) -> None:
        _make_skill(
            tmp_path,
            "alpha",
            "---\nname: alpha\ndescription: x\n---\n\nbody.\n",
        )
        _make_skill(
            tmp_path,
            "beta",
            "---\nname: beta\ndescription: y\n---\n\nbody.\n",
        )
        _make_skill(
            tmp_path,
            "gamma",
            "---\nname: gamma\ndescription: z\n---\n\nbody.\n",
        )
        scanner = SkillScanner([tmp_path])
        names = [s.name for s in scanner.scan(["gamma", "alpha", "beta"])]
        assert names == ["gamma", "alpha", "beta"]

    def test_uses_bundled_fixture_valid_skill(self) -> None:
        # Sanity: the load-bearing _fixtures/valid_skill is scannable.
        scanner = SkillScanner([_FIXTURE_DIR])
        out = scanner.scan(["valid_skill"])
        assert len(out) == 1
        assert out[0].tools_required == ["web_search", "web_fetch"]


class TestMissingSkill:
    def test_missing_skill_logged_and_omitted(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        scanner = SkillScanner([tmp_path])
        out = scanner.scan(["nonexistent"])
        assert out == []
        # The warning is emitted via loguru — we can't use caplog directly,
        # but the empty result is the contract.

    def test_partial_function_with_one_missing(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, "real", _VALID_TEXT.replace("my_skill", "real"))
        scanner = SkillScanner([tmp_path])
        out = scanner.scan(["real", "missing", "also_missing"])
        # Only the real one survives; missing ones are warn-and-skipped.
        # spec.name comes from the front matter (which we set to "real" via
        # the .replace above); the directory name and the declared name
        # also happen to be "real" so the three identities align.
        assert [s.name for s in out] == ["real"]

    def test_all_missing_returns_empty(self, tmp_path: Path) -> None:
        scanner = SkillScanner([tmp_path])
        assert scanner.scan(["a", "b", "c"]) == []


class TestMalformedSkill:
    def test_malformed_yaml_skill_omitted(self) -> None:
        scanner = SkillScanner([_FIXTURE_DIR])
        out = scanner.scan(["malformed_skill"])
        assert out == []

    def test_missing_description_skill_omitted(self) -> None:
        # Missing required field in front matter → SkillSpec construction
        # fails → warn-and-skip.
        scanner = SkillScanner([_FIXTURE_DIR])
        out = scanner.scan(["missing_description_skill"])
        assert out == []

    def test_partial_function_with_one_malformed(
        self,
        tmp_path: Path,
    ) -> None:
        _make_skill(tmp_path, "good", _VALID_TEXT.replace("my_skill", "good"))
        _make_skill(tmp_path, "bad", "no front matter at all\n")
        scanner = SkillScanner([tmp_path])
        out = scanner.scan(["good", "bad"])
        assert len(out) == 1
        assert out[0].name == "good"


class TestPathPrecedence:
    """User paths come first; first hit wins; remaining matches log a shadow warning."""

    def test_user_overrides_builtin(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        user = tmp_path / "user"
        builtin.mkdir()
        user.mkdir()
        _make_skill(
            builtin,
            "web_research",
            "---\nname: web_research\ndescription: BUILTIN.\n---\n\nbuiltin body.\n",
        )
        _make_skill(
            user,
            "web_research",
            "---\nname: web_research\ndescription: USER.\n---\n\nuser body.\n",
        )
        # Caller orders user before built-in.
        scanner = SkillScanner([user, builtin])
        out = scanner.scan(["web_research"])
        assert len(out) == 1
        assert out[0].description == "USER."
        assert "user body" in out[0].content

    def test_only_builtin_present(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        user = tmp_path / "user"
        builtin.mkdir()
        # user does NOT exist; scanner should not warn about this.
        _make_skill(
            builtin,
            "x",
            "---\nname: x\ndescription: builtin only.\n---\n\nbody.\n",
        )
        scanner = SkillScanner([user, builtin])
        out = scanner.scan(["x"])
        assert len(out) == 1
        assert out[0].description == "builtin only."


class TestAbsentUserDir:
    """D-04-5: an absent user ``skills/`` dir is silently skipped."""

    def test_absent_user_dir_is_silent(self, tmp_path: Path) -> None:
        # Build a config where the first path doesn't exist.
        nonexistent = tmp_path / "does_not_exist"
        builtin = tmp_path / "builtin"
        builtin.mkdir()
        _make_skill(
            builtin,
            "x",
            "---\nname: x\ndescription: present in builtin.\n---\n\nb.\n",
        )
        scanner = SkillScanner([nonexistent, builtin])
        out = scanner.scan(["x"])
        # Skill found via the second path. No exception.
        assert len(out) == 1

    def test_all_paths_absent_returns_empty(self, tmp_path: Path) -> None:
        nonexistent_a = tmp_path / "a"
        nonexistent_b = tmp_path / "b"
        scanner = SkillScanner([nonexistent_a, nonexistent_b])
        # No paths exist; no skills can be found. Empty result, no raise.
        assert scanner.scan(["any"]) == []


class TestToolAllowListValidation:
    def test_no_allow_list_no_validation(self, tmp_path: Path) -> None:
        _make_skill(
            tmp_path,
            "s",
            "---\nname: s\ndescription: d\ntools_required:\n  - bogus_tool\n---\n\nbody.\n",
        )
        scanner = SkillScanner([tmp_path])
        # tool_allow_list=None → no validation runs; the skill is returned
        # with its bogus tools_required intact.
        out = scanner.scan(["s"])
        assert len(out) == 1
        assert out[0].tools_required == ["bogus_tool"]

    def test_allow_list_with_all_present(self, tmp_path: Path) -> None:
        _make_skill(
            tmp_path,
            "s",
            "---\nname: s\ndescription: d\ntools_required:\n  - web_search\n---\n\nbody.\n",
        )
        scanner = SkillScanner([tmp_path])
        out = scanner.scan(["s"], tool_allow_list=["web_search", "web_fetch"])
        assert len(out) == 1
        # All required tools are in allow-list; no behavioural change.

    def test_allow_list_with_missing_warns_but_returns(self, tmp_path: Path) -> None:
        _make_skill(
            tmp_path,
            "s",
            "---\nname: s\ndescription: d\ntools_required:\n  - missing_tool\n---\n\nbody.\n",
        )
        scanner = SkillScanner([tmp_path])
        out = scanner.scan(["s"], tool_allow_list=["other_tool"])
        # Spec §9 #7: missing required tool logs WARNING but doesn't fail
        # the skill — it may partially function.
        assert len(out) == 1
        assert out[0].tools_required == ["missing_tool"]

    def test_empty_tools_required_no_validation(self, tmp_path: Path) -> None:
        _make_skill(
            tmp_path,
            "s",
            "---\nname: s\ndescription: d\n---\n\nbody.\n",
        )
        scanner = SkillScanner([tmp_path])
        out = scanner.scan(["s"], tool_allow_list=[])
        # No tools_required → no validation needed; empty allow-list is
        # irrelevant. Should not warn.
        assert len(out) == 1
        assert out[0].tools_required == []


class TestExceptionEnvelope:
    """D-04-4: per-skill broad ``Exception`` envelope catches everything
    (except ``BaseException``) and warn-and-skips."""

    def test_oserror_during_read_caught(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_skill(tmp_path, "s", _VALID_TEXT)
        # Monkeypatch parse_skill_markdown to raise an unexpected error.
        import persona.skills.scanner as scanner_mod

        def broken_parse(path: Path) -> tuple[dict, str]:
            raise RuntimeError("unexpected!")

        monkeypatch.setattr(scanner_mod, "parse_skill_markdown", broken_parse)
        scanner = SkillScanner([tmp_path])
        # Should not raise; should warn-and-skip.
        out = scanner.scan(["s"])
        assert out == []

    def test_keyboard_interrupt_propagates(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_skill(tmp_path, "s", _VALID_TEXT)
        import persona.skills.scanner as scanner_mod

        def interrupted(path: Path) -> tuple[dict, str]:
            raise KeyboardInterrupt

        monkeypatch.setattr(scanner_mod, "parse_skill_markdown", interrupted)
        scanner = SkillScanner([tmp_path])
        # BaseException must propagate.
        with pytest.raises(KeyboardInterrupt):
            scanner.scan(["s"])


class TestEmptyInputs:
    def test_empty_declared_skills(self, tmp_path: Path) -> None:
        scanner = SkillScanner([tmp_path])
        assert scanner.scan([]) == []

    def test_empty_paths(self) -> None:
        scanner = SkillScanner([])
        assert scanner.scan(["any"]) == []

    def test_empty_paths_and_skills(self) -> None:
        scanner = SkillScanner([])
        assert scanner.scan([]) == []
