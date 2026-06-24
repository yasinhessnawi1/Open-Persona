"""The A0 synthesis job handler (Spec K2, T8b). Unit-tested with fakes — no DB, no model.

Proves the durable-job glue: marker-CAS idempotency (a re-run past the high-water-mark
does nothing), the windowing handoff (K2-D-5), the meter call (Spec-08 visibility),
and the advance only after a successful synthesise.
"""

# ruff: noqa: ARG002 — fakes ignore some args by design.

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from persona.extraction import ExtractionInput
from persona_api.jobs.handlers.synthesis import (
    SYNTHESIS_JOB_TYPE,
    InteractionData,
    SynthesisHandler,
    SynthesisJobPayload,
    synthesis_idempotency_key,
)


class _FakeContext:
    def __init__(self, owner_id: str = "u1") -> None:
        self._owner_id = owner_id
        self.meters: list[dict[str, Any]] = []

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def job_id(self) -> str:
        return "job-1"

    @contextlib.contextmanager
    def connection(self) -> Iterator[object]:
        yield object()

    def meter(
        self, *, amount_micros: int, kind: str, detail: Mapping[str, str] | None = None
    ) -> None:
        self.meters.append({"amount_micros": amount_micros, "kind": kind, "detail": detail})


class _FakeRepo:
    def __init__(self, data: InteractionData | None) -> None:
        self._data = data
        self.advanced: list[int] = []

    def read(
        self, conn: object, *, owner_id: str, payload: SynthesisJobPayload
    ) -> InteractionData | None:
        return self._data

    def advance(
        self, conn: object, *, owner_id: str, payload: SynthesisJobPayload, high_water_mark: int
    ) -> None:
        self.advanced.append(high_water_mark)


class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[ExtractionInput] = []

    async def synthesise(self, owner_id: str, interaction: ExtractionInput) -> list[object]:
        self.calls.append(interaction)
        return [object(), object()]  # two merge outcomes


def _payload(hwm: int = 3) -> SynthesisJobPayload:
    return SynthesisJobPayload(
        interaction_kind="conversation",
        interaction_id="conv-1",
        persona_id="p1",
        high_water_mark=hwm,
    )


def _data(up_to: int) -> InteractionData:
    return InteractionData(
        synthesised_up_to=up_to,
        messages=(("user", "I'm a nurse"), ("assistant", "ok"), ("user", "I went vegetarian")),
        compacted_summary="",
    )


def test_idempotency_key_is_kind_interaction_high_water_mark() -> None:
    assert synthesis_idempotency_key(_payload(3)) == "synthesis:conversation:conv-1:3"
    # a continued conversation (new count) re-keys → a new job, not a dedup no-op
    assert synthesis_idempotency_key(_payload(5)) != synthesis_idempotency_key(_payload(3))


def test_job_type_constant() -> None:
    assert SYNTHESIS_JOB_TYPE == "synthesis"


@pytest.mark.asyncio
async def test_happy_path_synthesises_meters_then_advances() -> None:
    ctx, repo, runner = _FakeContext(), _FakeRepo(_data(0)), _FakeRunner()
    await SynthesisHandler(runner=runner, repository=repo).handle(_payload(), ctx)  # type: ignore[arg-type]
    assert len(runner.calls) == 1  # synthesis ran over the windowed tail
    assert "I went vegetarian" in runner.calls[0].content
    assert len(ctx.meters) == 1  # Spec-08 visibility
    assert ctx.meters[0]["kind"] == "model"
    assert repo.advanced == [3]  # marker advanced to the new high-water-mark


@pytest.mark.asyncio
async def test_already_synthesised_is_a_noop_no_synthesise_no_advance() -> None:
    # Marker at the message count → nothing new → idempotent skip (re-run = no dup).
    ctx, repo, runner = _FakeContext(), _FakeRepo(_data(3)), _FakeRunner()
    await SynthesisHandler(runner=runner, repository=repo).handle(_payload(), ctx)  # type: ignore[arg-type]
    assert runner.calls == []
    assert repo.advanced == []
    assert ctx.meters == []


@pytest.mark.asyncio
async def test_missing_interaction_is_a_noop() -> None:
    ctx, repo, runner = _FakeContext(), _FakeRepo(None), _FakeRunner()
    await SynthesisHandler(runner=runner, repository=repo).handle(_payload(), ctx)  # type: ignore[arg-type]
    assert runner.calls == []
    assert repo.advanced == []
