"""Tests for ``persona.errors`` — structured context on every domain exception.

See spec 01 §11.7 and D-01-12.
"""

from __future__ import annotations

import pytest
from persona.errors import (
    AuditWriteError,
    BrokenVersionChainError,
    ChannelUnreachableError,
    MessageDeliveryError,
    OriginationForbiddenError,
    PersonaError,
    PersonaNotFoundError,
    PersonaSelfWriteForbiddenError,
    RuntimeWriteForbiddenError,
    SandboxViolationError,
    SchemaVersionMismatchError,
    StoreNotFoundError,
    ToolExecutionError,
    ToolNotAllowedError,
)


class TestPersonaErrorBase:
    def test_no_context_renders_message_only(self) -> None:
        err = PersonaError("something broke")
        assert str(err) == "something broke"
        assert err.context == {}

    def test_context_renders_with_message(self) -> None:
        err = PersonaError("write rejected", context={"store": "identity", "source": "user"})
        rendered = str(err)
        assert "write rejected" in rendered
        assert "store=identity" in rendered
        assert "source=user" in rendered

    def test_context_only_renders_without_message(self) -> None:
        err = PersonaError(context={"persona_id": "astrid"})
        assert str(err) == "[persona_id=astrid]"

    def test_context_is_copied_not_aliased(self) -> None:
        ctx = {"k": "v"}
        err = PersonaError("x", context=ctx)
        ctx["k"] = "mutated"
        assert err.context["k"] == "v"

    def test_can_be_caught_as_exception(self) -> None:
        with pytest.raises(Exception, match="boom"):  # noqa: PT011 — base class check
            raise PersonaError("boom")


class TestPolicyExceptions:
    def test_persona_self_is_subclass_of_runtime_write_forbidden(self) -> None:
        err = PersonaSelfWriteForbiddenError(
            "persona_self rejected",
            context={"store": "self_facts", "reason": "confidence<0.8"},
        )
        assert isinstance(err, RuntimeWriteForbiddenError)
        assert isinstance(err, PersonaError)
        assert "store=self_facts" in str(err)

    def test_runtime_write_forbidden_does_not_match_persona_self_specifically(
        self,
    ) -> None:
        """Callers can disambiguate by catching the subclass first."""
        err = RuntimeWriteForbiddenError(context={"store": "identity"})
        assert not isinstance(err, PersonaSelfWriteForbiddenError)


class TestOriginationExceptions:
    """Spec C0 domain exceptions for the origination + delivery boundary."""

    def test_channel_unreachable_is_a_message_delivery_error(self) -> None:
        """Callers can catch the broad delivery error or the specific unreachable case."""
        err = ChannelUnreachableError("no reachable channel", context={"owner_user_id": "user_1"})
        assert isinstance(err, MessageDeliveryError)
        assert isinstance(err, PersonaError)
        assert "owner_user_id=user_1" in str(err)

    def test_message_delivery_error_is_not_channel_unreachable_specifically(self) -> None:
        err = MessageDeliveryError(context={"reason": "serialise"})
        assert not isinstance(err, ChannelUnreachableError)

    def test_origination_forbidden_is_a_persona_error_not_a_delivery_error(self) -> None:
        """Ownership (criterion 9) is an authorisation failure, NOT a delivery failure —
        a cross-tenant origination must not be mistaken for an unreachable channel."""
        err = OriginationForbiddenError(
            "persona may only originate to its owner",
            context={"persona_owner": "user_1", "target": "user_2"},
        )
        assert isinstance(err, PersonaError)
        assert not isinstance(err, MessageDeliveryError)
        assert "target=user_2" in str(err)


class TestAllExceptionsAcceptContext:
    """Smoke-test every public exception class with the context kwarg."""

    @pytest.mark.parametrize(
        "exc_cls",
        [
            SchemaVersionMismatchError,
            PersonaNotFoundError,
            RuntimeWriteForbiddenError,
            PersonaSelfWriteForbiddenError,
            StoreNotFoundError,
            BrokenVersionChainError,
            AuditWriteError,
            ToolNotAllowedError,
            ToolExecutionError,
            SandboxViolationError,
            MessageDeliveryError,
            ChannelUnreachableError,
            OriginationForbiddenError,
        ],
    )
    def test_each_exception_accepts_context(self, exc_cls: type[PersonaError]) -> None:
        err = exc_cls("msg", context={"k1": "v1", "k2": "v2"})
        rendered = str(err)
        assert "msg" in rendered
        assert "k1=v1" in rendered
        assert "k2=v2" in rendered
        assert isinstance(err, PersonaError)
