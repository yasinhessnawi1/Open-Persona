"""Unit tests for persona_runtime.agentic.run (T02, D-06-1, D-06-2).

Acceptance #10: the `Run` is serialisable to JSON for persistence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from persona_runtime.agentic.run import CancelToken, Run, RunStatus
from persona_runtime.agentic.step import Step, StepType
from pydantic import ValidationError


def _now() -> datetime:
    return datetime.now(UTC)


class TestRunStatus:
    def test_values(self) -> None:
        assert RunStatus.RUNNING == "running"
        assert RunStatus.COMPLETED == "completed"
        assert RunStatus.CANCELLED == "cancelled"
        assert RunStatus.MAX_STEPS_REACHED == "max_steps_reached"
        assert RunStatus.ERROR == "error"

    def test_terminal_statuses_are_distinct_from_completed(self) -> None:
        # D-06-2: a max-steps / error outcome must never read as completed.
        assert RunStatus.MAX_STEPS_REACHED != RunStatus.COMPLETED
        assert RunStatus.ERROR != RunStatus.COMPLETED


class TestRunConstruction:
    def test_defaults(self) -> None:
        run = Run(
            persona_id="astrid", task="draft a letter", status=RunStatus.RUNNING, started_at=_now()
        )
        assert run.persona_id == "astrid"
        assert run.task == "draft a letter"
        assert run.status is RunStatus.RUNNING
        assert run.steps == []
        assert run.output is None
        assert run.error is None
        assert run.finished_at is None
        assert run.id  # auto UUID

    def test_unique_default_ids(self) -> None:
        a = Run(persona_id="p", task="t", status=RunStatus.RUNNING, started_at=_now())
        b = Run(persona_id="p", task="t", status=RunStatus.RUNNING, started_at=_now())
        assert a.id != b.id

    def test_explicit_id_wins(self) -> None:
        run = Run(
            id="run-42", persona_id="p", task="t", status=RunStatus.RUNNING, started_at=_now()
        )
        assert run.id == "run-42"

    def test_completed_run_with_steps_and_output(self) -> None:
        step = Step(type=StepType.FINAL, content="the letter", tier_used="frontier")
        start = _now()
        run = Run(
            persona_id="astrid",
            task="draft a letter",
            status=RunStatus.COMPLETED,
            steps=[step],
            output="the letter",
            started_at=start,
            finished_at=start + timedelta(seconds=3),
        )
        assert run.status is RunStatus.COMPLETED
        assert run.output == "the letter"
        assert len(run.steps) == 1


class TestRunInvariants:
    def test_frozen(self) -> None:
        run = Run(persona_id="p", task="t", status=RunStatus.RUNNING, started_at=_now())
        with pytest.raises(ValidationError):
            run.status = RunStatus.COMPLETED  # type: ignore[misc]

    def test_naive_started_at_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Run(
                persona_id="p",
                task="t",
                status=RunStatus.RUNNING,
                started_at=datetime(2026, 5, 28, 12, 0, 0),  # noqa: DTZ001
            )

    def test_naive_finished_at_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Run(
                persona_id="p",
                task="t",
                status=RunStatus.COMPLETED,
                started_at=_now(),
                finished_at=datetime(2026, 5, 28, 12, 0, 0),  # noqa: DTZ001
            )

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            Run(persona_id="p", task="t", status=RunStatus.RUNNING, started_at=_now(), bogus=1)  # type: ignore[call-arg]


class TestRunSerialisation:
    def test_json_round_trip(self) -> None:
        # Acceptance #10.
        step = Step(type=StepType.TOOL_CALL, tier_used="mid", tokens=10)
        start = _now()
        run = Run(
            id="run-1",
            persona_id="astrid",
            task="t",
            status=RunStatus.MAX_STEPS_REACHED,
            steps=[step],
            output="best-effort summary",
            started_at=start,
            finished_at=start,
        )
        restored = Run.model_validate_json(run.model_dump_json())
        assert restored == run
        assert restored.status is RunStatus.MAX_STEPS_REACHED


class TestCancelToken:
    def test_starts_uncancelled(self) -> None:
        assert CancelToken().is_cancelled is False

    def test_cancel_flips(self) -> None:
        token = CancelToken()
        token.cancel()
        assert token.is_cancelled is True

    def test_cancel_idempotent(self) -> None:
        token = CancelToken()
        token.cancel()
        token.cancel()
        assert token.is_cancelled is True
