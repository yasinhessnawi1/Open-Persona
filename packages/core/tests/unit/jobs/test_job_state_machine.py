"""Unit tests for the job state machine (Spec A0, T1)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.errors import JobStateError
from persona.jobs import (
    TERMINAL_STATES,
    Job,
    JobPayload,
    JobState,
    can_transition,
    is_terminal,
    validate_transition,
)

_LEGAL_EDGES = [
    (JobState.QUEUED, JobState.CLAIMED),
    (JobState.CLAIMED, JobState.RUNNING),
    (JobState.CLAIMED, JobState.QUEUED),  # lease-expiry reclaim before running
    (JobState.RUNNING, JobState.SUCCEEDED),
    (JobState.RUNNING, JobState.FAILED),  # permanent failure
    (JobState.RUNNING, JobState.DEAD),  # retry exhausted
    (JobState.RUNNING, JobState.QUEUED),  # retry or lease-expiry reclaim
]

_ILLEGAL_EDGES = [
    (JobState.QUEUED, JobState.RUNNING),  # must be claimed first
    (JobState.QUEUED, JobState.SUCCEEDED),
    (JobState.CLAIMED, JobState.SUCCEEDED),  # must run first
    (JobState.SUCCEEDED, JobState.RUNNING),  # terminal cannot resurrect
    (JobState.FAILED, JobState.QUEUED),
    (JobState.DEAD, JobState.QUEUED),
    (JobState.QUEUED, JobState.QUEUED),  # no self-loop
]


@pytest.mark.parametrize(("frm", "to"), _LEGAL_EDGES)
def test_legal_transitions_are_allowed(frm: JobState, to: JobState) -> None:
    assert can_transition(frm, to) is True
    validate_transition(frm, to)  # does not raise


@pytest.mark.parametrize(("frm", "to"), _ILLEGAL_EDGES)
def test_illegal_transitions_are_rejected(frm: JobState, to: JobState) -> None:
    assert can_transition(frm, to) is False
    with pytest.raises(JobStateError) as excinfo:
        validate_transition(frm, to)
    # The error names the rejected edge for the log line.
    assert excinfo.value.context == {"from": frm.value, "to": to.value}


@pytest.mark.parametrize("state", [JobState.SUCCEEDED, JobState.FAILED, JobState.DEAD])
def test_terminal_states_admit_no_transitions(state: JobState) -> None:
    assert is_terminal(state) is True
    assert all(not can_transition(state, other) for other in JobState)


@pytest.mark.parametrize("state", [JobState.QUEUED, JobState.CLAIMED, JobState.RUNNING])
def test_non_terminal_states_are_not_terminal(state: JobState) -> None:
    assert is_terminal(state) is False


def test_terminal_states_constant_matches_is_terminal() -> None:
    assert frozenset(s for s in JobState if is_terminal(s)) == TERMINAL_STATES


def _job(state: JobState = JobState.QUEUED, *, attempt: int = 0) -> Job:
    now = datetime.now(UTC)
    return Job(
        id="job-1",
        type="avatar_generation",
        owner_id="user-1",
        payload=JobPayload(),
        idempotency_key="avatar:persona-1:create",
        state=state,
        attempt=attempt,
        scheduled_at=now,
        created_at=now,
    )


def test_job_transition_to_returns_new_frozen_job_in_new_state() -> None:
    job = _job(JobState.CLAIMED)
    running = job.transition_to(JobState.RUNNING)
    assert running.state is JobState.RUNNING
    assert job.state is JobState.CLAIMED  # original untouched (frozen)
    assert running.id == job.id


def test_job_transition_to_applies_extra_updates() -> None:
    job = _job(JobState.RUNNING, attempt=1)
    requeued = job.transition_to(JobState.QUEUED, attempt=2, last_error="boom")
    assert requeued.state is JobState.QUEUED
    assert requeued.attempt == 2
    assert requeued.last_error == "boom"


def test_job_transition_to_rejects_illegal_edge() -> None:
    job = _job(JobState.SUCCEEDED)
    with pytest.raises(JobStateError):
        job.transition_to(JobState.RUNNING)
