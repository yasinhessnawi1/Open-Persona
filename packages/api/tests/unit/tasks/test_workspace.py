"""Task workspace lifecycle (Spec A2, T10; D-A2-6).

The task workspace is a Spec-12 session keyed by task_id; a terminal task's workspace is
destroyed (idempotently) so scratch space doesn't leak.
"""

from __future__ import annotations

import pytest
from persona_api.tasks import (
    TASK_WORKSPACE_RETENTION_DAYS_DEFAULT,
    cleanup_task_workspace,
    task_workspace_session_id,
)


class _FakeSandbox:
    def __init__(self) -> None:
        self.destroyed: list[str] = []

    async def destroy_session(self, session_id: str) -> None:
        self.destroyed.append(session_id)


def test_session_id_is_stable_per_task() -> None:
    assert task_workspace_session_id("t1") == "task-t1"


def test_retention_default() -> None:
    assert TASK_WORKSPACE_RETENTION_DAYS_DEFAULT == 7


@pytest.mark.asyncio
async def test_cleanup_destroys_the_task_session() -> None:
    sandbox = _FakeSandbox()
    await cleanup_task_workspace(sandbox, "t1")  # type: ignore[arg-type]
    assert sandbox.destroyed == ["task-t1"]


@pytest.mark.asyncio
async def test_cleanup_is_idempotent_destroy() -> None:
    # destroy_session is idempotent (Spec-12); a re-run (e.g. a retried cleanup job) is safe.
    sandbox = _FakeSandbox()
    await cleanup_task_workspace(sandbox, "t1")  # type: ignore[arg-type]
    await cleanup_task_workspace(sandbox, "t1")  # type: ignore[arg-type]
    assert sandbox.destroyed == ["task-t1", "task-t1"]
