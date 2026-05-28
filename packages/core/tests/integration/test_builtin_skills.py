"""Integration test for the bundled built-in skill packs (T08).

Round-trips both ``web_research`` and ``document_drafting`` through the
real scanner → injector pipeline. Uses bundled SKILL.md files from
``packages/core/src/persona/skills/builtin/``.

Verifies spec §9 #8 (built-in skills are discoverable by the scanner)
and §9 #10 (test coverage of injector branches end-to-end with real
files, not synthetic fixtures).

Regression guard on token counts: ``web_research`` MUST stay > 2000
tokens and ``document_drafting`` MUST stay < 2000 tokens. If a future
polish pass drops ``web_research`` below 2000, end-to-end over-budget
coverage is silently lost — this test fails loud instead.
"""

# ruff: noqa: ANN401, ARG001, ARG002

from __future__ import annotations

from pathlib import Path

import pytest
from persona.skills import (
    SkillInjector,
    SkillScanner,
    count_tokens,
    make_use_skill_tool,
    render_skill_index,
)
from persona.skills.injector import MARKER

pytestmark = pytest.mark.integration

# The bundled built-in skills live alongside the package source.
BUILTIN_ROOT = (
    Path(__file__).parent.parent.parent / "src" / "persona" / "skills" / "builtin"
).resolve()


@pytest.fixture
def scanner() -> SkillScanner:
    return SkillScanner([BUILTIN_ROOT])


@pytest.fixture
def web_research_spec(scanner: SkillScanner):  # noqa: ANN201
    [spec] = scanner.scan(["web_research"])
    return spec


@pytest.fixture
def document_drafting_spec(scanner: SkillScanner):  # noqa: ANN201
    [spec] = scanner.scan(["document_drafting"])
    return spec


class TestBuiltinDiscovery:
    """Spec §9 #8 — built-in skills are discoverable."""

    def test_both_builtins_scan(self, scanner: SkillScanner) -> None:
        out = scanner.scan(["web_research", "document_drafting"])
        assert len(out) == 2
        names = [s.name for s in out]
        assert "web_research" in names
        assert "document_drafting" in names

    def test_scan_preserves_declared_order(self, scanner: SkillScanner) -> None:
        out = scanner.scan(["document_drafting", "web_research"])
        assert [s.name for s in out] == ["document_drafting", "web_research"]

    def test_web_research_has_expected_tools_required(
        self,
        web_research_spec,  # noqa: ANN001
    ) -> None:
        # The spec lists web_search, web_fetch, file_write as required.
        assert "web_search" in web_research_spec.tools_required
        assert "web_fetch" in web_research_spec.tools_required
        assert "file_write" in web_research_spec.tools_required

    def test_document_drafting_has_expected_tools_required(
        self,
        document_drafting_spec,  # noqa: ANN001
    ) -> None:
        assert "file_write" in document_drafting_spec.tools_required

    def test_both_have_when_to_use_populated(
        self,
        web_research_spec,  # noqa: ANN001
        document_drafting_spec,  # noqa: ANN001
    ) -> None:
        assert web_research_spec.when_to_use
        assert document_drafting_spec.when_to_use


class TestTokenCountRegressionGuards:
    """Pin the size relationship to the budget. If these fail, the
    builtin SKILL.md prose drifted and the injector test coverage is
    no longer end-to-end."""

    def test_web_research_over_budget(self, web_research_spec) -> None:  # noqa: ANN001
        # The over-budget path is exercised by this file's content.
        # Polish passes that drop it below 2000 silently break injector
        # coverage — fail loud here instead.
        assert web_research_spec.content_token_count > 2000, (
            f"web_research dropped to {web_research_spec.content_token_count} tokens; "
            "must stay > 2000 so the over-budget injector branches are exercised "
            "end-to-end."
        )

    def test_document_drafting_under_budget(
        self,
        document_drafting_spec,  # noqa: ANN001
    ) -> None:
        assert document_drafting_spec.content_token_count < 2000, (
            f"document_drafting grew to {document_drafting_spec.content_token_count} tokens; "
            "must stay < 2000 so the verbatim pass-through path is exercised."
        )

    def test_count_matches_recomputed(
        self,
        web_research_spec,  # noqa: ANN001
        document_drafting_spec,  # noqa: ANN001
    ) -> None:
        # Scanner-computed count must match a fresh count of the content.
        assert web_research_spec.content_token_count == count_tokens(
            web_research_spec.content,
        )
        assert document_drafting_spec.content_token_count == count_tokens(
            document_drafting_spec.content,
        )


class TestInjectorEndToEnd:
    """Spec §9 #10 — both injector branches exercised against real files."""

    @pytest.mark.asyncio
    async def test_document_drafting_verbatim_pass_through(
        self,
        document_drafting_spec,  # noqa: ANN001
    ) -> None:
        injector = SkillInjector()
        out = await injector.inject(document_drafting_spec)
        # Under budget → verbatim.
        assert out == document_drafting_spec.content
        assert MARKER not in out

    @pytest.mark.asyncio
    async def test_web_research_truncated_without_summariser(
        self,
        web_research_spec,  # noqa: ANN001
    ) -> None:
        injector = SkillInjector()
        out = await injector.inject(web_research_spec)
        # Over budget + no summariser → truncated.
        assert out.endswith(MARKER)
        assert count_tokens(out) <= SkillInjector.TOKEN_BUDGET
        # The result is a prefix of the original.
        prefix = out[: -len(MARKER)]
        assert web_research_spec.content.startswith(prefix)

    @pytest.mark.asyncio
    async def test_web_research_summarised_with_summariser(
        self,
        web_research_spec,  # noqa: ANN001
    ) -> None:
        captured: list[str] = []

        async def fake_summariser(content: str) -> str:
            captured.append(content)
            return "Brief summary of the web_research skill body."

        injector = SkillInjector(summariser=fake_summariser)
        out = await injector.inject(web_research_spec)
        assert out == "Brief summary of the web_research skill body."
        assert len(captured) == 1
        assert captured[0] == web_research_spec.content


class TestIndexRendering:
    """Spec §9 #2 — the index is rendered correctly from the scanned specs."""

    def test_index_contains_both_skill_names_and_descriptions(
        self,
        scanner: SkillScanner,
    ) -> None:
        specs = scanner.scan(["web_research", "document_drafting"])
        out = render_skill_index(specs)
        assert "**web_research**" in out
        assert "**document_drafting**" in out
        assert "Research a topic" in out
        assert "Draft a structured document" in out

    def test_index_contains_use_when_lines(self, scanner: SkillScanner) -> None:
        specs = scanner.scan(["web_research", "document_drafting"])
        out = render_skill_index(specs)
        # Both have when_to_use populated, so each gets a Use when: line.
        use_when_count = out.count("Use when:")
        assert use_when_count == 2

    def test_index_is_compact(self, scanner: SkillScanner) -> None:
        # The whole rendered index should be very small — spec §6 says
        # "typically 200-400 tokens for 2-5 skills." Pin it loosely.
        specs = scanner.scan(["web_research", "document_drafting"])
        out = render_skill_index(specs)
        n = count_tokens(out)
        assert n < 500, f"index grew to {n} tokens; expected < 500"


class TestUseSkillToolIntegration:
    """Spec §9 #3 (activation path) — use_skill exposes both built-ins."""

    @pytest.mark.asyncio
    async def test_factory_works_with_real_specs(
        self,
        scanner: SkillScanner,
    ) -> None:
        specs = scanner.scan(["web_research", "document_drafting"])
        t = make_use_skill_tool(specs)
        r = await t.execute(skill_name="web_research")
        assert r.is_error is False
        assert r.data == {"skill_name": "web_research"}

    @pytest.mark.asyncio
    async def test_factory_rejects_unknown_when_real_specs_loaded(
        self,
        scanner: SkillScanner,
    ) -> None:
        specs = scanner.scan(["web_research", "document_drafting"])
        t = make_use_skill_tool(specs)
        r = await t.execute(skill_name="not_a_skill")
        assert r.is_error is True
        # The available list should include both built-ins, sorted.
        assert "document_drafting" in r.content
        assert "web_research" in r.content


class TestPublicSurface:
    """`persona.skills` is importable as the documented public surface."""

    def test_all_public_names_importable(self) -> None:
        from persona.skills import (  # noqa: F401
            SkillInjector,
            SkillManifestError,
            SkillScanner,
            SkillSpec,
            count_tokens,
            make_use_skill_tool,
            render_skill_index,
        )

    def test_underscore_prefixed_not_re_exported(self) -> None:
        import persona.skills as skills_pkg

        # _tokens and _frontmatter and the modules backing the layer are
        # importable directly, but should NOT appear in __all__.
        assert "_tokens" not in skills_pkg.__all__
        assert "_frontmatter" not in skills_pkg.__all__
