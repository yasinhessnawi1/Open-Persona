"""Integration test: within-runtime origination end-to-end on Postgres (Spec C0, T7).

Proves criterion 7 against the REAL wiring (not fakes): a completed agentic run,
driven through the real :class:`RunRegistry`, fires
:class:`WithinRuntimeOriginator` at its conclusion → ``Originator`` → ``DeliveryRouter``
→ ``WebAppDeliverer`` → :class:`RunStreamSink` pushes a ``persona_originated`` event
onto the run's OWN real event queue (the queue ``/events`` SSE drains) — inline on
the open stream — AND the recorder persists the message (a first-class ``assistant``
``originated`` row in a started conversation, D-C0-3) + an episodic chunk (criterion 2).

Requires ``DATABASE_URL`` → a disposable ``*_test`` database; skipped otherwise.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from persona.stores.postgres import PostgresBackend
from persona_api.background.run_worker import RunRegistry
from persona_api.config import Edition
from persona_api.db.models import messages as messages_t
from persona_api.middleware.rls_context import current_user_id, make_rls_engine
from persona_api.services.within_runtime_origination import (
    ORIGINATED_EVENT_TYPE,
    WithinRuntimeOriginator,
)
from persona_runtime.agentic.events import RunEvent
from persona_runtime.agentic.run import Run, RunStatus
from sqlalchemy import func, select, text

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator
    from pathlib import Path

    from persona_runtime.agentic.events import RunEvent as _Ev
    from persona_runtime.agentic.run import CancelToken
    from sqlalchemy import Engine
    from tests.conftest import HashEmbedder384

pytestmark = pytest.mark.integration

_YAML = """\
schema_version: "1.0"
identity:
  name: Astrid
  role: assistant
  background: |
    x
  language_default: en
  constraints: []
"""


class _ScriptedLoop:
    """Emits started → completed and returns a COMPLETED Run with output."""

    def __init__(self, persona_id: str) -> None:
        self._persona_id = persona_id

    async def run(
        self,
        task: str,
        on_event: Callable[[_Ev], Awaitable[None]] | None = None,
        user_respond: Callable[[str], Awaitable[str]] | None = None,  # noqa: ARG002
        cancel_token: CancelToken | None = None,  # noqa: ARG002
    ) -> Run:
        started = datetime.now(UTC)
        if on_event is not None:
            await on_event(RunEvent.started(task))
            await on_event(RunEvent.completed(1, "I've finished the task you asked for."))
        return Run(
            id="",
            persona_id=self._persona_id,
            task=task,
            status=RunStatus.COMPLETED,
            output="I've finished the task you asked for.",
            started_at=started,
            finished_at=datetime.now(UTC),
        )


def _seed(superuser: Engine) -> None:
    with superuser.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email) VALUES ('user_a','a@example.com')"))
        conn.execute(
            text("INSERT INTO personas (id, owner_id, yaml) VALUES ('pa','user_a',:y)"),
            {"y": _YAML},
        )


@pytest.fixture
def wired(
    migrated_engine: Engine, database_url: str, embedder: HashEmbedder384, tmp_path: Path
) -> Iterator[tuple[RunRegistry, Engine]]:
    _seed(migrated_engine)
    rls_engine = make_rls_engine(database_url)
    gateway = WithinRuntimeOriginator(
        rls_engine=rls_engine,
        memory_backend=PostgresBackend(engine=rls_engine, embedder=embedder),
        edition=Edition.cloud,
        audit_root=tmp_path / "audit",
    )
    registry = RunRegistry(rls_engine, origination=gateway)
    try:
        yield registry, migrated_engine
    finally:
        rls_engine.dispose()
        current_user_id.set(None)


async def _drain(registry: RunRegistry, run_id: str) -> list[RunEvent]:
    handle = registry.get(run_id)
    assert handle is not None
    assert handle.task is not None
    await handle.task
    events: list[RunEvent] = []
    while not handle.events.empty():
        ev = handle.events.get_nowait()
        if ev is None:
            break
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_run_conclusion_originates_inline_and_persists(
    wired: tuple[RunRegistry, Engine],
) -> None:
    registry, superuser = wired
    registry.start(
        run_id="run_1", owner_id="user_a", loop=_ScriptedLoop("pa"), task_text="do a thing"
    )
    events = await _drain(registry, "run_1")

    # (1) the originated message appears INLINE on the run's own open stream.
    originated = [e for e in events if e.type == ORIGINATED_EVENT_TYPE]
    assert len(originated) == 1, [e.type for e in events]
    assert originated[0].data["content"] == "I've finished the task you asked for."
    assert originated[0].data["persona_name"] == "Astrid"  # identity tag survives (crit 5)
    # it lands AFTER the normal terminal event (inline, before the end sentinel).
    assert [e.type for e in events][-1] == ORIGINATED_EVENT_TYPE

    # (2) persisted as a first-class originated assistant row in a started conversation.
    with superuser.begin() as conn:
        rows = [dict(r) for r in conn.execute(select(messages_t)).mappings().all()]
        episodic = conn.execute(
            text("SELECT count(*) FROM memory_chunks WHERE persona_id='pa' AND kind='episodic'")
        ).scalar_one()
        conv_count = conn.execute(
            select(func.count()).select_from(text("conversations"))
        ).scalar_one()

    assert len(rows) == 1
    assert rows[0]["role"] == "assistant"
    assert rows[0]["originated"] is True
    assert rows[0]["content"] == "I've finished the task you asked for."
    assert conv_count == 1  # origination started a conversation (D-C0-3)
    assert episodic == 1  # the persona remembers reaching out (criterion 2)


@pytest.mark.asyncio
async def test_no_origination_when_gateway_absent(
    migrated_engine: Engine, database_url: str
) -> None:
    """No gateway (default) → byte-unchanged run behaviour, no originated event (crit 10)."""
    _seed(migrated_engine)
    rls_engine = make_rls_engine(database_url)
    try:
        registry = RunRegistry(rls_engine)  # no origination injected
        registry.start(run_id="run_2", owner_id="user_a", loop=_ScriptedLoop("pa"), task_text="t")
        events = await _drain(registry, "run_2")
        assert not any(e.type == ORIGINATED_EVENT_TYPE for e in events)
        with migrated_engine.begin() as conn:
            msg_count = conn.execute(select(func.count()).select_from(messages_t)).scalar_one()
        assert msg_count == 0
    finally:
        rls_engine.dispose()
        current_user_id.set(None)
