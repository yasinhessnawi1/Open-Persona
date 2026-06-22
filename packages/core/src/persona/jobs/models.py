"""The durable job model + state machine + lifecycle policies (Spec A0, T1).

persona-core owns the job contract: frozen-Pydantic payloads, the
``queued → claimed → running → succeeded | failed | dead`` state machine, and
the lease/retry value objects the worker reasons about. No DB, no I/O — the
durable Postgres queue (persona-api) and the worker service compose these.
The worker is a different *place* to run, never a different *thing* that runs;
these contracts are what keep the two paths honest.

See ``docs/specs/phase3/spec_A0/decisions.md`` (D-A0-1 lease/heartbeat,
D-A0-2 registry/state machine) and ``docs/research/spec_A0.md`` §1–§3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializeAsAny,
    field_validator,
    model_validator,
)

from persona.errors import JobStateError

__all__ = [
    "LONG_LEASE",
    "MEDIUM_LEASE",
    "SHORT_LEASE",
    "TERMINAL_STATES",
    "Job",
    "JobPayload",
    "JobState",
    "LeasePolicy",
    "RetryPolicy",
    "can_transition",
    "is_terminal",
    "validate_transition",
]


def _ensure_utc(value: datetime) -> datetime:
    """Reject naive datetimes; normalise tz-aware ones to UTC.

    Mirrors the schema-layer rule (spec_01 §11.4): every stored timestamp is
    tz-aware UTC so leases, schedules, and audit times share one frame.
    """
    if value.tzinfo is None:
        msg = "naive datetime not allowed; use datetime.now(UTC) or attach a tzinfo"
        raise ValueError(msg)
    return value.astimezone(UTC)


class JobState(StrEnum):
    """The durable lifecycle of a job.

    ``queued`` → ``claimed`` → ``running`` → ``succeeded`` | ``failed`` | ``dead``.
    A retryable failure or a lease-expiry reclaim returns the job to ``queued``;
    a permanent failure terminates at ``failed``; retry exhaustion dead-letters
    at ``dead`` (criterion 5). ``succeeded``/``failed``/``dead`` are terminal.
    """

    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


# The authoritative transition table. A target not listed for a source is
# illegal and raises JobStateError. CLAIMED/RUNNING → QUEUED covers both a
# scheduled retry and a lease-expiry reclaim (the crash-resume edge); RUNNING →
# {SUCCEEDED, FAILED, DEAD} are the three terminal outcomes.
_VALID_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.CLAIMED}),
    JobState.CLAIMED: frozenset({JobState.RUNNING, JobState.QUEUED}),
    JobState.RUNNING: frozenset(
        {JobState.SUCCEEDED, JobState.FAILED, JobState.DEAD, JobState.QUEUED}
    ),
    JobState.SUCCEEDED: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.DEAD: frozenset(),
}

TERMINAL_STATES: frozenset[JobState] = frozenset(
    {JobState.SUCCEEDED, JobState.FAILED, JobState.DEAD}
)


def is_terminal(state: JobState) -> bool:
    """True if ``state`` admits no further transitions."""
    return state in TERMINAL_STATES


def can_transition(frm: JobState, to: JobState) -> bool:
    """True if ``frm → to`` is a legal edge in the state machine."""
    return to in _VALID_TRANSITIONS[frm]


def validate_transition(frm: JobState, to: JobState) -> None:
    """Raise :class:`JobStateError` if ``frm → to`` is not a legal transition."""
    if not can_transition(frm, to):
        raise JobStateError(
            "illegal job state transition",
            context={"from": frm.value, "to": to.value},
        )


class LeasePolicy(BaseModel):
    """Lease duration + heartbeat interval for a job type (D-A0-1).

    The lease length is the worst-case crash-resume SLA (a dead worker's job is
    reclaimable only once its lease expires). ``heartbeat_seconds`` must be
    ``<= lease_seconds / 2`` so a live-but-slow worker renews well inside the
    window and is never falsely reclaimed (which would double-run the job).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lease_seconds: int = Field(gt=0)
    heartbeat_seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def _heartbeat_within_half_lease(self) -> LeasePolicy:
        if self.heartbeat_seconds * 2 > self.lease_seconds:
            msg = (
                "heartbeat_seconds must be <= lease_seconds / 2 "
                f"(got heartbeat={self.heartbeat_seconds}, lease={self.lease_seconds})"
            )
            raise ValueError(msg)
        return self


# The three lease classes (D-A0-1). Per-job-type; config can override.
SHORT_LEASE = LeasePolicy(lease_seconds=30, heartbeat_seconds=10)
MEDIUM_LEASE = LeasePolicy(lease_seconds=90, heartbeat_seconds=30)
LONG_LEASE = LeasePolicy(lease_seconds=300, heartbeat_seconds=60)


class RetryPolicy(BaseModel):
    """Per-job-type retry policy: exponential backoff with a cap (D-A0-2).

    ``backoff_for`` is deterministic (exponential, capped) so it is unit-testable;
    jitter is layered at the queue level (T6) to keep persona-core free of
    randomness. Exhaustion (``attempt >= max_attempts``) dead-letters the job.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(default=5, ge=1)
    base_backoff_seconds: float = Field(default=2.0, gt=0)
    max_backoff_seconds: float = Field(default=300.0, gt=0)

    @model_validator(mode="after")
    def _cap_not_below_base(self) -> RetryPolicy:
        if self.max_backoff_seconds < self.base_backoff_seconds:
            msg = (
                "max_backoff_seconds must be >= base_backoff_seconds "
                f"(got max={self.max_backoff_seconds}, base={self.base_backoff_seconds})"
            )
            raise ValueError(msg)
        return self

    def backoff_for(self, attempt: int) -> float:
        """Backoff in seconds before the next try of a job already run ``attempt`` times.

        ``attempt`` is 1-based (1 = back off before the second run). Exponential
        ``base * 2 ** (attempt - 1)``, capped at ``max_backoff_seconds``.
        """
        if attempt < 1:
            msg = f"attempt must be >= 1, got {attempt}"
            raise ValueError(msg)
        # 2.0 ** n (float base) keeps the result a ``float`` — ``int ** int`` is
        # typed ``Any`` because a negative exponent would yield a float.
        raw = self.base_backoff_seconds * (2.0 ** (attempt - 1))
        return min(raw, self.max_backoff_seconds)

    def is_exhausted(self, attempt: int) -> bool:
        """True if a job attempted ``attempt`` times has no retries left."""
        return attempt >= self.max_attempts


class JobPayload(BaseModel):
    """Base class for every job payload. Frozen + ``extra='forbid'`` by contract.

    Concrete payloads subclass this (e.g. the avatar-generation handler's
    payload at A0 T9). Keeping the base typed lets the registry hold a
    ``type[JobPayload]`` per job type and reconstruct the concrete payload from
    stored JSONB at claim time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class Job(BaseModel):
    """A durable job record — the faithful in-memory shape of one queue row.

    Frozen: lifecycle changes produce a new ``Job`` via :meth:`transition_to`,
    never an in-place mutation. ``idempotency_key`` is the dedup identity
    (operation+intent scoped — D-A0-X-idempotency-key-convention); a duplicate
    enqueue with the same key is a no-op. ``priority`` orders the claim query
    (higher first), then ``scheduled_at``. The lease fields are owned by the
    queue (T3) and are ``None`` until a worker claims the job.

    Attributes:
        id: Durable job id (UUID at the persistence layer).
        type: The registered job type (the :class:`JobRegistry` key).
        owner_id: The tenant the job runs as — the RLS scope and key material.
        payload: The frozen typed payload. ``SerializeAsAny`` so ``model_dump``
            includes the concrete subclass fields, not just the base.
        idempotency_key: Operation+intent-scoped dedup key (UNIQUE at the row).
        state: Current lifecycle state (defaults to ``queued``).
        priority: Claim ordering; higher is claimed sooner.
        attempt: Times this job has been claimed-to-run (0 before first claim).
        max_attempts: Dead-letter threshold (mirrors the type's RetryPolicy).
        scheduled_at: Earliest claimable instant (A1 rides this).
        created_at: Enqueue time.
        lease_expires_at: When the current lease lapses (None until claimed).
        locked_by: The worker id holding the lease (None until claimed).
        last_error: Cause recorded on the most recent failure/dead-letter.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    type: str
    owner_id: str
    payload: SerializeAsAny[JobPayload]
    idempotency_key: str
    state: JobState = JobState.QUEUED
    priority: int = 0
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=5, ge=1)
    scheduled_at: datetime
    created_at: datetime
    lease_expires_at: datetime | None = None
    locked_by: str | None = None
    last_error: str | None = None

    @field_validator("scheduled_at", "created_at", mode="after")
    @classmethod
    def _require_tz_aware(cls, value: datetime) -> datetime:
        return _ensure_utc(value)

    @field_validator("lease_expires_at", mode="after")
    @classmethod
    def _lease_tz_aware(cls, value: datetime | None) -> datetime | None:
        return _ensure_utc(value) if value is not None else None

    def transition_to(self, new_state: JobState, **updates: object) -> Job:
        """Return a new ``Job`` in ``new_state``, validating the edge first.

        Raises :class:`JobStateError` if the transition is illegal. Extra
        keyword ``updates`` (e.g. ``attempt=...``, ``last_error=...``,
        ``lease_expires_at=...``) are applied alongside the state change. Frozen:
        the receiver is never mutated; a copy is returned.
        """
        validate_transition(self.state, new_state)
        return self.model_copy(update={"state": new_state, **updates})
