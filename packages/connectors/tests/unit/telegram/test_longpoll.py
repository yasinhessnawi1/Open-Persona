"""run_long_poll — the dev inbound transport (Spec C2 T7, D-C2-1).

Offline: a fake client serves canned batches; the loop must advance the offset
(ack), dispatch every update to on_update, and stop on should_continue.
"""

from __future__ import annotations

import pytest
from persona_connectors.telegram.longpoll import run_long_poll


class _FakeClient:
    """Serves a fixed sequence of getUpdates batches, recording the offsets it saw."""

    def __init__(self, batches: list[list[dict[str, object]]]) -> None:
        self._batches = batches
        self.offsets_seen: list[int | None] = []
        self._i = 0

    async def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,  # noqa: ARG002 — matches the client signature (the loop passes it)
        allowed_updates: list[str],  # noqa: ARG002 — matches the client signature
    ) -> list[dict[str, object]]:
        self.offsets_seen.append(offset)
        if self._i >= len(self._batches):
            return []
        batch = self._batches[self._i]
        self._i += 1
        return batch


@pytest.mark.asyncio
async def test_dispatches_updates_and_advances_offset() -> None:
    """Each update reaches on_update; offset advances to last_update_id + 1 (ack)."""
    client = _FakeClient(
        [
            [
                {"update_id": 10, "message": {"text": "a"}},
                {"update_id": 11, "message": {"text": "b"}},
            ],
            [{"update_id": 12, "message": {"text": "c"}}],
        ]
    )
    seen: list[dict[str, object]] = []
    calls = {"n": 0}

    async def on_update(update: dict[str, object]) -> None:
        seen.append(update)

    def should_continue() -> bool:
        calls["n"] += 1
        return calls["n"] <= 2  # two polling iterations, then stop

    await run_long_poll(client=client, on_update=on_update, should_continue=should_continue)  # type: ignore[arg-type]

    assert [u["update_id"] for u in seen] == [10, 11, 12]
    # First poll starts with no offset; second poll acks past update 11.
    assert client.offsets_seen == [None, 12]


@pytest.mark.asyncio
async def test_stops_immediately_when_should_continue_is_false() -> None:
    client = _FakeClient([[{"update_id": 1}]])
    seen: list[dict[str, object]] = []

    async def on_update(update: dict[str, object]) -> None:
        seen.append(update)

    await run_long_poll(client=client, on_update=on_update, should_continue=lambda: False)  # type: ignore[arg-type]
    assert seen == []
    assert client.offsets_seen == []
