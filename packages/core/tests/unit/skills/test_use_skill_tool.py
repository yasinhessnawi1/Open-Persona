"""Tests for ``persona.skills.use_skill_tool.make_use_skill_tool`` (T07).

Covers spec §7.2 (Pattern 1) and §10 S04-2 via D-04-9 and D-04-10.

The factory closes over a list of skill names. The synthetic tool
validates ``skill_name`` against that closure, returning either:

- success: ``ToolResult(is_error=False, data={"skill_name": "X"})`` — the
  runtime hook for activation (spec 05 intercepts on ``data["skill_name"]``).
- error: ``ToolResult(is_error=True)`` with available skills listed in the
  content (mirrors D-03-8's idiom).

Integration smoke: the produced tool satisfies ``isinstance(tool,
AsyncTool)`` and dispatches through :class:`persona.tools.Toolbox` like any
other tool.

Also covers ``collect_skill_supplements`` (Spec 16 M1a, D-16-2 /
D-16-2-supplements-relative-path / D-16-X-7) — the producer must emit
**relative** ``SandboxFile.path`` values so the consumer side's
``host_in / f.path`` join lands at the bind-mount root, not at the
container-internal absolute path.
"""

# ruff: noqa: ANN401, ARG001, ARG002

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — used at runtime in fixtures

import pytest
from persona.schema.skills import SkillSpec
from persona.schema.tools import ToolCall
from persona.skills.use_skill_tool import collect_skill_supplements, make_use_skill_tool
from persona.tools.protocol import AsyncTool
from persona.tools.toolbox import Toolbox


def _spec(tmp_path: Path, name: str) -> SkillSpec:
    return SkillSpec(name=name, description=f"{name} desc", path=tmp_path)


def _spec_with_supplements(root: Path, name: str, topics: list[str]) -> SkillSpec:
    """Build a fake skill on disk with a ``supplements/`` directory.

    ``root`` is the parent ``tmp_path``; each topic in ``topics`` is created
    as ``<root>/<name>/supplements/<topic>.md`` with non-trivial bytes so
    ``content_bytes`` is observably non-empty.
    """
    skill_root = root / name
    supplements = skill_root / "supplements"
    supplements.mkdir(parents=True)
    for topic in topics:
        (supplements / f"{topic}.md").write_text(
            f"# {topic}\n\nDeeper guidance for {topic} under {name}.\n",
            encoding="utf-8",
        )
    return SkillSpec(name=name, description=f"{name} desc", path=skill_root)


class TestFactoryShape:
    def test_returns_async_tool(self, tmp_path: Path) -> None:
        skills = [_spec(tmp_path, "web_research")]
        t = make_use_skill_tool(skills)
        assert isinstance(t, AsyncTool)

    def test_tool_name_is_use_skill(self, tmp_path: Path) -> None:
        t = make_use_skill_tool([_spec(tmp_path, "x")])
        assert t.name == "use_skill"

    def test_tool_description_non_empty(self, tmp_path: Path) -> None:
        t = make_use_skill_tool([_spec(tmp_path, "x")])
        assert len(t.description) > 0
        # Description should mention skill_name so the model knows the
        # parameter shape from the system prompt alone.
        assert "skill_name" in t.description

    def test_parameters_schema_has_skill_name_required(
        self,
        tmp_path: Path,
    ) -> None:
        t = make_use_skill_tool([_spec(tmp_path, "x")])
        schema = t.parameters_schema
        assert schema["type"] == "object"
        assert "skill_name" in schema["properties"]
        assert schema["properties"]["skill_name"]["type"] == "string"
        assert "skill_name" in schema["required"]

    def test_parameters_schema_forbids_extras(self, tmp_path: Path) -> None:
        t = make_use_skill_tool([_spec(tmp_path, "x")])
        schema = t.parameters_schema
        # The @tool decorator's __config__ sets extra="forbid"; the
        # generated schema should reflect this.
        assert schema.get("additionalProperties") is False


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_known_skill_returns_data(self, tmp_path: Path) -> None:
        skills = [_spec(tmp_path, "web_research"), _spec(tmp_path, "document_drafting")]
        t = make_use_skill_tool(skills)
        result = await t.execute(skill_name="web_research")
        assert result.is_error is False
        assert result.data == {"skill_name": "web_research"}
        assert "Activating skill: web_research" in result.content

    @pytest.mark.asyncio
    async def test_second_known_skill_works(self, tmp_path: Path) -> None:
        skills = [_spec(tmp_path, "web_research"), _spec(tmp_path, "document_drafting")]
        t = make_use_skill_tool(skills)
        result = await t.execute(skill_name="document_drafting")
        assert result.is_error is False
        assert result.data == {"skill_name": "document_drafting"}


class TestUnknownSkill:
    @pytest.mark.asyncio
    async def test_unknown_skill_returns_is_error(self, tmp_path: Path) -> None:
        skills = [_spec(tmp_path, "x")]
        t = make_use_skill_tool(skills)
        result = await t.execute(skill_name="bogus")
        assert result.is_error is True
        assert result.data is None
        assert "Unknown skill: bogus" in result.content
        # Available list is included so the model can recover.
        assert "available: x" in result.content

    @pytest.mark.asyncio
    async def test_available_list_alphabetised(self, tmp_path: Path) -> None:
        # Mirrors D-03-8's idiom — sorted, comma-joined.
        skills = [_spec(tmp_path, "zebra"), _spec(tmp_path, "alpha"), _spec(tmp_path, "mike")]
        t = make_use_skill_tool(skills)
        result = await t.execute(skill_name="bogus")
        assert "alpha, mike, zebra" in result.content


class TestEmptySkillsList:
    """Construction with an empty skill list still produces a valid tool.

    Per D-04-10, the runtime won't normally register ``use_skill`` for a
    persona with no declared skills (it would advertise a capability that
    can never succeed). But the factory itself must produce a valid tool
    regardless — the runtime decides whether to register it, not the
    factory.
    """

    @pytest.mark.asyncio
    async def test_empty_skills_any_call_errors(self, tmp_path: Path) -> None:
        t = make_use_skill_tool([])
        result = await t.execute(skill_name="anything")
        assert result.is_error is True

    @pytest.mark.asyncio
    async def test_empty_skills_available_is_none_string(
        self,
        tmp_path: Path,
    ) -> None:
        t = make_use_skill_tool([])
        result = await t.execute(skill_name="anything")
        # When the available set is empty, the content shows "(none)" so
        # the model sees the situation explicitly rather than a trailing
        # ", " on an empty list.
        assert "(none)" in result.content


class TestArgumentValidation:
    """The @tool decorator (D-03-5) catches argument-validation errors
    and returns ToolResult(is_error=True). These tests confirm the
    behaviour composes through our closure."""

    @pytest.mark.asyncio
    async def test_missing_required_arg(self, tmp_path: Path) -> None:
        t = make_use_skill_tool([_spec(tmp_path, "x")])
        result = await t.execute()
        assert result.is_error is True
        assert "Invalid arguments" in result.content

    @pytest.mark.asyncio
    async def test_extra_arg_rejected(self, tmp_path: Path) -> None:
        t = make_use_skill_tool([_spec(tmp_path, "x")])
        result = await t.execute(skill_name="x", extra="y")
        assert result.is_error is True
        assert "Invalid arguments" in result.content


class TestClosureIsolation:
    """Two factories with different skill lists must have independent
    closures."""

    @pytest.mark.asyncio
    async def test_independent_closures(self, tmp_path: Path) -> None:
        t1 = make_use_skill_tool([_spec(tmp_path, "alpha")])
        t2 = make_use_skill_tool([_spec(tmp_path, "beta")])

        r1_alpha = await t1.execute(skill_name="alpha")
        r2_alpha = await t2.execute(skill_name="alpha")
        assert r1_alpha.is_error is False
        assert r2_alpha.is_error is True

        r1_beta = await t1.execute(skill_name="beta")
        r2_beta = await t2.execute(skill_name="beta")
        assert r1_beta.is_error is True
        assert r2_beta.is_error is False


class TestToolboxIntegration:
    """The synthetic tool dispatches through the spec-03 Toolbox normally."""

    @pytest.mark.asyncio
    async def test_dispatch_via_toolbox(self, tmp_path: Path) -> None:
        t = make_use_skill_tool([_spec(tmp_path, "web_research")])
        toolbox = Toolbox([t], allow_list=["use_skill"])
        call = ToolCall(name="use_skill", args={"skill_name": "web_research"})
        result = await toolbox.dispatch(call)
        assert result.is_error is False
        assert result.data == {"skill_name": "web_research"}

    @pytest.mark.asyncio
    async def test_dispatch_unknown_via_toolbox(self, tmp_path: Path) -> None:
        t = make_use_skill_tool([_spec(tmp_path, "web_research")])
        toolbox = Toolbox([t], allow_list=["use_skill"])
        call = ToolCall(name="use_skill", args={"skill_name": "bogus"})
        result = await toolbox.dispatch(call)
        assert result.is_error is True


# ---------------------------------------------------------------------------
# Spec 16 — collect_skill_supplements (D-16-2 / D-16-X-7 /
# D-16-2-supplements-relative-path regression guards)
# ---------------------------------------------------------------------------


class TestCollectSkillSupplementsRelativePath:
    """D-16-2-supplements-relative-path regression guards.

    Earlier revisions emitted absolute ``SandboxFile.path`` values of the
    form ``/workspace/in/.skills/<name>/supplements/<topic>.md``. Because
    ``Path('/host_in') / '/workspace/in/...'`` short-circuits to
    ``Path('/workspace/in/...')`` per Python's path-join semantics, the
    consumer ``LocalDockerSandbox._seed_workspace`` then attempted writes
    at ``/workspace/in/...`` on the **host** — failing with
    ``OSError: [Errno 30] Read-only file system: '/workspace'`` on macOS.
    These tests enforce the source-of-truth discipline: paths in transport
    are relative; the absolute form is only the mounted destination.
    """

    def test_returns_relative_paths(self, tmp_path: Path) -> None:
        """No emitted ``SandboxFile.path`` may start with ``/``; each must
        begin with ``.skills/`` (the workspace-root-relative prefix)."""
        spec = _spec_with_supplements(
            tmp_path,
            "docx_generation",
            ["tables", "styles"],
        )
        staged = collect_skill_supplements(spec)
        assert len(staged) == 2
        for entry in staged:
            assert not entry.path.startswith("/"), (
                f"SandboxFile.path {entry.path!r} is absolute; per "
                "D-16-2-supplements-relative-path the transport form is "
                "workspace-relative."
            )
            assert entry.path.startswith(".skills/"), (
                f"SandboxFile.path {entry.path!r} must begin with .skills/ "
                "(the workspace-relative supplements prefix)."
            )
            assert entry.content_bytes, "supplements payload must be non-empty"
            assert entry.media_type == "text/markdown"

    def test_relative_path_shape_per_topic(self, tmp_path: Path) -> None:
        """The relative path is ``.skills/<name>/supplements/<topic>.md`` —
        no leading slash, no ``/workspace/in/`` prefix."""
        spec = _spec_with_supplements(tmp_path, "xlsx_generation", ["formulas"])
        [entry] = collect_skill_supplements(spec)
        assert entry.path == ".skills/xlsx_generation/supplements/formulas.md"

    def test_round_trip_via_seed_workspace(self, tmp_path: Path) -> None:
        """Mimic ``LocalDockerSandbox._seed_workspace``'s ``host_in / f.path``
        join. With **relative** transport paths, the files must land under
        ``<host_in>/.skills/<name>/supplements/<topic>.md`` — NOT at
        ``/workspace/in/...`` on the host.

        This is the direct regression for the production bug D-16-X-7
        surfaced: pre-fix, the join short-circuited to an absolute
        ``/workspace/...`` path on the host, which raised
        ``OSError: [Errno 30] Read-only file system`` on macOS.
        """
        skills_root = tmp_path / "skills_src"
        spec = _spec_with_supplements(skills_root, "pdf_generation", ["flowables"])
        staged = collect_skill_supplements(spec)

        host_in = tmp_path / "host_in"
        host_in.mkdir()
        # Inline the consumer's join semantics verbatim from
        # ``LocalDockerSandbox._seed_workspace``.
        for f in staged:
            target = host_in / f.path
            assert str(target).startswith(str(host_in)), (
                f"join short-circuited: target {target!r} escaped host_in "
                f"{host_in!r} (the D-16-X-7 production bug)"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            assert f.content_bytes is not None
            target.write_bytes(f.content_bytes)

        expected = host_in / ".skills/pdf_generation/supplements/flowables.md"
        assert expected.is_file(), (
            f"expected supplement landed at {expected}; the host-side seed "
            "did not place the bytes correctly under host_in"
        )
        # And critically, NOT at the absolute /workspace/in/... path.
        forbidden = Path("/workspace/in/.skills/pdf_generation/supplements/flowables.md")
        assert not forbidden.exists(), (
            "supplement leaked to absolute /workspace/in/...; transport "
            "path must be relative (D-16-2-supplements-relative-path)"
        )

    def test_path_matches_skill_md_teaching(self, tmp_path: Path) -> None:
        """At the bind-mount-internal view, ``/workspace/in/`` + the
        relative ``SandboxFile.path`` equals the absolute path the SKILL.md
        packs teach the model (``/workspace/in/.skills/<name>/supplements/
        <topic>.md``).

        This is the D-16-2-path "path-taught == path-staged" invariant
        re-stated at the relative-transport layer: the SKILL.md packs
        teach the absolute sandbox-internal path; the transport carries
        the workspace-relative path; the two reconcile via the bind-mount
        prefix.
        """
        spec = _spec_with_supplements(
            tmp_path,
            "pptx_generation",
            ["layouts"],
        )
        [entry] = collect_skill_supplements(spec)
        # The SKILL.md teaches: /workspace/in/.skills/<name>/supplements/<topic>.md
        expected_skill_md_view = "/workspace/in/.skills/pptx_generation/supplements/layouts.md"
        # The bind-mount-internal view = "/workspace/in/" + relative transport.
        reconstructed = "/workspace/in/" + entry.path
        assert reconstructed == expected_skill_md_view, (
            f"reconstructed sandbox view {reconstructed!r} does not equal the "
            f"path the SKILL.md packs teach ({expected_skill_md_view!r}); "
            "D-16-2-path / D-16-2-supplements-relative-path invariant broken."
        )

    def test_no_supplements_dir_returns_empty(self, tmp_path: Path) -> None:
        """The lean case for skills that fit inline — no supplements/ dir
        ⇒ empty list, no errors."""
        spec = SkillSpec(name="lean_skill", description="lean", path=tmp_path)
        assert collect_skill_supplements(spec) == []
