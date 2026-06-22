"""Unit tests for worker loop knobs (no DB) (Spec A0, T5)."""

# ruff: noqa: SLF001 — exercising private loop internals directly.
from __future__ import annotations

from unittest.mock import MagicMock

from persona.jobs import JobRegistry
from persona_api.jobs import Worker


def _worker(**kw: object) -> Worker:
    return Worker(
        dispatch_engine=MagicMock(),
        rls_engine=MagicMock(),
        registry=JobRegistry(),
        worker_id="w-test",
        **kw,  # type: ignore[arg-type]
    )


def test_next_poll_delay_within_jitter_band() -> None:
    worker = _worker(poll_interval_seconds=1.0, poll_jitter_seconds=0.5)
    delays = [worker._next_poll_delay() for _ in range(200)]
    assert all(1.0 <= d <= 1.5 for d in delays), (
        "poll delay must stay in [interval, interval+jitter]"
    )
    assert len(set(delays)) > 1, "the poll delay must be jittered, not constant"


def test_zero_jitter_gives_constant_interval() -> None:
    worker = _worker(poll_interval_seconds=0.5, poll_jitter_seconds=0.0)
    assert worker._next_poll_delay() == 0.5


def test_request_drain_is_idempotent() -> None:
    worker = _worker()
    assert not worker._draining.is_set()
    worker.request_drain()
    worker.request_drain()  # second call absorbed
    assert worker._draining.is_set()


def test_worker_id_is_unique_per_process() -> None:
    from persona_api.jobs import make_worker_id

    assert make_worker_id() != make_worker_id(), "worker ids must be unique (PID reuse safety)"
