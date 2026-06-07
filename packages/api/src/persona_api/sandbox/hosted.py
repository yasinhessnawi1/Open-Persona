"""HostedSandbox — E2B Firecracker-microVM backend (spec 12 T08; D-12-12 substrate).

Adapter that wraps the E2B Code Interpreter Python SDK (``e2b-code-interpreter``)
into the :class:`persona.sandbox.protocol.CodeSandbox` Protocol so the same
``code_execution`` tool factory + runtime + agentic loop work unchanged across
local and hosted backends — the load-bearing reversibility property D-12-12
buys via the Protocol design.

**Threat-model scope (D-12-13):** isolation is **substrate-provided** by E2B's
Firecracker microVM. We do NOT replicate the R-12-2 Docker hardening here —
those flags are kernel-namespace knobs that don't apply to a microVM with its
own guest kernel. The equivalent hosted controls are the E2B SDK's native
``allow_internet_access`` (egress on/off), the substrate's CPU/memory caps,
and the SDK's ``kill()`` lifecycle.

**§9 acceptance contract:** the same T04 attack catalog
(``packages/core/tests/integration/sandbox/_attacks.py``) parametrises onto
``[hosted]`` — when E2B is reachable, the 26 attacks run against this backend
and verify the substrate delivers the same containment as
``LocalDockerSandbox``. The reversibility cost quantified in D-12-12 buys this.

**Lock-gates (D-12-12 §Lock criteria):** before T09 ships, T08 measures the
five gates against a real E2B Hobby account:
  1. Cold-start p95 < 2.5s (n>=20)
  2. 20-sandbox concurrent fan-out, p95 ready < 5s
  3. Adversarial egress denial — every IP in §9 #7 catalog blocked with
     ``allow_internet_access=False`` regardless of allow-list
  4. Mid-exec kill: SDK raises clean domain exception; no zombies
  5. 7-day realistic-load cost projection < $50/mo
The script that measures these lives at
``packages/api/tests/integration/sandbox/test_e2b_lock_gates.py``.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path  # noqa: TC003 — runtime use in copy_produced_file_to
from typing import TYPE_CHECKING, Any

from persona.logging import get_logger
from persona.sandbox.errors import (
    CodeSandboxError,
    ExecutionTimeoutError,
    ProducedFileSizeError,
    SandboxUnavailableError,
)
from persona.sandbox.protocol import PRODUCED_FILE_CAP_BYTES
from persona.sandbox.result import (
    ExecutionOutcome,
    ExecutionResult,
    NetworkPolicy,
    ResourceLimits,
    SandboxFile,
)

if TYPE_CHECKING:
    # The E2B SDK is lazy-imported at use-site (so importing this module
    # without the SDK installed is a graceful SandboxUnavailableError at
    # runtime, not an ImportError at module-load). For type-checking, pull
    # the ``Sandbox`` symbol into scope so the SDK-bearing parameter
    # annotations carry the real type instead of ``Any``.
    from e2b_code_interpreter import Sandbox as E2BSandbox

__all__ = ["HostedSandbox"]

_logger = get_logger("sandbox.hosted")

# T12 SCP-12-4 — hosted substrate template-class floors (E2B Hobby tier).
# The T12 audit measured MemTotal=2 GiB and RLIMIT_AS=-1 inside the default
# sandbox; tmpfs ~1 GiB at /tmp. User-supplied ResourceLimits below these
# floors are advisory only and emit a warning at sandbox construction.
# Future custom-template work (D-12-12 follow-up) can raise these by
# requesting a different template; lowering is not supported at Hobby tier.
_HOSTED_SUBSTRATE_MEMORY_FLOOR_MB = 2048
_HOSTED_SUBSTRATE_DISK_FLOOR_MB = 1024


class HostedSandbox:
    """E2B Firecracker-microVM backend satisfying the :class:`CodeSandbox` Protocol.

    Args:
        api_key: E2B API key. ``None`` ⇒ the SDK reads ``E2B_API_KEY`` from the
            environment natively (preferred — keeps secrets out of constructor calls).
        template: E2B sandbox template. ``None`` ⇒ the SDK default
            (``code-interpreter-v1``, the data-stack-preinstalled image).
            Production may override with a persona-sandbox-derived template
            once T06's image is published to E2B's registry (D-12-2 / R-12-3).
        timeout_default_s: Default per-sandbox idle timeout (substrate-side
            sandbox lifetime ceiling). Per-execute ``timeout_s`` is enforced
            by the SDK's ``request_timeout`` (separate dimension).

    Raises:
        SandboxUnavailableError: When the E2B API is unreachable or the
            account is unauthenticated (the substrate's equivalent of
            ``LocalDockerSandbox``'s "Docker daemon unreachable").
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        template: str | None = None,
        timeout_default_s: int = 300,
    ) -> None:
        self._api_key = api_key  # None ⇒ SDK reads E2B_API_KEY env var
        self._template = template
        self._timeout_default_s = timeout_default_s
        self._closed = False
        # Per-session E2B sandbox references — sandbox_id ↔ Sandbox SDK object.
        # Kept in memory; T09 pool replaces this with a warm-pool + reaper.
        self._sessions: dict[str, E2BSandbox] = {}
        _logger.debug(
            "HostedSandbox initialised",
            template=template or "<default>",
            timeout_default_s=timeout_default_s,
        )

    # -- CodeSandbox Protocol methods -------------------------------------

    async def execute(
        self,
        code: str,
        *,
        language: str = "python",
        session_id: str | None = None,
        timeout_s: float = 30.0,
        limits: ResourceLimits | None = None,
        network: NetworkPolicy | None = None,
        input_files: list[SandboxFile] | None = None,
    ) -> ExecutionResult:
        """Run ``code`` in an E2B sandbox; return the result.

        Stateless one-shot (``session_id=None``) creates a fresh sandbox,
        runs the code, kills the sandbox. Stateful (``session_id`` set) runs
        in the persistent E2B sandbox from :meth:`create_session` — variable
        state DOES persist (E2B's sandbox runs a single long-lived IPython
        kernel; this is the D-12-1 mental model the spec calls for, hosted
        side, no scaling caveat).
        """
        if self._closed:
            msg = "HostedSandbox is closed"
            raise SandboxUnavailableError(msg, context={"reason": "closed"})
        if language != "python":
            msg = f"unsupported language: {language!r}"
            raise CodeSandboxError(msg, context={"language": language})

        limits = limits or ResourceLimits()
        network = network or NetworkPolicy()
        input_files = input_files or []

        # T12 F-T12-RES-02 fix: enforce ``wall_clock_s`` at the async boundary.
        # The E2B SDK's ``run_code(timeout=...)`` maps to httpx read-timeout,
        # NOT substrate wall-clock — a silent CPU-bound ``while True: pass``
        # hung > 90 s in the T12 probe before OS SIGKILL took effect, breaking
        # spec §9 #8. Wrap the to_thread call in :func:`asyncio.wait_for` with
        # a small grace window for substrate-side cleanup; on timeout, kill
        # the substrate sandbox (if stateful) and raise
        # :class:`ExecutionTimeoutError` so the existing T03 catch path
        # converts it to ``ToolResult(outcome="timeout", is_error=True)``.
        grace_s = 2.0
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._execute_sync,
                    code,
                    session_id=session_id,
                    timeout_s=timeout_s,
                    limits=limits,
                    network=network,
                    input_files=input_files,
                ),
                timeout=timeout_s + grace_s,
            )
        except TimeoutError as exc:
            # Force-kill the substrate sandbox if stateful so a runaway
            # session doesn't continue consuming substrate-seconds after
            # we've abandoned it.
            if session_id is not None:
                sandbox = self._sessions.pop(session_id, None)
                if sandbox is not None:
                    await asyncio.to_thread(self._safe_kill, sandbox)
            msg = (
                f"code_execution exceeded wall_clock_s={timeout_s} "
                f"(grace +{grace_s}s) — substrate kill forced"
            )
            raise ExecutionTimeoutError(
                msg,
                context={
                    "wall_clock_s": str(timeout_s),
                    "session_id": session_id or "",
                },
            ) from exc

    async def create_session(
        self,
        session_id: str,
        *,
        limits: ResourceLimits,
        network: NetworkPolicy,
    ) -> None:
        """Create a long-lived E2B sandbox tracked by ``session_id``.

        Idempotent: an existing session is reused. Per D-12-7 every session
        is destroyed by :meth:`aclose`; the T09 pool adds reaping.
        """
        if self._closed:
            msg = "HostedSandbox is closed"
            raise SandboxUnavailableError(msg, context={"reason": "closed"})
        if session_id in self._sessions:
            return
        await asyncio.to_thread(
            self._create_session_sync,
            session_id=session_id,
            limits=limits,
            network=network,
        )

    async def destroy_session(self, session_id: str) -> None:
        """Kill the E2B sandbox for ``session_id``; idempotent."""
        sandbox = self._sessions.pop(session_id, None)
        if sandbox is None:
            return
        await asyncio.to_thread(self._safe_kill, sandbox)

    async def aclose(self) -> None:
        """Kill every live session; mark closed (D-12-7 explicit Protocol)."""
        if self._closed:
            return
        self._closed = True
        session_ids = list(self._sessions.keys())
        for session_id in session_ids:
            await self.destroy_session(session_id)

    async def copy_produced_file_to(
        self,
        session_id: str,
        ref: str,
        target_path: Path,
    ) -> None:
        """Copy a produced file from the E2B sandbox to a host target path.

        D-12-X-read-produced-file hosted impl: ``sandbox.files.read`` then
        ``target_path.write_bytes`` — memory == file size (the E2B SDK
        doesn't stream), bounded by :data:`PRODUCED_FILE_CAP_BYTES`.
        """
        data = await self.read_produced_file_bytes(session_id, ref)
        await asyncio.to_thread(self._write_bytes, target_path, data)

    async def read_produced_file_bytes(
        self,
        session_id: str,
        ref: str,
    ) -> bytes:
        """Read produced file bytes from the E2B sandbox.

        D-12-X-read-produced-file hosted impl: ``sandbox.files.read`` for
        the substrate-native fetch; the size cap is enforced post-read
        (E2B's SDK doesn't expose a server-side size check pre-fetch — we
        rely on the substrate-side resource caps to prevent runaway file
        creation in the first place).
        """
        sandbox = self._sessions.get(session_id)
        if sandbox is None:
            msg = f"session {session_id!r} not found; cannot read produced file"
            raise CodeSandboxError(
                msg,
                context={"reason": "no_session", "session_id": session_id, "ref": ref},
            )
        try:
            data = await asyncio.to_thread(self._read_e2b_bytes, sandbox, ref)
        except CodeSandboxError:
            raise
        except Exception as exc:  # noqa: BLE001 — SDK error hierarchy abstracted
            msg = f"E2B sandbox file read failed for {ref!r}: {type(exc).__name__}: {exc}"
            raise CodeSandboxError(
                msg,
                context={
                    "reason": "e2b_read_failed",
                    "session_id": session_id,
                    "ref": ref,
                },
            ) from exc
        size_bytes = len(data)
        if size_bytes > PRODUCED_FILE_CAP_BYTES:
            msg = (
                f"produced file {ref!r} is {size_bytes} bytes, "
                f"exceeds {PRODUCED_FILE_CAP_BYTES}-byte cap"
            )
            raise ProducedFileSizeError(
                msg,
                context={
                    "ref": ref,
                    "size_bytes": str(size_bytes),
                    "cap_bytes": str(PRODUCED_FILE_CAP_BYTES),
                    "session_id": session_id,
                },
            )
        return data

    @staticmethod
    def _read_e2b_bytes(sandbox: E2BSandbox, ref: str) -> bytes:
        """Sync E2B file read. Substrate path is ``/home/user/<ref>``
        (D-12-9 hosted equivalent of /workspace/out)."""
        result = sandbox.files.read(f"/home/user/{ref}", format="bytes")
        if not isinstance(result, bytes):
            # E2B's typed overloads return str by default; format="bytes" must yield bytes.
            return bytes(result)
        return result

    @staticmethod
    def _write_bytes(target_path: Path, data: bytes) -> None:
        """Sync write with parent-dir mkdir. Called via to_thread."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(data)

    # -- Sync internals (called via asyncio.to_thread) ---------------------

    def _execute_sync(
        self,
        code: str,
        *,
        session_id: str | None,
        timeout_s: float,
        limits: ResourceLimits,
        network: NetworkPolicy,
        input_files: list[SandboxFile],
    ) -> ExecutionResult:
        """Synchronous execute. Runs in :func:`asyncio.to_thread`."""
        if session_id is None:
            sandbox = self._create_sandbox(limits=limits, network=network)
            try:
                return self._run_and_marshal(sandbox, code, timeout_s, input_files)
            finally:
                self._safe_kill(sandbox)
        # Stateful: reuse the session container
        sandbox = self._sessions.get(session_id)
        if sandbox is None:
            msg = f"session {session_id!r} does not exist; call create_session() first"
            raise CodeSandboxError(msg, context={"reason": "no_session", "session_id": session_id})
        return self._run_and_marshal(sandbox, code, timeout_s, input_files)

    def _create_session_sync(
        self,
        *,
        session_id: str,
        limits: ResourceLimits,
        network: NetworkPolicy,
    ) -> None:
        sandbox = self._create_sandbox(limits=limits, network=network)
        self._sessions[session_id] = sandbox

    def _create_sandbox(
        self,
        *,
        limits: ResourceLimits,
        network: NetworkPolicy,
    ) -> E2BSandbox:
        """Create an E2B sandbox with the persona's policy applied."""
        try:
            from e2b_code_interpreter import Sandbox
        except ImportError as exc:
            msg = (
                "e2b-code-interpreter SDK not installed; "
                "install via `pip install 'e2b-code-interpreter>=1.0,<2'`"
            )
            raise SandboxUnavailableError(msg, context={"reason": "sdk_missing"}) from exc

        # T12 F-T12-RES-01 documentation hook (SCP-12-4 — substrate-class limit
        # ceiling): the E2B SDK constructor accepts only ``timeout`` and
        # ``allow_internet_access`` here; ``memory_mb`` / ``cpu_cores`` /
        # ``disk_mb`` from :class:`ResourceLimits` are SILENTLY DROPPED at the
        # SDK boundary — there is no SDK kwarg to pass them, and the substrate
        # enforces its template-class floor (~2 GiB / 1 vCPU / ~1 GiB tmpfs at
        # Hobby tier). The T12 audit confirmed a 900 MB allocation with
        # ``memory_mb=512`` returned ``outcome="ok"``. Emit a one-time-per-
        # construction warning when the user-supplied caps are below the
        # substrate-class floor so production telemetry surfaces the gap;
        # actual enforcement requires a custom E2B template (D-12-12 follow-up).
        if (
            limits.memory_mb < _HOSTED_SUBSTRATE_MEMORY_FLOOR_MB
            or limits.disk_mb < _HOSTED_SUBSTRATE_DISK_FLOOR_MB
        ):
            _logger.warning(
                "hosted substrate enforces template-class floor; "
                "user-supplied caps are advisory only (SCP-12-4)",
                requested_memory_mb=limits.memory_mb,
                substrate_memory_floor_mb=_HOSTED_SUBSTRATE_MEMORY_FLOOR_MB,
                requested_disk_mb=limits.disk_mb,
                substrate_disk_floor_mb=_HOSTED_SUBSTRATE_DISK_FLOOR_MB,
            )

        kwargs: dict[str, Any] = {
            "timeout": int(max(limits.wall_clock_s, self._timeout_default_s)),
        }
        if self._template is not None:
            kwargs["template"] = self._template
        if self._api_key is not None:
            kwargs["api_key"] = self._api_key
        # Network policy — E2B controls egress at the sandbox level. The SDK
        # accepts ``allow_internet_access`` at creation in recent versions;
        # the R-12-5 catalog blocks metadata + private ranges at the substrate
        # regardless of this flag (E2B's own infrastructure deny-list).
        if not network.enabled:
            kwargs["allow_internet_access"] = False
        try:
            return Sandbox(**kwargs)
        except Exception as exc:  # noqa: BLE001 — SDK raises a hierarchy we don't import
            _logger.warning(
                "E2B sandbox creation failed",
                exc_type=type(exc).__name__,
                msg=str(exc)[:200],
            )
            msg = f"E2B sandbox creation failed: {type(exc).__name__}: {exc}"
            raise SandboxUnavailableError(msg, context={"reason": "e2b_create_failed"}) from exc

    def _run_and_marshal(
        self,
        sandbox: E2BSandbox,
        code: str,
        timeout_s: float,
        input_files: list[SandboxFile],
    ) -> ExecutionResult:
        """Run ``code`` on ``sandbox``; marshal the E2B result into :class:`ExecutionResult`."""
        # Seed input files via the SDK's filesystem API. Best-effort: file
        # marshalling is the D-12-11 hosted bridge.
        for f in input_files:
            if f.content_bytes is None:
                continue
            try:
                sandbox.files.write(f"/home/user/{f.path}", f.content_bytes)
            except Exception as exc:  # noqa: BLE001 — defensive; T08 minimal scope
                _logger.warning(
                    "input-file seed failed",
                    path=f.path,
                    exc_type=type(exc).__name__,
                )

        started = time.perf_counter()
        try:
            execution = sandbox.run_code(code, timeout=timeout_s)
        except Exception as exc:  # noqa: BLE001 — SDK error hierarchy abstracted
            duration_ms = (time.perf_counter() - started) * 1000.0
            return ExecutionResult(
                stdout="",
                stderr=f"{type(exc).__name__}: {exc}",
                exit_status=-1,
                outcome="error",
                duration_ms=duration_ms,
            )

        duration_ms = (time.perf_counter() - started) * 1000.0

        # Marshal E2B's Execution into ExecutionResult.
        stdout = "".join(execution.logs.stdout) if execution.logs.stdout else ""
        stderr = "".join(execution.logs.stderr) if execution.logs.stderr else ""
        outcome: ExecutionOutcome
        if execution.error is not None:
            stderr = stderr + f"\n{execution.error.name}: {execution.error.value}"
            outcome = "error"
            exit_status = 1
        else:
            outcome = "ok"
            exit_status = 0

        return ExecutionResult(
            stdout=stdout,
            stderr=stderr,
            exit_status=exit_status,
            outcome=outcome,
            duration_ms=duration_ms,
        )

    @staticmethod
    def _safe_kill(sandbox: E2BSandbox) -> None:
        """Kill a sandbox; swallow errors (idempotent / already-killed)."""
        try:
            sandbox.kill()
        except Exception as exc:  # noqa: BLE001 — defensive
            _logger.debug(
                "sandbox kill failed (already gone?)",
                exc_type=type(exc).__name__,
            )
