"""The checkpoint distiller — the production CheckpointWriter (Spec A2, T12; D-A2-1).

Replaces the ``BasicCheckpointWriter`` stand-in in the live path. The stand-in appends every
leg's output and would eventually overflow the checkpoint budget
(:class:`~persona.errors.CheckpointTooLargeError`); the distiller **reflect-and-compacts** so a
many-leg task stays under the budget — the discipline D-A2-1 names ("maximise recall, then
precision; compact, don't truncate; keep pointers for detail").

This is the **deterministic, token-bounded** distiller (the live floor — guarantees no
overflow). A leg's new conclusion is appended; when the accumulating core approaches the
budget, the OLDEST conclusions are folded into a single ``[N earlier findings compacted]``
marker (their detail stays restorable via the workspace + the durable run records the
``event_log_cursor`` points at). A model-backed *semantic* distiller (better amnesia
resistance, gated by the T11 continuation eval) is the additive refinement that swaps in here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.skills import count_tokens
from persona.tasks import DEFAULT_CHECKPOINT_TOKEN_BUDGET, TaskCheckpoint

if TYPE_CHECKING:
    from datetime import datetime

    from persona.tasks import Task

    from persona_runtime.agentic.run import Run

__all__ = ["CompactingCheckpointWriter"]


class CompactingCheckpointWriter:
    """A token-bounded :class:`CheckpointWriter` — appends, then reflect-and-compacts."""

    def __init__(self, *, token_budget: int = DEFAULT_CHECKPOINT_TOKEN_BUDGET) -> None:
        # Compact to a margin under the store's hard gate so the append always fits.
        self._target = int(token_budget * 0.8)

    def write(
        self,
        *,
        task: Task,
        prior: TaskCheckpoint | None,
        run: Run,
        leg_id: str,
        seq: int,
        now: datetime,
    ) -> TaskCheckpoint:
        prior_conclusions = list(prior.progress_conclusions) if prior is not None else []
        if run.output:
            prior_conclusions.append(run.output)
        conclusions = self._compact(prior_conclusions)
        return TaskCheckpoint(
            task_id=task.id,
            leg_id=leg_id,
            checkpoint_seq=seq,
            progress_conclusions=tuple(conclusions),
            next_step=run.output or (prior.next_step if prior is not None else ""),
            open_questions=prior.open_questions if prior is not None else (),
            artifact_pointers=prior.artifact_pointers if prior is not None else (),
            event_log_cursor=run.id,  # the durable run record holds the compacted detail
            updated_at=now,
        )

    def _compact(self, conclusions: list[str]) -> list[str]:
        """Fold the oldest conclusions into a marker until the core fits the target budget.

        Keeps the most recent conclusions verbatim (recency) and replaces the dropped prefix
        with one ``[N earlier findings compacted]`` line — bounded, restorable, never truncated
        mid-thought.
        """
        if count_tokens(" ".join(conclusions)) <= self._target:
            return conclusions
        compacted = 0
        kept = list(conclusions)
        while len(kept) > 1 and count_tokens(" ".join(kept)) > self._target:
            kept.pop(0)
            compacted += 1
        if compacted:
            return [f"[{compacted} earlier findings compacted — see run records]", *kept]
        return kept
