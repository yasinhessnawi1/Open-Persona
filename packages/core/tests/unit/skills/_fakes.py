"""Small test fakes shared across the ``persona.skills`` test modules.

A real :class:`persona.backends.ChatBackend` is too heavy for unit tests
of the injector — the injector only needs a ``Callable[[str],
Awaitable[str]]``. These fakes implement that shape directly.
"""

# ruff: noqa: ANN401, ARG001, ARG002

from __future__ import annotations


class FakeSummariser:
    """Deterministic summariser that returns a fixed short string.

    ``calls`` records every input it received; ``return_value`` is what it
    sends back. Letting tests construct the output gives precise control
    over budget edge cases.
    """

    def __init__(self, return_value: str = "summarised.") -> None:
        self.return_value = return_value
        self.calls: list[str] = []

    async def __call__(self, content: str) -> str:
        self.calls.append(content)
        return self.return_value


class OverBudgetSummariser:
    """Returns text longer than the budget — exercises the defensive
    truncation fallback in :meth:`SkillInjector.inject`."""

    def __init__(self, return_value: str) -> None:
        self.return_value = return_value
        self.calls: list[str] = []

    async def __call__(self, content: str) -> str:
        self.calls.append(content)
        return self.return_value
