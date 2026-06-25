"""Milestone-granularity episodic distillation (Spec A2, T10; D-A2-4).

Proves the anti-spam discipline: the loop's per-leg episodic write lands in the throwaway
sink (NOT the persona's episodic), and only milestones are promoted — a CONTINUE leg with no
new conclusion records nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime

from persona.schema.chunks import ChunkProvenance, PersonaChunk, WriteSource
from persona.tasks import TaskCheckpoint
from persona_runtime.legs import (
    LegDisposition,
    MilestoneRecorder,
    TaskEpisodicSink,
    TaskMilestone,
    milestone_for,
)

_NOW = datetime(2026, 6, 25, 9, 0, tzinfo=UTC)


def _checkpoint(*conclusions: str) -> TaskCheckpoint:
    return TaskCheckpoint(
        task_id="t1",
        leg_id="leg",
        checkpoint_seq=0,
        progress_conclusions=conclusions,
        next_step="x",
        updated_at=_NOW,
    )


class _FakeEpisodic:
    """A minimal episodic store recording writes."""

    def __init__(self) -> None:
        self.chunks: list[PersonaChunk] = []

    def get_all(self, persona_id: str, *, include_superseded: bool = False) -> list[PersonaChunk]:  # noqa: ARG002
        return list(self.chunks)

    def write(self, persona_id: str, chunks: list[PersonaChunk], **_: object) -> None:  # noqa: ARG002
        self.chunks.extend(chunks)


# --- the sink absorbs the loop's per-leg write (no persona-episodic spam) -----


def test_episodic_sink_buffers_and_does_not_promote() -> None:
    sink = TaskEpisodicSink()
    chunk = PersonaChunk(
        id="c0",
        text="leg output",
        created_at=_NOW,
        provenance=ChunkProvenance(
            source=WriteSource.SYSTEM, logical_id="c0", version=1, written_at=_NOW, written_by="x"
        ),
    )
    sink.write("persona_a", [chunk], source=WriteSource.SYSTEM, written_by="agentic.run")
    # buffered (task-scoped), available to the loop's index computation, but never the persona's.
    assert len(sink.get_all("persona_a", include_superseded=True)) == 1
    assert sink.query("persona_a", "anything") == []


# --- the milestone gate is restrained ----------------------------------------


def test_first_leg_is_task_started() -> None:
    m = milestone_for(
        is_first_leg=True,
        prior_checkpoint=None,
        new_checkpoint=_checkpoint("c1"),
        disposition=LegDisposition.CONTINUE,
    )
    assert m == TaskMilestone.TASK_STARTED


def test_new_conclusion_is_major_progress() -> None:
    m = milestone_for(
        is_first_leg=False,
        prior_checkpoint=_checkpoint("c1"),
        new_checkpoint=_checkpoint("c1", "c2"),
        disposition=LegDisposition.CONTINUE,
    )
    assert m == TaskMilestone.MAJOR_PROGRESS


def test_continue_leg_with_no_new_conclusion_is_not_a_milestone() -> None:
    # The anti-spam case: a leg that advanced but established nothing new → no episodic entry.
    m = milestone_for(
        is_first_leg=False,
        prior_checkpoint=_checkpoint("c1"),
        new_checkpoint=_checkpoint("c1"),
        disposition=LegDisposition.CONTINUE,
    )
    assert m is None


def test_completed_and_failed_are_milestones() -> None:
    assert (
        milestone_for(
            is_first_leg=False,
            prior_checkpoint=_checkpoint("c1"),
            new_checkpoint=_checkpoint("c1"),
            disposition=LegDisposition.COMPLETED,
        )
        == TaskMilestone.COMPLETED
    )
    assert (
        milestone_for(
            is_first_leg=False,
            prior_checkpoint=_checkpoint("c1"),
            new_checkpoint=_checkpoint("c1"),
            disposition=LegDisposition.FAILED,
        )
        == TaskMilestone.FAILED
    )


# --- the recorder writes ONE chunk per milestone -----------------------------


def test_recorder_writes_one_tagged_chunk() -> None:
    store = _FakeEpisodic()
    MilestoneRecorder(store).record(
        "persona_a", TaskMilestone.MAJOR_PROGRESS, "found 1620kr", task_id="t1"
    )
    assert len(store.chunks) == 1
    chunk = store.chunks[0]
    assert chunk.text == "found 1620kr"
    assert chunk.metadata["source"] == "task_milestone"
    assert chunk.metadata["milestone"] == "major_progress"
    assert chunk.metadata["task_id"] == "t1"
