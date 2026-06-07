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


@pytest.fixture
def data_analysis_spec(scanner: SkillScanner):  # noqa: ANN201
    [spec] = scanner.scan(["data_analysis"])
    return spec


class TestBuiltinDiscovery:
    """Spec §9 #8 — built-in skills are discoverable."""

    def test_all_builtins_scan(self, scanner: SkillScanner) -> None:
        out = scanner.scan(["web_research", "document_drafting", "data_analysis"])
        assert len(out) == 3
        names = [s.name for s in out]
        assert "web_research" in names
        assert "document_drafting" in names
        assert "data_analysis" in names

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

    def test_data_analysis_has_expected_tools_required(
        self,
        data_analysis_spec,  # noqa: ANN001
    ) -> None:
        # Spec 17 §6 + the SKILL.md frontmatter: code_execution is the
        # sole tool. The skill teaches sandbox-side analysis; no file_write
        # (charts land via produced_files into charts/, not via the file
        # write path).
        assert data_analysis_spec.tools_required == ["code_execution"]

    def test_all_have_when_to_use_populated(
        self,
        web_research_spec,  # noqa: ANN001
        document_drafting_spec,  # noqa: ANN001
        data_analysis_spec,  # noqa: ANN001
    ) -> None:
        assert web_research_spec.when_to_use
        assert document_drafting_spec.when_to_use
        assert data_analysis_spec.when_to_use


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

    def test_data_analysis_under_budget(
        self,
        data_analysis_spec,  # noqa: ANN001
    ) -> None:
        # Spec 17 R-17-2 target: 1,400–1,800 tokens. Under-budget like
        # document_drafting; ~300-token cushion to absorb polish-pass drift
        # without tripping the D-04-7 summariser/truncation backstop.
        assert data_analysis_spec.content_token_count < 2000, (
            f"data_analysis grew to {data_analysis_spec.content_token_count} tokens; "
            "must stay < 2000 so the verbatim pass-through path is exercised."
        )
        assert data_analysis_spec.content_token_count < 1800, (
            f"data_analysis at {data_analysis_spec.content_token_count} tokens "
            "drifted past R-17-2's 1,800-token target ceiling; "
            "trim back or supplement-extract before the cushion erodes."
        )

    def test_count_matches_recomputed(
        self,
        web_research_spec,  # noqa: ANN001
        document_drafting_spec,  # noqa: ANN001
        data_analysis_spec,  # noqa: ANN001
    ) -> None:
        # Scanner-computed count must match a fresh count of the content.
        assert web_research_spec.content_token_count == count_tokens(
            web_research_spec.content,
        )
        assert document_drafting_spec.content_token_count == count_tokens(
            document_drafting_spec.content,
        )
        assert data_analysis_spec.content_token_count == count_tokens(
            data_analysis_spec.content,
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


# ---------------------------------------------------------------------------
# Spec 16 T08 — Document Generation Skills Discovery
# ---------------------------------------------------------------------------

# The four `*_generation` skills added by Spec 16 Phase 5 (T03-T06).
# Per D-16-X-2, all four declare ``tools_required: [code_execution]`` —
# NOT ``file_write`` (the scanner's missing-tool warning is the safety
# net but the value is the contract). Per D-04-7, each SKILL.md body
# must fit under the 2,000-token hard ceiling. Per D-16-2 (M1a mode),
# each ships a ``supplements/`` directory with at least one ``.md`` file
# the runtime stages via the use_skill intercept. Per D-16-2-path, the
# SKILL.md body teaches the verbatim path
# ``/workspace/in/.skills/<name>/supplements/<topic>.md`` that the runtime
# stages bytes at — the "path the SKILL.md teaches IS the path Spec 12
# stages bytes at" regression guard.

_DOCUMENT_GENERATION_SKILLS = [
    "docx_generation",
    "pptx_generation",
    "xlsx_generation",
    "pdf_generation",
]


class TestDocumentGenerationSkillsDiscovery:
    """Spec 16 T08 — discovery + per-D-16-X invariant guards for the four
    ``*_generation`` skills.

    Mirrors :class:`TestBuiltinDiscovery` + :class:`TestTokenCountRegressionGuards`
    patterns: per-skill discovery; per-D-16-X-2 ``tools_required`` value;
    per-D-04-7 token-budget regression guard; per-spec ``when_to_use``
    non-empty; per-D-16-2 supplements directory present; per-D-16-2-path
    SKILL.md teaches the same path the runtime stages bytes at.
    """

    def test_all_four_document_skills_scanned(self, scanner: SkillScanner) -> None:
        """Spec §9 #8 — scanner discovers docx/pptx/xlsx/pdf_generation."""
        out = scanner.scan(_DOCUMENT_GENERATION_SKILLS)
        assert len(out) == 4
        names = [s.name for s in out]
        for expected in _DOCUMENT_GENERATION_SKILLS:
            assert expected in names, f"{expected} missing from scanner output"

    @pytest.mark.parametrize("skill_name", _DOCUMENT_GENERATION_SKILLS)
    def test_each_declares_code_execution_tool_required(
        self,
        scanner: SkillScanner,
        skill_name: str,
    ) -> None:
        """D-16-X-2 — every ``*_generation`` skill declares
        ``tools_required: [code_execution]`` (NOT ``file_write``)."""
        [spec] = scanner.scan([skill_name])
        assert spec.tools_required == ["code_execution"], (
            f"{skill_name} declared tools_required={spec.tools_required}; "
            "expected [code_execution] per D-16-X-2"
        )

    @pytest.mark.parametrize("skill_name", _DOCUMENT_GENERATION_SKILLS)
    def test_each_under_token_budget(
        self,
        scanner: SkillScanner,
        skill_name: str,
    ) -> None:
        """D-04-7 hard-ceiling regression guard — SKILL.md body ≤ 2000 tokens.

        T03-T06 landed bodies at 1657 / 1738 / 1822 / 1860 tokens
        respectively. This guard fails loud if a future polish pass drifts
        any body over the budget — the M1a supplements mechanism is the
        only legitimate way to add detail without breaching it.
        """
        [spec] = scanner.scan([skill_name])
        n = count_tokens(spec.content)
        assert n <= 2000, (
            f"{skill_name} body grew to {n} tokens; D-04-7 hard ceiling is 2000. "
            "Move detail into supplements/ instead of growing the body."
        )

    @pytest.mark.parametrize("skill_name", _DOCUMENT_GENERATION_SKILLS)
    def test_each_has_when_to_use_populated(
        self,
        scanner: SkillScanner,
        skill_name: str,
    ) -> None:
        """Spec §S04 — front-matter ``when_to_use`` is the discriminator
        the scanner / injector use; must be non-empty."""
        [spec] = scanner.scan([skill_name])
        assert spec.when_to_use, f"{skill_name} when_to_use is empty"
        assert spec.when_to_use.strip(), f"{skill_name} when_to_use is whitespace-only"

    @pytest.mark.parametrize("skill_name", _DOCUMENT_GENERATION_SKILLS)
    def test_each_has_supplements_directory(
        self,
        scanner: SkillScanner,
        skill_name: str,
    ) -> None:
        """D-16-2 (M1a mode) — each skill ships a ``supplements/`` directory
        with at least one ``.md`` file. The runtime's
        :func:`collect_skill_supplements` stages them on the next
        ``code_execution`` dispatch (per D-16-2-wiring)."""
        [spec] = scanner.scan([skill_name])
        supplements_dir = spec.path / "supplements"
        assert supplements_dir.is_dir(), (
            f"{skill_name} has no supplements/ directory; expected per "
            "D-16-2 M1a mode (LOCKED at M1a wiring close-gate)"
        )
        md_files = list(supplements_dir.glob("*.md"))
        assert len(md_files) >= 1, (
            f"{skill_name}/supplements/ contains no .md files; "
            "expected at least one (D-16-2 M1a mode)"
        )

    @pytest.mark.parametrize("skill_name", _DOCUMENT_GENERATION_SKILLS)
    def test_path_taught_equals_path_staged(
        self,
        scanner: SkillScanner,
        skill_name: str,
    ) -> None:
        """D-16-2-path regression guard — the SKILL.md body teaches the
        **model-facing** absolute path inside the sandbox.

        Per D-16-2-path the supplements appear, from the model's
        perspective inside the container, at
        ``/workspace/in/.skills/<name>/supplements/<topic>.md``. The
        SKILL.md body must reference this prefix at least once (the model
        won't know to read the supplement otherwise — and a future rename
        of the staging path would silently break every body's pointers
        without this guard).

        Per D-16-2-supplements-relative-path (D-16-X-7 production-bug
        refinement), the **transport** form ``SandboxFile.path`` is the
        relative ``.skills/<name>/supplements/<topic>.md`` — the
        ``/workspace/in/`` prefix is the local-docker bind-mount root and
        the ``/home/user/`` prefix is the hosted equivalent. The transport
        path regression lives in
        ``packages/core/tests/unit/skills/test_use_skill_tool.py::
        TestCollectSkillSupplementsRelativePath``; this integration test
        guards the SKILL.md side of the two-layer contract.
        """
        [spec] = scanner.scan([skill_name])
        prefix = f"/workspace/in/.skills/{skill_name}/supplements/"
        assert prefix in spec.content, (
            f"{skill_name}/SKILL.md does NOT reference the literal supplements "
            f"path {prefix!r}. Per D-16-2-path, the body must teach the same path "
            "the runtime stages bytes at; the model otherwise cannot reach the "
            "supplement."
        )

    @pytest.mark.parametrize("skill_name", _DOCUMENT_GENERATION_SKILLS)
    def test_each_skill_md_packaged_and_readable(
        self,
        scanner: SkillScanner,
        skill_name: str,
    ) -> None:
        """T08 package-data acceptance — verifies the SKILL.md file is
        readable from ``SkillSpec.path`` (proxies the wheel-include guard;
        package-data wiring keeps the bundled .md files reachable after
        install)."""
        [spec] = scanner.scan([skill_name])
        skill_md = spec.path / "SKILL.md"
        assert skill_md.is_file(), f"{skill_name}/SKILL.md not on disk at {skill_md}"
        # Non-trivial content; a 0-byte file would pass `is_file()` but
        # break every downstream consumer.
        assert skill_md.stat().st_size > 200, (
            f"{skill_name}/SKILL.md is suspiciously small ({skill_md.stat().st_size} bytes)"
        )


class TestM1aDeferredInputFilesContract:
    """Spec 16 M1a-wiring composition-root follow-up — the loop's public
    ``deferred_input_files`` attribute is the contract surface the
    composition root drains from. Cross-layer guard that the runtime
    exposes the attribute the api wires its provider against."""

    def test_conversation_loop_exposes_deferred_input_files(self) -> None:
        """The ``ConversationLoop`` class has a public ``deferred_input_files``
        attribute (instance state per D-16-2-state-location option (a)).

        Inspected at the class level so the test doesn't need to construct
        a full loop. ``__init__`` sets the attribute; the inspection here
        is a structural guard.
        """
        import inspect

        from persona_runtime.loop import ConversationLoop

        src = inspect.getsource(ConversationLoop)
        assert "self.deferred_input_files" in src, (
            "ConversationLoop is missing the public deferred_input_files "
            "attribute; the api composition root needs it to wire the "
            "M1a deferred_input_files_provider callable per D-16-2."
        )

    def test_agentic_loop_exposes_deferred_input_files(self) -> None:
        """Mirror guard for the ``AgenticLoop``."""
        import inspect

        from persona_runtime.agentic.loop import AgenticLoop

        src = inspect.getsource(AgenticLoop)
        assert "self.deferred_input_files" in src, (
            "AgenticLoop is missing the public deferred_input_files "
            "attribute; the api composition root needs it to wire the "
            "M1a deferred_input_files_provider callable per D-16-2."
        )

    def test_make_code_execution_tool_accepts_provider(self) -> None:
        """The Spec 12 tool factory accepts the optional
        ``deferred_input_files_provider`` parameter the api drains into."""
        import inspect

        from persona.sandbox.tool import make_code_execution_tool

        sig = inspect.signature(make_code_execution_tool)
        assert "deferred_input_files_provider" in sig.parameters, (
            "make_code_execution_tool is missing "
            "deferred_input_files_provider; M1a wiring requires this "
            "parameter per D-16-2-state-location."
        )
