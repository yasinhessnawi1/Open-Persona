"""Tests for ``persona.errors`` — structured context on every domain exception.

See spec 01 §11.7 and D-01-12.
"""

from __future__ import annotations

import pytest
from persona.errors import (
    AuditWriteError,
    BrokenVersionChainError,
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
        ],
    )
    def test_each_exception_accepts_context(self, exc_cls: type[PersonaError]) -> None:
        err = exc_cls("msg", context={"k1": "v1", "k2": "v2"})
        rendered = str(err)
        assert "msg" in rendered
        assert "k1=v1" in rendered
        assert "k2=v2" in rendered
        assert isinstance(err, PersonaError)
