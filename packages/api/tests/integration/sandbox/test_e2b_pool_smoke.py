"""Live E2B Hobby smoke test for ``SandboxPool`` (spec 12 T09d).

Verifies the pool composes correctly against the real ``HostedSandbox``
(E2B Firecracker microVM substrate per D-12-12). Distinct from the unit
tests in ``test_api_sandbox_pool.py`` which use a Protocol-conforming fake:
this suite calls the SDK directly to confirm the lifecycle scaffolding
works end-to-end on the substrate that produced the D-12-12 lock-gates.

**Cost discipline (CLAUDE.md "executing actions with care"):**

  - Hard cost ceiling: **$0.01** per full suite run.
  - E2B Hobby billing is ~$0.0000185/sec/CPU. Each test below holds at most
    1–2 sandboxes for ≤5 seconds → ~$0.0002 per test → ~$0.001 per suite.
    That's 10× headroom against the ceiling.
  - The cost ceiling exists so a regression that accidentally holds a
    sandbox open across the suite stays bounded; if a test fails with a
    leaked session, the pool's ``aclose`` finalizer (called in the
    ``async with`` exit) still kills the substrate sandbox.

**Skip conditions:**

  - ``E2B_API_KEY`` not set in environment → suite is skipped via the
    ``external`` marker + the per-test ``e2b_required`` skip.
  - SDK not installed → ``SandboxUnavailableError`` from ``HostedSandbox``
    construction → suite skipped with a clear message.

Run with::

    uv run pytest -m external packages/api/tests/integration/sandbox/test_e2b_pool_smoke.py -v
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest
from persona.sandbox.errors import SandboxQuotaExceededError
from persona_api.sandbox import HostedSandbox, SandboxPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# NOTE: we deliberately do NOT call ``dotenv.load_dotenv()`` at module load —
# pytest collects this module even when ``-m external`` excludes it, and a
# module-level ``load_dotenv()`` pollutes every other test's environment with
# values like ``DATABASE_URL`` from ``.env`` (caught during T09d verification:
# breaks the unit suite's app-factory tests that expect DATABASE_URL unset).
# Users running this suite should set ``E2B_API_KEY`` in their shell or use
# ``dotenv -- pytest -m external …`` to load it inline.

pytestmark = [pytest.mark.external, pytest.mark.integration]


def _e2b_key_set() -> bool:
    return bool(os.environ.get("E2B_API_KEY", "").strip())


def _e2b_sdk_installed() -> bool:
    """The SDK is a ``persona-api[hosted]`` extra — graceful skip when absent.

    Without the extra, ``HostedSandbox._create_sandbox`` raises
    ``SandboxUnavailableError(reason=sdk_missing)`` — the test would surface
    as a confusing failure rather than a skip. Mirror the conditional-skip
    pattern used by ``persona-core[sandbox]`` Docker tests.
    """
    try:
        import importlib.util

        return importlib.util.find_spec("e2b_code_interpreter") is not None
    except ImportError:
        return False


e2b_required = pytest.mark.skipif(
    not _e2b_key_set() or not _e2b_sdk_installed(),
    reason=(
        "Live E2B smoke requires both E2B_API_KEY set AND the "
        "persona-api[hosted] extra installed (e2b-code-interpreter SDK)"
    ),
)


@asynccontextmanager
async def _pool(
    *,
    max_per_user: int = 2,
    idle_timeout_s: float = 60.0,
    reap_interval_s: float = 30.0,
) -> AsyncIterator[SandboxPool]:
    """Construct + start a real-E2B-backed pool; aclose on exit."""
    sandbox = HostedSandbox(timeout_default_s=30)
    pool = SandboxPool(
        sandbox=sandbox,
        max_per_user=max_per_user,
        idle_timeout_s=idle_timeout_s,
        reap_interval_s=reap_interval_s,
    )
    await pool.start()
    try:
        yield pool
    finally:
        await pool.aclose()


@e2b_required
@pytest.mark.asyncio
async def test_pool_acquires_real_session_and_executes_code() -> None:
    """End-to-end smoke: acquire → execute → release against live E2B."""
    async with _pool() as pool:
        handle = await pool.acquire(user_id="smoke-user-1", conversation_id="smoke-conv-1")
        assert handle.session_id == "smoke-user-1:smoke-conv-1"

        # Execute a trivial code snippet through the underlying sandbox via the
        # pool's session_id — verifies the pool's session bookkeeping aligns
        # with the substrate's session identity.
        result = await pool._sandbox.execute(  # noqa: SLF001 — smoke verifies composition
            "print('persona pool smoke')",
            session_id=handle.session_id,
        )
        assert result.outcome == "ok"
        assert "persona pool smoke" in result.stdout

        await pool.release(handle)


@e2b_required
@pytest.mark.asyncio
async def test_per_user_cap_enforced_against_real_substrate() -> None:
    """Per-user cap rejects the third acquire even with the substrate present.

    Verifies the pool's cap check runs ahead of any substrate call — no third
    sandbox is created at E2B (cost containment matters here: a leak past the
    cap would burn the cost ceiling).
    """
    async with _pool(max_per_user=2) as pool:
        h1 = await pool.acquire(user_id="cap-user", conversation_id="c1")
        h2 = await pool.acquire(user_id="cap-user", conversation_id="c2")
        with pytest.raises(SandboxQuotaExceededError) as excinfo:
            await pool.acquire(user_id="cap-user", conversation_id="c3")
        ctx = excinfo.value.context
        assert ctx == {"user_id": "cap-user", "current_count": "2", "cap": "2"}
        # Release both so the pool's aclose has nothing to drain on exit
        # (single-tenant test boundary).
        await pool.release(h1)
        await pool.release(h2)


@e2b_required
@pytest.mark.asyncio
async def test_reaper_task_lifecycle_against_real_substrate() -> None:
    """The pool-owned reaper task starts and cancels cleanly with a real sandbox.

    Doesn't wait for the reaper to actually fire (too long for a smoke test);
    instead verifies the task lifecycle composes correctly when the pool owns
    a real ``HostedSandbox``. The reaper's actual reap behaviour is exercised
    deterministically by the unit suite (fake clock + fake sandbox).
    """
    async with _pool(reap_interval_s=60.0) as pool:
        # Reaper task was spawned by _pool's start() — assert it's alive.
        reaper = pool._reaper_task  # noqa: SLF001 — smoke verifies composition
        assert reaper is not None
        assert not reaper.done()
        # aclose() in the context-exit cancels the task — assert via post-exit
        # by capturing the reference; the next line is post-exit (the with-
        # block hasn't unwound yet here).
    # After context exit, the reaper should be done (cancelled by aclose).
    assert reaper.done()
