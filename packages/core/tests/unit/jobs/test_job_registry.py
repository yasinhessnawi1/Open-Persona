"""Unit tests for the typed job-type registry (Spec A0, T1)."""

from __future__ import annotations

import pytest
from persona.errors import DuplicateJobTypeError, UnknownJobTypeError
from persona.jobs import (
    MEDIUM_LEASE,
    SHORT_LEASE,
    JobContext,
    JobHandler,
    JobPayload,
    JobRegistry,
    JobTypeSpec,
    RetryPolicy,
)
from pydantic import ValidationError


class _AvatarPayload(JobPayload):
    persona_id: str
    prompt: str = ""


class _RecordingHandler:
    """A trivial idempotent handler that records the payloads it handled."""

    def __init__(self) -> None:
        self.handled: list[_AvatarPayload] = []

    async def handle(self, payload: _AvatarPayload, _context: JobContext) -> None:
        self.handled.append(payload)


def _avatar_create_key(payload: _AvatarPayload) -> str:
    # D-A0-X-idempotency-key-convention: operation+intent scoped, not entity.
    return f"avatar:{payload.persona_id}:create"


def _avatar_spec(handler: JobHandler[_AvatarPayload] | None = None) -> JobTypeSpec[_AvatarPayload]:
    return JobTypeSpec(
        type="avatar_generation",
        payload_model=_AvatarPayload,
        handler=handler or _RecordingHandler(),
        idempotency_key=_avatar_create_key,
        retry=RetryPolicy(max_attempts=3),
        lease=SHORT_LEASE,
    )


def test_registry_registers_and_resolves_a_type() -> None:
    spec = _avatar_spec()
    registry = JobRegistry([spec])
    assert registry.types() == ["avatar_generation"]
    assert registry.get("avatar_generation") is spec


def test_registry_rejects_duplicate_type_at_construction() -> None:
    with pytest.raises(DuplicateJobTypeError) as excinfo:
        JobRegistry([_avatar_spec(), _avatar_spec()])
    assert excinfo.value.context == {"type": "avatar_generation"}


def test_registry_rejects_duplicate_type_via_register() -> None:
    registry = JobRegistry([_avatar_spec()])
    with pytest.raises(DuplicateJobTypeError):
        registry.register(_avatar_spec())


def test_registry_get_unknown_type_raises_with_known_list() -> None:
    registry = JobRegistry([_avatar_spec()])
    with pytest.raises(UnknownJobTypeError) as excinfo:
        registry.get("nope")
    assert excinfo.value.context["type"] == "nope"
    assert excinfo.value.context["known"] == "avatar_generation"


def test_registry_builds_idempotency_key_from_payload() -> None:
    registry = JobRegistry([_avatar_spec()])
    key = registry.idempotency_key_for("avatar_generation", _AvatarPayload(persona_id="persona-7"))
    assert key == "avatar:persona-7:create"


def test_registry_idempotency_key_is_stable_for_same_payload() -> None:
    registry = JobRegistry([_avatar_spec()])
    payload = _AvatarPayload(persona_id="persona-7", prompt="ignored-for-key")
    assert registry.idempotency_key_for(
        "avatar_generation", payload
    ) == registry.idempotency_key_for("avatar_generation", payload)


def test_registry_parse_payload_reconstructs_concrete_model() -> None:
    registry = JobRegistry([_avatar_spec()])
    payload = registry.parse_payload(
        "avatar_generation", {"persona_id": "persona-1", "prompt": "a fox"}
    )
    assert isinstance(payload, _AvatarPayload)
    assert payload.persona_id == "persona-1"
    assert payload.prompt == "a fox"


def test_registry_parse_payload_rejects_malformed_data() -> None:
    registry = JobRegistry([_avatar_spec()])
    with pytest.raises(ValidationError):
        registry.parse_payload("avatar_generation", {"wrong_field": "x"})


def test_spec_defaults_retry_and_lease() -> None:
    spec: JobTypeSpec[_AvatarPayload] = JobTypeSpec(
        type="t",
        payload_model=_AvatarPayload,
        handler=_RecordingHandler(),
        idempotency_key=_avatar_create_key,
    )
    assert spec.retry == RetryPolicy()
    assert spec.lease == MEDIUM_LEASE


def test_recording_handler_satisfies_protocol() -> None:
    # @runtime_checkable Protocol: the structural handler is recognised.
    assert isinstance(_RecordingHandler(), JobHandler)
