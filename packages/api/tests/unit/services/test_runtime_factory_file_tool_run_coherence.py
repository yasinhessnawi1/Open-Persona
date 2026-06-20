"""Run-path file-tool coherence: file_write → file_read works inside a run.

The agentic RUN path (``RunRegistry._run``) binds a
:class:`SandboxRequestContext` (owner_id + conversation_id=run_id) and the RLS
``current_user_id`` contextvar around ``loop.run(...)``. The toolbox the run
drives is the SAME one ``RuntimeFactory.build_agentic_loop`` composes, whose
``file_read`` / ``file_write`` resolve their root at *dispatch* time from that
bound context via ``_build_file_sandbox_root_provider`` →
``<workspace_root>/<owner_id>/<persona_id>``.

This test proves the run-scoped file workspace is COHERENT: a ``file_write`` to a
relative path, then a ``file_read`` of the same path, returns the exact bytes
written AND the file lands on disk at the per-(owner, persona) root. It drives the
REAL factory-built tools directly (no LLM loop) — the loop only adds token
plumbing around these same dispatches.

Mirrors:
- ``test_runtime_factory_skills.test_build_toolbox_advertises_use_skill_for_skilled_persona``
  for the sentinel-deps ``RuntimeFactory`` + direct ``_build_toolbox`` build, and
- ``unit/background/test_run_worker_sandbox_context`` for binding the context
  exactly as ``RunRegistry._run`` does (owner + run-id session), reset in finally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from persona.backends.errors import TierNotConfiguredError
from persona.schema.persona import Persona, PersonaIdentity
from persona.schema.tools import ToolCall
from persona_api.middleware.rls_context import current_user_id
from persona_api.sandbox import (
    SandboxRequestContext,
    reset_sandbox_request_context,
    set_sandbox_request_context,
)
from persona_api.services.runtime_factory import RuntimeFactory

if TYPE_CHECKING:
    from pathlib import Path

_OWNER = "u_test"
_RUN_ID = "run_test"
_PERSONA_ID = "p_file_run"


class _NoTierRegistry:
    """A tier registry whose ``get`` always fails (keyless test environment).

    ``_build_toolbox`` calls ``get("small")`` to wire the optional
    ``text_summarize`` tool; on ``TierNotConfiguredError`` it skips that tool
    gracefully (graceful-absence pattern). We don't need ``text_summarize`` for
    file-tool coherence, so a registry that declines keeps the build keyless.
    """

    def get(self, tier_name: str) -> object:
        raise TierNotConfiguredError("no tiers configured", context={"tier": tier_name})

    @property
    def configured_tier_names(self) -> tuple[str, ...]:
        return ()

    async def aclose(self) -> None:
        return None


def _persona() -> Persona:
    """A persona that explicitly allows the two file tools under test."""
    return Persona(
        persona_id=_PERSONA_ID,
        identity=PersonaIdentity(
            name="Filer",
            role="Writes and reads files in a task run.",
            background="A persona used to prove run-path file-tool coherence.",
        ),
        tools=["file_read", "file_write"],
    )


def _make_factory(workspace_root: Path) -> RuntimeFactory:
    """Build a RuntimeFactory with a real workspace_root (so the file tools get a
    per-request scope provider) and otherwise-sentinel deps.

    ``_build_toolbox`` never queries the engine, embedder, or audit sink for the
    file tools — only ``workspace_root`` + ``persona_id`` decide whether the
    per-request sandbox-root provider is wired. A real ``audit_root`` directory
    is passed so JSONLAuditLogger construction (in ``_build_stores``, unused here)
    stays valid if reached.
    """
    return RuntimeFactory(
        rls_engine=object(),  # type: ignore[arg-type]  # not queried by _build_toolbox
        embedder=None,  # type: ignore[arg-type]
        tier_registry=_NoTierRegistry(),  # type: ignore[arg-type]
        turn_log_writer=None,  # type: ignore[arg-type]
        audit_root=workspace_root,
        workspace_root=workspace_root,
    )


@pytest.mark.asyncio
async def test_run_path_file_write_then_read_is_coherent(tmp_path: Path) -> None:
    """file_write → file_read round-trips inside a run-scoped sandbox context.

    Binds the context EXACTLY as ``RunRegistry._run`` does (owner + run-id
    session + RLS contextvar), dispatches the REAL ``file_write`` then
    ``file_read`` through the factory-built agentic toolbox, and asserts:

    - ``file_read`` returns the exact bytes ``file_write`` wrote, and
    - the file exists on disk at ``<workspace_root>/<owner>/<persona>/notes.md``.
    """
    workspace_root = tmp_path / "workspaces"
    factory = _make_factory(workspace_root)
    persona = _persona()
    _scanner, scanned = factory._scan_skills(persona)  # noqa: SLF001
    toolbox = await factory._build_toolbox(persona, scanned)  # noqa: SLF001

    # The toolbox must actually advertise both file tools (persona allow-list).
    names = toolbox.names()  # type: ignore[attr-defined]
    assert {"file_read", "file_write"} <= set(names), names

    # Bind the run-scoped context exactly as RunRegistry._run does, reset in
    # the finally regardless of outcome.
    rls_token = current_user_id.set(_OWNER)
    sandbox_token = set_sandbox_request_context(
        SandboxRequestContext(owner_id=_OWNER, conversation_id=_RUN_ID)
    )
    try:
        write_result = await toolbox.dispatch(  # type: ignore[attr-defined]
            ToolCall(
                name="file_write",
                args={"path": "notes.md", "content": "hello from the task"},
            )
        )
        assert not write_result.is_error, write_result.content

        read_result = await toolbox.dispatch(  # type: ignore[attr-defined]
            ToolCall(name="file_read", args={"path": "notes.md"})
        )
        assert not read_result.is_error, read_result.content
        # The read returns EXACTLY what was written.
        assert read_result.content == "hello from the task"
    finally:
        reset_sandbox_request_context(sandbox_token)
        current_user_id.reset(rls_token)

    # The file landed on disk at the per-(owner, persona) run workspace root.
    on_disk = workspace_root / _OWNER / _PERSONA_ID / "notes.md"
    assert on_disk.is_file(), f"expected file at {on_disk}"
    assert on_disk.read_text(encoding="utf-8") == "hello from the task"


@pytest.mark.asyncio
async def test_run_path_file_tools_fail_closed_without_bound_context(tmp_path: Path) -> None:
    """With NO context bound, the run-path file tools fail closed (no fallback).

    The per-request provider returns ``None`` when no ``SandboxRequestContext``
    is bound, so ``file_write`` must NOT write to any shared root — it errors
    instead. This is the security invariant the run worker's context-binding
    exists to satisfy.
    """
    workspace_root = tmp_path / "workspaces"
    factory = _make_factory(workspace_root)
    persona = _persona()
    _scanner, scanned = factory._scan_skills(persona)  # noqa: SLF001
    toolbox = await factory._build_toolbox(persona, scanned)  # noqa: SLF001

    # No set_sandbox_request_context here — deliberately unbound.
    result = await toolbox.dispatch(  # type: ignore[attr-defined]
        ToolCall(name="file_write", args={"path": "leak.md", "content": "should not persist"})
    )
    assert result.is_error, "file_write must fail closed when no context is bound"
    # Nothing was written anywhere under the workspace root.
    assert not list(workspace_root.rglob("leak.md")), "file_write leaked with no context bound"
