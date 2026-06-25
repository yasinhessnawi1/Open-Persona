"""The production checkpoint distiller stays under budget (Spec A2, T12; D-A2-1).

The reason the live path must NOT use ``BasicCheckpointWriter``: it appends every leg's
output and overflows. The ``CompactingCheckpointWriter`` reflect-and-compacts so a many-leg
task never trips ``CheckpointTooLargeError``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from persona.tasks import (
    Contract,
    Task,
    TaskCheckpoint,
    checkpoint_token_count,
    enforce_checkpoint_budget,
)
from persona_runtime.agentic.run import Run, RunStatus
from persona_runtime.legs import CompactingCheckpointWriter

_NOW = datetime(2026, 6, 25, 9, 0, tzinfo=UTC)


def _task() -> Task:
    return Task(
        id="t1",
        owner_id="u",
        persona_id="p",
        contract=Contract(goal="g"),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _run(output: str) -> Run:
    return Run(
        persona_id="p",
        task="x",
        status=RunStatus.COMPLETED,
        steps=[],
        output=output,
        started_at=_NOW,
        finished_at=_NOW,
    )


def test_distiller_stays_under_budget_over_many_legs() -> None:
    writer = CompactingCheckpointWriter(token_budget=200)
    prior: TaskCheckpoint | None = None
    for i in range(30):
        cp = writer.write(
            task=_task(),
            prior=prior,
            run=_run(f"leg {i} established a fairly wordy conclusion about the work so far " * 3),
            leg_id=f"t1:leg:{i}",
            seq=i,
            now=_NOW,
        )
        # Never overflows the store's hard gate — the whole point.
        enforce_checkpoint_budget(cp, token_budget=200)
        prior = cp
    assert checkpoint_token_count(prior) <= 200  # type: ignore[arg-type]
    # The oldest findings were compacted into a marker, not lost (restorable via run records).
    assert any("earlier findings compacted" in c for c in prior.progress_conclusions)  # type: ignore[union-attr]
    assert prior.event_log_cursor is not None  # type: ignore[union-attr]


def test_basic_writer_would_overflow_proving_the_distiller_is_needed() -> None:
    # Contrast: the stand-in keeps every output, so a long task trips the budget. This is why
    # the live path MUST wire the distiller (the T12 no-unwired-seam audit).
    from persona.errors import CheckpointTooLargeError
    from persona_runtime.legs import BasicCheckpointWriter

    writer = BasicCheckpointWriter()
    prior: TaskCheckpoint | None = None
    overflowed = False
    for i in range(40):
        prior = writer.write(
            task=_task(),
            prior=prior,
            run=_run("a wordy conclusion " * 30),
            leg_id=f"t1:leg:{i}",
            seq=i,
            now=_NOW,
        )
        try:
            enforce_checkpoint_budget(prior, token_budget=200)
        except CheckpointTooLargeError:
            overflowed = True
            break
    assert overflowed, "BasicCheckpointWriter should overflow — that's why the distiller exists"
