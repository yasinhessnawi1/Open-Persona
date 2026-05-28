"""Synthetic ``use_skill`` AsyncTool factory (T07, D-04-9, D-04-10).

The ``use_skill`` tool is the **Pattern 1** activation channel for skills
(D-04-9). When the model calls it with ``skill_name="X"``, the tool returns
``ToolResult(is_error=False, content="Activating skill: X",
data={"skill_name": "X"})``. Spec 05's runtime intercepts on
``result.data["skill_name"]``, calls
:meth:`persona.skills.injector.SkillInjector.inject`, and re-prompts.

Pattern 2 (string-match on skill names in planning text) is **deferred
entirely** per D-04-9. The synthetic tool is the only activation channel
in v0.1.

For non-native-tool backends (Ollama default + HF local per spec 02), the
prompt-shim wire format ``{"tool": "use_skill", "args": {"skill_name":
"..."}}`` (D-02-6) IS the activation channel — the shim's parser produces
a ``ToolCall(name="use_skill", args={"skill_name": "..."})`` which the
toolbox dispatches normally. No new wire format introduced by spec 04.

Per D-04-10, the factory is **exported from this module** (and re-exported
from ``persona.skills.__init__``); it is **NOT** auto-registered inside
``persona.tools._factory.build_default_toolbox``. Spec 05's runtime
composes the toolbox with this tool when the persona declares skills.
Mirrors D-03-2's "sibling, not widening" pattern — spec-03's toolbox
surface is left untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.schema.tools import ToolResult
from persona.tools.protocol import AsyncTool, tool

if TYPE_CHECKING:
    from persona.schema.skills import SkillSpec

__all__ = ["make_use_skill_tool"]


def make_use_skill_tool(skills: list[SkillSpec]) -> AsyncTool:
    """Build a ``use_skill`` AsyncTool over the given skills.

    The returned tool validates ``skill_name`` against the closure-captured
    set of names. Unknown names return ``ToolResult(is_error=True)`` with
    the available list in the content (mirrors D-03-8's
    ``ToolNotAllowedError.context["allowed"]`` idiom).

    On a successful match, the tool returns
    ``ToolResult(is_error=False, data={"skill_name": "X"})``. The runtime
    inspects ``data["skill_name"]`` and dispatches to the injector.

    Args:
        skills: The persona's scanned skills. An empty list still produces
            a valid tool, but every call will return ``is_error=True`` —
            this is intended (a persona with no skills shouldn't dispatch
            ``use_skill``; the runtime simply won't register it if
            ``persona.skills`` is empty).

    Returns:
        An :class:`persona.tools.protocol.AsyncTool` instance named
        ``use_skill``.
    """
    available = {s.name for s in skills}

    @tool(
        name="use_skill",
        description=(
            "Activate one of the persona's declared skills by name. "
            "Pass the skill_name; the runtime will inject the skill's "
            "instructions into the next turn."
        ),
    )
    async def use_skill(skill_name: str) -> ToolResult:
        if skill_name not in available:
            return ToolResult(
                tool_name="use_skill",
                content=(
                    f"Unknown skill: {skill_name}; "
                    f"available: {', '.join(sorted(available)) or '(none)'}"
                ),
                is_error=True,
            )
        return ToolResult(
            tool_name="use_skill",
            content=f"Activating skill: {skill_name}",
            data={"skill_name": skill_name},
        )

    return use_skill
