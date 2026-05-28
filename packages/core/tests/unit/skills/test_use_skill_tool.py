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
"""

# ruff: noqa: ANN401, ARG001, ARG002

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — used at runtime in fixtures

import pytest
from persona.schema.skills import SkillSpec
from persona.schema.tools import ToolCall
from persona.skills.use_skill_tool import make_use_skill_tool
from persona.tools.protocol import AsyncTool
from persona.tools.toolbox import Toolbox


def _spec(tmp_path: Path, name: str) -> SkillSpec:
    return SkillSpec(name=name, description=f"{name} desc", path=tmp_path)


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
