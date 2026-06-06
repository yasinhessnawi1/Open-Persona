"""Unit tests for sandbox domain exceptions (spec 12 T01).

Verifies the D-12-6 hierarchy: ``SandboxError(PersonaError)`` parent + four
leaves (``CodeSandboxError``, ``ExecutionTimeoutError``, ``ResourceLimitError``,
``SandboxUnavailableError``), every leaf carrying the structured
``context: dict[str, str]`` per the established convention.

The "catch parent catches leaves" test is the load-bearing one — that
ergonomic is the whole reason the intermediate parent class exists.
"""

from __future__ import annotations

import pytest
from persona.errors import PersonaError
from persona.sandbox import (
    CodeSandboxError,
    ExecutionTimeoutError,
    ResourceLimitError,
    SandboxError,
    SandboxUnavailableError,
)

_LEAVES: list[type[SandboxError]] = [
    CodeSandboxError,
    ExecutionTimeoutError,
    ResourceLimitError,
    SandboxUnavailableError,
]


class TestSandboxErrorHierarchy:
    """D-12-6: ``SandboxError`` is an intermediate parent under
    :class:`persona.errors.PersonaError`. Four cohesive subtypes ship together,
    satisfying D-03-1's "introduce parent only when a third lands" condition.
    """

    @pytest.mark.parametrize("cls", _LEAVES)
    def test_subclasses_inherit_from_sandbox_error(self, cls: type[SandboxError]) -> None:
        assert issubclass(cls, SandboxError)

    @pytest.mark.parametrize("cls", [SandboxError, *_LEAVES])
    def test_all_inherit_from_persona_error(self, cls: type[PersonaError]) -> None:
        assert issubclass(cls, PersonaError)

    @pytest.mark.parametrize("cls", _LEAVES)
    def test_catching_parent_catches_leaves(self, cls: type[SandboxError]) -> None:
        """The whole point of the intermediate parent — one
        ``except SandboxError`` in T03's tool factory + the loops' ``_dispatch``
        catches every leaf without enumerating them."""
        try:
            raise cls("test")
        except SandboxError:
            return
        else:  # pragma: no cover — defensive
            pytest.fail(f"{cls.__name__} not caught by SandboxError")

    def test_sandbox_error_is_not_a_tool_not_allowed_error(self) -> None:
        """Sanity check: the hierarchy stays clean —
        :class:`SandboxError` is NOT a subclass of unrelated
        :class:`PersonaError` siblings like ``ToolNotAllowedError``."""
        from persona.errors import ToolNotAllowedError

        assert not issubclass(SandboxError, ToolNotAllowedError)
        assert not issubclass(CodeSandboxError, ToolNotAllowedError)


class TestSandboxErrorContext:
    """All sandbox errors carry ``context: dict[str, str]`` (inherited from
    :class:`PersonaError`) per the established convention."""

    def test_context_kwarg_round_trip(self) -> None:
        err = ExecutionTimeoutError(
            "wall-clock timeout",
            context={"wall_clock_s": "30.0", "session_id": "abc:42"},
        )
        assert err.context == {"wall_clock_s": "30.0", "session_id": "abc:42"}

    def test_context_defaults_empty(self) -> None:
        err = CodeSandboxError("sandbox crashed")
        assert err.context == {}

    def test_message_preserved(self) -> None:
        err = SandboxUnavailableError(
            "Docker daemon unreachable",
            context={"docker_host": "unix:///var/run/docker.sock"},
        )
        # PersonaError appends ``context`` k=v pairs to ``str(self)``; verify
        # both the human message and the structured key survive.
        rendered = str(err)
        assert "Docker daemon unreachable" in rendered
        assert "docker_host=unix:///var/run/docker.sock" in rendered

    def test_resource_limit_error_conventional_context(self) -> None:
        """The T03 tool factory inspects ``context["limit"]`` to choose
        ``outcome="oom"`` vs ``outcome="killed"`` on the synthesised result.
        Smoke-test the shape so the consumer convention is unit-pinned."""
        err = ResourceLimitError(
            "memory cap exceeded",
            context={"limit": "memory", "cap": "512"},
        )
        assert err.context["limit"] == "memory"
        assert err.context["cap"] == "512"
