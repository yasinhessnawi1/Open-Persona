"""Placeholder ``ChatBackend`` until spec 02 ships real model backends.

This module exists so ``persona chat`` can be exercised end-to-end before
spec 02. It will be deleted in spec 02 when the real
``OpenAICompatibleBackend`` / ``OllamaBackend`` / ``HFLocalBackend``
implementations land.

The protocol is also defined here in stub form so the CLI can type against
it without importing from spec 02. Spec 02 will export a richer
``ChatBackend`` Protocol from ``persona.backends.protocol`` and replace
this module's import paths.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from persona.schema.conversation import ConversationMessage

__all__ = ["ChatBackendStub", "EchoBackend"]


@runtime_checkable
class ChatBackendStub(Protocol):
    """Minimum surface the CLI needs from a chat backend.

    Spec 02's full ``ChatBackend`` Protocol extends this with streaming,
    tool calls, and async variants. The stub keeps the CLI typeable today.
    """

    def generate(self, messages: list[ConversationMessage]) -> str:
        """Return a single response string for the given message history."""
        ...


class EchoBackend:
    """Echoes the last user message back. Useful only for CLI smoke tests.

    Spec 02 deletes this and the CLI's ``chat`` command rewires to a real
    backend chosen via ``PERSONA_BACKEND``.
    """

    name: str = "echo"

    def generate(self, messages: list[ConversationMessage]) -> str:
        last_user = next(
            (m for m in reversed(messages) if m.role == "user"),
            None,
        )
        if last_user is None:
            return "I would say: (no user message yet)"
        return f"I would say: {last_user.content}"
