"""decide_route — the pure platform-agnostic routing decision (Spec C1 flow).

The decision tree every adapter (C2–C5) shares: Addressed → drive that persona;
NoName/Ambiguous with an active persona → drive the active (sticky pointer);
NoName with no active + a sole persona → auto-foreground; otherwise → list.
"""

from __future__ import annotations

from persona_connectors.domain.addressing import Addressed, Ambiguous, NoName
from persona_connectors.domain.routing import DrivePersona, ListAndInstructions, decide_route


def test_addressed_drives_that_persona_even_when_another_is_active() -> None:
    """An explicit address is a switch — drive the named persona regardless of active."""
    decision = decide_route(
        Addressed(persona_id="kai"), active_persona_id="astrid", owner_persona_ids=["astrid", "kai"]
    )
    assert decision == DrivePersona(persona_id="kai")


def test_no_name_with_active_drives_the_active_persona() -> None:
    """The sticky pointer: an unnamed message continues the active persona."""
    decision = decide_route(
        NoName(), active_persona_id="astrid", owner_persona_ids=["astrid", "kai"]
    )
    assert decision == DrivePersona(persona_id="astrid")


def test_no_name_no_active_single_persona_auto_foregrounds() -> None:
    """One persona + no active + no name → auto-foreground it (C1-D-7, no prompt)."""
    decision = decide_route(NoName(), active_persona_id=None, owner_persona_ids=["solo"])
    assert decision == DrivePersona(persona_id="solo")


def test_no_name_no_active_multiple_personas_lists() -> None:
    """No active + no name + several personas → the list-and-instructions reply."""
    decision = decide_route(NoName(), active_persona_id=None, owner_persona_ids=["astrid", "kai"])
    assert isinstance(decision, ListAndInstructions)


def test_ambiguous_with_active_stays_on_active() -> None:
    """Ambiguous never guesses a switch — it stays on the active persona (C1-D-4)."""
    decision = decide_route(
        Ambiguous(candidate_persona_ids=["a", "b"]),
        active_persona_id="astrid",
        owner_persona_ids=["astrid", "a", "b"],
    )
    assert decision == DrivePersona(persona_id="astrid")


def test_ambiguous_no_active_lists() -> None:
    """Ambiguous + no active → can't pick → list-and-instructions."""
    decision = decide_route(
        Ambiguous(candidate_persona_ids=["a", "b"]),
        active_persona_id=None,
        owner_persona_ids=["a", "b"],
    )
    assert isinstance(decision, ListAndInstructions)
