"""T3 (spec P1) — runs durability VERIFY-AND-HARDEN: ``runs.steps`` is the floor.

The P1 ruling (D-P1-runs-no-migration): runs already reattach durably via the
existing ``runs.steps`` column — ``RunRegistry`` snapshots the FULL event-log to
it on every event (``_persist_progress``), so a reattach-after-gap reads
everything that happened while away from the persisted row. **No ``run_events``
table is needed.** This test pins that invariant: mid-run, before the task
finishes, the persisted ``runs.steps`` already contains every event emitted so
far; on completion it holds the authoritative final steps.
"""

# ruff: noqa: ARG002 — the scripted loop's signature mirrors AgenticLoop.run.

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from persona.schema.tools import ToolCall
from persona_api.background.run_worker import RunRegistry
from persona_api.db.community import (
    create_community_schema,
    ensure_owner,
    make_community_engine,
)
from persona_api.db.models import personas as personas_t
from persona_api.db.models import runs as runs_t
from persona_runtime.agentic.events import RunEvent
from persona_runtime.agentic.run import Run, RunStatus
from sqlalchemy import insert, select

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from sqlalchemy import Engine

_OWNER = "user_alice"
_PERSONA = "astrid"
_RUN = "run_durable"


@pytest.fixture
def engine(tmp_path: object) -> Iterator[Engine]:
    eng = make_community_engine(tmp_path / "t.db")  # type: ignore[operator]
    create_community_schema(eng)
    ensure_owner(eng, owner_id=_OWNER, email="a@example.com")
    with eng.begin() as conn:
        conn.execute(insert(personas_t).values(id=_PERSONA, owner_id=_OWNER, yaml="name: Astrid"))
        conn.execute(
            insert(runs_t).values(
                id=_RUN, owner_id=_OWNER, persona_id=_PERSONA, task="t", status="running"
            )
        )
    return eng


def _persisted_steps(engine: Engine) -> list[dict[str, object]]:
    with engine.begin() as conn:
        steps = conn.execute(select(runs_t.c.steps).where(runs_t.c.id == _RUN)).scalar_one()
    if isinstance(steps, str):  # sqlite JSON round-trips as text
        steps = json.loads(steps)
    return list(steps) if steps else []


class _GatedLoop:
    """Emits two events, then blocks so the test can inspect the persisted floor
    MID-RUN (the reattach-after-gap moment), then completes."""

    def __init__(self, emitted: asyncio.Event, release: asyncio.Event) -> None:
        self._emitted = emitted
        self._release = release

    async def run(
        self,
        task: str,
        on_event: Callable[[RunEvent], Awaitable[None]] | None = None,
        user_respond: Callable[[str], Awaitable[str]] | None = None,
        cancel_token: object | None = None,
    ) -> Run:
        assert on_event is not None
        await on_event(RunEvent.started(task))
        await on_event(
            RunEvent.tool_calling(0, [ToolCall(name="web_search", args={}, call_id="c1")])
        )
        self._emitted.set()  # two events emitted + persisted
        await self._release.wait()  # hold the run open so the test inspects mid-run
        now = datetime.now(UTC)
        return Run(
            persona_id=_PERSONA,
            task=task,
            status=RunStatus.COMPLETED,
            steps=[],
            output="done",
            error=None,
            started_at=now,
            finished_at=now,
        )


@pytest.mark.asyncio
async def test_runs_steps_is_the_durable_floor_mid_run(engine: Engine) -> None:
    emitted, release = asyncio.Event(), asyncio.Event()
    registry = RunRegistry(engine)
    handle = registry.start(
        run_id=_RUN,
        owner_id=_OWNER,
        loop=_GatedLoop(emitted, release),
        task_text="t",  # type: ignore[arg-type]
    )

    await emitted.wait()
    # MID-RUN, before the task finishes: the persisted row already reflects
    # EVERYTHING emitted so far — a reattach-after-gap reads it all from the DB,
    # not only from the in-memory bus. This is the durable floor (no run_events).
    mid = _persisted_steps(engine)
    types = [e.get("type") for e in mid]
    assert "started" in types
    assert "tool_calling" in types

    release.set()
    assert handle.task is not None
    await handle.task

    with engine.begin() as conn:
        status = conn.execute(select(runs_t.c.status).where(runs_t.c.id == _RUN)).scalar_one()
    assert status == str(RunStatus.COMPLETED)  # authoritative final persisted on completion
