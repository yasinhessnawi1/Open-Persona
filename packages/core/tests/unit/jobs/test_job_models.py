"""Unit tests for the job models + lease/retry policies (Spec A0, T1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from persona.jobs import (
    LONG_LEASE,
    MEDIUM_LEASE,
    SHORT_LEASE,
    Job,
    JobPayload,
    JobState,
    LeasePolicy,
    RetryPolicy,
)
from pydantic import ValidationError


class _AvatarPayload(JobPayload):
    persona_id: str
    prompt: str


def _job(**overrides: object) -> Job:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "id": "job-1",
        "type": "avatar_generation",
        "owner_id": "user-1",
        "payload": JobPayload(),
        "idempotency_key": "avatar:persona-1:create",
        "scheduled_at": now,
        "created_at": now,
    }
    base.update(overrides)
    return Job(**base)  # type: ignore[arg-type]


# --- Job model ---------------------------------------------------------------


def test_job_is_frozen() -> None:
    job = _job()
    with pytest.raises(ValidationError):
        job.state = JobState.RUNNING  # type: ignore[misc]


def test_job_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _job(surprise="nope")


def test_job_defaults_to_queued_state_attempt_zero() -> None:
    job = _job()
    assert job.state is JobState.QUEUED
    assert job.attempt == 0
    assert job.lease_expires_at is None
    assert job.locked_by is None


def test_job_rejects_naive_scheduled_at() -> None:
    with pytest.raises(ValidationError):
        _job(scheduled_at=datetime(2026, 1, 1, 12, 0, 0))  # noqa: DTZ001 — testing the guard


def test_job_normalises_non_utc_to_utc() -> None:
    plus_two = timezone(timedelta(hours=2))
    job = _job(scheduled_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=plus_two))
    assert job.scheduled_at.tzinfo is UTC
    assert job.scheduled_at.hour == 10  # 12:00+02:00 == 10:00Z


def test_job_lease_expires_at_accepts_none_and_tz_aware() -> None:
    assert _job(lease_expires_at=None).lease_expires_at is None
    job = _job(lease_expires_at=datetime.now(UTC))
    assert job.lease_expires_at is not None
    assert job.lease_expires_at.tzinfo is UTC


def test_job_attempt_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        _job(attempt=-1)


def test_job_payload_serialize_as_any_preserves_subclass_fields() -> None:
    # SerializeAsAny: model_dump must include the concrete subclass fields,
    # not just the JobPayload base (which has none).
    job = _job(payload=_AvatarPayload(persona_id="persona-1", prompt="a fox"))
    dumped = job.model_dump()
    assert dumped["payload"] == {"persona_id": "persona-1", "prompt": "a fox"}


# --- LeasePolicy -------------------------------------------------------------


def test_lease_policy_accepts_heartbeat_at_or_below_half_lease() -> None:
    assert LeasePolicy(lease_seconds=30, heartbeat_seconds=15).heartbeat_seconds == 15
    assert LeasePolicy(lease_seconds=30, heartbeat_seconds=10).heartbeat_seconds == 10


def test_lease_policy_rejects_heartbeat_above_half_lease() -> None:
    with pytest.raises(ValidationError):
        LeasePolicy(lease_seconds=30, heartbeat_seconds=16)


def test_lease_policy_rejects_non_positive_values() -> None:
    with pytest.raises(ValidationError):
        LeasePolicy(lease_seconds=0, heartbeat_seconds=0)


def test_lease_policy_is_frozen_and_forbids_extra() -> None:
    policy = LeasePolicy(lease_seconds=30, heartbeat_seconds=10)
    with pytest.raises(ValidationError):
        policy.lease_seconds = 99  # type: ignore[misc]
    with pytest.raises(ValidationError):
        LeasePolicy(lease_seconds=30, heartbeat_seconds=10, extra="x")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("preset", "lease", "hb"),
    [(SHORT_LEASE, 30, 10), (MEDIUM_LEASE, 90, 30), (LONG_LEASE, 300, 60)],
)
def test_lease_presets_match_d_a0_1(preset: LeasePolicy, lease: int, hb: int) -> None:
    assert (preset.lease_seconds, preset.heartbeat_seconds) == (lease, hb)
    assert preset.heartbeat_seconds * 2 <= preset.lease_seconds


# --- RetryPolicy -------------------------------------------------------------


def test_retry_backoff_is_exponential_then_capped() -> None:
    policy = RetryPolicy(base_backoff_seconds=2.0, max_backoff_seconds=20.0, max_attempts=10)
    assert policy.backoff_for(1) == 2.0  # 2 * 2**0
    assert policy.backoff_for(2) == 4.0  # 2 * 2**1
    assert policy.backoff_for(3) == 8.0  # 2 * 2**2
    assert policy.backoff_for(4) == 16.0
    assert policy.backoff_for(5) == 20.0  # capped
    assert policy.backoff_for(9) == 20.0  # still capped


def test_retry_backoff_rejects_attempt_below_one() -> None:
    with pytest.raises(ValueError, match="attempt must be >= 1"):
        RetryPolicy().backoff_for(0)


def test_retry_is_exhausted_at_max_attempts() -> None:
    policy = RetryPolicy(max_attempts=3)
    assert policy.is_exhausted(2) is False
    assert policy.is_exhausted(3) is True
    assert policy.is_exhausted(4) is True


def test_retry_policy_rejects_cap_below_base() -> None:
    with pytest.raises(ValidationError):
        RetryPolicy(base_backoff_seconds=10.0, max_backoff_seconds=5.0)


def test_retry_policy_defaults() -> None:
    policy = RetryPolicy()
    assert policy.max_attempts == 5
    assert policy.base_backoff_seconds == 2.0
    assert policy.max_backoff_seconds == 300.0
