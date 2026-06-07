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
    from persona.sandbox.result import SandboxFile
    from persona.schema.skills import SkillSpec

__all__ = ["collect_skill_supplements", "make_use_skill_tool"]


def collect_skill_supplements(spec: SkillSpec) -> list[SandboxFile]:
    """Scan ``<spec.path>/supplements/`` for ``*.md`` files (Spec 16 M1a, D-16-2).

    Returns a list of :class:`SandboxFile` entries with **relative** ``path``
    values of the form ``.skills/<spec.name>/supplements/<topic>.md``
    (D-16-2-supplements-relative-path). The consumer joins them with the
    substrate's bind-mount root:

    * Local docker: ``host_in / f.path`` → ``<host_in>/.skills/<name>/...``,
      which becomes ``/workspace/in/.skills/<name>/...`` inside the container
      (the path the SKILL.md packs teach the model to read).
    * Hosted E2B: ``/home/user/{f.path}`` → ``/home/user/.skills/<name>/...``
      (the hosted equivalent of ``/workspace/in`` per D-12-9).

    **Source-of-truth discipline:** the path *in transport* (the
    ``SandboxFile.path`` field) is relative; the absolute form is only the
    mounted destination inside the sandbox. Earlier revisions used an
    absolute ``/workspace/in/.skills/...`` here, which short-circuited
    ``Path('/host_in') / '/workspace/in/...' == Path('/workspace/in/...')``
    semantics and caused the host-side write to fail with
    ``OSError: [Errno 30] Read-only file system: '/workspace'`` (D-16-X-7).

    The runtime stages these as ``input_files`` on the next ``code_execution``
    dispatch so the model can read deeper guidance from inside sandboxed code.
    No supplements directory ⇒ empty list (the lean case for skills that fit
    inline). Tool factory stays oblivious to ``SkillSpec`` — it just receives
    generic ``SandboxFile`` entries (D-16-2-wiring boundary).
    """
    from persona.sandbox.result import SandboxFile

    supplements_dir = spec.path / "supplements"
    if not supplements_dir.is_dir():
        return []
    staged: list[SandboxFile] = []
    for md_path in sorted(supplements_dir.glob("*.md")):
        payload = md_path.read_bytes()
        staged.append(
            SandboxFile(
                path=f".skills/{spec.name}/supplements/{md_path.stem}.md",
                content_bytes=payload,
                size_bytes=len(payload),
                media_type="text/markdown",
            )
        )
    return staged


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
