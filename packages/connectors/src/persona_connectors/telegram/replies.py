"""System-reply copy for the Telegram flow (Spec C2 flow) — pure product-voice text.

The "bot speaking" replies (not a persona): the list-and-instructions first-contact
(C1-D-7, rendered from the owner's persona names — the I/O half of the C1
``ListAndInstructions`` decision), plus the ``/new`` confirmations and the
no-personas edge. Sent as plain text (no persona tag). Pure + api-free.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "NEW_CONVERSATION_MESSAGE",
    "NO_ACTIVE_TO_RESET_MESSAGE",
    "NO_PERSONAS_MESSAGE",
    "render_list_and_instructions",
]

NEW_CONVERSATION_MESSAGE = "Started a fresh conversation — the slate's clear."
NO_ACTIVE_TO_RESET_MESSAGE = (
    "There's no active conversation to reset — just message a persona by name to start."
)
NO_PERSONAS_MESSAGE = (
    "You don't have any personas yet. Create one in your Open Persona web app, "
    "then come back here and message it by name."
)


def render_list_and_instructions(persona_names: Mapping[str, Sequence[str]]) -> str:
    """Render the list-and-instructions first-contact reply (C1-D-7, the I/O half).

    Lists the owner's persona display names and how to address one, in the product
    voice. The C1 ``decide_route`` decision picks WHEN to show this; this renders
    the content from the names the flow already loaded.

    Args:
        persona_names: ``persona_id`` → its addressable names; the first entry is
            the display name shown to the user.

    Returns:
        A plain-text reply (the connector renders no markup for system replies).
    """
    display_names = sorted(names[0] for names in persona_names.values() if names)
    if not display_names:
        return NO_PERSONAS_MESSAGE
    listed = ", ".join(display_names)
    example = display_names[0]
    return (
        f"You can talk to: {listed}. Start your message with a persona's name — "
        f'for example, "{example}, hello".'
    )
