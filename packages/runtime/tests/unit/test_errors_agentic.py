"""Unit tests for persona_runtime.agentic.errors (T01, D-06-2).

The agentic loop defines two terminal-outcome exception types but does NOT raise
them itself (the loop returns a ``Run`` with the right status). These tests cover
the exception surface only; the loop's status-not-raise contract is exercised in
test_loop_agentic.py (T06).
"""

from __future__ import annotations

import persona_runtime.agentic
import pytest
from persona.errors import PersonaError
from persona_runtime.agentic.errors import MaxStepsReachedError, RunCancelledError


class TestMaxStepsReachedError:
    def test_is_a_persona_error(self) -> None:
        assert issubclass(MaxStepsReachedError, PersonaError)

    def test_constructs_with_message_and_context(self) -> None:
        exc = MaxStepsReachedError("budget exhausted", context={"max_steps": "20", "run_id": "r-1"})
        assert exc.message == "budget exhausted"
        assert exc.context == {"max_steps": "20", "run_id": "r-1"}

    def test_str_includes_context(self) -> None:
        exc = MaxStepsReachedError("hit cap", context={"max_steps": "20"})
        rendered = str(exc)
        assert "hit cap" in rendered
        assert "max_steps=20" in rendered


class TestRunCancelledError:
    def test_is_a_persona_error(self) -> None:
        assert issubclass(RunCancelledError, PersonaError)

    def test_constructs_with_message_and_context(self) -> None:
        exc = RunCancelledError("user cancelled", context={"run_id": "r-2", "step": "3"})
        assert exc.message == "user cancelled"
        assert exc.context == {"run_id": "r-2", "step": "3"}

    def test_catchable_as_persona_error(self) -> None:
        with pytest.raises(PersonaError) as exc_info:
            raise RunCancelledError("x", context={"run_id": "r-3"})
        assert exc_info.value.context["run_id"] == "r-3"


class TestAgenticPackage:
    """The agentic subpackage imports cleanly and re-exports its errors."""

    def test_package_imports(self) -> None:
        assert persona_runtime.agentic is not None

    def test_reexports_the_two_errors(self) -> None:
        # The agentic package re-exports the full surface (T07); the two error
        # types are part of it.
        assert {"MaxStepsReachedError", "RunCancelledError"} <= set(persona_runtime.agentic.__all__)
