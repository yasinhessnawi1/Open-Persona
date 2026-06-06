"""The :class:`CodeSandbox` Protocol — sandbox execution contract (spec 12 T02).

Defines the structural contract every sandbox backend satisfies:

- :class:`LocalDockerSandbox` (T05a/b/c, behind a ``[sandbox]`` extra in
  ``pyproject.toml``) — runs on the user's machine for the open-source CLI.
- ``HostedSandbox`` (T08, in ``persona-api``) — runs against the E2B
  managed substrate per D-12-12 (provisional; locks after the five
  Phase-5 acceptance gates measured at T08-T11).

Both backends are duck-typed via Protocol structural subtyping. The
runtime and the agentic loop never import a concrete backend — they
depend on this Protocol and the composition root wires the appropriate
implementation (D-04-10 ``use_skill`` precedent; D-08 composition-root
pattern carried).

**Two cross-cutting decisions are encoded directly in the Protocol shape:**

- **D-12-4** ``NetworkPolicy`` is **not** a parameter of
  :meth:`CodeSandbox.execute` — it's bound at :meth:`CodeSandbox.create_session`
  time from the persona's YAML. Stateless one-shots (``session_id=None``) take
  the policy on ``execute()`` because there's no session to bind it to; the
  T03 tool factory always constructs the policy from the persona, never from
  model-supplied tool-call arguments.
- **D-12-7** :meth:`CodeSandbox.aclose` is **explicit** on the Protocol —
  every implementer ships lifecycle teardown so the composition root can
  reap warm pools (T09) and substrate-side state symmetrically. Forgetting
  it on a paid managed substrate is a continuous-billing leak (kickoff
  trip-up #7). Differs from spec-05 D-05-4 (which duck-typed
  :meth:`TierRegistry` backend cleanup because not every backend has
  lifecycle state — sandboxes ALL have it).

**Tenant-isolated session IDs (kickoff trip-up #6):** the runtime composes
``session_id`` as ``f"{owner_id}:{conversation_id}"`` — never bare
``conversation_id`` — so a different tenant with a colliding
``conversation_id`` cannot share session state. The Protocol takes
``session_id`` opaque; tenant-prefixing is the *caller's* invariant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from persona.sandbox.result import (
        ExecutionResult,
        NetworkPolicy,
        ResourceLimits,
        SandboxFile,
    )

__all__ = ["CodeSandbox"]


@runtime_checkable
class CodeSandbox(Protocol):
    """Structural contract for executing model-generated code in isolation.

    Two execution modes:

    - **Stateless one-shot** — ``session_id=None`` on :meth:`execute`. The
      substrate creates a fresh sandbox, runs the code, returns the result,
      and destroys the sandbox. No state survives.
    - **Stateful kernel-style session** — a session is created via
      :meth:`create_session`; subsequent :meth:`execute` calls with the same
      ``session_id`` share interpreter state (variables, imports, open files)
      — the IPython mental model per D-12-1. Idle sessions are reaped by the
      pool (T09) at the spec §7.1 idle timeout.

    All methods raise :class:`persona.sandbox.errors.SandboxError`
    subclasses on failure — never bare ``Exception``. The T03 tool factory
    catches and converts them to ``ToolResult(is_error=True, ...)`` so the
    model can recover, never crashes the SSE stream (spec-11 fix #1
    discipline carried).
    """

    async def execute(
        self,
        code: str,
        *,
        language: Literal["python"] = "python",
        session_id: str | None = None,
        timeout_s: float = 30.0,
        limits: ResourceLimits | None = None,
        network: NetworkPolicy | None = None,
        input_files: list[SandboxFile] | None = None,
    ) -> ExecutionResult:
        """Execute ``code`` in the sandbox; return the result.

        Args:
            code: The source code to execute. Treated as adversarial — see
                spec §5.
            language: Source language. ``"python"`` only in v0.1 (spec §2
                "Languages beyond Python for v1 of this spec" is out of scope).
                Future languages slot in behind the same Protocol.
            session_id: If ``None``, run as a stateless one-shot in a fresh
                ephemeral sandbox. If provided, run inside the persistent
                kernel session previously created via :meth:`create_session`
                — variables / imports / files from prior executions in the
                same session are visible. Tenant-isolated by the caller
                (``f"{owner_id}:{conversation_id}"`` shape; kickoff trip-up #6).
            timeout_s: Per-execution wall-clock cap in seconds. Should be
                ≤ ``limits.wall_clock_s`` (this is the per-call cap;
                ``limits.wall_clock_s`` is the session-wide ceiling). The
                substrate enforces the hard kill.
            limits: Resource caps (CPU / memory / disk / stdout / produced
                files). ``None`` ⇒ substrate defaults; the T03 tool factory
                always passes an explicit :class:`ResourceLimits` so this is
                only for direct callers.
            network: Network policy (default off per D-12-4). For session-mode
                this is ignored — :meth:`create_session` binds the policy
                once per session; for one-shot (``session_id=None``) this is
                applied for the single execution. ``None`` ⇒
                ``NetworkPolicy()`` (egress disabled).
            input_files: Files to seed into the sandbox workspace before
                execution. Written to the input-mount per D-12-9. The
                workspace's existing files (persisted across executions in
                the same session) are NOT replaced — ``input_files`` is
                additive.

        Returns:
            :class:`ExecutionResult` with ``stdout`` / ``stderr`` / ``outcome``
            / ``produced_files`` / ``duration_ms``. Always returned — never
            raises on code-level failure (non-zero exit → ``outcome="error"``;
            wall-clock cap → ``outcome="timeout"``; OOM → ``outcome="oom"``).

        Raises:
            persona.sandbox.errors.SandboxUnavailableError: The backend
                substrate is unreachable (Docker daemon down locally; E2B
                API outage; concurrency cap reached). Per D-12-5 there is
                NO degraded fallback — the tool surfaces this via
                ``ToolResult(is_error=True, ...)`` and the model recovers.
            persona.sandbox.errors.CodeSandboxError: Backend internal
                failure that doesn't fit a more specific subclass.
            persona.sandbox.errors.ExecutionTimeoutError: Raised by the
                backend ONLY if it cannot synthesise an ``outcome="timeout"``
                :class:`ExecutionResult` — generally the backend produces the
                result instead so the model sees a tool result it can
                reason about.
            persona.sandbox.errors.ResourceLimitError: Same conditional as
                above — generally surfaces as ``outcome="oom"`` / ``"killed"``
                on the result, not a raise.
        """
        ...

    async def create_session(
        self,
        session_id: str,
        *,
        limits: ResourceLimits,
        network: NetworkPolicy,
    ) -> None:
        """Create a persistent kernel-style session.

        Idempotent in spirit: creating a session with an ID that already
        exists is a no-op (the existing session is returned by subsequent
        :meth:`execute` calls). Backends that cannot make this idempotent
        cheaply may raise :class:`CodeSandboxError` on collision; the
        composition root (T10) treats this defensively.

        Args:
            session_id: Caller-supplied stable identifier. Tenant-isolated
                by the caller per kickoff trip-up #6 — the Protocol takes
                the value opaque.
            limits: Resource caps for the session. The session-wide
                ``wall_clock_s`` is the absolute kernel-lifetime cap.
            network: Network policy applied to every execution in the
                session — bound once at create time per D-12-4.

        Raises:
            persona.sandbox.errors.SandboxUnavailableError: Substrate
                unreachable or concurrency cap reached.
            persona.sandbox.errors.CodeSandboxError: Backend internal
                failure (e.g., session-id collision on a backend that can't
                idempotently no-op).
        """
        ...

    async def destroy_session(self, session_id: str) -> None:
        """Destroy a persistent session and release its substrate resources.

        Idempotent: destroying a session that doesn't exist (already reaped
        by the pool, or never created) is a no-op. The pool (T09) calls this
        at the spec §7.1 idle-timeout; the composition root calls it on
        conversation end and during :meth:`aclose`.

        Args:
            session_id: The session to destroy.
        """
        ...

    async def aclose(self) -> None:
        """Release all substrate-side resources owned by this backend.

        Closes warm pools, destroys live sessions, drops substrate API
        client connections. Idempotent: a second :meth:`aclose` is a no-op.

        Per D-12-7 this is **explicit on the Protocol** rather than
        duck-typed (the D-05-4 pattern), because forgetting to call it on
        a paid managed substrate is a continuous-billing leak. The
        composition root (T10) registers this with the FastAPI lifespan
        symmetrically to :meth:`TierRegistry.aclose` and the MCP client
        :meth:`disconnect` calls.
        """
        ...
