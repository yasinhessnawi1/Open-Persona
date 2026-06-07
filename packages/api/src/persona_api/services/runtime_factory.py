"""Runtime composition root (spec 08, T10).

Builds the **real** ``ConversationLoop`` / ``AgenticLoop`` per request, wiring
every collaborator (the keystones T08/T11 consume a ``build_*`` closure from
here). The app-scoped ``TierRegistry`` + MCP clients are owned by the lifespan
(T10 startup/shutdown): ``await tier_registry.aclose()`` + ``await
client.disconnect()`` on shutdown (D-05-4 / spec-06 handoff). The loops never
close the registry.

Per request, the four typed stores compose ``PostgresBackend`` over the
**RLS engine** (the checkout listener scopes every store connection to the
tenant — D-08-1), so the loop's memory reads/writes are tenant-isolated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.audit import JSONLAuditLogger
from persona.config import PersonaCoreConfig
from persona.errors import PersonaNotFoundError
from persona.history import ConversationHistoryManager
from persona.schema.persona import Persona
from persona.skills import BUILTIN_ROOT, SkillInjector, SkillScanner, make_use_skill_tool
from persona.stores import (
    EpisodicStore,
    IdentityStore,
    SelfFactsStore,
    WorldviewStore,
)
from persona.stores.postgres import PostgresBackend
from persona.tools import build_default_toolbox
from persona_runtime.agentic.loop import AgenticLoop
from persona_runtime.loop import ConversationLoop
from persona_runtime.prompt import PromptBuilder
from persona_runtime.router import Router
from sqlalchemy import select

from persona_api.db.models import personas as personas_t
from persona_api.sandbox import make_pool_code_execution_tool

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from persona.sandbox.result import SandboxFile
    from persona.stores.embedder import Embedder
    from persona.stores.protocol import MemoryStore
    from persona.tools.mcp.client import MCPClient
    from persona_runtime.logging import TurnLogWriter
    from persona_runtime.tier import TierRegistry
    from sqlalchemy import Engine

    from persona_api.sandbox.pool import SandboxPool

__all__ = ["RuntimeFactory"]


class RuntimeFactory:
    """Composes per-request loops; owns the app-scoped registry + MCP clients.

    One instance lives on ``app.state`` for the process. ``build_conversation_loop``
    / ``build_agentic_loop`` are the closures the routes call per request (the
    persona is loaded + stores built under the active RLS scope).
    """

    def __init__(
        self,
        *,
        rls_engine: Engine,
        embedder: Embedder,
        tier_registry: TierRegistry,
        turn_log_writer: TurnLogWriter,
        audit_root: Path,
        core_config: PersonaCoreConfig | None = None,
        sandbox_pool: SandboxPool | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self._engine = rls_engine
        self._embedder = embedder
        self._tier_registry = tier_registry
        self._turn_log_writer = turn_log_writer
        self._audit_root = audit_root
        self._core_config = core_config or PersonaCoreConfig()
        # Spec 12 T10 — hosted sandbox pool. None when E2B_API_KEY is unset
        # (dev environments without an account boot cleanly); the
        # ``code_execution`` tool is absent from the toolbox in that case
        # and surfaces ``SandboxUnavailableError`` if the model still calls it.
        self._sandbox_pool = sandbox_pool
        # Spec 17 D-17-X-bytes-persistence — workspace root for produced-file
        # persist + intermediate/* cross-turn staging. Threaded through to
        # ``make_pool_code_execution_tool``. None (test / CLI path) ⇒ no
        # persistence + no staging; tool dispatches as before.
        self._workspace_root = workspace_root
        # MCP clients accumulated across requests, closed on shutdown.
        self._mcp_clients: list[MCPClient] = []

    # -- shared per-request pieces ------------------------------------------

    def _load_persona(self, persona_id: str) -> Persona:
        """Load + validate the persona's YAML from the RLS-scoped row (→ 404)."""
        with self._engine.begin() as conn:
            row = (
                conn.execute(select(personas_t.c.yaml).where(personas_t.c.id == persona_id))
                .mappings()
                .first()
            )
        if row is None:
            raise PersonaNotFoundError("persona not found", context={"id": persona_id})
        import yaml

        raw = yaml.safe_load(str(row["yaml"]))
        if isinstance(raw, dict):
            raw["persona_id"] = persona_id
        return Persona.model_validate(raw)

    def _build_stores(self) -> dict[str, MemoryStore]:
        """The four typed stores over PostgresBackend (RLS-scoped engine)."""
        backend = PostgresBackend(engine=self._engine, embedder=self._embedder)
        audit = JSONLAuditLogger(self._audit_root)
        return {
            "identity": IdentityStore(backend=backend, audit_logger=audit),
            "self_facts": SelfFactsStore(backend=backend, audit_logger=audit),
            "worldview": WorldviewStore(backend=backend, audit_logger=audit),
            "episodic": EpisodicStore(backend=backend, audit_logger=audit),
        }

    async def _build_toolbox(
        self,
        persona: Persona,
        scanned_skills: list[object],
        deferred_input_files_holder: list[SandboxFile] | None = None,
    ) -> object:
        """Build the toolbox (+ use_skill when the persona has skills + code_execution
        when the sandbox pool is configured). MCP clients are tracked for shutdown.

        ``deferred_input_files_holder`` is the Spec 16 M1a shared bucket:
        a mutable ``list[SandboxFile]`` the caller passes in BEFORE the loop
        is constructed (the loop will share the same list as its
        ``deferred_input_files`` attribute). The ``code_execution`` tool's
        ``deferred_input_files_provider`` callable is a drain-and-clear
        closure over the same list — when the runtime appends staged
        supplements at the use_skill intercept, the next ``code_execution``
        dispatch sees them, consumes them, and clears the holder so a
        second dispatch in the same turn doesn't re-stage. ``None`` (test
        path) ⇒ no provider wired; staging is a no-op.
        """
        extra: list[object] = []
        if scanned_skills:
            extra.append(make_use_skill_tool(scanned_skills))  # type: ignore[arg-type]
        if self._sandbox_pool is not None:
            # Spec 12 T10: API-composed code_execution wires
            # (a) lazy-eager pool acquire via pre_execute_hook (D-12-17),
            # (b) D-12-3 flat per-execution credits deduction on outcome=="ok",
            # (c) Spec 16 M1a deferred-input-files drain-and-clear (D-16-2,
            #     D-16-2-state-location).
            provider: Callable[[], list[SandboxFile]] | None
            if deferred_input_files_holder is not None:
                # Bind the holder via a closure (not a default argument) so
                # mypy's narrowing carries through. The closure captures the
                # non-None holder by reference; mutations are visible across
                # the use_skill intercept (writer) and the tool dispatch
                # (drainer).
                holder = deferred_input_files_holder

                def _drain_and_clear() -> list[SandboxFile]:
                    """Return current staged supplements + clear the holder.

                    Atomic enough for the single-threaded asyncio loop the
                    tool dispatches on: ``copy()`` snapshot + ``clear()``
                    happen between awaits. The runtime's use_skill intercept
                    appends to ``holder``; the tool's dispatch drains.
                    """
                    snapshot = list(holder)
                    holder.clear()
                    return snapshot

                provider = _drain_and_clear
            else:
                provider = None
            extra.append(
                make_pool_code_execution_tool(
                    pool=self._sandbox_pool,
                    rls_engine=self._engine,
                    persona_id=persona.persona_id,
                    deferred_input_files_provider=provider,
                    workspace_root=self._workspace_root,
                )
            )
        toolbox, mcp_clients = await build_default_toolbox(
            self._core_config,
            persona,
            extra_tools=extra or None,  # type: ignore[arg-type]
        )
        self._mcp_clients.extend(mcp_clients)
        return toolbox

    def _scan_skills(self, persona: Persona) -> tuple[SkillScanner, list[object]]:
        # ``BUILTIN_ROOT`` (re-exported from persona-core ``persona.skills``)
        # is the single source of truth shared with ``catalog_service`` so
        # both surfaces resolve declared skills against the same on-disk
        # directory. Without this path, every persona-declared skill would
        # log a "declared skill not found" warning at every chat turn and
        # the loop would never inject any skill content.
        scanner = SkillScanner(skill_paths=[BUILTIN_ROOT])
        scanned = scanner.scan(
            declared_skills=persona.skills,
            tool_allow_list=list(persona.tools) if persona.tools else None,
        )
        return scanner, list(scanned)

    # -- the closures the routes call ---------------------------------------

    async def build_conversation_loop(self, persona_id: str) -> ConversationLoop:
        """Construct the ConversationLoop for ``persona_id`` (KEYSTONE 1, T08).

        Wires the Spec 16 M1a deferred-input-files holder (D-16-2 /
        D-16-2-state-location): a single ``list[SandboxFile]`` is shared
        between the loop's public ``deferred_input_files`` attribute and
        the ``code_execution`` tool's drain-and-clear provider closure.
        The use_skill intercept appends to this list; the next
        ``code_execution`` dispatch drains it.
        """
        persona = self._load_persona(persona_id)
        scanner, scanned = self._scan_skills(persona)
        # M1a shared holder — created BEFORE the toolbox so the
        # code_execution tool's drain-and-clear provider closes over the
        # same list the loop will write to.
        deferred_holder: list[SandboxFile] = []
        toolbox = await self._build_toolbox(
            persona,
            scanned,
            deferred_input_files_holder=deferred_holder,
        )
        loop = ConversationLoop(
            persona=persona,
            stores=self._build_stores(),
            toolbox=toolbox,  # type: ignore[arg-type]
            skill_scanner=scanner,
            skill_injector=SkillInjector(),
            scanned_skills=scanned,  # type: ignore[arg-type]
            history_manager=ConversationHistoryManager(),
            prompt_builder=PromptBuilder(),
            router=Router(),
            tier_registry=self._tier_registry,
            turn_log_writer=self._turn_log_writer,
        )
        # Replace the loop's default-empty deferred_input_files with the
        # SHARED holder (same identity), so the use_skill intercept's
        # ``self.deferred_input_files.extend(...)`` mutates the same list
        # the tool's provider drains. Per D-16-2-state-location the
        # attribute is public for exactly this composition-root binding.
        loop.deferred_input_files = deferred_holder
        return loop

    async def build_agentic_loop(self, persona_id: str) -> AgenticLoop:
        """Construct the AgenticLoop for ``persona_id`` (KEYSTONE 2, T11).

        Wires the Spec 16 M1a deferred-input-files holder symmetrically to
        :meth:`build_conversation_loop`.
        """
        persona = self._load_persona(persona_id)
        _scanner, scanned = self._scan_skills(persona)
        deferred_holder: list[SandboxFile] = []
        toolbox = await self._build_toolbox(
            persona,
            scanned,
            deferred_input_files_holder=deferred_holder,
        )
        loop = AgenticLoop(
            persona=persona,
            stores=self._build_stores(),
            toolbox=toolbox,  # type: ignore[arg-type]
            skill_injector=SkillInjector(),
            scanned_skills=scanned,  # type: ignore[arg-type]
            prompt_builder=PromptBuilder(),
            router=Router(),
            tier_registry=self._tier_registry,
        )
        loop.deferred_input_files = deferred_holder
        return loop

    async def build_title(self, first_message: str) -> str:
        """Generate a short (≤5-word) conversation title from the first message,
        using the **small** tier (pure boilerplate — architecture §5.1.1). Returns
        the title text; the caller (chat_service) applies it best-effort."""
        from datetime import UTC, datetime

        from persona.schema.conversation import ConversationMessage

        backend = self._tier_registry.get("small")
        now = datetime.now(UTC)
        prompt = [
            ConversationMessage(
                role="system",
                content=(
                    "Summarise the user's message as a conversation title of at most "
                    "5 words. Output ONLY the title — no quotes, no punctuation, no prose."
                ),
                created_at=now,
            ),
            ConversationMessage(role="user", content=first_message, created_at=now),
        ]
        response = await backend.chat(prompt, temperature=0.0, max_tokens=24)
        return response.content.strip().strip('"').splitlines()[0] if response.content else ""

    # -- lifecycle ----------------------------------------------------------

    async def aclose(self) -> None:
        """Shutdown: close the tier registry + every MCP client (D-05-4)."""
        await self._tier_registry.aclose()
        for client in self._mcp_clients:
            await client.disconnect()
