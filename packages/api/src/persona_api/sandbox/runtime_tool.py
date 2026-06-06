"""API-side ``code_execution`` tool factory composing pool + credits (spec 12 T10).

Bridges:

- :class:`persona_api.sandbox.pool.SandboxPool` — owns the substrate session
  lifecycle (per-tenant cap, idle reap, composition-root invariant).
- :func:`persona.sandbox.make_code_execution_tool` — the T03 tool body that
  the runtime/agentic loop dispatch through unchanged.
- :func:`persona_api.services.credits_service.deduct` — flat per-execution
  billing per D-12-3 (mirrors D-08-6: only successful executions are billed).
- :class:`SandboxRequestContext` — per-request ``(owner_id, conversation_id)``
  thread via contextvars so the loop builders' signatures stay stable.

**Lazy-eager acquire (D-12-17):** the pool's ``acquire()`` is called from the
tool's ``pre_execute_hook`` on first dispatch — not at toolbox-build time.
This matches D-12-17's "warm=0 with lazy-eager prewarm" semantics: a
conversation that never invokes ``code_execution`` never spawns a substrate
sandbox. Pool ``SandboxError`` raises (quota, unavailability) flow through
the T03 catch-and-convert path and surface as ``ToolResult(is_error=True)``.

**Credits hook:** ``credits_service.deduct`` is sync (short DB transaction);
the hook wraps it with :func:`asyncio.to_thread` so the event loop isn't
blocked. Hook failure is caught inside ``make_code_execution_tool`` so a
billing-write outage cannot break the tool's structured result (the model
still sees the successful execution).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from persona.logging import get_logger
from persona.sandbox.tool import make_code_execution_tool

from persona_api.sandbox.context import get_sandbox_request_context
from persona_api.services import credits_service

if TYPE_CHECKING:
    from persona.sandbox.result import ExecutionResult, NetworkPolicy, ResourceLimits
    from persona.tools.audit import ToolAuditLogger
    from persona.tools.protocol import AsyncTool
    from sqlalchemy import Engine

    from persona_api.sandbox.pool import SandboxPool

__all__ = ["make_pool_code_execution_tool"]

_logger = get_logger("sandbox.runtime_tool")

# D-12-3 flat per-execution credit cost (v0.1). v0.2 switches to duration-
# based against E2B's per-second billing per D-12-3's "Reversibility ~1 day".
_CODE_EXECUTION_CREDIT_COST = 1


def make_pool_code_execution_tool(
    *,
    pool: SandboxPool,
    rls_engine: Engine,
    network_policy: NetworkPolicy | None = None,
    resource_limits: ResourceLimits | None = None,
    audit_logger: ToolAuditLogger | None = None,
    persona_id: str | None = None,
    credit_cost: int = _CODE_EXECUTION_CREDIT_COST,
) -> AsyncTool:
    """Build the API-composed ``code_execution`` tool.

    On dispatch (per call):

      1. Read the per-request :class:`SandboxRequestContext` from contextvars
         (set by ``chat_service.stream_chat``). Without context, the tool
         dispatches in stateless one-shot mode (no pool acquire).
      2. ``pre_execute_hook`` lazily acquires the pool session for
         ``(owner_id, conversation_id)``. Pool ``SandboxError`` (quota,
         unavailability) flows through the T03 catch path → structured
         ``ToolResult(is_error=True)`` so the model recovers.
      3. T03 body dispatches to ``pool.sandbox.execute(code, session_id=...)``.
      4. ``on_execute_success`` (outcome=="ok") deducts ``credit_cost`` credits
         via :func:`credits_service.deduct` wrapped in :func:`asyncio.to_thread`.

    Args:
        pool: The hosted :class:`SandboxPool` (composition-root-owned).
        rls_engine: The RLS-scoped engine used by ``credits_service.deduct``.
        network_policy / resource_limits: Per-persona policy (D-12-4 / D-12-1).
        audit_logger / persona_id: Forwarded to the T03 factory for audit emission.
        credit_cost: Flat per-execution credit deduction (D-12-3 default = 1).

    Returns:
        An :class:`AsyncTool` named ``code_execution`` ready to register in
        the toolbox via :func:`build_default_toolbox`'s ``extra_tools`` slot.
    """

    def _session_id_provider() -> str | None:
        ctx = get_sandbox_request_context()
        return ctx.session_id if ctx is not None else None

    async def _pre_execute_hook() -> None:
        """Lazy-eager: acquire (or re-acquire) the pool session before execute.

        Pool ``acquire`` is idempotent on ``(user_id, conversation_id)`` — a
        re-acquire bumps last-used (so the reaper sees it fresh) without
        spawning a second substrate sandbox. Without a request context, this
        is a no-op (CLI / one-shot path).
        """
        ctx = get_sandbox_request_context()
        if ctx is None:
            return
        await pool.acquire(user_id=ctx.owner_id, conversation_id=ctx.conversation_id)

    async def _on_execute_success(_result: ExecutionResult) -> None:
        """Credits hook fired by the T03 body on outcome=="ok"."""
        ctx = get_sandbox_request_context()
        if ctx is None:
            # No request context → CLI / one-shot path; no billing.
            return
        await asyncio.to_thread(
            credits_service.deduct,
            rls_engine=rls_engine,
            user_id=ctx.owner_id,
            amount=credit_cost,
            reason="code_execution",
        )

    return make_code_execution_tool(
        pool.sandbox,
        network_policy=network_policy,
        resource_limits=resource_limits,
        audit_logger=audit_logger,
        persona_id=persona_id,
        session_id_provider=_session_id_provider,
        pre_execute_hook=_pre_execute_hook,
        on_execute_success=_on_execute_success,
    )
