"""System-reply copy (Spec C2 flow) — the list-and-instructions render."""

from __future__ import annotations

from persona_connectors.telegram.replies import NO_PERSONAS_MESSAGE, render_list_and_instructions


def test_lists_display_names_and_an_example() -> None:
    """The reply names the personas (display name = first entry) + how to address one."""
    text = render_list_and_instructions({"astrid": ["Astrid"], "kai": ["Kai", "K"]})
    assert "Astrid" in text
    assert "Kai" in text
    assert '"Astrid, hello"' in text  # an example using the first (sorted) name


def test_empty_personas_returns_the_no_personas_message() -> None:
    assert render_list_and_instructions({}) == NO_PERSONAS_MESSAGE
